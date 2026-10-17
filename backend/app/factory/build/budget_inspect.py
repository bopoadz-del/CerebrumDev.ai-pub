"""Stop-and-inspect coder budget — staged walls, never a silent 2h burn.

Default path: start a ~30 minute stage, inspect the ledger / workspace
(which caps were written, contract misses, timeouts, stub rate, why
``pilot_ready`` is still false), and only then decide whether to continue
with another gated brief at ~45 minutes.

An in-flight ``FACTORY_CODE_CLI`` model call that is still inside its
watchdog is not ``FACTORY_CODE_CLI_UNUSED``. Stage-1 inspect bumps
30→45 and waits; it must not kill the live CLI solely because
wall≈1800s elapsed. The CLI wait / Floor ``deadline_s`` tracks
``FACTORY_CODER_TIMEOUT_S`` (7200 → ~7230), not a frozen
``STAGE_1_S - 15`` (1785).

A leftover ``FACTORY_BUILD_WALL_CLOCK_S=7200`` or an already-granted high
wall is honoured (observe/log, do not slash). The default is not 2 hours.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.factory.build.failure_kinds import BUDGET_SKIPPED, TIMEOUT

from app.factory.build.authorship import (
    exclusive_authorship_caps,
    is_coding_agent_source,
    is_factory_grounded_source,
    is_templated_source,
    promote_cli_keep_ids,
    refuse_dual_listed_caps,
)

logger = logging.getLogger("cerebrumdev.factory.budget_inspect")

#: First stage. Hard-stop here and inspect before any extra wall.
STAGE_1_S = 1800.0
#: Optional second stage — only after inspect says real work is progressing.
STAGE_2_S = 2700.0
#: Last-resort ceiling. Never the default; never granted silently.
#:
#: The same number lives on ``BuildBudget.hard_ceiling_s``, which is where it
#: is ENFORCED -- this module only PROPOSES values, and two of the runner's
#: extension paths never ask it. One literal, one place, so proposer and
#: enforcer cannot drift; ``test_budget_ceiling`` asserts they are equal.
CEILING_S = 7200.0

INSPECT_NOTE_KIND = "budget_inspect"

def _timed_out_keys(failure_kinds: Mapping[str, Any]) -> List[str]:
    """Keys whose recorded failure kind is a timeout (failure_kinds.TIMEOUT).

    The kind is stamped where the failure happened, from the exception's
    TYPE or the dispatch blocker -- so ``timeout_s=7230`` metadata or
    ``timeouts=7`` inside a later reason can never be counted as one: the
    message text is not read at all.
    """
    return [str(k) for k, v in (failure_kinds or {}).items() if v == TIMEOUT]


def inspect_build(
    ledger: Any,
    workspace: Any = None,
    state: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Snapshot of what the run has achieved so far.

    Reads the ledger first. Provenance on disk fills authorship when the
    WRITER has already written ``docs/build_provenance.json``.
    """
    events = list(getattr(ledger, "events", lambda: ())())
    caps_written: List[str] = []
    caps_cli_or_llm: List[str] = []
    caps_factory_grounded: List[str] = []
    caps_templated: List[str] = []
    timeouts: List[str] = []
    contract_misses: List[str] = []
    current_capability = ""
    rework = 0
    model_call = ""
    phase_done = 0
    phase_total = 0

    for event in events:
        payload = getattr(event, "payload", None) or {}
        detail = str(getattr(event, "detail", "") or "")
        kind = getattr(getattr(event, "kind", None), "value", event.kind)
        cap = str(payload.get("capability") or "").strip()
        source = str(payload.get("source") or "")
        stage = str(payload.get("stage") or "")

        if cap and stage in {"handlers", "routes", "models", "coder"}:
            current_capability = cap
            if is_factory_grounded_source(source):
                if cap not in caps_written:
                    caps_written.append(cap)
                if cap not in caps_factory_grounded:
                    caps_factory_grounded.append(cap)
                if cap in caps_templated:
                    caps_templated.remove(cap)
            elif is_coding_agent_source(source):
                if cap not in caps_written:
                    caps_written.append(cap)
                if cap not in caps_cli_or_llm:
                    caps_cli_or_llm.append(cap)
                if cap in caps_templated:
                    caps_templated.remove(cap)
            elif is_templated_source(source):
                if cap not in caps_written and cap not in caps_templated:
                    caps_templated.append(cap)

        if payload.get("model_call"):
            model_call = detail or cap or "coder LLM call"

        done, total = payload.get("done"), payload.get("total")
        if isinstance(done, int) and isinstance(total, int) and total > 0:
            phase_done, phase_total = done, total

        if payload.get("failure_kind") == TIMEOUT:
            timeouts.append(detail[:240])

        if kind == "GATE_FAILED":
            findings = payload.get("findings") or []
            contract_misses.extend(str(f)[:240] for f in findings if f)
            if detail and detail not in contract_misses:
                contract_misses.append(detail[:240])
        if kind == "REWORK":
            rework += 1
            findings = payload.get("findings") or []
            contract_misses.extend(str(f)[:240] for f in findings if f)

    failures = dict((state or {}).get("coder_failures") or {})
    for key in _timed_out_keys((state or {}).get("coder_failure_kinds") or {}):
        timeouts.append(f"{key}: {str(failures.get(key, ''))[:200]}")
    kinds = dict((state or {}).get("coder_failure_kinds") or {})
    for key in failures:
        if kinds.get(key) == BUDGET_SKIPPED:
            if key not in caps_templated and key not in caps_written:
                caps_templated.append(str(key))

    provenance = _provenance(workspace)
    if provenance:
        for cap in provenance.get("agent_artifacts") or []:
            if cap not in caps_written:
                caps_written.append(str(cap))
            if cap not in caps_cli_or_llm:
                caps_cli_or_llm.append(str(cap))
            if cap in caps_templated:
                caps_templated.remove(str(cap))
        for cap in provenance.get("factory_grounded_artifacts") or []:
            if cap not in caps_written:
                caps_written.append(str(cap))
            if cap not in caps_factory_grounded:
                caps_factory_grounded.append(str(cap))
            if cap in caps_templated:
                caps_templated.remove(str(cap))
        fail_map = provenance.get("coder_failures") or {}
        for key in _timed_out_keys(provenance.get("coder_failure_kinds") or {}):
            line = f"{key}: {str(fail_map.get(key, ''))[:200]}"
            if line not in timeouts:
                timeouts.append(line)

    dispatch = dict((state or {}).get("brief_dispatch") or {})
    # Promote successful CLI keep-path ids. Do not credit unused/failed CLI
    # (thin-stub #368) or factory-grounded fill after exit 0 with no harvest
    # (explicit empty cli_authored_ids / FACTORY_CODE_CLI_NO_AUTHORSHIP).
    if str(dispatch.get("via") or "") == "cli" and dispatch.get("ok") is True:
        for cid in promote_cli_keep_ids(dispatch):
            if cid not in caps_written:
                caps_written.append(cid)
            if cid not in caps_cli_or_llm:
                caps_cli_or_llm.append(cid)
            if cid in caps_templated:
                caps_templated.remove(cid)

    caps_written, caps_templated = exclusive_authorship_caps(
        caps_written, caps_templated
    )
    refuse_dual_listed_caps(caps_written, caps_templated)

    authored = len(caps_written)
    stubbed = len(caps_templated)
    denom = authored + stubbed
    stub_rate = (stubbed / denom) if denom else 1.0 if not authored else 0.0
    cli_attempted = _cli_attempted(events, state)
    flight = _cli_flight(events, state)

    pilot_ready = False
    try:
        pilot_ready = bool(ledger.pilot_ready())
    except Exception:  # noqa: BLE001 — inspect must never fail a run
        pilot_ready = False

    blockers = _pilot_ready_blockers(
        ledger, workspace, pilot_ready=pilot_ready, stub_rate=stub_rate
    )
    progressing = is_progressing(
        caps_written=caps_written,
        caps_templated=caps_templated,
        timeouts=timeouts,
        stub_rate=stub_rate,
        phase_done=phase_done,
        phase_total=phase_total,
        rework=rework,
        model_call=model_call,
    )
    snapshot: Dict[str, Any] = {
        "kind": INSPECT_NOTE_KIND,
        "current_capability": current_capability,
        "caps_written": caps_written,
        "caps_templated": caps_templated,
        "agent_written": authored,
        "cli_or_llm_written": len(caps_cli_or_llm),
        "factory_grounded": len(caps_factory_grounded),
        "cli_authored_ids": list(
            dict((state or {}).get("brief_dispatch") or {}).get("cli_authored_ids")
            or []
        ),
        "templated": stubbed,
        "stub_rate": round(stub_rate, 3),
        "timeouts": timeouts[:12],
        "contract_misses": contract_misses[:12],
        "rework": rework,
        "phase_done": phase_done,
        "phase_total": phase_total,
        "model_call": model_call,
        "pilot_ready": pilot_ready,
        "pilot_ready_blockers": blockers,
        "progressing": progressing,
        "cli_attempted": cli_attempted,
        "cli_in_flight": flight["cli_in_flight"],
        "slot_waiting": flight["slot_waiting"],
        "cli_finished": flight["cli_finished"],
        "model_call_deadline_s": flight["model_call_deadline_s"],
        "cli_blocker": flight["cli_blocker"],
        "cli_steps_since_inspect": flight["cli_steps_since_inspect"],
        "cli_files_grown_since_inspect": flight["cli_files_grown_since_inspect"],
        # Capability counts (ledger). File counts come from provenance
        # authorship — sess_d10dfc28 inspect said templated=0 while the
        # Floor later showed 29 templated *files*. Different counters.
        "templated_caps": stubbed,
        "authored_files": provenance.get("authored_files"),
        "templated_files": provenance.get("templated_files"),
        "artifact_files": provenance.get("artifact_files"),
    }
    from app.factory.build.authorship import n_required_capabilities_from

    n_required = n_required_capabilities_from(
        snapshot, workspace, state=state
    )
    if n_required is not None:
        snapshot["n_required"] = n_required
    return snapshot


