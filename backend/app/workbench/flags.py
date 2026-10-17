"""Feature flags for Build Mode workbench (default OFF)."""

from __future__ import annotations

import os


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def build_mode_enabled() -> bool:
    """Master switch for workbench session / gates / staging promotion APIs."""
    return _truthy("BUILD_MODE_ENABLED")


def kimi_workbench_enabled() -> bool:
    """When false, use the honest labeled Factory regenerator stub (default)."""
    return _truthy("KIMI_WORKBENCH_ENABLED")
