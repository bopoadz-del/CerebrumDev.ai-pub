"""Cost and denial-of-service controls for the public launch.

Every test here is written in the shape that would have caught the ORIGINAL
defect, not the shape of the patch:

* ``TestDraftQuota`` calls the endpoint N+1 times and asserts the (N+1)th is a
  429 AND that the expensive function ran exactly N times. Asserting "the
  quota helper was called" would pass against a gate placed after the spend,
  or against a gate whose 429 is swallowed by the handler's blanket
  ``except Exception -> 400``.
* ``TestDraftPromptBounds`` is metamorphic: two briefs three orders of
  magnitude apart must produce the SAME bounded outbound payload size. A test
  that asserts ``max_tokens == 2000`` would pass while the prompt side stayed
  unbounded.
* ``TestRequestBodyCeiling`` POSTs real oversized bytes and asserts the
  handler never observed them. Asserting the status code alone would pass
  against a middleware that reads the whole body and then returns 413.
* ``TestUploadCaps`` measures the largest single chunk the handler ever
  received and asserts nothing is left on disk after a rejection. The original
  defect was ``await file.read()`` materialising a whole file, which a
  status-code assertion cannot see.
* ``TestRateLimitIdentity`` sends a DIFFERENT spoofed forwarded-for header on
  every attempt and asserts the limit still bites. A per-request fresh header
  minting a fresh bucket is exactly the bypass.
* ``TestRateLimitStoreBound`` floods distinct identities and asserts the store
  stays bounded AND that a live throttled bucket survives the eviction of
  stale ones.
"""

from __future__ import annotations

import io
import os
import uuid
from pathlib import Path

os.environ.setdefault("ALLOW_ANONYMOUS_DEV", "1")

import pytest  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.datastructures import UploadFile as StarletteUploadFile  # noqa: E402

from app.core import accounts_store, rate_limit  # noqa: E402
from app.core.request_limits import BodySizeLimitMiddleware  # noqa: E402
from app.factory import product_architect  # noqa: E402
from app.main import app as real_app  # noqa: E402


# --------------------------------------------------------------------------- #
# 1. Metering the unbounded LLM cost path
# --------------------------------------------------------------------------- #


@pytest.fixture()
def trial_client(tmp_path, monkeypatch):
    """A TestClient authenticated as a real, unsubscribed trial account."""
    monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/accounts.db")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    # Billing is a separate gate (402); keep it out of the way so a quota
    # regression cannot hide behind it.
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    # Quotas bind only where billing is configured; these tests are about the
    # quota itself, so this deployment enforces it.
    monkeypatch.setenv("TRIAL_LIMITS_ENFORCED", "1")
    monkeypatch.setenv("ACCOUNTS_REQUIRE_VERIFIED_EMAIL", "0")
    monkeypatch.setenv("ARCHITECT_LLM_DRAFTING_ENABLED", "0")

    account = accounts_store.create_account(
        f"quota-{uuid.uuid4().hex[:8]}@example.com", "hunter2hunter2"
    )
    account_id = account["account_id"]
    api_key = accounts_store.issue_api_key(account_id)["api_key"]

    client = TestClient(real_app)
    client.headers.update({"Authorization": f"Bearer {api_key}"})
    client.account_id = account_id  # type: ignore[attr-defined]
    return client


def _count_drafts(monkeypatch, module):
    """Wrap the expensive drafter in *module*'s namespace with a call counter."""
    calls = []
    original = module.draft_blueprint_from_brief

    def _counted(brief, **kwargs):
        calls.append(brief)
        return original(brief, **kwargs)

    monkeypatch.setattr(module, "draft_blueprint_from_brief", _counted)
    return calls


