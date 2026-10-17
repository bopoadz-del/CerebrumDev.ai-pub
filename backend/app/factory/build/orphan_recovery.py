"""Recover WRITER / FACTORY_CODE_CLI model calls orphaned by process restart.

Live sess_05914670d8f34533 (estate-management, Estate Steward, tip d4a4029 /
#382 deploy dep-daf3htmq at 2026-09-07T04:09:26Z) froze at WRITER dispatch:

* state=building, phases_done=2 (COLLECTOR+CLONER), current_phase=WRITER
* model_call_in_progress=true, model_call_deadline_s=7230
* last_event_at frozen at 04:09:21Z; coder_brief / coder_log absent
* instance churn 7bxn4 → t5hm8 → 9lcrr killed the runner thread

The ledger NOTE is not a live worker. On boot, resume the gated C-BRIEF
(RoleRunner re-enters interrupted WRITER — one compiled brief, not a
per-capability handle() loop) when a blueprint is available; otherwise
fail closed with ``FACTORY_CODE_CLI_ORPHANED``.

#383 then over-scanned: every leftover ``model_call`` on disk (including
historical ``__run2`` / already-terminal / abandoned ledgers) was treated
as a live zombie. Boot recovery must resume at most one *recent, active*
WRITER orphan — never a resume storm.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("cerebrumdev.factory.orphan_recovery")

ORPHAN_FAIL_DETAIL = (
    "FACTORY_CODE_CLI_ORPHANED: WRITER / FACTORY_CODE_CLI model call has no "
    "live worker after process restart — coding agent stopped (not in progress)"
)
ORPHAN_RESUME_NOTE = (
    "resuming orphaned WRITER C-BRIEF after process restart "
    "(FACTORY_CODE_CLI_ORPHANED)"
)
ORPHAN_DISK_ERROR_DETAIL = (
    "FACTORY_CODE_CLI_ORPHANED: ledger not writable after process restart "
    "— coding agent stopped (not resumed)"
)
#: Typed reason a restart resume did not start: the session records no
#: account to bind, and the worker refuses an unbound build. The owner's
#: Continue re-enters it under their own authenticated account.
ORPHAN_NO_TENANT_REASON = "resume_needs_tenant"
ORPHAN_NO_TENANT_DETAIL = (
    "build interrupted by a server restart: resuming it needs the account "
    "that started it, and none is recorded -- press Continue to resume it "
    "under your account"
)
_RUN_SUFFIX_RE = re.compile(r"__run(\d+)$")
_SKIP_NOT_ACTIVE = "not_the_active_orphan"


def iter_session_build_dirs(outputs_root: Optional[Path] = None) -> List[Path]:
    """``factory_outputs/sessions/{session_id}/{product_id}`` workspaces."""
    from app.factory.paths import factory_outputs_root

    root = Path(outputs_root) if outputs_root is not None else factory_outputs_root()
    sessions = root / "sessions"
    if not sessions.is_dir():
        return []
    found: List[Path] = []
    for session_dir in sorted(sessions.iterdir()):
        if not session_dir.is_dir():
            continue
        for product_dir in sorted(session_dir.iterdir()):
            if (product_dir / "build_ledger.jsonl").is_file():
                found.append(product_dir)
    return found


def session_id_from_output(output_dir: Path | str) -> Optional[str]:
    parts = Path(output_dir).resolve().parts
    try:
        idx = parts.index("sessions")
    except ValueError:
        return None
    if idx + 1 >= len(parts):
        return None
    return parts[idx + 1]


def _storage_sessions_root() -> Path:
    return Path(os.getenv("STORAGE_PATH", "./storage")) / "sessions"


def load_blueprint_for_workspace(
    output_dir: Path | str,
    *,
    storage_root: Optional[Path] = None,
) -> Optional[Any]:
    """Session snapshot first, then workspace ``docs/blueprint/`` copy."""
    from app.factory.blueprint import ProductBlueprint

    out = Path(output_dir)
    session_id = session_id_from_output(out)
    roots: List[Path] = []
    if storage_root is not None:
        roots.append(Path(storage_root) / "sessions")
    roots.append(_storage_sessions_root())
    if session_id:
        for root in roots:
            path = root / session_id / "state.json"
            bp = _blueprint_from_state_path(path)
            if bp is not None:
                return bp
        try:
            from app.core.session_persistence import load_session_state

            state = load_session_state(session_id)
        except Exception:  # noqa: BLE001
            state = None
        if state is not None:
            raw = getattr(getattr(state, "product_design", None), "blueprint", None)
            if isinstance(raw, dict) and raw:
                try:
                    return ProductBlueprint.model_validate(raw)
                except Exception:  # noqa: BLE001
                    logger.warning("session %s blueprint failed validation", session_id)
    for rel in (
        Path("docs") / "blueprint" / "product_blueprint.json",
        Path("docs") / "product_blueprint.json",
    ):
        path = out / rel
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            try:
                return ProductBlueprint.model_validate(data)
            except Exception:  # noqa: BLE001
                continue
    return None


def load_tenant_for_workspace(
    output_dir: Path | str,
    *,
    storage_root: Optional[Path] = None,
) -> Optional[str]:
    """The account the build was started under, from durable session state.

    At boot there is no request and no principal. The session state the
    Floor started the build from records the account (``user_id``) -- the
    same identity every Floor start passes as ``tenant_identity`` -- so a
    restart resume binds exactly that tenant. None when nothing is recorded:
    the caller must then stop, never run unbound.
    """
    session_id = session_id_from_output(output_dir)
    if not session_id:
        return None
    roots: List[Path] = []
    if storage_root is not None:
        roots.append(Path(storage_root) / "sessions")
    roots.append(_storage_sessions_root())
    for root in roots:
        path = root / session_id / "state.json"
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        account = str((data or {}).get("user_id") or "").strip()
        if account:
            return account
    try:
        from app.core.session_persistence import load_session_state

        state = load_session_state(session_id)
    except Exception:  # noqa: BLE001
        state = None
    account = str(getattr(state, "user_id", None) or "").strip()
    return account or None


def _blueprint_from_state_path(path: Path) -> Optional[Any]:
    from app.factory.blueprint import ProductBlueprint

    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    raw = (data.get("product_design") or {}).get("blueprint")
    if not isinstance(raw, dict) or not raw:
        return None
    try:
        return ProductBlueprint.model_validate(raw)
    except Exception:  # noqa: BLE001
        return None


def _workspace_run_number(output_dir: Path | str) -> Tuple[str, int]:
    name = Path(output_dir).name
    match = _RUN_SUFFIX_RE.search(name)
    if match:
        return _RUN_SUFFIX_RE.sub("", name), int(match.group(1))
    return name, 1


def is_superseded_workspace(output_dir: Path | str) -> bool:
    """True when a later ``{stem}__runN`` sibling already has a ledger."""
    out = Path(output_dir)
    parent = out.parent
    if not parent.is_dir():
        return False
    stem, mine = _workspace_run_number(out)
    for sibling in parent.iterdir():
        if not sibling.is_dir() or sibling == out:
            continue
        sib_stem, sib_n = _workspace_run_number(sibling)
        if sib_stem != stem:
            continue
        if not (sibling / "build_ledger.jsonl").is_file():
            continue
        if sib_n > mine:
            return True
    return False


def _open_calling_note(output_dir: Path | str) -> Tuple[Any, Any, Any]:
    """Return ``(ledger, events, calling_note)`` or ``(None, (), None)``."""
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import _ledger_path, _open_model_call_note

    path = _ledger_path(output_dir)
    if not path.is_file():
        return None, (), None
    ledger = BuildLedger(path)
    events = ledger.events()
    notes = [e for e in events if e.kind is EventKind.NOTE]
    activity = [
        e
        for e in notes
        if not (e.payload or {}).get("budget_inspect")
        and (e.payload or {}).get("kind") != "budget_inspect"
    ]
    return ledger, events, _open_model_call_note(activity)


def last_activity_age_s(output_dir: Path | str) -> float:
    from app.factory.build_jobs import _event_age_s

    _ledger, events, calling = _open_calling_note(output_dir)
    ts = ""
    if calling is not None:
        ts = str(getattr(calling, "ts", "") or "")
    if not ts and events:
        ts = str(getattr(events[-1], "ts", "") or "")
    return _event_age_s(ts, float("inf"))


def _resume_deadline_s(calling: Any) -> float:
    payload = (getattr(calling, "payload", None) or {}) if calling else {}
    raw = payload.get("deadline_s")
    try:
        deadline = float(raw)
    except (TypeError, ValueError):
        deadline = 0.0
    if deadline > 0:
        return deadline
    try:
        from app.factory.llm_watchdog import attempt_wall_s

        return float(attempt_wall_s())
    except Exception:  # noqa: BLE001
        return 2400.0


def _coder_control_allows_resume(output_dir: Path | str) -> bool:
    from app.factory.build.coder_session import (
        CONTROL_PAUSE,
        CONTROL_RUN,
        CONTROL_STOP,
        read_control,
    )

    action = read_control(Path(output_dir))
    if action in {CONTROL_STOP, CONTROL_PAUSE}:
        return False
    return action == CONTROL_RUN


def _writer_inflight(ledger: Any, calling: Any) -> bool:
    from app.factory.build.authority import BuildRole

    interrupted = getattr(ledger, "interrupted_role", lambda: None)()
    if interrupted is BuildRole.WRITER:
        return True
    role = getattr(calling, "role", None)
    return role is BuildRole.WRITER


def is_orphaned_inflight_workspace(output_dir: Path | str) -> bool:
    """Non-terminal ledger with an open model_call and no live worker."""
    from app.factory.build_jobs import _live_runner_thread

    try:
        ledger, events, calling = _open_calling_note(output_dir)
        if ledger is None or calling is None:
            return False
        if ledger.terminal_event() is not None:
            return False
        # Keyed by this workspace: another tenant's build of the same
        # product id is not this build's worker.
        return not _live_runner_thread(output_dir)
    except Exception:  # noqa: BLE001 — torn ledger is not a resume source
        logger.warning("could not inspect ledger at %s", output_dir, exc_info=True)
        return False


def is_boot_resumable_orphan(output_dir: Path | str) -> bool:
    """Recent, owner-still-running WRITER orphan — not a historical leftover.

    Requires: open model_call, no live thread, not terminal SUCCESS/FAIL,
    ``coder_control=run`` (missing file defaults to run), interrupted WRITER,
    last activity still inside the calling-NOTE deadline, and not superseded
    by a later ``__runN`` sibling.
    """
    if not is_orphaned_inflight_workspace(output_dir):
        return False
    if is_superseded_workspace(output_dir):
        return False
    if not _coder_control_allows_resume(output_dir):
        return False
    try:
        ledger, _events, calling = _open_calling_note(output_dir)
        if ledger is None or calling is None:
            return False
        if not _writer_inflight(ledger, calling):
            return False
        age = last_activity_age_s(output_dir)
        if age > _resume_deadline_s(calling):
            return False
    except Exception:  # noqa: BLE001
        logger.warning("could not score boot-resume at %s", output_dir, exc_info=True)
        return False
    return True


def fail_orphaned_model_call(
    output_dir: Path | str,
    *,
    detail: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Write RUN_FAILED so Floor shows coder-stopped, not a 7230s zombie."""
    from app.factory.build.coder_session import NAMED_BLOCKER_CLI_ORPHANED
    from app.factory.build.ledger import BuildLedger, EventKind, LedgerError
    from app.factory.build_jobs import (
        FACTORY_CODE_CLI_ORPHANED,
        _ledger_path,
        _write_crash_marker,
    )

    text = (detail or ORPHAN_FAIL_DETAIL).strip()
    if NAMED_BLOCKER_CLI_ORPHANED not in text:
        text = f"{NAMED_BLOCKER_CLI_ORPHANED}: {text}"
    payload: Dict[str, Any] = {
        "outcome": "FAILED_ROLE_ERROR",
        "honesty": FACTORY_CODE_CLI_ORPHANED,
        "orphan_recovery": True,
    }
    if reason:
        # A typed stop the owner acts on: Continue re-enters the build under
        # the owner's own authenticated account.
        payload.update({"reason": reason, "next": "continue"})
    try:
        BuildLedger(_ledger_path(output_dir)).append(
            EventKind.RUN_FAILED,
            detail=text,
            payload=payload,
        )
    except (OSError, LedgerError):
        logger.exception("could not fail-close orphaned model call at %s", output_dir)
        _write_crash_marker(output_dir, text)
        return {"action": "error", "output_dir": str(output_dir), "detail": text}
    except Exception:  # noqa: BLE001
        logger.exception("could not fail-close orphaned model call at %s", output_dir)
        _write_crash_marker(output_dir, text)
        return {"action": "error", "output_dir": str(output_dir), "detail": text}
    logger.error("failed closed orphaned FACTORY_CODE_CLI at %s: %s", output_dir, text)
    result: Dict[str, Any] = {
        "action": "failed",
        "output_dir": str(output_dir),
        "detail": text,
        "honesty": FACTORY_CODE_CLI_ORPHANED,
    }
    if reason:
        result["reason"] = reason
    return result


