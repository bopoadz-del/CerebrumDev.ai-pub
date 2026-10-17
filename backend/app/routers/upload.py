"""Session file upload.

Every file used to be read whole into memory (``await file.read()``) from an
unbounded ``List[UploadFile]``, with no size cap, no count cap and no type
check. One request could therefore pin an arbitrary amount of RAM and fill the
disk with arbitrary content.

Three caps now apply, all env-overridable:

* ``MAX_UPLOAD_FILES``       how many files one request may carry (default 20)
* ``MAX_UPLOAD_FILE_BYTES``  per-file ceiling                     (default 20 MiB)
* ``ALLOWED_UPLOAD_EXTENSIONS`` extension allowlist, cross-checked against the
  declared content type

Honest scope note: Starlette parses and spools the whole multipart body before
this handler runs, so reading in chunks bounds *memory*, not receipt. The only
control that rejects before receipt is ``core.request_limits`` at the ASGI
layer. Both are needed.
"""

import os
import uuid
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException

from ..models.session import SessionState
from ..core.session_guard import require_owned_session
from ..core.session_store import update_session
from ..core.upload_processor import process_upload
from ..core.background import start_task

router = APIRouter()

STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")

MAX_UPLOAD_FILES_ENV = "MAX_UPLOAD_FILES"
MAX_UPLOAD_FILES_DEFAULT = 20
MAX_UPLOAD_FILE_BYTES_ENV = "MAX_UPLOAD_FILE_BYTES"
MAX_UPLOAD_FILE_BYTES_DEFAULT = 20 * 1024 * 1024  # 20 MiB
ALLOWED_EXTENSIONS_ENV = "ALLOWED_UPLOAD_EXTENSIONS"

# Scoped to what core.upload_processor can actually consume (pdf / docx / text
# / image), plus the plain-text formats it reads directly. Anything else is a
# file we would store but never use.
DEFAULT_ALLOWED_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
}

# Declared content types that may legitimately accompany each extension.
# OOXML arrives as a zip and legacy Office as an OLE compound, so the mapping
# is deliberately loose; the extension allowlist is the primary control and
# this only catches an obvious mismatch (an .exe renamed and declared as such).
_EXT_CONTENT_TYPES = {
    ".pdf": ("application/pdf", "application/octet-stream"),
    ".doc": ("application/msword", "application/x-ole", "application/octet-stream"),
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    ),
    ".txt": ("text/",),
    ".md": ("text/",),
    ".csv": ("text/", "application/csv", "application/vnd.ms-excel"),
    ".json": ("application/json", "text/"),
    ".png": ("image/png",),
    ".jpg": ("image/jpeg",),
    ".jpeg": ("image/jpeg",),
    ".gif": ("image/gif",),
    ".webp": ("image/webp",),
    ".tif": ("image/tiff",),
    ".tiff": ("image/tiff",),
}

_CHUNK_BYTES = 1024 * 1024


def _positive_int_env(name: str, default: int) -> int:
    """Read caps per request, never at import (tests and late .env loads)."""
    try:
        value = int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value > 0 else default


def max_upload_files() -> int:
    return _positive_int_env(MAX_UPLOAD_FILES_ENV, MAX_UPLOAD_FILES_DEFAULT)


def max_upload_file_bytes() -> int:
    return _positive_int_env(MAX_UPLOAD_FILE_BYTES_ENV, MAX_UPLOAD_FILE_BYTES_DEFAULT)


def allowed_extensions() -> set:
    raw = os.getenv(ALLOWED_EXTENSIONS_ENV, "").strip()
    if not raw:
        return set(DEFAULT_ALLOWED_EXTENSIONS)
    parsed = {
        ("." + item.strip().lstrip(".").lower())
        for item in raw.split(",")
        if item.strip().strip(".")
    }
    return parsed or set(DEFAULT_ALLOWED_EXTENSIONS)


def _session_files_dir(session_id: str) -> Path:
    path = Path(STORAGE_PATH) / "sessions" / session_id / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


