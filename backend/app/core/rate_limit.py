"""Rate limiter for public auth endpoints.

Backends, in order of preference:
1. Redis (``REDIS_URL`` set) — buckets shared across instances and restarts.
2. In-memory deque buckets (default) — zero deps, single-process.

Redis failures fall back to the in-memory bucket for non-auth budgets so an
outage never locks paid/LLM routes. Auth buckets (login, register, …) fail
closed: a dead Redis must not lift the brute-force budget.

Two hardening properties beyond the original:

* The in-memory store is **bounded** (``RATE_LIMIT_MAX_KEYS``). It used to be
  an unbounded ``defaultdict``, so an attacker rotating source addresses grew
  it without limit — the limiter itself was the memory-exhaustion vector.
  Eviction prefers *expired* buckets, because evicting live ones is itself a
  bypass: flood distinct keys until your own throttled bucket is dropped.
  Hard-cap eviction of the oldest live bucket only happens when every tracked
  bucket is still live, which the sweep makes rare.

* The client identity may come from a proxy header, but **only** when
  ``TRUSTED_PROXY`` is explicitly set. Trusting ``X-Forwarded-For``
  unconditionally makes the limit key caller-controlled: a fresh header value
  per request is a fresh bucket, and the limit is gone. Default stays the
  socket peer.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Tuple

_LOCK = threading.Lock()
# Plain dict, not defaultdict: every insert goes through _bucket_locked so the
# bound is enforced in one place.
_BUCKETS: Dict[Tuple[str, str], Deque[float]] = {}

_REDIS_UNSET = object()
_redis_client: object = _REDIS_UNSET

_TRUTHY = {"1", "true", "yes", "on"}

MAX_KEYS_ENV = "RATE_LIMIT_MAX_KEYS"
MAX_KEYS_DEFAULT = 10000

TRUSTED_PROXY_ENV = "TRUSTED_PROXY"
TRUSTED_PROXY_HOPS_ENV = "TRUSTED_PROXY_HOP_COUNT"
TRUSTED_PROXY_HOPS_DEFAULT = 1
FORWARDED_FOR_HEADER = "x-forwarded-for"

# Auth endpoints only. LLM/other budgets keep the in-memory fallback.
AUTH_FAIL_CLOSED_BUCKETS = frozenset(
    {
        "register",
        "login",
        "smoke-login",
        "verify-email",
        "forgot-password",
        "reset-password",
        "change-password",
        "delete-account",
        "resend-verification",
    }
)


def _limits() -> tuple[int, int]:
    try:
        max_attempts = int(os.getenv("AUTH_RATE_LIMIT_MAX", "10"))
    except ValueError:
        max_attempts = 10
    try:
        window = int(os.getenv("AUTH_RATE_LIMIT_WINDOW_S", "600"))
    except ValueError:
        window = 600
    return max_attempts, window


def _max_keys() -> int:
    try:
        value = int(os.getenv(MAX_KEYS_ENV, "").strip() or MAX_KEYS_DEFAULT)
    except ValueError:
        return MAX_KEYS_DEFAULT
    return value if value > 0 else MAX_KEYS_DEFAULT


# --- client identity ----------------------------------------------------------


def trusted_proxy_enabled() -> bool:
    """Whether a proxy header may be believed. Opt-in, read per call."""
    return os.getenv(TRUSTED_PROXY_ENV, "").strip().lower() in _TRUTHY


def _trusted_proxy_hops() -> int:
    try:
        value = int(os.getenv(TRUSTED_PROXY_HOPS_ENV, "").strip() or TRUSTED_PROXY_HOPS_DEFAULT)
    except ValueError:
        return TRUSTED_PROXY_HOPS_DEFAULT
    return value if value > 0 else TRUSTED_PROXY_HOPS_DEFAULT


def _peer(request: Any) -> str:
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return host or "unknown"


def client_key(request: Any) -> str:
    """Resolve the rate-limit identity for *request*.

    Default: the socket peer. When ``TRUSTED_PROXY`` is set, the address is
    taken from ``X-Forwarded-For`` counting from the RIGHT by
    ``TRUSTED_PROXY_HOP_COUNT`` (default 1, the nearest proxy's view of its
    own client). The leftmost entry is attacker-supplied even behind a real
    proxy, so it is never used. If the header is missing or has fewer entries
    than declared hops, the socket peer stands.
    """
    peer = _peer(request)
    if not trusted_proxy_enabled():
        return peer
    headers = getattr(request, "headers", None)
    raw = ""
    if headers is not None:
        try:
            raw = headers.get(FORWARDED_FOR_HEADER) or ""
        except Exception:  # noqa: BLE001 - header access must never 500 a login
            raw = ""
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    if not parts:
        return peer
    hops = _trusted_proxy_hops()
    if len(parts) < hops:
        # Fewer entries than the declared proxy chain means the header did not
        # come from where we think it did. Trust the socket instead.
        return peer
    return parts[-hops]


# --- storage ------------------------------------------------------------------


def _redis():
    """Lazy Redis client; None when unconfigured or unreachable."""
    global _redis_client
    if _redis_client is _REDIS_UNSET:
        url = os.getenv("REDIS_URL", "").strip()
        client = None
        if url:
            try:
                import redis  # type: ignore

                client = redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
                client.ping()
            except Exception:
                client = None
        _redis_client = client
    return _redis_client


def _bucket_locked(
    bucket: str, key: str, now: float, window: float
) -> Deque[float]:
    """Fetch or create a bucket, keeping ``_BUCKETS`` under the key bound.

    Caller must hold ``_LOCK``.
    """
    entry = _BUCKETS.get((bucket, key))
    if entry is not None:
        return entry

    max_keys = _max_keys()
    if len(_BUCKETS) >= max_keys:
        # 1. Sweep buckets whose newest attempt has aged out of the window.
        #    These carry no state, so dropping them cannot lift anyone's limit.
        stale = [
            k
            for k, q in _BUCKETS.items()
            if not q or q[-1] <= now - window
        ]
        for k in stale:
            _BUCKETS.pop(k, None)
        # 2. Only if everything tracked is still live do we drop live buckets,
        #    oldest activity first. This is the bypass-shaped path, so it is
        #    the fallback and not the primary strategy.
        while len(_BUCKETS) >= max_keys:
            oldest = min(_BUCKETS.items(), key=lambda kv: kv[1][-1] if kv[1] else 0.0)[0]
            _BUCKETS.pop(oldest, None)

    entry = deque()
    _BUCKETS[(bucket, key)] = entry
    return entry


def check_rate_limit(
    bucket: str,
    key: str,
    *,
    max_attempts: int | None = None,
    window: int | None = None,
) -> bool:
    """Record an attempt; False when the bucket is full (caller returns 429).

    Defaults to the auth limits; callers with their own budget (e.g. the LLM
    burst throttle) pass explicit ``max_attempts``/``window``.
    """
    if max_attempts is None or window is None:
        auth_max, auth_window = _limits()
        max_attempts = auth_max if max_attempts is None else max_attempts
        window = auth_window if window is None else window
    client = _redis()
    if client is not None:
        try:
            slot = int(time.time() // window)
            rkey = f"rl:{bucket}:{key}:{slot}"
            count = client.incr(rkey)
            if count == 1:
                client.expire(rkey, window * 2)
            return count <= max_attempts
        except Exception:
            if bucket in AUTH_FAIL_CLOSED_BUCKETS:
                return False
            # Non-auth budgets: fall back to the in-memory bucket.
    now = time.monotonic()
    with _LOCK:
        q = _bucket_locked(bucket, key, now, window)
        while q and q[0] <= now - window:
            q.popleft()
        if len(q) >= max_attempts:
            return False
        q.append(now)
        return True


def check_rate_limit_for_request(bucket: str, request: Any) -> bool:
    """``check_rate_limit`` keyed by :func:`client_key` — the wiring callers want."""
    return check_rate_limit(bucket, client_key(request))


def tracked_key_count() -> int:
    """Number of in-memory buckets currently held (bound observability)."""
    with _LOCK:
        return len(_BUCKETS)


def reset_rate_limits() -> None:
    """Clear all buckets (test isolation)."""
    with _LOCK:
        _BUCKETS.clear()


def _reset_redis_client_for_tests() -> None:
    """Drop the cached Redis client so a test can re-resolve it."""
    global _redis_client
    _redis_client = _REDIS_UNSET
