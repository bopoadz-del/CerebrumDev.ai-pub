import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient

from app.core.auth import require_api_key, verify_production_auth


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    router = APIRouter()

    @router.get("/protected")
    async def protected():
        return {"ok": True}

    app = FastAPI()
    app.include_router(router, prefix="/v1", dependencies=[Depends(require_api_key)])
    return TestClient(app)


def test_open_mode_requires_explicit_opt_in(client, monkeypatch):
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)

    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    assert client.get("/v1/protected").status_code == 200

    # Without the opt-in the same configuration must refuse, not fall open.
    monkeypatch.delenv("ALLOW_ANONYMOUS_DEV", raising=False)
    assert client.get("/v1/protected").status_code == 401


def test_bearer_key_required(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "secret123")

    response = client.get("/v1/protected")
    assert response.status_code == 401

    response = client.get("/v1/protected", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401

    response = client.get("/v1/protected", headers={"Authorization": "Bearer secret123"})
    assert response.status_code == 200


def test_x_api_key_header(client, monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "secret123")
    response = client.get("/v1/protected", headers={"X-API-Key": "secret123"})
    assert response.status_code == 200


def test_master_key_read_per_call_not_frozen_at_import(client, monkeypatch):
    """A key set after import must take effect.

    The old code snapshotted the key into a module global at import time, so a
    key that arrived late read as absent and downgraded auth for the whole
    process lifetime.
    """
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    assert client.get("/v1/protected").status_code == 200

    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "arrived-late")
    assert client.get("/v1/protected").status_code == 401
    assert client.get(
        "/v1/protected", headers={"X-API-Key": "arrived-late"}
    ).status_code == 200


def test_verify_production_auth_passes_with_key(monkeypatch):
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "secret")
    monkeypatch.delenv("ALLOW_ANONYMOUS_DEV", raising=False)
    verify_production_auth()


def test_verify_production_auth_passes_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    verify_production_auth()


@pytest.mark.parametrize("env_value", ["production", "prod"])
def test_verify_production_auth_refuses_anonymous_dev_in_production(
    monkeypatch, env_value
):
    """ALLOW_ANONYMOUS_DEV is refused in production even when a master key exists."""
    monkeypatch.setenv("ENV", env_value)
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-present")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    with pytest.raises(RuntimeError, match="ALLOW_ANONYMOUS_DEV"):
        verify_production_auth()


# --- misconfiguration matrix ---------------------------------------------
#
# The previous guard fired only when ENV was exactly "production". Every other
# spelling -- and an unset ENV, which is what a typo in the variable NAME
# produces -- booted with anonymous access to every tenant's data. Enumerating
# the spellings is the shape that catches that class, because it fails for any
# guard that pattern-matches on ENV instead of on the credential itself.
@pytest.mark.parametrize(
    "env_value",
    ["production", "prod", "PRODUCTION", "Production", "prod-eu", "staging", "live", "", None],
)
def test_no_credential_never_boots_without_opt_in(monkeypatch, env_value):
    if env_value is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", env_value)
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.delenv("ALLOW_ANONYMOUS_DEV", raising=False)

    with pytest.raises(RuntimeError, match="Refusing to start"):
        verify_production_auth()


@pytest.mark.parametrize(
    "env_value",
    ["production", "prod", "PRODUCTION", "", None],
)
def test_anonymous_principal_never_issued_without_opt_in(client, monkeypatch, env_value):
    """Whatever ENV says, no credential plus no opt-in must mean no access."""
    if env_value is None:
        monkeypatch.delenv("ENV", raising=False)
    else:
        monkeypatch.setenv("ENV", env_value)
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.delenv("ALLOW_ANONYMOUS_DEV", raising=False)

    assert client.get("/v1/protected").status_code == 401


@pytest.fixture
def session_client(monkeypatch, tmp_path):
    storage_path = str(tmp_path / "storage")
    monkeypatch.setenv("STORAGE_PATH", storage_path)
    # These routes run unauthenticated here, which now needs the explicit
    # opt-in rather than the old no-key-means-open default.
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    import app.core.session_persistence as session_persistence
    monkeypatch.setattr(session_persistence, "STORAGE_PATH", storage_path)
    from app.routers import sessions

    app = FastAPI()
    app.include_router(sessions.router, prefix="/v1/sessions")
    return TestClient(app)


def test_create_session_generates_id(session_client):
    response = session_client.post("/v1/sessions/")
    assert response.status_code == 200
    data = response.json()
    assert "session_id" in data
    assert data["session_id"].startswith("sess_")
    assert len(data["session_id"]) > len("sess_")

    # Any client-supplied X-Session-ID must be ignored.
    response2 = session_client.post("/v1/sessions/", headers={"X-Session-ID": "sess_malicious"})
    assert response2.status_code == 200
    assert response2.json()["session_id"] != "sess_malicious"
    assert response2.json()["session_id"].startswith("sess_")