class TestDraftQuota:
    """Draft was the one paid LLM path with no quota at all."""

    def test_session_draft_stops_spending_at_the_limit(
        self, trial_client, monkeypatch
    ):
        from app.core.session_store import create_session
        from app.routers import session_product

        monkeypatch.setenv("TRIAL_DRAFT_LIMIT", "2")
        calls = _count_drafts(monkeypatch, session_product)

        session_id = f"sess_{uuid.uuid4().hex[:10]}"
        create_session(session_id, trial_client.account_id)

        statuses = [
            trial_client.post(
                f"/v1/sessions/{session_id}/product/draft",
                json={"brief": "Build a vineyard management platform"},
            ).status_code
            for _ in range(3)
        ]

        assert statuses == [200, 200, 429], statuses
        assert len(calls) == 2, f"drafter ran {len(calls)} times, quota did not gate it"

    def test_quota_refusal_is_429_not_400(self, trial_client, monkeypatch):
        """The session handler catches bare Exception and re-raises as 400.

        TrialLimitExceeded IS an HTTPException, so a gate placed inside that
        try block turns the quota into a 400 and the frontend never learns to
        offer an upgrade. Pin the code and the machine-readable body.
        """
        from app.core.session_store import create_session

        monkeypatch.setenv("TRIAL_DRAFT_LIMIT", "0")
        session_id = f"sess_{uuid.uuid4().hex[:10]}"
        create_session(session_id, trial_client.account_id)

        resp = trial_client.post(
            f"/v1/sessions/{session_id}/product/draft",
            json={"brief": "anything"},
        )
        assert resp.status_code == 429, resp.text
        assert resp.json()["detail"]["error"] == "trial_limit_reached"
        assert resp.json()["detail"]["counter"] == "draft"


# --------------------------------------------------------------------------- #
# 2. Bounding the LLM call itself
# --------------------------------------------------------------------------- #


class _CapturingClient:
    """Stand-in for httpx.Client that records the outbound payload."""

    captured: list = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):  # noqa: A002
        _CapturingClient.captured.append(json)

        class _Resp:
            @staticmethod
            def raise_for_status():
                return None

            @staticmethod
            def json():
                return {
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"product_name": "Bounded", '
                                    '"vertical": "bounded", '
                                    '"summary": "s", '
                                    '"capabilities": [{"id": "core", '
                                    '"description": "d", "block_ids": [], '
                                    '"strategy_hint": "GENERATE"}]}'
                                )
                            }
                        }
                    ]
                }

        return _Resp()


@pytest.fixture()
def capturing_llm(monkeypatch):
    _CapturingClient.captured = []
    monkeypatch.setattr(
        product_architect,
        "get_factory_llm_config",
        lambda: {
            "provider": "kimi",
            "base_url": "https://example.invalid/v1",
            "api_key": "test",
            "model": "kimi-test",
            "fallback_model": None,
            "temperature": None,
            "mock": False,
        },
    )
    def _post(url, json=None, headers=None, timeout=None):
        return _CapturingClient().post(url, json=json, headers=headers)

    monkeypatch.setattr("app.factory.llm_watchdog.httpx.post", _post)
    monkeypatch.setattr(product_architect, "dual_registered_ids", lambda: ["audit"])
    return _CapturingClient


class TestDraftPromptBounds:
    """The brief went into the prompt whole, with no max_tokens on the reply."""

    def test_outbound_size_does_not_track_input_size(self, capturing_llm, monkeypatch):
        """Metamorphic: a 10x larger brief must not make a larger request.

        This is the defect itself -- cost scaling with attacker-chosen input.
        """
        monkeypatch.setenv("ARCHITECT_BRIEF_MAX_CHARS", "2000")

        product_architect._draft_with_llm("a" * 20_000)
        product_architect._draft_with_llm("a" * 200_000)

        small, large = capturing_llm.captured
        small_user = small["messages"][-1]["content"]
        large_user = large["messages"][-1]["content"]
        # A 10x input must not grow the prompt at all beyond the few digits of
        # the truncation notice itself.
        assert abs(len(small_user) - len(large_user)) < 50, (
            "prompt size still tracks input size"
        )
        assert len(small_user) < 2_500

    def test_truncation_is_marked_not_silent(self, capturing_llm, monkeypatch):
        monkeypatch.setenv("ARCHITECT_BRIEF_MAX_CHARS", "100")
        tail = "SECRET_TAIL_MARKER"
        product_architect._draft_with_llm("b" * 5_000 + tail)

        sent = capturing_llm.captured[0]["messages"][-1]["content"]
        assert "truncated" in sent, "the brief was cut without saying so"
        assert str(5_000 + len(tail) - 100) in sent, "dropped-character count missing"
        assert tail not in sent

    def test_short_brief_is_untouched(self, capturing_llm, monkeypatch):
        monkeypatch.setenv("ARCHITECT_BRIEF_MAX_CHARS", "8000")
        brief = "Build a small inventory platform for one warehouse."
        product_architect._draft_with_llm(brief)
        assert capturing_llm.captured[0]["messages"][-1]["content"] == brief

    def test_completion_is_capped(self, capturing_llm, monkeypatch):
        monkeypatch.setenv("ARCHITECT_LLM_MAX_TOKENS", "777")
        product_architect._draft_with_llm("Build a platform")
        assert capturing_llm.captured[0]["max_tokens"] == 777

    def test_cap_has_a_default_when_env_is_absent(self, capturing_llm, monkeypatch):
        monkeypatch.delenv("ARCHITECT_LLM_MAX_TOKENS", raising=False)
        monkeypatch.delenv("ARCHITECT_BRIEF_MAX_CHARS", raising=False)
        product_architect._draft_with_llm("c" * 1_000_000)
        payload = capturing_llm.captured[0]
        assert payload["max_tokens"] > 0
        assert len(payload["messages"][-1]["content"]) < 1_000_000


