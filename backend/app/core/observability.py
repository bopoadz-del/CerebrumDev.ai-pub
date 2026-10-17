"""Observability bootstrap — Sentry error tracking + performance.

Activates only when ``SENTRY_DSN`` is set; otherwise a no-op so local dev and
tests are unaffected. Release is tagged from ``RENDER_GIT_COMMIT`` on Render.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def _rate(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except ValueError:
        return default


def init_sentry() -> None:
    """Initialize Sentry once, gated on SENTRY_DSN. Never raises."""
    global _initialized
    if _initialized:
        return
    _initialized = True
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logger.warning("SENTRY_DSN set but sentry-sdk not installed — skipping")
        return

    try:
        sentry_sdk.init(
            dsn=dsn,
            environment=os.getenv("ENV", "development"),
            release=os.getenv("RENDER_GIT_COMMIT") or None,
            send_default_pii=False,
            traces_sample_rate=_rate("SENTRY_TRACES_SAMPLE_RATE", 0.1),
            integrations=[FastApiIntegration(), LoggingIntegration()],
        )
        logger.info("Sentry initialized (env=%s)", os.getenv("ENV", "development"))
    except Exception:  # observability must never take the app down
        logger.exception("Sentry initialization failed — continuing without it")
