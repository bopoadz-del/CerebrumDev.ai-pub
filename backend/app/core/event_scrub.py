"""SEC-1: one scrub rule for every error-report event before it leaves.

The rule is generic. It works by field kind and by value pattern, and it
names no product, route or block:

* Payload subtrees (request bodies, cookies, stack-frame local variables)
  are dropped whole. A body or a frame can hold a secret under any name, so
  it is not scrubbed field by field.
* A field whose normalised name is a credential kind (authorization,
  cookie, token, key, password, connection string, e-mail and similar) has
  its value replaced with ``[redacted]``.
* Every remaining string goes through the value patterns: e-mail
  addresses, credentials inside a URI or a ``key=value`` connection string,
  bearer tokens, and the Factory's shared secret patterns
  (``app.factory.build.sanitize``).

SDK envelope scalars at the top level (event id, release, platform, level,
timestamps) are identifiers that the SDK writes, not caller data. They pass
through so that release tracking keeps working. A failed scrub drops the
event, so raw data never ships.
"""

from __future__ import annotations

import re
from typing import Any

REDACTED = "[redacted]"

#: Payload subtrees that are removed whole: bodies, cookies, frame locals.
DROPPED_KINDS = frozenset({"data", "body", "cookies", "vars"})

#: A field is a credential when its normalised name contains one of these.
SECRET_KIND_PARTS = (
    "authorization",
    "cookie",
    "token",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "apikey",
    "accesskey",
    "privatekey",
    "credential",
    "connectionstring",
    "databaseurl",
    "dsn",
    "signature",
    "bearer",
    "sessionid",
    "email",
)

#: ...or is exactly one of these short names. They are matched exactly so
#: that ``keywords`` or ``author`` survive.
SECRET_KIND_EXACT = frozenset({"key", "auth", "pwd", "session", "otp", "pin", "mail"})

#: Top-level SDK identifiers that are never caller data.
ENVELOPE_FIELDS = frozenset(
    {
        "event_id",
        "release",
        "dist",
        "environment",
        "platform",
        "level",
        "timestamp",
        "start_timestamp",
        "type",
    }
)

_VALUE_PATTERNS = (
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]+@"), r"\1" + REDACTED + "@"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), REDACTED),
    (re.compile(r"(?i)\b(password|pwd)\s*=\s*[^;\s]+"), r"\1=" + REDACTED),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer " + REDACTED),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|apikey|secret|token|access[_-]?key|private[_-]?key)"
            r"\s*[=:]\s*[^\s&;,]+"
        ),
        r"\1=" + REDACTED,
    ),
)

_MAX_DEPTH = 16


def _kind(name: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def is_secret_kind(name: Any) -> bool:
    """True when a field named *name* holds credential material."""
    kind = _kind(name)
    return kind in SECRET_KIND_EXACT or any(p in kind for p in SECRET_KIND_PARTS)


def scrub_text(text: str) -> str:
    """Remove e-mails, credentials and secret-shaped values from *text*."""
    # Imported here: app/__init__ starts the reporter before app.factory loads.
    from app.factory.build.sanitize import sanitize_for_status

    for pattern, replacement in _VALUE_PATTERNS:
        text = pattern.sub(replacement, text)
    return sanitize_for_status(text)


def _scrub(obj: Any, depth: int) -> Any:
    if depth > _MAX_DEPTH:
        return REDACTED
    if isinstance(obj, dict):
        return {
            k: REDACTED if is_secret_kind(k) else _scrub(v, depth + 1)
            for k, v in obj.items()
            if _kind(k) not in DROPPED_KINDS
        }
    if isinstance(obj, (list, tuple)):
        # A header list arrives as [name, value] pairs.
        if len(obj) == 2 and isinstance(obj[0], str) and is_secret_kind(obj[0]):
            return [obj[0], REDACTED]
        return [_scrub(item, depth + 1) for item in obj]
    if isinstance(obj, str):
        return scrub_text(obj)
    return obj


def scrub_event(event: Any, hint: Any = None) -> Any:
    """Sentry ``before_send`` / ``before_send_transaction`` hook.

    It never raises. If scrubbing fails, the event is dropped.
    """
    try:
        return {k: v if k in ENVELOPE_FIELDS else _scrub(v, 1) for k, v in event.items()}
    except Exception:  # noqa: BLE001 - a failed scrub must never ship raw data
        return None
