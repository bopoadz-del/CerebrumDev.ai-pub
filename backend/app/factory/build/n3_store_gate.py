"""Ingest cerebrum-builds ``store-gate`` 12/12 into a live Factory session.

After cli-pivot ``HANDOFF_TO_N3`` (receipt+diff clean), N3 is the GitHub
Actions store-gate on the session's ``build/**`` branch — not another
WRITER / Background Agent pass. This module polls the commit status
context ``store-gate`` (artifact ``store_gate.json`` is optional score
detail) and, on 12/12 success, stamps STORE green + pilot_ready so
package ship can unlock.

Fail-closed when the status is missing, red, or score ≠ 12/12.
Never dispatches WRITER / cli_pivot / BA.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import quote
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from app.factory.build.builds_push import (
    BUILDS_TOKEN_ENV,
    BuildsPushError,
    builds_token,
    fetch_commit_sha,
    github_request,
    parse_builds_repo,
)
HANDOFF_TO_N3 = "HANDOFF_TO_N3"
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.store_acceptance import (
    ACCEPTANCE_CHECK_NAMES,
    ACCEPTANCE_REQUIRED,
    NOT_RUN,
    AcceptanceLine,
    AcceptanceReport,
    finalize_owners,
    write_acceptance_report,
)

logger = logging.getLogger("cerebrumdev.factory.n3_store_gate")

STORE_GATE_CONTEXT = "store-gate"
N3_STORE_GATE_GREEN = "N3_STORE_GATE_GREEN"
N3_STORE_GATE_FAILED = "N3_STORE_GATE_FAILED"
N3_STORE_GATE_TIMEOUT = "N3_STORE_GATE_TIMEOUT"
N3_STORE_GATE_MISSING = "N3_STORE_GATE_MISSING"
#: The product passed every check it owns; what failed is the Factory's own
#: (a check whose subject the Factory wrote, or the gate's own workflow
#: never scoring). Terminal for THIS run -- no rework, nothing for the writer
#: -- and routed to the factory lane, never billed to the product.
N3_STORE_GATE_FACTORY_OWED = "N3_STORE_GATE_FACTORY_OWED"
N3_FAIL_HONESTY = frozenset(
    {
        N3_STORE_GATE_FAILED,
        N3_STORE_GATE_TIMEOUT,
        N3_STORE_GATE_MISSING,
        N3_STORE_GATE_FACTORY_OWED,
    }
)

DEFAULT_POLL_S = 15.0
DEFAULT_WALL_S = 1800.0
SCORE_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
#: Which checks failed is read from the gate's typed artifact
#: ``store-gate`` of the run on this sha (``store_gate.json``: every line with name, status,
#: detail) -- never parsed out of the status description, which is display
#: text capped at 140 characters. Without the names the Factory could not say
#: WHICH check -- or whose -- failed (live: eight automotive rounds read
#: "store gate failed" while the only red line was the Factory's own
#: authorship counter).
STORE_GATE_ARTIFACT_NAME = "store-gate"
STORE_GATE_ARTIFACT_FILE = "store_gate.json"


def store_gate_artifact_name(sha: str) -> str:
    """The artifact name the gate run uploads for ``sha``.

    The workflow uploads ``store-gate-${{ github.sha }}`` and GitHub's
    ``?name=`` filter is an exact match, so asking for the bare prefix found
    nothing on every red run: no itemised lines, and the fallback then wore
    every floor line with the same red status (live 82a19a82: the only FAIL
    was audit_clean, the writer was handed no_token_401).
    """
    return f"{STORE_GATE_ARTIFACT_NAME}-{sha}"

#: N3 G-floor names → Factory ``ACCEPTANCE_CHECK_NAMES`` aliases.
N3_NAME_ALIASES = {
    "ci_present_full_suite": "ci_present_and_full_suite",
    "authorship==receipt": "authorship_floor",
}

_N3_THREADS: Dict[str, threading.Thread] = {}
_N3_GUARD = threading.Lock()


@dataclass(frozen=True)
class BuildsTarget:
    owner: str
    repo: str
    sha: str
    branch: str = ""
    session_id: str = ""


@dataclass
class StoreGateSnapshot:
    """One read of the ``store-gate`` commit status (and optional artifact)."""

    state: str = ""
    context: str = STORE_GATE_CONTEXT
    description: str = ""
    passed: Optional[int] = None
    total: Optional[int] = None
    ok: bool = False
    missing: bool = True
    timeout: bool = False
    pending: bool = False
    sha: str = ""
    branch: str = ""
    owner: str = ""
    repo: str = ""
    via: str = "github-commit-status:store-gate"
    detail: str = ""
    lines: List[AcceptanceLine] = field(default_factory=list)
    #: False when the gate reported failure without ever scoring -- the
    #: workflow died before the harness ran. That is the gate's defect.
    harness_ran: bool = True
    #: True when GitHub could not be ASKED (no token, 401/403, 5xx): this
    #: read is no answer about the build. Typed at the read, never re-derived
    #: from ``detail``.
    unreachable: bool = False

    @property
    def score(self) -> str:
        if self.passed is None or self.total is None:
            return ""
        return f"{self.passed}/{self.total}"

    @property
    def is_12_of_12(self) -> bool:
        return (
            not self.missing
            and not self.timeout
            and self.ok
            and self.state == "success"
            and self.passed == ACCEPTANCE_REQUIRED
            and self.total == ACCEPTANCE_REQUIRED
        )


@dataclass
class IngestResult:
    honesty: str
    ok: bool
    pending: bool = False
    detail: str = ""
    snapshot: Optional[StoreGateSnapshot] = None
    already: bool = False

    def to_dict(self) -> Dict[str, Any]:
        snap = self.snapshot
        return {
            "honesty": self.honesty,
            "ok": self.ok,
            "pending": self.pending,
            "detail": self.detail,
            "already": self.already,
            "green": self.ok,
            "score": snap.score if snap else "",
            "sha": snap.sha if snap else "",
            "branch": snap.branch if snap else "",
        }


def n3_poll_s(env: Optional[Mapping[str, str]] = None) -> float:
    blob = env if env is not None else os.environ
    raw = str(blob.get("FACTORY_N3_POLL_S") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            return DEFAULT_POLL_S
    return DEFAULT_POLL_S


def n3_wall_s(env: Optional[Mapping[str, str]] = None) -> float:
    blob = env if env is not None else os.environ
    raw = str(blob.get("FACTORY_N3_WALL_S") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            return DEFAULT_WALL_S
    return DEFAULT_WALL_S


def _ledger(output_dir: Path | str) -> BuildLedger:
    return BuildLedger(Path(output_dir) / "build_ledger.jsonl")


def _event_honesty(event: Any) -> str:
    payload = getattr(event, "payload", None) or {}
    return str(payload.get("honesty") or "").strip()


def _event_outcome(event: Any) -> str:
    payload = getattr(event, "payload", None) or {}
    return str(payload.get("outcome") or "").strip()


def dispatch_store_gate(
    branch: str,
    *,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
    carry: Optional[Callable[..., str]] = None,
) -> str:
    """Trigger the cerebrum-builds store-gate workflow for *branch*; return
    the sha the gate will judge ('' when the builds repo is not armed).

    The handoff pushes the workspace with a GitHub App token
    (x-access-token), and pushes made with App tokens do NOT trigger
    workflow runs on GitHub. Without an explicit dispatch the store-gate
    never runs and the build waits in HANDOFF_TO_N3 forever (live retail
    build sess_c8b01eb6de53495c).

    The dispatch runs the gate from the branch's OWN tree, so ``main``'s whole
    gate is carried onto the branch first (branch_attach.carry_gate_onto_branch;
    a no-op when the branch already carries it). A branch whose gate predates a
    fix would otherwise be judged by the old gate (live 2026-10-10, run
    38040326004: a false 22/22). A carry that cannot be made raises -- nothing
    is dispatched on a gate that is not main's.
    """
    blob = env if env is not None else os.environ
    token = builds_token(blob)
    if not token:
        return ""
    owner, _name, _url = parse_builds_repo(blob)
    from app.factory.build.builds_push import github_request

    if carry is None:
        from app.factory.build.branch_attach import carry_gate_onto_branch as carry
    gated = str(carry(branch, blob) or "").strip()
    github_request(
        "POST",
        f"/repos/{owner}/{_name}/actions/workflows/store-gate.yml/dispatches",
        token=token,
        opener=opener,
        body={"ref": branch},
    )
    return gated


def handoff_awaiting_n3(output_dir: Path | str) -> bool:
    """True when cli-pivot handed off and N3 has not yet been ingested."""
    path = Path(output_dir) / "build_ledger.jsonl"
    if not path.is_file():
        return False
    ledger = BuildLedger(path)
    try:
        events = ledger.events()
    except Exception:  # noqa: BLE001
        return False
    # Judge from the LAST handoff onward, not from the start of the ledger.
    #
    # Walking from the beginning meant ANY historical N3_STORE_GATE_FAILED
    # short-circuited to False forever, so a build could never be re-opened
    # after one gate failure -- including a failure the gate itself caused.
    # Live case: a hospitality build sat at a genuine 12/12 on its branch and
    # could not be ingested, because the poller had recorded a FAILED from a
    # bogus 0/12 produced by a harness bug that was fixed minutes later.
    # reseed_handoff_ledger appends a fresh HANDOFF precisely to re-open a
    # build; the old verdict must not outrank it.
    last_handoff = -1
    for i, event in enumerate(events):
        if _event_honesty(event) == HANDOFF_TO_N3 or _event_outcome(event) == HANDOFF_TO_N3:
            last_handoff = i
    if last_handoff < 0:
        return False
    # Only events AFTER the newest handoff can settle it.
    for event in events[last_handoff + 1:]:
        honesty = _event_honesty(event)
        outcome = _event_outcome(event)
        if honesty == N3_STORE_GATE_GREEN:
            return False          # already ingested green
        if honesty in N3_FAIL_HONESTY:
            return False          # this handoff was already judged and failed
        if honesty == HANDOFF_TO_N3 or outcome == HANDOFF_TO_N3:
            continue
        if event.kind is EventKind.RUN_SUCCEEDED:
            return False
    return True


def n3_ingest_live(output_dir: Path | str) -> bool:
    key = str(Path(output_dir).resolve())
    thread = _N3_THREADS.get(key)
    return thread is not None and thread.is_alive()


def builds_fields_from_ledger(output_dir: Path | str) -> Dict[str, Any]:
    """Latest builds SHA/branch/authored ids recorded on HANDOFF notes."""
    path = Path(output_dir) / "build_ledger.jsonl"
    found: Dict[str, Any] = {}
    if not path.is_file():
        return found
    try:
        events = BuildLedger(path).events()
    except Exception:  # noqa: BLE001
        return found
    keys = (
        "builds_sha",
        "builds_branch",
        "builds_owner",
        "builds_repo",
        "cli_authored_ids",
    )
    for event in events:
        payload = getattr(event, "payload", None) or {}
        blobs: List[Mapping[str, Any]] = [payload]
        nested = payload.get("cli_pivot")
        if isinstance(nested, Mapping):
            blobs.append(nested)
        for blob in blobs:
            # A record that names the gate's branch names the whole target:
            # its sha, or none (= that branch's head). An older sha never
            # outlives it -- live 2026-10-08, a rework round's handoff named
            # only the branch, round 1's failure kept builds_sha, and round 2
            # re-read round 1's failed status (SAME_FAILURE_TWICE on a fix
            # whose own gate run passed).
            if blob.get("builds_branch"):
                found.pop("builds_sha", None)
            for key in keys:
                value = blob.get(key)
                if value:
                    found[key] = value
    return found


def resolve_builds_target(
    output_dir: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
    session_id: Optional[str] = None,
    opener: Callable[..., Any] = urlopen,
) -> BuildsTarget:
    blob = env if env is not None else os.environ
    token = builds_token(blob)
    if not token:
        raise BuildsPushError(
            f"{BUILDS_TOKEN_ENV} missing — fail-closed; cannot poll store-gate",
            unreachable=True,
        )
    owner, repo, _url = parse_builds_repo(blob)
    recorded = builds_fields_from_ledger(output_dir)
    sha = str(recorded.get("builds_sha") or "").strip()
    branch = str(recorded.get("builds_branch") or "").strip()
    owner = str(recorded.get("builds_owner") or owner).strip()
    repo = str(recorded.get("builds_repo") or repo).strip()
    sid = str(session_id or "").strip()
    if not sid:
        from app.factory.build.orphan_recovery import session_id_from_output

        sid = session_id_from_output(output_dir) or ""
    if sha:
        return BuildsTarget(
            owner=owner, repo=repo, sha=sha, branch=branch, session_id=sid
        )
    if branch:
        resolved = fetch_commit_sha(
            owner, repo, branch, token=token, opener=opener
        )
        return BuildsTarget(
            owner=owner, repo=repo, sha=resolved, branch=branch, session_id=sid
        )
    from app.factory.build.builds_push import build_refs_of_record
    from app.factory.build.platform_identity import branch_of_record, recorded_platform_id

    pid = recorded_platform_id(output_dir)
    if not sid and not pid:
        raise BuildsPushError(
            "N3 store-gate: no builds SHA/branch on the ledger, no platform and no session id"
        )
    refs = build_refs_of_record(
        owner, repo, platform_id=pid, session_id=sid, token=token, opener=opener
    )
    if not refs:
        wanted = branch_of_record(pid) if pid else f"build/{sid}-*"
        raise BuildsPushError(
            f"N3 store-gate: no {wanted} branch on {owner}/{repo}"
        )
    branch, sha = refs[-1]
    return BuildsTarget(
        owner=owner, repo=repo, sha=sha, branch=branch, session_id=sid
    )


def _parse_score(text: str) -> Tuple[Optional[int], Optional[int]]:
    match = SCORE_RE.search(text or "")
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


#: How much of a gate's raw evidence is kept per line, and how much of it a
#: writer work item carries (the tail -- where a build's error is).
EVIDENCE_CAP = 6000
EVIDENCE_IN_ITEM = 1500


def _factory_check_name(name: str) -> str:
    raw = str(name or "").strip()
    return N3_NAME_ALIASES.get(raw, raw)


class _DropAuthOnRedirect(HTTPRedirectHandler):
    """GitHub answers an artifact download with a redirect to blob storage,
    which rejects the API token: carry no Authorization across the hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            new.remove_header("Authorization")
        return new