_UNINFORMATIVE_CONTENT_TYPES = {"application/octet-stream", "binary/octet-stream"}


def _content_type_matches(declared: Optional[str], ext: str) -> bool:
    if not declared:
        return True  # nothing declared: the extension allowlist stands alone
    value = declared.split(";", 1)[0].strip().lower()
    if value in _UNINFORMATIVE_CONTENT_TYPES:
        # curl -F and browsers on hosts with no MIME mapping send this for
        # .md/.csv/.json and plenty of images. It carries no information, so
        # treat it as absent rather than as a mismatch; the extension
        # allowlist (checked first) is the real control.
        return True
    permitted = _EXT_CONTENT_TYPES.get(ext)
    if not permitted:
        return True
    return any(value.startswith(prefix) for prefix in permitted)


def _discard(paths: List[str]) -> None:
    """Remove everything written for a request that is being rejected.

    ``start_task`` never fires on the rejection path, so anything already
    written would otherwise sit in session storage forever.
    """
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            pass


def _fail(
    session_id: str,
    state: SessionState,
    saved_paths: List[str],
    status_code: int,
    detail: str,
) -> HTTPException:
    _discard(saved_paths)
    state.upload.status = "failed"
    state.upload.message = detail
    update_session(session_id, state)
    return HTTPException(status_code=status_code, detail=detail)


@router.post("/{session_id}/upload")
async def upload_files(
    files: List[UploadFile] = File(...),
    state: SessionState = Depends(require_owned_session),
):
    session_id = state.session_id

    if state.upload.status == "processing":
        raise HTTPException(status_code=409, detail="Upload already in progress")

    file_cap = max_upload_files()
    size_cap = max_upload_file_bytes()
    allowed = allowed_extensions()

    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    if len(files) > file_cap:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files: {len(files)} (max {file_cap} per request)",
        )

    target_dir = _session_files_dir(session_id)
    saved_paths: List[str] = []

    state.upload.status = "pending"
    state.upload.progress = 0.0
    state.upload.failed_files = []
    state.upload.message = "Uploading files..."
    update_session(session_id, state)

    for file in files:
        safe_name = Path(file.filename or "unknown").name
        ext = Path(safe_name).suffix.lower()
        if ext not in allowed:
            raise _fail(
                session_id,
                state,
                saved_paths,
                415,
                f"File type '{ext or safe_name}' is not accepted",
            )
        if not _content_type_matches(file.content_type, ext):
            raise _fail(
                session_id,
                state,
                saved_paths,
                415,
                f"Content type '{file.content_type}' does not match '{ext}'",
            )

        dest = target_dir / f"{uuid.uuid4().hex}_{safe_name}"
        written = 0
        oversized = False
        # Incremental copy: one chunk in memory at a time, and the write stops
        # the moment the cap is crossed rather than after buffering the file.
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(_CHUNK_BYTES)
                if not chunk:
                    break
                written += len(chunk)
                if written > size_cap:
                    oversized = True
                    break
                out.write(chunk)
        if oversized:
            try:
                os.remove(dest)
            except OSError:
                pass
            raise _fail(
                session_id,
                state,
                saved_paths,
                413,
                f"File '{safe_name}' exceeds the {size_cap} byte per-file limit",
            )
        saved_paths.append(str(dest))

    # session_id doubles as job_id for simplicity
    start_task(session_id, process_upload(session_id, saved_paths))

    return {"job_id": session_id, "files_received": len(saved_paths)}


@router.get("/{session_id}/upload/status")
async def upload_status(state: SessionState = Depends(require_owned_session)):
    return state.upload


@router.get("/{session_id}/upload/result")
async def upload_result(state: SessionState = Depends(require_owned_session)):
    if state.upload.status not in ("completed", "completed_with_warnings", "failed"):
        raise HTTPException(status_code=400, detail="Upload not finished")
    return {
        "status": state.upload.status,
        "indexed_collection": state.upload.indexed_collection,
        "total_chunks": state.upload.total_chunks,
        "failed_files": state.upload.failed_files,
        "message": state.upload.message,
    }
