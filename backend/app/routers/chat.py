import asyncio
import json
import logging
from datetime import datetime
from typing import AsyncGenerator, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..core.auth import Principal, require_api_key
from ..core.billing import assert_entitled
from ..core.session_guard import require_owned_session
from ..core.session_store import get_session, update_session
from ..core.chain_generator import (
    generate_chain_suggestion,
    validate_chain,
    fetch_block_registry,
    check_chain_quality,
)
from ..core.grounding import (
    VERDICT_OUT_OF_SCOPE,
    check_scope_refusal,
    evaluate_grounding,
    persist_verdict,
    strict_figures,
)
from ..core.rule_injector import inject_rules
from ..core.llm_throttle import require_llm_rate
from ..core.trial_limits import TrialLimitExceeded, require_remaining, require_within_limit
from ..factory import platform_chat_flow, platform_chat_llm
from ..factory.floor_actions import (
    INTAKE_ACTIONS,
    REFINEMENT_ACTIONS,
    RUN_ACTIONS,
    FloorAction,
    FloorActionError,
    parse_action,
    require_build_level,
)
from ..factory.locale_choice import intake_state
from ..models.session import SessionState

logger = logging.getLogger(__name__)
router = APIRouter()


class ChatMessage(BaseModel):
    message: str
    #: The vertical the user picked or typed on the Floor -- a typed field,
    #: never read out of ``message``. Sent once is enough: it persists on the
    #: session (``product_design.vertical``) until the user changes it.
    vertical: Optional[str] = None
    #: Country (ISO 3166 alpha-2) and currency (ISO 4217) the user typed on
    #: the Floor -- typed fields, persisted on the session like ``vertical``.
    country: Optional[str] = None
    currency: Optional[str] = None
    #: What a Floor control asked the Factory to DO (app.factory.floor_actions:
    #: approve, continue, run_pilot, draft, add/remove_capability, rename,
    #: set_vertical, set_build_level, confirm_intake, list_capabilities) and
    #: its value. Typed -- the Factory never decides an action from the words
    #: in ``message``.
    action: Optional[str] = None
    value: Optional[str] = None


class ApproveRequest(BaseModel):
    approve: bool = True


def _docs_summary(state) -> str:
    if not state.chunks:
        return ""
    preview = " ".join(state.chunks)[:1500]
    return f"Document preview: {preview}"


def _session_state_summary(state) -> str:
    """Authoritative session facts used to ground the conversational fallback.

    The LLM fallback answers strictly from this summary; it performs no
    platform actions itself, so it must not invent them either.
    """
    pd = getattr(state, "product_design", None)
    if not pd or not getattr(pd, "blueprint", None):
        return "No platform blueprint has been drafted in this session."
    bp = pd.blueprint or {}
    caps = bp.get("capabilities") or []
    lines = [
        f"Drafted blueprint: {bp.get('product_name')} (vertical: {bp.get('vertical')}), "
        f"{len(caps)} capabilities.",
        f"Blueprint approved: {'yes' if pd.blueprint_approved else 'no'}.",
    ]
    gen = getattr(pd, "generation", None)
    if gen:
        lines.append(
            f"Generated product: {gen.get('product_id')} — available under Your Platforms."
        )
        if gen.get("inputs_hash"):
            lines.append(f"Blueprint hash: {str(gen.get('inputs_hash'))[:12]}.")
        if gen.get("output_dir"):
            lines.append(f"Output dir: {gen.get('output_dir')}.")
        if platform_chat_flow.is_generation_complete(state):
            lines.append("Last coding run finished successfully.")
        elif platform_chat_flow.is_generation_terminal_failure(state):
            lines.append(
                "Last coding run FAILED (rework exhausted or gates still red). "
                "A new brief starts a new product; continue starts a fresh workspace."
            )
        elif platform_chat_flow.is_generation_resumable(state):
            lines.append(
                "Coding run is incomplete and can be resumed with continue/resume."
            )
    else:
        lines.append("No product has been generated yet.")
    if getattr(pd, "last_error", None):
        lines.append(f"Last error: {pd.last_error}")
    return "\n".join(lines)