def _download(url: str, token: str, opener: Callable[..., Any]) -> bytes:
    request = Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
    )
    if opener is urlopen:
        with build_opener(_DropAuthOnRedirect).open(request, timeout=30) as resp:
            return resp.read()
    with opener(request, timeout=30) as resp:
        return resp.read()


def _artifact_lines(
    target: "BuildsTarget", token: str, opener: Callable[..., Any]
) -> List[AcceptanceLine]:
    """Every floor line from the gate run's typed ``store_gate.json``
    artifact; [] when the run published none (or it has expired)."""
    import io
    import zipfile

    path = (
        f"/repos/{target.owner}/{target.repo}/actions/artifacts"
        f"?name={quote(store_gate_artifact_name(target.sha), safe='')}&per_page=100"
    )
    status, body = github_request("GET", path, token=token, opener=opener)
    if status >= 400 or not isinstance(body, Mapping):
        return []
    for artifact in body.get("artifacts") or []:
        if not isinstance(artifact, Mapping) or artifact.get("expired"):
            continue
        run = artifact.get("workflow_run")
        if not isinstance(run, Mapping) or run.get("head_sha") != target.sha:
            continue
        url = str(artifact.get("archive_download_url") or "")
        if not url:
            continue
        try:
            blob = _download(url, token, opener)
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                payload = json.loads(archive.read(STORE_GATE_ARTIFACT_FILE).decode("utf-8"))
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            continue
        if isinstance(payload, Mapping):
            return list(report_from_store_gate_payload(payload).lines)
    return []


