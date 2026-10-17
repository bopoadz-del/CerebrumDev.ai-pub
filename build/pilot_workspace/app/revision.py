"""Deploy revision identity for health and the rollback drill.

APP_REVISION / APP_MARK override the compiled defaults so a local
or Render rollback is a process restart against the same disk, not
a schema wipe. N+1 is a detectable mark change; rolling back to N
restores the prior mark and leaves persisted rows in place.
"""

from __future__ import annotations

import os

REVISION_N = "rev-n"
REVISION_N_PLUS_1 = "rev-n-plus-1"
MARK_BASELINE = "baseline"
MARK_CHANGED = "changed"
MARK = "baseline"


def current_app_revision() -> str:
    return os.getenv("APP_REVISION") or REVISION_N


def current_app_mark() -> str:
    return os.getenv("APP_MARK") or MARK
