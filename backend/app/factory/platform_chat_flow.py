"""Chat-driven platform creation flow.

Bridges free-text chat messages onto the EXISTING session product state
machine (routers/session_product.py). No parallel machinery: the same
draft_blueprint_from_brief / plan_blueprint / generate_product functions,
the same ProductDesignState on the session. The chat is simply a second
front door onto the same house.

Routing contract (this is law, the smoke tests enforce it):
    1. Explicit commands ALWAYS enter the platform flow:
     "/platform <brief>", "new platform <brief>", "platform: <brief>".
    2. Free-text NLP intent ("build me a platform for hotels") enters the
     platform flow by DEFAULT — factory doctrine: the chat's purpose is
     building platforms. Set PLATFORM_CHAT_FLOW_ENABLED=off to keep the
     legacy kit-configurator routing for unauthenticated deployments.
    3. When a factory LLM key is configured, the chat LLM decides the action
     (draft / start_coder / reply). start_coder is the only chat door that
     launches the WRITER coding agent. Regex approval ("approve") is the
     offline fallback when the LLM is unset or the call fails.
    4. Approval starts WRITER when a blueprint is pending. continue/resume
     resumes an in-flight or interrupted (non-terminal) run even after the
     blueprint is already approved — a pending unapproved blueprint is not
     required. After code-phase 5/5 SUCCESS, continue opens a pilot cycle
     on the same workspace (pytest -m pilot + STORE ops), not a new product.
     A RUN_FAILED / rework-exhausted ledger is terminal: same-hash continue
     or a new brief must start a fresh workspace (reset rework budget),
     never a no-op resume of the dead run. ``HANDOFF_TO_N3`` is
     also not a coding-failure terminal: continue / start_coder ingests
     the cerebrum-builds ``store-gate`` 12/12 status and must not
     re-enter WRITER or launch another Background Agent.
    5. Kit-configurator vocabulary (chain/blocks/kits/domain/lora/...) stays
     in the legacy chat flow even when it also mentions a platform noun.
    6. Anything else falls through to the normal kit-configurator chat.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

from .blueprint import CapabilitySpec, ProductBlueprint
from .dual_registry import dual_registered_ids
from .floor_actions import (
    REFINEMENT_ACTIONS,
    VALUE_REQUIRED,
    FloorAction,
    FloorActionError,
    parse_level,
)
from .locale_choice import confirm_intake, intake_state, sync_blueprint_intake
from .blocks_source import resolve_blocks_root
from .paths import factory_outputs_root
from .product_architect import (
    blueprint_to_yaml,
    draft_blueprint_from_brief,
    generate_product,
    plan_blueprint,
    session_domain_from_blueprint,
)
from .build.coder_session import NAMED_BLOCKER_CLI, CodeCliUnavailable
from .build.platform_identity import ensure_platform_id, platform_id_of

# --- Routing ---------------------------------------------------------------
#
# The Floor never decides what the user wants from the words they typed.
# Actions arrive typed (app.factory.floor_actions); free text goes to the
# Floor chat LLM, whose decision is typed too. The regexes that used to
# detect "a platform brief", "approve", "continue", "run the pilot" in the
# message are gone.


def platform_chat_enabled() -> bool:
    """Free-text NLP interception gate.

    Default ON (factory doctrine: the chat's purpose is building platforms).
    Set PLATFORM_CHAT_FLOW_ENABLED to 0/false/no/off to force the legacy
    kit-configurator routing.
    """
    return os.getenv("PLATFORM_CHAT_FLOW_ENABLED", "true").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


# --- State helpers ----------------------------------------------------------

def has_pending_blueprint(state: Any) -> bool:
    pd = getattr(state, "product_design", None)
    return bool(pd and pd.blueprint and not pd.blueprint_approved)


def _blocks_root() -> Optional[Path]:
    """Thin alias for the shared resolver (kept so existing monkeypatches and
    call sites stay valid). See app.factory.blocks_source for the fix history:
    every generation door — chat AND the HTTP plan/generate routes — must use
    the same resolution or products differ in fidelity by entry point."""
    return resolve_blocks_root()


def _session_output(session_id: str, product_id: str, output_root: Optional[Path]) -> Path:
    if output_root is not None:
        return Path(output_root) / product_id
    return factory_outputs_root() / "sessions" / session_id / product_id


# --- Refinement commands ------------------------------------------------------

def _capability_for_id(cap_id: str, dual_ids: List[str]) -> Dict[str, Any]:
    """Build a minimal capability spec for a user-added capability id."""
    cap_id = re.sub(r"[^a-z0-9_]+", "_", cap_id.lower()).strip("_")
    human = cap_id.replace("_", " ").title()
    if cap_id in dual_ids:
        return {
            "id": cap_id,
            "description": f"{human} capability (reused from the block store)",
            "block_ids": [cap_id],
            "strategy_hint": "REUSE",
            "required": True,
        }
    return {
        "id": cap_id,
        "description": f"{human} capability — generated scaffolding, extend via Factory templates",
        "block_ids": [],
        "strategy_hint": "GENERATE",
        "required": True,
    }


#: Result ``action`` names the Floor and tests read (unchanged wire names).
_RESULT_ACTION = {
    FloorAction.ADD_CAPABILITY: "add_capability",
    FloorAction.REMOVE_CAPABILITY: "remove_capability",
    FloorAction.RENAME: "rename_product",
    FloorAction.SET_VERTICAL: "set_vertical",
    FloorAction.LIST_CAPABILITIES: "list_capabilities",
}


def _level_bar_sentence(level: str) -> str:
    """What the chosen level asks of the build, read from its bar."""
    from app.factory.build.build_level import BARS, BuildLevel

    bar = BARS[BuildLevel(level)]
    if not bar.reaches_pilot:
        return (
            f"{level} — the build is done when the code passes (CODE_GREEN); "
            "the pilot suite and the Store gate do not run."
        )
    reach = "the build climbs to the Store gate (pilot suite, then acceptance)"
    thin = (
        "; a templates-only pass counts as a failure"
        if bar.thin_success_is_failure
        else "; a pass on Factory-template handlers still counts"
    )
    floor = (
        "; every acceptance check is enforced, the security scan included."
        if bar.full_floor
        else "; the security scan is advisory, every other check enforced."
    )
    return f"{level} — {reach}{thin}{floor}"


def apply_intake_action(
    state: Any, action: FloorAction, value: Optional[str] = None
) -> Dict[str, Any]:
    """The user's TYPED intake on the Confirm / Change line. ``set_build_level``
    (Change) puts the chosen level on the proposal; only ``confirm_intake``
    (Confirm) stores the proposal as the session's typed fields. Works before
    any blueprint exists. Refused once the blueprint is approved: the level is
    part of the frozen blueprint hashed at approval, never added afterwards."""
    pd = state.product_design
    reopened = bool(getattr(pd, "intake_reopened", False)) and not has_running_build(state)
    if (pd.blueprint_approved and not reopened) or has_running_build(state):
        return {
            "sse": "info",
            "ok": False,
            "summary": (
                "The blueprint is approved: its intake, build level included, "
                "is frozen with it. Draft a new platform to build at another level."
            ),
            "intake": intake_state(pd),
        }
    if action is FloorAction.SET_BUILD_LEVEL:
        try:
            level = parse_level(value).value
        except FloorActionError as exc:
            return {"sse": "info", "ok": False, "summary": str(exc), "intake": intake_state(pd)}
        pd.intake_proposal = {**(pd.intake_proposal or {}), "build_level": level}
        summary = (
            "Proposed build level " + _level_bar_sentence(level)
            + " Press Confirm to use it."
        )
    elif action is FloorAction.CONFIRM_INTAKE:
        stored = confirm_intake(pd)
        if not stored:
            return {
                "sse": "info",
                "ok": False,
                "summary": "There is no proposed intake to confirm.",
                "intake": intake_state(pd),
            }
        parts = [f"{k.replace('_', ' ')} {v}" for k, v in stored.items()]
        summary = "Confirmed: " + ", ".join(parts) + "."
        if stored.get("build_level"):
            summary += " Build level " + _level_bar_sentence(stored["build_level"])
    else:
        raise FloorActionError(f"{action.value} is not an intake action")
    result: Dict[str, Any] = {
        "sse": "info",
        "ok": True,
        "summary": summary,
        "intake": intake_state(pd),
    }
    if pd.blueprint:
        result["blueprint"] = pd.blueprint
    return result


def apply_refinement(
    state: Any, action: FloorAction, value: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Apply a TYPED refinement (a Floor control, or the chat LLM's typed
    decision) to the pending blueprint and return a summary.

    Returns None when there is no pending blueprint to refine. Nothing here
    reads the user's words: the action and its value arrive as fields.
    """
    pd = getattr(state, "product_design", None)
    if not pd or not pd.blueprint:
        return None
    if pd.blueprint_approved:
        return None
    if action not in REFINEMENT_ACTIONS:
        raise FloorActionError(f"{action.value} is not a blueprint refinement")
    value = (value or "").strip()
    if action in VALUE_REQUIRED and not value:
        return {
            "ok": False,
            "refined": False,
            "action": _RESULT_ACTION[action],
            "summary": f"'{action.value}' needs a value.",
            "blueprint": pd.blueprint,
        }
    args: Dict[str, Any] = {}
    if action in (FloorAction.ADD_CAPABILITY, FloorAction.REMOVE_CAPABILITY):
        args["cap_id"] = value
    elif action is FloorAction.RENAME:
        args["name"] = value
    elif action is FloorAction.SET_VERTICAL:
        args["vertical"] = value
    action = _RESULT_ACTION[action]  # the body below speaks the wire names

    sync_blueprint_intake(pd)  # the user's declared locale and build level, never a guess
    bp = ProductBlueprint.model_validate(pd.blueprint)
    caps = [c.model_dump(mode="json") for c in bp.capabilities]
    cap_ids = {c["id"] for c in caps}

    if action == "add_capability":
        cap_id = args["cap_id"]
        if cap_id in cap_ids:
            return {
                "ok": True,
                "refined": True,
                "action": action,
                "summary": f"Capability '{cap_id}' is already in the blueprint.",
                "blueprint": pd.blueprint,
                "yaml": blueprint_to_yaml(bp),
            }
        dual = sorted(dual_registered_ids())
        caps.append(_capability_for_id(cap_id, dual))

    elif action == "remove_capability":
        cap_id = args["cap_id"]
        new_caps = [c for c in caps if c["id"] != cap_id]
        if len(new_caps) == len(caps):
            return {
                "ok": True,
                "refined": True,
                "action": action,
                "summary": f"Capability '{cap_id}' was not found in the blueprint.",
                "blueprint": pd.blueprint,
                "yaml": blueprint_to_yaml(bp),
            }
        caps = new_caps
        if not caps:
            return {
                "ok": False,
                "refined": False,
                "action": action,
                "summary": "Cannot remove the last capability — a blueprint needs at least one.",
                "blueprint": pd.blueprint,
            }

    elif action == "rename_product":
        bp.product_name = args["name"][:120]
        pd.blueprint = bp.model_dump(mode="json")
        return {
            "ok": True,
            "refined": True,
            "action": action,
            "summary": f"Product renamed to '{bp.product_name}'.",
            "blueprint": pd.blueprint,
            "yaml": blueprint_to_yaml(bp),
        }

    elif action == "set_vertical":
        # The user TYPED the vertical (an explicit command, not inference):
        # it becomes the session's choice, exactly like the Floor picker.
        from app.factory.store_kits import chosen_vertical

        vertical = chosen_vertical(args["vertical"])
        pd.vertical = vertical
        bp.vertical = vertical
        bp.product_id = vertical
        pd.blueprint = bp.model_dump(mode="json")
        state.config.domain = session_domain_from_blueprint(bp)
        return {
            "ok": True,
            "refined": True,
            "action": action,
            "summary": f"Vertical set to '{vertical}'.",
            "blueprint": pd.blueprint,
            "yaml": blueprint_to_yaml(bp),
        }

    elif action == "list_capabilities":
        lines = [f"- {c['id']} ({c.get('strategy_hint','?')})" for c in caps]
        return {
            "ok": True,
            "refined": False,
            "action": action,
            "summary": f"Blueprint '{bp.product_name}' has {len(caps)} capabilities:\n" + "\n".join(lines),
            "blueprint": pd.blueprint,
            "yaml": blueprint_to_yaml(bp),
        }

    # Rebuild blueprint after add/remove
    bp.capabilities = [CapabilitySpec.model_validate(c) for c in caps]
    pd.blueprint = bp.model_dump(mode="json")
    pd.plan = None  # force re-plan after change
    pd.generation = None
    state.config.domain = session_domain_from_blueprint(bp)
    yaml_text = blueprint_to_yaml(bp)
    return {
        "ok": True,
        "refined": True,
        "action": action,
        "summary": (
            f"Blueprint updated: {len(bp.capabilities)} capabilities. "
            "Approve the feature list to build, or keep refining."
        ),
        "blueprint": pd.blueprint,
        "yaml": yaml_text,
    }


# --- Flow actions ------------------------------------------------------------

def draft_from_chat(state: Any, message: str) -> Dict[str, Any]:
    """Draft a blueprint from a chat brief and park it on the session.

    Mutates state.product_design exactly like POST /product/draft.
    Returns the summary payload streamed back to the chat.
    """
    pd = state.product_design
    # The vertical is the user's own choice from the Floor (persisted on the
    # session), never read out of the message.
    bp = draft_blueprint_from_brief(message, vertical_hint=getattr(pd, "vertical", None))
    pd.brief = message
    pd.blueprint = bp.model_dump(mode="json")
    pd.plan = None
    pd.blueprint_approved = False
    pd.generation = None
    pd.last_error = None
    pd.mode = "product"
    state.config.domain = session_domain_from_blueprint(bp)

    from app.factory.build.intake_blueprint import (
        chat_turns_from_session,
        intake_from_product_blueprint,
        render_plain_language,
    )
    from app.factory.build.brief_compiler import synthesize_domain_pack

    turns = chat_turns_from_session(state)
    if not turns and message.strip():
        turns = [{"turn": 1, "role": "user", "text": message.strip()}]
    try:
        intake = intake_from_product_blueprint(
            bp,
            plan=None,
            chat_turns=turns,
            brief=message,
            domain_pack=synthesize_domain_pack(bp, type("P", (), {"capabilities": bp.capabilities})()),
        )
        pd.intake_blueprint = intake
        plain = render_plain_language(intake)
    except Exception:  # noqa: BLE001 — draft must still park the product blueprint
        pd.intake_blueprint = None
        plain = ""

    capabilities = [c.id for c in bp.capabilities]
    blocks = sorted({b for c in bp.capabilities for b in c.block_ids})
    # The blueprint says how it was drafted.
    source = "golden" if bp.drafting_mode == "golden" else "drafted"
    # Say who drafted it. A dead LLM key must not look identical to a
    # working architect.
    mode_labels = {
        "architect_llm": "Drafted by the architect LLM.",
        "golden": "Drafted from a golden blueprint (chosen by structure).",
        "keyword_fallback": "Drafted by deterministic templates (no LLM).",
    }
    mode_line = mode_labels.get(bp.drafting_mode or "", "")
    if bp.drafting_note:
        mode_line = (mode_line + " " + bp.drafting_note + ".").strip()
    summary = (
        f"Blueprint drafted: {bp.product_name} ({bp.vertical}). "
        f"{len(capabilities)} capabilities, {len(blocks)} blocks. "
        + (mode_line + " " if mode_line else "")
        + "Press Approve & build to generate the product, or refine your brief."
    )
    return {
        "ok": True,
        "source": source,
        "drafting_mode": bp.drafting_mode,
        "drafting_note": bp.drafting_note,
        "blueprint": pd.blueprint,
        "yaml": blueprint_to_yaml(bp),
        "summary": summary,
        "intake_blueprint": pd.intake_blueprint,
        "plain_language": plain,
    }


def _compile_and_lint_approved(state: Any, bp: ProductBlueprint) -> Dict[str, Any]:
    """Approve is the only Floor event that opens compile → lint → session.

    Spend-gated by construction: a rejected brief never starts generate.
    """
    from app.factory.build.brief_compiler import compile_brief, verify_inventory
    from app.factory.build.brief_lint import lint_brief
    from app.factory.build.intake_blueprint import (
        chat_turns_from_session,
        render_plain_language,
    )

    plan = plan_blueprint(bp, blocks_root=_blocks_root())
    turns = chat_turns_from_session(state)
    compiled = compile_brief(
        bp,
        plan,
        blocks_root=_blocks_root(),
        chat_turns=turns,
        brief=str(getattr(state.product_design, "brief", "") or ""),
        intake=getattr(state.product_design, "intake_blueprint", None),
    )
    from app.factory.build.brief_compiler import InventoryHalt

    try:
        verify_inventory(compiled)
    except InventoryHalt as exc:
        pd = state.product_design
        pd.intake_blueprint = compiled.intake
        pd.brief_lint = {
            "ok": False,
            "errors": [str(exc)],
            "checks": {"inventory_halt": True},
        }
        pd.plan = plan.to_dict()
        class _Halt:
            ok = False
            errors = [str(exc)]

            def to_dict(self):
                return pd.brief_lint

        return {
            "compiled": compiled,
            "lint": _Halt(),
            "plan": plan,
            "plain_language": render_plain_language(compiled.intake),
        }
    lint = lint_brief(compiled)
    pd = state.product_design
    pd.intake_blueprint = compiled.intake
    pd.brief_lint = lint.to_dict()
    pd.plan = plan.to_dict()
    return {
        "compiled": compiled,
        "lint": lint,
        "plan": plan,
        "plain_language": render_plain_language(compiled.intake),
    }


def _cli_unavailable_reply(pd: Any, exc: BaseException) -> Dict[str, Any]:
    """Fail-closed named class — coding session never opened, no takeover."""
    detail = str(exc)
    blocker = getattr(exc, "blocker", NAMED_BLOCKER_CLI)
    pd.last_error = detail
    logger.error("%s", detail)
    return {
        "ok": False,
        "sse": "error",
        "blocker": blocker,
        "summary": (
            f"{blocker} — coding session never opened. {detail}"
        ),
        "blueprint_approved": bool(getattr(pd, "blueprint_approved", False)),
        "stream_delta": False,
    }


def approve_and_generate(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "regex_approve",
) -> Dict[str, Any]:
    """Approve the pending blueprint, compile+lint the brief, then generate.

    Mutates state.product_design exactly like POST /product/approve +
    /product/generate. Returns the generation payload.

    ``triggered_by`` is provenance for the chat door: ``chat_llm`` when the
    Floor LLM called start_coder, ``regex_approve`` when the offline keyword
    path ran. The coding agent still lives only in WRITER.

    Approve is the only event that opens compile → lint → coder session.
    A BRIEF_LINT_REJECTED brief never starts generate.
    """
    pd = state.product_design
    if not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")

    sync_blueprint_intake(pd)  # the user's declared locale and build level, never a guess
    bp = ProductBlueprint.model_validate(pd.blueprint)
    was_approved = bool(pd.blueprint_approved)
    pd.blueprint_approved = True
    gated = _compile_and_lint_approved(state, bp)
    lint = gated["lint"]
    if not lint.ok:
        pd.last_error = "BRIEF_LINT_REJECTED: " + "; ".join(lint.errors)
        # No build started: the feature list stays approvable, never frozen
        # behind a run that does not exist.
        pd.blueprint_approved = was_approved
        return {
            "ok": False,
            "sse": "error",
            "summary": (
                "Brief rejected — coding session never opened. "
                + pd.last_error
            ),
            "brief_lint": lint.to_dict(),
            "plain_language": gated.get("plain_language"),
            "blueprint_approved": bool(pd.blueprint_approved),
        }
    if not pd.plan:
        pd.plan = gated["plan"].to_dict()

    out = _session_output(state.session_id, bp.product_id, output_root)
    if has_running_build(state) or _live_build_thread(out) is not None:
        if not pd.generation:
            # Nothing of THIS session's is running or recorded: an approve
            # that starts nothing must not leave "approved, no generation".
            pd.blueprint_approved = was_approved
        reply = running_build_reply(state)
        reply["already_running"] = True
        return reply

    try:
        result = generate_product(
            bp,
            out,
            blocks_root=_blocks_root(),
            quota_account_id=getattr(state, "user_id", None),
            tenant_identity=getattr(state, "user_id", None),
            brief=str(getattr(getattr(state, "product_design", None), "brief", "") or "").strip(),
            platform_id=ensure_platform_id(state.product_design),
        )
    except CodeCliUnavailable as exc:
        pd.blueprint_approved = was_approved
        return _cli_unavailable_reply(pd, exc)
    if result.get("already_running"):
        _record_generation(pd, result, triggered_by=triggered_by, resumed=False)
        reply = running_build_reply(state)
        reply["already_running"] = True
        return reply
    _record_generation(pd, result, triggered_by=triggered_by, resumed=False)
    pd.last_error = None

    trigger_line = (
        "The chat LLM started the coding agent. "
        if triggered_by == "chat_llm"
        else ""
    )

    # A runner build has STARTED, not finished. Saying "product generated —
    # download it" here would be a lie the customer discovers as a 409 on the
    # download, so the runner engine gets its own honest message.
    if result.get("engine") == "runner":
        caps = len((pd.plan or {}).get("capabilities", []) or [])
        from app.factory.build.build_level import start_expectation

        expect = start_expectation(bp)
        takeover = (
            f"{trigger_line}Build started for {result['product_id']}: the coding agent "
            "has taken over the floor and is "
            f"writing {caps} capability(ies) against the real block contracts. "
        )
        return {
            "ok": True,
            "generation": pd.generation,
            "plan": pd.plan,
            "triggered_by": triggered_by,
            "summary": takeover + expect,
        }

    # Say what generation actually is: deterministic composition of prebuilt
    # blocks (plus LLM-written handlers for GENERATE capabilities when the
    # coder runs). "Generated" alone oversold this as bespoke code.
    strategies = [c.get("strategy") for c in (pd.plan or {}).get("capabilities", [])]
    reuse_n = sum(1 for s in strategies if s == "REUSE")
    composition = f"composed from {reuse_n} prebuilt block capabilities"
    coder = result.get("coder") or {}
    if coder.get("written"):
        composition += f" + {len(coder['written'])} coder-written"
    summary = (
        f"Product generated: {result['product_id']} ({composition}). "
        f"Download it from Your Platforms (product package) or keep refining."
    )
    stubbed = coder.get("stubbed") or {}
    if stubbed:
        # Degraded output is fine; invisible degradation is not.
        names = ", ".join(sorted(stubbed))
        reason = next(iter(stubbed.values()))
        summary += (
            f" Note: {len(stubbed)} capability(ies) shipped as honest stubs "
            f"— the coder could not write them ({names}: {reason})."
        )
    return {
        "ok": True,
        "generation": pd.generation,
        "plan": pd.plan,
        "triggered_by": triggered_by,
        "summary": trigger_line + summary,
    }


def has_running_build(state: Any) -> bool:
    """True while the coding agent is manufacturing the approved feature list."""
    pd = getattr(state, "product_design", None)
    gen = getattr(pd, "generation", None) if pd else None
    if not gen or gen.get("engine") != "runner":
        return False
    out = gen.get("output_dir")
    if not out:
        return False
    from app.factory.build_jobs import build_status

    st = build_status(out)
    # HANDOFF_TO_N3 keeps state=building while N3 polls store-gate. That is
    # not a live WRITER — Continue / n3-reseed must not be 409'd as "already
    # in progress" (sess_1ef39fcba8f54dbc AirOps).
    if st.get("n3_waiting") or st.get("honesty") == "HANDOFF_TO_N3" or st.get("outcome") == "HANDOFF_TO_N3":
        return False
    return st.get("state") == "building"


def running_build_reply(state: Any) -> Dict[str, Any]:
    """Grounded status while the coding agent owns the floor."""
    pd = state.product_design
    gen = pd.generation or {}
    from app.factory.build_jobs import build_status

    st = build_status(gen.get("output_dir") or "")
    activity = st.get("activity") or "writing the platform"
    done = st.get("phases_done") or 0
    total = st.get("phases_total") or 5
    summary = (
        f"The coding agent has taken over. It is writing {gen.get('product_id')} "
        f"— {done}/{total} phases ({activity}). Confirmations are already in: "
        "wait for the gates, or watch progress on Your Platforms."
    )
    return {
        "ok": True,
        "sse": "info",
        "summary": summary,
        "stream_delta": True,
        "build": st,
    }


# --- Resume an in-flight or interrupted coding run --------------------------


def _generation_output_dir(state: Any, output_root: Optional[Path] = None) -> Optional[Path]:
    """Where this session's coding run writes. Survives a worker restart."""
    pd = getattr(state, "product_design", None)
    if not pd:
        return None
    gen = getattr(pd, "generation", None) or {}
    out = gen.get("output_dir")
    if out:
        return Path(out)
    bp = pd.blueprint or {}
    product_id = gen.get("product_id") or bp.get("product_id")
    session_id = getattr(state, "session_id", None)
    if product_id and session_id:
        return _session_output(session_id, product_id, output_root)
    return None


def _ledger_for(output_dir: Path):
    from app.factory.build.ledger import BuildLedger

    return BuildLedger(Path(output_dir) / "build_ledger.jsonl")


def _live_build_thread(output_dir: Path | str):
    """The in-process runner thread of the build at ``output_dir``, if any.

    Keyed by the build's workspace of record -- never by product id, which
    two tenants share whenever neither declared a vertical ("product").
    After a deploy / disconnect the thread is gone even while the ledger
    still reads "building". That is the case continue must restart.
    """
    from app.factory.build_jobs import live_runner_thread

    return live_runner_thread(output_dir)


def _generation_status(state: Any, output_root: Optional[Path] = None) -> Dict[str, Any]:
    """Live ledger status, falling back to the persisted generation snapshot."""
    pd = getattr(state, "product_design", None)
    gen = getattr(pd, "generation", None) if pd else None
    if not gen:
        return {}
    out = _generation_output_dir(state, output_root)
    if out:
        from app.factory.build_jobs import build_status

        raw_bp = getattr(pd, "blueprint", None) if pd is not None else None
        plan = getattr(pd, "plan", None) if pd is not None else None
        blueprint: Any = raw_bp
        if isinstance(raw_bp, dict) and raw_bp:
            try:
                from app.factory.blueprint import ProductBlueprint

                blueprint = ProductBlueprint.model_validate(raw_bp)
            except Exception:  # noqa: BLE001 — dict still has capabilities
                blueprint = raw_bp
        st = build_status(out, blueprint=blueprint, plan=plan)
        if st.get("state") != "unknown":
            return st
    persisted = gen.get("build")
    return dict(persisted) if isinstance(persisted, dict) else {}


def _ledger_resume_point(state: Any, output_root: Optional[Path] = None) -> Optional[str]:
    out = _generation_output_dir(state, output_root)
    if not out:
        return None
    try:
        ledger = _ledger_for(out)
        if not ledger.exists():
            return None
        point = ledger.resume_point()
        return point.value if point else None
    except Exception:  # noqa: BLE001 — a torn ledger must not block chat
        return None


def _artifacts_remain(status: Dict[str, Any]) -> bool:
    """True when WRITER progress is incomplete (the live 22/28 case)."""
    done = status.get("activity_done")
    total = status.get("activity_total")
    try:
        if total is not None and done is not None and int(done) < int(total):
            return True
    except (TypeError, ValueError):
        pass
    auth = status.get("authorship") or {}
    artifacts = auth.get("artifacts") or 0
    written = auth.get("agent_written") or 0
    return bool(artifacts) and written < artifacts


def is_generation_complete(state: Any) -> bool:
    """True only when the last coding run recorded SUCCESS."""
    st = _generation_status(state)
    if st.get("state") == "succeeded":
        return True
    pd = getattr(state, "product_design", None)
    gen = getattr(pd, "generation", None) if pd else None
    if not gen:
        return False
    out = _generation_output_dir(state)
    if not out:
        return False
    try:
        return bool(_ledger_for(out).succeeded())
    except Exception:  # noqa: BLE001
        return False


def is_handoff_awaiting_n3(
    state: Any, output_root: Optional[Path] = None
) -> bool:
    """True when cli-pivot handed off and N3 store-gate is not yet ingested."""
    out = _generation_output_dir(state, output_root)
    if not out:
        return False
    try:
        from app.factory.build.n3_store_gate import handoff_awaiting_n3

        return bool(handoff_awaiting_n3(out))
    except Exception:  # noqa: BLE001
        return False


def is_generation_terminal_failure(
    state: Any, output_root: Optional[Path] = None
) -> bool:
    """True when the last coding run recorded RUN_FAILED (incl. rework exhausted).

    A terminal failure is not an interrupted run. Resume would attach to a
    dead ledger (TESTER still red, rework spent) and the Floor would stay
    CODING AGENT STOPPED. Callers must start a fresh workspace instead.

    ``HANDOFF_TO_N3`` is a ledger note, not this terminal — Continue must
    ingest store-gate, not start a fresh WRITER.
    """
    if is_handoff_awaiting_n3(state, output_root):
        return False
    st = _generation_status(state, output_root)
    if st.get("state") == "failed":
        return True
    out = _generation_output_dir(state, output_root)
    if not out:
        return False
    try:
        from app.factory.build.ledger import EventKind

        event = _ledger_for(out).terminal_event()
        return event is not None and event.kind is EventKind.RUN_FAILED
    except Exception:  # noqa: BLE001
        return False


def is_generation_resumable(state: Any) -> bool:
    """True when a coding run exists that is interrupted, not finished.

    Does not require a pending (unapproved) blueprint. After takeover the
    blueprint is approved and the runner workspace / ledger is the resume
    source — the same-hash path ``POST .../product/generate`` already uses.

    A RUN_FAILED / rework-exhausted ledger is terminal, not resumable.
    ``HANDOFF_TO_N3`` is resumable via Continue → store-gate ingest
    (not WRITER).
    """
    pd = getattr(state, "product_design", None)
    if not pd or not getattr(pd, "blueprint", None):
        return False
    if is_handoff_awaiting_n3(state):
        return True
    if is_generation_complete(state):
        return False
    if is_generation_terminal_failure(state):
        return False
    gen = getattr(pd, "generation", None) or {}
    if not gen:
        return False
    out = _generation_output_dir(state)
    if out:
        try:
            ledger = _ledger_for(out)
            if ledger.exists() and not ledger.succeeded():
                from app.factory.build.ledger import EventKind

                term = ledger.terminal_event()
                if term is not None and term.kind is EventKind.RUN_FAILED:
                    return False
                return True
        except Exception:  # noqa: BLE001
            pass
        if (Path(out) / "build_ledger.jsonl").is_file():
            return not is_generation_terminal_failure(state)
        if Path(out).is_dir() and any(Path(out).iterdir()):
            return True
    st = _generation_status(state)
    if st.get("state") in {"building", "stalled"}:
        return True
    if _artifacts_remain(st):
        return True
    return bool(gen.get("output_dir") or gen.get("inputs_hash"))


def _record_generation(
    pd: Any,
    result: Dict[str, Any],
    *,
    triggered_by: str,
    resumed: bool,
) -> None:
    """Persist enough for a new uvicorn worker to resume the same run."""
    from app.factory.build_jobs import build_status

    out = result.get("output_dir") or ""
    st = result.get("build") if isinstance(result.get("build"), dict) else None
    if out and (st is None or st.get("state") == "unknown"):
        st = build_status(
            out,
            blueprint=getattr(pd, "blueprint", None),
            plan=getattr(pd, "plan", None),
        )
    st = st or {}
    resume_point = None
    if out:
        try:
            point = _ledger_for(Path(out)).resume_point()
            resume_point = point.value if point else None
        except Exception:  # noqa: BLE001
            resume_point = None
    pd.generation = {
        "output_dir": result.get("output_dir"),
        "inputs_hash": result.get("inputs_hash"),
        "product_id": result.get("product_id"),
        "coder": result.get("coder"),
        "engine": result.get("engine"),
        "build": st,
        "phases_done": st.get("phases_done"),
        "resume_point": resume_point,
        "triggered_by": triggered_by,
        "resumed": resumed,
    }


def _resume_cycle(state: Any, output_root: Optional[Path] = None) -> str:
    """Resume a failed/open pilot as pilot, not as a fresh code cycle."""
    out = _generation_output_dir(state, output_root)
    if not out:
        return "code"
    try:
        ledger = _ledger_for(out)
        if ledger.pilot_ready():
            return "code"
        if ledger.pilot_cycle_open():
            return "pilot"
        from app.factory.build.ledger import EventKind

        if any(e.kind is EventKind.PILOT_OPENED for e in ledger.events()):
            return "pilot"
    except Exception:  # noqa: BLE001
        return "code"
    return "code"


def is_pilot_ready(state: Any, output_root: Optional[Path] = None) -> bool:
    """True only after a Store-green SUCCESS with acceptance k/k.

    Ledger ``pilot_ready`` (pre-#395 authorship-green) is not enough.
    Floor continue / Approve must be allowed to reopen Writer + Store so
    a new cycle can stamp ``scripts/acceptance.py`` and measure k/12.
    """
    out = _generation_output_dir(state, output_root)
    if not out:
        return False
    try:
        if not bool(_ledger_for(out).pilot_ready()):
            return False
    except Exception:  # noqa: BLE001
        return False
    from app.factory.build.store_acceptance import workspace_acceptance_is_kk

    return workspace_acceptance_is_kk(out)


def already_complete_reply(state: Any) -> Dict[str, Any]:
    """Honest answer when continue is typed after a finished run.

    Pilot-ready (Store-green) is the only terminal that refuses another
    coding run. Code-phase 5/5 still has a pilot cycle to open — callers
    should use ``resume_pilot_cycle`` instead of this reply.
    """
    pd = state.product_design
    gen = pd.generation or {}
    product = gen.get("product_id") or (pd.blueprint or {}).get("product_name") or "this product"
    st = _generation_status(state)
    done = st.get("phases_done")
    total = st.get("phases_total")
    phase = f" ({done}/{total} phases)" if done is not None and total is not None else ""
    if is_pilot_ready(state):
        summary = (
            f"{product} is already pilot-ready (Store-green, acceptance k/k)"
            f"{phase}. I did not start a new coding run. Download it from Your Platforms."
        )
        return {
            "ok": True,
            "sse": "info",
            "summary": summary,
            "stream_delta": True,
            "already_complete": True,
            "pilot_ready": True,
            "generation": gen,
            "build": st,
        }
    summary = (
        f"{product} finished the code-phase 5/5{phase}. "
        "It is not yet pilot-ready (pytest -m pilot / Store ops). "
        "Say continue to open a pilot cycle on the same workspace."
    )
    return {
        "ok": True,
        "sse": "info",
        "summary": summary,
        "stream_delta": True,
        "already_complete": True,
        "pilot_ready": False,
        "generation": gen,
        "build": st,
    }


def ingest_n3_store_gate_reply(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "regex_n3",
) -> Dict[str, Any]:
    """Continue-as-ingest: poll/fetch store-gate. Never re-enters WRITER."""
    from app.factory.build.n3_store_gate import (
        N3_STORE_GATE_FACTORY_OWED,
        ingest_n3_store_gate,
        n3_ingest_live,
        start_n3_ingest_job,
    )
    from app.factory.build_jobs import build_status

    pd = state.product_design
    out = _generation_output_dir(state, output_root)
    if out is None:
        return {
            "ok": False,
            "sse": "info",
            "summary": (
                "HANDOFF_TO_N3 is recorded but there is no workspace to "
                "ingest store-gate into."
            ),
            "stream_delta": True,
        }
    result = ingest_n3_store_gate(out, wait=False)
    if result.pending and not n3_ingest_live(out):
        start_n3_ingest_job(out)
    st = build_status(
        out,
        blueprint=getattr(pd, "blueprint", None),
        plan=getattr(pd, "plan", None),
    )
    if out:
        fake = {
            "output_dir": str(out),
            "inputs_hash": (pd.generation or {}).get("inputs_hash"),
            "product_id": (pd.generation or {}).get("product_id"),
            "engine": (pd.generation or {}).get("engine") or "runner",
            "build": st,
        }
        _record_generation(pd, fake, triggered_by=triggered_by, resumed=True)
    if result.ok:
        summary = (
            "Ingested cerebrum-builds store-gate 12/12. Store-green is on "
            "the ledger and package ship can unlock. I did not start "
            "another coding agent or Background Agent."
        )
    elif result.pending:
        summary = (
            "HANDOFF_TO_N3: polling cerebrum-builds store-gate (commit "
            "status context store-gate) for 12/12. I did not re-enter "
            "WRITER or launch another Background Agent."
        )
    elif result.honesty == N3_STORE_GATE_FACTORY_OWED:
        # The one sentence the owner needs: it is not their product, and a
        # re-run will not help. No softening, no "store gate failed".
        summary = f"{result.detail} I did not start another coding agent."
    else:
        summary = (
            "Store-gate ingest failed closed "
            f"({result.honesty}: {result.detail}). I did not start another "
            "coding agent."
        )
    return {
        "ok": result.ok or result.pending,
        "sse": "generation" if result.ok or result.pending else "info",
        "summary": summary,
        "stream_delta": False,
        "n3_ingest": True,
        "n3": result.to_dict(),
        "generation": pd.generation,
        "build": st,
        "triggered_by": triggered_by,
        "resumed": True,
    }


def reseed_and_ingest_n3(
    state: Any,
    *,
    builds_sha: str,
    builds_branch: str,
    cli_authored_ids: Sequence[str],
    builds_owner: str = "bopoadz-del",
    builds_repo: str = "cerebrum-builds",
    output_root: Optional[Path] = None,
    triggered_by: str = "n3_reseed",
) -> Dict[str, Any]:
    """One-time HANDOFF reseed then store-gate ingest. Never calls generate_product.

    For workspaces whose ledger was wiped before factory_outputs persistence
    (#426). Stamps HANDOFF_TO_N3 like the N3 test helper, then
    ``ingest_n3_store_gate(wait=False)`` / starts the waiter. Fail-closed when
    store-gate is not 12/12 success.
    """
    from app.factory.build.n3_store_gate import (
        HandoffReseedError,
        ingest_n3_store_gate,
        n3_ingest_live,
        reseed_handoff_ledger,
        start_n3_ingest_job,
    )
    from app.factory.build_jobs import build_status

    pd = getattr(state, "product_design", None)
    if pd is None or not getattr(pd, "blueprint", None):
        raise ValueError("no blueprint — draft and approve before n3_reseed")

    sync_blueprint_intake(pd)  # the user's declared locale and build level, never a guess
    bp = ProductBlueprint.model_validate(pd.blueprint)
    out = _generation_output_dir(state, output_root)
    if out is None:
        out = _session_output(state.session_id, bp.product_id, output_root)
    inputs_hash = str((pd.generation or {}).get("inputs_hash") or "n3_handoff_reseed")

    try:
        stamped = reseed_handoff_ledger(
            out,
            builds_sha=builds_sha,
            builds_branch=builds_branch,
            cli_authored_ids=cli_authored_ids,
            builds_owner=builds_owner,
            builds_repo=builds_repo,
            product_id=bp.product_id,
            inputs_hash=inputs_hash,
        )
    except HandoffReseedError:
        raise

    result = ingest_n3_store_gate(out, wait=False, session_id=state.session_id)
    if result.pending and not n3_ingest_live(out):
        start_n3_ingest_job(out, session_id=state.session_id)

    st = build_status(
        out,
        blueprint=getattr(pd, "blueprint", None),
        plan=getattr(pd, "plan", None),
    )
    fake = {
        "output_dir": str(out),
        "inputs_hash": inputs_hash,
        "product_id": bp.product_id,
        "engine": (pd.generation or {}).get("engine") or "runner",
        "build": st,
    }
    _record_generation(pd, fake, triggered_by=triggered_by, resumed=True)

    if result.ok:
        summary = (
            "Reseeded HANDOFF_TO_N3 and ingested cerebrum-builds store-gate "
            "12/12. Store-green is on the ledger. I did not start WRITER or "
            "a Background Agent."
        )
    elif result.pending:
        summary = (
            "Reseeded HANDOFF_TO_N3; polling cerebrum-builds store-gate for "
            "12/12. I did not re-enter WRITER or launch another Background Agent."
        )
    else:
        summary = (
            "HANDOFF reseed stamped but store-gate ingest failed closed "
            f"({result.honesty}: {result.detail}). I did not start another "
            "coding agent."
        )
    return {
        "ok": result.ok or result.pending,
        "sse": "generation" if result.ok or result.pending else "info",
        "summary": summary,
        "stream_delta": False,
        "n3_reseed": True,
        "n3_ingest": True,
        "reseed": stamped,
        "n3": result.to_dict(),
        "generation": pd.generation,
        "build": st,
        "triggered_by": triggered_by,
        "resumed": True,
    }


def start_fresh_generation(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "start_over",
) -> Dict[str, Any]:
    """A new workspace for the platform -- reached ONLY through start_over().

    The old head is already tagged when this runs. The platform keeps its
    one branch: the fresh run's phases are pushed onto build/<platform_id>
    (phase-forward), so the branch's history holds both runs.
    """
    pd = state.product_design
    if not pd or not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")
    if is_pilot_ready(state, output_root):
        return already_complete_reply(state)
    if is_handoff_awaiting_n3(state, output_root):
        return ingest_n3_store_gate_reply(
            state, output_root=output_root, triggered_by=triggered_by
        )
    if has_running_build(state):
        reply = running_build_reply(state)
        reply["already_running"] = True
        return reply

    sync_blueprint_intake(pd)  # the user's declared locale and build level, never a guess
    bp = ProductBlueprint.model_validate(pd.blueprint)
    pd.blueprint_approved = True
    if not pd.plan:
        pd.plan = plan_blueprint(bp, blocks_root=_blocks_root()).to_dict()

    from app.factory.build_jobs import next_fresh_output

    prior = _generation_output_dir(state, output_root)
    base = prior or _session_output(state.session_id, bp.product_id, output_root)
    live = _live_build_thread(base)
    if live is not None:
        st = _generation_status(state, output_root)
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"The coding agent is still writing {bp.product_id}. "
                "I did not start a second run."
            ),
            "stream_delta": True,
            "already_running": True,
            "generation": pd.generation,
            "build": st,
        }

    out = next_fresh_output(base) if prior else Path(base)
    prior_hash = (pd.generation or {}).get("inputs_hash")
    try:
        result = generate_product(
            bp,
            out,
            blocks_root=_blocks_root(),
            cycle="code",
            quota_account_id=getattr(state, "user_id", None),
            tenant_identity=getattr(state, "user_id", None),
            brief=str(getattr(getattr(state, "product_design", None), "brief", "") or "").strip(),
            platform_id=ensure_platform_id(state.product_design),
            start_over=True,
        )
    except CodeCliUnavailable as exc:
        return _cli_unavailable_reply(pd, exc)
    if result.get("already_running"):
        _record_generation(pd, result, triggered_by=triggered_by, resumed=False)
        reply = running_build_reply(state)
        reply["already_running"] = True
        return reply
    _record_generation(pd, result, triggered_by=triggered_by, resumed=False)
    if prior_hash and result.get("inputs_hash") and result["inputs_hash"] != prior_hash:
        logger.warning(
            "fresh-start hash changed for %s: was %s now %s",
            bp.product_id,
            prior_hash[:12],
            str(result["inputs_hash"])[:12],
        )
    pd.last_error = None
    prior_dir = str(prior) if prior else None
    new_dir = result.get("output_dir")
    summary = (
        f"Started over: a fresh build of {result['product_id']} on a new "
        "workspace. The previous run's head is kept as an archive tag on the "
        "platform's branch -- nothing was deleted."
    )
    return {
        "ok": True,
        "sse": "generation",
        "generation": pd.generation,
        "plan": pd.plan,
        "triggered_by": triggered_by,
        "resumed": False,
        "fresh": True,
        "fresh_workspace": True,
        "prior_output_dir": prior_dir,
        "output_dir": new_dir,
        "stream_delta": False,
        "summary": summary,
        "build": result.get("build"),
    }


