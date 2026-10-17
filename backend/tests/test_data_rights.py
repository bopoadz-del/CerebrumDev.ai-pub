"""Data rights: export and erasure.

The load-bearing shape here is CONSERVATION. "Delete returned 204" proves
nothing — a handler that deletes the account row and abandons the session
snapshot, the uploaded file, the Drive token and the vector collection returns
204 just as cheerfully. So these tests build two fully-populated accounts,
delete one, and then assert across *every* store that account A left nothing
behind and account B lost nothing. Anything that only one of the two accounts
would satisfy is a test that cannot tell erasure from over-deletion.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


class _FakeCollection:
    def __init__(self, name: str):
        self.name = name


class _FakeChromaClient:
    """Stand-in for the persistent Chroma client.

    Used so the vector-purge path is exercised deterministically on any host,
    including one without ``chromadb`` installed. It models the only two calls
    the purge makes, and it is genuinely destructive, so "did A's collection go
    and B's survive" is a real assertion rather than a mock recording.
    """

    def __init__(self):
        self.collections: dict[str, _FakeCollection] = {}

    def add(self, name: str) -> None:
        self.collections[name] = _FakeCollection(name)

    def list_collections(self):
        return list(self.collections.values())

    def delete_collection(self, name: str):
        if name not in self.collections:
            raise ValueError(f"collection {name} does not exist")
        del self.collections[name]


@pytest.fixture()
def chroma(monkeypatch):
    from app.core import chroma_store

    fake = _FakeChromaClient()
    monkeypatch.setattr(chroma_store, "_get_chroma_client", lambda: fake)
    return fake


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    storage_path = str(tmp_path / "storage")
    monkeypatch.setenv("STORAGE_PATH", storage_path)
    import app.core.session_persistence as session_persistence

    monkeypatch.setattr(session_persistence, "STORAGE_PATH", storage_path)
    return tmp_path / "storage"


@pytest.fixture()
def client(monkeypatch, storage):
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("ACCOUNTS_EXPOSE_DEV_TOKENS", "1")
    monkeypatch.delenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", raising=False)
    monkeypatch.delenv("ACCOUNTS_DB_PATH", raising=False)
    monkeypatch.delenv("ACCOUNTS_DATABASE_URL", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_MAX", raising=False)
    monkeypatch.delenv("AUTH_RATE_LIMIT_WINDOW_S", raising=False)

    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()

    # The session cache is a module-level dict; left dirty it leaks state
    # between tests and can make a purged session look present (or absent).
    from app.core import session_store

    session_store._session_store.clear()

    from app.routers import accounts, sessions

    app = FastAPI()
    app.include_router(accounts.router, prefix="/v1/auth")
    app.include_router(sessions.router, prefix="/v1/sessions")
    return TestClient(app)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

PASSWORD = "pilot-pass-123"


def _register(client, email):
    res = client.post("/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert res.status_code == 201, res.text
    return res.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _populate(client, chroma, storage, email):
    """Create an account that owns every category of data we claim to purge."""
    from app.core import accounts_store

    account = _register(client, email)
    account_id = account["account_id"]
    token = account["login_token"]

    key = client.post("/v1/auth/keys", json={"label": "ci"}, headers=_auth(token))
    assert key.status_code == 201, key.text

    created = client.post("/v1/sessions/", headers=_auth(token))
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    accounts_store.increment_usage(account_id, "generations", "lifetime")
    accounts_store.increment_usage(account_id, "generations", "lifetime")

    # An uploaded document and its extracted text.
    files_dir = storage / "sessions" / session_id / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "brief.txt").write_text(f"private brief for {email}", encoding="utf-8")

    # A Google Drive OAuth token and file metadata.
    drive_dir = storage / "google_drive" / session_id / "tokens"
    drive_dir.mkdir(parents=True, exist_ok=True)
    (drive_dir / "conn.json").write_text('{"refresh_token": "secret"}', encoding="utf-8")

    # A workbench sandbox workspace.
    bench_dir = storage / "workbench" / "sessions" / session_id
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "scratch.txt").write_text("work", encoding="utf-8")

    # Vector entries for the session.
    from app.core import chroma_store

    chroma.add(chroma_store.collection_name(session_id))

    return {
        "account_id": account_id,
        "token": token,
        "api_key": key.json()["api_key"],
        "key_id": key.json()["key_id"],
        "session_id": session_id,
        "email": email,
    }


def _db_rows(account_id):
    """Row counts straight from the tables, bypassing the public getters.

    A getter that filters rather than deletes would satisfy every API-level
    assertion while leaving the data on disk, so erasure is checked against
    the storage engine itself.
    """
    from app.core import accounts_store as s

    import sqlalchemy as sa

    with s._engine().begin() as conn:
        def count(table, column):
            return conn.execute(
                sa.select(sa.func.count()).select_from(table).where(column == account_id)
            ).scalar_one()

        return {
            "accounts": conn.execute(
                sa.select(sa.func.count())
                .select_from(s._t_accounts)
                .where(s._t_accounts.c.id == account_id)
            ).scalar_one(),
            "api_keys": count(s._t_api_keys, s._t_api_keys.c.account_id),
            "login_tokens": count(s._t_login_tokens, s._t_login_tokens.c.account_id),
            "session_owners": count(
                s._t_session_owners, s._t_session_owners.c.account_id
            ),
            "usage_counters": count(
                s._t_usage_counters, s._t_usage_counters.c.account_id
            ),
        }


# ---------------------------------------------------------------------------
# conservation
# ---------------------------------------------------------------------------


def test_delete_purges_only_the_deleted_account(client, chroma, storage):
    """Two populated accounts; delete one. Everything of A must be gone from
    every store, and everything of B must be untouched."""
    from app.core import chroma_store, session_store

    a = _populate(client, chroma, storage, "a@example.com")
    b = _populate(client, chroma, storage, "b@example.com")

    # Preconditions: both accounts really do have data everywhere.
    for who in (a, b):
        assert _db_rows(who["account_id"]) == {
            "accounts": 1,
            "api_keys": 1,
            "login_tokens": 1,
            "session_owners": 1,
            "usage_counters": 1,
        }
        assert (storage / "sessions" / who["session_id"] / "files" / "brief.txt").exists()
        assert (storage / "google_drive" / who["session_id"]).exists()
        assert (storage / "workbench" / "sessions" / who["session_id"]).exists()
        assert chroma_store.collection_name(who["session_id"]) in chroma.collections

    res = client.request(
        "DELETE",
        "/v1/auth/account",
        json={"password": PASSWORD},
        headers=_auth(a["token"]),
    )
    assert res.status_code == 204, res.text
    assert res.content in (b"", None)

    # --- A: nothing left, anywhere -----------------------------------------
    assert _db_rows(a["account_id"]) == {
        "accounts": 0,
        "api_keys": 0,
        "login_tokens": 0,
        "session_owners": 0,
        "usage_counters": 0,
    }
    assert not (storage / "sessions" / a["session_id"]).exists()
    assert not (storage / "google_drive" / a["session_id"]).exists()
    assert not (storage / "workbench" / "sessions" / a["session_id"]).exists()
    assert chroma_store.collection_name(a["session_id"]) not in chroma.collections
    # Covers the in-memory cache, the disk snapshot and the Chroma rehydrate
    # path in one assertion: a purged session must not come back.
    assert session_store.get_session(a["session_id"]) is None
    # A's credentials no longer authenticate. /me returns 401 (not 403) so
    # the Floor SPA opens Sign in instead of "Factory unreachable".
    assert client.get("/v1/auth/me", headers=_auth(a["token"])).status_code == 401
    assert (
        client.get("/v1/auth/me", headers={"X-API-Key": a["api_key"]}).status_code == 401
    )

    # --- B: untouched -------------------------------------------------------
    assert _db_rows(b["account_id"]) == {
        "accounts": 1,
        "api_keys": 1,
        "login_tokens": 1,
        "session_owners": 1,
        "usage_counters": 1,
    }
    brief = storage / "sessions" / b["session_id"] / "files" / "brief.txt"
    assert brief.read_text(encoding="utf-8") == "private brief for b@example.com"
    assert (storage / "google_drive" / b["session_id"] / "tokens" / "conn.json").exists()
    assert (storage / "workbench" / "sessions" / b["session_id"]).exists()
    assert chroma_store.collection_name(b["session_id"]) in chroma.collections
    assert session_store.get_session(b["session_id"]) is not None

    me = client.get("/v1/auth/me", headers=_auth(b["token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "b@example.com"
    keys = client.get("/v1/auth/keys", headers=_auth(b["token"]))
    assert [k["key_id"] for k in keys.json()["keys"]] == [b["key_id"]]
    from app.core import accounts_store

    assert accounts_store.get_usage(b["account_id"], "generations", "lifetime") == 2
    assert accounts_store.sessions_for_owner(b["account_id"]) == [b["session_id"]]


def test_purged_session_is_not_resurrected_from_vectors(client, chroma, storage):
    """Deleting the snapshot but leaving the Chroma collection would let
    ``get_session`` rehydrate the session as ``anonymous`` — erasure undone by
    the recovery path. The vector entries must go first."""
    from app.core import session_store

    a = _populate(client, chroma, storage, "ghost@example.com")
    session_id = a["session_id"]

    res = client.request(
        "DELETE",
        "/v1/auth/account",
        json={"password": PASSWORD},
        headers=_auth(a["token"]),
    )
    assert res.status_code == 204, res.text

    session_store._session_store.clear()  # force the recovery paths to run
    assert session_store.get_session(session_id) is None


# ---------------------------------------------------------------------------
# re-authentication
# ---------------------------------------------------------------------------


def test_delete_without_password_is_refused_and_nothing_is_touched(
    client, chroma, storage
):
    """A stolen bearer token must not be able to erase an account. The status
    code is the weak half of this test; the survival assertions are the point,
    because a handler that purges and *then* 403s would pass a status check."""
    from app.core import chroma_store

    a = _populate(client, chroma, storage, "victim@example.com")

    res = client.request(
        "DELETE",
        "/v1/auth/account",
        json={"password": "not-the-password"},
        headers=_auth(a["token"]),
    )
    assert res.status_code == 403

    assert _db_rows(a["account_id"])["accounts"] == 1
    assert (storage / "sessions" / a["session_id"] / "files" / "brief.txt").exists()
    assert chroma_store.collection_name(a["session_id"]) in chroma.collections
    assert client.get("/v1/auth/me", headers=_auth(a["token"])).status_code == 200

    # Missing password entirely is a schema error, not a silent default.
    res = client.request("DELETE", "/v1/auth/account", json={}, headers=_auth(a["token"]))
    assert res.status_code == 422
    assert _db_rows(a["account_id"])["accounts"] == 1


def test_delete_requires_an_account_credential(client):
    res = client.request("DELETE", "/v1/auth/account", json={"password": PASSWORD})
    assert res.status_code == 403


def test_password_of_one_account_cannot_delete_another(client, chroma, storage):
    """Re-auth must be bound to the *calling* account, not to any valid
    password on the platform."""
    a = _populate(client, chroma, storage, "one@example.com")
    _populate(client, chroma, storage, "two@example.com")

    from app.core import accounts_store

    assert not accounts_store.verify_account_password(a["account_id"], "wrong")
    assert accounts_store.verify_account_password(a["account_id"], PASSWORD)
    assert not accounts_store.verify_account_password("acct_does_not_exist", PASSWORD)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def test_export_returns_everything_held_and_no_secret_material(
    client, chroma, storage
):
    """Scanned as a serialized string, not by key absence: a nested or renamed
    field carrying a hash would slip past a key-name check."""
    a = _populate(client, chroma, storage, "export@example.com")
    from app.core import accounts_store

    accounts_store.set_subscription(
        a["account_id"], "active", "cus_test_123", "sub_test_456"
    )

    res = client.get("/v1/auth/export", headers=_auth(a["token"]))
    assert res.status_code == 200, res.text
    payload = res.json()

    # Completeness.
    assert payload["profile"]["email"] == "export@example.com"
    assert payload["profile"]["account_id"] == a["account_id"]
    assert payload["billing"]["stripe_customer_id"] == "cus_test_123"
    assert payload["billing"]["stripe_subscription_id"] == "sub_test_456"
    assert payload["billing"]["subscription_status"] == "active"
    assert payload["sessions"] == [a["session_id"]]
    assert [k["key_id"] for k in payload["api_keys"]] == [a["key_id"]]
    assert payload["usage_counters"][0]["counter"] == "generations"
    assert payload["usage_counters"][0]["value"] == 2

    # No secret material. Scanned over the serialized data — a nested or
    # renamed field carrying a hash would slip past a key-name check.
    # ``export_notes`` is prose *about* the exclusions, so it is scanned only
    # for literal secret values, not for the words naming them.
    data_blob = json.dumps({k: v for k, v in payload.items() if k != "export_notes"})
    for needle in ("pbkdf2", "password", "token_hash", "key_hash", "hash"):
        assert needle not in data_blob.lower(), needle
    full_blob = json.dumps(payload)
    assert a["token"] not in full_blob
    assert a["api_key"] not in full_blob

    # And not by luck: the raw hashes really do exist in the database.
    import sqlalchemy as sa

    from app.core import accounts_store as s

    with s._engine().begin() as conn:
        row = conn.execute(
            sa.select(s._t_accounts.c.password_hash).where(
                s._t_accounts.c.id == a["account_id"]
            )
        ).first()
        key_hash = conn.execute(
            sa.select(s._t_api_keys.c.key_hash).where(
                s._t_api_keys.c.account_id == a["account_id"]
            )
        ).scalar_one()
        verify_hash = conn.execute(
            sa.select(s._t_accounts.c.verify_token_hash).where(
                s._t_accounts.c.id == a["account_id"]
            )
        ).scalar_one()
    assert row[0].startswith("pbkdf2$")
    assert row[0] not in full_blob
    assert key_hash not in full_blob
    # The verification hash is the one secret column on the accounts row that
    # the whitelist could plausibly pick up by accident.
    assert verify_hash and verify_hash not in full_blob


def test_export_requires_an_account_credential(client):
    assert client.get("/v1/auth/export").status_code == 403


def test_export_does_not_leak_another_account(client, chroma, storage):
    a = _populate(client, chroma, storage, "mine@example.com")
    b = _populate(client, chroma, storage, "yours@example.com")

    payload = client.get("/v1/auth/export", headers=_auth(a["token"])).json()
    blob = json.dumps(payload)
    assert b["email"] not in blob
    assert b["account_id"] not in blob
    assert b["session_id"] not in blob


# ---------------------------------------------------------------------------
# honest partial purge
# ---------------------------------------------------------------------------


def test_unreachable_vector_store_is_reported_not_silently_passed(
    client, chroma, storage, monkeypatch
):
    """``chroma_store.collection_exists`` returns False on any error, so the
    naive implementation reports a clean purge on a host where Chroma is down.
    The purge must distinguish "nothing there" from "could not look"."""
    from app.core import chroma_store, data_rights

    a = _populate(client, chroma, storage, "partial@example.com")

    def _boom():
        raise RuntimeError("chroma is down")

    monkeypatch.setattr(chroma_store, "_get_chroma_client", _boom)

    res = client.request(
        "DELETE",
        "/v1/auth/account",
        json={"password": PASSWORD},
        headers=_auth(a["token"]),
    )
    # Not 204: something survived, and the response says so.
    assert res.status_code == 200, res.text
    report = res.json()
    assert report["ok"] is False
    assert "vector_index" not in report["purged"]
    failing = {c["category"]: c for c in report["not_purged"]}
    assert failing["vector_index"]["status"] == data_rights.UNAVAILABLE
    # The ownership index is gone with the account, so the ids an operator
    # needs to finish the job by hand must be handed back.
    assert report["residual_session_ids"] == [a["session_id"]]
    # The identity is still erased, and the categories that could be purged were.
    assert report["account_deleted"] is True
    assert _db_rows(a["account_id"])["accounts"] == 0
    assert not (storage / "sessions" / a["session_id"]).exists()
    assert "session_storage" in report["purged"]


def test_hostile_session_id_never_becomes_a_delete_path(client, chroma, storage):
    """``record_session_owner`` stores whatever string it is given, and the
    purge interpolates session ids into ``shutil.rmtree``. A traversal id must
    be refused and reported, not walked."""
    import sqlalchemy as sa

    from app.core import accounts_store as s
    from app.core import data_rights

    a = _populate(client, chroma, storage, "hostile@example.com")

    outsider = storage.parent / "not_ours"
    outsider.mkdir(parents=True, exist_ok=True)
    (outsider / "keep.txt").write_text("someone else's data", encoding="utf-8")

    with s._engine().begin() as conn:
        conn.execute(
            sa.insert(s._t_session_owners).values(
                # Escapes STORAGE_PATH/sessions/ entirely: two levels up lands
                # on the storage root's parent, where other tenants' data sits.
                session_id="../../not_ours",
                account_id=a["account_id"],
                created_at=s._iso(s._utcnow()),
            )
        )

    report = data_rights.purge_account(a["account_id"])

    assert (outsider / "keep.txt").exists()
    assert outsider.exists()
    # Refused, and said so: the skip is not laundered into a clean purge.
    assert report["ok"] is False
    failing = {c["category"] for c in report["not_purged"]}
    assert "unsafe_session_ids" in failing
    # The well-formed session was still purged.
    assert not (storage / "sessions" / a["session_id"]).exists()


def test_purge_report_names_what_it_cannot_reach(client, chroma, storage):
    from app.core import data_rights

    a = _populate(client, chroma, storage, "limits@example.com")
    report = data_rights.purge_account(a["account_id"])
    assert report["ok"] is True
    categories = {c["category"] for c in report["not_reachable_by_design"]}
    assert {"orphan_sessions", "external_processors"} <= categories


def test_purge_of_unknown_account_reports_no_deletion(client):
    from app.core import data_rights

    report = data_rights.purge_account("acct_missing")
    assert report["account_deleted"] is False
    assert report["ok"] is False
    assert report["session_ids"] == []


# ---------------------------------------------------------------------------
# retention
# ---------------------------------------------------------------------------


def test_retention_purges_expired_tokens_and_keeps_live_ones(client, storage):
    """Time-limited rows expire; everything else survives. The expired and the
    live token belong to different accounts so the pass cannot pass by simply
    truncating the table."""
    import sqlalchemy as sa

    from app.core import accounts_store as s
    from app.core import data_rights

    a = _register(client, "old@example.com")
    b = _register(client, "new@example.com")

    # Backdate A's login token and its verification token past their TTLs.
    past = s._iso(s._utcnow() - s.timedelta(days=30))
    with s._engine().begin() as conn:
        conn.execute(
            sa.update(s._t_login_tokens)
            .where(s._t_login_tokens.c.account_id == a["account_id"])
            .values(expires_at=past)
        )
        conn.execute(
            sa.update(s._t_accounts)
            .where(s._t_accounts.c.id == a["account_id"])
            .values(verify_expires_at=past)
        )

    result = data_rights.run_retention_pass()
    assert result["login_tokens_deleted"] == 1
    assert result["verify_tokens_cleared"] == 1

    with s._engine().begin() as conn:
        remaining = conn.execute(
            sa.select(s._t_login_tokens.c.account_id)
        ).scalars().all()
        verify_a = conn.execute(
            sa.select(s._t_accounts.c.verify_token_hash).where(
                s._t_accounts.c.id == a["account_id"]
            )
        ).scalar_one()
        verify_b = conn.execute(
            sa.select(s._t_accounts.c.verify_token_hash).where(
                s._t_accounts.c.id == b["account_id"]
            )
        ).scalar_one()
    assert remaining == [b["account_id"]]
    assert verify_a is None
    assert verify_b is not None

    # Accounts themselves are untouched by a retention pass.
    assert _db_rows(a["account_id"])["accounts"] == 1
    assert client.get("/v1/auth/me", headers=_auth(b["login_token"])).status_code == 200
    # Expired token is not a user principal; 401 opens Sign in.
    assert client.get("/v1/auth/me", headers=_auth(a["login_token"])).status_code == 401


def test_retention_leaves_billing_state_alone(client, storage):
    from app.core import accounts_store, data_rights

    a = _register(client, "billing@example.com")
    accounts_store.set_subscription(a["account_id"], "trialing", "cus_x", "sub_y")
    before = accounts_store.subscription_fields(a["account_id"])
    data_rights.run_retention_pass()
    assert accounts_store.subscription_fields(a["account_id"]) == before


def test_retention_endpoint_is_master_key_only(client, monkeypatch):
    a = _register(client, "user@example.com")
    res = client.post("/v1/auth/admin/retention", headers=_auth(a["login_token"]))
    assert res.status_code == 403

    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-secret")
    res = client.post("/v1/auth/admin/retention", headers=_auth("master-secret"))
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