def resume_orphaned_model_call(
    output_dir: Path | str,
    blueprint: Any,
    *,
    tenant_identity: Optional[str],
    cycle: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-enter RoleRunner at interrupted WRITER (gated C-BRIEF, not micro-fixer).

    ``tenant_identity`` is the account the build was started under (see
    ``load_tenant_for_workspace``). It is required: the worker refuses an
    unbound job, so a resume without one would only die later as a WRITER
    failure it never had. Without it the build stops here, typed.
    """
    if not str(tenant_identity or "").strip():
        return fail_orphaned_model_call(
            output_dir, detail=ORPHAN_NO_TENANT_DETAIL, reason=ORPHAN_NO_TENANT_REASON
        )
    from app.factory.build.ledger import BuildLedger, EventKind, LedgerError
    from app.factory.build_jobs import (
        FACTORY_CODE_CLI_ORPHANED,
        _ledger_path,
        _write_crash_marker,
        start_runner_build,
    )

    out = Path(output_dir)
    try:
        BuildLedger(_ledger_path(out)).append(
            EventKind.NOTE,
            detail=ORPHAN_RESUME_NOTE,
            payload={
                "stage": "dispatch",
                "orphan_recovery": True,
                "honesty": FACTORY_CODE_CLI_ORPHANED,
            },
        )
    except (OSError, LedgerError) as exc:
        # Do not start a runner that will crash-loop on the same disk error.
        text = f"{ORPHAN_DISK_ERROR_DETAIL}: {type(exc).__name__}: {exc}"
        logger.exception("could not note orphan resume at %s", out)
        _write_crash_marker(out, text)
        return fail_orphaned_model_call(out, detail=text)
    result = start_runner_build(
        blueprint, out, cycle=cycle, tenant_identity=str(tenant_identity).strip()
    )
    logger.info(
        "resumed orphaned WRITER at %s already_running=%s",
        out,
        result.get("already_running"),
    )
    return {
        "action": "resumed",
        "output_dir": str(result.get("output_dir") or out),
        "product_id": result.get("product_id"),
        "already_running": result.get("already_running"),
        "build": result.get("build"),
    }


def _choose_active_orphan(candidates: List[Path]) -> Optional[Path]:
    if not candidates:
        return None
    return min(candidates, key=last_activity_age_s)


def recover_orphaned_model_calls(
    *,
    outputs_root: Optional[Path] = None,
    storage_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Boot hook: resume at most one recent in-flight WRITER orphan."""
    from app.factory.build.coder_session import CodeCliUnavailable

    results: List[Dict[str, Any]] = []
    eligible = [
        Path(output_dir)
        for output_dir in iter_session_build_dirs(outputs_root)
        if is_boot_resumable_orphan(output_dir)
    ]
    chosen = _choose_active_orphan(eligible)
    for output_dir in eligible:
        if chosen is None or output_dir != chosen:
            results.append(
                {
                    "action": "skipped",
                    "output_dir": str(output_dir),
                    "reason": _SKIP_NOT_ACTIVE,
                }
            )
    if chosen is None:
        if results:
            logger.info(
                "orphan model_call recovery: %s",
                [(r.get("action"), r.get("output_dir")) for r in results],
            )
        return results

    blueprint = load_blueprint_for_workspace(chosen, storage_root=storage_root)
    if blueprint is None:
        results.append(
            fail_orphaned_model_call(
                chosen,
                detail=(
                    f"{ORPHAN_FAIL_DETAIL}; no product_blueprint to resume "
                    f"C-BRIEF for {chosen}"
                ),
            )
        )
    else:
        try:
            tenant = load_tenant_for_workspace(chosen, storage_root=storage_root)
            results.append(
                resume_orphaned_model_call(chosen, blueprint, tenant_identity=tenant)
            )
        except CodeCliUnavailable as exc:
            results.append(fail_orphaned_model_call(chosen, detail=str(exc)))
        except Exception as exc:  # noqa: BLE001 — boot must not die
            logger.exception("orphan resume failed at %s", chosen)
            results.append(
                fail_orphaned_model_call(
                    chosen,
                    detail=f"{ORPHAN_FAIL_DETAIL}; resume raised {exc}",
                )
            )
    logger.info(
        "orphan model_call recovery: %s",
        [(r.get("action"), r.get("output_dir")) for r in results],
    )
    return results


def recover_stranded_n3_handoffs(
    *, outputs_root: Optional[Path] = None
) -> List[Dict[str, Any]]:
    """Boot hook: restart the Store-gate waiter for every handed-off build.

    After a handoff the verdict is collected by ``wait_and_ingest_n3`` -- a
    daemon thread inside this process. Any restart during the CI wait (a
    deploy is enough) kills it, and nothing started it again: the platform
    passed in Docker on cerebrum-builds and the Factory went on saying
    "building / HANDOFF_TO_N3" until someone typed 'continue' in that exact
    session. Audit 2026-09-19: 8 of 31 build branches were green on
    cerebrum-builds and never ingested.

    Unlike WRITER recovery this is not limited to one: a waiter is a cheap
    poll that ends on a verdict or its own wall, and an old branch whose gate
    already finished is ingested on the first fetch.
    """
    from app.factory.build.n3_store_gate import (
        handoff_awaiting_n3,
        n3_ingest_live,
        start_n3_ingest_job,
    )

    results: List[Dict[str, Any]] = []
    for output_dir in iter_session_build_dirs(outputs_root):
        try:
            if is_superseded_workspace(output_dir):
                continue
            if not handoff_awaiting_n3(output_dir) or n3_ingest_live(output_dir):
                continue
            started = start_n3_ingest_job(
                output_dir, session_id=session_id_from_output(output_dir)
            )
            results.append(
                {"action": "n3_waiter_restarted" if started else "already_live",
                 "output_dir": str(output_dir)}
            )
        except Exception:  # noqa: BLE001 -- one torn workspace must not stop the scan
            logger.warning("n3 handoff recovery failed at %s", output_dir, exc_info=True)
    if results:
        logger.info("n3 handoff recovery: restarted %d waiter(s)", len(results))
    return results
