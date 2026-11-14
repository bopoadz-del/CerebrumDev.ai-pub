"""Wall-clock watchdog around factory LLM HTTP posts.

httpx's ``timeout=N`` is idle-based: a streaming or keepalive response
resets the read timer on every byte, so a reasoning model that dribbles
tokens (or an upstream that never closes) can occupy the WRITER for tens
of minutes. Live sess_3f3115ba6d764102 sat in WRITER with
"quiet for 14+ min — model call may still be running" because of this.

#291 joined a daemon thread for ``timeout_s``. That releases the caller
ONLY if ``Thread.join(timeout)`` returns. Live sess_97a1bc6525924e8b
waited 1965s against a 480s attempt wall (production
``FACTORY_CODER_TIMEOUT_S=150`` × 3 legs + 30s grace): join was waited
without cancelling the socket, so a hung httpx/ssl/anyio thread pinned
the WRITER until the upstream finally dropped.

#297 switched the wait to ``Event.wait`` and called ``httpx.Client.close()``
on the caller thread. Live sess_ab446de still waited 1085s vs 480s
(~2.3×). Proven path: ``Event.wait`` *does* return near the per-leg
cap, but ``Client.close()`` → httpcore pool close → ``SSLSocket.close``
takes the SSL lock the worker holds in ``SSL_read``. The raise therefore
runs only after the proxy finally drops the TLS session (often ~18 min).
That is a post-wait cleanup hang, not a join miss and not a reset
contextvar.

This helper:

* waits on ``Event.wait(timeout)`` — never ``join`` after the deadline
* never runs ``Client.close()`` on the caller thread
* shuts the raw socket down (unbound ``socket.shutdown``) on a daemon
  thread so ``SSL_read`` unblocks without taking the SSL lock
* honours an optional monotonic ``deadline`` so stacked legs cannot
  outrun ``attempt_wall_s()``

The 510s live abort (sess MakersHub Leeds, SHA da47307) is the identity
``FACTORY_CODER_TIMEOUT_S=160 × MAX_LEGS(3) + MODEL_CALL_GRACE_S(30)``.
Earlier notes guessed 150 (480s + poll lag); the Floor copy is exactly
510s, so Render is 160. That band is a hang detector leftover, not a
coding budget. Production defaults are 20 min per HTTP post and 40 min
per calling-NOTE; a truly hung/dribbling socket still dies at those
walls. The Store-green build wall (2 hours) is the coding budget.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Any, Callable, Dict, Iterable, Optional

import httpx

from app.factory.build.coder_session_status import (
    OwnerStopped,
    check_owner,
    current_owner_control,
)

logger = logging.getLogger("cerebrumdev.factory.llm_watchdog")

CODER_TIMEOUT_ENV = "FACTORY_CODER_TIMEOUT_S"
ATTEMPT_WALL_ENV = "FACTORY_CODER_ATTEMPT_WALL_S"
#: One HTTP post. Live Render ``FACTORY_CODER_TIMEOUT_S=160`` produced
#: the Floor copy ``timed out after 510s`` (160 × 3 + 30). 120 / 150
#: are the same hang-detect leftover. A real GENERATE body needs many
#: minutes; 20 min is a hang abort, not a "finish the handler" race.
DEFAULT_CALL_TIMEOUT_S = 1200.0
#: One calling-NOTE: one real generation + one alternate-model retry.
#: Not ``timeout × 3`` — stacking three 20 min hangs would eat the
#: 2-hour Store-green wall on a single capability.
DEFAULT_ATTEMPT_WALL_S = 2400.0
#: Live Render leftover 120 / 150 / **160** must not keep killing
#: pilots. 160 is the measured production value (160 × 3 + 30 = 510).
#: Sub-minute values stay honoured for tests.
LEGACY_TIMEOUT_BAND_MIN_S = 60.0
LEGACY_TIMEOUT_BAND_MAX_S = 180.0
#: How often a model call in flight reads the owner's Stop (F-U1a).
OWNER_POLL_S = 0.25
#: One _llm_code_call may try primary + same-endpoint fallback +
#: cross-provider. Plus a small grace so status polling does not race the
#: last attempt. Used for test-scale walls only.
MAX_LEGS = 3
MODEL_CALL_GRACE_S = 30.0
#: Caller must be released within this much of the deadline. Live 1965s
#: vs 480s was ~4×; live 1085s vs 480s was ~2.3× after #297. A hung
#: post that overshoots by this much is still a bug in the watchdog.
WATCHDOG_OVERSHOOT_GRACE_S = 8.0

#: Per-call connect/read split. ``httpx.Client(timeout=N)`` gives ONE
#: idle value to every phase, so a black-holed connect burns the whole N.
#: The split names the two failure modes: a connect that never completes
#: (30s) and a read that never answers (300s). Env-overridable defaults;
#: neither touches the call/attempt walls or the pilot ceilings.
CONNECT_TIMEOUT_ENV = "FACTORY_CODER_CONNECT_TIMEOUT_S"
READ_TIMEOUT_ENV = "FACTORY_CODER_READ_TIMEOUT_S"
DEFAULT_CONNECT_TIMEOUT_S = 30.0
DEFAULT_READ_TIMEOUT_S = 300.0
#: One fresh-connection retry after a per-call connect/read timeout.
#: Capped so a retry storm can never form; 0 disables.
MODEL_CALL_RETRY_ENV = "FACTORY_CODER_MODEL_CALL_RETRIES"
DEFAULT_MODEL_CALL_RETRIES = 1
MODEL_CALL_RETRY_MAX = 3
#: Named event a per-call stall surfaces as, distinct from a wall timeout.
MODEL_CALL_TIMEOUT = "model_call_timeout"


def call_timeout_s() -> float:
    """Per-attempt ceiling for one coder HTTP request.

    Override with FACTORY_CODER_TIMEOUT_S. Sub-minute values (tests) and
    values above the old hang-detect band are honoured. Live leftover
    120s / 150s / 160s is treated as unset so a dashboard pin cannot
    keep aborting WRITER at 510s (160 × 3 + 30).
    """
    raw = os.getenv(CODER_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_CALL_TIMEOUT_S
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_CALL_TIMEOUT_S
    if LEGACY_TIMEOUT_BAND_MIN_S <= value <= LEGACY_TIMEOUT_BAND_MAX_S:
        return DEFAULT_CALL_TIMEOUT_S
    return max(0.05, value)


def attempt_wall_s() -> float:
    """Ceiling for one coder call including one alternate-model retry.

    Test-scale per-attempt timeouts keep the historical ``× 3 legs +
    grace`` formula so stacked-leg tests stay tight. Production uses a
    40-minute calling-NOTE wall — long enough to write a handler, short
    enough that a hung socket cannot occupy the 2-hour pilot budget.
    """
    raw = os.getenv(ATTEMPT_WALL_ENV, "").strip()
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            pass
    per = call_timeout_s()
    stacked = per * MAX_LEGS + MODEL_CALL_GRACE_S
    if per < LEGACY_TIMEOUT_BAND_MIN_S:
        return stacked
    return max(DEFAULT_ATTEMPT_WALL_S, per + MODEL_CALL_GRACE_S)


def connect_timeout_s() -> float:
    """Connect timeout for one per-call HTTP attempt.

    A short floor keeps a black-holed SYN from burning the per-post wall.
    Override with FACTORY_CODER_CONNECT_TIMEOUT_S.
    """
    raw = os.getenv(CONNECT_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            pass
    return DEFAULT_CONNECT_TIMEOUT_S


def read_timeout_s() -> float:
    """Read timeout for one per-call HTTP attempt.

    Longer than connect (a real generation streams), but still far below
    the wall so a silent upstream is a named event, not a wall burn.
    Override with FACTORY_CODER_READ_TIMEOUT_S.
    """
    raw = os.getenv(READ_TIMEOUT_ENV, "").strip()
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            pass
    return DEFAULT_READ_TIMEOUT_S


def model_call_retries() -> int:
    """Fresh-connection retries after a per-call connect/read timeout."""
    raw = os.getenv(MODEL_CALL_RETRY_ENV, "").strip()
    if raw:
        try:
            return max(0, min(int(raw), MODEL_CALL_RETRY_MAX))
        except ValueError:
            pass
    return DEFAULT_MODEL_CALL_RETRIES


def per_call_timeout() -> httpx.Timeout:
    """httpx.Timeout that splits connect from read, so a stalled socket
    fails fast instead of sharing the whole per-post wall across phases."""
    return httpx.Timeout(
        connect=connect_timeout_s(),
        read=read_timeout_s(),
        write=read_timeout_s(),
        pool=connect_timeout_s(),
    )


def is_timeout_error(exc: BaseException) -> bool:
    """A timeout by TYPE, anywhere in the cause chain -- never by the words
    of its message."""
    from app.factory.build.failure_kinds import TIMEOUT, failure_kind

    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return True
    return failure_kind(exc) == TIMEOUT


def _is_real_httpx_post(fn: Callable[..., Any]) -> bool:
    """True only for the library function, not a test monkeypatch."""
    return (
        getattr(fn, "__module__", "") == "httpx"
        and getattr(fn, "__name__", "") == "post"
    )


def remaining_timeout_s(
    timeout_s: float, deadline_mono: Optional[float]
) -> float:
    """Cap one post so stacked legs cannot outrun a shared attempt wall."""
    cap = max(0.05, float(timeout_s))
    if deadline_mono is None:
        return cap
    left = deadline_mono - time.monotonic()
    if left < 0.05:
        raise httpx.ReadTimeout(
            f"coder LLM watchdog: attempt deadline already passed "
            f"({left:.2f}s left)"
        )
    return min(cap, left)


def _timeout_error(url: str, timeout_s: float) -> httpx.ReadTimeout:
    return httpx.ReadTimeout(
        f"coder LLM watchdog fired after {timeout_s:.1f}s "
        f"(upstream still open at {url})"
    )


class ModelCallTimeout(httpx.ReadTimeout):
    """A per-call connect/read timeout -- a named stall, not a silent wall.

    Subclasses ``httpx.ReadTimeout`` so ``is_timeout_error`` still recognises
    it by type; ``named`` carries the ledger token ``model_call_timeout`` so
    the event reads as a stall, never a bare wall kill.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.named = MODEL_CALL_TIMEOUT