def _itemised_lines(failing: Sequence[str]) -> List[AcceptanceLine]:
    """One line per floor check from an itemised status: the named ones FAIL,
    every other is satisfied (PASS or an advisory SKIP -- the harness demoted
    those before it scored, so they are not in the FAIL list)."""
    return [
        AcceptanceLine(
            name=name,
            status="FAIL" if name in failing else "PASS",
            detail=(
                "FAIL per the store-gate status"
                if name in failing
                else "satisfied (itemised by the store-gate status)"
            ),
        )
        for name in ACCEPTANCE_CHECK_NAMES
    ]


def report_from_store_gate_payload(raw: Mapping[str, Any]) -> AcceptanceReport:
    """Map a GHA ``store_gate.json`` (N3 names) onto Factory aliases."""
    lines: List[AcceptanceLine] = []
    seen: set[str] = set()
    #: FAIL lines the floor has no entry for. Never relabelled as another
    #: check: the Factory owes them (its floor and its gate disagree).
    unknown_failed: List[str] = []
    for item in raw.get("lines") or []:
        if not isinstance(item, Mapping):
            continue
        name = _factory_check_name(str(item.get("name") or ""))
        if name not in ACCEPTANCE_CHECK_NAMES:
            if name and str(item.get("status") or "").upper() == "FAIL":
                unknown_failed.append(name)
            continue
        if name in seen:
            continue
        seen.add(name)
        lines.append(
            AcceptanceLine(
                name=name,
                status=str(item.get("status") or "FAIL").upper(),
                detail=str(item.get("detail") or ""),
                evidence=str(item.get("evidence") or "")[:EVIDENCE_CAP],
                evidence_rows=[
                    dict(r) for r in item.get("evidence_rows") or [] if isinstance(r, Mapping)
                ],
            )
        )
    by_name = {line.name: line for line in lines}
    ordered = [
        by_name.get(
            name,
            # The gate reported nothing for this check: not measured, so not
            # anyone's failure (a FAIL here was billed to the product).
            AcceptanceLine(
                name=name, status=NOT_RUN, detail="omitted by the store-gate artifact"
            ),
        )
        for name in ACCEPTANCE_CHECK_NAMES
    ]
    passed = int(raw.get("passed") or sum(1 for line in ordered if line.satisfied))
    total = int(raw.get("total") or ACCEPTANCE_REQUIRED)
    if total < ACCEPTANCE_REQUIRED:
        total = ACCEPTANCE_REQUIRED
    ok = bool(raw.get("ok")) and passed >= total and all(
        line.satisfied for line in ordered
    )
    report = finalize_owners(
        AcceptanceReport(
            passed=passed,
            total=total,
            ok=ok and not unknown_failed,
            lines=ordered,
            missing=False,
            via=str(raw.get("via") or "store_gate.json"),
            detail=str(raw.get("detail") or raw.get("score") or f"{passed}/{total}"),
        )
    )
    report.factory_owed = list(report.factory_owed) + [
        name for name in unknown_failed if name not in report.factory_owed
    ]
    return report


def report_from_snapshot(snap: StoreGateSnapshot) -> AcceptanceReport:
    if snap.lines:
        return finalize_owners(
            AcceptanceReport(
                passed=int(snap.passed or 0),
                total=int(snap.total or ACCEPTANCE_REQUIRED),
                ok=snap.is_12_of_12,
                lines=list(snap.lines),
                missing=False,
                via=snap.via,
                detail=snap.detail or snap.score,
                harness_ran=snap.harness_ran,
            )
        )
    # Not itemised: the status carried a score and nothing else, so no line
    # can be anyone's FAIL -- every one is NOT_RUN. Wearing each line with
    # the red status made every product-owned line "failed", and the first
    # became the writer's rework item (live 82a19a82: audit_clean failed, the
    # writer was told to fix no_token_401 and stopped SAME_FAILURE_TWICE).
    status = "PASS" if snap.is_12_of_12 else NOT_RUN
    detail = snap.description or snap.detail or snap.score or "store-gate"
    lines = [
        AcceptanceLine(name=name, status=status, detail=detail)
        for name in ACCEPTANCE_CHECK_NAMES
    ]
    passed = snap.passed if snap.passed is not None else 0
    total = snap.total if snap.total is not None else ACCEPTANCE_REQUIRED
    return finalize_owners(
        AcceptanceReport(
            passed=passed,
            total=total,
            ok=snap.is_12_of_12,
            lines=lines,
            missing=snap.missing,
            via=snap.via,
            detail=snap.detail or snap.score or "store-gate",
            harness_ran=snap.harness_ran,
        )
    )


def fetch_store_gate_status(
    target: BuildsTarget,
    *,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
) -> StoreGateSnapshot:
    """Read the latest ``store-gate`` commit status. Fail-closed if absent."""
    blob = env if env is not None else os.environ
    token = builds_token(blob)
    if not token:
        return StoreGateSnapshot(
            missing=True,
            unreachable=True,
            detail=f"{BUILDS_TOKEN_ENV} missing",
            sha=target.sha,
            branch=target.branch,
            owner=target.owner,
            repo=target.repo,
        )
    path = f"/repos/{target.owner}/{target.repo}/commits/{quote(target.sha, safe='')}/statuses"
    status, body = github_request("GET", path, token=token, opener=opener)
    if status >= 400:
        return StoreGateSnapshot(
            missing=True,
            unreachable=status in (401, 403) or status >= 500,
            detail=f"GitHub statuses HTTP {status}",
            sha=target.sha,
            branch=target.branch,
            owner=target.owner,
            repo=target.repo,
        )
    rows = body if isinstance(body, list) else []
    match: Optional[Mapping[str, Any]] = None
    for item in rows:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("context") or "").strip() == STORE_GATE_CONTEXT:
            match = item
            break
    if match is None:
        return StoreGateSnapshot(
            missing=True,
            pending=True,
            detail="store-gate commit status is missing",
            sha=target.sha,
            branch=target.branch,
            owner=target.owner,
            repo=target.repo,
        )
    return snapshot_from_status(
        target,
        str(match.get("state") or ""),
        str(match.get("description") or ""),
        lambda: _artifact_lines(target, token, opener),
    )


