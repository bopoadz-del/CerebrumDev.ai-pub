"""Feature flag for Resident Engineer runtime (default OFF)."""

from __future__ import annotations

import os


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def resident_engineer_enabled() -> bool:
    """Resident Mode entrypoints are gated by ``RESIDENT_ENGINEER_ENABLED``.

    Default is false — observe/heal APIs refuse service when unset.
    """
    return _truthy("RESIDENT_ENGINEER_ENABLED")