def is_progressing(
    *,
    caps_written: Sequence[str],
    caps_templated: Sequence[str],
    timeouts: Sequence[str],
    stub_rate: float,
    phase_done: int,
    phase_total: int,
    rework: int,
    model_call: str,
) -> bool:
    """True only when real manufacturing work is visible — not stubs/timeouts."""
    if caps_written:
        return True
    if phase_total > 0 and 0 < phase_done < phase_total and stub_rate < 1.0:
        return True
    if timeouts and not caps_written:
        return False
    if stub_rate >= 1.0 and not caps_written:
        return False
    if rework and not caps_written and stub_rate >= 0.9:
        return False
    if model_call and not caps_written and stub_rate >= 1.0:
        return False
    return False


def should_continue_after_inspect(snapshot: Mapping[str, Any]) -> bool:
    """Continue only when inspect shows progressing work and not pilot_ready."""
    if snapshot.get("pilot_ready"):
        return False
    return bool(snapshot.get("progressing"))


def _written_contracts_pass(snapshot: Mapping[str, Any]) -> bool:
    """True when inspect shows real writes and no contract misses.

    Per-cap timeout ledger noise must not veto this. stub_rate=1.0 with
    written=0 is a thin template run and stays fail-closed.
    """
    try:
        written = int(snapshot.get("agent_written") or 0)
    except (TypeError, ValueError):
        written = 0
    if written <= 0:
        return False
    misses = snapshot.get("contract_misses") or []
    if misses:
        return False
    try:
        stub_rate = float(snapshot.get("stub_rate") or 0.0)
    except (TypeError, ValueError):
        stub_rate = 0.0
    return stub_rate < 1.0


