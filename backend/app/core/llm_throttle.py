"""Per-account burst throttle for LLM-backed routes.

Trial quotas (``trial_limits``) meter TOTALS and exempt active subscribers;
before this module, a subscribed account had no limit of any kind on the LLM
routes. This meters RATE and binds every principal: a single account —
subscribed or not — must not be able to serialize the one-worker service
under back-to-back 120 s LLM calls, and a leaked key must not be an
unbounded spend primitive.

Env knobs: ``LLM_RATE_LIMIT_MAX`` (default 30 requests) per
``LLM_RATE_LIMIT_WINDOW_S`` (default 60 s). ``LLM_RATE_LIMIT_MAX=0``
disables the throttle. Shares the auth limiter's storage (Redis when
configured, bounded in-memory fallback), keyed by account — not by client
address — because every guarded route is authenticated.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from .rate_limit import check_rate_limit


def _limits() -> tuple[int, int]:
    try:
        max_attempts = int(os.getenv("LLM_RATE_LIMIT_MAX", "30"))
    except ValueError:
        max_attempts = 30
    try:
        window = int(os.getenv("LLM_RATE_LIMIT_WINDOW_S", "60"))
    except ValueError:
        window = 60
    return max_attempts, max(1, window)


def require_llm_rate(principal: Any, bucket: str) -> None:
    """Raise 429 when the principal exceeds the LLM burst budget.

    ``principal`` may be a Principal, a bare account-id string (the chat flow
    only carries ``state.user_id``), or None (keys with no account — master
    key, local dev — share one bucket per principal kind).
    """
    max_attempts, window = _limits()
    if max_attempts <= 0:
        return
    if isinstance(principal, str) and principal.strip():
        key = principal.strip()
    else:
        key = getattr(principal, "account_id", None) or getattr(
            principal, "kind", "anonymous"
        )
    if check_rate_limit(
        f"llm:{bucket}", str(key), max_attempts=max_attempts, window=window
    ):
        return
    raise HTTPException(
        status_code=429,
        detail={
            "error": "rate_limited",
            "bucket": bucket,
            "message": (
                f"Too many {bucket} requests; limit is {max_attempts} "
                f"per {window}s. Retry after the window."
            ),
        },
        headers={"Retry-After": str(window)},
    )