#: The typed reason Continue answers when a failed platform has neither a
#: workspace on this host nor a branch of record to re-enter.
NOTHING_TO_RESUME = "NOTHING_TO_RESUME"


def resume_failed_platform(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "continue",
    intake_changed: bool = False,
) -> Dict[str, Any]:
    """Continue / Build again / Rebuild with new intake on a FAILED platform.

    One platform = one branch, forever: the failed run is RESUMED in its own
    workspace at its first non-green phase, with the rework budget reset
    (start_runner_build records the reset; the runner rule counts from it).
    It is never replaced by a fresh workspace -- that is Start over only. A
    workspace this host no longer holds is re-entered from the platform's
    branch of record (restored from its archive tag if it was archived).
    """
    from app.factory.build.platform_identity import branch_of_record, platform_id_of

    pd = state.product_design
    if not pd or not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")
    sync_blueprint_intake(pd)  # the user's declared locale and build level, never a guess
    bp = ProductBlueprint.model_validate(pd.blueprint)
    pd.blueprint_approved = True
    if not pd.plan:
        pd.plan = plan_blueprint(bp, blocks_root=_blocks_root()).to_dict()

    prior = _generation_output_dir(state, output_root)
    if prior is None or not Path(prior).is_dir():
        pid = platform_id_of(pd)
        from app.factory.build.builds_push import builds_token

        if pid and builds_token(os.environ):
            reply = attach_from_link(state, branch_of_record(pid), output_root=output_root)
            if reply is not None:
                return reply
        return {
            "ok": False,
            "sse": "info",
            "refused": NOTHING_TO_RESUME,
            "summary": (
                "This platform's workspace is not on this host and it has no "
                "branch of record to re-enter. Press Start over to build it again "
                "from the approved feature list; the old run is kept, not deleted."
            ),
            "awaiting_action": "start_over",
            "stream_delta": True,
        }

    live = _live_build_thread(prior)
    if live is not None:
        reply = running_build_reply(state)
        reply["already_running"] = True
        return reply

    before = _generation_status(state, output_root) or {}
    try:
        result = generate_product(
            bp,
            Path(prior),
            blocks_root=_blocks_root(),
            cycle=_resume_cycle(state, output_root),
            quota_account_id=getattr(state, "user_id", None),
            tenant_identity=getattr(state, "user_id", None),
            brief=str(getattr(pd, "brief", "") or "").strip(),
            platform_id=ensure_platform_id(pd),
            intake_changed=intake_changed,
        )
    except CodeCliUnavailable as exc:
        return _cli_unavailable_reply(pd, exc)
    _record_generation(pd, result, triggered_by=triggered_by, resumed=True)
    pd.last_error = None
    failed = before.get("failed") or {}
    target = failed.get("check") or "the failing check"
    summary = (
        f"Resuming {bp.product_id} on its own branch at {failed.get('gate') or 'the failing phase'} "
        f"with a fresh rework budget — the writer is sent {target}. "
        "Phases that already passed are not rerun."
    )
    return {
        "ok": True,
        "sse": "generation",
        "generation": pd.generation,
        "plan": pd.plan,
        "triggered_by": triggered_by,
        "resumed": True,
        "fresh": False,
        "output_dir": result.get("output_dir"),
        "stream_delta": False,
        "summary": summary,
        "build": result.get("build"),
    }