def _cli_progressing_for_ceiling_bump(snapshot: Mapping[str, Any]) -> bool:
    """One STAGE_2→CEILING bump needs visible work, not a quiet kimi."""
    if snapshot.get("progressing"):
        return True
    done = int(snapshot.get("phase_done") or 0)
    total = int(snapshot.get("phase_total") or 0)
    if total > 0 and 0 < done < total:
        return True
    if int(snapshot.get("agent_written") or 0) > 0:
        return True
    if int(snapshot.get("cli_or_llm_written") or 0) > 0:
        return True
    # A CodeWhale writer authors in one session and harvests at its end, so
    # caps_written stays 0 until it exits. Its own progress this stage --
    # steps it reported, files it put on disk -- is the visible work (live
    # 2026-10-08: STEP 7/8 three minutes before a 2700s kill, 238 files).
    if int(snapshot.get("cli_steps_since_inspect") or 0) > 0:
        return True
    if int(snapshot.get("cli_files_grown_since_inspect") or 0) > 0:
        return True
    return False


def next_stage_wall(
    elapsed_s: float,
    current_wall_s: float,
    snapshot: Mapping[str, Any],
) -> Optional[float]:
    """Return the next wall, or None when the stage must stay stopped.

    A wall already above stage 2 is honoured (no cut, no silent jump to
    the 2h ceiling). Default path: 30 min → inspect → 45 min only.
    """
    if current_wall_s <= 0:
        return None
    if current_wall_s > STAGE_2_S + 1:
        # Leftover / explicit high wall: observe only.
        return None
    if not should_continue_after_inspect(snapshot):
        return None
    if elapsed_s + 1 >= STAGE_1_S and current_wall_s <= STAGE_1_S + 1:
        return STAGE_2_S
    return None


