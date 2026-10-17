"""S3 Domain Pack — the build's own pack, bound to kernel contracts.

The numbered 15-field contract is ``delivery_standard.DOMAIN_PACK_FIELDS``
(section 3 of ``product_delivery_standard.md``). This module does not invent
a second list. RoleRunner WRITER ships the pack as ``docs/domain_pack.json``.

Each field carries a value *and* a kernel binding (ActionSpec / execute_action
/ permissions / S12 outcomes). A markdown essay without kernel bindings fails
the gate. LotDesk-class empty packs fail the gate. The fixture is not patched.

The pack is the BUILD'S OWN: its values come from the build's blueprint
(``brief_compiler.synthesize_domain_pack`` -- empty slots stay named, never
invented) and its kernel bindings from the 15-field contract below. This
module used to hold one product's pack (a dealership: vehicle status machine,
VIN schema, F&I roles) and shipped it into every product's
``docs/domain_pack.json`` whatever its vertical. A product's domain content
belongs to that product's Store kit, never to the Factory.

Evidence: ``build/stages/S3_domain_pack.json`` + reread twin.
Does not emit PILOT_READY.
"""

from __future__ import annotations

import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from app.factory.build import probe_set

from app.cerebrum_product_kernel.contract.runtime import execute_action
from app.factory.build.domain_acceptance import (
    OUTCOMES,
    PROCESS_PERMISSION,
    READ_PERMISSION,
    WRITE_PERMISSION,
)
from app.factory.build.lotdesk_gate import resolve_lotdesk_fixture
from app.factory.delivery_standard import DOMAIN_PACK_FIELDS, _fmt, render
from app.factory.generator import git_head

EMITTER_ID = "app.factory.build.domain_pack.evaluate_domain_pack"
STAGE_NAME = "DOMAIN_PACK"
STAGE = probe_set.stage_id(STAGE_NAME)
SCHEMA_VERSION = "domain_pack.v1"
PACK_REL = Path("docs") / "domain_pack.json"
KERNEL_ENTRY = "app.cerebrum_product_kernel.contract.runtime.execute_action"

#: Where the numbered 15-field contract lives. REASONING_KERNEL.md Phase 2 is
#: a different, unnumbered directory spec — not this gate.
FIELD_CONTRACT_SOURCES: tuple[str, ...] = (
    "backend/app/factory/delivery_standard.py:DOMAIN_PACK_FIELDS",
    "backend/app/factory/standards/product_delivery_standard.md:section 3",
    "backend/app/factory/standards/domain_packs/buildops_construction.md",
    "docs/PRODUCT_DELIVERY_STANDARD.md",
)

class DomainPackError(ValueError):
    """Missing field, empty pack, or unbound kernel contract."""