# --------------------------------------------------------------------------- #
# 3. Request body ceiling
# --------------------------------------------------------------------------- #


@pytest.fixture()
def ceiling_app():
    """Throwaway app carrying the middleware, plus a witness of what got through."""
    seen: list = []
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):  # noqa: ANN202
        body = await request.body()
        seen.append(len(body))
        return {"received": len(body)}

    app.add_middleware(BodySizeLimitMiddleware)
    return TestClient(app), seen


class TestRequestBodyCeiling:
    def test_oversized_body_is_413_and_never_reaches_the_handler(
        self, ceiling_app, monkeypatch
    ):
        client, seen = ceiling_app
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "1024")

        resp = client.post("/echo", content=b"x" * 200_000)

        assert resp.status_code == 413, resp.text
        assert seen == [], "handler processed a body that should have been refused"
        assert resp.json()["detail"]["error"] == "request_body_too_large"

    def test_body_within_the_ceiling_still_works(self, ceiling_app, monkeypatch):
        client, seen = ceiling_app
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "1024")
        resp = client.post("/echo", content=b"x" * 512)
        assert resp.status_code == 200
        assert seen == [512]

    def test_streamed_body_without_content_length_is_still_bounded(
        self, ceiling_app, monkeypatch
    ):
        """A lying or absent Content-Length must not get past the ceiling."""
        client, seen = ceiling_app
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "1024")

        def _chunks():
            for _ in range(50):
                yield b"y" * 4096

        # A generator body makes httpx use chunked transfer encoding, so the
        # declared-length check cannot fire and only the streaming count can.
        resp = client.post("/echo", content=_chunks())

        assert resp.status_code == 413, resp.text
        assert seen == []

    def test_multipart_gets_the_upload_ceiling_not_the_json_one(
        self, ceiling_app, monkeypatch
    ):
        """A single global ceiling would silently pre-empt the upload caps."""
        client, seen = ceiling_app
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "100")
        monkeypatch.setenv("MAX_UPLOAD_BODY_BYTES", str(5 * 1024 * 1024))

        resp = client.post(
            "/echo", files={"file": ("a.txt", io.BytesIO(b"z" * 50_000), "text/plain")}
        )

        assert resp.status_code == 200, resp.text
        assert seen and seen[0] > 50_000

    def test_413_is_readable_by_the_browser_when_wired_below_cors(self, monkeypatch):
        """Wiring order is load-bearing, so pin it here.

        Starlette runs the LAST-added middleware outermost. If the body
        ceiling is added after CORSMiddleware it wraps CORS, and the 413 comes
        back without ``access-control-allow-origin`` -- the browser reports a
        CORS failure and the user sees a generic network error instead of
        "file too large". The ceiling must be added BEFORE the CORS block.
        """
        from fastapi.middleware.cors import CORSMiddleware

        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "100")
        app = FastAPI()

        @app.post("/echo")
        async def echo(request: Request):  # noqa: ANN202
            return {"received": len(await request.body())}

        app.add_middleware(BodySizeLimitMiddleware)
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://front.test"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        resp = TestClient(app).post(
            "/echo", content=b"z" * 5000, headers={"Origin": "http://front.test"}
        )
        assert resp.status_code == 413
        assert resp.headers.get("access-control-allow-origin") == "http://front.test"

    def test_upload_ceiling_still_bites(self, ceiling_app, monkeypatch):
        client, seen = ceiling_app
        monkeypatch.setenv("MAX_UPLOAD_BODY_BYTES", "2048")
        resp = client.post(
            "/echo", files={"file": ("a.txt", io.BytesIO(b"z" * 50_000), "text/plain")}
        )
        assert resp.status_code == 413
        assert seen == []