def inspect_decision(
    *,
    elapsed_s: float,
    current_wall_s: float,
    snapshot: Mapping[str, Any],
    stage: str,
    state: Optional[Mapping[str, Any]] = None,
    workspace: Any = None,
    lead_s: float = 0.0,
) -> Dict[str, Any]:
    """Attach a continue/stop decision to an inspect snapshot.

    ``lead_s``: how far ahead of a stage mark the runner inspected because a
    coding-agent call is live (runner._maybe_stage_inspect). The in-flight
    bump treats the mark as reached within that lead -- an inspect run one
    heartbeat early to beat the writer's wall must still lift it, not decide
    await_cli and spend the stage's one inspect.
    """
    new_wall = next_stage_wall(elapsed_s, current_wall_s, snapshot)
    if snapshot.get("pilot_ready"):
        # Store-green close outranks leftover-wall observe. A 7230s C-BRIEF
        # wall must not hide already_pilot_ready on the closing inspect.
        new_wall = None
        decision = "already_pilot_ready"
        reason = f"inspect {stage}: pilot_ready already true"
    elif new_wall:
        decision = "continue_stage_2"
        reason = (
            f"inspect {stage}: progressing "
            f"(agent_written={snapshot.get('agent_written')}, "
            f"stub_rate={snapshot.get('stub_rate')}) — bump wall "
            f"{current_wall_s:g}s → {new_wall:g}s"
        )
    elif current_wall_s > STAGE_2_S + 1:
        decision = "inspect_only_high_wall_honored"
        reason = (
            f"inspect {stage}: leftover wall {current_wall_s:g}s honoured "
            f"(no cut, no silent extra ceiling)"
        )
    elif snapshot.get("cli_in_flight") or snapshot.get("slot_waiting"):
        # sess_9d8e9a2dc01b40a1: UI still inside the CLI watchdog while
        # stage_1 hard-stopped as FACTORY_CODE_CLI_UNUSED at ~1800s.
        # Mid-run inspect may bump 30→45; it must not kill the live call.
        watchdog = snapshot.get("model_call_deadline_s")
        watchdog_bit = (
            f"{watchdog:g}s watchdog" if watchdog else "watchdog still open"
        )
        reached = float(elapsed_s) + float(lead_s) + 1
        if reached >= STAGE_1_S and current_wall_s <= STAGE_1_S + 1:
            new_wall = STAGE_2_S
            decision = "continue_stage_2"
            reason = (
                f"inspect {stage}: FACTORY_CODE_CLI in-flight "
                f"({watchdog_bit}, written={snapshot.get('agent_written')}, "
                f"stub_rate={snapshot.get('stub_rate')}) — bump wall "
                f"{current_wall_s:g}s → {new_wall:g}s; not "
                "FACTORY_CODE_CLI_UNUSED"
            )
        elif (
            reached >= STAGE_2_S
            and current_wall_s <= STAGE_2_S + 1
            and _cli_progressing_for_ceiling_bump(snapshot)
        ):
            new_wall = CEILING_S
            decision = "continue_ceiling"
            reason = (
                f"inspect {stage}: FACTORY_CODE_CLI in-flight and progressing "
                f"({watchdog_bit}, written={snapshot.get('agent_written')}, "
                f"phase={snapshot.get('phase_done')}/"
                f"{snapshot.get('phase_total')}) — one inspect-gated bump "
                f"{current_wall_s:g}s → {new_wall:g}s; not "
                "FACTORY_CODE_CLI_UNUSED; not a silent 2h grant"
            )
        else:
            decision = "await_cli"
            reason = (
                f"inspect {stage}: FACTORY_CODE_CLI in-flight "
                f"({watchdog_bit}) — wait for the CLI to finish or fail; "
                "not FACTORY_CODE_CLI_UNUSED"
            )
    else:
        from app.factory.build.coder_session import thin_stub_success_blocked

        unused = thin_stub_success_blocked(
            snapshot=snapshot,
            elapsed_s=elapsed_s,
            state=state,
            ledger=None,
            workspace=workspace,
        )
        dispatch = dict((state or {}).get("brief_dispatch") or {})
        cli_finished = bool(snapshot.get("cli_finished")) or (
            str(dispatch.get("via") or "") == "cli"
        )
        after_wall = float(elapsed_s) + 1.0 >= float(STAGE_1_S)
        if unused and not cli_finished and not after_wall:
            # Keep the staged wall alive — do not SUCCESS thin templates
            # and do not treat 8s stub_rate=1.0 as the only coding chance.
            decision = "await_cli"
            reason = (
                f"inspect {stage}: await FACTORY_CODE_CLI stage-1 wall — "
                f"{unused}"
            )
        elif unused:
            decision = "hard_stop"
            reason = (
                f"inspect {stage}: hard-stop — {unused}"
            )
        elif _written_contracts_pass(snapshot):
            # sess_d10dfc28: written=7, templated=0, contract_misses=0,
            # timeouts=7 ledger noise, then hard-stop at pilot_open
            # blocked RUN_SUCCEEDED. Written work + green contracts is
            # not a halt — bump once so the pilot suite can run.
            if (
                elapsed_s + 1 >= STAGE_2_S
                and current_wall_s <= STAGE_2_S + 1
            ):
                new_wall = CEILING_S
                decision = "continue_ceiling"
                reason = (
                    f"inspect {stage}: written="
                    f"{snapshot.get('agent_written')} contracts pass — "
                    f"one inspect-gated bump {current_wall_s:g}s → "
                    f"{new_wall:g}s so the pilot suite can run; timeout "
                    "ledger is not a hard-stop"
                )
            else:
                decision = "continue_pilot"
                reason = (
                    f"inspect {stage}: written="
                    f"{snapshot.get('agent_written')} contracts pass — "
                    "continue toward Store-green; timeout ledger "
                    f"({len(snapshot.get('timeouts') or [])}) is not a halt"
                )
        else:
            decision = "hard_stop"
            reason = (
                f"inspect {stage}: hard-stop — "
                f"cap={snapshot.get('current_capability') or 'none'}, "
                f"written={snapshot.get('agent_written')}, "
                f"templated={snapshot.get('templated')}, "
                f"stub_rate={snapshot.get('stub_rate')}, "
                f"timeouts={len(snapshot.get('timeouts') or [])}, "
                f"contract_misses={len(snapshot.get('contract_misses') or [])}, "
                f"pilot_ready=false ({'; '.join(snapshot.get('pilot_ready_blockers') or ['unknown'])})"
            )
    if str(stage) == "pilot_open" and decision == "hard_stop":
        # _grant_pilot_budget treats this inspect as informational.
        # sess_d10dfc28 logged hard-stop here (written=7, timeouts=7
        # noise, pilot_ready=false) then still SUCCESS-ed Store-green.
        # Mid-run pilot_ready=false is expected — no RUN_SUCCEEDED yet.
        decision = "observe_pilot_open"
        new_wall = None
        reason = (
            f"inspect {stage}: observe — mid-run pilot_ready=false is "
            f"expected before RUN_SUCCEEDED; written="
            f"{snapshot.get('agent_written')} templated_caps="
            f"{snapshot.get('templated')} templated_files="
            f"{snapshot.get('templated_files')} timeouts="
            f"{len(snapshot.get('timeouts') or [])}. Not a halt."
        )
    out = dict(snapshot)
    out.update(
        {
            "elapsed_s": round(float(elapsed_s), 1),
            "stage": stage,
            "current_wall_s": float(current_wall_s),
            "next_wall_s": new_wall,
            "continue": new_wall is not None,
            "decision": decision,
            "reason": reason,
        }
    )
    if str(stage) == "pilot_open" and decision == "hard_stop":
        # _grant_pilot_budget treats this inspect as informational.
        # sess_d10dfc28 logged hard-stop here (written=7, timeouts=7
        # noise, pilot_ready=false) then still SUCCESS-ed Store-green.
        # Mid-run pilot_ready=false is expected — no RUN_SUCCEEDED yet.
        decision = "observe_pilot_open"
        reason = (
            f"inspect {stage}: observe — mid-run pilot_ready=false is "
            f"expected before RUN_SUCCEEDED; written="
            f"{snapshot.get('agent_written')} templated_caps="
            f"{snapshot.get('templated')} templated_files="
            f"{snapshot.get('templated_files')} timeouts="
            f"{len(snapshot.get('timeouts') or [])}. Not a halt."
        )
        new_wall = None
    out["next_wall_s"] = new_wall
    out["continue"] = new_wall is not None
    out["decision"] = decision
    out["reason"] = reason
    logger.info("factory budget inspect: %s", reason)
    return out


