"""Upload allowlist + validators for the client ``+`` attach (F-1a).

The F-1 owner scope (ORDERS_0733Z): accept PDF / DOCX / XLSX / CSV / TXT / MD;
photos, video, CAD, zip/rar stay 415. The machinery mirrors
``app.routers.upload`` EXACTLY — extension allowlist as the primary control, a
content-type cross-check that treats ``octet-stream`` as absent, an incremental
chunked read with a per-file byte cap (``MAX_UPLOAD_FILE_BYTES``, default
20 MiB) that stops the moment the cap is crossed, 413 on oversize, 400 on no
file. The ASGI body ceiling (``core.request_limits``) already guards receipt in
``app.main`` and in generated products; it is not re-added here.

This module raises :class:`fastapi.HTTPException` directly so both the kernel
route and the Factory-floor route share one validator rather than two.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile

#: The F-1 allowlist. Anything outside it (.png/.jpg photos, video, .dwg CAD,
#: .zip/.rar archives, …) is refused 415 by the extension check below.
DEFAULT_ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".csv", ".txt", ".md"}

MAX_UPLOAD_FILE_BYTES_ENV = "MAX_UPLOAD_FILE_BYTES"
MAX_UPLOAD_FILE_BYTES_DEFAULT = 20 * 1024 * 1024  # 20 MiB
ALLOWED_EXTENSIONS_ENV = "ALLOWED_UPLOAD_EXTENSIONS"

# Declared content types that may legitimately accompany each extension. OOXML
# (.docx/.xlsx) arrives as a zip, so the mapping is deliberately loose; the
# extension allowlist is the primary control and this only catches an obvious
# mismatch (e.g. an image declared image/png but named .pdf).
_EXT_CONTENT_TYPES = {
    ".pdf": ("application/pdf",),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    ),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
        "application/vnd.ms-excel",
    ),
    ".csv": ("text/", "application/csv", "application/vnd.ms-excel"),
    ".txt": ("text/",),
    ".md": ("text/",),
}

_UNINFORMATIVE_CONTENT_TYPES = {"application/octet-stream", "binary/octet-stream"}

_CHUNK_BYTES = 1024 * 1024


def _positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def max_upload_file_bytes() -> int:
    """Per-file ceiling, read per request (never cached at import)."""
    return _positive_int_env(MAX_UPLOAD_FILE_BYTES_ENV, MAX_UPLOAD_FILE_BYTES_DEFAULT)


def allowed_extensions() -> set:
    """The allowlist, env-overridable like ``app.routers.upload``."""
    raw = os.getenv(ALLOWED_EXTENSIONS_ENV, "").strip()
    if not raw:
        return set(DEFAULT_ALLOWED_EXTENSIONS)
    parsed = {
        ("." + item.strip().lstrip(".").lower())
        for item in raw.split(",")
        if item.strip().strip(".")
    }
    return parsed or set(DEFAULT_ALLOWED_EXTENSIONS)


def _content_type_matches(declared: Optional[str], ext: str) -> bool:
    if not declared:
        return True  # nothing declared: the extension allowlist stands alone
    value = declared.split(";", 1)[0].strip().lower()
    if value in _UNINFORMATIVE_CONTENT_TYPES:
        # curl -F and browsers with no MIME mapping send octet-stream for
        # .md/.csv and more; it carries no information, so treat it as absent.
        return True
    permitted = _EXT_CONTENT_TYPES.get(ext)
    if not permitted:
        return True
    return any(value.startswith(prefix) for prefix in permitted)


async def validate_and_read(file: UploadFile) -> tuple[str, bytes]:
    """Validate one ``UploadFile`` and return ``(safe_name, bytes)``.

    Raises :class:`HTTPException` — 415 (type not accepted / content-type
    mismatch), 413 (over the per-file byte cap), 400 (no file name). The read is
    incremental: one chunk in memory at a time, and it stops the moment the cap
    is crossed rather than after buffering the whole file.
    """
    safe_name = Path(file.filename or "").name
    if not safe_name:
        raise HTTPException(status_code=400, detail="No file provided")

    ext = Path(safe_name).suffix.lower()
    allowed = allowed_extensions()
    if ext not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"File type '{ext or safe_name}' is not accepted",
        )
    if not _content_type_matches(file.content_type, ext):
        raise HTTPException(
            status_code=415,
            detail=f"Content type '{file.content_type}' does not match '{ext}'",
        )

    size_cap = max_upload_file_bytes()
    buf = bytearray()
    while True:
        chunk = await file.read(_CHUNK_BYTES)
        if not chunk:
            break
        buf += chunk
        if len(buf) > size_cap:
            raise HTTPException(
                status_code=413,
                detail=f"File '{safe_name}' exceeds the {size_cap} byte per-file limit",
            )
    return safe_name, bytes(buf)
