"""Level grade — founding-customer-ready is earned, never implied.

The three-gate verdict (CODE / PRODUCT / STORE) says what ran. This grade
says whether the artifact is a full exportable pilot or a thin scaffold.

Fail-closed: ``pilot_ready`` false cannot become STORE_GREEN or
FOUNDING_CUSTOMER_READY. Missing 14-class files, HTTP store callbacks, or
absent payload-contract helpers also block founding.

A CLI billing/auth miss (``FACTORY_CODE_CLI_FAILED`` /
``FACTORY_CODE_CLI_BILLING`` / equivalent), factory-LLM GENERATE
fallthrough, or template-majority / near-zero agent-written authorship
cannot stamp founding-customer-ready. Keep-paths may still be
Store-green when PRODUCT + STORE pass *and* authorship meets the
launching-ready full-pilot floor (need = min(5, max(1, n_required))
agent-written action handlers or ``cli_authored_ids``; unknown
n_required keeps need=5). Below that floor the grade demotes
``pilot_ready`` and refuses Store-green — thin Download is a lie.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.factory.build.authority import BuildRole
from app.factory.build.authorship import full_pilot_authorship_from
from app.factory.build.converge import FOURTEEN_ARTIFACT_CLASSES, present_classes
from app.factory.build.product_gate import GATE_SCOPES

GRADE_EMITTER = "app.factory.build.level_grade.grade_workspace"

#: Files a founding-customer-ready export must carry in addition to the
#: 14-class contract. Absence is a blocker, not a warning.
FOUNDING_EXTRA_FILES: Sequence[str] = (
    "Dockerfile",
    "README.md",
    "requirements.txt",
    "app/block_inputs.py",
    "tests/test_routes.py",
    "frontend/src/App.tsx",
)


class Level(str, Enum):
    SCAFFOLD = "SCAFFOLD"
    CODE_GREEN = "CODE_GREEN"
    STORE_GREEN = "STORE_GREEN"
    FOUNDING_CUSTOMER_READY = "FOUNDING_CUSTOMER_READY"


def three_gate_verdict(status: Mapping[str, Any]) -> Dict[str, str]:
    """CODE / PRODUCT / STORE from the run's TYPED state -- never from the
    sentence the runner prints.

    Every producer of that sentence (runner._finish on SUCCESS, the n3 green
    ingest, build_jobs) writes it from two facts the status already carries:
    the run succeeded, and its cycle (PRODUCT and STORE run only on the pilot
    cycle). A failed run is located by its failed phase: TESTER on the pilot
    cycle is the PRODUCT gate, STORE_MANAGER is the STORE gate, anything
    earlier is CODE. Anything else is UNKNOWN.
    """
    found = {name: "UNKNOWN" for name in GATE_SCOPES}
    state = str(status.get("state") or "").strip().lower()
    pilot = str(status.get("cycle") or "").strip().lower() == "pilot"
    if state == "succeeded":
        found["CODE"] = "PASS"
        found["PRODUCT"] = found["STORE"] = "PASS" if pilot else "NOT_RUN"
        return found
    failure = status.get("failure")
    phase = str((failure or {}).get("phase") or "").strip().upper() if isinstance(failure, Mapping) else ""
    if state != "failed" or not phase:
        return found
    if phase == BuildRole.STORE_MANAGER.value:
        found.update(CODE="PASS", PRODUCT="PASS" if pilot else "NOT_RUN", STORE="FAIL")
    elif phase == BuildRole.TESTER.value and pilot:
        found.update(CODE="PASS", PRODUCT="FAIL", STORE="NOT_RUN")
    else:
        found.update(CODE="FAIL", PRODUCT="NOT_RUN", STORE="NOT_RUN")
    return found


#: The setting a generated platform would read to reach the Store over HTTP.
STORE_URL_SETTING = "CEREBRUM_API_URL"
#: The TESTER-emitted route test that proves every capability accepts a payload.
ACCEPT_PAYLOAD_TEST = "test_every_capability_route_accepts_payload"


def _handlers_call_the_store(root: Path) -> List[str]:
    actions = root / "app" / "actions"
    if not actions.is_dir():
        return ["app/actions is missing"]
    from app.factory.build.block_inputs import settings_names
    from app.factory.build.network_posture import _code_urls

    hits: List[str] = []
    for path in sorted(actions.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        # Structure, not names in the text: the handler reads the Store's
        # location setting from the environment, or writes an outbound URL
        # as a code constant (the syntax tree -- comments are not calls).
        if STORE_URL_SETTING in settings_names(source) or _code_urls(source):
            hits.append(path.name)
    return hits


def _missing_founding_files(root: Path) -> List[str]:
    missing = [rel for rel in FOUNDING_EXTRA_FILES if not (root / rel).is_file()]
    classes = present_classes(root)
    missing.extend(rel for rel, ok in classes.items() if not ok)
    return missing


def _cli_honesty_miss(status: Mapping[str, Any]) -> Optional[str]:
    """Named CLI miss on coder_receipt. Not founding.

    Read from the receipt's TYPED fields: a receipt that names a blocker (or
    an honesty class) did not come from a clean ≥2h writer session, whatever
    the blocker is called -- so a new named blocker needs no entry here, and
    the prose ``detail`` is never searched. Still Store-green when the three
    gates pass (empty-gap REUSE keep-path after a billing refusal).
    """
    receipt = status.get("coder_receipt")
    if not isinstance(receipt, Mapping):
        return None
    for field in ("blocker", "honesty_class"):
        named = str(receipt.get(field) or "").strip()
        if named:
            return named
    if receipt.get("ok") is False:
        return "FACTORY_CODE_CLI_FAILED"
    return None


def _as_nonneg_int(value: Any) -> Optional[int]:
    """Authorship counts. ``bool`` is excluded (``True`` is an ``int``)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _thin_templated_authorship(status: Mapping[str, Any]) -> bool:
    """Template-majority or near-zero writer (live 1/23 and 8/16).

    Missing authorship is not thin here: workspace file checks can still
    earn founding. When counts are present, a templated majority cannot.
    """
    authorship = status.get("authorship")
    if not isinstance(authorship, Mapping):
        return False
    written = _as_nonneg_int(authorship.get("agent_written"))
    templated = _as_nonneg_int(authorship.get("templated"))
    artifacts = _as_nonneg_int(authorship.get("artifacts"))
    if written is None:
        return False
    if written <= 1:
        return True
    # Judge the CAPABILITY work when it is counted. The whole-artifact
    # comparison below counts the factory's own substrate as "templated"
    # against the agent, and the factory writes more of it every release:
    # FinOps (sess_065fc3eac75c4f62) graded thin at 13 vs 13 while the agent
    # had written all 8 of its handlers. A pass is thin when it authored no
    # handler, or when most handlers are templates -- not when the platform
    # it was given is well equipped.
    action_artifacts = _as_nonneg_int(authorship.get("action_artifacts"))
    agent_actions = _as_nonneg_int(authorship.get("action_py"))
    templated_actions = _as_nonneg_int(authorship.get("templated_actions"))
    if action_artifacts is not None and action_artifacts > 0 and agent_actions is not None:
        if agent_actions == 0:
            return True
        return templated_actions is not None and templated_actions >= agent_actions
    if templated is not None and templated >= written:
        return True
    if artifacts is not None and artifacts > 0 and written * 2 <= artifacts:
        return True
    return False