def start_over(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "start_over",
) -> Dict[str, Any]:
    """The typed Floor action "Start over": the ONLY door to a fresh workspace.

    First the platform's current head is tagged ``archive/<platform_id>/<date>``
    (nothing is deleted) and the tag is recorded in the run's ledger; only then
    is a fresh workspace created. A tag that cannot be written stops here --
    no fresh workspace without the old head kept."""
    from app.factory.build.builds_push import builds_token

    pd = state.product_design
    if not pd or not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")
    pid = ensure_platform_id(pd)
    archived = None
    if builds_token(os.environ):
        from app.factory.build.platform_branch import builds_remote, tag_head_for_archive

        try:
            archived = tag_head_for_archive(builds_remote(), pid)
        except Exception as exc:  # noqa: BLE001 -- a reply, and nothing is created
            return {
                "ok": False,
                "sse": "info",
                "summary": (
                    f"Start over stopped before anything changed: the current "
                    f"head could not be tagged for safekeeping ({exc})."
                ),
                "stream_delta": True,
            }
    prior = _generation_output_dir(state, output_root)
    if prior is not None and Path(prior).is_dir():
        from app.factory.build.ledger import BuildLedger, EventKind

        ledger = BuildLedger(Path(prior) / "build_ledger.jsonl")
        if ledger.exists():
            ledger.append(
                EventKind.NOTE,
                detail=(
                    f"START OVER: head kept as {archived.tag}@{archived.sha[:7]}"
                    if archived
                    else "START OVER: no branch head to keep"
                ),
                payload={
                    "start_over": True,
                    "archived_tag": archived.tag if archived else None,
                    "archived_sha": archived.sha if archived else None,
                },
            )
    reply = start_fresh_generation(state, output_root=output_root, triggered_by=triggered_by)
    reply["archived"] = (
        {"tag": archived.tag, "sha": archived.sha} if archived else None
    )
    return reply


