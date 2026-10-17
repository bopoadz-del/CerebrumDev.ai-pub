"""Factory Floor chat LLM — the door that starts the coding agent.

The Floor used to intercept "approve" with a regex and never ask the chat
model anything. That meant the coding agent was a keyword side-door, not
something the conversation started.

When a factory LLM key is configured, platform chat messages go through
this orchestrator. The model returns a JSON action:

  draft_platform  — park a blueprint (architect LLM still drafts it)
  start_coder     — approve the pending blueprint and launch WRITER
  refine_blueprint — apply a refinement command already understood by
                     platform_chat_flow.apply_refinement (typed op + value)
  reply           — talk, do not start the coder
  ask_user        — ask what the brief leaves open BEFORE drafting (who uses
                    it, documents to answer from, size, automation). The
                    model sees the conversation and the Store inventory;
                    the number of rounds is capped in code.

Kit-configurator vocabulary never enters this path. Exact ``approve`` /
``approved`` (the Approve button) skips the LLM and uses the regex door.
Other approval phrasing still asks the model; regex remains the fallback
when the LLM is unset, returns a soft miss (empty / ``{}``), or fails.

The coding agent still lives only inside WRITER (see
docs/factory/AGENT_IN_THE_KERNELS.md). This module does not move it into
COLLECTOR or TESTER; it only decides *when* WRITER starts.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from app.core.llm_config import get_llm_config
from app.factory import platform_chat_flow
from app.factory.floor_actions import (
    REFINEMENT_ACTIONS,
    FloorAction,
    FloorActionError,
    parse_action,
)
from app.factory.locale_choice import intake_state, shaped_proposal
from app.factory.product_architect import LlmSoftMiss

#: Refinements the model may apply itself. The vertical is the user's typed
#: intake: from the model it is only ever a PROPOSAL (``intake``) the user
#: confirms -- never a refinement that stores.
_MODEL_REFINEMENTS = frozenset(REFINEMENT_ACTIONS - {FloorAction.SET_VERTICAL})
from app.factory.product_architect import _llm_json_call as _architect_llm_json_call

logger = logging.getLogger(__name__)

CHAT_LLM_ENV = "PLATFORM_CHAT_LLM_ENABLED"

_ACTIONS = frozenset(
    {"draft_platform", "start_coder", "refine_blueprint", "reply", "ask_user"}
)

#: How many times the Floor may ask before it must draft. Enforced in
#: ``enforce_elicitation_cap`` -- the model is told the number but is not
#: trusted with it: a chat that can ask forever never builds.
#: How many rounds of questions the chat may ask before it must draft.
#: Two was a cap on curiosity: the chat drafted while the customer was still
#: willing to talk, and the writer then invented what nobody had said (FinOps
#: got no country, no approval limits, and guessed both). What the customer
#: tells us here is free; what the agent assumes costs a rework round or a
#: wrong platform. The customer ends it by saying build now -- not the clock.
MAX_ELICITATION_ROUNDS = 6

#: Conversation the model is shown. The router used to send the current
#: message alone, so the chat could not hold a dialogue even in principle.
#:
#: These were 12 turns capped at 600 characters EACH, and the per-turn cap was the
#: wrong shape. The current message goes in full, so a long brief -- a domain
#: encoding sheet, a spec, a list of the customer's own rules -- was read once and
#: then shredded to its first 600 characters on every turn afterwards. The model
#: then asked again for what it had been told, or drafted on assumptions, while 12
#: lines of chit-chat passed through untouched. The one thing most worth keeping was
#: the only thing being cut.
#:
#: So: a TOTAL budget, spent newest-first, and whole turns dropped rather than every
#: turn mutilated. 120k characters is roughly 30k tokens, well inside the window of
#: the DeepSeek primary and the OpenRouter fallback.
_HISTORY_TURNS = 40
_CONVERSATION_BUDGET_CHARS = 120_000
#: When ONE turn is bigger than the whole budget, keep its head and its TAIL. The
#: end of a long brief is where the asks are ("...and it must handle VAT"), so
#: keeping only the head loses the part that was the point of sending it.
_SINGLE_TURN_TAIL_CHARS = 20_000

#: How much of the owner's question sheet the Floor prompt may carry. Whole
#: questions, as many as fit — never a question cut mid-list, because the model is
#: told to ask them in the sheet's own words and half a question is a different one.
_KIT_QUESTIONS_BUDGET_CHARS = 12_000

_SYSTEM = """You are the Cerebrum Factory Floor chat. Users describe software \
platforms in this conversation. You do not configure kit chains or invent \
block ids. You pick exactly one JSON action.

