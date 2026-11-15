"""One config source for the support address shown in the product UI.

The support/legal contact is an operator-owned constant, read from the
``SUPPORT_EMAIL`` environment variable with a factory default. Nothing in the
UI or the product hardcodes the address: it travels through this helper (and
the public ``/version`` endpoint) so an operator can change it without a code
change.
"""

from __future__ import annotations

import os

SUPPORT_EMAIL_DEFAULT = "support@cerebrum-dev.com"


def support_email() -> str:
    """The configured support address, or the default when unset/blank."""
    value = os.getenv("SUPPORT_EMAIL", "").strip()
    return value or SUPPORT_EMAIL_DEFAULT
