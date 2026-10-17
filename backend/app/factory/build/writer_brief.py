"""The one gated coding-agent brief.

The Factory coder (Kimi HTTP or an agentic CLI) is not a swarm of tiny
product stories. Each handle()/spec/route packet is a task. The *system*
context is this single brief: gates, contracts, and what done means.

The exit is the BUILD LEVEL the user chose (app.factory.build.build_level):
prototype is DONE at CODE_GREEN; light, pilot and production climb to the
STORE gate, and thin SUCCESS is a failure from pilot up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.factory.build.authority import kernel_seat_brief
from app.factory.build.build_level import render_exit_condition
from app.factory.build.persist_accept import persist_accept_brief_contract
from app.factory.build.reuse_accept import reuse_accept_brief_contract
from app.factory.build.schema_accept import schema_accept_brief_contract
from app.factory.build.workflow_accept import workflow_accept_brief_contract

#: Path to the standing coder protocol for handling a failing factory check.
TESTING_ERRORS_PROTOCOL_PATH = Path(__file__).with_name("testing_errors_protocol.md")


def testing_errors_protocol() -> str:
    """The full coder protocol text (testing_errors_protocol.md).

    Handed to the coder so a failing check is fixed at the cause, never by
    weakening a validation, a route check, or a test to pass. Fails closed to
    the one hard rule if the file is somehow missing on the build host.
    """
    try:
        return TESTING_ERRORS_PROTOCOL_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return (
            "Testing-errors protocol: never weaken, delete, or loosen a "
            "validation, a route check, or a test assertion to turn a red check "
            "green. Fix the cause. If a value was rejected, declare the accepted "
            "vocabulary on the spec (allowed_values) so the suite builds a payload "
            "the route accepts -- do not make the route accept anything."
        )


#: Shared system brief for every WRITER / rework coder call.
CODING_AGENT_BRIEF = f"""
FACTORY CODING-AGENT BRIEF (one prompt — this is the product story)

You are manufacturing the platform to the BUILD LEVEL the user chose. The
exit condition and the CODE → PRODUCT → STORE ladder for that level open the
compiled brief below; the level is the user's typed choice, never inferred.

Contracts you must honour on every capability you write:
- Blocks are action-dispatched. Pass action= as a keyword, never inside the
  payload dict. Prefer action=BLOCK_DEFAULT_ACTIONS.get(block_id).
- Call execute() for EVERY id in BLOCK_IDS. A declared block that is never
  invoked fails the WRITER behaviour gate and halts the build.
- Validate only the capability's own fields. Construct block inputs; do not
  demand block-specific keys (topic, sql/table, file paths, team_id,
  channel, steps) from the caller. The execute wrapper synthesizes those
  from the domain record. If you require a field, type, or vocabulary,
  declare it on the spec — the factory copies handler contracts onto the
  spec so the pilot suite can build a payload you will accept. Do not
  invent a second, stricter contract the spec cannot express.
- Offline platform: no network, no HTTP store callbacks, channel "mcp" only.
- {schema_accept_brief_contract()}
- {reuse_accept_brief_contract()}
- {persist_accept_brief_contract()}
- {workflow_accept_brief_contract()}

When a factory check fails, fix the cause. Never weaken, delete, or loosen a
validation, a route check, or a test assertion to turn a red check green -- a
green bought by removing a check is a red you hid, and the next gate finds it.
If a value was rejected, the rejection is almost always correct: declare the
accepted vocabulary on the spec (allowed_values) so the suite builds a payload
the route accepts, instead of making the route accept anything. The full rules
are in testing_errors_protocol.md (printed below), and the tester will tell you
when it already tried the values a route named and the route still refused --
that is a contradiction inside the product, not a tester artifact.

{testing_errors_protocol()}

This brief is the horizon. The user message is the compiled whole-job brief
(TARGET / STEP 0 INVENTORY / DO / ACCEPTANCE) — not one handle(), one spec,
or one route. One FACTORY_CODE_CLI writer; three gated phases (backend →
frontend+RAG → integration) with STOP / checkpoint between them. Fail-closed:
phase N acceptance before phase N+1. Resume skips landed writer phases.
A stage wall may hard-stop you so the factory can inspect what was achieved;
that stop is not permission to ship a scaffold.
""".strip()


def writer_system_brief() -> str:
    """Seat JD plus the one gated brief. This is what the coder receives."""
    return kernel_seat_brief("WRITER") + "\n\n" + CODING_AGENT_BRIEF + "\n"


def level_exit_condition(blueprint: Any) -> str:
    """The exit condition and ladder for the blueprint's BUILD LEVEL -- the
    head of every compiled brief, so the writer knows where THIS run stops."""
    return render_exit_condition(blueprint)