def _iter_network_streams(client: Any) -> Iterable[Any]:
    """Yield httpcore streams for an in-flight httpx Client.

    Layouts differ across httpx/httpcore minors; missing attributes are
    skipped. This is cancel-only — a miss just leaves the daemon worker.
    """
    transport = getattr(client, "_transport", None)
    pool = getattr(transport, "_pool", None)
    connections = list(getattr(pool, "_connections", None) or [])
    seen: set[int] = set()
    for conn in connections:
        for stream in (
            getattr(conn, "_network_stream", None),
            getattr(getattr(conn, "_connection", None), "_network_stream", None),
        ):
            if stream is None:
                continue
            marker = id(stream)
            if marker in seen:
                continue
            seen.add(marker)
            yield stream


def _shutdown_client_sockets(client: Any) -> None:
    """Unblock SSL/read without taking the SSL lock ``close()`` waits on.

    ``SSLSocket.shutdown`` / ``SSLSocket.close`` serialize on the same lock
    as ``SSL_read``. ``socket.socket.shutdown`` (unbound) hits the fd and
    wakes the reader so the worker can exit.
    """
    for stream in _iter_network_streams(client):
        get_info = getattr(stream, "get_extra_info", None)
        sock = get_info("socket") if callable(get_info) else None
        if sock is None:
            continue
        try:
            socket.socket.shutdown(sock, socket.SHUT_RDWR)
        except OSError:
            pass


