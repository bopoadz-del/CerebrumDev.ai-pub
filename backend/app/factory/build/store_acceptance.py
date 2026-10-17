"""Store-green = scripts/acceptance.py (≥12 measured checks) in the Store image.

Authorship floor is not acceptance. HTTP 200 ok:false is not a pass.
Export / Store-green require k/k of the twelve named lines.

RAG skip policy (documented): when the product has no RAG surface the
``rag_roundtrip_hit`` line is ``SKIP:no-rag-surface`` and counts as a
satisfied line. Steward (or any product with a RAG route/capability) must
plant a paragraph and get a hit — skip is forbidden there.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.factory.build.gates import GateContext, GateResult

GATE_NAME = "store_acceptance"
ACCEPTANCE_SCRIPT_REL = Path("scripts") / "acceptance.py"
#: How the writer runs the Store gate's own harness on its box (the same N
#: checks the gate scores; gate-only inputs print SKIP with the reason).
ACCEPTANCE_SELF_CHECK_COMMAND = "python scripts/acceptance.py --self-check"
ACCEPTANCE_REPORT_REL = Path("docs") / "store_acceptance.json"
OPENAPI_REL = Path("docs") / "openapi.json"
GITHUB_CI_REL = Path(".github") / "workflows" / "ci.yml"
UI_INDEX_REL = Path("app") / "static" / "index.html"
AUTH_REL = Path("app") / "auth.py"

#: The checklist, read from the same file the writer's prompt is rendered
#: from. It used to be written out here, which is how the coder came to be
#: graded on thirteen checks nothing ever told it about -- see
#: app/factory/build/acceptance_floor.py.
from app.factory.build.acceptance_floor import advisory_ids as _floor_advisory_ids
from app.factory.build.authorship import AGENT_SOURCE_EXACT as _AGENT_SOURCE_EXACT
from app.factory.build.authorship import AGENT_SOURCE_PREFIXES as _AGENT_SOURCE_PREFIXES
from app.factory.build.authorship import FULL_PILOT_MIN_AUTHORED_ACTIONS as _FULL_PILOT_MIN_AUTHORED
from app.factory.build.authorship import RENDERED_MARKER_READER as _RENDERED_MARKER_READER
from app.factory.build.acceptance_floor import check_ids as _floor_check_ids
from app.factory.build.rejection_contract import (
    ACCEPT_STATUSES,
    ALLOWED_VALUES_HEADER,
    AUTH_REFUSAL_STATUS,
    ERROR_KEY,
    MISSING_REQUIRED,
    NOT_ALLOWED,
    OK_KEY,
    RECORD_ID_KEY,
    REJECTED_FIELD_HEADER,
    REJECTION_REASON_HEADER,
    STORED_RECORD_KEY,
    VALIDATION_REFUSAL_STATUS,
)

ACCEPTANCE_CHECK_NAMES: tuple[str, ...] = _floor_check_ids()

#: Every check on the floor must pass. A literal here drifts from the file
#: the moment a check is added, and a build would be graded 14/13.
ACCEPTANCE_REQUIRED = len(ACCEPTANCE_CHECK_NAMES)
#: Reported, scored as SKIP, never a veto. Read from the floor file so the gate
#: and the stamped harness agree on which lines are advisory; see
#: acceptance_floor.advisory_ids for why any line is.
ACCEPTANCE_ADVISORY_NAMES: frozenset = frozenset(_floor_advisory_ids())
assert ACCEPTANCE_ADVISORY_NAMES <= set(ACCEPTANCE_CHECK_NAMES)
assert "authorship_floor" not in ACCEPTANCE_ADVISORY_NAMES


def demote_if_advisory(name: str, status: str, detail: str) -> tuple:
    """An advisory FAIL becomes a SKIP that still says why it failed.

    SKIP is the status both consumers already count as satisfied, so no new
    status and no new arithmetic: the line stays in k/N, the reason stays on
    the line, and only the veto is removed. PASS and SKIP pass through unchanged,
    and a non-advisory FAIL is untouched -- the floor still fails a build.
    """
    if status == "FAIL" and name in ACCEPTANCE_ADVISORY_NAMES:
        return "SKIP", f"advisory (not on the floor yet): {detail}"
    return status, detail

assert len(ACCEPTANCE_CHECK_NAMES) >= ACCEPTANCE_REQUIRED
assert ACCEPTANCE_CHECK_NAMES[-1] == "authorship_floor"

#: The harness prints a human line per check AND a typed JSON record; the
#: Factory reads only the records (never the human text).
ACCEPTANCE_RECORD_KEY = "acceptance_record"
ACCEPTANCE_TOTAL_KEY = "acceptance_total"
#: The env variable the Store gate sets to where it mounts the product's
#: checkout inside the image. Rendered into the harness as REPO_ROOT_ENV; the
#: gate reads the name from there, so the two sides hold one name.
ACCEPTANCE_REPO_ROOT_ENV = "ACCEPTANCE_REPO_ROOT"
#: The env variable the Store gate sets to the image's runtime tree (the
#: container's working directory) when it runs the harness FROM THE MOUNTED
#: CHECKOUT. The harness is a repository artifact: a minimal production image
#: (``COPY app`` only) need not carry it. Rendered as RUNTIME_ROOT_ENV; the gate
#: reads the name from there.
ACCEPTANCE_RUNTIME_ROOT_ENV = "ACCEPTANCE_RUNTIME_ROOT"
_STATUSES =("PASS", "FAIL", "SKIP")


def harness_record(name: str, status: str, detail: str = "") -> str:
    """One check's typed record, exactly as the stamped harness prints it."""
    return json.dumps({ACCEPTANCE_RECORD_KEY: {"name": name, "status": status, "detail": detail}})


def harness_total(passed: int, required: int) -> str:
    """The harness's typed score record."""
    return json.dumps({ACCEPTANCE_TOTAL_KEY: {"passed": passed, "required": required}})


def _records(text: str):
    """Every JSON object line in ``text`` (the harness's typed records)."""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            yield value


#: A check the gate could not run because an earlier step it depends on
#: failed (the image never built, so nothing ran inside it). Never satisfied
#: -- the build is not certified -- and never a failure handed to anyone: the
#: failure is the line that stopped the run, not the lines it starved.
NOT_RUN = "NOT_RUN"


@dataclass
class AcceptanceLine:
    name: str
    status: str
    detail: str = ""
    #: PRODUCT or FACTORY, derived from the check's subject and who wrote it
    #: (acceptance_floor.owner_of). Empty until finalize_owners stamps it.
    owner: str = ""
    #: Raw evidence the gate attached (e.g. the tail of a failed ``docker
    #: build``). Shown to whoever fixes it; NEVER read by owner_of, which
    #: decides from the check's subject and the structural ``detail`` -- a log
    #: tail naming a Factory script the product's Dockerfile chose to run must
    #: not move the failure onto the Factory.
    evidence: str = ""
    #: The gate's TYPED evidence rows for this line (factory_receipt.ROW_*):
    #: an audit line's bandit/pip-audit findings as mappings, so ownership is
    #: read per row from provenance and never parsed out of ``evidence`` text.
    evidence_rows: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def satisfied(self) -> bool:
        return self.status in {"PASS", "SKIP"}

    @property
    def failed(self) -> bool:
        """Measured and not satisfied -- what an owner must act on."""
        return not self.satisfied and self.status != NOT_RUN


@dataclass
class AcceptanceReport:
    passed: int = 0
    total: int = ACCEPTANCE_REQUIRED
    ok: bool = False
    lines: List[AcceptanceLine] = field(default_factory=list)
    missing: bool = False
    via: str = "scripts/acceptance.py"
    detail: str = ""
    #: Scored over the checks the PRODUCT owns only, so a check the Factory
    #: owns cannot lower the product's score; those are ``factory_owed``.
    product_passed: int = 0
    product_total: int = 0
    factory_owed: List[str] = field(default_factory=list)
    #: False when the harness never produced a verdict (the gate's own
    #: workflow failed before scoring) -- nobody's product did that.
    harness_ran: bool = True

    @property
    def product_ok(self) -> bool:
        return (
            self.harness_ran
            and not self.missing
            and self.product_total > 0
            and self.product_passed == self.product_total
        )

    @property
    def product_score(self) -> str:
        return f"{self.product_passed}/{self.product_total}"

    def to_json(self) -> Dict[str, Any]:
        return {
            "schema_version": "store_acceptance.v1",
            "passed": self.passed,
            "total": self.total,
            "ok": self.ok,
            "missing": self.missing,
            "via": self.via,
            "detail": self.detail,
            "score": f"{self.passed}/{self.total}",
            "lines": [asdict(line) for line in self.lines],
            "checks": list(ACCEPTANCE_CHECK_NAMES),
            "product_passed": self.product_passed,
            "product_total": self.product_total,
            "product_score": self.product_score,
            "product_ok": self.product_ok,
            "factory_owed": list(self.factory_owed),
            "harness_ran": self.harness_ran,
        }


def finalize_owners(report: AcceptanceReport) -> AcceptanceReport:
    """Stamp each line's owner and score the report per owner.

    Ownership is derived per line from what the check judges and who wrote it
    (``acceptance_floor.owner_of``) -- there is no list of check names that
    says who owns what, so a Factory-owned check added tomorrow is attributed
    correctly without anyone touching this. The product's score counts only
    the lines the product owns; a Factory-owned line that is not satisfied is
    listed in ``factory_owed`` for the factory lane instead of failing the
    product.
    """
    from app.factory.build.acceptance_floor import FACTORY, owner_of

    owed: List[str] = []
    p_pass = p_total = 0
    for line in report.lines:
        if not line.owner:
            line.owner = owner_of(line.name, line.detail)
        if line.owner == FACTORY:
            if line.failed:
                owed.append(line.name)
            continue
        p_total += 1
        if line.satisfied:
            p_pass += 1
    report.product_passed = p_pass
    report.product_total = p_total
    report.factory_owed = owed
    return report


def missing_acceptance_report(*, detail: str = "scripts/acceptance.py was not run") -> AcceptanceReport:
    return AcceptanceReport(
        passed=0,
        total=ACCEPTANCE_REQUIRED,
        ok=False,
        missing=True,
        detail=detail,
        lines=[
            AcceptanceLine(name=name, status="FAIL", detail="not measured")
            for name in ACCEPTANCE_CHECK_NAMES
        ],
    )


def parse_acceptance_output(text: str) -> AcceptanceReport:
    """Parse harness stdout. Presence of ok:true in a product is not a pass."""
    lines: List[AcceptanceLine] = []
    seen: set[str] = set()
    total = ACCEPTANCE_REQUIRED
    for record in _records(text):
        summary = record.get(ACCEPTANCE_TOTAL_KEY)
        if isinstance(summary, dict):
            try:
                total = max(int(summary.get("required") or 0), ACCEPTANCE_REQUIRED)
            except (TypeError, ValueError):
                pass
            continue
        item = record.get(ACCEPTANCE_RECORD_KEY)
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").upper()
        name = str(item.get("name") or "")
        detail = str(item.get("detail") or "").strip()
        if status not in _STATUSES or name not in ACCEPTANCE_CHECK_NAMES or name in seen:
            continue
        seen.add(name)
        status, detail = demote_if_advisory(name, status, detail)
        lines.append(AcceptanceLine(name=name, status=status, detail=detail))
    by_name = {line.name: line for line in lines}
    ordered = [
        by_name.get(
            name,
            AcceptanceLine(name=name, status="FAIL", detail="harness omitted this line"),
        )
        for name in ACCEPTANCE_CHECK_NAMES
    ]
    satisfied = sum(1 for line in ordered if line.satisfied)
    ok = (
        satisfied >= ACCEPTANCE_REQUIRED
        and total >= ACCEPTANCE_REQUIRED
        and all(line.satisfied for line in ordered)
        and ordered[-1].name == "authorship_floor"
    )
    return finalize_owners(
        AcceptanceReport(
            passed=satisfied,
            total=total,
            ok=ok,
            lines=ordered,
            detail=f"{satisfied}/{total}",
        )
    )


