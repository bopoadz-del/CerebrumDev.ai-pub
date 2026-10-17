"""Build Mode workbench (Milestone 4) — sandboxed candidate builder + packager promotion.

Consumes approved change-requests from the M3 queue. Never deploys. Never bypasses
the Factory packager. Flags default OFF.
"""

from app.workbench.flags import build_mode_enabled, kimi_workbench_enabled

__all__ = ["build_mode_enabled", "kimi_workbench_enabled"]
