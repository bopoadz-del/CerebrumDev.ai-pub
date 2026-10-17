"""Structured request logs with a correlation id (F13).

Machine-parseable stdout is one JSON object per line. Emoji is
stripped so a cp1252 or log shipper cannot turn a request line into
a parse failure. Human banners do not belong here.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "x-request-id"
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002700-\U000027BF"
    "\U0001F600-\U0001F64F"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE,
)
_request_id: ContextVar[str] = ContextVar("request_id", default="")


def strip_emoji(text: str) -> str:
    return EMOJI_RE.sub("", text or "")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": record.levelname,
            "logger": record.name,
            "msg": strip_emoji(record.getMessage()),
            "request_id": getattr(record, "request_id", "") or _request_id.get(),
        }
        return json.dumps(payload, ensure_ascii=True)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = getattr(record, "request_id", "") or _request_id.get()
        return True


class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        rid = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        token = _request_id.set(rid)
        request.state.request_id = rid
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = rid
            logging.getLogger("platform.request").info(
                "%s %s %s", request.method, request.url.path, response.status_code,
                extra={"request_id": rid},
            )
            return response
        finally:
            _request_id.reset(token)


def configure_logging() -> None:
    formatter = JsonFormatter()
    filt = RequestIdFilter()
    root = logging.getLogger()
    root.handlers.clear()
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    stream.addFilter(filt)
    root.addHandler(stream)
    storage = os.getenv("STORAGE_PATH")
    if storage:
        dest = Path(storage)
        if dest.is_dir():
            file_handler = logging.FileHandler(dest / "request.jsonl")
            file_handler.setFormatter(formatter)
            file_handler.addFilter(filt)
            root.addHandler(file_handler)
    root.setLevel(logging.INFO)


def install_observability(app) -> None:
    configure_logging()
    app.add_middleware(CorrelationMiddleware)
