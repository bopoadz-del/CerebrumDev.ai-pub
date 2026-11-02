"""The upload 409 guard distinguishes a LIVE upload from an ORPHANED one.

"processing" in the persisted session only means a conflict while a background
task is actually running it. When the worker died mid-parse (crash, OOM, a
deploy rollout killing in-flight tasks) the terminal write never happened and
the in-memory task registry no longer shows a running task -- that stale marker
used to wedge the session behind a permanent 409 "Upload already in progress"
(mobile incident 2026-11-02). Now: live task -> 409; orphan -> the old upload
is recorded failed and the new request proceeds.

Full dotted import so the mutation gate selects this module.
"""
import io

import pytest
from fastapi.testclient import TestClient

import app.routers.upload  # noqa: F401  dotted token for CI-M
import app.routers.upload as upload_router

_SESSION = "sess_unwedge"

# A tiny PDF-looking payload: the response under test is the POST's own status
# code (the guard), not the background parse, so the bytes only need to clear
# the extension + content-type checks.
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

    create_session(_SESSION, "dev-local")
    return TestClient(app)


def _wedge(monkeypatch, task_status: str) -> None:
    """Persist upload.status == 'processing' and pin the task registry's
    answer for this session to ``task_status``."""
    from app.core.session_store import get_session, update_session

    state = get_session(_SESSION)
    state.upload.status = "processing"
    state.upload.message = "Parsing documents..."
    update_session(_SESSION, state)
    monkeypatch.setattr(
        upload_router, "get_task_status", lambda tid: {"status": task_status}
    )


def _post(client):
    return client.post(
        f"/v1/sessions/{_SESSION}/product/ingestion/upload",
        files={"files": ("plan.pdf", io.BytesIO(_PDF_BYTES), "application/pdf")},
    )


@pytest.mark.parametrize(
    "task_status,expected_code",
    [
        # a task is genuinely running this session's upload -> real conflict
        ("running", 409),
        # the registry has no live task (worker died / process restarted):
        # the marker is an orphan -> the new upload proceeds
        ("unknown", 200),
    ],
)
def test_processing_marker_blocks_only_while_a_task_is_live(
    client, monkeypatch, task_status, expected_code
):
    _wedge(monkeypatch, task_status)
    resp = _post(client)
    assert resp.status_code == expected_code
    if expected_code == 409:
        assert resp.json()["detail"] == "Upload already in progress"
    else:
        assert resp.json()["files_received"] == 1