# --------------------------------------------------------------------------- #
# 4. Upload caps
# --------------------------------------------------------------------------- #


@pytest.fixture()
def upload_client(tmp_path, monkeypatch):
    """Real app + a session, with the background processor stubbed out."""
    from app.core.session_store import create_session
    from app.routers import upload as upload_router

    storage = tmp_path / "storage"
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    monkeypatch.setattr(upload_router, "STORAGE_PATH", str(storage))

    started: list = []
    monkeypatch.setattr(
        upload_router, "process_upload", lambda session_id, paths: (session_id, paths)
    )
    monkeypatch.setattr(
        upload_router, "start_task", lambda job_id, coro: started.append(coro)
    )

    session_id = f"sess_{uuid.uuid4().hex[:10]}"
    create_session(session_id, "tester")
    client = TestClient(real_app)
    files_dir = storage / "sessions" / session_id / "files"
    return client, session_id, files_dir, started


def _stored(files_dir: Path) -> list:
    return sorted(p.name for p in files_dir.glob("*")) if files_dir.is_dir() else []


class TestUploadCaps:
    def test_accepts_a_normal_upload(self, upload_client):
        client, session_id, files_dir, started = upload_client
        resp = client.post(
            f"/v1/sessions/{session_id}/upload",
            files=[
                ("files", ("notes.txt", io.BytesIO(b"hello"), "text/plain")),
                ("files", ("more.md", io.BytesIO(b"# hi"), "text/markdown")),
            ],
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["files_received"] == 2
        assert len(_stored(files_dir)) == 2
        assert started, "processing was never scheduled for a valid upload"

    def test_oversized_file_is_refused_and_leaves_nothing_behind(
        self, upload_client, monkeypatch
    ):
        client, session_id, files_dir, started = upload_client
        monkeypatch.setenv("MAX_UPLOAD_FILE_BYTES", "1024")

        resp = client.post(
            f"/v1/sessions/{session_id}/upload",
            files=[("files", ("big.txt", io.BytesIO(b"x" * 200_000), "text/plain"))],
        )

        assert resp.status_code == 413, resp.text
        assert _stored(files_dir) == [], "a refused upload left bytes on disk"
        assert not started

    def test_earlier_files_are_cleaned_up_when_a_later_one_is_refused(
        self, upload_client, monkeypatch
    ):
        """The rejection path must not leak the files it already wrote."""
        client, session_id, files_dir, started = upload_client
        monkeypatch.setenv("MAX_UPLOAD_FILE_BYTES", "1024")

        resp = client.post(
            f"/v1/sessions/{session_id}/upload",
            files=[
                ("files", ("small.txt", io.BytesIO(b"ok"), "text/plain")),
                ("files", ("big.txt", io.BytesIO(b"x" * 200_000), "text/plain")),
            ],
        )

        assert resp.status_code == 413
        assert _stored(files_dir) == [], "partial upload left orphaned files"
        assert not started

    def test_file_is_never_materialised_whole(self, upload_client, monkeypatch):
        """The original defect: ``await file.read()`` with no argument.

        Record the largest single read the handler ever received. Unbounded
        reads return the entire file in one object; a chunked reader cannot.
        """
        client, session_id, files_dir, _ = upload_client
        monkeypatch.setenv("MAX_UPLOAD_FILE_BYTES", str(8 * 1024 * 1024))

        biggest = {"n": 0}
        original_read = StarletteUploadFile.read

        async def _spy_read(self, size=-1):
            data = await original_read(self, size)
            biggest["n"] = max(biggest["n"], len(data))
            return data

        monkeypatch.setattr(StarletteUploadFile, "read", _spy_read)

        payload = b"q" * (4 * 1024 * 1024)
        resp = client.post(
            f"/v1/sessions/{session_id}/upload",
            files=[("files", ("big.txt", io.BytesIO(payload), "text/plain"))],
        )

        assert resp.status_code == 200, resp.text
        assert biggest["n"] < len(payload), (
            f"handler held {biggest['n']} bytes at once for a "
            f"{len(payload)} byte file"
        )

    def test_too_many_files_is_refused(self, upload_client, monkeypatch):
        client, session_id, files_dir, started = upload_client
        monkeypatch.setenv("MAX_UPLOAD_FILES", "2")

        resp = client.post(
            f"/v1/sessions/{session_id}/upload",
            files=[
                ("files", (f"f{i}.txt", io.BytesIO(b"x"), "text/plain"))
                for i in range(5)
            ],
        )

        assert resp.status_code == 400, resp.text
        assert _stored(files_dir) == []
        assert not started

    def test_disallowed_extension_is_refused(self, upload_client):
        client, session_id, files_dir, started = upload_client
        resp = client.post(
            f"/v1/sessions/{session_id}/upload",
            files=[
                ("files", ("payload.exe", io.BytesIO(b"MZ"), "application/octet-stream"))
            ],
        )
        assert resp.status_code == 415, resp.text
        assert _stored(files_dir) == []
        assert not started

    def test_generic_octet_stream_is_not_treated_as_a_mismatch(self, upload_client):
        """``curl -F`` and MIME-less hosts send octet-stream for text files.

        Rejecting it would 415 legitimate uploads while the extension
        allowlist already does the real work.
        """
        client, session_id, files_dir, started = upload_client
        resp = client.post(
            f"/v1/sessions/{session_id}/upload",
            files=[
                ("files", ("notes.md", io.BytesIO(b"# hi"), "application/octet-stream")),
                ("files", ("rows.csv", io.BytesIO(b"a,b"), "application/octet-stream")),
            ],
        )
        assert resp.status_code == 200, resp.text
        assert len(_stored(files_dir)) == 2

    def test_content_type_must_match_the_extension(self, upload_client):
        client, session_id, files_dir, started = upload_client
        resp = client.post(
            f"/v1/sessions/{session_id}/upload",
            files=[("files", ("thing.png", io.BytesIO(b"MZ"), "application/x-msdownload"))],
        )
        assert resp.status_code == 415, resp.text
        assert _stored(files_dir) == []
        assert not started


# --------------------------------------------------------------------------- #
# 5. Rate limiter identity and store bound
# --------------------------------------------------------------------------- #


class _FakeRequest:
    def __init__(self, peer, headers=None):
        self.client = type("C", (), {"host": peer})()
        self.headers = headers or {}


@pytest.fixture(autouse=False)
def limiter(monkeypatch):
    monkeypatch.setattr(rate_limit, "_redis_client", None, raising=False)
    monkeypatch.delenv("TRUSTED_PROXY", raising=False)
    monkeypatch.delenv("TRUSTED_PROXY_HOP_COUNT", raising=False)
    rate_limit.reset_rate_limits()
    yield rate_limit
    rate_limit.reset_rate_limits()


class TestRateLimitIdentity:
    def test_spoofed_header_does_not_mint_a_fresh_bucket_by_default(
        self, limiter, monkeypatch
    ):
        """The bypass shape: a new forwarded-for value on every attempt.

        With no trusted-proxy declaration, the key must stay the socket peer,
        so the limit still bites on the (N+1)th attempt.
        """
        monkeypatch.setenv("AUTH_RATE_LIMIT_MAX", "3")
        monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW_S", "600")

        results = [
            limiter.check_rate_limit_for_request(
                "login",
                _FakeRequest("10.0.0.1", {"x-forwarded-for": f"1.2.3.{i}"}),
            )
            for i in range(4)
        ]
        assert results == [True, True, True, False], results
        assert limiter.tracked_key_count() == 1, "spoofed headers created extra buckets"

    def test_socket_peer_still_separates_real_clients(self, limiter, monkeypatch):
        monkeypatch.setenv("AUTH_RATE_LIMIT_MAX", "2")
        for _ in range(2):
            limiter.check_rate_limit_for_request("login", _FakeRequest("10.0.0.1"))
        assert limiter.check_rate_limit_for_request("login", _FakeRequest("10.0.0.1")) is False
        assert limiter.check_rate_limit_for_request("login", _FakeRequest("10.0.0.2")) is True

    def test_trusted_proxy_reads_from_the_right_not_the_left(
        self, limiter, monkeypatch
    ):
        """Even behind a real proxy the LEFTMOST entry is attacker-written.

        The attacker prepends junk; the proxy appends the address it actually
        saw. Both requests must land in the same bucket.
        """
        monkeypatch.setenv("TRUSTED_PROXY", "1")
        monkeypatch.setenv("TRUSTED_PROXY_HOP_COUNT", "1")

        first = limiter.client_key(
            _FakeRequest("10.0.0.9", {"x-forwarded-for": "evil-a, 203.0.113.7"})
        )
        second = limiter.client_key(
            _FakeRequest("10.0.0.9", {"x-forwarded-for": "evil-b, 203.0.113.7"})
        )
        assert first == second == "203.0.113.7"

    def test_trusted_proxy_off_ignores_the_header_entirely(self, limiter):
        key = limiter.client_key(
            _FakeRequest("10.0.0.9", {"x-forwarded-for": "203.0.113.7"})
        )
        assert key == "10.0.0.9"

    def test_missing_header_falls_back_to_the_socket_peer(self, limiter, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXY", "1")
        assert limiter.client_key(_FakeRequest("10.0.0.9")) == "10.0.0.9"

    def test_short_chain_falls_back_to_the_socket_peer(self, limiter, monkeypatch):
        """Fewer entries than declared hops means the header is not ours."""
        monkeypatch.setenv("TRUSTED_PROXY", "1")
        monkeypatch.setenv("TRUSTED_PROXY_HOP_COUNT", "2")
        key = limiter.client_key(
            _FakeRequest("10.0.0.9", {"x-forwarded-for": "203.0.113.7"})
        )
        assert key == "10.0.0.9"

    def test_live_login_endpoint_is_not_bypassable_with_a_spoofed_header(
        self, limiter, monkeypatch, tmp_path
    ):
        """End-to-end on the real public endpoint the limiter exists to guard."""
        monkeypatch.setenv("ACCOUNTS_DATABASE_URL", f"sqlite:///{tmp_path}/a.db")
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        monkeypatch.setenv("AUTH_RATE_LIMIT_MAX", "3")
        monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW_S", "600")

        client = TestClient(real_app)
        codes = [
            client.post(
                "/v1/auth/login",
                json={"email": "nobody@example.com", "password": "wrongwrongwrong"},
                headers={"X-Forwarded-For": f"9.9.9.{i}"},
            ).status_code
            for i in range(4)
        ]
        assert codes[-1] == 429, codes


class _FakeClock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class TestRateLimitStoreBound:
    def test_store_stays_bounded_under_a_key_flood(self, limiter, monkeypatch):
        """``_BUCKETS`` was an unbounded defaultdict: the limiter was the leak."""
        monkeypatch.setenv("RATE_LIMIT_MAX_KEYS", "50")
        monkeypatch.setenv("AUTH_RATE_LIMIT_MAX", "10")
        monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW_S", "600")

        for i in range(5000):
            limiter.check_rate_limit_for_request("login", _FakeRequest(f"10.1.{i // 256}.{i % 256}"))

        assert limiter.tracked_key_count() <= 50, limiter.tracked_key_count()

    def test_a_throttled_bucket_survives_eviction_of_stale_ones(
        self, limiter, monkeypatch
    ):
        """Eviction must prefer expired buckets, or it is itself a bypass.

        Flooding distinct keys to evict your own throttled bucket would undo
        the limit entirely.
        """
        clock = _FakeClock()
        monkeypatch.setattr(rate_limit, "time", clock)
        monkeypatch.setenv("RATE_LIMIT_MAX_KEYS", "50")
        monkeypatch.setenv("AUTH_RATE_LIMIT_MAX", "3")
        monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW_S", "600")

        # 40 buckets that will age out of the window.
        for i in range(40):
            limiter.check_rate_limit("login", f"stale-{i}")
        clock.advance(1200)

        # The victim fills its bucket and is now blocked.
        for _ in range(3):
            limiter.check_rate_limit("login", "victim")
        assert limiter.check_rate_limit("login", "victim") is False

        # Flood enough new keys to force eviction.
        for i in range(40):
            limiter.check_rate_limit("login", f"flood-{i}")

        assert limiter.check_rate_limit("login", "victim") is False, (
            "a live throttled bucket was evicted by a key flood"
        )
        assert limiter.tracked_key_count() <= 50
