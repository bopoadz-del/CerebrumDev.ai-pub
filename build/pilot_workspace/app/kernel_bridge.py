"""Bridge capability handle() through the vendored product kernel.

The kernel owns trust-scope, input/output validation, and ActionResult.
Persistence stays in the HTTP route after ActionStatus.SUCCESS.
"""

from __future__ import annotations

import importlib
from typing import Any, Dict

from app.cerebrum_product_kernel.contract.models import (
    ActionContext,
    ActionOutcome,
    ActionSpec,
    ActionStatus,
)
from app.cerebrum_product_kernel.contract.runtime import execute_action


def _wrap_handle(handle):
    async def _handler(context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
        out = handle(arguments)
        if not isinstance(out, dict):
            return ActionOutcome(
                status=ActionStatus.EXECUTION_ERROR,
                error_code="invalid_handler_result",
                error_message="handle() returned a non-mapping",
            )
        if out.get("ok") is False:
            return ActionOutcome(
                status=ActionStatus.VALIDATION_ERROR,
                error_code="refused",
                error_message=str(out.get("error") or "refused"),
                output=out,
            )
        return ActionOutcome.success(out)

    return _handler


def spec_for(capability_id: str) -> ActionSpec:
    name = capability_id.replace("-", "_")
    mod = importlib.import_module(f"app.actions.{name}")
    return ActionSpec(
        action_id=f"product.{name}",
        domain="product",
        name=name,
        description=str(getattr(mod, "CAPABILITY_ID", name)),
        input_schema={},
        output_schema={},
        required_context=[],
        permissions=[],
        read_only=False,
        handler=_wrap_handle(mod.handle),
    )


def product_context() -> ActionContext:
    return ActionContext(
        user_id="anonymous",
        tenant_id="local",
        organisation_id="local",
        project_id="local",
        permissions=[],
        allowed_domains=["product"],
    )


async def run_capability(capability_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    result = await execute_action(spec_for(capability_id), product_context(), payload or {})
    return result.to_dict()