def write_acceptance_report(root: Path | str, report: AcceptanceReport) -> Path:
    dest = Path(root) / ACCEPTANCE_REPORT_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def read_acceptance_report(
    root: Optional[Path | str] = None,
    status: Optional[Mapping[str, Any]] = None,
) -> AcceptanceReport:
    """Fail-closed: missing report is 0/12, not a pass.

    ``docs/store_acceptance.json`` is what the Store gate just wrote and is
    authoritative. A stale status / level_grade blob — ``missing: true`` or a
    measured 0/12 from an earlier miss — must not hide a later k/k file.
    """
    if root:
        path = Path(root) / ACCEPTANCE_REPORT_REL
        if path.is_file():
            try:
                return _report_from_mapping(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                return missing_acceptance_report(detail="docs/store_acceptance.json is unreadable")
    if status:
        raw = status.get("acceptance")
        if isinstance(raw, Mapping) and raw.get("missing") is not True:
            mapped = _report_from_mapping(raw)
            if not mapped.missing:
                return mapped
        grade = status.get("level_grade")
        if isinstance(grade, Mapping) and isinstance(grade.get("acceptance"), Mapping):
            raw_grade = grade["acceptance"]
            if raw_grade.get("missing") is not True:
                mapped = _report_from_mapping(raw_grade)
                if not mapped.missing:
                    return mapped
    return missing_acceptance_report()


def _report_from_mapping(raw: Mapping[str, Any]) -> AcceptanceReport:
    lines: List[AcceptanceLine] = []
    for item in raw.get("lines") or []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "")
        if name not in ACCEPTANCE_CHECK_NAMES:
            continue
        lines.append(
            AcceptanceLine(
                name=name,
                status=str(item.get("status") or "FAIL").upper(),
                detail=str(item.get("detail") or ""),
                evidence=str(item.get("evidence") or ""),
                evidence_rows=[
                    dict(r) for r in item.get("evidence_rows") or [] if isinstance(r, Mapping)
                ],
            )
        )
    if not lines:
        if raw.get("missing") is True:
            return missing_acceptance_report(detail=str(raw.get("detail") or ""))
        return parse_acceptance_output(str(raw.get("detail") or ""))
    by_name = {line.name: line for line in lines}
    ordered = [
        by_name.get(name, AcceptanceLine(name=name, status="FAIL", detail="omitted"))
        for name in ACCEPTANCE_CHECK_NAMES
    ]
    passed = int(raw.get("passed") or sum(1 for line in ordered if line.satisfied))
    total = int(raw.get("total") or ACCEPTANCE_REQUIRED)
    if total < ACCEPTANCE_REQUIRED:
        total = ACCEPTANCE_REQUIRED
    ok = bool(raw.get("ok")) and passed >= total and all(line.satisfied for line in ordered)
    return finalize_owners(
        AcceptanceReport(
            passed=passed,
            total=total,
            ok=ok,
            lines=ordered,
            missing=bool(raw.get("missing")),
            via=str(raw.get("via") or "scripts/acceptance.py"),
            detail=str(raw.get("detail") or f"{passed}/{total}"),
            harness_ran=raw.get("harness_ran") is not False,
        )
    )


def acceptance_is_kk(report: Optional[AcceptanceReport]) -> bool:
    if report is None or report.missing:
        return False
    return (
        report.ok
        and report.passed >= ACCEPTANCE_REQUIRED
        and report.total >= ACCEPTANCE_REQUIRED
        and report.passed == report.total
    )


def workspace_acceptance_is_kk(
    root: Optional[Path | str] = None,
    status: Optional[Mapping[str, Any]] = None,
) -> bool:
    """True only when the workspace (or status) has a measured k/k report."""
    return acceptance_is_kk(read_acceptance_report(root, status))


