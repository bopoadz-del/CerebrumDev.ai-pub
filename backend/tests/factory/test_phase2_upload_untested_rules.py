"""F-1a rework: the three untested upload rules the grade named.

These are tests-only; the upload feature itself is unchanged. Each rule is
proved on BOTH surfaces — the generated product's kernel route
(``/v1/product/ingestion/upload``) and the pilot's Factory-floor route
(``/v1/sessions/{id}/product/ingestion/upload``):

1. The status GET reflects the job's REAL state (not a hardwired "queued").
2. A job is reachable ONLY under the tenant that created it (cross-tenant 404).
3. An allowed extension carried by a MISMATCHED content-type is refused 415.

Each test has a documented mutant (noted in-line) that it is designed to catch.
"""

from __future__ import annotations

import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cerebrum_product_kernel.ingestion import parse_job
from app.cerebrum_product_kernel.ingestion.parse_job import ParseStatus
from app.cerebrum_product_kernel.ingestion.router import router as ingestion_router

_PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


# ── kernel surface (stubbed app.tenancy, like test_phase2_client_ingestion) ──


def _stub_tenancy(monkeypatch, token_to_tenant):
    mod = types.ModuleType("app.tenancy")

    class TenantRefused(Exception):
        pass

    class _Tenant:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id
            self.roles = []

    def resolve_tenant(headers):
        auth = (headers or {}).get("authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        tid = token_to_tenant.get(token)
        if tid is None:
            raise TenantRefused(token)
        return _Tenant(tid)

    mod.TenantRefused = TenantRefused
    mod.resolve_tenant = resolve_tenant
    monkeypatch.setitem(sys.modules, "app.tenancy", mod)
    return mod


def _kernel_client(monkeypatch, tmp_path, token_to_tenant):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    _stub_tenancy(monkeypatch, token_to_tenant)
    app = FastAPI()
    app.include_router(ingestion_router)
    return TestClient(app)


# ── Floor surface (the real app.main, ALLOW_ANONYMOUS_DEV) ───────────────────


def _floor_env(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    monkeypatch.setenv("ACCOUNTS_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()


def _register_user(email):
    """Register an account on app.main; return (account_id, bearer_headers)."""
    from app.main import app

    # A throwaway client keeps the login cookie off the dev client's jar.
    reg = TestClient(app).post(
        "/v1/auth/register", json={"email": email, "password": "pilot-pass-123"}
    )
    assert reg.status_code == 201, reg.text
    body = reg.json()
    return body["account_id"], {"Authorization": f"Bearer {body['login_token']}"}


# ── Rule 1: status GET reflects the REAL job state, not a hardwired queued ───
#
# MUTANT caught: hardwiring ``"status": "queued"`` in
# router.job_status_payload (shared by both surfaces) -> both asserts below
# fail, because the advanced job reads back queued instead of parsing/parsed.


def test_status_reflects_real_state_both_surfaces(monkeypatch, tmp_path):
    # Kernel surface: upload, advance to PARSING, GET shows parsing.
    kc = _kernel_client(monkeypatch, tmp_path / "k", {"tok-a": "tenant-a"})
    headers = {"Authorization": "Bearer tok-a"}
    up = kc.post(
        "/v1/product/ingestion/upload",
        headers=headers,
        files={"files": ("spec.pdf", _PDF_BYTES, "application/pdf")},
    )
    assert up.status_code == 200, up.text
    job_id = up.json()["job_id"]
    assert up.json()["status"] == "queued"  # baseline before we advance it

    parse_job.set_status("tenant-a", job_id, ParseStatus.PARSING)
    got = kc.get(f"/v1/product/ingestion/upload/{job_id}", headers=headers)
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "parsing", "kernel status must be the REAL state"

    # Floor surface: upload (dev-local), advance to PARSED, GET shows parsed.
    _floor_env(monkeypatch, tmp_path / "f")
    from app.core.session_store import create_session
    from app.main import app

    create_session("sess_rule1", "dev-local")
    fc = TestClient(app)
    fup = fc.post(
        "/v1/sessions/sess_rule1/product/ingestion/upload",
        files={"files": ("plan.pdf", _PDF_BYTES, "application/pdf")},
    )
    assert fup.status_code == 200, fup.text
    fjob = fup.json()["job_id"]
    assert fup.json()["status"] == "queued"

    parse_job.set_status("dev-local", fjob, ParseStatus.PARSED)
    fgot = fc.get(f"/v1/sessions/sess_rule1/product/ingestion/upload/{fjob}")
    assert fgot.status_code == 200, fgot.text
    assert fgot.json()["status"] == "parsed", "floor status must be the REAL state"


# ── Rule 2: a job is reachable only under its own tenant (cross-tenant 404) ──
#
# MUTANT caught: hardwiring the route's resolved tenant to a constant
# (_require_product_tenant on the kernel route, _require_build_tenant on the
# Floor route) -> the two callers collapse onto one store, so B's GET of A's
# job 200s instead of 404.


def test_cross_tenant_route_isolation_both_surfaces(monkeypatch, tmp_path):
    # Kernel surface: tenant-a uploads; tenant-b's GET of A's job -> 404.
    kc = _kernel_client(
        monkeypatch, tmp_path / "k", {"tok-a": "tenant-a", "tok-b": "tenant-b"}
    )
    a_up = kc.post(
        "/v1/product/ingestion/upload",
        headers={"Authorization": "Bearer tok-a"},
        files={"files": ("spec.pdf", _PDF_BYTES, "application/pdf")},
    )
    assert a_up.status_code == 200, a_up.text
    a_job = a_up.json()["job_id"]

    # tenant-a sees its own job...
    own = kc.get(
        f"/v1/product/ingestion/upload/{a_job}",
        headers={"Authorization": "Bearer tok-a"},
    )
    assert own.status_code == 200, own.text
    # ...tenant-b does not.
    other = kc.get(
        f"/v1/product/ingestion/upload/{a_job}",
        headers={"Authorization": "Bearer tok-b"},
    )
    assert other.status_code == 404, other.text

    # Floor surface: tenant A = dev-local uploads; tenant B = a user account,
    # whose GET of A's job on its OWN session -> 404 (a dev-principal GET of a
    # real account's session would 403 at the guard, so the user is B here).
    _floor_env(monkeypatch, tmp_path / "f")
    from app.core.session_store import create_session
    from app.main import app

    acct_b, b_headers = _register_user("tenant-b@example.com")
    create_session("sess_dev_a", "dev-local")
    create_session("sess_user_b", acct_b)

    dev_client = TestClient(app)  # own cookie jar -> stays the dev principal
    dev_up = dev_client.post(
        "/v1/sessions/sess_dev_a/product/ingestion/upload",
        files={"files": ("plan.pdf", _PDF_BYTES, "application/pdf")},
    )
    assert dev_up.status_code == 200, dev_up.text
    dev_job = dev_up.json()["job_id"]

    # dev-local sees its own job...
    dev_own = dev_client.get(
        f"/v1/sessions/sess_dev_a/product/ingestion/upload/{dev_job}"
    )
    assert dev_own.status_code == 200, dev_own.text
    # ...the user tenant, on its own session, does not.
    b_client = TestClient(app)
    b_other = b_client.get(
        f"/v1/sessions/sess_user_b/product/ingestion/upload/{dev_job}",
        headers=b_headers,
    )
    assert b_other.status_code == 404, b_other.text


# ── Rule 3: allowed extension + mismatched content-type -> 415 ───────────────
#
# MUTANT caught: upload_validation._content_type_matches -> always True ->
# the .pdf declared image/png is accepted (200) instead of refused 415, on
# both surfaces (both share validate_and_read).


def test_content_type_mismatch_415_both_surfaces(monkeypatch, tmp_path):
    # Kernel surface: a .pdf name with image/png content-type -> 415.
    kc = _kernel_client(monkeypatch, tmp_path / "k", {"tok-a": "tenant-a"})
    k_resp = kc.post(
        "/v1/product/ingestion/upload",
        headers={"Authorization": "Bearer tok-a"},
        files={"files": ("spec.pdf", _PDF_BYTES, "image/png")},
    )
    assert k_resp.status_code == 415, k_resp.text

    # Floor surface: same mismatch on the pilot route -> 415.
    _floor_env(monkeypatch, tmp_path / "f")
    from app.core.session_store import create_session
    from app.main import app

    create_session("sess_rule3", "dev-local")
    fc = TestClient(app)
    f_resp = fc.post(
        "/v1/sessions/sess_rule3/product/ingestion/upload",
        files={"files": ("plan.pdf", _PDF_BYTES, "image/png")},
    )
    assert f_resp.status_code == 415, f_resp.text


# ── Rule 4: ALL files are validated before ANY is stored (no partial store) ──
#
# MUTANT caught: storing each file as the loop validates it (the pre-rework
# single-pass loop) -> the valid first file is already stored when the second
# file 415s, so the tenant's object store is non-empty after the 415.


def test_multi_file_all_validated_before_store_both_surfaces(monkeypatch, tmp_path):
    from app.cerebrum_product_kernel.ingestion.object_store import list_objects

    # Kernel surface: [valid.pdf, bad.zip] -> 415, and NOTHING stored.
    kc = _kernel_client(monkeypatch, tmp_path / "k", {"tok-a": "tenant-a"})
    resp = kc.post(
        "/v1/product/ingestion/upload",
        headers={"Authorization": "Bearer tok-a"},
        files=[
            ("files", ("good.pdf", _PDF_BYTES, "application/pdf")),
            ("files", ("bad.zip", b"PK\x03\x04bad", "application/zip")),
        ],
    )
    assert resp.status_code == 415, resp.text
    assert list_objects("tenant-a") == [], "a rejected batch must store nothing"

    # Floor surface: same batch on the pilot route -> 415, nothing stored.
    _floor_env(monkeypatch, tmp_path / "f")
    from app.core.session_store import create_session
    from app.main import app

    create_session("sess_rule4", "dev-local")
    fc = TestClient(app)
    f_resp = fc.post(
        "/v1/sessions/sess_rule4/product/ingestion/upload",
        files=[
            ("files", ("good.pdf", _PDF_BYTES, "application/pdf")),
            ("files", ("bad.zip", b"PK\x03\x04bad", "application/zip")),
        ],
    )
    assert f_resp.status_code == 415, f_resp.text
    assert list_objects("dev-local") == [], "a rejected batch must store nothing"
