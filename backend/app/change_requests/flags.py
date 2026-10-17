"""Feature flags for the change-request loop (default OFF)."""

from __future__ import annotations

import os


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def change_request_intake_enabled() -> bool:
    """Factory intake endpoint + dry-run evaluator gate."""
    return _truthy("CHANGE_REQUEST_INTAKE_ENABLED")


def resident_emit_change_requests_enabled() -> bool:
    """Allow Resident L2 escalations to emit signed REPAIR requests."""
    return _truthy("RESIDENT_EMIT_CHANGE_REQUESTS")