def _close_client(client: Any) -> None:
    if client is None:
        return
    _shutdown_client_sockets(client)
    try:
        client.close()
    except Exception:  # noqa: BLE001 — best-effort; caller already released
        pass


def post_with_deadline(
    url: str,
    *,
    json: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float | None = None,
    post: Optional[Callable[..., Any]] = None,
    deadline_mono: Optional[float] = None,
) -> httpx.Response:
    """POST with an idle httpx timeout and a hard wall-clock deadline.

    ``post`` defaults to ``httpx.post``. Callers that re-export ``httpx``
    (the coder) pass their own so existing test monkeypatches keep working.

    The caller is released when ``Event.wait`` hits ``timeout``. Cancel
    (socket shutdown + ``Client.close``) runs on a daemon thread so a
    close that blocks in SSL/read cannot double the wall — live 1085s
    vs 480s.
    """
    timeout_s = remaining_timeout_s(
        float(timeout) if timeout is not None else call_timeout_s(),
        deadline_mono,
    )
    # F-U1a: no model call starts once the owner has pressed Stop (a no-op
    # on a thread with no owner control installed).
    control = current_owner_control()
    check_owner(control)
    poster = post or httpx.post
    box: Dict[str, Any] = {}
    client_holder: Dict[str, Any] = {}
    done = threading.Event()

    def _cancel() -> None:
        _close_client(client_holder.get("client"))

    def _cancel_off_caller() -> None:
        # Live 1085s>480s: close() on THIS thread waits for SSL_read.
        threading.Thread(
            target=_cancel, name="factory-llm-watchdog-cancel", daemon=True
        ).start()

    def _run() -> None:
        try:
            if _is_real_httpx_post(poster):
                retries = model_call_retries()
                for attempt in range(retries + 1):
                    # A fresh client per attempt = a fresh connection, so
                    # the retry cannot inherit a half-open socket from the
                    # timed-out one.
                    client = httpx.Client(timeout=per_call_timeout())
                    client_holder["client"] = client
                    try:
                        box["resp"] = client.post(
                            url, json=json, headers=headers
                        )
                        break
                    except httpx.TimeoutException as exc:
                        # Per-call connect/read timeout: retry once on a new
                        # connection, then name the stall -- never let it
                        # silently occupy the whole post wall.
                        if attempt < retries:
                            continue
                        raise ModelCallTimeout(
                            f"{MODEL_CALL_TIMEOUT}: no answer within per-call "
                            f"timeouts (connect {connect_timeout_s():g}s / "
                            f"read {read_timeout_s():g}s) after "
                            f"{attempt + 1} attempt(s); last "
                            f"{type(exc).__name__}: {exc}"
                        ) from exc
                    finally:
                        try:
                            client.close()
                        except Exception:  # noqa: BLE001
                            pass
            else:
                box["resp"] = poster(
                    url, json=json, headers=headers, timeout=timeout_s
                )
        except BaseException as exc:  # noqa: BLE001 — re-raised on the caller
            box["exc"] = exc
        finally:
            done.set()

    worker = threading.Thread(
        target=_run, name="factory-llm-watchdog", daemon=True
    )
    worker.start()
    # Never join after the deadline. join(timeout) is how live 1965s>480s
    # happened: the worker held the caller until the socket died.
    # Never close() after the deadline on this thread either — that is
    # how live 1085s>480s happened.
    # With an owner control installed the wait runs in short slices, so a
    # Stop pressed mid-call cancels the call (off the caller, like the
    # deadline) instead of waiting for its answer.
    wait_until = time.monotonic() + timeout_s
    finished = done.wait(timeout_s if control is None else min(timeout_s, OWNER_POLL_S))
    while not finished and control is not None and time.monotonic() < wait_until:
        try:
            check_owner(control)
        except OwnerStopped:
            _cancel_off_caller()
            raise
        finished = done.wait(min(OWNER_POLL_S, max(0.0, wait_until - time.monotonic())))
    if not finished:
        logger.warning(
            "coder LLM watchdog fired after %.1fs; aborting off-caller "
            "(Client.close must not run on this thread) url=%s",
            timeout_s,
            url,
        )
        _cancel_off_caller()
        raise _timeout_error(url, timeout_s)
    if "exc" in box:
        raise box["exc"]
    resp = box.get("resp")
    if resp is None:
        raise httpx.ReadTimeout(
            f"coder LLM watchdog got no response after {timeout_s:.1f}s"
        )
    if deadline_mono is not None and time.monotonic() >= deadline_mono:
        raise _timeout_error(url, timeout_s)
    return resp
