"""CerebrumDev.ai factory backend package.

Observability (Sentry) initializes at package import — inert unless SENTRY_DSN
is set. Kept import-light: observability guards its own optional imports.
"""

from app.core.observability import init_sentry as _init_sentry

_init_sentry()

del _init_sentry
