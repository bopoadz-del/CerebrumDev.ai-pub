"""Request body size ceiling — the only limit that fires before receipt.

Nothing else in the stack bounds an inbound body. FastAPI/Starlette will read
whatever the client sends: a JSON payload is materialised in memory before any
handler runs, and a multipart upload is spooled to disk. Handler-level checks
(``routers/upload.py``) bound what a *file* may be, but they only run after the
body has already been accepted. This middleware is the ceiling that runs first.

Two ceilings, chosen by content type:

* ``MAX_UPLOAD_BODY_BYTES``  for ``multipart/form-data`` (default 100 MiB) —
  file uploads legitimately need headroom, and the per-file/per-count caps in
  the upload router do the fine-grained work.
* ``MAX_REQUEST_BODY_BYTES`` for everything else (default 2 MiB) — JSON briefs,
  blueprints and chat turns have no business being larger.

A single global ceiling would either be too tight for uploads or too loose for
JSON, and a tight one would silently pre-empt the upload router's own caps.

Both ``Content-Length`` and streamed (chunked, no ``Content-Length``) bodies
are guarded: the declared length is rejected up front, and the actual bytes are
counted as they arrive so a lying or absent header cannot get past.

Wiring lives in ``app/main.py`` (see the module docstring in the review notes);
this module deliberately does not touch the app object.
"""

from __future__ import annotations

import json
import os
from typing import Any, Awaitable, Callable, Dict, MutableMapping

MAX_REQUEST_BODY_BYTES_ENV = "MAX_REQUEST_BODY_BYTES"
MAX_REQUEST_BODY_BYTES_DEFAULT = 2 * 1024 * 1024  # 2 MiB

MAX_UPLOAD_BODY_BYTES_ENV = "MAX_UPLOAD_BODY_BYTES"
MAX_UPLOAD_BODY_BYTES_DEFAULT = 100 * 1024 * 1024  # 100 MiB

Scope = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[MutableMapping[str, Any]]]
Send = Callable[[MutableMapping[str, Any]], Awaitable[None]]


class BodyTooLarge(Exception):
    """Raised out of the wrapped receive when the body exceeds the ceiling."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"request body exceeds {limit} bytes")
        self.limit = limit


def _positive_int_env(name: str, default: int) -> int:
    """Read the ceiling per request, never at import.

    A value snapshotted in ``__init__`` is read while the app object is being
    constructed, which is before tests (or a late ``load_dotenv``) can set it.
    Mirrors the ``core.auth._master_key`` house pattern.
    """
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def max_request_body_bytes() -> int:
    return _positive_int_env(MAX_REQUEST_BODY_BYTES_ENV, MAX_REQUEST_BODY_BYTES_DEFAULT)


def max_upload_body_bytes() -> int:
    return _positive_int_env(MAX_UPLOAD_BODY_BYTES_ENV, MAX_UPLOAD_BODY_BYTES_DEFAULT)


def _headers(scope: Scope) -> Dict[bytes, bytes]:
    return {k.lower(): v for k, v in scope.get("headers") or []}


def limit_for_scope(scope: Scope) -> int:
    """Multipart uploads get the upload ceiling; everything else the tight one."""
    content_type = _headers(scope).get(b"content-type", b"")
    if content_type.lower().startswith(b"multipart/form-data"):
        return max_upload_body_bytes()
    return max_request_body_bytes()


async def _send_413(send: Send, limit: int) -> None:
    body = json.dumps(
        {
            "detail": {
                "error": "request_body_too_large",
                "limit_bytes": limit,
                "message": (
                    f"Request body exceeds the {limit} byte limit for this "
                    "content type."
                ),
            }
        }
    ).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"connection", b"close"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


class BodySizeLimitMiddleware:
    """Pure-ASGI body ceiling.

    Deliberately not a ``BaseHTTPMiddleware`` subclass: counting streamed bytes
    means wrapping ``receive``, which BaseHTTPMiddleware does not expose. It
    also runs before the request ever reaches the routing/dependency layer, so
    an oversized body never touches a handler.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limit = limit_for_scope(scope)

        declared = _headers(scope).get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > limit:
                    # Reject without ever invoking the application.
                    await _send_413(send, limit)
                    return
            except ValueError:
                pass  # malformed header: fall through to the streaming count

        state = {"received": 0, "response_started": False}

        async def counting_receive() -> MutableMapping[str, Any]:
            message = await receive()
            if message.get("type") == "http.request":
                state["received"] += len(message.get("body") or b"")
                if state["received"] > limit:
                    # Raised into the application's read; caught below. A
                    # chunked body with no Content-Length only trips here.
                    raise BodyTooLarge(limit)
            return message

        async def tracking_send(message: MutableMapping[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                state["response_started"] = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if not state["response_started"]:
                await _send_413(send, limit)
            # If the response already started there is no safe way to change
            # the status; the connection close header on 413 does not apply,
            # so we simply stop feeding the app.
