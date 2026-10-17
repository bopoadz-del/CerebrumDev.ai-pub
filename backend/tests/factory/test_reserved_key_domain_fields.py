"""A domain column named like a trust-scope key must survive to handle().

Live, from the booted sess_6400b6c zip. Two of the four capabilities answered
every well-formed request with a refusal of their own required field:

    POST /v1/daily_log_management {"project_id": "P-1", ...}
      -> {"ok": false, "error": "project_id must be a non-empty string"}
    POST /v1/punch_list_tracking  {"project_id": "P-1", ...}
      -> {"ok": false, "error": "project_id must be a string"}

Calling the same handler directly with the same payload worked. The kernel
was deleting the field on the way in, and saying so in a warnings list
nothing surfaces:

    ignored reserved argument 'project_id' (trust scope is server-controlled)

`project_id` is in RESERVED_CONTEXT_KEYS, and it is also the obvious column
name for a construction platform. `photo_documentation` was the only
capability of the four that worked end to end, and only because its column
happens to be spelled `project_code`.

The strip is right and stays. Trust scope travels in ActionContext -- a
separate parameter the caller cannot reach -- so an argument key can only
ever be domain data; what it can do is collide with a column name and
silently delete it. An action that DECLARES the name keeps it. One that does
not is stripped, with the warning, exactly as before.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from app.cerebrum_product_kernel.contract.models import (
    ActionContext,
    ActionOutcome,
    ActionSpec,
    ActionStatus,
    RESERVED_CONTEXT_KEYS,
)
from app.cerebrum_product_kernel.contract.runtime import (
    _declared_fields,
    _sanitize_arguments,
    execute_action,
)

LIVE_DAILY_LOG = {
    "log_date": "2026-08-31",
    "project_id": "P-1",
    "superintendent_name": "A",
    "weather_condition": "sunny",
    "work_completed": "x",
    "man_hours": 8,
    "safety_incident": False,
}


def _context() -> ActionContext:
    return ActionContext(
        user_id="anonymous",
        tenant_id="local",
        organisation_id="local",
        project_id="local",
        permissions=[],
        allowed_domains=["product"],
    )


def _spec(fields, seen: Dict[str, Any]) -> ActionSpec:
    async def _handler(context, arguments):
        seen.clear()
        seen.update(arguments)
        return ActionOutcome.success({"ok": True})

    return ActionSpec(
        action_id="product.daily_log_management",
        domain="product",
        name="daily_log_management",
        description="daily_log_management",
        input_schema={"properties": {f: {} for f in fields}},
        output_schema={},
        required_context=[],
        permissions=[],
        read_only=False,
        handler=_handler,
    )


# ── the live failure ─────────────────────────────────────────────────────────


def test_a_declared_project_id_reaches_the_handler():
    seen: Dict[str, Any] = {}
    spec = _spec(sorted(LIVE_DAILY_LOG), seen)
    result = asyncio.run(execute_action(spec, _context(), dict(LIVE_DAILY_LOG)))
    assert result.status == ActionStatus.SUCCESS, result.error_message
    assert seen.get("project_id") == "P-1"
    assert seen == LIVE_DAILY_LOG


def test_the_trust_context_is_untouched_by_the_argument():
    """The whole point: the argument must not become the scope."""
    seen: Dict[str, Any] = {}
    captured = {}

    async def _handler(context, arguments):
        captured["project_id"] = context.project_id
        seen.update(arguments)
        return ActionOutcome.success({"ok": True})

    spec = _spec(sorted(LIVE_DAILY_LOG), seen)
    spec = ActionSpec(**{**spec.__dict__, "handler": _handler})
    asyncio.run(execute_action(spec, _context(), dict(LIVE_DAILY_LOG)))
    assert captured["project_id"] == "local"      # server-controlled
    assert seen["project_id"] == "P-1"            # domain data


def test_no_warning_for_a_declared_column():
    seen: Dict[str, Any] = {}
    spec = _spec(sorted(LIVE_DAILY_LOG), seen)
    result = asyncio.run(execute_action(spec, _context(), dict(LIVE_DAILY_LOG)))
    assert not [w for w in (result.warnings or []) if "project_id" in w]


# ── what must NOT change ─────────────────────────────────────────────────────


def test_an_undeclared_reserved_key_is_still_stripped():
    clean, warnings = _sanitize_arguments(
        {"project_id": "P-1", "note": "n"}, frozenset({"note"})
    )
    assert clean == {"note": "n"}
    assert any("project_id" in w for w in warnings)


@pytest.mark.parametrize("key", sorted(RESERVED_CONTEXT_KEYS))
def test_every_reserved_key_is_stripped_when_undeclared(key):
    clean, warnings = _sanitize_arguments({key: "x"}, frozenset())
    assert clean == {}
    assert any(key in w for w in warnings)


@pytest.mark.parametrize("key", ["permissions", "allowed_domains", "tenant_id"])
def test_declaring_a_scope_key_is_still_only_domain_data(key):
    """Even declared, the key cannot reach the context -- it is a separate
    parameter. This test exists so that stays true if the signature changes."""
    seen: Dict[str, Any] = {}
    spec = _spec([key], seen)
    result = asyncio.run(execute_action(spec, _context(), {key: "attacker"}))
    assert result.status == ActionStatus.SUCCESS
    assert seen[key] == "attacker"


def test_no_declared_fields_means_the_old_behaviour():
    seen: Dict[str, Any] = {}
    spec = _spec([], seen)
    result = asyncio.run(execute_action(spec, _context(), dict(LIVE_DAILY_LOG)))
    assert "project_id" not in seen
    assert any("project_id" in w for w in (result.warnings or []))


def test_a_non_reserved_field_never_needed_declaring():
    """photo_documentation's `project_code` -- the one that worked."""
    clean, warnings = _sanitize_arguments({"project_code": "P-1"}, frozenset())
    assert clean == {"project_code": "P-1"}
    assert warnings == []


# ── the declared-field reader ────────────────────────────────────────────────


def test_declared_fields_reads_properties():
    spec = _spec(["a", "b"], {})
    assert _declared_fields(spec) == frozenset({"a", "b"})


def test_declared_fields_tolerates_an_empty_schema():
    spec = _spec([], {})
    assert _declared_fields(spec) == frozenset()


# ── the generated header carries the names ───────────────────────────────────


def test_the_handler_module_declares_its_columns():
    from app.factory.build.roles_handlers import _handler_module

    src = _handler_module(
        "daily_log_management", ["database"], "    return {}", "test",
        {"database": "insert"},
        field_names=["log_date", "project_id", "man_hours"],
    )
    assert "CAPABILITY_FIELDS = ['log_date', 'project_id', 'man_hours']" in src


def test_the_bridge_feeds_those_columns_to_the_spec():
    from app.factory.build.roles_handlers import _render_kernel_bridge

    bridge = _render_kernel_bridge()
    assert "CAPABILITY_FIELDS" in bridge
    assert '"properties"' in bridge
    assert "input_schema={}," not in bridge
