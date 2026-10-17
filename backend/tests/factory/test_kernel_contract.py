"""Kernel contract extracted from RetailClient remains product-neutral."""

from app.cerebrum_product_kernel.contract import (
    ActionContext,
    ActionOutcome,
    ActionRegistry,
    ActionSpec,
    ActionStatus,
    execute_action,
)


def test_kernel_has_no_retailops_import_paths():
    import app.cerebrum_product_kernel.contract.registry as reg
    import inspect

    src = inspect.getsource(reg)
    assert "retailops" not in src
    assert "app.product.kit.domains" in src or "DOMAINS_PACKAGE" in src


async def _handler(ctx, args):
    return ActionOutcome.success({"ok": True, "n": args.get("n", 0)})


import pytest


@pytest.mark.asyncio
async def test_execute_action_success():
    spec = ActionSpec(
        action_id="product.ping",
        domain="product",
        name="ping",
        description="ping",
        input_schema={"type": "object", "properties": {"n": {"type": "integer"}}},
        output_schema={"type": "object"},
        handler=_handler,
        required_context=["user_id", "tenant_id"],
        permissions=["product.execute"],
    )
    reg = ActionRegistry()
    reg.register(spec)
    ctx = ActionContext(
        user_id="u1",
        tenant_id="atlas",
        organisation_id="atlas",
        project_id="p1",
        permissions=["product.execute"],
        allowed_domains=["product"],
    )
    result = await execute_action(spec, ctx, {"n": 3})
    assert result.status == ActionStatus.SUCCESS
    assert result.output["ok"] is True


@pytest.mark.asyncio
async def test_execute_action_rejects_unknown_fields():
    """Negative space: additionalProperties:false is a refusal, not a pass."""
    spec = ActionSpec(
        action_id="product.ping",
        domain="product",
        name="ping",
        description="ping",
        input_schema={
            "type": "object",
            "properties": {"n": {"type": "integer"}},
            "additionalProperties": False,
        },
        output_schema={"type": "object"},
        handler=_handler,
        required_context=["user_id", "tenant_id"],
        permissions=["product.execute"],
    )
    ctx = ActionContext(
        user_id="u1",
        tenant_id="atlas",
        organisation_id="atlas",
        project_id="p1",
        permissions=["product.execute"],
        allowed_domains=["product"],
    )
    result = await execute_action(spec, ctx, {"n": 3, "nope": 1})
    assert result.status == ActionStatus.VALIDATION_ERROR
    assert result.status != ActionStatus.SUCCESS
    assert "nope" in (result.error_message or "")