Actions:
- ask_user: the user described a platform but the brief leaves open things \
that change what gets built. Ask BEFORE drafting. Put at most 3 short \
questions in "message", in plain language, specific to THEIR business — \
never a generic form, never a question the conversation already answers. \
Worth knowing when unsaid: who will use it and roughly how many people / \
which roles; whether they have documents, manuals, price lists or procedures \
the platform should answer from (they can upload them); the size of the \
operation (sites, rooms, vehicles, staff); anything that should happen \
automatically, or a specialist assistant they want; where they operate \
(the country) and the currency they work in -- these decide tax, VAT, \
payroll and regulatory rules, so never assume them for anything that \
touches money; the BUILD LEVEL they want -- prototype (done when the code \
passes), light, pilot, or production (the full acceptance floor) -- which the \
build cannot start without, so ask it whenever the session facts say it is \
not chosen; their own formulas or \
rules of thumb. Keep drawing the brief out while they are still answering: each round goes deeper than the last, never repeats what they have told you, and stops guessing on their behalf. Do NOT stop because the brief reads well -- a platform built on your assumptions is their rework. The customer ends the questions, not you: the moment they say build now / just build it / go / start / skip / you decide / enough, call draft_platform on that turn and ask nothing more. Never \
ask_user when a blueprint is pending or a coding run exists. The session \
facts say how many rounds of questions remain; at zero you must draft.
- draft_platform: the user wants a new platform / product. Set "brief" to a \
complete restatement of what they want, folding in EVERY answer they gave \
in the conversation (users, documents, size, automation, their own rules, \
country and currency). If the platform handles money and the country or \
currency is still unknown, write that in the brief -- never a guess. \
This drafts a blueprint; it does NOT start the coding agent yet. Never draft_platform on continue/resume \
— that would wipe an in-flight run.
- start_coder: launch or resume the coding agent (WRITER). Call it when \
(1) a blueprint is pending AND the user confirms (approve, go ahead, looks \
good, build it, ship it, yes), OR (2) a coding run is in-flight, stalled, \
or interrupted (no RUN_FAILED) and the user says continue / resume / keep \
going, OR (3) the last run FAILED (rework exhausted / TESTER still red) \
and the user asks to continue or try again — that RESUMES the platform \
on its own branch at the failing phase with a fresh rework budget (only \
the user's Start over control makes a fresh workspace). Do not require a \
pending unapproved \
blueprint to resume. If the last run already succeeded AND the product is \
pilot-ready, do NOT call start_coder — reply that it finished. If \
code-phase 5/5 succeeded but it is NOT pilot-ready, continue/resume MUST \
call start_coder to open a pilot cycle. Do not call a code-cycle SUCCESS \
"finished" or "download ready". After a FAILED run a new brief MUST call \
draft_platform (new product); do not treat that brief as a resume.
- refine_blueprint: the user wants to change the pending blueprint. Set \
"refine" to {"op": ONE OF add_capability | remove_capability | rename | \
list_capabilities, "value": the capability id or the new product name; "" \
for list_capabilities}. The vertical, country, currency and build level are \
NOT refinements: propose them in "intake".
- On EVERY action, "intake" carries what the user SAID in this conversation \
about their vertical (only if they named one), country (2-letter code), \
currency (3-letter code) and build level (only one of prototype | light | \
pilot | production, and only if they chose it in so many words -- never \
from how their business sounds). Leave a field "" when they have not said \
it. The Floor shows your proposal with Confirm / Change buttons; it stores \
nothing until the user presses Confirm.
- reply: questions, chit-chat, or a pending blueprint with no confirmation. \
Set "message" to a short grounded reply. Tell them they can confirm to start \
the coding agent. Do not pretend a build started.

The session facts carry the STORE: ready-made blocks, CONNECTORS and MCP \
parts that are attachable, parts that are in the store but NOT cleared for \
factory builds, and the KITS (deep, certified domain packs). Be honest about \
all of it and never invent an id.
- Kits: the user chooses their vertical on the Floor; the factory resolves \
the kit from that choice and tells the user itself whether a ready kit \
exists. Do not pick a kit or a vertical for them, do not repeat the kit \
notice, and never imply a kit exists when it does not.
- Connectors and MCP: ask which outside systems they already use (accounting, \
booking / PMS, ERP, drives, email, messaging) when the brief does not say. On \
draft_platform, put the ids of attachable CONNECTORS / MCP parts the platform \
needs in "connectors" — only ids from those two lists. Put every system the \
user named that is NOT attachable (not in the store, or not cleared) in \
"missing_connectors" by its plain name, and tell the user plainly that it \
will ship as a marked placeholder until it is built — never as working.

Return ONLY JSON: {"action": "...", "brief": "", "refine": {"op": "", "value": ""}, "message": "", "connectors": [], "missing_connectors": [], "intake": {"vertical": "", "country": "", "currency": "", "build_level": ""}}.
"""


def _llm_json_call(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """Floor chat JSON call — uses ``CEREBRUM_CHAT_LLM_*``, never Cursor completions."""
    return _architect_llm_json_call(messages, use_chat_config=True)


def chat_llm_enabled() -> bool:
    """Route Floor chat through the LLM when factory credentials exist.

    Explicit ``PLATFORM_CHAT_LLM_ENABLED=0`` keeps regex-only routing.
    Explicit ``1`` forces the LLM path (call failures still fall back).
    Unset means: orchestrate whenever a factory API key is configured.
    """
    raw = os.getenv(CHAT_LLM_ENV, "").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    cfg = get_llm_config()
    if cfg.get("error") or cfg.get("mock"):
        return False
    return bool(cfg.get("api_key"))


def should_orchestrate(state: Any, message: str) -> bool:
    """True when this message should be decided by the Floor chat LLM."""
    if not chat_llm_enabled():
        return False
    if not (message or "").strip():
        return False
    # Typed Floor actions (Approve, Continue, the feature-list editor ...)
    # are dispatched before this is asked; only free text reaches the model.
    return True


def _intake_facts(pd: Any) -> str:
    """The user's typed intake as the model may describe it: declared values
    are the user's; anything unset is to be ASKED, never assumed."""
    if pd is None:
        return ""
    declared = intake_state(pd)["declared"]
    shown = ", ".join(
        f"{k}={v}" if v else f"{k}=NOT DECLARED"
        for k, v in declared.items()
    )
    line = f"Declared intake: {shown}."
    if not declared.get("build_level"):
        line += " The build cannot start until the user chooses a build level."
    return line


def _session_facts(state: Any) -> str:
    pd = getattr(state, "product_design", None)
    if not pd or not getattr(pd, "blueprint", None):
        return "Session: no pending blueprint. start_coder is forbidden. " + _intake_facts(pd)
    bp = pd.blueprint or {}
    caps = bp.get("capabilities") or []
    cap_ids = ", ".join(str(c.get("id") or "") for c in caps if isinstance(c, dict))
    pending = not bool(pd.blueprint_approved)
    lines = [
        f"Pending blueprint: {'yes' if pending else 'no'}.",
        f"Product: {bp.get('product_name')} (vertical={bp.get('vertical')}).",
        f"Capabilities: {cap_ids or '(none)'}.",
        _intake_facts(pd),
    ]
    if getattr(pd, "generation", None):
        gen = pd.generation or {}
        lines.append(f"Last generation: {gen.get('product_id')}.")
        if gen.get("inputs_hash"):
            lines.append(f"Blueprint hash: {str(gen.get('inputs_hash'))[:12]}.")
        if gen.get("output_dir"):
            lines.append(f"Output dir: {gen.get('output_dir')}.")
        if gen.get("phases_done") is not None:
            lines.append(f"Phases done: {gen.get('phases_done')}.")
    if platform_chat_flow.is_pilot_ready(state):
        lines.append(
            "Product is pilot-ready (Store-green). start_coder is forbidden — "
            "tell the user it already finished; do not start a new product."
        )
    elif platform_chat_flow.is_handoff_awaiting_n3(state):
        lines.append(
            "Cli-pivot handed off to N3 (HANDOFF_TO_N3). continue / "
            "start_coder MUST ingest the cerebrum-builds store-gate "
            "12/12 commit status. Do NOT draft a new platform, do NOT "
            "re-enter WRITER, and do NOT launch another Background Agent."
        )
    elif platform_chat_flow.is_generation_complete(state):
        lines.append(
            "Code-phase 5/5 SUCCEEDED but the platform is NOT pilot-ready "
            "(pytest -m pilot / Store ops still open). continue/resume MUST "
            "call start_coder — that opens a pilot cycle on the same hash. "
            "Do not draft a new platform."
        )
    elif platform_chat_flow.is_generation_terminal_failure(state):
        lines.append(
            "Last coding run FAILED (rework exhausted or TESTER still red). "
            "Do NOT say 'same blueprint hash — not starting over'. A new "
            "platform brief MUST call draft_platform. continue / try again / "
            "start_coder RESUMES this platform on its own branch at the "
            "failing phase with a reset rework budget; only the user's Start "
            "over control makes a FRESH workspace (the old head is kept as an "
            "archive tag)."
        )
    elif platform_chat_flow.is_generation_resumable(state):
        point = platform_chat_flow._ledger_resume_point(state) or "the last phase"
        lines.append(
            f"Coding run is incomplete (resume at {point}). "
            "continue/resume MUST call start_coder — that resumes the same "
            "hash. Do not draft a new platform. A pending blueprint is not required."
        )
    elif pending:
        lines.append("The user must confirm before you call start_coder.")
    else:
        lines.append(
            "No pending blueprint and no interrupted coding run. "
            "start_coder is forbidden."
        )
    return " ".join(lines)


def _conversation(state: Any, message: str) -> str:
    """The recent dialogue, oldest first, without the message being decided.

    chat.py appends the user turn before routing, so the current message is
    usually the last history entry -- drop it rather than show it twice.
    """
    history = list(getattr(state, "chat_history", None) or [])
    if history and isinstance(history[-1], dict):
        last = history[-1]
        if (
            last.get("role") == "user"
            and str(last.get("content") or "").strip() == (message or "").strip()
        ):
            history = history[:-1]
    # Newest first, spending a total budget: a long turn survives whole and an old
    # turn is dropped whole. Nothing is shredded in the middle, because a brief cut
    # mid-sentence reads to the model as a brief that said less than it did.
    rendered: List[str] = []
    spent = 0
    dropped = 0
    for turn in reversed(history[-_HISTORY_TURNS:]):
        if not isinstance(turn, dict):
            continue
        text = str(turn.get("content") or "").strip()
        if not text:
            continue
        who = "User" if turn.get("role") == "user" else "Floor"
        if spent + len(text) > _CONVERSATION_BUDGET_CHARS:
            remaining = _CONVERSATION_BUDGET_CHARS - spent
            # Only the NEWEST turn is worth keeping partially; once the budget is
            # this close to spent, older turns are dropped whole and counted.
            if not rendered and remaining > _SINGLE_TURN_TAIL_CHARS * 2:
                head = text[: remaining - _SINGLE_TURN_TAIL_CHARS]
                tail = text[-_SINGLE_TURN_TAIL_CHARS:]
                cut = len(text) - len(head) - len(tail)
                rendered.append(
                    f"{who}: {head}\n[... {cut} characters elided from the middle of "
                    f"this turn; the start and the end are verbatim ...]\n{tail}"
                )
                spent = _CONVERSATION_BUDGET_CHARS
                continue
            dropped += 1
            continue
        rendered.append(f"{who}: {text}")
        spent += len(text)

    lines = list(reversed(rendered))
    if dropped:
        # Said, not silent: the model must not treat a trimmed conversation as the
        # whole of what it was told.
        lines.insert(0, f"[{dropped} earlier turn(s) dropped to fit the context "
                        f"budget — ask rather than assume what they said]")
    return "\n".join(lines) or "(this is the first message)"


def _store_inventory() -> str:
    """The Store as the chat may describe it -- so it can be honest."""
    try:
        from app.factory.store_catalog import render_for_chat, store_catalog

        return render_for_chat(store_catalog())
    except Exception:  # noqa: BLE001 -- inventory is context, never a blocker
        logger.warning("Floor chat: Store inventory unavailable", exc_info=True)
        return (
            "STORE INVENTORY: unavailable right now — do not claim any "
            "ready-made part, connector or kit."
        )



def _reasoning_kit_facts(state: Any) -> str:
    """The chosen kit's OPEN questions, for the model to ask from.

    The Floor already has a bounded number of question rounds. What it did not
    have is anything to ask ABOUT: each reasoning kit ships with every figure
    empty and each empty figure carrying the question that fills it, and the model
    could not see them. So they are listed here, read from the kit on disk -- the
    model asks them, it does not invent them.

    Two sources, in this order. Where the kit carries the DOMAIN OWNER'S OWN
    question sheet (``questions.yaml``), that is what the model asks from, in the
    owner's words, with the owner's [GATE] / [GAP] mark and the fields that domain
    requires with an answer. Where it does not, the per-quantity questions derived
    from the manifest are the fallback AND THE PROMPT SAYS THEY ARE DERIVED -- a
    model that presents "what is the rate?" as the domain's own question invites an
    answer that no rule can then use.

    Unanswered is not a blocker. The platform is built either way, the kernel
    refuses anything needing an unanswered figure and names the question, and the
    operator answers the rest later through /v1/reasoning/pending. So the model
    should ask the few that change the DESIGN and leave the operational ones to
    the platform.
    """
    try:
        from app.factory.build import reasoning_socket
        from app.factory.blocks_source import resolve_blocks_root

        pd = getattr(state, "product_design", None)
        blueprint = getattr(pd, "blueprint", None) if pd else None
        if blueprint is None:
            return (
                "REASONING KIT: none matched for this vertical yet — do not claim "
                "the platform will gate its figures."
            )
        # Resolve the Store BEFORE the kit, and pass it in. This leg already
        # resolved it two lines further down for every kit it recognised, so the
        # order costs nothing and buys the honest answer: a kit the Store
        # publishes but the alias table has no entry for was reported here as
        # "none matched for this vertical", which is a claim about the domain
        # made from a stale copy of the Store's kit list.
        root = resolve_blocks_root()
        if root is None:
            # Which kit serves a vertical is the Store's declaration; with no
            # Store the honest answer is that it cannot be read, not "none".
            return (
                "REASONING KIT: unknown (Store unreachable) — do not claim the "
                "platform will gate its figures."
            )
        kit = reasoning_socket.kit_for_vertical(blueprint, store_root=root)
        if not kit:
            return (
                "REASONING KIT: none matched for this vertical yet — do not claim "
                "the platform will gate its figures."
            )
        import pathlib as _pathlib

        import yaml as _yaml

        kit_dir = _pathlib.Path(root) / "app" / "blocks" / kit
        manifest = _yaml.safe_load(
            (kit_dir / "manifest.yaml").read_text(encoding="utf-8")
        ) or {}

        # The kit's OWN figure register, where it has one. Reported FIRST because
        # it is the only source that can already hold answers: one of the six is
        # filled in from a completed encoding sheet, and telling the model to go
        # and ask for figures a platform already holds wastes a bounded question
        # round and invites the user to restate what is on file.
        register_note = ""
        register_path = kit_dir / "design_basis.yaml"
        if register_path.is_file():
            register = _yaml.safe_load(register_path.read_text(encoding="utf-8")) or {}
            block = next((n for n in ("design_basis", "operating_basis")
                          if n in register), None)
            entries = (register.get(block) or {}) if block else {}
            answered = [n for n, spec in entries.items()
                        if (spec or {}).get("value") is not None]
            still_open = [n for n in entries if n not in answered]
            if answered:
                register_note = (
                    f" This kit ALREADY HOLDS {len(answered)} of {len(entries)} figures "
                    f"in its own register for "
                    f"{register.get('facility') or 'its declared scope'} "
                    f"({register.get('scope') or 'scope not stated'}) — do not ask for "
                    f"those again. Still open: {', '.join(sorted(still_open)) or 'none'}."
                )
            else:
                register_note = (
                    f" This kit declares a figure register of {len(entries)} figures and "
                    f"EVERY ONE IS EMPTY — no interview has run. Nothing needing one of "
                    f"them can be answered yet."
                )

        # The domain owner's own question sheet, where one exists. It beats the
        # derived per-quantity questions below, which asked "what is the rate?" --
        # one number for a whole domain. The sheet asks for the rate per package,
        # and marks each question [GATE] (blocks) or [GAP] (worth having). The
        # model asks from the sheet; it does not paraphrase it.
        sheet_path = kit_dir / "questions.yaml"
        if sheet_path.is_file():
            sheet = _yaml.safe_load(sheet_path.read_text(encoding="utf-8")) or {}
            questions = [q for q in (sheet.get("questions") or []) if isinstance(q, dict)]
            # UNMARKED gates: a question whose class cannot be read must block.
            gating = [q for q in questions if q.get("gate") is not False]
            fields = [
                str(f).strip().lower().replace(" ", "_")
                for f in (sheet.get("answer_format") or ())
                if str(f).strip().lower() != "value"
            ]
            # The owner's questions go in WHOLE. This used to cap each at 220
            # characters, and the questions worth asking are the long ones: fit-out's
            # B.2 ("your rate per package — partitions (plasterboard, glazed,
            # demountable), ceilings (grid, plasterboard/feature), raised floor,
            # ...") runs past 500, so the model was told to ask, verbatim, half a
            # question. A total budget instead, so nothing is asked in fragments.
            listed_parts: List[str] = []
            spent = 0
            for question in gating:
                text = str(question.get("text") or "").strip()
                entry = f"[{question.get('id')}] {text}"
                if spent + len(entry) > _KIT_QUESTIONS_BUDGET_CHARS and listed_parts:
                    break
                listed_parts.append(entry)
                spent += len(entry)
            listed = "; ".join(listed_parts)
            if len(listed_parts) < len(gating):
                listed += (f"; [and {len(gating) - len(listed_parts)} more gating "
                           f"questions in the sheet — ask for them by id]")
            return (
                f"REASONING KIT: {kit}. It carries the DOMAIN OWNER'S OWN question "
                f"sheet ({sheet.get('source_document') or 'questions.yaml'}): "
                f"{len(questions)} questions, {len(gating)} of them gating. Every "
                f"answer must arrive with {', '.join(fields)} — an answer short of "
                f"any of those cannot be cited and will be refused. Ask these in the "
                f"sheet's own words, never a paraphrase, and ask ONLY the ones that "
                f"change the DESIGN; the rest are operational and the platform "
                f"collects them after the build at /v1/reasoning/interview. The first "
                f"gating questions are: {listed}. An unanswered question is not a "
                f"blocker and not a stub: the platform refuses anything needing it and "
                f"names the question, so NEVER invent a value to fill one, and never "
                f"tell the user a figure is in hand because the question was asked."
                + register_note
            )

        open_questions = [
            str((entry or {}).get("question") or name)
            for name, entry in (manifest.get("figures") or {}).items()
            if not isinstance(entry, dict) or entry.get("value") is None
        ]
        declared = manifest.get("figures")
        if not declared:
            # Said plainly: an absent question list is NOT "all answered". The
            # first version reported "every figure already answered" for a kit
            # that declared no figures at all, which is the most misleading thing
            # it could have said.
            if register_note:
                return f"REASONING KIT: {kit}.{register_note}"
            return (
                f"REASONING KIT: {kit} — it declares NO question list yet, so the "
                f"platform will refuse every figure it needs. Do not claim it can "
                f"answer any of them."
            )
        if not open_questions:
            return f"REASONING KIT: {kit} — every declared figure is answered."
        listed = "; ".join(open_questions[:12])
        return (
            f"REASONING KIT: {kit}, with {len(open_questions)} unanswered figure(s). "
            f"This kit has NO owner question sheet, so the questions below are DERIVED "
            f"from its quantity names and are not the domain owner's own wording — say "
            f"so if the user asks where they come from. "
            f"These are the questions the kit itself asks: {listed}. Ask only the ones "
            f"that change the DESIGN; the rest are operational and the platform "
            f"collects them later at /v1/reasoning/pending. An unanswered figure is "
            f"not a blocker — the platform refuses anything needing it and names the "
            f"question, so never invent a value to fill one." + register_note
        )
    except Exception:  # noqa: BLE001 -- kit facts are context, never a blocker
        logger.warning("Floor chat: reasoning-kit questions unavailable", exc_info=True)
        return "REASONING KIT: questions unavailable right now."


def _elicitation_facts(state: Any) -> str:
    pd = getattr(state, "product_design", None)
    asked = int(getattr(pd, "elicitation_rounds", 0) or 0)
    left = max(0, MAX_ELICITATION_ROUNDS - asked)
    if left == 0:
        return "Question rounds remaining: 0 — ask_user is forbidden; draft now."
    return f"Question rounds remaining: {left}."


def _str_list(raw: Any) -> List[str]:
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if isinstance(x, (str, int)) and str(x).strip()][:12]