def certification_withdrawn(
    output_dir: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
) -> Optional[str]:
    """Why this build's Store-gate certification no longer stands, or None.

    Read again at export time from the same ``store-gate`` commit status the
    build was certified by. cerebrum-builds withdraws it when a certification
    is revoked (revoked_certifications.json -- data, not code), so an export
    whose verdict was later proven false is never presented as certified.
    No builds token (not armed) or no answer from GitHub is no verdict: None.
    """
    blob = env if env is not None else os.environ
    if not builds_token(blob):
        return None
    try:
        target = resolve_builds_target(output_dir, env=blob, opener=opener)
        snap = fetch_store_gate_status(target, env=blob, opener=opener)
    except Exception:  # noqa: BLE001 -- unreadable is no verdict; logged by the caller's path
        return None
    if snap.unreachable or snap.missing or snap.pending or snap.state in ("", "success", "pending"):
        return None
    return (
        f"store-gate certification of {target.branch or target.sha} no longer stands "
        f"({snap.state}): {snap.description or snap.detail}"
    )


#: The Store gate withdrew a certification it had given (a later run on the
#: same branch, or cerebrum-builds revoked_certifications.json).
N3_CERTIFICATION_WITHDRAWN = "N3_CERTIFICATION_WITHDRAWN"


def record_certification_withdrawn(output_dir: Path | str, reason: str) -> bool:
    """Put a withdrawn certification on the build's ledger as what it is: the
    Store gate's verdict, now failed.

    A succeeded run whose certification the gate later withdrew used to stay
    "succeeded" -- the export refused it, while Continue answered "already
    complete", so the platform could never be re-gated or reworked (live: the
    cycle-2 smoke B build, revoked by builds#41). Recorded, the Store phase is
    no longer passed and the run is a failed platform: Continue resumes it at
    the Store gate with a fresh rework budget, and a product-owned failure
    there sends the writer back by the runner's one rule.

    Only a SUCCEEDED run is touched, once. Returns whether it recorded."""
    from app.factory.build.authority import BuildRole

    root = Path(output_dir)
    ledger = _ledger(root)
    if not ledger.exists() or not ledger.succeeded():
        return False
    detail = f"{N3_CERTIFICATION_WITHDRAWN}: {reason}"
    ledger.append(
        EventKind.GATE_FAILED,
        role=BuildRole.STORE_MANAGER,
        detail=detail,
        payload={"honesty": N3_CERTIFICATION_WITHDRAWN, "check": STORE_GATE_CONTEXT},
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.STORE_MANAGER,
        detail=detail,
        payload={
            "outcome": "FAILED_GATE",
            "honesty": N3_CERTIFICATION_WITHDRAWN,
            "pilot_ready": False,
            "green": False,
        },
    )
    return True


def snapshot_from_status(
    target: BuildsTarget,
    state: str,
    description: str,
    artifact_lines: Callable[[], List[AcceptanceLine]],
) -> StoreGateSnapshot:
    """One ``store-gate`` status (state + description) as a snapshot.

    ``artifact_lines`` is asked only for a red, scored run -- the gate's typed
    ``store_gate.json``. The live read and the pre-merge gate replay
    (gate_replay.py) map a gate result through THIS rule, so a replay judges a
    result exactly as a live build would."""
    state = str(state or "").strip().lower()
    description = str(description or "")
    passed, total = _parse_score(description)
    # A red status with no score at all: the workflow failed before the
    # harness scored anything (live round 9: the gate's own YAML broke
    # ``docker create``). No product check ran, so none can have failed.
    harness_ran = not (passed is None and state in {"failure", "error"})
    lines: List[AcceptanceLine] = []
    if passed is not None and passed == total:
        lines = _itemised_lines([])
    elif passed is not None and state in {"failure", "error"}:
        lines = artifact_lines()
    ok = (
        state == "success"
        and passed == ACCEPTANCE_REQUIRED
        and total == ACCEPTANCE_REQUIRED
    )
    pending = state in {"pending", "expected"} or (
        not ok and state not in {"failure", "error", "success"}
    )
    if state == "success" and not ok:
        pending = False
    return StoreGateSnapshot(
        state=state,
        description=description,
        passed=passed,
        total=total,
        ok=ok,
        missing=False,
        pending=pending and not ok,
        sha=target.sha,
        branch=target.branch,
        owner=target.owner,
        repo=target.repo,
        detail=description or f"store-gate {state}",
        lines=lines,
        harness_ran=harness_ran,
    )


def wait_for_store_gate(
    target: BuildsTarget,
    *,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: Optional[float] = None,
    wall_s: Optional[float] = None,
) -> StoreGateSnapshot:
    """Bounded poll. Returns on 12/12, red, or timeout. Never launches a BA."""
    blob = env if env is not None else os.environ
    interval = n3_poll_s(blob) if poll_s is None else max(0.0, float(poll_s))
    wall = n3_wall_s(blob) if wall_s is None else max(0.0, float(wall_s))
    deadline = clock() + wall
    last = StoreGateSnapshot(
        missing=True,
        pending=True,
        sha=target.sha,
        branch=target.branch,
        owner=target.owner,
        repo=target.repo,
        detail="store-gate poll not started",
    )
    while True:
        last = fetch_store_gate_status(target, env=blob, opener=opener)
        if last.is_12_of_12:
            return last
        if not last.missing and last.state in {"failure", "error"}:
            return last
        if last.state == "success" and not last.is_12_of_12:
            return last
        remaining = deadline - clock()
        if remaining <= 0:
            last.timeout = True
            last.pending = False
            last.detail = (
                last.detail + "; N3 store-gate poll timed out"
                if last.detail
                else "N3 store-gate poll timed out"
            )
            return last
        sleep(min(interval, remaining) if interval > 0 else 0.0)


def _cli_authored_ids(output_dir: Path | str) -> List[str]:
    recorded = builds_fields_from_ledger(output_dir)
    ids = recorded.get("cli_authored_ids") or []
    if isinstance(ids, str):
        return [ids] if ids.strip() else []
    return [str(item).strip() for item in ids if str(item).strip()]