def take_copy(state: Any, output_root: Optional[Path] = None) -> Dict[str, Any]:
    """The typed "Take a copy" on a FAILED build: the as-is export, whose
    MANIFEST.json names FAILED(gate, check, finding) and ``certified: false``.
    It changes nothing: the platform and its branch stay as they are."""
    if not is_generation_terminal_failure(state, output_root):
        return {
            "sse": "info",
            "ok": False,
            "summary": "There is no failed build to take a copy of.",
        }
    return {
        "sse": "info",
        "ok": True,
        "summary": (
            "Your copy is the platform as it stands: the export is labelled "
            "FAILED, its MANIFEST.json names the failing gate, check and finding, "
            "and it is not certified. Nothing about the platform changes."
        ),
        "download": {"as_is": True},
    }


def continue_with_intake(state: Any, output_root: Optional[Path] = None) -> Dict[str, Any]:
    """The typed "Continue with new answers" on a FAILED build: the intake
    line opens again, pre-filled with the declared answers. Nothing runs until
    the typed ``confirm_intake``, which resumes this platform's own branch."""
    pd = state.product_design
    if not is_generation_terminal_failure(state, output_root):
        return {
            "sse": "info",
            "ok": False,
            "summary": "There is no failed build to continue.",
            "intake": intake_state(pd),
        }
    declared = {
        k: v
        for k, v in intake_state(pd)["declared"].items()
        if isinstance(v, str) and v
    }
    pd.intake_reopened = True
    pd.intake_proposal = declared or None
    return {
        "sse": "info",
        "ok": True,
        "summary": (
            "Change any answer on the intake line, then press Confirm. The build "
            "resumes on this platform's own branch with a fresh rework budget; "
            "nothing runs until you confirm."
        ),
        "intake": intake_state(pd),
    }


