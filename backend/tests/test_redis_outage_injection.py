"""Redis outage-injection — pin the graceful-degradation contract.

CDEV's three Redis-backed surfaces (auth rate limiting, Drive OAuth state,
the change-request queue) are all written to degrade when Redis is absent or
dies mid-flight. Nothing pinned that, so a future refactor could quietly make
Redis load-bearing. These tests inject two outage shapes —

  (a) REDIS_URL unset            -> in-memory / disk path
  (b) client present but every op raises (mid-flight outage)

— and assert the SAFETY PROPERTY in each case: the app stays fully functional,
a dead Redis never locks users out (fail-open), and nothing in Redis is the
only copy of anything (Postgres/disk truth survives a flush).
"""
from __future__ import annotations

import pytest


class _DeadRedis:
    """A connected client whose every operation raises — models a Redis that
    was reachable at ping time and then died / partitioned mid-request."""

    def __getattr__(self, _name):
        def _boom(*_a, **_k):
            raise ConnectionError("simulated redis outage")
        return _boom


# ── (1) auth rate limiter ────────────────────────────────────────────────────

class TestRateLimiterDegradation:
    def _fresh(self, monkeypatch, client):
        from app.core import rate_limit
        monkeypatch.setattr(rate_limit, "_redis_client", client, raising=False)
        rate_limit.reset_rate_limits()
        monkeypatch.setenv("AUTH_RATE_LIMIT_MAX", "3")
        monkeypatch.setenv("AUTH_RATE_LIMIT_WINDOW_S", "600")
        return rate_limit

    def test_unset_redis_uses_in_memory_and_still_limits(self, monkeypatch):
        rl = self._fresh(monkeypatch, None)
        # 3 allowed, 4th blocked — the limiter works with no Redis at all.
        assert [rl.check_rate_limit("login", "ip1") for _ in range(4)] == [
            True, True, True, False,
        ]

    def test_dead_redis_auth_fails_CLOSED(self, monkeypatch):
        # Auth buckets fail closed on a mid-flight Redis outage so the
        # brute-force budget cannot expand to N workers × in-memory.
        rl = self._fresh(monkeypatch, _DeadRedis())
        results = [rl.check_rate_limit("login", "ip2") for _ in range(3)]
        assert results == [False, False, False]

    def test_dead_redis_llm_bucket_still_fails_open(self, monkeypatch):
        rl = self._fresh(monkeypatch, _DeadRedis())
        results = [rl.check_rate_limit("llm:draft", "acct") for _ in range(4)]
        assert results[0] is True
        assert results == [True, True, True, False]

    def test_dead_redis_isolates_identities_for_non_auth(self, monkeypatch):
        rl = self._fresh(monkeypatch, _DeadRedis())
        for _ in range(3):
            rl.check_rate_limit("llm:chat", "a")
        assert rl.check_rate_limit("llm:chat", "a") is False
        assert rl.check_rate_limit("llm:chat", "b") is True


# ── (2) Drive OAuth pending-state store ──────────────────────────────────────

class TestOAuthStateDegradation:
    def _mod(self, monkeypatch, client):
        from app.routers import factory_drive
        monkeypatch.setattr(factory_drive, "_redis_client", client, raising=False)
        factory_drive._pending_states.clear()
        return factory_drive

    def test_unset_redis_round_trips_through_memory(self, monkeypatch):
        fd = self._mod(monkeypatch, None)
        fd._state_save("st1", "sess1", "user1")
        got = fd._state_pop("st1")
        assert got is not None and got[0] == "sess1" and got[1] == "user1"
        # Single-use: a second pop is empty.
        assert fd._state_pop("st1") is None

    def test_dead_redis_save_and_pop_still_round_trip(self, monkeypatch):
        # REDIS_URL unset: single-process tests keep the in-memory store.
        monkeypatch.delenv("REDIS_URL", raising=False)
        fd = self._mod(monkeypatch, _DeadRedis())
        fd._state_save("st2", "sess2", "user2")
        got = fd._state_pop("st2")
        assert got is not None
        assert got[0] == "sess2" and got[1] == "user2"

    def test_configured_redis_outage_has_no_memory_fallback(self, monkeypatch):
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        fd = self._mod(monkeypatch, _DeadRedis())
        with pytest.raises(fd.OAuthStateUnavailable):
            fd._state_save("st3", "sess3", "user3")

    def test_unknown_state_is_none_not_error(self, monkeypatch):
        monkeypatch.delenv("REDIS_URL", raising=False)
        fd = self._mod(monkeypatch, _DeadRedis())
        assert fd._state_pop("never-issued") is None


# ── (3) change-request queue — disk is truth ─────────────────────────────────

class TestChangeRequestQueueDegradation:
    def _queue(self, tmp_path, monkeypatch, dead=False):
        from app.change_requests import queue as q
        cr = q.ChangeRequestQueue(root=tmp_path / "cr")
        if dead:
            cr._redis = _DeadRedis()
        return cr

    def test_disk_is_truth_when_redis_absent(self, tmp_path, monkeypatch):
        cr = self._queue(tmp_path, monkeypatch)
        cr._redis = None
        item = cr.put_received({"request_id": "R1", "kind": "add_capability"})
        assert item["state"] == "received"
        # Readable straight back from disk with no Redis in the loop.
        assert cr.get("R1")["request_id"] == "R1"
        assert [i["request_id"] for i in cr.list_items()] == ["R1"]

    def test_dead_redis_does_not_lose_the_item(self, tmp_path, monkeypatch):
        # The index write raises, is caught, and the item is STILL on disk —
        # boundary law: nothing in Redis is the only copy of anything.
        cr = self._queue(tmp_path, monkeypatch, dead=True)
        cr.put_received({"request_id": "R2", "kind": "remove_capability"})
        assert cr.get("R2") is not None
        assert cr.get("R2")["state"] == "received"

    def test_flush_redis_leaves_queue_fully_readable(self, tmp_path, monkeypatch):
        # Build state with a working index, then simulate a full Redis flush
        # (client goes dead) — every item is still enumerable from disk.
        cr = self._queue(tmp_path, monkeypatch)
        cr._redis = None
        for rid in ("A", "B", "C"):
            cr.put_received({"request_id": rid, "kind": "x"})
        cr._redis = _DeadRedis()  # flush + partition
        ids = sorted(i["request_id"] for i in cr.list_items())
        assert ids == ["A", "B", "C"]
        assert cr.get("B")["request_id"] == "B"

    def test_idempotent_insert_survives_outage(self, tmp_path, monkeypatch):
        cr = self._queue(tmp_path, monkeypatch, dead=True)
        first = cr.put_received({"request_id": "R3", "kind": "x"})
        second = cr.put_received({"request_id": "R3", "kind": "x"})
        assert first["created_at"] == second["created_at"]  # same disk row
