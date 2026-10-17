"""Typed Floor actions: what the user asked the Factory to DO.

The Floor's controls (Approve, Continue, Run pilot, Draft, the feature-list
editor, rename, vertical, the build level, Confirm on a proposed intake) send
an explicit ``action`` -- and a ``value`` where the action takes one -- with
the chat request. The Factory dispatches on that field. It never reads the
user's words to decide an action: free-typed text goes to the Floor chat LLM,
whose answer is itself a typed decision, and with no LLM configured free text
gets an honest pointer to the controls. (The regexes this replaces matched
"approve", "go ahead", "rename to ...", "make it a prototype" ... in the
message and decided by them.)
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from app.factory.build.build_level import BuildLevel, BuildLevelError, parse_build_level


class FloorAction(str, Enum):
    """Every action a Floor control can send."""

    APPROVE = "approve"
    CONTINUE = "continue"
    RUN_PILOT = "run_pilot"
    DRAFT = "draft"
    ADD_CAPABILITY = "add_capability"
    REMOVE_CAPABILITY = "remove_capability"
    RENAME = "rename"
    SET_VERTICAL = "set_vertical"
    #: Change on the intake line: puts the level the user picked (prototype |
    #: light | pilot | production) on the proposal. Stores nothing by itself.
    SET_BUILD_LEVEL = "set_build_level"
    #: The user pressed Confirm on the intake the chat proposed (vertical,
    #: country, currency, build level). The ONLY way a proposal becomes the
    #: session's typed fields; the model's proposal alone stores nothing.
    CONFIRM_INTAKE = "confirm_intake"
    LIST_CAPABILITIES = "list_capabilities"
    #: The ONLY action that gives a platform a fresh workspace. It first tags
    #: the platform's current head ``archive/<platform_id>/<date>`` and records
    #: it in the ledger; nothing is deleted. Continue (and every other run
    #: action) resumes the platform's one branch instead.
    START_OVER = "start_over"
    #: On a FAILED build: take the platform as it is -- the as-is export, its
    #: MANIFEST naming FAILED(gate, check, finding) and ``certified: false``.
    TAKE_COPY = "take_copy"
    #: On a FAILED build: re-open the intake (country, currency, build level,
    #: vertical) for this platform. Nothing runs until the typed
    #: ``confirm_intake``, which then resumes the SAME branch of record.
    CONTINUE_WITH_INTAKE = "continue_with_intake"
    #: The legacy kit-chain configurator (chain suggestion over the user's
    #: documents) -- reached by this typed action, never by kit vocabulary.
    CHAIN = "chain"


#: Actions that edit a pending blueprint (feature list, name, vertical).
REFINEMENT_ACTIONS = frozenset(
    {
        FloorAction.ADD_CAPABILITY,
        FloorAction.REMOVE_CAPABILITY,
        FloorAction.RENAME,
        FloorAction.SET_VERTICAL,
        FloorAction.LIST_CAPABILITIES,
    }
)

#: Actions that record the user's intake (the session's typed fields). They
#: work before any blueprint exists -- the chat asks for them up front.
INTAKE_ACTIONS = frozenset({FloorAction.SET_BUILD_LEVEL, FloorAction.CONFIRM_INTAKE})

#: Actions that need a value (a capability id, a name, a vertical, a level).
VALUE_REQUIRED = frozenset(
    (REFINEMENT_ACTIONS - {FloorAction.LIST_CAPABILITIES}) | {FloorAction.SET_BUILD_LEVEL}
)

#: The three choices a FAILED build offers -- typed only, never read from chat.
FAILURE_ACTIONS = frozenset(
    {FloorAction.TAKE_COPY, FloorAction.CONTINUE_WITH_INTAKE, FloorAction.START_OVER}
)

#: Actions that start or resume the coding agent.
RUN_ACTIONS = frozenset(
    {FloorAction.APPROVE, FloorAction.CONTINUE, FloorAction.RUN_PILOT, FloorAction.START_OVER}
)


#: The committed, shared action spec (repo-relative). The SPA, its browser e2e
#: and scripts/post_deploy_smoke.py all build their typed requests from this
#: one file; tests/factory/test_floor_action_spec.py fails when it differs
#: from ``action_spec()`` -- the set this module accepts.
ACTION_SPEC_PATH = "frontend/src/api/floor_actions.json"
ACTION_SPEC_SCHEMA = "floor_actions.v1"


def action_spec() -> dict:
    """Every action this module accepts, with the shape of its ``value``:
    ``null`` (no value), ``"string"`` (free text: an id, a name, a vertical),
    or ``{"one_of": [...]}`` (a closed set)."""

    def value_shape(action: "FloorAction"):
        if action is FloorAction.SET_BUILD_LEVEL:
            return {"one_of": [level.value for level in BuildLevel]}
        if action in VALUE_REQUIRED:
            return "string"
        return None

    return {
        "schema": ACTION_SPEC_SCHEMA,
        "source": "backend/app/factory/floor_actions.py",
        "actions": {a.value: {"value": value_shape(a)} for a in FloorAction},
    }


class FloorActionError(ValueError):
    """An action the Floor does not define, or one missing its value."""


def parse_action(raw: Optional[str]) -> Optional[FloorAction]:
    """The typed action, or None when the request carries none.

    An unknown action is refused, never guessed at.
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return FloorAction(text)
    except ValueError as exc:
        raise FloorActionError(
            f"unknown Floor action {text!r}; expected one of "
            + ", ".join(a.value for a in FloorAction)
        ) from exc


def parse_level(raw: Optional[str]) -> BuildLevel:
    try:
        return parse_build_level(raw)
    except BuildLevelError as exc:
        raise FloorActionError(str(exc)) from exc


#: The typed reason a run action is refused while no level is chosen.
BUILD_LEVEL_REQUIRED = "BUILD_LEVEL_REQUIRED"


def require_build_level(product_design: Any) -> Optional[dict]:
    """None when the session carries a chosen build level; otherwise the
    typed refusal a run action returns. There is no default level: the build
    waits for the user's choice."""
    if str(getattr(product_design, "build_level", None) or "").strip():
        return None
    return {
        "sse": "info",
        "ok": False,
        "refused": BUILD_LEVEL_REQUIRED,
        "summary": (
            "Choose the build level before the build starts: prototype (done "
            "when the code passes), light, pilot, or production (the full "
            "acceptance floor). Nothing is built until you choose."
        ),
        "awaiting_action": FloorAction.SET_BUILD_LEVEL.value,
        "stream_delta": True,
    }