def intake_reopened(state: Any) -> bool:
    return bool(getattr(getattr(state, "product_design", None), "intake_reopened", False))


def confirm_and_continue(state: Any, output_root: Optional[Path] = None) -> Dict[str, Any]:
    """The typed Confirm on a re-opened intake: store the answers, then resume
    the SAME branch of record. Unchanged answers resume at the failing phase;
    changed ones change the frozen blueprint's inputs, so the run is rebased
    onto them on the same workspace (start_runner_build ``intake_changed``)."""
    pd = state.product_design
    stored = confirm_intake(pd)
    pd.intake_reopened = False
    if isinstance(pd.blueprint, dict) and pd.vertical and pd.blueprint.get("vertical") != pd.vertical:
        pd.blueprint["vertical"] = pd.vertical
    reply = resume_failed_platform(
        state, output_root=output_root, triggered_by="continue_with_intake", intake_changed=True
    )
    reply["intake"] = intake_state(pd)
    if stored:
        parts = [f"{k.replace('_', ' ')} {v}" for k, v in stored.items()]
        reply["summary"] = "Confirmed: " + ", ".join(parts) + ". " + str(reply.get("summary") or "")
    return reply


def attach_from_link(
    state: Any,
    message: str,
    output_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """R1-R3: a pasted cerebrum-builds session link resumes THAT build.

    ``None`` when the message is not a build link at all -- normal chat
    handling continues. A link to any other repo, or one naming no
    ``build/sess_*`` branch, is refused with no state change. Never reaches
    ``start_fresh_generation``: the branch is the workspace, and the run
    starts at the first phase the branch does not prove done.
    """
    from app.factory.build.branch_attach import AttachError, attach, parse_build_link

    try:
        branch, refusal = parse_build_link(message)
    except Exception as exc:  # noqa: BLE001 -- a lookup failure is a reply, not a crash
        return {"ok": False, "sse": "info", "summary": f"Could not resolve that session: {exc}",
                "stream_delta": True}
    if refusal:
        return {"ok": False, "sse": "info", "summary": refusal, "stream_delta": True}
    if not branch:
        return None

    from app.factory.build.platform_identity import platform_id_from_branch

    archived_pid = platform_id_from_branch(branch)
    if archived_pid:
        # Continue on an archived platform: recreate its branch from the
        # newest archive tag before attaching (no-op when the branch exists).
        from app.factory.build.builds_push import builds_token

        if builds_token(os.environ):
            from app.factory.build.platform_branch import builds_remote, restore_branch

            try:
                restore_branch(builds_remote(), archived_pid)
            except Exception as exc:  # noqa: BLE001 -- a reply, not a crash
                return {"ok": False, "sse": "info", "stream_delta": True,
                        "summary": f"Could not restore {branch} from its archive tag: {exc}"}
    session_id = branch.split("/", 1)[1].split("-", 1)[0]
    parent = (
        Path(output_root) / "attached"
        if output_root is not None
        else factory_outputs_root() / "sessions" / session_id / "attached"
    )
    try:
        got = attach(branch, parent)
    except AttachError as exc:
        return {"ok": False, "sse": "info", "summary": str(exc), "stream_delta": True}

    bp = ProductBlueprint.model_validate(got.blueprint)
    pd = state.product_design
    from app.factory.build.platform_identity import platform_id_from_branch

    adopted = platform_id_from_branch(branch)
    if adopted:
        # The branch IS the platform: a pasted platform branch carries its id.
        pd.platform_id = adopted
    pd.blueprint = got.blueprint
    pd.blueprint_approved = True
    if got.plan:
        pd.plan = got.plan

    from app.factory.build_jobs import start_runner_build

    try:
        result = start_runner_build(
            bp,
            got.workspace,
            blocks_root=_blocks_root(),
            cycle="code",
            quota_account_id=getattr(state, "user_id", None),
            tenant_identity=getattr(state, "user_id", None),
            brief=str(getattr(pd, "brief", "") or "").strip(),
            attach_branch=got.branch,
            attach_sha=got.sha,
            attach_passed=got.passed,
            platform_id=platform_id_of(pd),
        )
    except CodeCliUnavailable as exc:
        return _cli_unavailable_reply(pd, exc)
    _record_generation(pd, result, triggered_by="build_link", resumed=True)
    phase = (pd.generation or {}).get("resume_point") or "the next phase"
    summary = f"Attached {session_id} @ {got.sha[:7]} — resuming at {phase}"
    if result.get("already_running"):
        summary = f"Attached {session_id} @ {got.sha[:7]} — already running at {phase}"
    return {
        "ok": True,
        "sse": "generation",
        "generation": pd.generation,
        "plan": pd.plan,
        "triggered_by": "build_link",
        "resumed": True,
        "stream_delta": False,
        "summary": summary,
    }


def resume_generation(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "regex_resume",
) -> Dict[str, Any]:
    """Resume the session's coding run on the same blueprint hash.

    Calls ``generate_product`` into the existing output dir. The runner
    sees the ledger, skips completed phases, and does not re-CLONER from
    zero when WRITER/TESTER already progressed.

    A terminal RUN_FAILED is not resumed — that path starts a fresh
    workspace so a dead ledger cannot swallow the request.
    """
    pd = state.product_design
    if not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")
    if is_generation_complete(state):
        return already_complete_reply(state)
    if is_handoff_awaiting_n3(state, output_root):
        return ingest_n3_store_gate_reply(
            state, output_root=output_root, triggered_by=triggered_by
        )
    if is_generation_terminal_failure(state, output_root):
        # One platform = one branch: a failed run is resumed, never replaced.
        return resume_failed_platform(
            state, output_root=output_root, triggered_by=triggered_by
        )

    sync_blueprint_intake(pd)  # the user's declared locale and build level, never a guess
    bp = ProductBlueprint.model_validate(pd.blueprint)
    pd.blueprint_approved = True
    if not pd.plan:
        pd.plan = plan_blueprint(bp, blocks_root=_blocks_root()).to_dict()

    out = _generation_output_dir(state, output_root)
    if out is None:
        out = _session_output(state.session_id, bp.product_id, output_root)

    live = _live_build_thread(out)
    if live is not None:
        st = _generation_status(state, output_root)
        activity = st.get("activity") or "writing the platform"
        done = st.get("phases_done") or 0
        total = st.get("phases_total") or 5
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"The coding agent is still writing {bp.product_id} — "
                f"{done}/{total} phases ({activity}). I did not start a second run."
            ),
            "stream_delta": True,
            "already_running": True,
            "generation": pd.generation,
            "build": st,
        }

    prior_hash = (pd.generation or {}).get("inputs_hash")
    try:
        result = generate_product(
            bp,
            out,
            blocks_root=_blocks_root(),
            cycle=_resume_cycle(state, output_root),
            quota_account_id=getattr(state, "user_id", None),
            tenant_identity=getattr(state, "user_id", None),
            brief=str(getattr(getattr(state, "product_design", None), "brief", "") or "").strip(),
            platform_id=ensure_platform_id(state.product_design),
        )
    except CodeCliUnavailable as exc:
        return _cli_unavailable_reply(pd, exc)
    if result.get("already_running"):
        _record_generation(pd, result, triggered_by=triggered_by, resumed=True)
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"The coding agent is still writing {bp.product_id}. "
                "I did not start a second run."
            ),
            "stream_delta": True,
            "already_running": True,
            "generation": pd.generation,
            "build": result.get("build"),
        }
    _record_generation(pd, result, triggered_by=triggered_by, resumed=True)
    if prior_hash and result.get("inputs_hash") and result["inputs_hash"] != prior_hash:
        logger.warning(
            "resume hash changed for %s: was %s now %s",
            bp.product_id,
            prior_hash[:12],
            str(result["inputs_hash"])[:12],
        )
    pd.last_error = None

    st = (pd.generation or {}).get("build") or {}
    resume_at = (pd.generation or {}).get("resume_point") or st.get("activity") or "the last phase"
    done = st.get("phases_done") or 0
    total = st.get("phases_total") or 5
    written = st.get("activity_done")
    of = st.get("activity_total")
    artifact_bit = ""
    if written is not None and of is not None:
        artifact_bit = f", {written}/{of} artifacts"
    summary = (
        f"Resuming the coding agent for {result['product_id']} from {resume_at} "
        f"({done}/{total} phases{artifact_bit}). Same blueprint hash — not starting over."
    )
    return {
        "ok": True,
        "sse": "generation",
        "generation": pd.generation,
        "plan": pd.plan,
        "triggered_by": triggered_by,
        "resumed": True,
        "stream_delta": False,
        "summary": summary,
    }


