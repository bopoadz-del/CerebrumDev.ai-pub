"""PR #51 — build routes fail closed AT THE ROUTE for a no-tenant or
cross-tenant principal, and admin-issued tenant-scoped API tokens.

ORDERS_28OCT_0230Z #1(b) / 0245Z #5. The worker already refuses an unbound
build (no_authenticated_tenant); these lock the refusal at the route, before a
job or workspace is created, and prove the tenant-scoped token path a
programmatic caller must use instead of the master key.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core import accounts_store
from app.core.auth import Principal, _resolve_user_principal
from app.routers.session_product import _require_build_tenant


class _State:
    """Minimal stand-in for a SessionState: only the owner field is read."""

    def __init__(self, owner: str) -> None:
        self.user_id = owner


# --- route guard (unit, no HTTP) --------------------------------------------

def test_no_tenant_principal_is_refused_403_before_any_job():
    st = _State("acct_owner")
    for p in (
        Principal(kind="admin"),
        Principal(kind="user", account_id=None),
        Principal(kind="user", account_id=""),
    ):
        with pytest.raises(HTTPException) as exc:
            _require_build_tenant(st, p)
        assert exc.value.status_code == 403
        assert exc.value.detail["error"] == "no_tenant"


def test_dev_principal_binds_the_explicit_dev_tenant_not_empty():
    # The dev principal binds the EXPLICIT dev tenant ("dev-local", the owner
    # routers/sessions.py assigns a credential-free caller), never "": the
    # guard has no tenant-less hole. On a dev-owned session it returns that
    # tenant (so the build still isolates on the dev store).
    from app.core.auth import DEV_LOCAL_TENANT

    st = _State(DEV_LOCAL_TENANT)
    assert _require_build_tenant(st, Principal(kind="dev")) == DEV_LOCAL_TENANT
    # Owner-less session (never created through sessions.py): still binds the
    # explicit dev tenant rather than an empty string.
    assert _require_build_tenant(_State(""), Principal(kind="dev")) == DEV_LOCAL_TENANT


def test_dev_principal_is_refused_cross_tenant_on_a_real_session():
    # The SAME cross-tenant check runs for dev as for a real user: a dev
    # principal may not build a session owned by a real account.
    st = _State("acct_owner")
    with pytest.raises(HTTPException) as exc:
        _require_build_tenant(st, Principal(kind="dev"))
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "tenant_mismatch"


def test_cross_tenant_user_is_refused_403():
    st = _State("acct_owner")
    with pytest.raises(HTTPException) as exc:
        _require_build_tenant(st, Principal(kind="user", account_id="acct_intruder"))
    assert exc.value.status_code == 403
    assert exc.value.detail["error"] == "tenant_mismatch"


def test_same_tenant_user_is_allowed_and_returns_the_account():
    st = _State("acct_owner")
    got = _require_build_tenant(st, Principal(kind="user", account_id="acct_owner"))
    assert got == "acct_owner"


# --- admin-issued tenant-scoped tokens (HTTP) -------------------------------

@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ACCOUNTS_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.setenv("ACCOUNTS_EXPOSE_DEV_TOKENS", "1")
    monkeypatch.delenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_MAX", raising=False)
    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()
    from app.routers import accounts

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    return TestClient(app)


def _make_account(client, email):
    reg = client.post(
        "/v1/auth/register", json={"email": email, "password": "pilot-pass-123"}
    )
    assert reg.status_code == 201, reg.text
    return reg.json()["account_id"]


def test_admin_issues_tenant_scoped_token_resolves_revokes_and_is_single_tenant(
    client, monkeypatch
):
    acct_a = _make_account(client, "a@example.com")
    acct_b = _make_account(client, "b@example.com")

    # Turn on the master key: now admin-only routes are reachable with it.
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-test-key")
    master = {"X-API-Key": "master-test-key"}

    # A non-admin (open-dev, no master header) cannot mint tenant keys.
    denied = client.post("/v1/auth/admin/keys", json={"account_id": acct_a})
    assert denied.status_code in (401, 403)

    # Admin issues a tenant-scoped key for account A.
    res = client.post(
        "/v1/auth/admin/keys", headers=master, json={"account_id": acct_a}
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["account_id"] == acct_a
    api_key = body["api_key"]
    key_id = body["key_id"]

    # It resolves to a USER principal bound to account A (and no other).
    p = _resolve_user_principal(api_key)
    assert p is not None and p.kind == "user" and p.account_id == acct_a

    # That token passes the build guard for A's session, and is refused
    # (tenant_mismatch) for B's session — single-tenant by construction.
    assert _require_build_tenant(_State(acct_a), p) == acct_a
    with pytest.raises(HTTPException) as exc:
        _require_build_tenant(_State(acct_b), p)
    assert exc.value.status_code == 403 and exc.value.detail["error"] == "tenant_mismatch"

    # Issuing for a non-existent account fails closed.
    missing = client.post(
        "/v1/auth/admin/keys", headers=master, json={"account_id": "acct_nope"}
    )
    assert missing.status_code == 404

    # Revoke (admin) -> the token no longer resolves.
    rev = client.request(
        "DELETE", f"/v1/auth/admin/keys/{acct_a}/{key_id}", headers=master
    )
    assert rev.status_code == 200, rev.text
    assert _resolve_user_principal(api_key) is None


# --- worker-side tenant assertion (bar b) -----------------------------------
# The worker independently refuses a job whose bound tenant is not the account
# the build is owned/metered under — the second boundary behind the route guard.

from app.factory.build.tenant_bind import TenantMismatchError, bind_tenant_store


def test_bind_tenant_store_same_owner_binds(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    b = bind_tenant_store("acct_a", owner="acct_a")
    assert b is not None and b.tenant_key


def test_bind_tenant_store_refuses_cross_tenant_job(monkeypatch, tmp_path):
    # A job that would isolate on acct_a's store while owned by acct_b is a
    # cross-tenant build: the worker refuses it rather than bind, even though
    # no route guard ran.
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    with pytest.raises(TenantMismatchError):
        bind_tenant_store("acct_a", owner="acct_b")


def test_bind_tenant_store_unbound_is_none_not_mismatch(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    # No identity -> unbound (worker then refuses no_authenticated_tenant),
    # never a mismatch — an empty identity cannot be "the wrong tenant".
    assert bind_tenant_store(None) is None
    assert bind_tenant_store("", owner="acct_a") is None


def test_bind_tenant_store_single_identity_path_unchanged(monkeypatch, tmp_path):
    # Resume / internal callers that pass one identity and no owner keep the
    # original single-identity binding.
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    assert bind_tenant_store("acct_a").tenant_key


# --- dev mode cannot ride into production (bar c) ---------------------------

def test_production_refuses_to_boot_with_anonymous_dev(monkeypatch):
    from app.core.auth import verify_production_auth

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    with pytest.raises(RuntimeError, match="ALLOW_ANONYMOUS_DEV"):
        verify_production_auth()


def test_non_production_dev_boots(monkeypatch):
    from app.core.auth import verify_production_auth

    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    verify_production_auth()  # does not raise


# --- route-level HTTP tests (bar a) -----------------------------------------
# Each guarded route is exercised over HTTP: no-tenant (admin) -> 403 no_tenant,
# cross-tenant (dev on a real account's session) -> 403 tenant_mismatch, and
# same-tenant -> the build is launched with the store bound to the SESSION's
# tenant. The 403s fire at the guard before any blueprint work, and fail when
# the route's _require_build_tenant call is deleted (mutation per route).

from types import SimpleNamespace


def _stub_build_path(monkeypatch, tmp_path, recorder):
    """Stub the pre-gates and the downstream build entries so a same-tenant
    request reaches the point where the tenant is bound, without a real build.
    The route's auth, ownership and _require_build_tenant run for real."""
    import app.factory.build.platform_identity as pid
    import app.factory.locale_choice as lc
    import app.factory.platform_chat_flow as pcf
    import app.routers.session_product as sp

    monkeypatch.setattr(sp, "require_remaining", lambda *a, **k: None)
    monkeypatch.setattr(sp, "require_llm_rate", lambda *a, **k: None)
    monkeypatch.setattr(sp, "_refuse_without_build_level", lambda *a, **k: None)
    monkeypatch.setattr(sp, "_consume_generation_on_start", lambda *a, **k: None)
    monkeypatch.setattr(sp, "resolve_blocks_root", lambda *a, **k: tmp_path)

    class _BP:
        product_id = "p1"

        @classmethod
        def model_validate(cls, raw):
            return cls()

    monkeypatch.setattr(sp, "ProductBlueprint", _BP)

    def _gen(bp, out, **kw):
        recorder["generate_tenant"] = kw.get("tenant_identity")
        recorder["generate_quota"] = kw.get("quota_account_id")
        return {
            "output_dir": str(out),
            "inputs_hash": "h" * 12,
            "product_id": "p1",
            "canonical_output": None,
            "engine": "runner",
            "build": {},
        }

    monkeypatch.setattr(sp, "generate_product", _gen)

    class _Lint:
        ok = True
        errors: list = []

    monkeypatch.setattr(pcf, "has_running_build", lambda *a, **k: False)
    monkeypatch.setattr(
        pcf, "_compile_and_lint_approved", lambda *a, **k: {"lint": _Lint()}
    )

    # /pilot runs for real down to the binding seam: resume_pilot_cycle is NOT
    # stubbed — it derives tenant_identity from the session and hands it to
    # generate_product -> start_runner_build -> bind_tenant_store. Only the
    # pre-gates and generate_product are stubbed, so the test records the
    # tenant the JOB'S OWN code passes (breaks if that derivation breaks), the
    # same strength as /generate — not a digest the stub recomputes.
    class _Bar:
        reaches_pilot = True

    import app.factory.build.build_level as bl

    monkeypatch.setattr(pcf, "is_pilot_ready", lambda *a, **k: False)
    monkeypatch.setattr(pcf, "sync_blueprint_intake", lambda *a, **k: None)
    monkeypatch.setattr(pcf, "ProductBlueprint", _BP)
    monkeypatch.setattr(bl, "bar_for", lambda *a, **k: _Bar())
    monkeypatch.setattr(pcf, "_blocks_root", lambda *a, **k: tmp_path)
    monkeypatch.setattr(
        pcf, "_generation_output_dir", lambda *a, **k: tmp_path / "pilotout"
    )
    monkeypatch.setattr(pcf, "_live_build_thread", lambda *a, **k: None)
    monkeypatch.setattr(pcf, "ensure_platform_id", lambda *a, **k: "plt_x")

    def _gen_pilot(bp, out, **kw):
        recorder["pilot_tenant"] = kw.get("tenant_identity")
        recorder["pilot_quota"] = kw.get("quota_account_id")
        return {
            "output_dir": str(out),
            "inputs_hash": "h" * 12,
            "product_id": "p1",
            "canonical_output": None,
            "engine": "runner",
            "build": {},
        }

    monkeypatch.setattr(pcf, "generate_product", _gen_pilot)

    # /n3-reseed binds NO per-tenant store: reseed_and_ingest_n3 does store-gate
    # ingest and never calls generate_product/start_runner_build/bind_tenant_store.
    # Its tenant isolation is the route guard (the 403 tests) + the session's own
    # workspace. Record that the same-tenant caller reaches the real reseed entry
    # with the session owner's state. (There is no job-level "bound store" to
    # observe for n3, unlike generate/pilot — surfaced to the orchestrator.)
    def _reseed(state, **k):
        recorder["reseed_owner"] = state.user_id
        return {"ok": True, "summary": "ok"}

    monkeypatch.setattr(pcf, "reseed_and_ingest_n3", _reseed)
    monkeypatch.setattr(lc, "sync_blueprint_intake", lambda *a, **k: None)
    monkeypatch.setattr(pid, "ensure_platform_id", lambda *a, **k: "plt_x")