def decide(state: Any, message: str) -> Dict[str, Any]:
    """Ask the factory LLM which Floor action to take. Raises on failure."""
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                _session_facts(state)
                + " "
                + _elicitation_facts(state)
                + "\n"
                + _store_inventory()
                + "\n\nConversation so far:\n"
                + _conversation(state, message)
                + "\n\nUser message:\n"
                + (message or "")
            ),
        },
    ]
    data = _llm_json_call(messages)
    if not isinstance(data, dict):
        raise ValueError("Floor chat LLM returned a non-object")
    action = str(data.get("action") or "").strip()
    if not action:
        raise LlmSoftMiss("Floor chat LLM returned empty action")
    if action not in _ACTIONS:
        raise ValueError(f"Floor chat LLM returned unknown action {action!r}")
    return {
        "action": action,
        "brief": str(data.get("brief") or "").strip(),
        "refine": _refine_of(data.get("refine")),
        "message": str(data.get("message") or "").strip(),
        "connectors": _str_list(data.get("connectors")),
        "missing_connectors": _str_list(data.get("missing_connectors")),
        "intake": data.get("intake") if isinstance(data.get("intake"), dict) else {},
    }


def _refine_of(raw: Any) -> Dict[str, str]:
    """The model's typed refinement: {"op", "value"} as fields, never text."""
    if not isinstance(raw, dict):
        return {"op": "", "value": ""}
    return {
        "op": str(raw.get("op") or "").strip(),
        "value": str(raw.get("value") or "").strip(),
    }


