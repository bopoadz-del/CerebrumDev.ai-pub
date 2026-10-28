"""F-1a: the pilot user's ``+`` attach on the Factory Floor.

The session-product route resolves the bound tenant via _require_build_tenant
(dev-local in dev), reuses the SAME kernel helpers, and returns the same shape
as the generated-product route. Runs credential-free (ALLOW_ANONYMOUS_DEV).
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient


def _make_pdf(pages_text):
    """A minimal multi-page PDF whose pages carry extractable text (pypdf)."""
    n_pages = len(pages_text)
    parts = {}
    next_id = 3
    kids_ids = []
    page_to_content = {}
    content_text = {}
    for text in pages_text:
        page_id = next_id
        next_id += 1
        content_id = next_id
        next_id += 1
        kids_ids.append(page_id)
        page_to_content[page_id] = content_id
        content_text[content_id] = text

    parts[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join(f"{k} 0 R" for k in kids_ids).encode()
    parts[2] = (
        b"<< /Type /Pages /Kids [" + kids + b"] /Count "
        + str(n_pages).encode() + b" >>"
    )
    for page_id, content_id in page_to_content.items():
        parts[page_id] = (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            b"/BaseFont /Helvetica >> >> >> /Contents "
            + str(content_id).encode() + b" 0 R >>"
        )
    for content_id, text in content_text.items():
        stream = b"BT /F1 24 Tf 72 700 Td (" + text.encode() + b") Tj ET"
        parts[content_id] = (
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
            + stream + b"\nendstream"
        )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = {}
    for i in range(1, next_id):
        offsets[i] = out.tell()
        out.write(str(i).encode() + b" 0 obj\n" + parts[i] + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(b"xref\n0 " + str(next_id).encode() + b"\n")
    out.write(b"0000000000 65535 f \n")
    for i in range(1, next_id):
        out.write(f"{offsets[i]:010d} 00000 n \n".encode())
    out.write(b"trailer\n<< /Size " + str(next_id).encode() + b" /Root 1 0 R >>\n")
    out.write(b"startxref\n" + str(xref_pos).encode() + b"\n%%EOF\n")
    return out.getvalue()


# A real, parseable PDF: upload now parses inline, so a valid file reaches the
# terminal "parsed" state (not the pre-parse "queued").
_PDF_BYTES = _make_pdf(["FloorPlanPageOne", "FloorPlanPageTwo"])


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)

    from app.core.session_store import create_session
    from app.main import app

    create_session("sess_floor_upload", "dev-local")
    return TestClient(app)


def _upload(client, session_id, name, data, content_type):
    return client.post(
        f"/v1/sessions/{session_id}/product/ingestion/upload",
        files={"files": (name, data, content_type)},
    )


def test_floor_upload_parses_a_real_job(client):
    resp = _upload(client, "sess_floor_upload", "plan.pdf", _PDF_BYTES, "application/pdf")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Upload parses inline by default, so a valid PDF lands at the terminal
    # "parsed" state, not the pre-parse "queued".
    assert body["status"] == "parsed"
    assert body["source_name"] == "plan.pdf"
    assert body["bytes"] == len(_PDF_BYTES)
    assert body["object_id"] and body["job_id"]

    status = client.get(
        f"/v1/sessions/sess_floor_upload/product/ingestion/upload/{body['job_id']}"
    )
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "parsed"
    assert status.headers["cache-control"] == "no-store"


def test_floor_upload_no_file_400(client):
    # A ``+`` attach with no file selected is a client error, not a 200 with an
    # empty job: the Floor route must answer 400 (mirrors app.routers.upload and
    # the generated-product route), before any object is stored.
    resp = client.post("/v1/sessions/sess_floor_upload/product/ingestion/upload")
    assert resp.status_code == 400, resp.text


def test_floor_upload_wrong_type_415(client):
    resp = _upload(
        client, "sess_floor_upload", "clip.mp4", b"\x00\x00\x00video", "video/mp4"
    )
    assert resp.status_code == 415, resp.text


def test_floor_upload_oversize_413(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.setenv("MAX_UPLOAD_FILE_BYTES", "16")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)

    from app.core.session_store import create_session
    from app.main import app

    create_session("sess_floor_big", "dev-local")
    client = TestClient(app)
    resp = _upload(client, "sess_floor_big", "big.pdf", b"x" * 64, "application/pdf")
    assert resp.status_code == 413, resp.text