def reconcile_budget_inspect_after_success(
    last_inspect: Optional[Mapping[str, Any]],
    *,
    pilot_ready: bool,
) -> Optional[Dict[str, Any]]:
    """Stop a mid-run hard-stop inspect from reading as current truth.

    sess_d10dfc28: last ledger inspect said hard-stop / pilot_ready=false
    / templated=0, then RUN_SUCCEEDED Store-green. Floor status must not
    keep that inspect as the live verdict.
    """
    if not last_inspect:
        return None
    out = dict(last_inspect)
    if not pilot_ready:
        return out
    mid_ready = out.get("pilot_ready")
    mid_decision = out.get("decision")
    if mid_ready is True and mid_decision != "hard_stop":
        return out
    out["mid_run_pilot_ready"] = mid_ready
    out["mid_run_decision"] = mid_decision
    out["pilot_ready"] = True
    out["superseded_by"] = "RUN_SUCCEEDED"
    if mid_decision == "hard_stop":
        out["decision"] = "superseded_by_pilot_success"
        out["reason"] = (
            "mid-run inspect hard-stop superseded by Store-green "
            "RUN_SUCCEEDED — mid-run pilot_ready=false is expected "
            "before the pilot cycle closes"
        )
    return out


def _cli_attempted(
    events: Sequence[Any], state: Optional[Mapping[str, Any]]
) -> bool:
    dispatch = dict((state or {}).get("brief_dispatch") or {})
    if str(dispatch.get("via") or "") == "cli":
        return True
    from app.factory.build.authorship import SOURCE_CODER_CLI, is_coding_agent_source

    for event in events:
        payload = getattr(event, "payload", None) or {}
        source = payload.get("source")
        if str(source or "") == SOURCE_CODER_CLI:
            return True
        # The codewhale worker's model call is a CLI attempt too.
        if payload.get("model_call") and is_coding_agent_source(source):
            return True
    return False