def try_decide_with_reason(state: Any, message: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """``(decision, refusal_kind)``: the decision, or None plus WHY no model
    answered -- the model ladder's typed kind (``payment_refused``,
    ``rate_limited``, ``unavailable``, ``bad_request``, ``not_configured``),
    or None for a soft miss / non-provider failure.

    Sentry LoggingIntegration turns ``logger.exception`` into error events.
    Empty OpenRouter completions are expected; do not page on them.
    """
    from app.core.model_ladder import ModelUnavailable

    try:
        return decide(state, message), None
    except LlmSoftMiss as exc:
        logger.warning("Floor chat LLM miss; falling back to regex routing: %s", exc)
        return None, None
    except ModelUnavailable as exc:
        logger.warning("Floor chat LLM refused by every provider (%s): %s", exc.kind, exc)
        return None, exc.kind
    except Exception:
        logger.exception("Floor chat LLM failed; falling back to regex routing")
        return None, None


def try_decide(state: Any, message: str) -> Optional[Dict[str, Any]]:
    """Decide, or None on miss/failure (see :func:`try_decide_with_reason`)."""
    return try_decide_with_reason(state, message)[0]


def _elicitation_allowed(state: Any) -> bool:
    """Questions belong before the first draft, and only a bounded number."""
    pd = getattr(state, "product_design", None)
    if pd is None:
        return False
    if getattr(pd, "blueprint", None) or getattr(pd, "generation", None):
        return False
    return int(getattr(pd, "elicitation_rounds", 0) or 0) < MAX_ELICITATION_ROUNDS


def enforce_elicitation_cap(
    decision: Dict[str, Any], state: Any, message: str
) -> Dict[str, Any]:
    """An ask_user the session cannot afford becomes a draft.

    The model is told the limit; this is what makes it true. Without it a
    model that keeps finding one more question strands the user in a
    conversation that never reaches a blueprint.
    """
    if decision.get("action") != "ask_user":
        return decision
    if _elicitation_allowed(state) and decision.get("message"):
        return decision
    return {
        "action": "draft_platform",
        "brief": decision.get("brief") or "",
        "refine": {"op": "", "value": ""},
        "message": "",
        "connectors": list(decision.get("connectors") or []),
        "missing_connectors": list(decision.get("missing_connectors") or []),
        "intake": dict(decision.get("intake") or {}),
        "coerced": True,
    }


def _consolidated_brief(state: Any, message: str, model_brief: str) -> str:
    """The brief the architect drafts from: the model's restatement PLUS
    the user's own words.

    The restatement is a summary, and a summary can drop the one answer
    that mattered. The verbatim turns ride along so nothing the user said
    during the questions is lost to paraphrase.
    """
    pd = getattr(state, "product_design", None)
    said = [s for s in (getattr(pd, "elicitation_turns", None) or []) if s.strip()]
    if not said:
        return model_brief or message
    if (message or "").strip() and message.strip() not in said:
        said = [*said, message.strip()]
    head = (model_brief or said[0]).strip()
    return (
        head
        + "\n\nIn the user's own words:\n"
        + "\n".join(f"- {s}" for s in said)
    )


def _attach_connectors(
    state: Any, result: Dict[str, Any], chosen: List[str], missing: List[str]
) -> None:
    """Bind the connectors the conversation settled on to the drafted blueprint.

    An attachable Store part becomes a REUSE capability, so CLONER really
    clones it. A system the Store cannot supply goes to
    ``blueprint.connectors``, which the generator emits as a marked
    ``not_implemented`` placeholder -- present and honest, never working by
    implication. The model's ids are not trusted: anything that is not an
    attachable part in the catalog is treated as missing.
    """
    if not chosen and not missing:
        return
    from app.factory.blueprint import ProductBlueprint, connector_slug
    from app.factory.store_catalog import offerable_connector_ids, store_catalog

    pd = state.product_design
    if not isinstance(pd.blueprint, dict):
        return
    try:
        offerable = set(offerable_connector_ids(store_catalog()))
    except Exception:  # noqa: BLE001
        logger.warning("Floor chat: catalog unavailable; no connectors attached", exc_info=True)
        return
    attach = [c for c in dict.fromkeys(chosen) if c in offerable]
    placeholders = [*missing, *[c for c in chosen if c not in offerable]]
    bp = dict(pd.blueprint)
    caps = [dict(c) for c in (bp.get("capabilities") or []) if isinstance(c, dict)]
    # A connector a drafted capability declared it calls, that the Store
    # cannot supply, is a placeholder too -- so the capability's declaration
    # and the blueprint's placeholder list name the same systems.
    declared = [c for cap in caps for c in (cap.get("connectors") or [])]
    slugs = list(
        dict.fromkeys(
            s
            for s in (connector_slug(m) for m in [*placeholders, *declared])
            if s and s not in offerable
        )
    )

    bound = {b for c in caps for b in (c.get("block_ids") or [])}
    added: List[str] = []
    for cid in attach:
        if cid in bound:
            continue
        caps.append(platform_chat_flow._capability_for_id(cid, sorted(offerable)))
        added.append(cid)
    bp["capabilities"] = caps
    bp["connectors"] = list(dict.fromkeys([*(bp.get("connectors") or []), *slugs]))
    try:
        validated = ProductBlueprint.model_validate(bp)
    except Exception:  # noqa: BLE001 -- a bad attach must not cost the draft
        logger.warning("Floor chat: connector attach rejected by the blueprint", exc_info=True)
        return
    pd.blueprint = validated.model_dump(mode="json")
    result["blueprint"] = pd.blueprint
    try:
        from app.factory.blueprint import blueprint_to_yaml

        result["yaml"] = blueprint_to_yaml(validated)
    except Exception:  # noqa: BLE001
        pass
    notes: List[str] = []
    if added:
        notes.append("Connectors from the store: " + ", ".join(added) + ".")
    if slugs:
        notes.append(
            "Not available ready-made, so shipped as marked placeholders until built: "
            + ", ".join(slugs)
            + "."
        )
    if notes and isinstance(result.get("summary"), str):
        result["summary"] = result["summary"].rstrip() + " " + " ".join(notes)


#: Said by the factory, not left to the model: a prose instruction to mention
#: it was simply ignored in the first live conversation.
KIT_NOTICE = (
    "Heads up: we don't have a top-notch kit for this in the store today, but "
    "we can build a simple one for you now \u2014 it will take longer and may cost "
    "more than a platform built on a ready kit."
)

#: Said when a Store kit DOES serve the vertical the user chose, but the
#: operator has not declared it ready (certified) for domain depth. Absent and
#: uncertified are different truths (live 2026-09-30: an automotive brief was
#: told "we don't have a kit" while an uncertified automotive kit sat on the
#: shelf).
UNCERTIFIED_KIT_NOTICE = (
    "Heads up: the store holds an uncertified '%s' kit \u2014 its domain depth "
    'is not verified yet. Say "build on %s" to compose on it anyway, or '
    "continue and we build a simple one fresh \u2014 that will take longer and "
    "may cost more than a certified ready kit."
)


def _kit_notice(state: Any) -> str:
    """The once-per-session notice, decided by the vertical the USER chose.

    The kit is never guessed -- not by the model from the user's business
    description, not by scanning the message for kit names. The user's
    vertical (``product_design.vertical``) is resolved against the Store's own
    kit declarations: a ready kit serves it -> no notice; a kit serves it but
    is not declared ready -> the uncertified notice naming that kit; nothing
    serves it (or no vertical was chosen) -> the no-ready-kit notice.
    """
    pd = getattr(state, "product_design", None)
    if pd is None or getattr(pd, "kit_notice_given", False):
        return ""
    try:
        from app.factory.store_kits import (
            NO_VERTICAL,
            SERVED,
            chosen_vertical,
            domain_kits,
            resolve_vertical,
        )

        kits = domain_kits()
    except Exception:  # noqa: BLE001 -- never claim or deny a kit we cannot see
        logger.warning("Floor chat: kit shelf unavailable; no kit notice", exc_info=True)
        return ""
    choice = chosen_vertical(getattr(pd, "vertical", None))
    status, kit, _reason = (
        resolve_vertical(choice, kits) if choice != NO_VERTICAL else ("", None, "")
    )
    if status == SERVED and kit and kits.get(kit, {}).get("build_ready") is True:
        return ""
    pd.kit_notice_given = True
    if status == SERVED and kit:
        return UNCERTIFIED_KIT_NOTICE % (kit, kit)
    return KIT_NOTICE


def record_intake_proposal(state: Any, decision: Dict[str, Any]) -> None:
    """Keep what the model proposed for the user's intake, as a PROPOSAL.

    The typed fields (vertical, country, currency, build level) are not
    touched here: only the user's typed ``confirm_intake`` stores them. A new
    proposal replaces the previous one; an empty one leaves it in place.
    """
    pd = getattr(state, "product_design", None)
    if pd is None:
        return
    raw = dict(decision.get("intake") or {})
    refine = decision.get("refine") or {}
    if refine.get("op") == FloorAction.SET_VERTICAL.value and refine.get("value"):
        raw.setdefault("vertical", refine.get("value"))
    proposal = shaped_proposal(raw)
    if proposal:
        pd.intake_proposal = proposal


def apply_decision(state: Any, message: str, decision: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a decided Floor action against the session product state, and
    hand the Floor the intake line (declared fields + any pending proposal)."""
    record_intake_proposal(state, decision)
    result = _apply_decision(state, message, decision)
    pd = getattr(state, "product_design", None)
    if pd is not None and isinstance(result, dict):
        result["intake"] = intake_state(pd)
    return result


def _apply_decision(state: Any, message: str, decision: Dict[str, Any]) -> Dict[str, Any]:
    action = decision.get("action")

    if action == "start_coder":
        # No approve -> no build. A build starts only from the TYPED Floor
        # Approve / Continue action; free text (however the model reads it)
        # never spends a generation. Point the user at the control.
        pending = platform_chat_flow.has_pending_blueprint(state)
        return {
            "sse": "info",
            "ok": True,
            "summary": (
                "Press Approve to start the coding agent on this feature list."
                if pending
                else "Press Continue to resume the build."
            ),
            "stream_delta": True,
            "awaiting_action": "approve" if pending else "continue",
        }

    if action == "ask_user":
        pd = state.product_design
        if (message or "").strip():
            pd.elicitation_turns = [*pd.elicitation_turns, message.strip()]
        pd.elicitation_rounds = int(pd.elicitation_rounds or 0) + 1
        notice = _kit_notice(state)
        return {
            "sse": "info",
            "ok": True,
            "summary": (notice + " " if notice else "") + (decision.get("message") or ""),
            "stream_delta": True,
            "elicitation": True,
        }

    if action == "draft_platform":
        brief = _consolidated_brief(state, message, decision.get("brief") or "")
        result = platform_chat_flow.draft_from_chat(state, brief)
        state.product_design.elicitation_turns = []
        state.product_design.elicitation_rounds = 0
        _attach_connectors(
            state,
            result,
            list(decision.get("connectors") or []),
            list(decision.get("missing_connectors") or []),
        )
        # The model's own words ride along with the draft: that is where it
        # says "no top-notch kit for this, a simple one takes longer and may
        # cost more". Dropping them made a draft look like a silent switch.
        said = " ".join(
            s for s in (_kit_notice(state), str(decision.get("message") or "").strip()) if s
        )
        if said and isinstance(result.get("summary"), str):
            result["summary"] = said + " " + result["summary"]
        result["sse"] = "blueprint"
        result["stream_delta"] = False
        return result

    if action == "refine_blueprint":
        refine = decision.get("refine") or {}
        refined = None
        try:
            op = parse_action(refine.get("op"))
            if op is FloorAction.SET_VERTICAL:
                # Proposed (record_intake_proposal), not applied: the user
                # confirms the vertical on the Floor.
                return {
                    "sse": "info",
                    "ok": True,
                    "summary": "Confirm the proposed vertical on the Floor to use it.",
                    "stream_delta": True,
                }
            if op in _MODEL_REFINEMENTS:
                refined = platform_chat_flow.apply_refinement(state, op, refine.get("value"))
        except FloorActionError:
            refined = None
        if refined:
            refined["sse"] = "blueprint" if refined.get("refined") else "info"
            refined["stream_delta"] = not refined.get("refined")
            return refined
        return {
            "sse": "info",
            "ok": True,
            "summary": (
                "I could not apply that change. Use the feature-list editor, "
                "rename or build-level controls on the Floor, or Approve to build."
            ),
            "stream_delta": True,
        }

    summary = decision.get("message") or (
        "A blueprint is drafted. Confirm to start the coding agent, or refine it."
        if platform_chat_flow.has_pending_blueprint(state)
        else "Describe the platform you want and I will draft a blueprint."
    )
    return {"sse": "info", "ok": True, "summary": summary, "stream_delta": True}


def try_handle(state: Any, message: str) -> Optional[Dict[str, Any]]:
    """Decide + apply. None means no chat LLM answered (the caller then
    points the user at the Floor controls -- there is no prose fallback)."""
    if not should_orchestrate(state, message):
        return None
    decision = try_decide(state, message)
    if decision is None:
        return None
    decision = enforce_elicitation_cap(decision, state, message)
    return apply_decision(state, message, decision)