def acceptance_surface_incomplete(root: Path | str) -> bool:
    """True when WRITER has not stamped the files STORE measures.

    Pre-#395 workspaces can be ledger-pilot-ready and still lack the harness,
    ``app/auth.py``, the UI stamp, or route token/422 wiring. STORE cannot
    evaluate those — WRITER must run again.
    """
    dest = Path(root)
    if not (dest / ACCEPTANCE_SCRIPT_REL).is_file():
        return True
    if not (dest / AUTH_REL).is_file():
        return True
    if not (dest / UI_INDEX_REL).is_file():
        return True
    if not (dest / OPENAPI_REL).is_file():
        return True
    if not (dest / GITHUB_CI_REL).is_file():
        return True
    routes = dest / "app" / "routes.py"
    if not routes.is_file():
        return True
    try:
        tree = ast.parse(routes.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return True
    # The two guards are names the routes module USES (a call or a reference),
    # read from its syntax tree -- never a search of the file's text.
    used = (
        {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        | {a.asname or a.name for n in ast.walk(tree)
           if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    )
    return not {"require_platform_token", "reject_invalid_payload"} <= used


def acceptance_export_blocker(
    status: Optional[Mapping[str, Any]] = None,
    workspace: Optional[Path | str] = None,
) -> Optional[str]:
    """Refuse Store-green / Export unless acceptance is k/k.

    Code-cycle prototypes (PRODUCT/STORE not run) are not this lie.
    Authorship-only green is not acceptance.
    """
    status = dict(status or {})
    cycle = str(status.get("cycle") or "").strip().lower()
    grade = status.get("level_grade")
    gates = grade.get("three_gate") if isinstance(grade, Mapping) else None
    if not isinstance(gates, Mapping):
        from app.factory.build.level_grade import three_gate_verdict

        gates = three_gate_verdict(status)
    claiming = cycle == "pilot" or (
        str(gates.get("PRODUCT") or "") == "PASS" and str(gates.get("STORE") or "") == "PASS"
    )
    if not claiming:
        return None
    report = read_acceptance_report(workspace, status)
    if acceptance_is_kk(report):
        return None
    return (
        f"STORE_ACCEPTANCE: acceptance is {report.passed}/{report.total} "
        "(Export / Store-green require k/k PASS of scripts/acceptance.py; "
        "authorship floor is not acceptance)"
    )


def render_auth_module() -> str:
    """Emit app/auth.py: tenancy-resolving token auth + payload validator."""
    lines = [
        '"""Capability write routes require a platform token.',
        '',
        'POST /v1/<capability> without a bearer / X-Platform-Token is HTTP 401.',
        'Missing required fields and invalid enums are HTTP 422. JSON ``ok: false``',
        'with HTTP 200 is not an auth or validation pass.',
        '"""',
        '',
        'from __future__ import annotations',
        '',
        'import os',
        'from typing import Any, Dict',
        '',
        'from fastapi import HTTPException, Request',
        '',
        'PLATFORM_TOKEN_ENV = "PLATFORM_TOKEN"',
        '# No baked default: RUNTIME reads the token from the environment only.',
        '',
        '',
        'def platform_token() -> str:',
        '    token = (os.environ.get(PLATFORM_TOKEN_ENV) or "").strip()',
        '    return "" if token == "set-at-deploy" else token',
        '',
        '',
        'def require_platform_token(request: Request) -> Any:',
        '    """Resolve the caller\'s tenant from the presented bearer token.',
        '',
        '    Delegates to app.tenancy.resolve_tenant — the single resolution path',
        '    shared with rag_routes and kernel_bridge. A missing, unknown, or',
        '    client-named tenant is refused with 401 by name',
        '    (authentication_required), never silently mapped to a default.',
        '    """',
        '    import app.tenancy as tenancy',
        '',
        '    try:',
        '        return tenancy.resolve_tenant(request.headers)',
        '    except tenancy.TenantRefused:',
        '        raise HTTPException(status_code=401, detail="authentication_required")',
        '',
        '',
        'def reject_invalid_payload(capability_id: str, payload: Dict[str, Any] | None) -> None:',
        '    from app.models import MODELS',
        '',
        '    cls = MODELS.get(capability_id)',
        '    if cls is None:',
        '        raise HTTPException(status_code=422, detail="unknown capability")',
        '    if not isinstance(payload, dict):',
        '        raise HTTPException(status_code=422, detail="payload must be an object")',
        '    fields = list(getattr(cls, "FIELDS", []) or [])',
        '    constraints = getattr(cls, "CONSTRAINTS", {}) or {}',
        '    required = [',
        '        name',
        '        for name in fields',
        '        if (constraints.get(name) or {}).get("required")',
        '        or name in getattr(cls, "REQUIRED", ())',
        '    ]',
        '    if not required:',
        '        # Models stamp required on the field spec; fall back to every field',
        '        # that has no default in CONSTRAINTS.required=False only.',
        '        required = [',
        '            name',
        '            for name in fields',
        '            if (constraints.get(name) or {}).get("required") is not False',
        '            and name in constraints',
        '            and constraints[name].get("required")',
        '        ]',
        '    for name in required:',
        '        if name not in payload or payload[name] in (None, ""):',
        '            raise HTTPException(',
        '                status_code=422,',
        '                detail="Missing required field: " + name,',
        f'                headers=_rejection(name, {MISSING_REQUIRED!r}),',
        '            )',
        '    for name, rules in constraints.items():',
        '        if name not in payload:',
        '            continue',
        '        allowed = rules.get("allowed_values")',
        '        if allowed is not None and payload[name] not in allowed:',
        '            raise HTTPException(',
        '                status_code=422,',
        '                detail=name + " must be one of: " + ", ".join(str(v) for v in allowed),',
        f'                headers=_rejection(name, {NOT_ALLOWED!r}, allowed),',
        '            )',
        '',
        '',
        'def _rejection(field: str, reason: str, allowed: Any = None) -> Dict[str, str]:',
        '    """The rejection as data: which field, why, and the accepted values.',
        '',
        '    Callers (the route suite included) read these headers; the prose',
        '    detail is for humans only."""',
        '    import json',
        '',
        '    out = {',
        f'        {REJECTED_FIELD_HEADER!r}: json.dumps(field),',
        f'        {REJECTION_REASON_HEADER!r}: reason,',
        '    }',
        '    if allowed is not None:',
        f'        out[{ALLOWED_VALUES_HEADER!r}] = json.dumps(list(allowed))',
        '    return out',
    ]
    return "\n".join(lines)


#: The tenant a single-tenant product's rows live under: the default the
#: rendered app/tenancy.py declares. One definition, read by every renderer.
DEFAULT_TENANT_ID = "local"

TENANCY_REL = "app/tenancy.py"


def render_tenancy_module() -> str:
    """Emit the deterministic tenancy module (single resolution path)."""
    lines = [
        '"""Tenancy for this platform: one tenant per request, always.',
        '',
        'The tenant is resolved from the authenticated principal — the bearer',
        'token the caller presented — and never from a client-supplied name. A',
        'payload that tries to name its own tenant is refused rather than trusted.',
        '',
        'Token → tenant mapping comes from the environment:',
        '',
        '    PLATFORM_TOKEN      the platform token (required; no baked default)',
        '    TENANT_TOKENS       "token:tenant,token:tenant" for extra tenants',
        '    TENANT_NAMES        "tenant:display name" for readable names',
        '',
        'Every capability read and write goes through the tenant resolved here —',
        'app/store.py scopes every row by tenant_id.',
        '"""',
        '',
        'from __future__ import annotations',
        '',
        'import os',
        'from dataclasses import dataclass',
        'from typing import Dict, List, Mapping, Tuple',
        '',
        '#: Reserved keys a capability payload may never carry: tenancy is',
        '#: server-side, resolved from the token, never from the payload.',
        'RESERVED_TENANT_KEYS = ("tenant", "tenant_id", "tenant_name", "org_id", "organisation_id")',
        '',
        'DEFAULT_TENANT = %r' % (DEFAULT_TENANT_ID,),
        '',
        '',
        'class TenantRefused(PermissionError):',
        '    """A caller presented no token, an unbound token, or tried to name',
        '    their own tenant."""',
        '',
        '',
        '@dataclass(frozen=True)',
        'class Tenant:',
        '    tenant_id: str',
        '    name: str',
        '    roles: Tuple[str, ...] = ("admin",)',
        '',
        '    def to_dict(self) -> Dict[str, object]:',
        '        return {"tenant_id": self.tenant_id, "name": self.name, "roles": list(self.roles)}',
        '',
        '',
        'def _pairs(raw: str) -> List[Tuple[str, str]]:',
        '    out: List[Tuple[str, str]] = []',
        '    for chunk in str(raw or "").split(","):',
        '        chunk = chunk.strip()',
        '        if not chunk or ":" not in chunk:',
        '            continue',
        '        left, right = chunk.split(":", 1)',
        '        left, right = left.strip(), right.strip()',
        '        if left and right:',
        '            out.append((left, right))',
        '    return out',
        '',
        '',
        'def token_map() -> Dict[str, str]:',
        '    """token → tenant_id. The platform token owns the default tenant."""',
        '    out: Dict[str, str] = {}',
        '    # No baked fallback: a deploy that sets no token has NO platform',
        '    # token (fail closed). The deploy env carries a per-package random',
        '    # value; the TEST bootstrap (conftest) provides the dev value.',
        '    platform = (os.environ.get("PLATFORM_TOKEN") or "").strip()',
        '    if platform == "set-at-deploy":',
        '        platform = ""  # the shipped placeholder is not a credential',
        '    if platform:',
        '        out[platform] = DEFAULT_TENANT',
        '    for token, tenant in _pairs(os.environ.get("TENANT_TOKENS", "")):',
        '        out[token] = tenant',
        '    return out',
        '',
        '',
        'def name_map() -> Dict[str, str]:',
        '    return dict(_pairs(os.environ.get("TENANT_NAMES", "")))',
        '',
        '',
        'def resolve_tenant(headers: Mapping[str, str]) -> Tenant:',
        '    """Resolve the caller\'s tenant from the request headers.',
        '',
        '    Refuses by name: no token, an unknown token, or a payload-supplied',
        '    tenant identity all raise TenantRefused — the caller is never mapped',
        '    to a default tenant.',
        '    """',
        '    header = str(headers.get("authorization") or headers.get("Authorization") or "")',
        '    token = str(headers.get("x-platform-token") or "").strip()',
        '    if header.lower().startswith("bearer "):',
        '        token = header[7:].strip()',
        '    if not token:',
        '        raise TenantRefused("no token presented")',
        '    tenant_id = token_map().get(token)',
        '    if not tenant_id:',
        '        raise TenantRefused("token is not bound to a tenant")',
        '    return Tenant(tenant_id=tenant_id, name=name_map().get(tenant_id, tenant_id))',
    ]
    return "\n".join(lines)


def render_github_ci() -> str:
    """The product's CI. The ONLY emitter of it.

    ``stamp_acceptance_into_path`` writes this file unconditionally, so a
    second, richer version gap-filled elsewhere was silently overwritten and
    ``audit_clean`` would have failed every build while a file containing
    pip-audit sat in the substrate list. One source, and it is this one.
    """
    return (
        "# Full suite — python -m pytest tests. Store-green measures this file.\n"
        "name: ci\n"
        "on:\n"
        "  push:\n"
        "  pull_request:\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "3.12"\n'
        "      - run: pip install -c constraints.txt -r requirements.txt -r requirements-dev.txt\n"
        "      # Boot-clean: the platform imports with NO environment set. A\n"
        "      # credential the operator supplies at deploy is never read at import.\n"
        "      - name: import app.main with an empty environment\n"
        '        run: env -i PATH="$PATH" HOME="$HOME" python -c "import app.main"\n'
        "      - run: python -m pytest tests\n"
        "  audit:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "3.12"\n'
        "      - run: pip install -c constraints.txt -r requirements.txt pip-audit bandit\n"
        "      - run: pip-audit\n"
        "      - run: bandit -ll -r app\n"
        "  bench:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "3.12"\n'
        "      - run: pip install -c constraints.txt -r requirements.txt\n"
        "      - run: python scripts/bench.py\n"
    )


def render_openapi(product_name: str, cap_ids: Sequence[str]) -> str:
    paths: Dict[str, Any] = {
        "/": {"get": {"responses": {"200": {"description": "served UI"}}}},
        "/health": {"get": {"responses": {"200": {"description": "fail-closed health"}}}},
    }
    for cap in cap_ids:
        name = str(cap).replace("-", "_")
        paths[f"/v1/{name}"] = {
            "post": {
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {"description": "accepted"},
                    "401": {"description": "no token"},
                    "422": {"description": "validation"},
                },
            },
            "get": {"responses": {"200": {"description": "list"}}},
        }
    doc = {
        "openapi": "3.0.3",
        "info": {"title": product_name or "platform", "version": "1.0.0"},
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"}
            }
        },
    }
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def render_ui_index(product_name: str) -> str:
    """The console the platform serves. It drives the product, not a banner.

    This used to emit a one-line page ("Generated platform UI.") whose only
    job was to make ui_served_200 go green -- a facade the factory itself
    wrote, so no writer could start above it. A pilot is handed to a DevOps
    team to deploy and test, so what it serves has to reach the platform.

    Written against no product in particular: it discovers capabilities from
    GET /v1/capabilities and builds every route from what it finds, so it
    drives whatever the product has. The agent may replace it -- the
    ui_end_to_end gate judges the result, not the authorship.
    """
    title = product_name or "Platform"
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
 :root { color-scheme: light dark; --line:#8883; }
 body { font: 15px/1.5 system-ui, sans-serif; margin: 0; padding: 24px; max-width: 980px; }
 h1 { font-size: 20px; margin: 0 0 4px; }
 .dim { opacity: .7; font-size: 13px; }
 .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
 .card { border: 1px solid var(--line); border-radius: 8px; padding: 12px; margin: 12px 0; }
 button { padding: 6px 12px; border-radius: 6px; border: 1px solid var(--line); cursor: pointer; }
 input, select, textarea { padding: 6px 8px; border-radius: 6px; border: 1px solid var(--line); font: inherit; }
 pre { background: #8881; padding: 8px; border-radius: 6px; overflow: auto; max-height: 260px; font-size: 12px; }
 .label { display: inline-block; font-size: 11px; text-transform: uppercase; letter-spacing: .06em;
          border: 1px solid var(--line); border-radius: 999px; padding: 1px 8px; margin-left: 6px; }
 .err { color: #c33; }
</style>
</head>
<body>
<h1>__TITLE__</h1>
<p class="dim">Operator console. Paste a platform token, then drive the capabilities this build ships.</p>

<div class="card row">
  <label for="tok">Bearer token</label>
  <input id="tok" type="password" size="28" placeholder="platform token">
  <button onclick="boot()">Connect</button>
  <span id="who" class="dim"></span>
</div>

<div id="caps"></div>

<div class="card" id="ask" hidden>
  <strong>Ask the documents</strong>
  <p class="dim">Answers carry the authority layer they came from.</p>
  <div class="row">
    <input id="q" size="52" placeholder="question">
    <button onclick="ask()">Ask</button>
  </div>
  <div id="answer"></div>
</div>

<script>
const $ = (id) => document.getElementById(id);
const token = () => $("tok").value.trim();
const auth = () => ({ "Authorization": "Bearer " + token(), "Content-Type": "application/json" });
let SCHEMA = {};
let RAG = "";

async function call(path, init) {
  const res = await fetch(path, Object.assign({ headers: auth() }, init || {}));
  const text = await res.text();
  let body = null;
  try { body = text ? JSON.parse(text) : null; } catch (e) { body = text; }
  return { status: res.status, body };
}

// Every label an answer may carry its authority under.
const LABEL_KEYS = ["label", "labels", "authority", "precedence", "basis", "layer"];
function labelsIn(value, found) {
  found = found || [];
  if (!value || typeof value !== "object") return found;
  for (const [k, v] of Object.entries(value)) {
    if (LABEL_KEYS.includes(k) && v) found.push(typeof v === "string" ? v : JSON.stringify(v));
    else if (typeof v === "object") labelsIn(v, found);
  }
  return found;
}

async function boot() {
  $("caps").innerHTML = "";
  const caps = await call("/v1/capabilities");
  if (caps.status !== 200) {
    $("who").innerHTML = '<span class="err">' + caps.status + " — check the token</span>";
    return;
  }
  const items = (caps.body && (caps.body.items || caps.body.capabilities)) || [];
  const ids = items.map((c) => (typeof c === "string" ? c : c.id || c.capability_id)).filter(Boolean);
  $("who").textContent = ids.length + " capability(ies)";
  try {
    const doc = await (await fetch("/openapi.json")).json();
    SCHEMA = doc || {};
  } catch (e) { SCHEMA = {}; }
  ids.forEach(renderCapability);
  // Only offer what this product actually declares.
  const paths = (SCHEMA && SCHEMA.paths) || {};
  RAG = Object.keys(paths).find((p) => p.indexOf("/rag/") !== -1 && p.indexOf("query") !== -1) || "";
  $("ask").hidden = !RAG;
}

function fieldsFor(cap) {
  const paths = (SCHEMA && SCHEMA.paths) || {};
  const post = paths["/v1/" + cap] && paths["/v1/" + cap].post;
  const schema = post && post.requestBody && post.requestBody.content
    && post.requestBody.content["application/json"]
    && post.requestBody.content["application/json"].schema;
  const props = (schema && schema.properties) || {};
  const names = Object.keys(props);
  return names.length ? names.slice(0, 8) : ["reference", "status"];
}

function renderCapability(cap) {
  const card = document.createElement("div");
  card.className = "card";
  const fields = fieldsFor(cap);
  card.innerHTML =
    "<strong>" + cap + "</strong>" +
    '<div class="row" style="margin:8px 0">' +
    fields.map((f) => '<input data-f="' + f + '" placeholder="' + f + '" size="14">').join("") +
    ' <button data-act="create">Create</button> <button data-act="list">List</button></div>' +
    '<pre data-out>—</pre>';
  const out = card.querySelector("[data-out]");
  card.querySelector('[data-act="create"]').onclick = async () => {
    const record = {};
    card.querySelectorAll("[data-f]").forEach((i) => { if (i.value) record[i.dataset.f] = i.value; });
    const res = await call("/v1/" + cap, { method: "POST", body: JSON.stringify(record) });
    show(out, res);
  };
  card.querySelector('[data-act="list"]').onclick = async () => show(out, await call("/v1/" + cap));
  $("caps").appendChild(card);
}

function show(node, res) {
  const labels = labelsIn(res.body);
  node.textContent = res.status + "  " + JSON.stringify(res.body, null, 2);
  if (labels.length) {
    const tag = document.createElement("span");
    tag.className = "label";
    tag.textContent = labels[0];
    node.prepend(tag);
  }
}

async function ask() {
  if (!RAG) return;
  const res = await call(RAG, {
    method: "POST",
    body: JSON.stringify({ question: $("q").value, q: $("q").value }),
  });
  const labels = labelsIn(res.body);
  $("answer").innerHTML =
    "<pre>" + res.status + "  " + JSON.stringify(res.body, null, 2) + "</pre>" +
    (labels.length ? '<span class="label">' + labels[0] + "</span>" : "");
}
</script>
</body>
</html>
""".replace("__TITLE__", title)


def _with_deploy_time_settings(script: str) -> str:
    """Paste the shared deploy-time-settings snippet into the rendered script.

    After formatting, not inside the f-string: the snippet is full of braces.
    The SAME text goes into tests/conftest.py (roles_constants._CONFTEST), so
    the two places the Factory boots a product cannot come to disagree.
    """
    from app.factory.build.deploy_time_settings import SNIPPET

    slot = "#<<DEPLOY_TIME_SETTINGS>>"
    assert script.count(slot) == 1, "acceptance script lost its deploy-time slot"
    return script.replace(slot, SNIPPET.strip("\n"))


def render_acceptance_script(blueprint: Any = None) -> str:
    """Self-contained harness stamped into every pilot zip.

    The advisory set follows THIS build's brief: all checks still RUN and report,
    but a conditional check the brief never asked for is advisory (FAIL -> SKIP,
    no veto), exactly like the statically-advisory pipeline-evidence checks.
    ``None`` (no brief) raises every signal, so the advisory set is the static
    one — byte-for-byte what it was before brief-driven applicability existed."""
    names = ", ".join(repr(n) for n in ACCEPTANCE_CHECK_NAMES)
    # The Factory owns ci.yml (re-stamped before every TESTER): the harness
    # measures that the product's CI IS that workflow, by digest.
    ci_digest = hashlib.sha256(
        render_github_ci().replace("\r\n", "\n").encode("utf-8")
    ).hexdigest()
    advisory = ", ".join(repr(n) for n in sorted(_floor_advisory_ids(blueprint)))
    from app.factory.build.acceptance_floor import (
        audit_check_ids,
        brief_signals,
        image_check_ids,
        repository_check_ids,
    )

    image_checks = ", ".join(repr(n) for n in image_check_ids())
    audit_checks = ", ".join(repr(n) for n in audit_check_ids())
    repo_checks = ", ".join(repr(n) for n in repository_check_ids())
    from app.factory.build.writer_phases import (
        RAG_INGEST_PATHS,
        RAG_INGEST_TEXT_FIELDS,
        RAG_QUERY_PATHS,
    )

    # Decided here, from the build's declared contract: does a capability bind
    # a block whose signed manifest declares the retrieval read, and which
    # routes does the platform contract give the RAG surface.
    retrieves = "retrieval" in brief_signals(blueprint)
    rag_ingest = list(RAG_INGEST_PATHS)
    rag_query = list(RAG_QUERY_PATHS)
    # The create path the Store gate's isolation check uses: the round-trip
    # probes' declared-entity resolver and the emitted suites' payload builder,
    # rendered in (one source each; imported here, entity_contract imports
    # this module).
    from app.factory.build.entity_contract import ENTITY_RESOLVER_SRC
    from app.factory.build.payload_helpers import render_payload_helpers

    _CREATE_THROUGH_DECLARED_ENTITY = "\n".join(
        [*render_payload_helpers(), ENTITY_RESOLVER_SRC]
    )
    # The concurrency floor: every declared write path driven at 2x the
    # product's pool capacity (app/factory/build/concurrency_floor.py).
    from app.factory.build.concurrency_floor import render_concurrent_writes

    _CONCURRENT_WRITES = render_concurrent_writes()
    return _with_deploy_time_settings(f'''#!/usr/bin/env python3
"""Store-green acceptance — ≥12 measured checks. Presence-only is a fail.

Authorship floor is the LAST line. HTTP 200 ok:false is not a pass.
RAG skip policy: SKIP:no-rag-surface only when the product has no RAG
route or rag capability. Steward must plant + hit.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sys
import uuid
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: The product's RUNTIME tree -- what a runtime check judges and where the
#: harness serves ``app.main`` from. The Store gate runs this harness from the
#: mounted checkout and names the image's working directory here; unset (the
#: writer's --self-check, a local run) it is the tree this harness sits in.
RUNTIME_ROOT_ENV = {ACCEPTANCE_RUNTIME_ROOT_ENV!r}
ROOT = Path(os.environ.get(RUNTIME_ROOT_ENV) or Path(__file__).resolve().parents[1]).resolve()

#: Where the Store gate mounts the product's CHECKOUT inside the image, named
#: by this env variable (the gate reads the name from this constant). A check
#: whose declared subject is the repository (REPO_CHECKS: ``tree:`` and
#: ``factory_record``) reads its files there, because the image is not the
#: repository: a .dockerignore may leave CI wiring, docs or tests out of the
#: image, and that is the product's call. Unset (the writer's own run, a local
#: run) the checkout IS the directory this harness sits in.
REPO_ROOT_ENV = {ACCEPTANCE_REPO_ROOT_ENV!r}
REPO = Path(os.environ.get(REPO_ROOT_ENV) or ROOT).resolve()
#: The tree the running check reads files from: REPO for a repository-subject
#: check, the image (ROOT) for a runtime one. Set per check by main().
SRC = ROOT

#: ``python scripts/acceptance.py --self-check`` is the WRITER's run of this
#: same harness on its own box. Three inputs exist only where the Store gate
#: runs (a Docker daemon, a Postgres service, the bandit/pip-audit scan) and
#: arrive as STORE_* env values the gate exports. In a self-check an UNSET one
#: is reported SKIP with that reason -- never PASS. At the gate the flag is
#: never passed, so unmeasured stays a FAIL; a measured bad value fails both.
SELF_CHECK = "--self-check" in sys.argv[1:]


def _only_the_gate_measures(var):
    """SKIP line for a gate-only input left unset in a self-check, else None."""
    if SELF_CHECK and not (os.environ.get(var) or "").strip():
        return "SKIP", "%s is measured only by the Store gate (Docker/Postgres/scan); the gate still scores it" % var
    return None
#: sha256 of the Factory's own full-suite CI workflow (LF-normalised).
CI_SHA256 = {ci_digest!r}


def _parse(path: Path) -> Optional[ast.AST]:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError, ValueError):
        return None


def _dotted(expr: ast.AST) -> str:
    parts: List[str] = []
    while isinstance(expr, ast.Attribute):
        parts.append(expr.attr)
        expr = expr.value
    if isinstance(expr, ast.Name):
        parts.append(expr.id)
        return ".".join(reversed(parts))
    return ""


def _aliases(tree: ast.AST) -> Dict[str, str]:
    """Local name -> the module or object an import bound it to."""
    out: Dict[str, str] = {{}}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                out[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            mod = "." * node.level + (node.module or "")
            for a in node.names:
                out[a.asname or a.name] = (mod + "." + a.name) if mod else a.name
    return out


def _callees(tree: ast.AST) -> List[Tuple[str, ast.Call]]:
    """(callee resolved through the module's imports, call) for every call."""
    alias = _aliases(tree)
    out: List[Tuple[str, ast.Call]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name:
                head, _, rest = name.partition(".")
                base = alias.get(head, head)
                out.append((base + ("." + rest if rest else ""), node))
    return out


def _env_reads(tree: ast.AST) -> set:
    """Environment keys the module reads (os.environ.get / os.getenv /
    os.environ[...]), by constant key."""
    keys: set = set()
    readers = {{"os.environ.get", "os.getenv"}}
    for name, call in _callees(tree):
        if name in readers and call.args and isinstance(call.args[0], ast.Constant):
            keys.add(call.args[0].value)
    alias = _aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            head, _, rest = _dotted(node.value).partition(".")
            if (alias.get(head, head) + ("." + rest if rest else "")) == "os.environ":
                keys.add(node.slice.value)
    return keys


def _imports_module(tree: ast.AST, dotted: str) -> bool:
    """Does the module import ``dotted`` -- absolutely, from its parent
    package, or as a relative sibling?"""
    parent, _, leaf = dotted.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == dotted for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom):
            names = {{a.name for a in node.names}}
            if node.level == 0 and node.module == dotted:
                return True
            if node.level == 0 and node.module == parent and leaf in names:
                return True
            if node.level >= 1 and (node.module == leaf or (not node.module and leaf in names)):
                return True
    return False


class _Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: set = set()

    def handle_starttag(self, tag, attrs):  # noqa: ARG002 -- tags only
        self.tags.add(tag.lower())


CHECKS = [{names}]
# Reported and scored, never a veto -- same source as CHECKS (the floor file).
ADVISORY = [{advisory}]
# Checks judged on the built image (the floor's ``stage: image``). When the
# product's Dockerfile does not build, the Store gate scores THESE as FAIL and
# the rest NOT_RUN, reading this constant -- the gate names no check itself.
IMAGE_CHECKS = [{image_checks}]
# Checks whose verdict is the gate's security scan (the floor's ``stage:
# audit``). The gate attaches the scan's findings to THESE lines as evidence,
# reading this constant -- it names no check itself.
AUDIT_CHECKS = [{audit_checks}]
# Checks whose declared subject is the repository, not the running image (the
# floor's ``subject: tree:<path>`` and ``factory_record``): their files are read
# from REPO. Same source as CHECKS; no check is named here.
REPO_CHECKS = [{repo_checks}]
REQUIRED ={ACCEPTANCE_REQUIRED}
# Keys of the typed records the Factory reads from this harness's stdout --
# the same constants the Factory's parser uses (store_acceptance.py).
RECORD_KEY = {ACCEPTANCE_RECORD_KEY!r}
TOTAL_KEY = {ACCEPTANCE_TOTAL_KEY!r}
# The build's retrieval contract, rendered by the Factory from its blueprint:
# whether a capability binds a block that declares the vector-store read, and
# the platform's RAG routes. Never inferred from names or words at run time.
RETRIEVES = {retrieves!r}
RAG_INGEST_PATHS = {rag_ingest!r}
RAG_QUERY_PATHS = {rag_query!r}
# The ingest body fields the brief declares for the document text.
RAG_INGEST_TEXT_FIELDS = {tuple(RAG_INGEST_TEXT_FIELDS)!r}
# How long the harness waits for the service it starts to answer.
SERVE_TIMEOUT_S = 60
# The extra tenants the isolation check reads and writes as. The service the
# harness starts is a separate process, so it is started with them bound.
# Per-run tokens: a fixed token here would be a credential in every built image.
CHECK_TENANT_TOKENS = "token-a-" + os.urandom(8).hex() + ":tenant-a" + "," + "token-b-" + os.urandom(8).hex() + ":tenant-b"  # generated per run, never a fixed value


def _token() -> str:
    return (os.environ.get("PLATFORM_TOKEN") or "dev-local-token").strip()


def _auth() -> Dict[str, str]:
    return {{"Authorization": "Bearer " + _token()}}


class _Http:
    def __init__(self, client: Any):
        self.client = client

    def request(self, method: str, path: str, **kw: Any) -> Any:
        fn = getattr(self.client, method.lower())
        return fn(path, **kw)


class _Resp:
    def __init__(self, status, body, headers):
        self.status_code = int(status)
        self._body = body or b""
        self.headers = headers or {{}}
        self.content = self._body

    def json(self):
        return json.loads(self._body.decode("utf-8") or "{{}}")

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")


class _Url:
    """Standard-library HTTP against a running service.

    This harness runs INSIDE the image, where only the runtime requirements
    are installed. An in-process test client is a test-only library (it needs
    an HTTP client package the image never declares): live, an upstream
    release moved that dependency and every in-image run crashed before its
    first check. urllib is always there."""

    def __init__(self, base: str):
        self.base = base

    def request(self, method: str, path: str, json=None, headers=None, **_kw):
        import json as _json
        import urllib.error
        import urllib.request

        data = None
        hdrs = dict(headers or {{}})
        if json is not None:
            data = _json.dumps(json).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=_kw.get("timeout") or 8) as resp:
                return _Resp(resp.status, resp.read(), resp.headers)
        except urllib.error.HTTPError as exc:
            return _Resp(exc.code, exc.read(), exc.headers)

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, **kw):
        return self.request("POST", path, **kw)


class _Down:
    """The service never answered: every HTTP check fails with the reason;
    checks that read the tree still run."""

    def __init__(self, reason: str):
        self.reason = reason

    def get(self, path, **_kw):
        raise RuntimeError(self.reason)

    post = get


class _Served:
    """The service this harness started for itself; stopped on exit."""

    def __init__(self, proc: Any):
        self.proc = proc

    def __exit__(self, *_exc: Any) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _serve_env() -> Dict[str, str]:
    env = dict(os.environ)
    bound = (env.get("TENANT_TOKENS") or "").strip()
    env["TENANT_TOKENS"] = (bound + "," if bound else "") + CHECK_TENANT_TOKENS
    return env


def _serve() -> Tuple[str, _Served]:
    """Start the product's own app under its declared server (uvicorn, a
    runtime requirement) on a free local port, and wait until it answers.

    Any HTTP answer -- even an error status -- means it is serving; what that
    answer says is a check's verdict, not the harness's."""
    import subprocess
    import tempfile
    import time
    import urllib.error
    import urllib.request

    port = _free_port()
    base = "http://127.0.0.1:%d" % port
    log = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning", "--no-access-log"],
        cwd=str(ROOT), env=_serve_env(), stdout=log, stderr=subprocess.STDOUT,
    )
    served = _Served(proc)
    deadline = time.monotonic() + SERVE_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.seek(0)
            tail = log.read().decode("utf-8", errors="replace")[-1500:]
            raise RuntimeError("the service exited %s before answering: %s" % (proc.returncode, tail))
        try:
            with urllib.request.urlopen(base + "/health", timeout=2):
                return base, served
        except urllib.error.HTTPError:
            return base, served
        except Exception:
            time.sleep(0.2)
    served.__exit__(None, None, None)
    raise RuntimeError("the service did not answer within %ss" % SERVE_TIMEOUT_S)