def _persist_cli_authorship(output_dir: Path, ids: Sequence[str]) -> None:
    """Keep package / Store-green floor honest from the HANDOFF receipt."""
    if not ids:
        return
    dest = Path(output_dir) / "docs" / "build_provenance.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload: Dict[str, Any] = {}
    if dest.is_file():
        try:
            raw = json.loads(dest.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                payload = raw
        except (OSError, ValueError):
            payload = {}
    dispatch = payload.get("brief_dispatch")
    if not isinstance(dispatch, dict):
        dispatch = {}
    dispatch["cli_authored_ids"] = list(ids)
    if dispatch.get("n_required") in (None, "", 0) and ids:
        dispatch["n_required"] = len(ids)
    payload["brief_dispatch"] = dispatch
    dest.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _three_gate_pilot_detail() -> str:
    from app.factory.build.product_gate import GATE_SCOPES

    return "; ".join(
        (
            "CODE PASS — %s" % GATE_SCOPES["CODE"],
            "PRODUCT PASS — %s" % GATE_SCOPES["PRODUCT"],
            "STORE PASS — %s" % GATE_SCOPES["STORE"],
        )
    )


#: Why an audit line became advisory: every finding on it is in Factory
#: substrate, so it is the Factory's to fix and does not fail the build.
REASON_AUDIT_FACTORY_ORIGIN = (
    "audit findings only in Factory substrate (by the build's factory receipt)"
)
REASON_AUDIT_NO_ROWS = (
    "the gate attached no typed findings to this audit line: origin unknown, "
    "a Factory fault"
)


def split_audit_by_origin(root: Path | str, report: AcceptanceReport) -> List[Dict[str, Any]]:
    """Split each failed audit line's findings by who wrote them.

    Provenance comes from the build's factory receipt (factory_receipt.py),
    never from a filename. Per failed audit line (the floor's ``stage: audit``):

    * findings in writer-authored files or writer-added dependencies stay on
      the line -- it stays a PRODUCT failure and those rows (file:line) are
      what the writer is handed;
    * findings in Factory substrate are the Factory's and advisory for this
      build: when they are ALL the line has, the line is satisfied (owner
      FACTORY, reason recorded); when mixed, only the writer's rows remain;
    * a line the gate gave no typed rows cannot be attributed: the Factory's
      (unknown origin is never silently the writer's).

    Mutates and re-scores ``report``; returns the ``gate_advisory`` entries
    ([{check, reason, findings}]). Re-scored whenever a line was split, not
    only when something became advisory: a line moved to the writer must
    leave ``factory_owed`` too, or a product finding is billed to the Factory
    (live 2026-10-07: "product passed 18/18 ... the Factory failed
    audit_clean", no rework, export dead).
    """
    from app.factory.build.acceptance_floor import FACTORY, PRODUCT, audit_check_ids
    from app.factory.build.factory_receipt import load_receipt, row_text, split_rows

    audit = set(audit_check_ids())
    receipt = load_receipt(root)
    advisory: List[Dict[str, Any]] = []
    split = False
    for line in report.lines:
        if line.name not in audit or not line.failed:
            continue
        split = True
        if not line.evidence_rows:
            line.owner = FACTORY
            line.status = "SKIP"
            line.detail = f"advisory: {REASON_AUDIT_NO_ROWS}"
            advisory.append({"check": line.name, "reason": REASON_AUDIT_NO_ROWS, "findings": []})
            continue
        writer_rows, factory_rows = split_rows(receipt, line.evidence_rows)
        if factory_rows:
            advisory.append(
                {
                    "check": line.name,
                    "reason": "; ".join(sorted({reason for _, reason in factory_rows})),
                    "findings": [row_text(row) for row, _ in factory_rows],
                }
            )
        if writer_rows:
            line.owner = PRODUCT
            line.evidence_rows = [dict(r) for r in writer_rows]
            line.evidence = "\n".join(row_text(r) for r in writer_rows)[:EVIDENCE_CAP]
        else:
            line.owner = FACTORY
            line.status = "SKIP"
            line.detail = f"advisory: {REASON_AUDIT_FACTORY_ORIGIN}"
    if split:
        finalize_owners(report)
        report.passed = sum(1 for line in report.lines if line.satisfied)
        report.ok = bool(report.harness_ran) and all(line.satisfied for line in report.lines)
    return advisory


#: Typed row the Store gate attaches to an image-stage line when the image
#: built but the container died at boot: the innermost frame of the crash
#: inside the product tree (cerebrum-builds .github/store_gate/boot_crash.py).
ROW_BOOT_CRASH = "boot_crash"


def split_image_by_origin(root: Path | str, report: AcceptanceReport) -> List[str]:
    """Attribute an image-stage FAIL that is a boot crash to whoever wrote the
    crashing file, by the build's factory receipt -- never by its name.

    A crash in a file the Factory stamped (app/observe.py, app/health.py, the
    harness) is the Factory's: listed in factory_owed, never the writer's
    rework, never the product's failure. A crash in a writer-authored file
    stays the product's, with that file:line handed to the writer. A line
    without boot-crash rows (an image that did not build) keeps the owner
    owner_of gave it. Returns the checks moved to the Factory.
    """
    from app.factory.build.acceptance_floor import FACTORY, PRODUCT, image_check_ids
    from app.factory.build.factory_receipt import WRITER, load_receipt, row_origin, row_text

    image = set(image_check_ids())
    receipt = load_receipt(root)
    moved: List[str] = []
    for line in report.lines:
        if line.name not in image or not line.failed:
            continue
        rows = [r for r in line.evidence_rows if r.get("kind") == ROW_BOOT_CRASH]
        if not rows:
            continue
        writer_rows = [r for r in rows if row_origin(receipt, r).owner == WRITER]
        if writer_rows:
            line.owner = PRODUCT
            line.evidence = "\n".join(row_text(r) for r in writer_rows)[:EVIDENCE_CAP]
        else:
            line.owner = FACTORY
            moved.append(line.name)
    if moved:
        finalize_owners(report)
        report.passed = sum(1 for line in report.lines if line.satisfied)
    return moved


def record_audit_advisory(root: Path | str, advisory: Sequence[Mapping[str, Any]]) -> None:
    """One ledger NOTE carrying ``gate_advisory`` -- the record the build
    status and the export manifest already read (brief_gates.advisory_checks)."""
    if not advisory:
        return
    from app.factory.build.authority import BuildRole

    ledger = _ledger(root)
    if not ledger.exists():
        ledger.start_run(product_id=Path(root).name, inputs_hash="n3_store_gate")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.STORE_MANAGER,
        detail="GATE ADVISORY: "
        + ", ".join(f"{a['check']} ({a['reason']})" for a in advisory),
        payload={
            "gate_advisory": [dict(a) for a in advisory],
            "gate": "store_acceptance",
            "factory_owed": [a["check"] for a in advisory],
        },
    )