def _cli_flight(
    events: Sequence[Any], state: Optional[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Whether FACTORY_CODE_CLI is still inside its model-call watchdog.

    ``brief_dispatch`` is written only after the session returns. A live
    kimi/DeepSeek call therefore has the dispatch NOTE but no receipt.
    """
    dispatch = dict((state or {}).get("brief_dispatch") or {})
    dispatched = False
    finished = False
    # A coding agent queued for a build slot (typed ``slot_wait`` NOTE, open
    # until it holds its slot). It has no model call open, but it is the
    # run's live work: the budget must not kill it at the phase box -- the
    # wait is bounded by the phase-wall ceiling only (owner, 2026-10-06).
    slot_waiting = False
    deadline_s: Optional[float] = None
    from app.factory.build.authorship import is_coding_agent_source
    from app.factory.build.model_call import closes_model_call

    for event in events:
        payload = getattr(event, "payload", None) or {}
        # Any coding-agent CLI's model call -- the C-BRIEF "coder CLI" and
        # the codewhale worker ("codewhale_worker") alike. Matching only the
        # one exact "coder CLI" string meant the codewhale path, which is
        # what production runs, was never seen in flight: the ramp never
        # fired and the writer died at its dispatch wall (live 2026-10-06).
        if payload.get("model_call") and is_coding_agent_source(payload.get("source")):
            # A NEW call (a rework round re-dispatches the writer) re-opens
            # the flight: ``finished`` describes the LATEST call, never an
            # earlier one. Without this, the first call's close marked every
            # later call finished the moment it started, and the ramp never
            # fired for a rework round's writer.
            if not payload.get("cli_wall_extended"):
                finished = False
            dispatched = True
            slot_waiting = False
        slot = payload.get("slot_wait")
        if slot is not None and is_coding_agent_source(payload.get("source")):
            from app.factory.build.codewhale_worker import SLOT_WAIT_OPEN

            slot_waiting = slot == SLOT_WAIT_OPEN
        if closes_model_call(payload):
            finished = True
        if payload.get("model_call"):
            raw = payload.get("deadline_s")
            if raw is not None:
                try:
                    deadline_s = float(raw)
                except (TypeError, ValueError):
                    pass
    via = str(dispatch.get("via") or "")
    if via:
        finished = True
        if via == "cli":
            dispatched = True
    blocker = str(dispatch.get("blocker") or "") or None
    return {
        "cli_in_flight": bool(dispatched and not finished),
        "slot_waiting": bool(slot_waiting),
        "cli_finished": bool(dispatched and finished),
        "model_call_deadline_s": deadline_s,
        "cli_blocker": blocker,
        **_cli_progress_since_inspect(events),
    }


def _cli_progress_since_inspect(events: Sequence[Any]) -> Dict[str, int]:
    """What the coding agent has visibly done since the previous inspect.

    Read from the worker's typed progress fields (steps the agent reported,
    files on disk) -- never from the narration text. Counting from the last
    inspect NOTE means "work in THIS stage": a writer that reported steps an
    hour ago and has been silent since does not qualify.
    """
    from app.factory.build.authorship import is_coding_agent_source
    from app.factory.build.codewhale_worker import FILES_ON_DISK, STEPS_REPORTED

    start = 0
    for index, event in enumerate(events):
        payload = getattr(event, "payload", None) or {}
        if payload.get("budget_inspect") or payload.get("kind") == INSPECT_NOTE_KIND:
            start = index + 1

    def _int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    files_before: Optional[int] = None
    for event in events[:start]:
        payload = getattr(event, "payload", None) or {}
        if is_coding_agent_source(payload.get("source")):
            seen = _int(payload.get(FILES_ON_DISK))
            if seen is not None:
                files_before = seen
    steps = 0
    files_after: Optional[int] = None
    for event in events[start:]:
        payload = getattr(event, "payload", None) or {}
        if not is_coding_agent_source(payload.get("source")):
            continue
        if _int(payload.get(STEPS_REPORTED)) is not None and _int(payload.get(FILES_ON_DISK)) is None:
            steps += 1  # a STEP line (heartbeats carry the file count too)
        seen = _int(payload.get(FILES_ON_DISK))
        if seen is not None:
            files_after = seen
    grown = 0
    if files_after is not None:
        grown = max(0, files_after - (files_before or 0))
    return {"cli_steps_since_inspect": steps, "cli_files_grown_since_inspect": grown}


def _provenance(workspace: Any) -> Dict[str, Any]:
    from pathlib import Path
    import json

    root = None
    if workspace is None:
        return {}
    if hasattr(workspace, "workspace"):
        root = Path(workspace.workspace)
    else:
        root = Path(workspace)
    manifest = root / "docs" / "build_provenance.json"
    if not manifest.is_file():
        return {}
    try:
        prov = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    sources = prov.get("artifact_sources") or {}
    agent = sorted(k for k, v in sources.items() if is_coding_agent_source(v))
    factory = sorted(k for k, v in sources.items() if is_factory_grounded_source(v))
    from app.factory.build.authorship import writer_authorship_counts

    counts = writer_authorship_counts(sources)
    return {
        "agent_artifacts": agent,
        "factory_grounded_artifacts": factory,
        "coder_failures": prov.get("coder_failures") or {},
        "authored_files": counts["agent_written"],
        "templated_files": counts["templated"],
        "artifact_files": counts["artifacts"],
    }


def _pilot_ready_blockers(
    ledger: Any,
    workspace: Any,
    *,
    pilot_ready: bool,
    stub_rate: float,
) -> List[str]:
    blockers: List[str] = []
    if not pilot_ready:
        blockers.append("pilot_ready is false")
    if stub_rate >= 1.0:
        blockers.append("all visible artifacts are templated/stubbed")
    try:
        terminal = ledger.terminal_event()
    except Exception:  # noqa: BLE001
        terminal = None
    if terminal is None:
        blockers.append("no RUN_SUCCEEDED pilot cycle")
    else:
        payload = getattr(terminal, "payload", None) or {}
        cycle = str(payload.get("cycle") or "")
        if cycle != "pilot":
            blockers.append(f"cycle is {cycle or 'code'}, not pilot")
    # Do not call grade_workspace here — it reads build_status and would
    # recurse when status itself attaches this snapshot.
    # Deduplicate while keeping order.
    seen = set()
    out: List[str] = []
    for item in blockers:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out