def resume_pilot_cycle(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "regex_pilot",
) -> Dict[str, Any]:
    """Open a Store-green cycle on the same workspace / hash / output.

    Does not draft or approve a new blueprint. The runner sees
    ``PILOT_OPENED``, skips COLLECTOR/CLONER/WRITER, runs ``pytest -m pilot``,
    reworks only failing capabilities, and applies STORE ops.
    """
    if is_pilot_ready(state, output_root):
        return already_complete_reply(state)
    pd = state.product_design
    if not pd or not pd.blueprint:
        raise ValueError("no blueprint drafted — describe the platform first")
    sync_blueprint_intake(pd)  # the user's declared locale and build level, never a guess
    bp = ProductBlueprint.model_validate(pd.blueprint)
    from app.factory.build.build_level import bar_for

    bar = bar_for(bp)
    if bar is not None and not bar.reaches_pilot:
        # The level is the single input: a prototype is DONE at CODE_GREEN.
        # Climbing further is a different bar, so it is the user's to raise.
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"This build's level is {bar.level.value}: it is done at "
                "CODE_GREEN and no pilot cycle opens. The level is frozen with "
                "the approved blueprint; draft a new platform at light, pilot "
                "or production to climb to the Store gate."
            ),
            "stream_delta": True,
            "already_complete": True,
        }
    pd.blueprint_approved = True
    if not pd.plan:
        pd.plan = plan_blueprint(bp, blocks_root=_blocks_root()).to_dict()
    out = _generation_output_dir(state, output_root)
    if out is None:
        out = _session_output(state.session_id, bp.product_id, output_root)
    live = _live_build_thread(out)
    if live is not None:
        st = _generation_status(state, output_root)
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"The coding agent is still writing {bp.product_id}. "
                "I did not start a second run."
            ),
            "stream_delta": True,
            "already_running": True,
            "generation": pd.generation,
            "build": st,
        }
    prior_hash = (pd.generation or {}).get("inputs_hash")
    try:
        result = generate_product(
            bp,
            out,
            blocks_root=_blocks_root(),
            cycle="pilot",
            quota_account_id=getattr(state, "user_id", None),
            tenant_identity=getattr(state, "user_id", None),
            brief=str(getattr(getattr(state, "product_design", None), "brief", "") or "").strip(),
            platform_id=ensure_platform_id(state.product_design),
        )
    except CodeCliUnavailable as exc:
        return _cli_unavailable_reply(pd, exc)
    if result.get("already_running"):
        _record_generation(pd, result, triggered_by=triggered_by, resumed=True)
        return {
            "ok": True,
            "sse": "info",
            "summary": (
                f"The coding agent is still writing {bp.product_id}. "
                "I did not start a second run."
            ),
            "stream_delta": True,
            "already_running": True,
            "generation": pd.generation,
            "build": result.get("build"),
        }
    _record_generation(pd, result, triggered_by=triggered_by, resumed=True)
    if prior_hash and result.get("inputs_hash") and result["inputs_hash"] != prior_hash:
        logger.warning(
            "pilot resume hash changed for %s: was %s now %s",
            bp.product_id,
            prior_hash[:12],
            str(result["inputs_hash"])[:12],
        )
    st = result.get("build") or {}
    summary = (
        f"Opening a pilot cycle for {result['product_id']} on the same "
        "workspace/hash. TESTER will run pytest -m pilot; WRITER reworks "
        "only failing capabilities; STORE_MANAGER applies store ops. "
        "Not a new product."
    )
    return {
        "ok": True,
        "sse": "generation",
        "generation": pd.generation,
        "plan": pd.plan,
        "triggered_by": triggered_by,
        "resumed": True,
        "cycle": "pilot",
        "stream_delta": False,
        "summary": summary,
        "build": st,
    }