def _binding(
    *,
    action_ids: List[str],
    permissions: List[str],
    contracts: List[str],
    outcomes: Optional[List[str]] = None,
    confirmation_required: bool = False,
    input_schema: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    kernel: Dict[str, Any] = {
        "entry": KERNEL_ENTRY,
        "action_ids": list(action_ids),
        "permissions": list(permissions),
        "contracts": list(contracts),
        "outcomes": list(outcomes or []),
        "confirmation_required": confirmation_required,
    }
    if input_schema is not None:
        kernel["input_schema"] = input_schema
    if extra:
        kernel.update(extra)
    return kernel


def _field(value: Any, kernel: Mapping[str, Any]) -> Dict[str, Any]:
    return {"value": value, "kernel": dict(kernel)}


_CRUD = ["product.create", "product.read", "product.update", "product.delete", "product.list"]

#: The kernel binding each contract field carries. This is the 15-field
#: contract's own shape (which kernel actions, permissions and contracts make a
#: field enforceable) -- it names no product, vertical or capability.
FIELD_BINDINGS: Dict[str, Dict[str, Any]] = {
    "domain_purpose": dict(
        action_ids=_CRUD, permissions=[WRITE_PERMISSION, READ_PERMISSION],
        contracts=[KERNEL_ENTRY], outcomes=["create_persists", "read_returns_persisted"]),
    "primary_users": dict(
        action_ids=["product.read"], permissions=[READ_PERMISSION],
        contracts=["app.cerebrum_product_kernel.contract.models.ActionContext"]),
    "required_roles": dict(
        action_ids=_CRUD + ["product.enqueue", "product.process"],
        permissions=[WRITE_PERMISSION, READ_PERMISSION, PROCESS_PERMISSION],
        contracts=["app.cerebrum_product_kernel.contract.models.ActionSpec.permissions"]),
    "required_product_modules": dict(
        action_ids=_CRUD, permissions=[WRITE_PERMISSION, READ_PERMISSION],
        contracts=["app.factory.build.domain_acceptance.compact_specs", KERNEL_ENTRY]),
    "core_business_workflows": dict(
        action_ids=["product.enqueue", "product.process"],
        permissions=[WRITE_PERMISSION, PROCESS_PERMISSION],
        contracts=[KERNEL_ENTRY], outcomes=["queue_item_processed"]),
    "authoritative_calculations": dict(
        action_ids=["product.create", "product.update"], permissions=[WRITE_PERMISSION],
        contracts=[KERNEL_ENTRY, "app.cerebrum_product_kernel.contract.schema_validation.validate"],
        outcomes=["missing_field_rejected"]),
    "domain_rules": dict(
        action_ids=["product.update"], permissions=[WRITE_PERMISSION], contracts=[KERNEL_ENTRY]),
    "high_impact_actions": dict(
        action_ids=["product.update", "product.process"],
        permissions=[WRITE_PERMISSION, PROCESS_PERMISSION],
        contracts=["app.cerebrum_product_kernel.contract.models.ActionSpec"],
        confirmation_required=True),
    "prohibited_autonomous_actions": dict(
        action_ids=["product.refuse"], permissions=[WRITE_PERMISSION], contracts=[KERNEL_ENTRY],
        outcomes=["refused_action_errors", "unauthorized_rejected"]),
    "data_sources": dict(
        action_ids=["product.create"], permissions=[WRITE_PERMISSION],
        contracts=["app.factory.build.converge:app/connectors"]),
    "required_connectors": dict(
        action_ids=["product.create"], permissions=[WRITE_PERMISSION], contracts=["app/connectors"]),
    "required_exports": dict(
        action_ids=["product.read", "product.list"], permissions=[READ_PERMISSION],
        contracts=[KERNEL_ENTRY]),
    "security_regulatory_rules": dict(
        action_ids=["product.read"], permissions=[READ_PERMISSION],
        contracts=["app.cerebrum_product_kernel.isolation",
                   "app.cerebrum_product_kernel.contract.models.RESERVED_CONTEXT_KEYS"],
        outcomes=["unauthorized_rejected"]),
    "demo_data_requirements": dict(
        action_ids=["product.create"], permissions=[WRITE_PERMISSION],
        contracts=["app.cerebrum_product_kernel.contract.models.ActionSpec.evaluation_fixtures"]),
    "domain_acceptance_conditions": dict(
        action_ids=_CRUD + ["product.enqueue", "product.process", "product.refuse"],
        permissions=[WRITE_PERMISSION, READ_PERMISSION, PROCESS_PERMISSION],
        contracts=["app.factory.build.domain_acceptance.perform_all", KERNEL_ENTRY],
        outcomes=list(OUTCOMES)),
}

_HEADER_KEYS = ("platform_name", "domain", "product_type", "target_users", "mission")


def _slug(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def domain_pack_for(blueprint: Any, plan: Any = None) -> Dict[str, Any]:
    """The build's own Domain Pack: blueprint values, contract bindings."""
    from app.factory.build.brief_compiler import synthesize_domain_pack

    values = synthesize_domain_pack(blueprint, plan)
    product_id = str(getattr(blueprint, "product_id", "") or "")
    product_name = str(getattr(blueprint, "product_name", "") or values.get("platform_name") or "")
    domain_id = _slug(getattr(blueprint, "vertical", "") or values.get("domain"))
    fields = {
        # An empty slot stays NAMED ("none claimed"), never invented and
        # never silently blank -- the same wording intake uses.
        name: _field(values.get(name) or ["none claimed"], _binding(**FIELD_BINDINGS[name]))
        for name in DOMAIN_PACK_FIELDS
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "domain_id": domain_id,
        "product_id": product_id,
        "product_name": product_name,
        "contract": "backend/app/factory/delivery_standard.py:DOMAIN_PACK_FIELDS",
        "field_count": len(DOMAIN_PACK_FIELDS),
        "fields_order": list(DOMAIN_PACK_FIELDS),
        "header": {k: values.get(k) for k in _HEADER_KEYS},
        "kernel_entry": KERNEL_ENTRY,
        "fields": fields,
        "lotdesk": "fixture only; not patched",
        "PILOT_READY": False,
    }


def field_names(pack: Mapping[str, Any]) -> List[str]:
    fields = pack.get("fields")
    if not isinstance(fields, Mapping):
        return []
    return list(fields.keys())


def as_delivery_domain_pack(pack: Mapping[str, Any]) -> Dict[str, Any]:
    """Flatten header + field values for delivery_standard.render()."""
    header = pack.get("header") if isinstance(pack.get("header"), Mapping) else {}
    out: Dict[str, Any] = dict(header)
    fields = pack.get("fields") if isinstance(pack.get("fields"), Mapping) else {}
    for name in DOMAIN_PACK_FIELDS:
        item = fields.get(name)
        if isinstance(item, Mapping) and "value" in item:
            out[name] = item["value"]
        else:
            out[name] = item
    return out


def _value_present(value: Any) -> bool:
    return bool(_fmt(value))


def _kernel_bound(kernel: Any) -> bool:
    if not isinstance(kernel, Mapping):
        return False
    actions = kernel.get("action_ids") or []
    contracts = kernel.get("contracts") or []
    entry = str(kernel.get("entry") or "").strip()
    return bool(actions) and bool(contracts) and bool(entry)


def missing_fields(pack: Mapping[str, Any]) -> List[str]:
    found = []
    fields = pack.get("fields") if isinstance(pack.get("fields"), Mapping) else {}
    for name in DOMAIN_PACK_FIELDS:
        item = fields.get(name)
        if not isinstance(item, Mapping):
            found.append(name)
            continue
        if not _value_present(item.get("value")):
            found.append(name)
            continue
        if not _kernel_bound(item.get("kernel")):
            found.append(f"{name}.kernel")
    return found


def is_empty_pack(pack: Any) -> bool:
    if not isinstance(pack, Mapping) or not pack:
        return True
    fields = pack.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        return True
    return all(
        not _value_present(
            (item.get("value") if isinstance(item, Mapping) else item)
        )
        for item in fields.values()
    )


def assert_pack(pack: Mapping[str, Any]) -> None:
    """Fail closed: every DOMAIN_PACK_FIELDS name present, valued, kernel-bound."""
    if is_empty_pack(pack):
        raise DomainPackError("empty Domain Pack")
    if pack.get("schema_version") != SCHEMA_VERSION:
        raise DomainPackError("schema_version must be domain_pack.v1")
    if not str(pack.get("domain_id") or "").strip():
        raise DomainPackError("domain_id must name the build's vertical")
    if int(pack.get("field_count") or 0) != len(DOMAIN_PACK_FIELDS):
        raise DomainPackError(
            f"field_count must be {len(DOMAIN_PACK_FIELDS)}, got {pack.get('field_count')}"
        )
    order = pack.get("fields_order")
    if list(order or []) != list(DOMAIN_PACK_FIELDS):
        raise DomainPackError("fields_order must equal DOMAIN_PACK_FIELDS")
    missing = missing_fields(pack)
    if missing:
        raise DomainPackError(
            "Domain Pack is incomplete: " + ", ".join(missing)
        )
    names = field_names(pack)
    extra = [name for name in names if name not in DOMAIN_PACK_FIELDS]
    if extra:
        raise DomainPackError("unknown Domain Pack fields: " + ", ".join(extra))
    if not callable(execute_action):
        raise DomainPackError("execute_action is not callable")


def render_pack(pack: Mapping[str, Any]) -> str:
    # Do not sort_keys: fields_order / DOMAIN_PACK_FIELDS order is the contract.
    return json.dumps(pack, indent=2, ensure_ascii=True) + "\n"


def emit_domain_pack(
    workspace: Any,
    *,
    blueprint: Any = None,
) -> Dict[str, Any]:
    """WRITER ships the BUILD'S OWN pack into the product tree."""
    pack = domain_pack_for(blueprint)
    assert_pack(pack)
    workspace.write_text(PACK_REL, render_pack(pack))
    return {
        "path": str(PACK_REL),
        "field_count": len(DOMAIN_PACK_FIELDS),
        "domain_id": pack["domain_id"],
    }


def load_emitted_pack(root: Path) -> Dict[str, Any]:
    path = Path(root) / PACK_REL
    if not path.is_file():
        raise DomainPackError(f"Domain Pack missing: {PACK_REL}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainPackError(f"Domain Pack unreadable: {PACK_REL}") from exc
    if not isinstance(data, dict):
        raise DomainPackError("Domain Pack is not an object")
    return data


def _pack_from_zip(zip_path: Path) -> Dict[str, Any]:
    with zipfile.ZipFile(zip_path) as zf:
        names = [
            name.replace("\\", "/")
            for name in zf.namelist()
            if name.replace("\\", "/").endswith("docs/domain_pack.json")
            or name.replace("\\", "/").rsplit("/", 1)[-1] == "domain_pack.json"
        ]
        if not names:
            return {}
        try:
            data = json.loads(zf.read(names[0]).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}


def load_lotdesk_pack(explicit: Optional[Path] = None) -> Dict[str, Any]:
    """Inspect LotDesk as shipped. Never patch the fixture."""
    path = resolve_lotdesk_fixture(explicit)
    if path.is_file() and path.suffix == ".zip":
        return _pack_from_zip(path)
    candidate = path / PACK_REL if path.is_dir() else path
    if candidate.is_file():
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}
    return {}


def reject_lotdesk_pack(explicit: Optional[Path] = None) -> Dict[str, Any]:
    """LotDesk-class empty pack fails S3. A hollow accept is a factory defect."""
    path = resolve_lotdesk_fixture(explicit)
    pack = load_lotdesk_pack(explicit)
    try:
        assert_pack(pack)
    except DomainPackError:
        return {
            "ok": False,
            "gate": "lotdesk_domain_pack",
            "fixture": _posix_under_repo(path),
            "empty": True,
            "reason": "LotDesk-class empty Domain Pack",
            "lotdesk": "fixture only; not patched",
        }
    raise AssertionError("GATE HOLLOW: LotDesk Domain Pack was accepted by S3")


def _missing_field_fails_closed(pack: Mapping[str, Any]) -> bool:
    fields = dict(pack.get("fields") or {})
    drop = DOMAIN_PACK_FIELDS[5]  # authoritative_calculations
    fields.pop(drop, None)
    bad = dict(pack)
    bad["fields"] = fields
    try:
        assert_pack(bad)
    except DomainPackError as exc:
        return drop in str(exc)
    return False


def _golden_blueprints(root: Path) -> List[Any]:
    from app.factory.blueprint import load_blueprint

    out = []
    for path in sorted((root / "blueprints").rglob("*.yaml")):
        try:
            out.append(load_blueprint(path))
        except Exception:  # noqa: BLE001 -- not a product blueprint
            continue
    return out


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _posix_under_repo(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(_repo_root()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def canonical_fingerprint(result: Dict[str, Any]) -> Dict[str, Any]:
    """Stable subset compared across a reread. Timestamps excluded."""
    return {
        "git_sha": result.get("git_sha"),
        "emitter": result.get("emitter"),
        "verdict": result.get("verdict"),
        "ok": result.get("ok"),
        "first_failing_criterion": result.get("first_failing_criterion"),
        "pass_criteria": result.get("pass_criteria"),
        "fields": result.get("fields"),
        "field_count": result.get("field_count"),
        "contract": result.get("contract"),
        "PILOT_READY": result.get("PILOT_READY"),
        "lotdesk_empty_rejected": (result.get("lotdesk_pack") or {}).get("empty"),
    }


def fingerprint_disagreements(
    primary: Dict[str, Any], reread: Dict[str, Any]
) -> List[str]:
    left = canonical_fingerprint(primary)
    right = canonical_fingerprint(reread)
    if left == right:
        return []
    found: List[str] = []
    for key in left:
        if left.get(key) != right.get(key):
            found.append(key)
    return found or ["canonical_fingerprint"]


def evaluate_domain_pack(*, repo: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo) if repo is not None else _repo_root()
    # Every golden blueprint on disk gets its own pack, and every one must
    # pass: the gate holds for builds it has never seen named.
    packs = [domain_pack_for(bp) for bp in _golden_blueprints(root)]
    pack_findings: List[str] = []
    pack_ok = bool(packs)
    if not packs:
        pack_findings.append("no golden blueprint found to build a pack from")
    for candidate in packs:
        try:
            assert_pack(candidate)
        except DomainPackError as exc:
            pack_ok = False
            pack_findings.append(f"{candidate.get('domain_id')}: {exc}")
    pack = packs[0] if packs else {}
    missing_fails = _missing_field_fails_closed(pack) if pack_ok else False
    lotdesk = reject_lotdesk_pack()
    module_path = root / "backend" / "app" / "factory" / "build" / "domain_pack.py"
    from app.factory.build.roles import _coder_route_body

    coder_none = _coder_route_body(None, None, None) is None
    first = None
    if len(DOMAIN_PACK_FIELDS) != 15:
        first = "field_count"
    elif not module_path.is_file():
        first = "domain_pack_module_missing"
    elif not pack_ok:
        first = "pack:" + (pack_findings[0] if pack_findings else "invalid")
    elif not missing_fails:
        first = "missing_field_did_not_fail"
    elif lotdesk.get("ok") is not False or not lotdesk.get("empty"):
        first = "lotdesk_empty_pack_not_rejected"
    elif not coder_none:
        first = "coder_route_body_restored"
    ok = first is None
    git_sha = git_head(root)
    return {
        "stage": STAGE,
        "name": STAGE_NAME,
        "evaluated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "emitter": EMITTER_ID,
        "verdict": "PASS" if ok else "FAIL",
        "ok": ok,
        "first_failing_criterion": first,
        "git_sha": git_sha,
        "contract": {
            "module": "backend/app/factory/delivery_standard.py",
            "symbol": "DOMAIN_PACK_FIELDS",
            "field_count": len(DOMAIN_PACK_FIELDS),
            "sources": list(FIELD_CONTRACT_SOURCES),
            "not_the_contract": (
                "docs/REASONING_KERNEL.md Phase 2 directory spec is unnumbered "
                "and is not DOMAIN_PACK_FIELDS"
            ),
        },
        "fields": list(DOMAIN_PACK_FIELDS),
        "field_count": len(DOMAIN_PACK_FIELDS),
        "pack": {
            "ok": pack_ok,
            "schema_version": SCHEMA_VERSION,
            "domain_ids": sorted({str(p.get("domain_id")) for p in packs}),
            "path": str(PACK_REL),
            "module": "backend/app/factory/build/domain_pack.py",
            "findings": pack_findings,
        },
        "lotdesk_pack": lotdesk,
        "pass_criteria": {
            "numbered_15_field_contract_found": len(DOMAIN_PACK_FIELDS) == 15,
            "pack_exists": pack_ok and module_path.is_file(),
            "all_15_fields_present": pack_ok,
            "missing_field_fails_gate": missing_fails,
            "lotdesk_empty_pack_rejected": bool(lotdesk.get("empty"))
            and lotdesk.get("ok") is False,
            "kernel_bindings_present": pack_ok,
            "coder_route_body_is_None": coder_none,
        },
        "implementation": {
            "module": "backend/app/factory/build/domain_pack.py",
            "emitter": "RoleRunner WRITER writes docs/domain_pack.json",
            "kernel_entry": KERNEL_ENTRY,
        },
        "PILOT_READY": False,
        "not_claimed": [
            "PILOT_READY",
            "S4 U4 _ensure_route_persists_payload removal",
            "S5 U7 FACTORY_SUITE_MARKER_EXPR",
            "generate-from-pack wiring",
        ],
        "lotdesk": "fixture only; not patched",
        "llm_route_authorship": "not restored; _coder_route_body still returns None",
        "not_started": list(probe_set.stages_after(STAGE)),
    }


def reread_matches(evidence: Dict[str, Any], twin: Dict[str, Any]) -> bool:
    if str(evidence.get("verdict") or "").strip().upper() != str(
        twin.get("verdict") or ""
    ).strip().upper():
        return False
    disagreements = twin.get("disagreements")
    if isinstance(disagreements, list) and disagreements:
        return False
    return True


def write_reread_twin(
    evidence_path: Path,
    result: Dict[str, Any],
    *,
    reread: Optional[Dict[str, Any]] = None,
) -> Path:
    from app.factory.build.preflight import reread_twin_path, write_evidence

    second = reread if reread is not None else evaluate_domain_pack()
    disagreements = fingerprint_disagreements(result, second)
    if disagreements:
        result["verdict"] = "FAIL"
        result["ok"] = False
        result["first_failing_criterion"] = "reread_mismatch:" + ",".join(
            disagreements
        )
        write_evidence(evidence_path, result)
    twin = {
        "stage": STAGE,
        "name": "domain_pack",
        "verdict": result.get("verdict"),
        "reread_of": _posix_under_repo(evidence_path),
        "reread_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "independent": True,
        "reader": "cloud-agent-s3-domain-pack",
        "emitter": EMITTER_ID,
        "git_sha": second.get("git_sha"),
        "disagreements": disagreements,
        "checked": [
            "DOMAIN_PACK_FIELDS has 15 names in delivery_standard.py",
            "every golden blueprint's own pack emits all 15 fields with kernel bindings",
            "dropping a field fails assert_pack",
            "LotDesk-class empty pack is rejected; fixture not patched",
            "_coder_route_body still returns None",
        ],
        "fields": list(DOMAIN_PACK_FIELDS),
        "not_claimed": result.get("not_claimed") or [],
        "PILOT_READY": False,
    }
    dest = reread_twin_path(evidence_path)
    dest.write_text(
        json.dumps(twin, indent=2, ensure_ascii=True) + "\n", encoding="utf-8"
    )
    return dest


SAMPLE_PLATFORM = {
    "product_repository": "not created",
    "default_branch": "main",
    "working_branch": "none",
    "pull_request": "none",
    "head_sha": "none",
    "capability_repository": "bopoadz-del/Cerebrum-Blocks",
    "factory_repository": "bopoadz-del/CerebrumDev.ai",
    "reference_repositories": "none",
    "deployment_target": "RENDER",
    "production_url": "not deployed",
    "test_result": "unknown",
    "deployment_state": "new platform",
}


def render_brief_for(blueprint: Any) -> str:
    return render(SAMPLE_PLATFORM, as_delivery_domain_pack(domain_pack_for(blueprint)))


def main(argv: Optional[Iterable[str]] = None) -> int:
    from app.factory.build.preflight import default_stages_dir, write_evidence

    args = list(argv if argv is not None else sys.argv[1:])
    stages = default_stages_dir()
    dest = stages / "S3_domain_pack.json"
    if args:
        stages = Path(args[0])
        dest = stages / "S3_domain_pack.json"
    if len(args) > 1:
        dest = Path(args[1])
    result = evaluate_domain_pack()
    write_evidence(dest, result)
    write_reread_twin(dest, result)
    print(
        json.dumps(
            {
                "wrote": str(dest),
                "verdict": result["verdict"],
                "first_failing_criterion": result.get("first_failing_criterion"),
                "PILOT_READY": False,
            },
            indent=2,
        )
    )
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
