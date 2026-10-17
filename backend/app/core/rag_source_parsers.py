"""Bounded parser adapters for RAG source acquisition previews.

Supports PDF, HTML/XHTML, and plain text / Markdown. No OCR, no browser
automation, no JavaScript execution, no chunking.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Result of a bounded parse operation."""

    text: str
    parser_id: str
    parser_version: Optional[str] = None
    page_count: Optional[int] = None
    warnings: List[str] = None
    #: The extracted text was cut at ``max_chars`` -- the typed fact the
    #: truncation warning only describes.
    truncated: bool = False

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _clean_text(text: str) -> str:
    """Normalize whitespace in extracted text."""
    return re.sub(r"\s+", " ", text).strip()


def parse_pdf(
    raw_bytes: bytes,
    temp_path: Path,
    max_pages: int,
    max_chars: int,
) -> ParseResult:
    """Extract embedded text from a PDF using pypdf.

    Rejects encrypted PDFs that cannot be opened safely. Does not use OCR.
    """
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("pypdf is not installed") from exc

    warnings: List[str] = []

    # Write bytes to temp_path so PdfReader can read from disk if needed.
    temp_path.write_bytes(raw_bytes)

    try:
        reader = PdfReader(str(temp_path), strict=False)
    except Exception as exc:
        raise RuntimeError(f"Cannot open PDF: {exc}") from exc

    if reader.is_encrypted:
        raise RuntimeError("Encrypted PDFs are not supported.")

    total_pages = len(reader.pages)
    if total_pages > max_pages:
        warnings.append(
            f"PDF has {total_pages} pages; only the first {max_pages} were inspected."
        )

    pages_to_read = reader.pages[:max_pages]
    pieces: List[str] = []
    for page in pages_to_read:
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            warnings.append(f"Failed to extract text from a page: {exc}")
            text = ""
        pieces.append(text)
        if sum(len(p) for p in pieces) >= max_chars:
            break

    full_text = _clean_text("\n".join(pieces))
    text = _truncate(full_text, max_chars)
    if len(full_text) > max_chars:
        warnings.append(
            f"Extracted text exceeded {max_chars} characters and was truncated."
        )

    return ParseResult(
        text=text,
        parser_id="pypdf",
        parser_version=getattr(__import__("pypdf", fromlist=["__version__"]), "__version__", None),
        page_count=total_pages,
        warnings=warnings,
        truncated=len(full_text) > max_chars,
    )


def parse_html(raw_bytes: bytes, max_chars: int) -> ParseResult:
    """Extract visible text from HTML/XHTML, stripping scripts and styles."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("beautifulsoup4 is not installed") from exc

    warnings: List[str] = []
    text_bytes = raw_bytes
    # Try UTF-8 first, then fallback with replacement.
    try:
        html_text = text_bytes.decode("utf-8")
    except UnicodeDecodeError:
        html_text = text_bytes.decode("utf-8", errors="replace")
        warnings.append("HTML declared an invalid UTF-8 sequence; replaced invalid bytes.")

    soup = BeautifulSoup(html_text, "html.parser")

    # Remove executable / active content.
    for tag in soup(["script", "style", "iframe", "object", "embed", "applet"]):
        tag.decompose()

    # Extract visible text.
    full_text = _clean_text(soup.get_text(separator="\n"))
    text = _truncate(full_text, max_chars)
    if len(full_text) > max_chars:
        warnings.append(
            f"Extracted text exceeded {max_chars} characters and was truncated."
        )

    return ParseResult(
        text=text,
        parser_id="beautifulsoup4",
        parser_version=getattr(__import__("bs4", fromlist=["__version__"]), "__version__", None),
        page_count=None,
        warnings=warnings,
        truncated=len(full_text) > max_chars,
    )


def parse_text(raw_bytes: bytes, max_chars: int) -> ParseResult:
    """Decode plain text or Markdown safely with bounded output."""
    warnings: List[str] = []
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        text = raw_bytes.decode("utf-8", errors="replace")
        warnings.append("Text contained invalid UTF-8 bytes; replaced invalid bytes.")

    full_text = _clean_text(text)
    text = _truncate(full_text, max_chars)
    if len(full_text) > max_chars:
        warnings.append(
            f"Extracted text exceeded {max_chars} characters and was truncated."
        )

    return ParseResult(
        text=text,
        parser_id="plaintext",
        parser_version=None,
        page_count=None,
        warnings=warnings,
        truncated=len(full_text) > max_chars,
    )


def parse_bytes(
    raw_bytes: bytes,
    content_type: str,
    temp_path: Path,
    max_pages: int,
    max_chars: int,
    preview_chars: int,
) -> ParseResult:
    """Dispatch to the correct parser based on the observed content type."""
    normalized = content_type.split(";")[0].strip().lower()

    if normalized == "application/pdf":
        return parse_pdf(raw_bytes, temp_path, max_pages=max_pages, max_chars=max_chars)

    if normalized in {"text/html", "application/xhtml+xml"}:
        return parse_html(raw_bytes, max_chars=max_chars)

    if normalized in {"text/plain", "text/markdown"}:
        return parse_text(raw_bytes, max_chars=max_chars)

    raise RuntimeError(f"Unsupported content type for parsing: {content_type}")