def _adopt_green_build(
    state: Any, output_root: Optional[Path], triggered_by: str
) -> Optional[Dict[str, Any]]:
    """Reseed + ingest a passing, identical build branch. None = nothing adopted."""
    try:
        from app.factory.build.adopt_green import cli_authored_ids, find_adoptable_branch

        out = _generation_output_dir(state, output_root)
        if out is None or not Path(out).is_dir():
            return None
        found = find_adoptable_branch(out, str(getattr(state, "session_id", "") or ""))
        if found is None:
            return None
        ids = list(cli_authored_ids(out))
        if not ids:
            return None
        logger.info("adopting green build branch %s for %s", found["branch"], state.session_id)
        return reseed_and_ingest_n3(
            state,
            builds_sha=found["sha"],
            builds_branch=found["branch"],
            cli_authored_ids=ids,
            builds_owner=found["owner"],
            builds_repo=found["repo"],
            output_root=output_root,
            triggered_by="adopt_green:" + str(triggered_by),
        )
    except Exception:  # noqa: BLE001 -- adoption is an optimisation; never block a resume
        logger.warning("green build adoption failed; falling through", exc_info=True)
        return None


def record_withdrawn_certification(
    state: Any, output_root: Optional[Path] = None
) -> Optional[str]:
    """When the Store gate has withdrawn the certification of this session's
    succeeded run, record it on the ledger (n3_store_gate.
    record_certification_withdrawn) and return why; otherwise None.

    Read from the same store-gate commit status the build was certified by;
    no builds token or no answer from GitHub is no verdict, so nothing is
    recorded. Never raises into the Floor."""
    out = _generation_output_dir(state, output_root)
    if not out:
        return None
    try:
        if not _ledger_for(out).succeeded():
            return None
        from app.factory.build.n3_store_gate import (
            certification_withdrawn,
            record_certification_withdrawn,
        )

        reason = certification_withdrawn(out)
        if reason and record_certification_withdrawn(out, reason):
            logger.warning("certification withdrawn for %s: %s", out, reason)
            return reason
    except Exception:  # noqa: BLE001 -- an unreadable verdict changes nothing
        logger.warning("withdrawn-certification check failed for %s", out, exc_info=True)
    return None