def apply_store_gate_success(
    output_dir: Path | str,
    snap: StoreGateSnapshot,
    *,
    cli_authored_ids: Optional[Sequence[str]] = None,
    report: Optional[AcceptanceReport] = None,
) -> None:
    """Stamp acceptance k/k + STORE-green SUCCESS. Does not claim SCAFFOLD.

    ``report`` is the origin-split report (split_audit_by_origin): a gate that
    went red ONLY on audit findings in Factory substrate passes here with the
    line recorded advisory -- never on any other red line."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    split = report is not None
    if report is None:
        report = report_from_snapshot(snap)
    green = snap.is_12_of_12 and report.ok
    if split and not green:
        green = (
            not snap.missing and not snap.timeout and bool(report.harness_ran) and report.ok
        )
    if not green:
        raise BuildsPushError(
            "refuse N3 SUCCESS: store-gate is not 12/12 ok=true "
            f"(score={snap.score!r} state={snap.state!r})"
        )
    write_acceptance_report(root, report)
    ids = list(cli_authored_ids or _cli_authored_ids(root))
    _persist_cli_authorship(root, ids)
    try:
        from app.factory.build.package import IDENTITY_REL, identity_document

        doc = identity_document(
            root,
            extra={
                "engine": "n3_store_gate",
                "via": snap.via,
                "builds_sha": snap.sha,
            },
        )
        ident = root / IDENTITY_REL
        ident.parent.mkdir(parents=True, exist_ok=True)
        ident.write_text(
            json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except Exception:  # noqa: BLE001 — identity is not the green bit
        logger.exception("n3 ingest: package identity stamp failed at %s", root)
    ledger = _ledger(root)
    if not ledger.exists():
        ledger.start_run(product_id=root.name, inputs_hash="n3_store_gate")
    from app.factory.build.authority import BuildRole

    ledger.append(
        EventKind.GATE_PASSED,
        role=BuildRole.STORE_MANAGER,
        detail=f"STORE (n3 store-gate): {snap.score} on {snap.sha[:12]}",
        payload={
            "gate": "store_acceptance",
            "via": snap.via,
            "score": snap.score,
            "builds_sha": snap.sha,
            "builds_branch": snap.branch,
            "honesty": N3_STORE_GATE_GREEN,
        },
    )
    ledger.append(
        EventKind.RUN_SUCCEEDED,
        role=BuildRole.STORE_MANAGER,
        detail=_three_gate_pilot_detail(),
        payload={
            "outcome": "SUCCESS",
            "cycle": "pilot",
            "pilot_ready": True,
            "honesty": N3_STORE_GATE_GREEN,
            "green": True,
            "via": snap.via,
            "score": snap.score,
            "builds_sha": snap.sha,
            "builds_branch": snap.branch,
            "cli_authored_ids": ids,
        },
    )


def _product_failure_detail(snap: StoreGateSnapshot, report: AcceptanceReport) -> str:
    """What to tell the owner when the PRODUCT failed its own checks."""
    from app.factory.build.acceptance_floor import PRODUCT

    failed = [
        line.name
        for line in report.lines
        if line.owner == PRODUCT and line.failed
    ]
    if not snap.lines:
        base = snap.detail or f"store-gate {snap.state or 'missing'} {snap.score}"
        return (
            base
            + " (the store-gate status did not itemise which check failed; "
            "the workflow on this branch predates the itemised status)"
        )
    text = (
        f"store-gate {snap.score}: your product failed {len(failed)} of the "
        f"{report.product_total} checks it owns: {', '.join(failed)}"
    )
    if report.factory_owed:
        text += (
            "; the factory separately owes its own: "
            + ", ".join(report.factory_owed)
            + " (not yours)"
        )
    return text


def apply_store_gate_failure(
    output_dir: Path | str,
    snap: StoreGateSnapshot,
    *,
    honesty: str,
    report: Optional[AcceptanceReport] = None,
) -> None:
    from app.factory.build.acceptance_floor import PRODUCT

    root = Path(output_dir)
    ledger = _ledger(root)
    if not ledger.exists():
        ledger.start_run(product_id=root.name, inputs_hash="n3_store_gate")
    from app.factory.build.authority import BuildRole

    if report is None:
        detail = snap.detail or f"store-gate {snap.state or 'missing'} {snap.score}"
        extra: Dict[str, Any] = {}
    else:
        detail = _product_failure_detail(snap, report)
        extra = {
            "failure_owner": PRODUCT,
            "product_score": report.product_score,
            "factory_owed": list(report.factory_owed),
            "product_failed": [
                line.name
                for line in report.lines
                if line.owner == PRODUCT and line.failed
            ],
            # What each failed line said, so a re-opened WRITER is told the
            # gate's own evidence, not just a name.
            "product_failed_detail": {
                line.name: str(getattr(line, "detail", "") or "")
                for line in report.lines
                if line.owner == PRODUCT and line.failed
            },
            # The raw evidence the gate attached (a failed image build's log
            # tail), kept apart from ``detail`` so ownership never reads it.
            "product_failed_evidence": {
                line.name: str(getattr(line, "evidence", "") or "")
                for line in report.lines
                if line.owner == PRODUCT and line.failed and getattr(line, "evidence", "")
            },
        }
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.STORE_MANAGER,
        detail=detail,
        payload={
            "outcome": "FAILED_GATE",
            "cycle": "code",
            "pilot_ready": False,
            "honesty": honesty,
            "green": False,
            "next": "n3_gate",
            "score": snap.score,
            "builds_sha": snap.sha,
            "builds_branch": snap.branch,
            "state": snap.state,
            **extra,
        },
    )


def _what_to_build(check_id: str) -> str:
    """The floor's own line for a check: what the writer must build."""
    from app.factory.build.acceptance_floor import checks

    for row in checks():
        if str(row.get("id") or "") == check_id:
            line = str(row.get("brief_render") or row.get("requirement_text") or "")
            prefix = f"- {check_id}:"
            return line[len(prefix):].strip() if line.startswith(prefix) else line.strip()
    return ""


def store_rework_item(check_id: str, detail: str, evidence: str = "") -> str:
    """One typed work item: the check, what the gate saw, what to build, the
    gate's raw evidence when it attached any, and the command that re-checks
    it (the same harness the gate runs)."""
    from app.factory.build.store_acceptance import ACCEPTANCE_SELF_CHECK_COMMAND

    build = _what_to_build(check_id)
    text = f"[{check_id}] Store gate FAIL"
    if detail.strip():
        text += f": {detail.strip()}"
    if build:
        text += f". Build: {build}"
    if evidence.strip():
        text += f". Gate evidence (tail): {evidence.strip()[-EVIDENCE_IN_ITEM:]}"
    return text + f" (re-check: `{ACCEPTANCE_SELF_CHECK_COMMAND}`)"


def store_gate_verdict(payload: Mapping[str, Any]) -> Optional[Any]:
    """The N3 Store gate's PRODUCT-owned failure as a phase verdict, for the
    runner's one rule (RoleRunner.reopen_after_store_gate). None when the
    terminal is not a product-owned Store-gate failure (a Factory-owned miss
    never reaches the writer)."""
    from app.factory.build.acceptance_floor import PRODUCT
    from app.factory.build.gates import GateResult
    from app.factory.build.store_acceptance import GATE_NAME

    if (payload or {}).get("failure_owner") != PRODUCT:
        return None
    failed = [str(c) for c in payload.get("product_failed") or [] if str(c).strip()]
    if not failed:
        return None
    details = payload.get("product_failed_detail") or {}
    evidence = payload.get("product_failed_evidence") or {}
    return GateResult(
        ok=False,
        gate=GATE_NAME,
        reason="store_gate_failed",
        detail=f"store-gate {payload.get('score') or ''}: product failed "
        + ", ".join(failed),
        findings=[
            store_rework_item(c, str(details.get(c) or ""), str(evidence.get(c) or ""))
            for c in failed
        ],
        payload={"check": GATE_NAME, "finding_checks": failed},
    )


def apply_store_gate_factory_owed(
    output_dir: Path | str,
    snap: StoreGateSnapshot,
    report: AcceptanceReport,
) -> str:
    """The product passed every check it owns; the Factory failed its own.

    Terminal for this run and routed to the factory lane: no rework (there is
    nothing for the writer to do), no re-run (the same Factory would fail the
    same way), and the Floor says so in one sentence -- the sentence that,
    had it been printed, would have saved rounds two through eight of the
    automotive build. Returns the detail written.
    """
    from app.factory.build.acceptance_floor import FACTORY
    from app.factory.build.authority import BuildRole
    from app.factory.build.branch_attach import STORE_GATE_PATH
    from app.factory.build.failure_owner import generator_location

    root = Path(output_dir)
    ledger = _ledger(root)
    if not ledger.exists():
        ledger.start_run(product_id=root.name, inputs_hash="n3_store_gate")

    if not report.harness_ran:
        owed = ["store_gate_workflow"]
        generator = str(STORE_GATE_PATH)
        detail = (
            "store-gate did not run: the Factory's own workflow "
            f"({STORE_GATE_PATH}) failed before scoring, so no product check "
            "was measured. This is a Factory defect, not your product. No "
            "rework; do not re-run until the Factory fix is live."
        )
    else:
        owed = list(report.factory_owed)
        generator = generator_location("check_" + owed[0], "store_acceptance.py")
        detail = (
            f"store-gate: your product passed {report.product_score} of the "
            f"checks it owns. The Factory failed {len(owed)} of its own "
            f"({', '.join(owed)}) -- a Factory defect, not your product. No "
            "rework; do not re-run until the Factory fix is live "
            f"({generator})."
        )
    report.detail = detail
    write_acceptance_report(root, report)
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.STORE_MANAGER,
        detail=detail,
        payload={
            "outcome": "FAILED_GATE",
            "cycle": "code",
            "pilot_ready": False,
            "honesty": N3_STORE_GATE_FACTORY_OWED,
            "green": False,
            "next": "factory_fix",
            "rework": False,
            "failure_owner": FACTORY,
            "factory_owed": owed,
            "generator": generator,
            "product_score": report.product_score,
            "product_ok": report.product_ok,
            "harness_ran": report.harness_ran,
            "score": snap.score,
            "builds_sha": snap.sha,
            "builds_branch": snap.branch,
            "state": snap.state,
        },
    )
    return detail