def _factory_llm_fallthrough(status: Mapping[str, Any]) -> bool:
    """Factory-LLM GENERATE keep-path is not a founding writer session."""
    receipt = status.get("coder_receipt")
    if not isinstance(receipt, Mapping):
        return False
    return receipt.get("factory_llm_generate_fallthrough") is True


def _accept_payload_test_present(root: Path) -> bool:
    routes = root / "tests" / "test_routes.py"
    if not routes.is_file():
        return False
    import ast

    try:
        tree = ast.parse(routes.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == ACCEPT_PAYLOAD_TEST
        for node in tree.body
    )


def grade_workspace(
    root: Path | str,
    *,
    status: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Grade a built product tree. Never upgrades a false ``pilot_ready``.

    ``status`` is the Floor ``build_status`` dict when the caller already
    has it; otherwise the ledger is read. The grade does not re-run gates.
    """
    workspace = Path(root)
    if status is None:
        from app.factory.build_jobs import build_status

        if (workspace / "build_ledger.jsonl").is_file():
            status = build_status(workspace)
        else:
            status = {"state": "unknown", "pilot_ready": False, "detail": ""}

    state = str(status.get("state") or "unknown")
    ready = bool(status.get("pilot_ready"))
    cycle = str(status.get("cycle") or "code")
    gates = three_gate_verdict(status)
    floor = full_pilot_authorship_from(status, workspace)
    missing = _missing_founding_files(workspace) if workspace.is_dir() else list(
        FOUNDING_EXTRA_FILES
    ) + list(FOURTEEN_ARTIFACT_CLASSES)
    store_hits = _handlers_call_the_store(workspace) if workspace.is_dir() else [
        "app/actions is missing"
    ]
    accept = _accept_payload_test_present(workspace) if workspace.is_dir() else False

    blockers: List[str] = []
    if state != "succeeded":
        blockers.append(f"build state is {state}, not succeeded")
    if not ready:
        blockers.append("pilot_ready is false")
    if gates.get("CODE") != "PASS":
        blockers.append(f"CODE gate is {gates.get('CODE')}")
    if gates.get("PRODUCT") != "PASS":
        blockers.append(f"PRODUCT gate is {gates.get('PRODUCT')}")
    if gates.get("STORE") != "PASS":
        blockers.append(f"STORE gate is {gates.get('STORE')}")
    if missing:
        blockers.append("missing founding files: " + ", ".join(missing[:8]))
    if store_hits:
        blockers.append("handlers call the store over HTTP: " + ", ".join(store_hits[:6]))
    if not accept:
        blockers.append("tests/test_routes.py has no accept-payload contract")
    cli_miss = _cli_honesty_miss(status)
    if cli_miss:
        blockers.append(
            f"coder_receipt honesty {cli_miss} is not a founding writer product"
        )
    if _factory_llm_fallthrough(status):
        blockers.append(
            "coder_receipt factory_llm_generate_fallthrough is not a founding writer product"
        )
    if _thin_templated_authorship(status):
        blockers.append(
            "authorship is overwhelmingly templated (near-zero agent_written)"
        )
    if floor.below_floor:
        n_req = (
            f", n_required={floor.n_required}"
            if floor.n_required is not None
            else ""
        )
        blockers.append(
            "authorship is below the full-pilot floor "
            f"(action_py={floor.action_py}, "
            f"cli_authored_ids={len(floor.cli_authored_ids)}, "
            f"need ≥{floor.need}{n_req})"
        )
        # Honesty: a measured thin keep-path cannot stay Store-green.
        ready = False

    from app.factory.build.store_acceptance import (
        ACCEPTANCE_REQUIRED,
        acceptance_is_kk,
        read_acceptance_report,
    )

    acceptance = read_acceptance_report(workspace, status)
    if cycle == "pilot" or (
        gates.get("PRODUCT") == "PASS" and gates.get("STORE") == "PASS"
    ):
        if not acceptance_is_kk(acceptance):
            blockers.append(
                f"acceptance is {acceptance.passed}/{acceptance.total}, not "
                f"k/{ACCEPTANCE_REQUIRED} (authorship floor is not acceptance)"
            )
            ready = False

    if ready and not blockers:
        level = Level.FOUNDING_CUSTOMER_READY
    elif ready and gates.get("PRODUCT") == "PASS" and gates.get("STORE") == "PASS":
        level = Level.STORE_GREEN
    elif (
        state == "succeeded"
        and gates.get("CODE") == "PASS"
        and not ready
    ):
        level = Level.CODE_GREEN
    else:
        level = Level.SCAFFOLD

    # Honesty lock: a false pilot_ready can never read as Store-green.
    if not ready and level in {Level.STORE_GREEN, Level.FOUNDING_CUSTOMER_READY}:
        level = Level.CODE_GREEN if gates.get("CODE") == "PASS" else Level.SCAFFOLD
        blockers.append("honesty lock: pilot_ready false cannot grade Store-green")

    full_pilot = (
        level in {Level.STORE_GREEN, Level.FOUNDING_CUSTOMER_READY}
        and ready
        and floor.meets_floor
    )
    return {
        "emitter": GRADE_EMITTER,
        "level": level.value,
        "pilot_ready": ready,
        "full_pilot": full_pilot,
        "action_py": floor.action_py,
        "cli_authored_ids": list(floor.cli_authored_ids),
        "cycle": cycle,
        "state": state,
        "three_gate": gates,
        "gate_scopes": dict(GATE_SCOPES),
        "missing": missing,
        "blockers": blockers,
        "founding_customer_ready": level is Level.FOUNDING_CUSTOMER_READY,
        "acceptance": acceptance.to_json(),
    }


def attach_level_grade(status: Dict[str, Any], root: Path | str) -> Dict[str, Any]:
    """Stamp ``level_grade`` onto a build_status dict. Fail-closed on error."""
    try:
        grade = grade_workspace(root, status=status)
        status["level_grade"] = grade
        if isinstance(grade.get("acceptance"), dict):
            status["acceptance"] = grade["acceptance"]
        # Floor / package read status.pilot_ready. A thin keep-path that
        # demoted the grade must not keep a Store-green ledger bit.
        # Acceptance k/k is a separate Export / Store-green lock — do not
        # rewrite the ledger bit just because the harness has not run.
        blockers = [str(b) for b in (grade.get("blockers") or [])]
        authorship_demote = any(
            "full-pilot floor" in b or "overwhelmingly templated" in b for b in blockers
        )
        if grade.get("pilot_ready") is False and authorship_demote:
            status["pilot_ready"] = False
    except Exception as exc:  # noqa: BLE001 — a grade fault must not 500 status
        status["level_grade"] = {
            "emitter": GRADE_EMITTER,
            "level": Level.SCAFFOLD.value,
            "pilot_ready": False,
            "full_pilot": False,
            "founding_customer_ready": False,
            "blockers": [f"level grade could not run: {type(exc).__name__}: {exc}"],
        }
        status["pilot_ready"] = False
    return status
