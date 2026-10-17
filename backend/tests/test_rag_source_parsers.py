"""Tests for RAG source parser adapters."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.core.rag_source_parsers import parse_bytes, parse_html, parse_pdf, parse_text


def _pdf_bytes() -> bytes:
    """Return a minimal valid PDF with one page of text."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length 44 >>\nstream\nBT /F1 12 Tf 100 700 Td (Hello PDF) Tj ET\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n0000000000 65535 f\n0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\n0000000266 00000 n\n0000000359 00000 n\n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n437\n%%EOF\n"
    )


def test_parse_pdf_extracts_text():
    raw = _pdf_bytes()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        result = parse_pdf(raw, tmp_path, max_pages=250, max_chars=200000)
    finally:
        tmp_path.unlink(missing_ok=True)
    assert "Hello PDF" in result.text
    assert result.parser_id == "pypdf"
    assert result.page_count == 1


def test_parse_pdf_page_limit():
    raw = _pdf_bytes()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        result = parse_pdf(raw, tmp_path, max_pages=0, max_chars=200000)
    finally:
        tmp_path.unlink(missing_ok=True)
    assert result.page_count == 1
    assert any("first 0" in w for w in result.warnings)


def test_parse_pdf_character_limit():
    raw = _pdf_bytes()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        result = parse_pdf(raw, tmp_path, max_pages=250, max_chars=5)
    finally:
        tmp_path.unlink(missing_ok=True)
    assert len(result.text) <= 5
    assert result.truncated is True
    assert any("truncated" in w for w in result.warnings)


def test_parse_html_strips_scripts_and_styles():
    html = b"""
    <html>
      <head><style>body{color:red}</style><script>alert('xss')</script></head>
      <body>
        <h1>Title</h1>
        <p>Visible paragraph.</p>
        <script>another script</script>
      </body>
    </html>
    """
    result = parse_html(html, max_chars=200000)
    assert "Title" in result.text
    assert "Visible paragraph" in result.text
    assert "alert" not in result.text
    assert "color:red" not in result.text
    assert "another script" not in result.text


def test_parse_html_character_limit():
    html = b"<p>" + b"x " * 10000 + b"</p>"
    result = parse_html(html, max_chars=10)
    assert len(result.text) <= 10
    assert result.truncated is True
    assert any("truncated" in w for w in result.warnings)


def test_parse_text():
    raw = b"Hello, world!\nSecond line."
    result = parse_text(raw, max_chars=200000)
    assert "Hello, world!" in result.text
    assert "Second line" in result.text


def test_parse_text_invalid_utf8():
    raw = b"\xff\xfe\x00"
    result = parse_text(raw, max_chars=200000)
    assert result.text != ""
    assert any("invalid UTF-8" in w for w in result.warnings)


def test_parse_bytes_dispatches_pdf():
    raw = _pdf_bytes()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(raw)
        tmp_path = Path(tmp.name)
    try:
        result = parse_bytes(
            raw,
            content_type="application/pdf",
            temp_path=tmp_path,
            max_pages=250,
            max_chars=200000,
            preview_chars=10000,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    assert "Hello PDF" in result.text


def test_parse_bytes_dispatches_html():
    html = b"<html><body><p>HTML content</p></body></html>"
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        result = parse_bytes(
            html,
            content_type="text/html",
            temp_path=tmp_path,
            max_pages=250,
            max_chars=200000,
            preview_chars=10000,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
    assert "HTML content" in result.text


def test_parse_bytes_unsupported_type():
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        with pytest.raises(RuntimeError):
            parse_bytes(
                b"data",
                content_type="application/octet-stream",
                temp_path=tmp_path,
                max_pages=250,
                max_chars=200000,
                preview_chars=10000,
            )
    finally:
        tmp_path.unlink(missing_ok=True)