def _failure_honesty(snap: StoreGateSnapshot) -> str:
    if snap.timeout:
        return N3_STORE_GATE_TIMEOUT
    if snap.missing and not snap.state:
        return N3_STORE_GATE_MISSING
    return N3_STORE_GATE_FAILED


def is_infrastructure_error(exc: BaseException) -> bool:
    """GitHub could not be asked (no token, 401/403, API down) -- as opposed to
    GitHub answering that there is nothing there."""
    return bool(getattr(exc, "unreachable", False))


def ingest_n3_store_gate(
    output_dir: Path | str,
    *,
    wait: bool = False,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: Optional[float] = None,
    wall_s: Optional[float] = None,
    session_id: Optional[str] = None,
) -> IngestResult:
    """Fetch or poll store-gate and apply the ledger stamp. Never starts a BA."""
    root = Path(output_dir)
    if not handoff_awaiting_n3(root):
        ledger_path = root / "build_ledger.jsonl"
        if ledger_path.is_file():
            try:
                ledger = BuildLedger(ledger_path)
                if ledger.succeeded() and ledger.pilot_ready():
                    return IngestResult(
                        honesty=N3_STORE_GATE_GREEN,
                        ok=True,
                        already=True,
                        detail="N3 store-gate already ingested (pilot_ready)",
                    )
            except Exception:  # noqa: BLE001
                pass
            if not handoff_awaiting_n3(root):
                # A later N3 fail is terminal; a workspace that never
                # handed off is not this seam.
                events_honesty = ""
                try:
                    for event in BuildLedger(ledger_path).events():
                        events_honesty = _event_honesty(event) or events_honesty
                except Exception:  # noqa: BLE001
                    events_honesty = ""
                if events_honesty in N3_FAIL_HONESTY:
                    return IngestResult(
                        honesty=events_honesty,
                        ok=False,
                        detail="N3 store-gate already failed-closed",
                    )
        return IngestResult(
            honesty=HANDOFF_TO_N3,
            ok=False,
            pending=False,
            detail="no HANDOFF_TO_N3 awaiting N3 on this workspace",
        )

    blob = env if env is not None else os.environ
    try:
        target = resolve_builds_target(
            root, env=blob, session_id=session_id, opener=opener
        )
    except BuildsPushError as exc:
        # Not being able to ASK GitHub is not an answer about the build. A
        # revoked token (live 2026-09-19: "GitHub API down: matching-refs HTTP
        # 401") was being written to the ledger as N3_STORE_GATE_MISSING -- a
        # terminal failure -- on a platform whose branch had passed. Leave the
        # handoff open and say why; only "this session has no build branch"
        # is a verdict.
        if is_infrastructure_error(exc):
            logger.warning("n3 store-gate unreachable for %s: %s", root, exc)
            return IngestResult(
                honesty=HANDOFF_TO_N3,
                ok=False,
                pending=True,
                detail=f"store-gate not reachable, still waiting: {exc}",
            )
        snap = StoreGateSnapshot(missing=True, detail=str(exc))
        apply_store_gate_failure(root, snap, honesty=N3_STORE_GATE_MISSING)
        return IngestResult(
            honesty=N3_STORE_GATE_MISSING,
            ok=False,
            detail=str(exc),
            snapshot=snap,
        )

    if wait:
        snap = wait_for_store_gate(
            target,
            env=blob,
            opener=opener,
            clock=clock,
            sleep=sleep,
            poll_s=poll_s,
            wall_s=wall_s,
        )
    else:
        snap = fetch_store_gate_status(target, env=blob, opener=opener)
    return judge_snapshot(root, snap, wait=wait)


def judge_snapshot(root: Path | str, snap: StoreGateSnapshot, *, wait: bool = True) -> IngestResult:
    """The verdict on one read of the gate, applied to ``root``'s ledger.

    The live ingest and the pre-merge gate replay (gate_replay.py) both judge
    a gate result here, so a replay can never pass what a build would fail."""
    root = Path(root)
    if snap.is_12_of_12:
        apply_store_gate_success(root, snap)
        return IngestResult(
            honesty=N3_STORE_GATE_GREEN,
            ok=True,
            detail=f"ingested store-gate {snap.score} on {snap.sha[:12]}",
            snapshot=snap,
        )
    if snap.pending and not wait:
        return IngestResult(
            honesty=HANDOFF_TO_N3,
            ok=False,
            pending=True,
            detail=snap.detail or "store-gate still pending",
            snapshot=snap,
        )
    # "GitHub statuses HTTP 401" / a missing token is the gate being
    # unreachable, not the gate saying no. Never write that as a verdict.
    # A poll that TIMED OUT while every read was a 401 is the same thing: the
    # waiter never once saw the gate. (Live: three handoffs were branded
    # N3_STORE_GATE_TIMEOUT by an hour of 401s from a revoked token.)
    if (snap.missing or snap.timeout) and snap.unreachable:
        logger.warning("n3 store-gate unreadable for %s: %s", root, snap.detail)
        return IngestResult(
            honesty=HANDOFF_TO_N3,
            ok=False,
            pending=True,
            detail=f"store-gate not reachable, still waiting: {snap.detail}",
            snapshot=snap,
        )
    return ingest_store_gate_snapshot(root, snap)


def ingest_store_gate_snapshot(root: Path | str, snap: StoreGateSnapshot) -> IngestResult:
    """The verdict for one scored (or failed-to-score) store-gate snapshot:
    green, the Factory's (no rework), or the product's (rework)."""
    root = Path(root)
    # Owner by construction. The product's score counts only the checks it
    # owns; a red line whose subject the Factory wrote -- or a gate that never
    # scored -- is the Factory's, routed to the factory lane, and can never
    # fail the product (acceptance_floor.owner_of derives this per line).
    report = report_from_snapshot(snap)
    # A container that died at boot is owned by whoever wrote the crashing
    # file (the build's factory receipt): a crash in Factory-stamped code is
    # factory_owed, never the writer's rework.
    if report.harness_ran and not snap.timeout and not snap.missing:
        split_image_by_origin(root, report)
    # audit_clean ownership by line origin (the build's factory receipt): a
    # finding in Factory substrate is advisory for this build, never the
    # writer's rework; a gate red only on such lines does not fail the build.
    advisory = (
        split_audit_by_origin(root, report)
        if report.harness_ran and not snap.timeout and not snap.missing
        else []
    )
    if advisory:
        record_audit_advisory(root, advisory)
        if report.ok:
            apply_store_gate_success(root, snap, report=report)
            return IngestResult(
                honesty=N3_STORE_GATE_GREEN,
                ok=True,
                detail=(
                    f"ingested store-gate {snap.score} on {snap.sha[:12]}; advisory: "
                    + ", ".join(a["check"] for a in advisory)
                ),
                snapshot=snap,
            )
    if not snap.timeout and not snap.missing and (
        not report.harness_ran or (report.product_ok and report.factory_owed)
    ):
        detail = apply_store_gate_factory_owed(root, snap, report)
        return IngestResult(
            honesty=N3_STORE_GATE_FACTORY_OWED,
            ok=False,
            detail=detail,
            snapshot=snap,
        )
    honesty = _failure_honesty(snap)
    apply_store_gate_failure(root, snap, honesty=honesty, report=report)
    return IngestResult(
        honesty=honesty,
        ok=False,
        detail=_product_failure_detail(snap, report),
        snapshot=snap,
    )