async def _stream_response(
    session_id: str,
    user_message: str,
    action: Optional[FloorAction] = None,
    value: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    state = get_session(session_id)
    if not state:
        yield _sse_event("error", "Session not found")
        return

    # Server-side trial boundary: chat messages are metered per account.
    try:
        require_within_limit(getattr(state, "user_id", None), "chat_message")
    except TrialLimitExceeded as exc:
        yield _sse_event("error", exc.detail["message"])
        yield _sse_event("done", "")
        return

    # Scope refusal precedes everything: a refused question never reaches
    # the platform flow or the LLM, however grounded the corpus may be.
    refusal = check_scope_refusal(user_message)
    if refusal is not None:
        persist_verdict(
            {
                "surface": "chat",
                "session_id": session_id,
                "query": user_message,
                "verdict": VERDICT_OUT_OF_SCOPE,
                "refusal_id": refusal["id"],
            }
        )
        notice = (
            "I can't help with that: " + refusal["reason"] + " (out_of_scope)"
        )
        state.chat_history.append({"role": "user", "content": user_message})
        state.chat_history.append({"role": "assistant", "content": notice})
        state.updated_at = datetime.utcnow()
        update_session(session_id, state)
        for word in notice.split(" "):
            yield _sse_event("delta", word + " ")
        yield _sse_event("done", "")
        return

    state.chat_history.append({"role": "user", "content": user_message})
    state.updated_at = datetime.utcnow()

    # --- Platform-creation flow: chat bridges to the product state machine ---
    # Typed Floor actions are dispatched deterministically; free text goes to
    # the Floor chat LLM (a typed decision) or, with no LLM, to an honest
    # pointer at the controls. Nothing reads the user's words to pick an
    # action. PLATFORM_CHAT_FLOW_ENABLED=0 keeps the legacy configurator.
    if action is FloorAction.CHAIN or (
        action is None and not platform_chat_flow.platform_chat_enabled()
    ):
        pass  # legacy kit-chain generator below
    else:
        try:
            async for ev in _platform_turn(session_id, state, user_message, action, value):
                yield ev
            return
        except Exception as exc:  # honest failure, stay in chat
            logger.exception("Platform flow failed")
            message = (
                "Platform flow hit a blocker: "
                f"{exc}. Nothing was generated; refine the brief or check the factory logs."
            )
            state.chat_history.append({"role": "assistant", "content": message})
            state.updated_at = datetime.utcnow()
            update_session(session_id, state)
            yield _sse_event("error", message)
            yield _sse_event("done", "")
            return

    update_session(session_id, state)

    yield _sse_event("status", "thinking")

    try:
        suggestion = await generate_chain_suggestion(
            domain=state.config.domain,
            user_message=user_message,
            chat_history=state.chat_history[:-1],
            docs_summary=_docs_summary(state),
            session_state=_session_state_summary(state),
        )
    except Exception as exc:
        logger.exception("Chain generation failed")
        yield _sse_event("error", f"Failed to generate suggestion: {exc}")
        return

    assistant_message = suggestion.get("message", "")
    chain = suggestion.get("chain")
    rules = suggestion.get("rules", [])

    # Mandatory grounding stage: the LLM answer never reaches the stream
    # unverified. Blocked → answer null; flagged → estimate disclosure attached.
    grounding_sources = [
        _docs_summary(state),
        _session_state_summary(state),
        *[m.get("content", "") for m in state.chat_history],
    ]
    # This surface reports build state and artifact counts, so a figure the
    # sources do not assert is refused rather than annotated: a confidently
    # wrong count is worse here than a refusal.
    verdict = evaluate_grounding(
        assistant_message,
        sources=grounding_sources,
        query=user_message,
        strict=strict_figures(),
    )
    persist_verdict(
        {
            "surface": "chat",
            "session_id": session_id,
            "query": user_message,
            "verdict": verdict["verdict"],
            "reasons": verdict["reasons"],
            "unsupported_figures": verdict["unsupported_figures"],
        }
    )
    yield _sse_event(
        "grounding",
        json.dumps(
            {"verdict": verdict["verdict"], "answer": verdict["allowed_response"]}
        ),
    )
    if verdict["verdict"] == "blocked":
        # The refusal must not echo the invented content; full reasons live
        # only in the persisted verdict record.
        refusal = (
            "I can't answer that from the grounded session context — the drafted "
            "reply contained unverifiable claims (such as links or figures with "
            "no grounded source) and was withheld."
        )
        state.chat_history.append({"role": "assistant", "content": refusal})
        state.updated_at = datetime.utcnow()
        update_session(session_id, state)
        for word in refusal.split(" "):
            yield _sse_event("delta", word + " ")
        yield _sse_event("done", "")
        return

    assistant_message = verdict["allowed_response"]

    # Stream the assistant message word-by-word for UI effect
    words = assistant_message.split(" ")
    for word in words:
        yield _sse_event("delta", word + " ")

    state.chat_history.append({"role": "assistant", "content": assistant_message})

    if chain:
        registry = await fetch_block_registry()
        if validate_chain(chain, list(registry.keys())):
            state.proposed_chain = chain
            state.validation_passed = True
            state.chain_quality = check_chain_quality(state.config.domain, chain, True)
            chain_payload = {"chain": chain}
            if state.chain_quality:
                chain_payload["quality"] = state.chain_quality
            yield _sse_event("chain", json.dumps(chain_payload))
        else:
            state.validation_passed = False
            state.chain_quality = None
            yield _sse_event("error", "Generated chain failed validation")

    if rules:
        state.extracted_rules = list(set(state.extracted_rules + rules))
        yield _sse_event("rules", json.dumps(rules))

    state.updated_at = datetime.utcnow()
    update_session(session_id, state)

    yield _sse_event("done", "")


def _sse_event(event: str, data: str) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _yield_platform_result(result: dict) -> AsyncGenerator[str, None]:
    """Emit one platform-flow result as SSE, then done.

    Blueprint/generation cards carry the summary; info replies also stream
    as deltas so they appear in the bubble without a card renderer.
    """
    sse = result.get("sse")
    if not sse:
        if result.get("generation"):
            sse = "generation"
        elif result.get("blueprint") and result.get("refined") is not False:
            sse = "blueprint"
        else:
            sse = "info"
    summary = result.get("summary") or ""
    if result.get("intake") is not None:
        # The Floor's intake line: the declared typed fields and any pending
        # proposal, as their own event so no card has to carry them.
        yield _sse_event("intake", json.dumps(result["intake"]))
    if sse in {"blueprint", "generation"}:
        yield _sse_event(sse, json.dumps(result))
    elif sse == "error":
        yield _sse_event("error", summary)
    else:
        yield _sse_event("info", json.dumps(result))
        if result.get("stream_delta", True):
            for word in summary.split(" "):
                yield _sse_event("delta", word + " ")
    yield _sse_event("done", "")


async def _platform_turn(
    session_id: str,
    state: SessionState,
    user_message: str,
    action: Optional[FloorAction],
    value: Optional[str],
) -> AsyncGenerator[str, None]:
    """One platform-flow turn. Card events (blueprint/generation) carry their
    own summary; only ``info`` replies stream as deltas."""

    def _record(result):
        state.chat_history.append({"role": "assistant", "content": result["summary"]})
        state.updated_at = datetime.utcnow()
        update_session(session_id, state)

    # R1: a pasted cerebrum-builds session link resumes THAT build, from
    # where it stopped -- a URL is structure, not prose.
    attached = platform_chat_flow.attach_from_link(state, user_message)
    if attached is not None:
        _record(attached)
        async for ev in _yield_platform_result(attached):
            yield ev
        return

    if action is not None:
        async for ev in _typed_action(session_id, state, user_message, action, value, _record):
            yield ev
        return

    # Free text. Once the feature list is approved the coding agent owns
    # the floor: a running build answers with its status.
    if platform_chat_flow.has_running_build(state):
        result = platform_chat_flow.running_build_reply(state)
        _record(result)
        async for ev in _yield_platform_result(result):
            yield ev
        return

    llm_result = None
    orchestrated = platform_chat_llm.should_orchestrate(state, user_message)
    refusal_kind = None
    if orchestrated:
        decision, refusal_kind = await asyncio.to_thread(
            platform_chat_llm.try_decide_with_reason, state, user_message
        )
        if decision:
            decision = platform_chat_llm.enforce_elicitation_cap(decision, state, user_message)
            # Free text never starts a build (apply_decision answers a
            # start_coder with a pointer at the typed Approve/Continue), so
            # nothing on this path spends a generation.
            llm_result = platform_chat_llm.apply_decision(state, user_message, decision)
    if llm_result is not None:
        _record(llm_result)
        async for ev in _yield_platform_result(llm_result):
            yield ev
        return

    # No chat LLM answered. The Factory does not guess an action from the
    # words: the Floor's controls perform them. Say WHY, by the typed kind:
    # "not configured" only when no provider is configured at all -- a
    # provider that refused (payment, rate limit, outage) is configured.
    from app.core.model_ladder import NOT_CONFIGURED, floor_unavailable_reason

    if not orchestrated or refusal_kind == NOT_CONFIGURED:
        why = floor_unavailable_reason(None)
    elif refusal_kind:
        why = floor_unavailable_reason(refusal_kind)
    else:
        why = "the Floor chat model did not return a usable answer this time"
    guidance = (
        "Use the Floor controls: Draft turns your brief into a feature list; "
        "Approve starts the coding agent; Continue resumes a build; the "
        "feature-list editor and rename change a pending blueprint; the "
        "intake line sets the build level, vertical, country and currency. "
        f"Free-text conversation is unavailable right now: {why}."
    )
    result = {"sse": "info", "ok": True, "summary": guidance, "stream_delta": True}
    _record(result)
    async for ev in _yield_platform_result(result):
        yield ev


async def _typed_action(session_id, state, user_message, action, value, _record):
    """Dispatch a typed Floor action. Deterministic; no model, no prose."""
    if action is FloorAction.TAKE_COPY:
        result = platform_chat_flow.take_copy(state)
        _record(result)
        async for ev in _yield_platform_result(result):
            yield ev
        return

    if action is FloorAction.CONTINUE_WITH_INTAKE:
        result = platform_chat_flow.continue_with_intake(state)
        _record(result)
        async for ev in _yield_platform_result(result):
            yield ev
        return

    if action is FloorAction.CONFIRM_INTAKE and platform_chat_flow.intake_reopened(state):
        # Confirm on a re-opened intake resumes the platform: it spends a
        # generation exactly as Continue does.
        try:
            require_remaining(getattr(state, "user_id", None), "generation")
        except TrialLimitExceeded as exc:
            yield _sse_event("error", exc.detail["message"])
            yield _sse_event("done", "")
            return
        result = platform_chat_flow.confirm_and_continue(state)
        if result.get("ok") and not result.get("already_running"):
            require_within_limit(getattr(state, "user_id", None), "generation")
        _record(result)
        async for ev in _yield_platform_result(result):
            yield ev
        return

    if action in INTAKE_ACTIONS:
        result = platform_chat_flow.apply_intake_action(state, action, value)
        _record(result)
        async for ev in _yield_platform_result(result):
            yield ev
        return

    if action in RUN_ACTIONS:
        # No build without the user's chosen level -- no default, no guess.
        refused = require_build_level(state.product_design)
        if refused is not None:
            refused = {**refused, "intake": intake_state(state.product_design)}
            _record(refused)
            async for ev in _yield_platform_result(refused):
                yield ev
            return

    if action in REFINEMENT_ACTIONS:
        refined = platform_chat_flow.apply_refinement(state, action, value)
        if refined is None:
            refined = {
                "sse": "info",
                "ok": False,
                "summary": "There is no pending blueprint to change. Draft one first.",
            }
        _record(refined)
        async for ev in _yield_platform_result(refined):
            yield ev
        return

    if action is FloorAction.DRAFT:
        if not (user_message or "").strip():
            result = {"sse": "info", "ok": False, "summary": "Draft needs a brief."}
            _record(result)
            async for ev in _yield_platform_result(result):
                yield ev
            return
        result = platform_chat_flow.draft_from_chat(state, user_message)
        _record(result)
        yield _sse_event("blueprint", json.dumps(result))
        yield _sse_event("done", "")
        return

    if action is FloorAction.APPROVE:
        if not platform_chat_flow.has_pending_blueprint(state):
            result = {"sse": "info", "ok": False, "summary": "There is no feature list waiting for approval."}
            _record(result)
            async for ev in _yield_platform_result(result):
                yield ev
            return
        try:
            require_remaining(getattr(state, "user_id", None), "generation")
        except TrialLimitExceeded as exc:
            yield _sse_event("error", exc.detail["message"])
            yield _sse_event("done", "")
            return
        result = platform_chat_flow.approve_and_generate(state)
        if result.get("ok") and not result.get("already_running") and result.get("sse") != "error":
            require_within_limit(getattr(state, "user_id", None), "generation")
        if result.get("ok") and not result.get("sse"):
            result = {**result, "sse": "generation"}
        _record(result)
        async for ev in _yield_platform_result(result):
            yield ev
        return

    if action is FloorAction.START_OVER:
        # The only door to a fresh workspace: the old head is tagged first.
        if platform_chat_flow.has_pending_blueprint(state) or not state.product_design.blueprint:
            result = {
                "sse": "info",
                "ok": False,
                "summary": "There is no built platform to start over. Approve the feature list to build it.",
            }
            _record(result)
            async for ev in _yield_platform_result(result):
                yield ev
            return
        try:
            require_remaining(getattr(state, "user_id", None), "generation")
        except TrialLimitExceeded as exc:
            yield _sse_event("error", exc.detail["message"])
            yield _sse_event("done", "")
            return
        result = platform_chat_flow.start_over(state)
        if result.get("ok") and not result.get("already_running"):
            require_within_limit(getattr(state, "user_id", None), "generation")
        _record(result)
        async for ev in _yield_platform_result(result):
            yield ev
        return

    # CONTINUE / RUN_PILOT: a resume door, never a new draft.
    # A certification the Store gate has since withdrawn is the gate's
    # failure, recorded before anything reads the run as complete.
    platform_chat_flow.record_withdrawn_certification(state)
    resumable = (
        platform_chat_flow.has_pending_blueprint(state)
        or platform_chat_flow.is_generation_resumable(state)
        or platform_chat_flow.is_generation_complete(state)
        or platform_chat_flow.is_generation_terminal_failure(state)
        or platform_chat_flow.is_handoff_awaiting_n3(state)
    )
    if not resumable:
        result = {"sse": "info", "ok": False, "summary": "There is no build to continue. Draft a platform first."}
        _record(result)
        async for ev in _yield_platform_result(result):
            yield ev
        return
    spends = (
        platform_chat_flow.has_pending_blueprint(state)
        or platform_chat_flow.is_generation_resumable(state)
        or platform_chat_flow.is_generation_terminal_failure(state)
        or (
            platform_chat_flow.is_generation_complete(state)
            and not platform_chat_flow.is_pilot_ready(state)
        )
    )
    if spends:
        try:
            require_remaining(getattr(state, "user_id", None), "generation")
        except TrialLimitExceeded as exc:
            yield _sse_event("error", exc.detail["message"])
            yield _sse_event("done", "")
            return
    result = platform_chat_flow.start_or_resume_coder(state)
    if not result.get("already_running") and not result.get("already_complete"):
        require_within_limit(getattr(state, "user_id", None), "generation")
    _record(result)
    async for ev in _yield_platform_result(result):
        yield ev


def _chat_starts_generation(state: SessionState, action: Optional[FloorAction]) -> bool:
    """True when this request's typed action will start or resume a build."""
    if action in RUN_ACTIONS and require_build_level(state.product_design) is not None:
        return False  # refused before anything starts: no level chosen
    if action is FloorAction.CONFIRM_INTAKE:
        # Confirm on a re-opened intake (after "Continue with new answers")
        # resumes the failed platform; any other Confirm only stores fields.
        return platform_chat_flow.intake_reopened(state)
    if action is FloorAction.APPROVE:
        return platform_chat_flow.has_pending_blueprint(state)
    if action is FloorAction.START_OVER:
        return bool(state.product_design.blueprint) and not platform_chat_flow.has_pending_blueprint(state)
    if action in RUN_ACTIONS:
        return (
            platform_chat_flow.has_pending_blueprint(state)
            or platform_chat_flow.is_generation_resumable(state)
            or platform_chat_flow.is_generation_complete(state)
            or platform_chat_flow.is_generation_terminal_failure(state)
        )
    return False


@router.post("/{session_id}/chat")
async def chat(
    body: ChatMessage,
    state: SessionState = Depends(require_owned_session),
    principal: Principal = Depends(require_api_key),
):
    # Burst throttle before the stream opens (429 is clean here; inside the
    # SSE generator it could only surface as an event). Quotas exempt
    # subscribers; this binds every account.
    require_llm_rate(getattr(state, "user_id", None), "chat")
    if body.vertical is not None:
        # The user's own choice, persisted before the stream reloads the
        # session. An empty value clears it back to "no vertical".
        from ..factory.store_kits import NO_VERTICAL, chosen_vertical

        choice = chosen_vertical(body.vertical)
        state.product_design.vertical = None if choice == NO_VERTICAL else choice
        update_session(state.session_id, state)
    # The declared country/currency, and the stored blueprint kept carrying
    # exactly that pair on EVERY request -- a blueprint drafted mid-chat
    # picks it up before an approve starts the build.
    from ..factory.locale_choice import apply_locale_choice, sync_blueprint_intake

    if body.country is not None or body.currency is not None:
        apply_locale_choice(state.product_design, body.country, body.currency)
        update_session(state.session_id, state)
    elif sync_blueprint_intake(state.product_design):
        update_session(state.session_id, state)
    try:
        action = parse_action(body.action)
    except FloorActionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if _chat_starts_generation(state, action):
        assert_entitled(principal)
    return StreamingResponse(
        _stream_response(state.session_id, body.message, action, body.value),
        media_type="text/event-stream",
    )


@router.get("/{session_id}/chain/preview")
async def preview_chain(state: SessionState = Depends(require_owned_session)):
    if not state.proposed_chain:
        raise HTTPException(status_code=404, detail="No chain proposed yet")
    response = {"chain": state.proposed_chain, "rules": state.extracted_rules}
    if state.chain_quality:
        response["quality"] = state.chain_quality
    return response


@router.post("/{session_id}/chain/approve")
async def approve_chain(
    body: ApproveRequest = ApproveRequest(),
    state: SessionState = Depends(require_owned_session),
):
    session_id = state.session_id
    if not state.proposed_chain:
        raise HTTPException(status_code=400, detail="No chain to approve")

    state.chain_approved = body.approve
    if body.approve:
        state.phase = 4
        state.phase_status = "in_progress"
        if state.extracted_rules:
            try:
                state.container_modified_path = inject_rules(
                    session_id, state.config.domain, state.extracted_rules
                )
                state.rules_injected = True
            except Exception as exc:
                logger.exception("Rule injection failed")
                raise HTTPException(status_code=500, detail=f"Rule injection failed: {exc}")

    state.updated_at = datetime.utcnow()
    update_session(session_id, state)
    return {
        "chain_approved": state.chain_approved,
        "rules_injected": state.rules_injected,
        "container_modified_path": state.container_modified_path,
        "phase": state.phase,
    }
