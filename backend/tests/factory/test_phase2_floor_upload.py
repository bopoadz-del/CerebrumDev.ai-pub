"""F-1a: the pilot user's ``+`` attach on the Factory Floor.

The session-product route resolves the bound tenant via _require_build_tenant
(dev-local in dev), reuses the SAME kernel helpers, and returns the same shape
as the generated-product route. Runs credential-free (ALLOW_ANONYMOUS_DEV).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


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


def test_floor_upload_queues_a_real_job(client):
    resp = _upload(client, "sess_floor_upload", "plan.pdf", _PDF_BYTES, "application/pdf")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["source_name"] == "plan.pdf"
    assert body["bytes"] == len(_PDF_BYTES)
    assert body["object_id"] and body["job_id"]

    status = client.get(
        f"/v1/sessions/sess_floor_upload/product/ingestion/upload/{body['job_id']}"
    )
    assert status.status_code == 200, status.text
    assert status.json()["status"] == "queued"
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