def wait_and_ingest_n3(
    output_dir: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
    opener: Callable[..., Any] = urlopen,
    **kwargs: Any,
) -> IngestResult:
    """Long-poll ingest for the background build thread after HANDOFF_TO_N3."""
    key = str(Path(output_dir).resolve())
    with _N3_GUARD:
        existing = _N3_THREADS.get(key)
        if existing is not None and existing.is_alive() and existing is not threading.current_thread():
            return IngestResult(
                honesty=HANDOFF_TO_N3,
                ok=False,
                pending=True,
                already=True,
                detail="store-gate waiter already live",
            )
        _N3_THREADS[key] = threading.current_thread()
    try:
        return ingest_n3_store_gate(
            output_dir, wait=True, env=env, opener=opener, **kwargs
        )
    finally:
        with _N3_GUARD:
            if _N3_THREADS.get(key) is threading.current_thread():
                _N3_THREADS.pop(key, None)


def start_n3_ingest_job(
    output_dir: Path | str,
    *,
    env: Optional[Mapping[str, str]] = None,
    session_id: Optional[str] = None,
) -> bool:
    """Start a daemon waiter if one is not already live. Returns True if started."""
    key = str(Path(output_dir).resolve())
    with _N3_GUARD:
        existing = _N3_THREADS.get(key)
        if existing is not None and existing.is_alive():
            return False

        def _run() -> None:
            try:
                wait_and_ingest_n3(
                    output_dir, env=env, session_id=session_id
                )
            except Exception:  # noqa: BLE001
                logger.exception("n3 store-gate waiter crashed for %s", output_dir)

        thread = threading.Thread(
            target=_run,
            name=f"n3-gate-{Path(output_dir).name}",
            daemon=True,
        )
        _N3_THREADS[key] = thread
        thread.start()
        return True


class HandoffReseedError(ValueError):
    """Refuse an unsafe or incomplete HANDOFF reseed request."""


def reseed_handoff_ledger(
    output_dir: Path | str,
    *,
    builds_sha: str,
    builds_branch: str,
    cli_authored_ids: Sequence[str],
    builds_owner: str = "bopoadz-del",
    builds_repo: str = "cerebrum-builds",
    product_id: Optional[str] = None,
    inputs_hash: str = "n3_handoff_reseed",
) -> Dict[str, Any]:
    """Stamp a minimal HANDOFF_TO_N3 ledger for one-time N3 ingest.

    Mirrors the test helper ``_handoff_ledger``: COLLECTOR/CLONER seed
    phases, a WRITER ``NOTE`` with builds fields + ``cli_authored_ids``,
    and ``RUN_FAILED`` honesty ``HANDOFF_TO_N3``. Never launches WRITER,
    Background Agent, or ``generate_product``.

    Safe rules:
    - Already awaiting N3 → leave ledger alone (``stamped=False``).
    - Already N3 green / pilot_ready → refuse.
    - Missing/empty ledger → create the minimal HANDOFF stamp.
    - Existing non-handoff ledger → append HANDOFF NOTE + RUN_FAILED only
      when builds fields are complete (caller must pass them).
    """
    root = Path(output_dir)
    sha = str(builds_sha or "").strip()
    branch = str(builds_branch or "").strip()
    owner = str(builds_owner or "").strip() or "bopoadz-del"
    repo = str(builds_repo or "").strip() or "cerebrum-builds"
    ids = [str(item).strip() for item in (cli_authored_ids or []) if str(item).strip()]
    if not sha or not branch:
        raise HandoffReseedError(
            "n3_reseed requires builds_sha and builds_branch"
        )
    if not ids:
        raise HandoffReseedError(
            "n3_reseed requires non-empty cli_authored_ids"
        )

    root.mkdir(parents=True, exist_ok=True)
    ledger = _ledger(root)

    if handoff_awaiting_n3(root):
        return {
            "stamped": False,
            "already_awaiting": True,
            "output_dir": str(root),
            "builds_sha": sha,
            "builds_branch": branch,
            "cli_authored_ids": ids,
        }

    if ledger.exists():
        try:
            if ledger.succeeded() and ledger.pilot_ready():
                raise HandoffReseedError(
                    "N3 store-gate already ingested (pilot_ready) — refuse reseed"
                )
            for event in ledger.events():
                if _event_honesty(event) == N3_STORE_GATE_GREEN:
                    raise HandoffReseedError(
                        "N3_STORE_GATE_GREEN already on ledger — refuse reseed"
                    )
        except HandoffReseedError:
            raise
        except Exception:  # noqa: BLE001
            pass

    pid = str(product_id or root.name or "product").strip() or "product"
    from app.factory.build.authority import BuildRole

    if not ledger.exists():
        ledger.start_run(product_id=pid, inputs_hash=inputs_hash)
        for role in (BuildRole.COLLECTOR, BuildRole.CLONER):
            ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
            ledger.append(
                EventKind.GATE_PASSED,
                role=role,
                detail="ok",
                payload={"gate": "seed", "via": "n3_handoff_reseed"},
            )

    payload = {
        "honesty": HANDOFF_TO_N3,
        "seam": "cli_pivot",
        "next": "n3_gate",
        "green": False,
        "cli_authored_ids": list(ids),
        "builds_sha": sha,
        "builds_branch": branch,
        "builds_owner": owner,
        "builds_repo": repo,
        "via": "n3_handoff_reseed",
    }
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="HANDOFF_TO_N3: store-gate next",
        payload=payload,
    )
    ledger.append(
        EventKind.RUN_FAILED,
        role=BuildRole.WRITER,
        detail="HANDOFF_TO_N3: store-gate next",
        payload={
            "outcome": HANDOFF_TO_N3,
            "honesty": HANDOFF_TO_N3,
            "next": "n3_gate",
            "green": False,
            "cycle": "code",
            "pilot_ready": False,
            "cli_authored_ids": list(ids),
            "builds_sha": sha,
            "builds_branch": branch,
            "builds_owner": owner,
            "builds_repo": repo,
            "via": "n3_handoff_reseed",
        },
    )
    _persist_cli_authorship(root, ids)
    if not handoff_awaiting_n3(root):
        raise HandoffReseedError(
            "reseed stamped but handoff_awaiting_n3 is still false — refuse"
        )
    return {
        "stamped": True,
        "already_awaiting": False,
        "output_dir": str(root),
        "builds_sha": sha,
        "builds_branch": branch,
        "builds_owner": owner,
        "builds_repo": repo,
        "cli_authored_ids": ids,
        "product_id": pid,
        "inputs_hash": inputs_hash,
    }
