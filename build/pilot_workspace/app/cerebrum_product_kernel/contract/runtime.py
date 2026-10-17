"""Generic action execution engine.

``execute_action`` is the single, domain-neutral entry point that a host
runtime uses to run any registered action. It enforces trust-scope, validates
inputs/outputs, runs the handler, and always returns a fully-formed
:class:`ActionResult` — never a raw exception or stack trace.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.cerebrum_product_kernel.contract.models import (
    RESERVED_CONTEXT_KEYS,
    ActionContext,
    ActionDependency,
    ActionOutcome,
    ActionResult,
    ActionSpec,
    ActionStatus,
)
from app.cerebrum_product_kernel.contract.schema_validation import validate as validate_schema

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sanitize_arguments(arguments: Dict[str, Any]) -> tuple[Dict[str, Any], list[str]]:
    """Strip reserved trust-scope keys from model/caller-supplied arguments.

    Model-generated arguments must never be able to set tenant/permission/domain
    scope. Any such keys are removed and reported as warnings.
    """
    clean: Dict[str, Any] = {}
    warnings: list[str] = []
    for key, value in (arguments or {}).items():
        if key in RESERVED_CONTEXT_KEYS:
            warnings.append(
                f"ignored reserved argument '{key}' (trust scope is server-controlled)"
            )
            continue
        clean[key] = value
    return clean, warnings


def _result(
    spec: ActionSpec,
    request_id: str,
    started_at: datetime,
    outcome: ActionOutcome,
) -> ActionResult:
    completed_at = _now()
    duration_ms = int((completed_at - started_at).total_seconds() * 1000)
    return ActionResult(
        request_id=request_id,
        action_id=spec.action_id,
        kit_version=spec.kit_version or spec.version,
        status=outcome.status,
        started_at=started_at,
        completed_at=completed_at,
        duration_ms=max(duration_ms, 0),
        output=outcome.output,
        dependencies=outcome.dependencies,
        evidence=outcome.evidence,
        warnings=outcome.warnings,
        error_code=outcome.error_code,
        error_message=outcome.error_message,
        metadata=outcome.metadata,
    )


async def execute_action(
    spec: ActionSpec,
    context: ActionContext,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    request_id: Optional[str] = None,
    confirmed: bool = False,
) -> ActionResult:
    """Execute ``spec`` under ``context`` with ``arguments``.

    Pipeline: sanitize args → verify domain allowance → verify permissions →
    verify confirmation → validate inputs → run handler → validate output →
    build result. Exceptions are converted to an ``execution_error`` result
    with no secret/stack-trace leakage.
    """
    request_id = request_id or f"act_{uuid.uuid4().hex}"
    started_at = _now()
    clean_args, arg_warnings = _sanitize_arguments(arguments or {})

    # 1. Trusted context completeness (required_context).
    missing_ctx = [
        field
        for field in spec.required_context
        if not getattr(context, field, None)
    ]
    if missing_ctx:
        return _result(
            spec,
            request_id,
            started_at,
            ActionOutcome(
                status=ActionStatus.VALIDATION_ERROR,
                error_code="missing_context",
                error_message=(
                    "missing required trusted context: " + ", ".join(sorted(missing_ctx))
                ),
                warnings=arg_warnings,
            ),
        )

    # 2. Domain allowance (fail-closed).
    if not context.allows_domain(spec.domain):
        return _result(
            spec,
            request_id,
            started_at,
            ActionOutcome(
                status=ActionStatus.PERMISSION_DENIED,
                error_code="domain_not_allowed",
                error_message=f"domain '{spec.domain}' is not permitted for this context",
                warnings=arg_warnings,
            ),
        )

    # 3. Permission check.
    missing_perms = [p for p in spec.permissions if not context.has_permission(p)]
    if missing_perms:
        return _result(
            spec,
            request_id,
            started_at,
            ActionOutcome(
                status=ActionStatus.PERMISSION_DENIED,
                error_code="permission_denied",
                error_message="missing permissions: " + ", ".join(sorted(missing_perms)),
                warnings=arg_warnings,
            ),
        )

    # 4. Confirmation requirement (write / high-risk actions).
    if spec.confirmation_required and not confirmed:
        return _result(
            spec,
            request_id,
            started_at,
            ActionOutcome(
                status=ActionStatus.VALIDATION_ERROR,
                error_code="confirmation_required",
                error_message="this action requires explicit confirmation",
                dependencies=[
                    ActionDependency(
                        kind="confirmation",
                        description="Caller must confirm this action before execution.",
                    )
                ],
                warnings=arg_warnings,
            ),
        )

    # 5. Input validation against the declared schema.
    input_errors = validate_schema(clean_args, spec.input_schema)
    if input_errors:
        return _result(
            spec,
            request_id,
            started_at,
            ActionOutcome(
                status=ActionStatus.VALIDATION_ERROR,
                error_code="invalid_input",
                error_message="; ".join(input_errors[:10]),
                warnings=arg_warnings,
            ),
        )

    # 6. Execute the handler; convert exceptions to execution_error.
    try:
        outcome = await spec.handler(context, clean_args)
    except Exception:  # noqa: BLE001 - deliberately broad; no leakage
        logger.exception("action '%s' handler raised", spec.action_id)
        return _result(
            spec,
            request_id,
            started_at,
            ActionOutcome(
                status=ActionStatus.EXECUTION_ERROR,
                error_code="handler_exception",
                error_message="the action failed to execute",
                warnings=arg_warnings,
            ),
        )

    if not isinstance(outcome, ActionOutcome):
        logger.error("action '%s' handler returned %r", spec.action_id, type(outcome))
        return _result(
            spec,
            request_id,
            started_at,
            ActionOutcome(
                status=ActionStatus.EXECUTION_ERROR,
                error_code="invalid_handler_result",
                error_message="the action returned an invalid result",
                warnings=arg_warnings,
            ),
        )

    # Merge argument-sanitization warnings into the outcome.
    if arg_warnings:
        outcome = outcome.model_copy(
            update={"warnings": [*outcome.warnings, *arg_warnings]}
        )

    # 7. Output validation only for successful outcomes.
    if outcome.status == ActionStatus.SUCCESS and spec.output_schema:
        output_errors = validate_schema(outcome.output, spec.output_schema)
        if output_errors:
            logger.error(
                "action '%s' produced output failing its schema: %s",
                spec.action_id,
                output_errors[:5],
            )
            return _result(
                spec,
                request_id,
                started_at,
                ActionOutcome(
                    status=ActionStatus.EXECUTION_ERROR,
                    error_code="invalid_output",
                    error_message="the action produced an invalid result",
                    warnings=outcome.warnings,
                ),
            )

    return _result(spec, request_id, started_at, outcome)