def start_or_resume_coder(
    state: Any,
    output_root: Optional[Path] = None,
    triggered_by: str = "regex_approve",
) -> Dict[str, Any]:
    """Start WRITER on a pending blueprint, or resume an interrupted run.

    Never requires a pending unapproved blueprint to resume. Code-phase
    SUCCESS is not the end: continue opens a pilot cycle on the same
    workspace. Only a Store-green SUCCESS refuses another run.

    A RUN_FAILED / rework-exhausted ledger is terminal: continue or
    start_coder starts a fresh workspace with a reset rework budget.
    """
    if is_handoff_awaiting_n3(state, output_root):
        return ingest_n3_store_gate_reply(
            state, output_root=output_root, triggered_by=triggered_by
        )
    if has_pending_blueprint(state):
        return approve_and_generate(
            state, output_root=output_root, triggered_by=triggered_by
        )
    if is_pilot_ready(state, output_root):
        return already_complete_reply(state)
    if is_generation_complete(state):
        resume_by = (
            "chat_llm" if triggered_by == "chat_llm" else "regex_pilot"
        )
        return resume_pilot_cycle(
            state, output_root=output_root, triggered_by=resume_by
        )
    # Before rebuilding or resuming: did a build of this session ALREADY pass
    # the Store gate? Audit 2026-09-19 found sessions marked failed / stalled
    # whose cerebrum-builds branch was green in Docker. Adopt it -- but only
    # when the branch's files are byte-identical to the workspace the zip is
    # cut from (adopt_green verifies; anything unverifiable adopts nothing).
    adopted = _adopt_green_build(state, output_root, triggered_by)
    if adopted is not None:
        return adopted
    if is_generation_terminal_failure(state, output_root):
        # One platform = one branch: a failed run is resumed, never replaced.
        return resume_failed_platform(
            state, output_root=output_root, triggered_by=triggered_by
        )
    if is_generation_resumable(state):
        resume_by = (
            "chat_llm" if triggered_by == "chat_llm" else "regex_resume"
        )
        return resume_generation(
            state, output_root=output_root, triggered_by=resume_by
        )
    return {
        "ok": False,
        "sse": "info",
        "summary": (
            "There is no pending blueprint to build and no interrupted "
            "coding run to resume. Describe the platform you want first."
        ),
        "stream_delta": True,
    }