@pytest.fixture()
def routes(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ACCOUNTS_DB_PATH", str(tmp_path / "accounts.db"))
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.setenv("ACCOUNTS_EXPOSE_DEV_TOKENS", "1")
    monkeypatch.setenv("FACTORY_STAGE_RESUME", "0")
    monkeypatch.delenv("BILLING_ENFORCEMENT", raising=False)
    monkeypatch.delenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", raising=False)
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_MAX", raising=False)
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()

    from fastapi import Depends

    from app.core.auth import DEV_LOCAL_TENANT, require_api_key
    from app.core.session_store import create_session, update_session
    from app.routers import accounts, session_product

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    app.include_router(
        session_product.router,
        prefix="/v1/sessions",
        dependencies=[Depends(require_api_key)],
    )
    client = TestClient(app)

    acct_a = _make_account(client, "owner@example.com")
    _make_account(client, "intruder@example.com")

    # Mint acct_a's tenant-scoped key (needs the master key present).
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-test-key")
    master = {"X-API-Key": "master-test-key"}
    minted = client.post(
        "/v1/auth/admin/keys", headers=master, json={"account_id": acct_a}
    )
    assert minted.status_code == 201, minted.text
    tok_a = minted.json()["api_key"]

    sess = "sess_routeguard00"
    st = create_session(session_id=sess, user_id=acct_a)
    st.product_design.blueprint = {"product_id": "p1"}
    st.product_design.blueprint_approved = True
    st.product_design.plan = {"ok": 1}
    update_session(sess, st)

    # A session owned by the dev tenant, for the dev-principal generate path.
    sess_dev = "sess_routeguard_dev"
    sd = create_session(session_id=sess_dev, user_id=DEV_LOCAL_TENANT)
    sd.product_design.blueprint = {"product_id": "p1"}
    sd.product_design.blueprint_approved = True
    sd.product_design.plan = {"ok": 1}
    update_session(sess_dev, sd)

    # Registration left a session cookie on the client; drop it so a request
    # with no auth header is a genuinely credential-free caller (the dev
    # principal), not the last-registered user riding a cookie.
    client.cookies.clear()

    return SimpleNamespace(
        client=client,
        sess=sess,
        sess_dev=sess_dev,
        acct_a=acct_a,
        tok_a=tok_a,
        master=master,
        monkeypatch=monkeypatch,
    )


_N3_BODY = {
    "builds_sha": "abc1234",
    "builds_branch": "build/plt_x",
    "cli_authored_ids": ["id1"],
}


def _post(routes, route, headers, json=None):
    url = f"/v1/sessions/{routes.sess}/product/{route}"
    body = _N3_BODY if route == "n3-reseed" else (json if json is not None else {})
    return routes.client.post(url, headers=headers, json=body)


@pytest.mark.parametrize("route", ["generate", "pilot", "n3-reseed"])
def test_route_refuses_no_tenant_admin_before_any_job(routes, route):
    # Admin (master key) owns no tenant store -> 403 no_tenant at the guard,
    # before any blueprint/build work.
    r = _post(routes, route, routes.master)
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"] == "no_tenant"


@pytest.mark.parametrize("route", ["generate", "pilot", "n3-reseed"])
def test_route_refuses_cross_tenant_dev_on_real_session(routes, route):
    # No credential + ALLOW_ANONYMOUS_DEV -> dev principal, whose tenant is
    # "dev-local", not the session's real owner -> 403 tenant_mismatch.
    routes.monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    r = _post(routes, route, {})
    assert r.status_code == 403, r.text
    assert r.json()["detail"]["error"] == "tenant_mismatch"


def test_generate_same_tenant_launches_bound_to_the_session_tenant(routes, tmp_path):
    recorder: dict = {}
    _stub_build_path(routes.monkeypatch, tmp_path, recorder)
    r = _post(routes, "generate", {"Authorization": f"Bearer {routes.tok_a}"})
    assert r.status_code == 200, r.text
    # The build was launched with the store bound to the SESSION's owner,
    # never a caller-supplied value.
    assert recorder.get("generate_tenant") == routes.acct_a


def test_pilot_same_tenant_launches_bound_to_the_session_tenant(routes, tmp_path):
    recorder: dict = {}
    _stub_build_path(routes.monkeypatch, tmp_path, recorder)
    r = _post(routes, "pilot", {"Authorization": f"Bearer {routes.tok_a}"})
    assert r.status_code == 200, r.text
    # resume_pilot_cycle's own code handed the session owner's tenant to the
    # build (both the isolation identity and the quota account).
    assert recorder.get("pilot_tenant") == routes.acct_a
    assert recorder.get("pilot_quota") == routes.acct_a


def test_n3_reseed_same_tenant_reaches_the_job_with_the_owner_session(routes, tmp_path):
    recorder: dict = {}
    _stub_build_path(routes.monkeypatch, tmp_path, recorder)
    r = _post(routes, "n3-reseed", {"Authorization": f"Bearer {routes.tok_a}"})
    assert r.status_code == 200, r.text
    # n3-reseed binds no per-tenant store (store-gate ingest only); the
    # same-tenant caller reaches the real reseed entry with the owner's session.
    assert recorder.get("reseed_owner") == routes.acct_a


# --- worker call-site pin (ORDERS_0630Z #1(a)) ------------------------------
# The owner==identity check must be pinned at the REAL build call site
# (build_jobs.start_runner_build -> bind_tenant_store(owner=quota_account_id)),
# not only at the bind helper: dropping owner=quota_account_id there must fail.

def test_start_runner_build_refuses_cross_tenant_at_the_call_site(tmp_path, monkeypatch):
    from pathlib import Path as _Path

    import app.factory.build_jobs as bj
    from app.factory.blueprint import load_blueprint
    from app.factory.build.tenant_bind import TenantMismatchError

    smoke = (
        _Path(__file__).resolve().parents[3]
        / "blueprints"
        / "examples"
        / "runner_smoke.yaml"
    )
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))

    def fake_run(*a, **k):
        pass

    class _InlineThread:
        def __init__(self, *args, **kwargs):
            self._kwargs = kwargs
            self.name = kwargs.get("name", "inline")
            self.daemon = True

        def start(self):
            self._kwargs.get("target", lambda *_: None)(*self._kwargs.get("args", ()))

        def is_alive(self):
            return False

    monkeypatch.setattr(bj, "_run", fake_run)
    monkeypatch.setattr("threading.Thread", _InlineThread)
    # The CLI-readiness probe runs before the bind in this environment; stub it
    # so the test reaches the tenant bind (the behaviour under test).
    monkeypatch.setattr(
        "app.factory.build.coder_session.raise_if_cli_session_unready",
        lambda *a, **k: None,
    )

    # The build would isolate on acct_a's store while owned/metered under
    # acct_b: the worker refuses at the real call site, before the thread starts.
    with pytest.raises(TenantMismatchError):
        bj.start_runner_build(
            load_blueprint(smoke),
            tmp_path / "out",
            tenant_identity="acct_a",
            quota_account_id="acct_b",
        )


# --- dev-principal build is tenant-checked too (ORDERS_0630Z #1(b)) ---------

def test_dev_principal_generate_binds_dev_local_quota_and_tenant(routes, tmp_path):
    # A dev principal on a dev-owned session launches with quota_account_id and
    # tenant_identity both = DEV_LOCAL_TENANT (never None), so the worker's
    # owner==identity check is live for dev builds, not a no-op.
    from app.core.auth import DEV_LOCAL_TENANT

    recorder: dict = {}
    _stub_build_path(routes.monkeypatch, tmp_path, recorder)
    routes.monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    routes.client.cookies.clear()
    r = routes.client.post(
        f"/v1/sessions/{routes.sess_dev}/product/generate", json={}
    )
    assert r.status_code == 200, r.text
    assert recorder.get("generate_quota") == DEV_LOCAL_TENANT
    assert recorder.get("generate_tenant") == DEV_LOCAL_TENANT
