"""A certified export reaches the user who built it, from "Your Platforms".

Owner's rule (2026-10-08): the last certified exports land in the Floor's
"Your Platforms" download for their users -- the product path -- not only an
ops bucket. That path is the session the user built it in: Your Platforms
lists the account's sessions (``GET /v1/sessions/``) and its Download button
calls ``GET /v1/sessions/{id}/product/package``, which zips the build's output
from the persistent disk and writes the certified MANIFEST.json into it.

These tests drive that path end to end with real accounts:

- the owner sees the session in their list and downloads a zip whose
  MANIFEST says ``certified: true``;
- another account neither lists it nor downloads it (404, never 403 -- the
  session's existence is not disclosed);
- after a process restart (the in-memory session map emptied, everything read
  back from disk) the owner still lists it and still downloads it certified.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def world(monkeypatch, tmp_path):
    storage_path = str(tmp_path / "storage")
    monkeypatch.setenv("STORAGE_PATH", storage_path)
    import app.core.session_persistence as session_persistence

    monkeypatch.setattr(session_persistence, "STORAGE_PATH", storage_path)
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.delenv("ALLOW_ANONYMOUS_DEV", raising=False)
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("ACCOUNTS_EXPOSE_DEV_TOKENS", "1")
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_MAX", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_WINDOW_S", raising=False)
    monkeypatch.setenv("FACTORY_CI_RUN_ID", "ci-run-1")
    monkeypatch.delenv("CEREBRUM_BUILDS_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()

    from app.core.auth import require_api_key
    from fastapi import Depends

    from app.routers import accounts, session_product, sessions

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    app.include_router(
        sessions.router, prefix="/v1/sessions", dependencies=[Depends(require_api_key)]
    )
    app.include_router(
        session_product.router,
        prefix="/v1/sessions",
        dependencies=[Depends(require_api_key)],
    )
    client = TestClient(app)

    def verified(email):
        made = client.post(
            "/v1/auth/register", json={"email": email, "password": "pilot-pass-123"}
        )
        assert made.status_code == 201, made.text
        body = made.json()
        ok = client.post(
            "/v1/auth/verify-email",
            json={"token": body["verification"]["dev_verification_token"]},
        )
        assert ok.status_code == 200, ok.text
        return {"Authorization": f"Bearer {body['login_token']}"}

    return client, verified, tmp_path


def _certified_build(monkeypatch, out):
    """A runner build that passed every gate. Which gates, and how they are
    measured, is not this test's subject -- whether the result reaches its
    owner is."""
    from app.factory import build_jobs
    from app.factory.build.ledger import BuildLedger

    (out / "app").mkdir(parents=True)
    (out / "app" / "main.py").write_text("app = None\n", encoding="utf-8")
    (out / "README.md").write_text("product", encoding="utf-8")
    BuildLedger(out / "build_ledger.jsonl").start_run(product_id=out.name, inputs_hash="abc")

    real_status = build_jobs.build_status

    def certified_status(output_dir, **kw):
        status = dict(real_status(output_dir, **kw))
        status.update(
            state="succeeded",
            pilot_ready=True,
            authorship={"agent_written": 1},
            build_level={"build_level": "production", "stop_gate": "STORE"},
        )
        return status

    monkeypatch.setattr(build_jobs, "build_status", certified_status)
    monkeypatch.setattr(
        "app.factory.build.authorship.thin_store_green_export_blocker",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "app.factory.build.store_acceptance.acceptance_export_blocker",
        lambda *a, **k: None,
    )


def _manifest(resp):
    assert resp.status_code == 200, resp.text
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    return json.loads(zf.read("MANIFEST.json"))


def test_the_owner_lists_and_downloads_the_certified_export(world, monkeypatch):
    client, verified, tmp = world
    owner = verified("owner@example.test")
    other = verified("other@example.test")

    sid = client.post("/v1/sessions/", headers=owner).json()["session_id"]
    out = tmp / "outputs" / "sessions" / sid / "product-one"
    _certified_build(monkeypatch, out)

    from app.core.session_store import get_session, update_session

    state = get_session(sid)
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "product-one",
        "inputs_hash": "abc",
        "engine": "runner",
    }
    update_session(sid, state)

    listed = [s.get("session_id") for s in client.get("/v1/sessions/", headers=owner).json()]
    assert sid in listed

    manifest = _manifest(client.get(f"/v1/sessions/{sid}/product/package", headers=owner))
    assert manifest["certified"] is True
    assert manifest["build_level"] == {"build_level": "production", "stop_gate": "STORE"}

    # Another account: not listed, not downloadable, existence not disclosed.
    assert sid not in [s.get("session_id") for s in client.get("/v1/sessions/", headers=other).json()]
    denied = client.get(f"/v1/sessions/{sid}/product/package", headers=other)
    assert denied.status_code == 404


def test_the_certified_export_survives_a_restart(world, monkeypatch):
    client, verified, tmp = world
    owner = verified("owner2@example.test")

    sid = client.post("/v1/sessions/", headers=owner).json()["session_id"]
    out = tmp / "outputs" / "sessions" / sid / "product-two"
    _certified_build(monkeypatch, out)

    from app.core import session_store

    state = session_store.get_session(sid)
    state.product_design.generation = {
        "output_dir": str(out),
        "product_id": "product-two",
        "inputs_hash": "abc",
        "engine": "runner",
    }
    session_store.update_session(sid, state)
    assert _manifest(client.get(f"/v1/sessions/{sid}/product/package", headers=owner))[
        "certified"
    ] is True

    # A restart: the process's session map is gone; disk is all there is.
    session_store._session_store.clear()

    listed = [s.get("session_id") for s in client.get("/v1/sessions/", headers=owner).json()]
    assert sid in listed
    assert _manifest(client.get(f"/v1/sessions/{sid}/product/package", headers=owner))[
        "certified"
    ] is True