def _client() -> Tuple[_Http, Any]:
    base = (os.environ.get("ACCEPTANCE_BASE_URL") or "").rstrip("/")
    if base:
        return _Http(_Url(base)), None
    try:
        base, served = _serve()
    except Exception as exc:
        return _Http(_Down("%s: %s" % (type(exc).__name__, exc))), None
    return _Http(_Url(base)), served


def _cap_order() -> List[str]:
    receipt = SRC / "docs" / "coder_receipt.json"
    if receipt.is_file():
        try:
            data = json.loads(receipt.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {{}}
        caps = data.get("capabilities") or []
        if caps:
            out = []
            for item in caps:
                if isinstance(item, dict):
                    item = item.get("id") or item.get("capability_id") or ""
                if str(item).strip():
                    out.append(str(item))
            if out:
                return out
    try:
        from app.jobs import CAPABILITIES

        out = []
        for item in CAPABILITIES or []:
            if isinstance(item, dict) and item.get("id"):
                out.append(str(item["id"]))
            elif isinstance(item, str) and item.strip():
                out.append(item)
        if out:
            return out
    except Exception:
        pass
    try:
        from app.models import MODELS

        return sorted(MODELS or {{}})
    except Exception:
        return []


def _first_cap() -> str:
    """The capability the HTTP checks write through.

    A capability whose connector is a DECLARED placeholder answers the typed
    unavailable refusal and stores nothing (app/placeholders.py), so a check
    that needs a stored record writes through the first one that works.
    """
    order = _cap_order()
    try:
        from app.placeholders import PLACEHOLDER_CONNECTORS
    except Exception:
        PLACEHOLDER_CONNECTORS = {{}}
    for cap in order:
        if not PLACEHOLDER_CONNECTORS.get(cap):
            return cap
    return order[0] if order else ""


def _models():
    try:
        from app.models import MODELS

        return MODELS
    except Exception:
        return {{}}


def _required_and_enum(cap_id: str) -> Tuple[Optional[str], Optional[Tuple[str, List[Any]]]]:
    """The required field and the vocabulary field THIS capability's own model
    declares. Never another model's field: posting capability X a field only
    model Y declares measures nothing X promised (the route may ignore it)."""
    models = _models()
    cls = models.get(cap_id) if cap_id else None
    required = None
    enum = None
    if cls is not None:
        fields = list(getattr(cls, "FIELDS", []) or [])
        constraints = getattr(cls, "CONSTRAINTS", {{}}) or {{}}
        for name in fields:
            rules = constraints.get(name) or {{}}
            if rules.get("required") and required is None:
                required = name
            allowed = rules.get("allowed_values") or []
            if allowed and enum is None:
                enum = (name, list(allowed))
    return required, enum


def _cap_declaring(want: str) -> Tuple[str, Any]:
    """(capability, declaration) for the first working capability whose own
    model declares ``want`` -- "required" or "enum". Declared placeholders
    answer their typed refusal before validation, so they cannot be measured
    here. ("", None) when no capability declares it."""
    try:
        from app.placeholders import PLACEHOLDER_CONNECTORS
    except Exception:
        PLACEHOLDER_CONNECTORS = {{}}
    for cap in _cap_order():
        if PLACEHOLDER_CONNECTORS.get(cap):
            continue
        required, enum = _required_and_enum(cap)
        found = required if want == "required" else enum
        if found:
            return cap, found
    return "", None


def check_no_token_401(http: _Http) -> Tuple[str, str]:
    cap = _first_cap()
    if not cap:
        return "FAIL", "no first capability to POST"
    resp = http.request("post", "/v1/" + cap, json={{}})
    if resp.status_code == {AUTH_REFUSAL_STATUS}:
        return "PASS", "HTTP {AUTH_REFUSAL_STATUS}"
    if resp.status_code in {tuple(ACCEPT_STATUSES)!r}:
        body = {{}}
        try:
            body = resp.json()
        except Exception:
            pass
        return "FAIL", "HTTP %s ok:%s (must be {AUTH_REFUSAL_STATUS}, not ok:false)" % (resp.status_code, body.get({OK_KEY!r}))
    return "FAIL", "HTTP %s (want {AUTH_REFUSAL_STATUS})" % resp.status_code


def check_missing_field_422(http: _Http) -> Tuple[str, str]:
    # Measured on a capability whose OWN model declares a required field.
    cap, required = _cap_declaring("required")
    if not cap:
        return "FAIL", "no capability declares a required field to measure (not a presence skip)"
    resp = http.request("post", "/v1/" + cap, json={{}}, headers=_auth())
    if resp.status_code == {VALIDATION_REFUSAL_STATUS}:
        return "PASS", "HTTP {VALIDATION_REFUSAL_STATUS} missing %s" % required
    return "FAIL", "HTTP %s (want {VALIDATION_REFUSAL_STATUS} for missing %s on %s)" % (resp.status_code, required, cap)


def check_enum_422(http: _Http) -> Tuple[str, str]:
    # Measured on a capability whose OWN model declares the vocabulary, with
    # the shared payload builder filling every other required field from that
    # model -- so the only thing wrong with the payload is the measured value.
    cap, enum = _cap_declaring("enum")
    if not cap:
        return "FAIL", "no capability declares an enum field to measure (not a presence skip)"
    name, _allowed = enum
    payload = dict(_sample_payload_for(cap, {{}}))
    payload[name] = "__not_in_contract__"
    resp = http.request("post", "/v1/" + cap, json=payload, headers=_auth())
    if resp.status_code == {VALIDATION_REFUSAL_STATUS}:
        return "PASS", "HTTP {VALIDATION_REFUSAL_STATUS} invalid %s" % name
    return "FAIL", "HTTP %s (want {VALIDATION_REFUSAL_STATUS} for invalid enum %s on %s)" % (resp.status_code, name, cap)


def check_ui_served_200(http: _Http) -> Tuple[str, str]:
    resp = http.request("get", "/")
    if resp.status_code != 200:
        return "FAIL", "GET / HTTP %s" % resp.status_code
    text = getattr(resp, "text", "") or ""
    ctype = ""
    headers = getattr(resp, "headers", {{}}) or {{}}
    if hasattr(headers, "get"):
        ctype = str(headers.get("content-type") or headers.get("Content-Type") or "")
    # The media type, or an <html> element the HTML parser actually finds.
    media = ctype.split(";", 1)[0].strip().lower()
    tags = _Tags()
    try:
        tags.feed(text)
    except Exception:  # noqa: BLE001 -- unparseable is simply not HTML
        pass
    if media == "text/html" or "html" in tags.tags:
        return "PASS", "GET / HTTP 200 HTML"
    return "FAIL", "GET / was 200 but not served UI (content-type=%s)" % ctype


def _contract_routes(contract: List[str]) -> List[str]:
    """The contract's routes, plus every POST route the PRODUCT declares in its
    committed openapi.json whose path ends with the same segments -- the same
    route mounted under a prefix. Matched by path shape, never by words."""
    out = list(contract)
    tails = [[s for s in str(p).split("/") if s][1:] for p in contract]
    try:
        doc = json.loads((SRC / "docs" / "openapi.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    for path, ops in (doc.get("paths") or {{}}).items():
        if not isinstance(ops, dict) or "post" not in {{str(k).lower() for k in ops}}:
            continue
        segs = [s for s in str(path).split("/") if s]
        if any(tail and segs[-len(tail):] == tail for tail in tails) and path not in out:
            out.append(path)
    return out


def check_rag_roundtrip_hit(http: _Http) -> Tuple[str, str]:
    if not RETRIEVES:
        return "SKIP", "no capability binds a block that declares the retrieval read"
    # The nonce identifies the planted content and MUST NEVER be sent as part
    # of the query request itself -- a prior version queried the exact phrase
    # it POSTed (and even resent the planted "text" in the query body), so any
    # endpoint that echoed its own request back (idiomatic REST design, not a
    # bug) satisfied this check regardless of whether retrieval ran at all.
    # A second, never-planted nonce is the negative control: a query for it
    # must come back EMPTY, or the "hit" mechanism is proven to be an echo.
    nonce = uuid.uuid4().hex[:12]
    absent_nonce = uuid.uuid4().hex[:12]
    marker = "ACCEPTANCE-PLANT-%s the reorder threshold procedure" % nonce
    ingest_paths = _contract_routes(RAG_INGEST_PATHS)
    planted = False
    for path in ingest_paths:
        resp = http.request(
            "post",
            path,
            json={{field: marker for field in RAG_INGEST_TEXT_FIELDS}},
            headers=_auth(),
        )
        if resp.status_code in {tuple(ACCEPT_STATUSES)!r}:
            planted = True
            break
    if not planted:
        return "FAIL", "RAG surface present but plant did not accept"
    query_paths = _contract_routes(RAG_QUERY_PATHS)

    def _content_hit(resp: Any, needle: str, sent: Dict[str, Any]) -> bool:
        # Retrieved content only: every top-level field whose value is exactly
        # something the request sent is the request echoed back and is
        # dropped, then the rest of the answer is searched. No field name is
        # assumed -- an endpoint that only echoes has nothing left to search.
        if resp.status_code != 200:
            return False
        try:
            data = resp.json()
        except Exception:
            return False
        if isinstance(data, list):
            return needle in json.dumps(data).lower()
        if not isinstance(data, dict):
            return False
        echoed = {{json.dumps(v, sort_keys=True) for v in sent.values()}}
        kept = {{
            k: v for k, v in data.items()
            if json.dumps(v, sort_keys=True) not in echoed
        }}
        return needle in json.dumps(kept).lower()

    positive_hit = False
    negative_leak = False
    for path in query_paths:
        pos_sent = {{"q": marker, "query": marker}}
        pos_resp = http.request("post", path, json=pos_sent, headers=_auth())
        if _content_hit(pos_resp, nonce.lower(), pos_sent):
            positive_hit = True
        neg_sent = {{"q": absent_nonce, "query": absent_nonce}}
        neg_resp = http.request("post", path, json=neg_sent, headers=_auth())
        if _content_hit(neg_resp, absent_nonce.lower(), neg_sent):
            negative_leak = True
        if positive_hit or negative_leak:
            break
    if negative_leak:
        return "FAIL", "query for a never-planted term still came back as a hit (echo, not retrieval)"
    if positive_hit:
        return "PASS", "plant->retrieve round trip confirmed in a content field, not a request echo"
    return "FAIL", "RAG surface present but query missed"


def check_single_persistence_root() -> Tuple[str, str]:
    store = SRC / "app" / "store.py"
    if not store.is_file():
        return "FAIL", "app/store.py missing"
    tree = _parse(store)
    if tree is None:
        return "FAIL", "app/store.py does not parse"
    consts = [n.value for n in ast.walk(tree)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    if "STORAGE_PATH" not in set(consts):
        return "FAIL", "STORAGE_PATH not used"
    db_names = {{c for c in consts if Path(c).suffix == ".db"}}
    if len(db_names) > 1:
        return "FAIL", "multiple db files: " + ", ".join(sorted(db_names))
    # A database opened by a call that names neither the STORAGE_PATH root nor
    # the platform file -- read from the call's own arguments.
    extra_roots = []
    for name, call in _callees(tree):
        args = {{n.value for n in ast.walk(call)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)}}
        opens_db = name == "sqlite3.connect" or (
            name == "open" and any(Path(a).suffix == ".db" for a in args)
        )
        if opens_db and "STORAGE_PATH" not in args and "platform.db" not in args:
            extra_roots.append(ast.unparse(call))
    if extra_roots:
        return "FAIL", "connect() outside STORAGE_PATH: " + extra_roots[0][:80]
    return "PASS", "one STORAGE_PATH root (%s)" % (next(iter(db_names), "platform.db"))


def check_ci_present_and_full_suite() -> Tuple[str, str]:
    ci = SRC / ".github" / "workflows" / "ci.yml"
    if not ci.is_file():
        return "FAIL", ".github/workflows/ci.yml missing"
    # ci.yml is a Factory-owned file, re-stamped from the Factory's full-suite
    # workflow before every TESTER: the measure is that the product's CI IS
    # that workflow (by digest), never a search of its lines for words.
    raw = ci.read_bytes().replace(b"\\r\\n", b"\\n")
    if hashlib.sha256(raw).hexdigest() == CI_SHA256:
        return "PASS", "CI is the Factory's full-suite workflow (python -m pytest tests)"
    return "FAIL", "ci.yml is not the Factory's full-suite workflow (edited or stale)"


def check_handler_bodies_distinct() -> Tuple[str, str]:
    actions = SRC / "app" / "actions"
    if not actions.is_dir():
        return "FAIL", "app/actions missing"
    bodies: Dict[str, List[str]] = {{}}
    for path in sorted(actions.glob("*.py")):
        if path.name.startswith("_"):
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return "FAIL", "%s does not parse" % path.name
        handle = None
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "handle":
                handle = node
                break
        if handle is None:
            continue
        chunk = ast.get_source_segment(src, handle) or ast.dump(handle)
        digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        bodies.setdefault(digest, []).append(path.name)
    twins = [names for names in bodies.values() if len(names) > 1]
    if twins:
        return "FAIL", "identical handle() bodies: " + ", ".join(twins[0])
    if len(bodies) < 2:
        return "PASS", "single handler — nothing to clone"
    return "PASS", "%d distinct handle() bodies" % len(bodies)


#: The DB-API entry points a statement is handed to -- callable NAMES,
#: compared exactly against the call's own target, never searched in text.
_SQL_ENTRY_CALLS = frozenset(
    ("execute", "executemany", "executescript", "exec_driver_sql", "text")
)


def _is_dynamic_string(node, built) -> bool:
    """Built at run time: an f-string with fields, a + or % with a string
    side, a .format() call, or a local name bound to one of those."""
    if isinstance(node, ast.JoinedStr):
        return any(isinstance(v, ast.FormattedValue) for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        for side in (node.left, node.right):
            if isinstance(side, ast.Constant) and isinstance(side.value, str):
                return True
            if _is_dynamic_string(side, built):
                return True
        return False
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    ):
        return True
    if isinstance(node, ast.Name):
        return node.id in built
    return False


def _call_target(call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _scope_nodes(scope):
    """Every node of one scope, not descending into nested functions."""
    out = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        out.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(node))
    return out


def _dynamic_sql_sites() -> List[str]:
    """file:line of every execute()-family call handed a dynamically built
    string. Decided on the syntax tree: the call's target plus the shape of
    its first argument. Core statements, compiled statements and constant
    literals with bound parameters pass."""
    sites = []
    app_dir = SRC / "app"
    if not app_dir.is_dir():
        return sites
    for path in sorted(app_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        functions = [
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        # A function that hands one of its parameters to an execute call is
        # itself an entry point for that argument position (a writer's own
        # _exec(conn, sql, params) wrapper) -- followed structurally, not by name.
        sinks = dict((name, 0) for name in _SQL_ENTRY_CALLS)
        for _ in range(3):
            for fn in functions:
                params = [a.arg for a in fn.args.args]
                for n in _scope_nodes(fn):
                    if not isinstance(n, ast.Call):
                        continue
                    callee = _call_target(n)
                    position = sinks.get(callee)
                    if position is None or len(n.args) <= position:
                        continue
                    arg = n.args[position]
                    if isinstance(arg, ast.Name) and arg.id in params:
                        sinks.setdefault(fn.name, params.index(arg.id))
        scopes = [tree] + functions
        for scope in scopes:
            nodes = _scope_nodes(scope)
            built = set()
            for _ in range(2):
                for n in nodes:
                    if isinstance(n, ast.Assign) and _is_dynamic_string(n.value, built):
                        built.update(t.id for t in n.targets if isinstance(t, ast.Name))
                    elif (
                        isinstance(n, ast.AugAssign)
                        and isinstance(n.target, ast.Name)
                        and isinstance(n.op, ast.Add)
                    ):
                        built.add(n.target.id)
            for n in nodes:
                if not isinstance(n, ast.Call):
                    continue
                position = sinks.get(_call_target(n))
                if position is None or len(n.args) <= position:
                    continue
                if _is_dynamic_string(n.args[position], built):
                    sites.append("%s:%s" % (path.relative_to(SRC).as_posix(), n.lineno))
    return sorted(set(sites))


def _health_probes_app_db() -> Tuple[bool, str]:
    """app/health.py asks app.db for the live database and runs a probe on
    it -- read from the syntax tree (imports, calls), never from its text."""
    path = SRC / "app" / "health.py"
    if not path.is_file():
        return False, "app/health.py missing"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return False, "app/health.py does not parse: %s" % exc
    db_modules = set()
    db_connect = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module == "app":
            for alias in n.names:
                if alias.name == "db":
                    db_modules.add(alias.asname or alias.name)
        elif isinstance(n, ast.ImportFrom) and n.module == "app.db":
            for alias in n.names:
                if alias.name == "connect":
                    db_connect.add(alias.asname or alias.name)
    connects = False
    executes = False
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        func = n.func
        if isinstance(func, ast.Attribute) and func.attr == "connect":
            if isinstance(func.value, ast.Name) and func.value.id in db_modules:
                connects = True
        if isinstance(func, ast.Name) and func.id in db_connect:
            connects = True
        if isinstance(func, ast.Attribute) and func.attr == "execute":
            executes = True
    if not connects:
        return False, "app/health.py does not take its database connection from app.db"
    if not executes:
        return False, "app/health.py never runs a probe on the database"
    return True, "health probes the database app.db selects"


def check_health_fail_closed() -> Tuple[str, str]:
    probes, why = _health_probes_app_db()
    if not probes:
        return "FAIL", why
    missing = ROOT / ".acceptance-missing-disk"
    previous = os.environ.get("STORAGE_PATH")
    os.environ["STORAGE_PATH"] = str(missing)
    try:
        from app.health import evaluate_health

        code, body = evaluate_health()
    except Exception as exc:
        return "FAIL", "evaluate_health raised %s" % type(exc).__name__
    finally:
        # Restore so later checks (docker_health_200) probe the real disk,
        # not the poisoned path from this fail-closed probe.
        if previous is None:
            os.environ.pop("STORAGE_PATH", None)
        else:
            os.environ["STORAGE_PATH"] = previous
    if code == 200 or (isinstance(body, dict) and body.get("ok") is True):
        return "FAIL", "health stayed 200/ok when STORAGE_PATH is missing"
    if int(code) in (503, 500) and (not body.get("ok")):
        return "PASS", "HTTP %s fail-closed" % code
    return "FAIL", "health code=%s ok=%s" % (code, (body or {{}}).get("ok"))


def check_migration_no_create_all() -> Tuple[str, str]:
    """Schema belongs to alembic, not to boot.

    create_all() builds tables from whatever the models happen to say at
    start-up, so the migration becomes decoration and the first deploy
    against a real database diverges from what the tests ran on.
    """
    def _calls(tree, attr):
        return [name for name, call in _callees(tree)
                if isinstance(call.func, ast.Attribute) and call.func.attr == attr
                or isinstance(call.func, ast.Name) and call.func.id == attr]

    offenders = []
    for rel in ("app/store.py", "app/main.py", "app/db.py", "app/models.py"):
        tree = _parse(SRC / rel)
        if tree is not None and _calls(tree, "create_all"):
            offenders.append(rel)
    versions = SRC / "alembic" / "versions"
    if not versions.is_dir():
        return "FAIL", "alembic/versions missing: the schema is not migrated"
    revisions = sorted(versions.glob("*.py"))
    if not revisions:
        return "FAIL", "no alembic revision: the schema is not migrated"
    has_ddl = False
    for revision in revisions:
        tree = _parse(revision)
        if tree is None:
            continue
        if _calls(tree, "create_all"):
            offenders.append("alembic/versions/" + revision.name)
        # Real DDL: a call to alembic's op.create_table, resolved by import.
        if any(name.rpartition(".")[0].rpartition(".")[2] == "op"
               for name in _calls(tree, "create_table")):
            has_ddl = True
    if offenders:
        return "FAIL", "create_all in " + ", ".join(sorted(set(offenders)))
    if not has_ddl:
        return "FAIL", "no op.create_table in any revision: not real DDL"
    return "PASS", "%d revision(s), real DDL, no create_all" % len(revisions)


def _capability_stems() -> List[str]:
    actions = SRC / "app" / "actions"
    if not actions.is_dir():
        return []
    return sorted(
        f.stem for f in actions.glob("*.py") if not f.stem.startswith("_")
    )


#: The client-error statuses a counter-case asserts (HTTP 4xx).
NEGATIVE_CODES = frozenset({{400, 401, 403, 404, 409, 422, 429}})


def _is_4xx(node: ast.AST) -> bool:
    return (isinstance(node, ast.Constant) and isinstance(node.value, int)
            and not isinstance(node.value, bool) and 400 <= node.value < 500)


def _status_compare(node: ast.AST) -> bool:
    """``<x>.status_code == 4xx`` (either side), read from the syntax tree."""
    if not isinstance(node, ast.Compare):
        return False
    sides = [node.left, *node.comparators]
    return any(isinstance(s, ast.Attribute) and s.attr == "status_code" for s in sides) and any(
        _is_4xx(s) for s in sides
    )


def _negative_hits(func: ast.AST) -> int:
    """Count counter-case ASSERTIONS in one test function, from its AST.

    Counting bare 4xx anywhere in a file made a 27KB shared route test hand
    its hits to every capability named in it, and a suite with nine
    counter-cases in total scored four-per-capability. A gate that passes
    what it exists to refuse is worse than no gate. Counted: each
    ``pytest.raises`` context, each ``status_code == 4xx`` comparison, and
    each ``assert`` naming a client-error status without such a comparison.
    """
    alias = _aliases(func)
    hits = 0
    counted = set()
    for node in ast.walk(func):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call):
                    head, _, rest = _dotted(expr.func).partition(".")
                    if rest == "raises" and alias.get(head, head) == "pytest":
                        hits += 1
        elif _status_compare(node):
            hits += 1
            counted.add(id(node))
    for node in ast.walk(func):
        if isinstance(node, ast.Assert):
            inner = list(ast.walk(node.test))
            if any(id(n) in counted for n in inner):
                continue
            if any(isinstance(n, ast.Constant) and n.value in NEGATIVE_CODES
                   and not isinstance(n.value, bool) for n in inner):
                hits += 1
    return hits


def check_negative_floor() -> Tuple[str, str]:
    """Four counter-cases per capability, attributed per TEST FUNCTION.

    Attribution is the whole difficulty. Counting hits in any file that
    merely mentions a capability credited every capability with the two big
    shared test files, so a suite with nine counter-cases in total scored
    four-per-capability and passed the gate that exists to refuse it. A
    counter-case counts for a capability only when the test that makes the
    assertion is the test that exercises the capability.
    """
    stems = _capability_stems()
    if not stems:
        return "FAIL", "no app/actions/: nothing to count against"
    tests = SRC / "tests"
    if not tests.is_dir():
        return "FAIL", "no tests/"
    per = dict((stem, 0) for stem in stems)
    for path in sorted(tests.rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            segment = ast.get_source_segment(text, node) or ""
            if not segment:
                continue
            hits = _negative_hits(node)
            if not hits:
                continue
            for stem in stems:
                if stem in segment or stem in node.name:
                    per[stem] += hits
    thin = ["%s=%d" % (s, per[s]) for s in stems if per[s] < 4]
    if thin:
        return "FAIL", "under 4 counter-cases: " + ", ".join(thin)
    return "PASS", "%d capabilities, each with >=4 counter-cases" % len(stems)


def check_postgres_boot_200(http: _Http) -> Tuple[str, str]:
    """store.py routes through app.db, and DATABASE_URL is actually honoured.

    The old shape of this defect: DATABASE_URL read in the kernel config and
    used nowhere, while store.py held its own sqlite3 handle. The operator
    sets the variable, the platform accepts it without complaint and writes a
    SQLite file onto the container disk. Reading the variable somewhere is
    not the bar; the store taking its connection from one place is.
    """
    db = SRC / "app" / "db.py"
    store = SRC / "app" / "store.py"
    if not db.is_file():
        return "FAIL", "app/db.py missing: nothing decides the backend"
    db_tree = _parse(db)
    if db_tree is None or "DATABASE_URL" not in _env_reads(db_tree):
        return "FAIL", "app/db.py does not read DATABASE_URL"
    if not store.is_file():
        return "FAIL", "app/store.py missing"
    store_tree = _parse(store)
    if store_tree is None:
        return "FAIL", "app/store.py does not parse"
    # From the syntax tree: store.py imports app.db, and opens no database of
    # its own (a resolved sqlite3.connect / create_engine call).
    routes = _imports_module(store_tree, "app.db")
    opens_own = any(
        name == "sqlite3.connect" or name.rpartition(".")[2] == "create_engine"
        for name, _call in _callees(store_tree)
    )
    if not routes:
        return (
            "FAIL",
            "app/store.py does not take its connection from app.db, so a set "
            "DATABASE_URL is read and ignored",
        )
    if opens_own:
        return (
            "FAIL",
            "app/store.py opens its own database beside app.db; one place must "
            "decide the backend or the two disagree",
        )
    unmeasured = _only_the_gate_measures("STORE_POSTGRES_BOOT")
    if unmeasured is not None:
        return unmeasured
    measured = (os.environ.get("STORE_POSTGRES_BOOT") or "").strip()
    if measured != "200":
        return "FAIL", "STORE_POSTGRES_BOOT=%r (gate must boot it on Postgres)" % measured
    resp = http.request("get", "/health")
    if resp.status_code != 200:
        return "FAIL", "postgres boot env=200 but /health is %s" % resp.status_code
    return "PASS", "store.py routes through app.db; boots on Postgres"


def check_one_live_connector() -> Tuple[str, str]:
    measured = (os.environ.get("STORE_LIVE_CONNECTOR") or "").strip()
    if not measured:
        return "FAIL", "STORE_LIVE_CONNECTOR unset: no real delivery was observed"
    if measured.lower() in ("0", "false", "mocked", "no"):
        return "FAIL", "the only observed delivery was mocked (%s)" % measured
    return "PASS", "live delivery observed: %s" % measured


def check_metrics_served(http: _Http) -> Tuple[str, str]:
    """Measured on the booted app. Shipping the module is not mounting it."""
    resp = http.request("get", "/metrics")
    if resp.status_code != 200:
        mounted = "mount_observability" in (
            (SRC / "app" / "main.py").read_text(encoding="utf-8", errors="ignore")
            if (SRC / "app" / "main.py").is_file()
            else ""
        )
        hint = (
            "app/main.py never calls mount_observability(app)"
            if not mounted
            else "mount_observability is called but /metrics does not answer"
        )
        return "FAIL", "GET /metrics is %s -- %s" % (resp.status_code, hint)
    try:
        body = resp.text
    except Exception:
        body = ""
    low = body.lower()
    if not any(k in low for k in ("count", "total", "requests")):
        return "FAIL", "/metrics answers 200 but reports no request count"
    if not any(k in low for k in ("latency", "duration", "seconds")):
        return "FAIL", "/metrics reports no latency"
    return "PASS", "/metrics serves a request count and latency"


def check_backup_restore_roundtrip() -> Tuple[str, str]:
    measured = (os.environ.get("STORE_BACKUP_RESTORE") or "").strip().lower()
    if measured in ("ok", "pass", "1", "true"):
        return "PASS", "backup restored with rows intact"
    if not (SRC / "app" / "backup.py").is_file():
        return "FAIL", "no app/backup.py"
    return "FAIL", "STORE_BACKUP_RESTORE=%r: a backup nobody restored is a file" % measured


def check_bench_p95() -> Tuple[str, str]:
    measured = (os.environ.get("STORE_BENCH_P95_MS") or "").strip()
    if not measured:
        return "FAIL", "STORE_BENCH_P95_MS unset: p95 was not measured"
    try:
        value = float(measured)
    except ValueError:
        return "FAIL", "STORE_BENCH_P95_MS=%r is not a number" % measured
    if value >= 500.0:
        return "FAIL", "p95 %.0fms over the 500ms budget" % value
    return "PASS", "p95 %.0fms under 500ms" % value


def check_audit_clean() -> Tuple[str, str]:
    ci = SRC / ".github" / "workflows" / "ci.yml"
    if not ci.is_file():
        return "FAIL", ".github/workflows/ci.yml missing"
    text = ci.read_text(encoding="utf-8", errors="ignore").lower()
    missing = [t for t in ("pip-audit", "bandit") if t not in text]
    if missing:
        return "FAIL", "CI does not run " + ", ".join(missing)
    dynamic = _dynamic_sql_sites()
    if dynamic:
        return "FAIL", "dynamically built SQL reaches an execute call: " + ", ".join(dynamic[:6])
    unmeasured = _only_the_gate_measures("STORE_AUDIT_CLEAN")
    if unmeasured is not None:
        return unmeasured
    measured = (os.environ.get("STORE_AUDIT_CLEAN") or "").strip().lower()
    if measured in ("0", "false", "dirty"):
        return "FAIL", "bandit/pip-audit reported HIGH or SQL-construction findings"
    if measured not in ("1", "true", "clean"):
        # F3: a scan that is CONFIGURED but never RUN is not a pass. A product
        # shipped 16 bandit SQL findings and still scored 21/21 because this
        # check only proved ci.yml mentions the scanners. The store gate must
        # RUN the scan and export STORE_AUDIT_CLEAN, exactly as it measures
        # STORE_POSTGRES_BOOT -- unmeasured is a fail, never a silent pass.
        return "FAIL", "STORE_AUDIT_CLEAN unmeasured: the gate must run bandit/pip-audit and read the verdict, not just confirm the scan is configured"
    return "PASS", "scan ran and is clean (no HIGH, no SQL-construction findings)"


def check_no_token_literal() -> Tuple[str, str]:
    """Runtime app/** carries no baked bearer token and no token fallback.

    The well-known dev values live in the TEST bootstrap (tests/conftest.py)
    only; the deploy env carries a per-package random token. A baked token is
    a production backdoor -- live 2026-09-29, a second-tenant token literal
    opened another tenant on a CERTIFIED product with a world-known value.
    An EMPTY default (or two quotes with nothing between) is the fail-closed
    pattern and is allowed.
    """
    app_dir = SRC / "app"
    if not app_dir.is_dir():
        return "FAIL", "app/ missing"
    # The world-known development token values (the test bootstrap's), as
    # exact constants -- a value comparison, not a search of text.
    literals = frozenset({{"dev-local-token-b", "dev-local-token"}})
    readers = {{"os.environ.get", "os.getenv"}}

    def _token_read(node: ast.AST, calls: Dict[int, str]) -> bool:
        return (isinstance(node, ast.Call) and calls.get(id(node)) in readers
                and bool(node.args) and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "PLATFORM_TOKEN")

    def _nonempty_str(node: ast.AST) -> bool:
        return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != ""

    for path in sorted(app_dir.rglob("*.py")):
        tree = _parse(path)
        if tree is None:
            continue
        rel = path.relative_to(SRC)
        calls = {{id(call): name for name, call in _callees(tree)}}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in literals:
                return "FAIL", "%s:%s bakes %s -- runtime tokens come from the environment only" % (rel, node.lineno, node.value)
            if _token_read(node, calls) and len(node.args) > 1 and _nonempty_str(node.args[1]):
                return "FAIL", "%s:%s has a token default -- read the environment and fail closed instead" % (rel, node.lineno)
            if (isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
                    and any(_token_read(v, calls) for v in node.values)
                    and any(_nonempty_str(v) for v in node.values)):
                return "FAIL", "%s:%s has an env-fallback token -- read the environment and fail closed instead" % (rel, node.lineno)
    return "PASS", "no token literal or fallback in runtime app/**"


def check_openapi_committed() -> Tuple[str, str]:
    path = SRC / "docs" / "openapi.json"
    if not path.is_file():
        return "FAIL", "docs/openapi.json missing"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return "FAIL", "openapi is not JSON: %s" % exc
    if not str(doc.get("openapi") or "").startswith("3."):
        return "FAIL", "openapi version is not 3.x"
    paths = doc.get("paths")
    if not isinstance(paths, dict) or "/health" not in paths:
        return "FAIL", "openapi paths omit /health"
    v1 = [p for p in paths if str(p).startswith("/v1/")]
    if not v1:
        return "FAIL", "openapi has no /v1/ paths"
    return "PASS", "openapi 3.x with %d paths" % len(paths)


#: Advice only, appended to the self-check's SKIP reason: the image is measured
#: by the Store gate alone, but the writer may build it itself to catch a
#: Dockerfile that cannot build before the gate does.
IMAGE_BUILD_ADVICE = (
    "; build it yourself with `docker build .` -- the Dockerfile must build "
    "without dev dependencies or running the test suite"
)


def check_docker_health_200(http: _Http) -> Tuple[str, str]:
    unmeasured = _only_the_gate_measures("STORE_DOCKER_HEALTH")
    if unmeasured is not None:
        status, reason = unmeasured
        return status, reason + IMAGE_BUILD_ADVICE
    measured = (os.environ.get("STORE_DOCKER_HEALTH") or "").strip()
    if measured != "200":
        return "FAIL", "STORE_DOCKER_HEALTH=%r (Store gate must measure container /health=200)" % measured
    resp = http.request("get", "/health")
    if resp.status_code != 200:
        return "FAIL", "container health env=200 but GET /health is %s" % resp.status_code
    return "PASS", "docker health 200"


{_CREATE_THROUGH_DECLARED_ENTITY}

class _CreateClient:
    """The shared payload builder posts through a module-level ``client``;
    in the harness that is the harness's own HTTP client."""

    def __init__(self, http: _Http):
        self.http = http

    def post(self, path, json=None, headers=None):
        return self.http.request("post", path, json=json, headers=headers)


def _persisting_caps() -> List[str]:
    """Capabilities that DECLARE a persisted entity -- read by the same
    resolver as the round-trip probes, never assumed from the capability id --
    minus declared placeholders, in capability order. A read-only capability
    (declared entity None) and an undeclared one are not creatable here."""
    try:
        from app.placeholders import PLACEHOLDER_CONNECTORS
    except Exception:
        PLACEHOLDER_CONNECTORS = {{}}
    # The resolver reads the declarations once at import; read them again
    # now that the harness has its environment, so a route module that could
    # not import earlier is not mistaken for "declares nothing".
    globals()["_ROUTE_ENTITIES"] = _route_entities()
    globals()["_JOBS_ENTITIES"] = _jobs_entities()
    models = _models()
    out = []
    for cap in _cap_order():
        if PLACEHOLDER_CONNECTORS.get(cap):
            continue
        entity, declared = _declared_entity(cap, models.get(cap))
        if declared and entity:
            out.append(cap)
    return out


def _created_id(resp: Any) -> Tuple[Any, str]:
    """(record id, refusal) read by the route's create contract. HTTP 200
    with {OK_KEY!r}: false is a refusal, never a success."""
    try:
        body = resp.json()
    except Exception:
        body = None
    if resp.status_code not in {tuple(ACCEPT_STATUSES)!r}:
        detail = body.get({ERROR_KEY!r}) if isinstance(body, dict) else ""
        return None, "HTTP %s %s" % (resp.status_code, detail or "")
    if not isinstance(body, dict):
        return None, "answered no JSON object"
    if body.get({OK_KEY!r}) is False:
        return None, "refused: %s" % (body.get({ERROR_KEY!r}) or {OK_KEY!r} + ": false")
    record = body.get({STORED_RECORD_KEY!r})
    if not isinstance(record, dict) or record.get({RECORD_ID_KEY!r}) in (None, ""):
        return None, "accepted but answered no %s.%s" % ({STORED_RECORD_KEY!r}, {RECORD_ID_KEY!r})
    return record.get({RECORD_ID_KEY!r}), ""


def check_cross_tenant_404(http: _Http) -> Tuple[str, str]:
    """Write as tenant A, read as tenant B: the read must be 404.

    404-not-403 is the platform's stated doctrine -- cross-tenant access
    never leaks existence. A 200/403 here means the product is
    single-tenant by construction (the Phase-2 0.2 defect).

    The record is created through a capability that DECLARES a persisted
    entity, with the shared payload builder every emitted suite uses (the
    product's own model fills required fields; a rejection stated as data is
    corrected once). A refused create is reported with the product's own
    refusal as evidence -- never as "no stored id".
    """
    previous = os.environ.get("TENANT_TOKENS")
    os.environ["TENANT_TOKENS"] = CHECK_TENANT_TOKENS
    try:
        caps = _persisting_caps()
        if not caps:
            return "SKIP", (
                "no capability declares a persisted entity -- isolation needs a "
                "stored record, so it is not judgeable on this product"
            )
        globals()["client"] = _CreateClient(http)
        refusals = []
        for cap in caps:
            resp, _corrections = _post_accepting(
                "/v1/" + cap, {{}}, {{"Authorization": "Bearer token-a"}}, cap,
            )
            record_id, refusal = _created_id(resp)
            if record_id is None:
                refusals.append("%s: %s" % (cap, refusal))
                continue
            read = http.request(
                "get", "/v1/%s/%s" % (cap, record_id),
                headers={{"Authorization": "Bearer token-b"}},
            )
            if read.status_code == 404:
                return "PASS", "tenant B read of tenant A %s record: HTTP 404" % cap
            return "FAIL", "tenant B read of tenant A %s record returned HTTP %s (want 404)" % (cap, read.status_code)
        return "FAIL", (
            "tenant A could not store a record through any capability that "
            "declares a persisted entity -- " + "; ".join(refusals)
        )[:700]
    finally:
        if previous is None:
            os.environ.pop("TENANT_TOKENS", None)
        else:
            os.environ["TENANT_TOKENS"] = previous


{_CONCURRENT_WRITES}
{_RENDERED_MARKER_READER}

def check_authorship_floor() -> Tuple[str, str]:
    # Judge the product by the product. Each handler's module-level
    # authorship marker, read from the syntax tree by a reader RENDERED in
    # here from the Factory's canonical one (with its source vocabulary) at
    # stamp time: a delivered product never carries the Factory package, and
    # importing it failed this line on every honest build (2026-10-04). A
    # docstring sentence decides nothing.
    agent_prefixes = {tuple(_AGENT_SOURCE_PREFIXES)!r}
    agent_exact = {sorted(_AGENT_SOURCE_EXACT)!r}
    floor_min = {_FULL_PILOT_MIN_AUTHORED}
    receipt = {{}}
    for rel in ("docs/coder_receipt.json", "docs/build_provenance.json"):
        path = SRC / rel
        if path.is_file():
            try:
                receipt.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    actions = SRC / "app" / "actions"
    authored = 0
    for path in (sorted(actions.glob("*.py")) if actions.is_dir() else []):
        try:
            source = _authored_by(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if source and (source.startswith(tuple(agent_prefixes)) or source.lower() in agent_exact):
            authored += 1
    n_required = receipt.get("n_required") or receipt.get("n_required_capabilities")
    try:
        n_required = int(n_required) if n_required is not None else None
    except (TypeError, ValueError):
        n_required = None
    need = floor_min if not n_required or n_required <= 0 else min(floor_min, max(1, n_required))
    if authored >= need:
        return "PASS", "authored=%s need≥%s" % (authored, need)
    return "FAIL", "authored=%s below need≥%s" % (authored, need)


#<<DEPLOY_TIME_SETTINGS>>


def main() -> int:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    # Before anything imports ``app``: a credential the operator supplies at
    # deploy time must not fail acceptance on the build box. Shared, word for
    # word, with tests/conftest.py.
    _stand_in_for_deploy_time_settings(ROOT)
    http, cm = _client()
    results: List[Tuple[str, str, str]] = []
    try:
        # Generated from the floor, not written out here. A hand-kept list
        # is the second copy the floor file exists to abolish: it drifts the
        # moment a check is added, and the build is then graded against a
        # roster nobody updated. Order is the floor's order.
        import inspect as _inspect

        runners = []
        for _name in CHECKS:
            _fn = globals().get("check_" + _name)
            if _fn is None:
                runners.append(
                    (_name, (lambda n=_name: ("FAIL", "no check_%s in the harness" % n)))
                )
                continue
            if "http" in _inspect.signature(_fn).parameters:
                runners.append((_name, (lambda f=_fn: f(http))))
            else:
                runners.append((_name, _fn))
        global SRC
        for name, fn in runners:
            SRC = REPO if name in REPO_CHECKS else ROOT
            try:
                status, detail = fn()
            except Exception as exc:
                status, detail = "FAIL", "%s: %s" % (type(exc).__name__, exc)
            if status == "FAIL" and name in ADVISORY:
                status, detail = "SKIP", "advisory (not on the floor yet): " + detail
            results.append((name, status, detail))
            print("%s %s — %s" % (status, name, detail))
            # The typed record the Factory reads; the line above is for people.
            print(json.dumps({{RECORD_KEY: {{"name": name, "status": status, "detail": detail}}}}))
    finally:
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception:
                pass
    satisfied = sum(1 for _n, status, _d in results if status in {{"PASS", "SKIP"}})
    print("ACCEPTANCE: %d/%d" % (satisfied, REQUIRED))
    if SELF_CHECK:
        print("SELF-CHECK: the same %d checks the Store gate scores; fix every FAIL before you declare done" % REQUIRED)
    print(json.dumps({{TOTAL_KEY: {{"passed": satisfied, "required": REQUIRED}}}}))
    if results and results[-1][0] != "authorship_floor":
        print("FAIL harness — authorship_floor was not last")
        return 1
    if satisfied < REQUIRED or any(status == "FAIL" for _n, status, _d in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''')


def factory_renders(
    product_name: str, cap_ids: Sequence[str], blueprint: Any = None
) -> Dict[Path, str]:
    """Every file the Factory stamps into a workspace, path -> text.

    The ONE table both stampers write from, and the table
    ``factory_rendered_paths`` reads -- so "which files are the Factory's" is
    the same fact as "which files the Factory writes", by construction, and a
    file added here is owned here without a second list anywhere.
    """
    from app.factory.build.placeholder_connectors import (
        PRODUCT_MODULE,
        render_product_module,
    )

    return {
        ACCEPTANCE_SCRIPT_REL: render_acceptance_script(blueprint),
        # Declared placeholder connectors, read by the routes, the generated
        # suites and the probes alike.
        Path(PRODUCT_MODULE): render_product_module(blueprint),
        AUTH_REL: render_auth_module(),
        Path(TENANCY_REL): render_tenancy_module(),
        GITHUB_CI_REL: render_github_ci(),
        OPENAPI_REL: render_openapi(product_name, cap_ids),
        UI_INDEX_REL: render_ui_index(product_name),
    }


#: Factory files not stamped here but still the Factory's: the re-entry
#: refresh re-renders these from templates (factory_refresh), and the Store
#: gate's own workflow is pushed by the Factory (branch_attach).
_OTHER_FACTORY_RELS = ("scripts/release_gate.py", "requirements.txt", "constraints.txt")


def factory_rendered_paths() -> Tuple[str, ...]:
    """Relative paths of every file the Factory renders into a build.

    Derived from the stamp table, never kept by hand: a failure whose subject
    is one of these is the Factory's failure (acceptance_floor.owner_of).
    """
    from app.factory.build.branch_attach import STORE_GATE_PATH

    rels = [str(p).replace("\\", "/") for p in factory_renders("platform", ())]
    rels += list(_OTHER_FACTORY_RELS)
    rels.append(str(STORE_GATE_PATH).replace("\\", "/"))
    return tuple(dict.fromkeys(rels))


def stamp_acceptance_artifacts(
    workspace: Any,
    *,
    product_name: str,
    cap_ids: Sequence[str],
    blueprint: Any = None,
) -> None:
    """WRITER / ProductGenerator emit the harness and the files it measures."""
    for rel, text in factory_renders(product_name, cap_ids, blueprint).items():
        workspace.write_text(rel, text)


def stamp_acceptance_harness(workspace: Any, *, blueprint: Any = None) -> None:
    """Stamp scripts/acceptance.py BEFORE the writer starts.

    The CodeWhale writer ran first and found no harness, so it wrote its own
    (live 665da6da: "scripts/acceptance.py: 15 measured checks, k/k") and
    self-verified against that; the Factory's 22-check harness was stamped
    only afterwards, and the Store gate failed three checks the writer had
    never measured. The same table entry the gate's stamp writes, so the
    writer's ``--self-check`` run scores the same N checks the gate will.
    """
    rel = ACCEPTANCE_SCRIPT_REL
    workspace.write_text(rel, factory_renders("platform", (), blueprint)[rel])


def stamp_acceptance_into_path(
    root: Path | str,
    *,
    product_name: str = "platform",
    cap_ids: Optional[Sequence[str]] = None,
    blueprint: Any = None,
) -> None:
    dest = Path(root)
    for rel, text in factory_renders(product_name, cap_ids or (), blueprint).items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    # The rendered CI installs with ``-c constraints.txt``; a tree stamped
    # without the scaffold (an attach, a fixture) gets the pins too.
    from app.factory.build.dependency_pins import CONSTRAINTS_REL, constraints_for_tree

    if not (dest / CONSTRAINTS_REL).is_file():
        (dest / CONSTRAINTS_REL).write_text(constraints_for_tree(dest), encoding="utf-8")


def evaluate_acceptance(
    root: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
    python: Optional[str] = None,
    docker_health: Optional[str] = None,
) -> AcceptanceReport:
    """Run the stamped harness (or a temp copy) against *root*."""
    dest = Path(root)
    script = dest / ACCEPTANCE_SCRIPT_REL
    if not script.is_file():
        stamp_acceptance_into_path(dest)
        script = dest / ACCEPTANCE_SCRIPT_REL
    run_env = {**os.environ, **dict(env or {})}
    if docker_health is not None:
        run_env["STORE_DOCKER_HEALTH"] = str(docker_health)
    import subprocess

    proc = subprocess.run(
        [python or sys_executable(), str(script)],
        cwd=str(dest),
        capture_output=True,
        text=True,
        env=run_env,
        timeout=120,
    )
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    report = parse_acceptance_output(text)
    if proc.returncode != 0 and report.ok:
        report.ok = False
        report.detail += "; process exited %s" % proc.returncode
    return report


def sys_executable() -> str:
    import sys

    return sys.executable


def _docker_available(ctx: "GateContext") -> bool:
    if ctx.runner is not None:
        return True
    return shutil.which("docker") is not None


def gate_store_acceptance(ctx: "GateContext") -> "GateResult":
    """STORE: run scripts/acceptance.py inside the Store-built Docker image."""
    from app.factory.build.gates import GateResult

    script = ctx.workspace / ACCEPTANCE_SCRIPT_REL
    if not script.is_file():
        report = missing_acceptance_report(detail="scripts/acceptance.py is not stamped")
        write_acceptance_report(ctx.workspace, report)
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason="acceptance_script_missing",
            detail="STORE (acceptance): scripts/acceptance.py is missing",
            findings=["missing scripts/acceptance.py"],
            payload=report.to_json(),
        )

    if not _docker_available(ctx):
        report = missing_acceptance_report(
            detail="docker is required — Store-green runs acceptance inside the image"
        )
        write_acceptance_report(ctx.workspace, report)
        return GateResult(
            ok=False,
            gate=GATE_NAME,
            reason="docker_unavailable",
            detail="STORE (acceptance): docker is not available; will not pass on a host-side skip",
            findings=["docker CLI missing — Store-green is image-measured"],
            payload=report.to_json(),
        )

    tag = "store-accept-" + hashlib.sha256(str(ctx.workspace.resolve()).encode()).hexdigest()[:12]
    name = tag + "-ctr"
    findings: List[str] = []

    def _run(argv: List[str]) -> Any:
        return ctx.run(argv)

    try:
        built = _run(["docker", "build", "-t", tag, "."])
        if getattr(built, "returncode", 1) != 0:
            err = ((getattr(built, "stderr", "") or "") + (getattr(built, "stdout", "") or "")).strip()
            findings.append((err or "docker build failed")[:400])
            report = missing_acceptance_report(detail="docker build failed")
            write_acceptance_report(ctx.workspace, report)
            return GateResult(
                ok=False,
                gate=GATE_NAME,
                reason="docker_build_failed",
                detail="STORE (acceptance): docker build failed",
                findings=findings[:8],
                payload=report.to_json(),
            )
        started = _run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "-e",
                "PLATFORM_TOKEN=dev-local-token",
                tag,
            ]
        )
        if getattr(started, "returncode", 1) != 0:
            report = missing_acceptance_report(detail="docker run failed")
            write_acceptance_report(ctx.workspace, report)
            return GateResult(
                ok=False,
                gate=GATE_NAME,
                reason="docker_run_failed",
                detail="STORE (acceptance): docker run failed",
                findings=[(getattr(started, "stderr", "") or "docker run failed")[:400]],
                payload=report.to_json(),
            )
        health_code = "000"
        probe = (
            "import urllib.request, time, sys\n"
            "for _ in range(30):\n"
            "    try:\n"
            "        r = urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)\n"
            "        print(r.status)\n"
            "        sys.exit(0)\n"
            "    except Exception:\n"
            "        time.sleep(1)\n"
            "print('000')\n"
            "sys.exit(1)\n"
        )
        for _attempt in range(8):
            probed = _run(["docker", "exec", name, "python3", "-c", probe])
            out = ((getattr(probed, "stdout", "") or "") + "\n" + (getattr(probed, "stderr", "") or ""))
            match = re.search(r"\b(200|503|000)\b", out)
            if match:
                health_code = match.group(1)
            if health_code == "200" or getattr(probed, "returncode", 1) == 0 and "200" in out:
                health_code = "200"
                break
            time.sleep(0.05)
        ran = _run(
            [
                "docker",
                "exec",
                "-e",
                "STORE_DOCKER_HEALTH=" + health_code,
                "-e",
                "PLATFORM_TOKEN=dev-local-token",
                name,
                "python3",
                "scripts/acceptance.py",
            ]
        )
        text = (getattr(ran, "stdout", "") or "") + "\n" + (getattr(ran, "stderr", "") or "")
        report = parse_acceptance_output(text)
        report.via = "docker exec python3 scripts/acceptance.py"
        write_acceptance_report(ctx.workspace, report)
        if not acceptance_is_kk(report):
            failed = [
                f"{line.name}={line.status}: {line.detail}"
                for line in report.lines
                if not line.satisfied
            ]
            return GateResult(
                ok=False,
                gate=GATE_NAME,
                reason="acceptance_not_kk",
                detail="STORE (acceptance): %s/%s inside the Store-built image" % (report.passed, report.total),
                findings=failed[:12] or ["acceptance is not k/k"],
                payload=report.to_json(),
            )
        return GateResult(
            ok=True,
            gate=GATE_NAME,
            detail="STORE (acceptance): %s/%s inside the Store-built image" % (report.passed, report.total),
            payload=report.to_json(),
        )
    finally:
        _run(["docker", "rm", "-f", name])
