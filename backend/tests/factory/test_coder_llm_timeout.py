"""Coder LLM calls are bounded: timeout, one alternate retry, then fail closed.

Live sess_3f3115ba6d764102 sat in WRITER with "quiet for 14+ min — model
call may still be running" because httpx's idle read timeout never fired
on a streaming/keepalive completion. #291 added a daemon-thread join;
live sess_97a1bc6525924e8b still waited 1965s against a 480s attempt
wall because join was waited without cancelling the socket. #297 switched
to Event.wait + Client.close(); live sess_ab446de still waited 1085s
vs 480s because close() blocks on the SSL lock the worker holds in
SSL_read. These tests pin the hard wall-clock abort (including a hung
close), the alternate-model retry, the terminal message, and the Floor
status that must not claim progress while the call is in flight.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import httpx
import pytest

from app.factory import coder
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import RoleContext, RoleError
from app.factory.build_jobs import build_status
from app.factory.llm_watchdog import (
    WATCHDOG_OVERSHOOT_GRACE_S,
    post_with_deadline,
)


@contextmanager
def _hold_build_thread(output_dir):
    """Keep a live runner registered for THIS workspace (keyed by path, never
    by product id) so status may claim a live model call."""
    from app.factory.build_jobs import register_runner_thread

    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, daemon=True)
    thread.start()
    register_runner_thread(output_dir, thread)
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def _kimi_cfg(**overrides):
    cfg = {
        "provider": "kimi",
        "model": "primary",
        "fallback_model": "fallback",
        "base_url": "https://api.moonshot.ai/v1",
        "api_key": "test-key-not-real",
    }
    cfg.update(overrides)
    return cfg


def _arm_kimi(monkeypatch, **overrides):
    monkeypatch.setattr(
        "app.factory.product_architect.get_factory_llm_config",
        lambda: _kimi_cfg(**overrides),
    )
    monkeypatch.setattr(
        "app.core.llm_config.get_factory_fallback_leg", lambda: None
    )
    monkeypatch.setattr("time.sleep", lambda s: None)


def test_watchdog_fires_when_httpx_never_returns(monkeypatch):
    """(a) A hung post is cut off by the wall-clock join, not by idle-read."""
    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "0.15")
    _arm_kimi(monkeypatch, fallback_model=None)

    def _hang(url, json=None, headers=None, timeout=None):
        # time.sleep is no-op'd by _arm_kimi (connect-retry backoff).
        threading.Event().wait(8)
        raise AssertionError("caller was not released")

    monkeypatch.setattr(coder.httpx, "post", _hang)

    started = time.monotonic()
    with pytest.raises(coder.CoderTimeout) as exc:
        coder._llm_code_call([{"role": "user", "content": "u"}])
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, elapsed
    assert "coder LLM timed out" in str(exc.value)


def test_hung_post_aborts_near_deadline_when_join_ignores_timeout(monkeypatch):
    """1965s > 480s class: join-without-cancel must not pin the caller.

    #291's watchdog did ``worker.join(timeout)`` and raised only after
    join returned. If join waits for the worker anyway (GIL-holding C
    in httpx/ssl, anyio portal, or a join that ignores its timeout),
    the WRITER burns until the upstream drops — live, 1965s vs 480s.
    Simulate that join by ignoring the timeout; Event.wait + cancel
    must still release the caller near the deadline, not ~4× late.
    """
    orig_join = threading.Thread.join

    def _join_without_cancel(self, timeout=None):  # noqa: ARG001
        return orig_join(self, None)

    monkeypatch.setattr(threading.Thread, "join", _join_without_cancel)

    hang_s = 2.0
    timeout_s = 0.25

    def _hang(url, json=None, headers=None, timeout=None):  # noqa: ARG001
        threading.Event().wait(hang_s)
        raise AssertionError("caller was not released")

    started = time.monotonic()
    with pytest.raises(httpx.ReadTimeout) as exc:
        post_with_deadline(
            "https://example.invalid/v1/chat/completions",
            json={"model": "primary"},
            timeout=timeout_s,
            post=_hang,
        )
    elapsed = time.monotonic() - started

    # Not 4× the deadline (1.0s) and not the full hang (2.0s).
    assert elapsed < timeout_s + 0.8, elapsed
    assert elapsed < hang_s / 2, elapsed
    assert "watchdog fired" in str(exc.value)


def _dribble_server(
    hold_s: float, interval_s: float = 0.05, accept_n: int = 1
) -> int:
    """HTTP/1.1 chunked dribble that resets an idle read timeout."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(max(1, accept_n))
    port = sock.getsockname()[1]

    def _serve_one(conn: socket.socket) -> None:
        try:
            conn.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Transfer-Encoding: chunked\r\n"
                b"\r\n"
            )
            end = time.monotonic() + hold_s
            while time.monotonic() < end:
                try:
                    conn.sendall(b"1\r\n \r\n")
                except OSError:
                    break
                time.sleep(interval_s)
            try:
                conn.sendall(b"0\r\n\r\n")
            except OSError:
                pass
        except OSError:
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _serve() -> None:
        try:
            sock.settimeout(hold_s + 2.0)
            for _ in range(max(1, accept_n)):
                try:
                    conn, _ = sock.accept()
                except OSError:
                    break
                threading.Thread(
                    target=_serve_one,
                    args=(conn,),
                    name="dribble-conn",
                    daemon=True,
                ).start()
        finally:
            sock.close()

    threading.Thread(target=_serve, name="dribble-http", daemon=True).start()
    return port


def test_watchdog_aborts_streaming_dribble_near_deadline():
    """Idle httpx timeout resets on each chunk; the wall clock must not."""
    port = _dribble_server(hold_s=3.0)
    timeout_s = 0.3
    started = time.monotonic()
    with pytest.raises(httpx.ReadTimeout) as exc:
        post_with_deadline(
            f"http://127.0.0.1:{port}/",
            json={"model": "primary"},
            timeout=timeout_s,
        )
    elapsed = time.monotonic() - started
    assert elapsed < timeout_s + 0.8, elapsed
    assert "watchdog" in str(exc.value).lower()


def test_hung_client_close_does_not_double_the_deadline(monkeypatch):
    """1085s > 480s class: Client.close() blocked in SSL/read.

    #297's Event.wait returns on time, then the caller invokes
    ``Client.close()``. close() → httpcore pool → SSLSocket.close takes
    the SSL lock the worker holds in SSL_read, so the raise runs only
    after the proxy drops the TLS session. Live: 1085s vs 480s (~2.3×),
    matching an ~18 min idle drop after a 150s wait.

    Patch close to hang ~2× the deadline the way that cleanup did.
    The WRITER caller must return near the deadline, never ≥2×.
    """
    orig_close = httpx.Client.close
    close_hang_s = 2.2
    closed = threading.Event()

    def _close_blocked_in_ssl_read(self):  # noqa: ANN001
        # SSLSocket.close waits on the reader lock; do not use time.sleep
        # (other tests no-op it for connect-retry backoff).
        threading.Event().wait(close_hang_s)
        closed.set()
        return orig_close(self)

    monkeypatch.setattr(httpx.Client, "close", _close_blocked_in_ssl_read)

    port = _dribble_server(hold_s=4.0)
    timeout_s = 0.5
    started = time.monotonic()
    with pytest.raises(httpx.ReadTimeout) as exc:
        post_with_deadline(
            f"http://127.0.0.1:{port}/",
            json={"model": "primary"},
            timeout=timeout_s,
        )
    elapsed = time.monotonic() - started

    # Success criteria: ≤ deadline+15s; never ≥2× deadline.
    # Without the fix elapsed ≈ timeout + close_hang (~2.7s ≥ 2× 0.5).
    assert elapsed <= timeout_s + 15.0, elapsed
    assert elapsed < 2 * timeout_s, elapsed
    assert elapsed < close_hang_s, elapsed
    assert elapsed <= timeout_s + WATCHDOG_OVERSHOOT_GRACE_S, elapsed
    assert "watchdog fired" in str(exc.value)
    # Cancel may still be blocked in the daemon — that is the point.
    assert not closed.is_set() or elapsed < 2 * timeout_s


def test_llm_code_call_hung_close_retries_then_coder_timeout(monkeypatch):
    """Hung close must not eat the shared attempt wall before fallback."""
    orig_close = httpx.Client.close

    def _close_blocked_in_ssl_read(self):  # noqa: ANN001
        threading.Event().wait(2.0)
        return orig_close(self)

    monkeypatch.setattr(httpx.Client, "close", _close_blocked_in_ssl_read)
    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "0.25")
    monkeypatch.setattr("app.factory.llm_watchdog.MODEL_CALL_GRACE_S", 0.2)
    monkeypatch.setattr("app.factory.coder.MODEL_CALL_GRACE_S", 0.2, raising=False)

    port = _dribble_server(hold_s=6.0, accept_n=3)
    _arm_kimi(monkeypatch, base_url=f"http://127.0.0.1:{port}")
    # Real httpx.post — Client.close is the live abort path.

    started = time.monotonic()
    with pytest.raises(coder.CoderTimeout) as exc:
        coder._llm_code_call([{"role": "user", "content": "u"}])
    elapsed = time.monotonic() - started

    # attempt_wall = 0.25 * 3 + 0.2 = 0.95s. Two legs + hung close on
    # the caller would be ~4.5s. Must stay under 2× the wall.
    wall = 0.25 * 3 + 0.2
    assert elapsed <= wall + 15.0, elapsed
    assert elapsed < 2 * wall, elapsed
    assert "coder LLM timed out" in str(exc.value)
    assert "primary" in str(exc.value)
    assert "fallback" in str(exc.value)


def test_stacked_legs_cannot_outrun_attempt_wall(monkeypatch):
    """One calling-NOTE's _llm_code_call must stay inside attempt_wall_s()."""
    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "0.2")
    monkeypatch.setattr("app.factory.llm_watchdog.MODEL_CALL_GRACE_S", 0.15)
    monkeypatch.setattr("app.factory.coder.MODEL_CALL_GRACE_S", 0.15, raising=False)
    _arm_kimi(monkeypatch)
    monkeypatch.setattr(
        "app.core.llm_config.get_factory_fallback_leg",
        lambda: {
            "provider": "openrouter",
            "model": "free",
            "base_url": "https://example.invalid/v1",
            "api_key": "test-key-not-real",
            "is_free": True,
        },
    )

    def _hang(url, json=None, headers=None, timeout=None):  # noqa: ARG001
        # Must not use time.sleep: _arm_kimi no-ops it for connect-retry
        # backoff, which made this test a false pass on the old join path.
        threading.Event().wait(2.0)
        raise httpx.ReadTimeout("model still open")

    monkeypatch.setattr(coder.httpx, "post", _hang)

    started = time.monotonic()
    with pytest.raises(coder.CoderTimeout):
        coder._llm_code_call([{"role": "user", "content": "u"}])
    elapsed = time.monotonic() - started
    # attempt_wall = 0.2 * 3 + 0.15 = 0.75s. 3 × 2s hang would be 6s;
    # 3 × 0.2s posts without a shared wall is still fine — the live
    # failure was posts that ignored their own timeout. Bound well
    # under 4× the wall (3.0s).
    assert elapsed < 1.6, elapsed


def test_validate_retry_does_not_swallow_coder_timeout(monkeypatch):
    calls = []

    def _boom(messages):  # noqa: ARG001
        calls.append(1)
        raise coder.CoderTimeout("coder LLM timed out on primary after retry")

    monkeypatch.setattr(coder, "_llm_code_call", _boom)
    with pytest.raises(coder.CoderTimeout):
        coder._call_validate_retry(
            [{"role": "user", "content": "u"}],
            "class_and_event_scheduling",
        )
    assert calls == [1]


def test_timeout_retries_the_alternate_configured_model(monkeypatch):
    """(b) First-leg timeout tries the fallback model; it may still succeed."""
    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "30")
    _arm_kimi(monkeypatch)
    models = []

    def _post(url, json=None, headers=None, timeout=None):
        model = (json or {}).get("model")
        models.append(model)
        if model == "primary":
            raise httpx.ReadTimeout("model did not answer")
        class _R:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "choices": [
                        {
                            "message": {"content": "return {}"},
                            "finish_reason": "stop",
                        }
                    ]
                }

        return _R()

    monkeypatch.setattr(coder.httpx, "post", _post)

    text, model_used = coder._llm_code_call([{"role": "user", "content": "u"}])
    assert text == "return {}"
    assert model_used == "fallback"
    assert models == ["primary", "fallback"]


def test_timeout_exhaustion_raises_coder_timeout_not_a_generic_failure(
    monkeypatch,
):
    """(c) After every configured leg times out, the error names the timeout."""
    _arm_kimi(monkeypatch)
    models = []

    def _timeout(url, json=None, headers=None, timeout=None):
        models.append((json or {}).get("model"))
        raise httpx.ReadTimeout("model did not answer")

    monkeypatch.setattr(coder.httpx, "post", _timeout)

    with pytest.raises(coder.CoderTimeout) as exc:
        coder._llm_code_call([{"role": "user", "content": "u"}])

    message = str(exc.value)
    assert message.startswith("coder LLM timed out")
    assert "primary" in message
    assert "fallback" in message
    # One attempt per model — do not loop the same slug.
    assert models == ["primary", "fallback"]


def test_timeout_without_alternate_retries_the_same_model_once(monkeypatch):
    _arm_kimi(monkeypatch, fallback_model=None)
    calls = []

    def _timeout(url, json=None, headers=None, timeout=None):
        calls.append(1)
        raise httpx.ReadTimeout("model did not answer")

    monkeypatch.setattr(coder.httpx, "post", _timeout)

    with pytest.raises(coder.CoderTimeout) as exc:
        coder._llm_code_call([{"role": "user", "content": "u"}])

    assert "coder LLM timed out" in str(exc.value)
    assert "after retry" in str(exc.value)
    assert len(calls) == 2


def test_writer_timeout_is_a_role_error_not_a_silent_template(monkeypatch):
    """A hung handler must stop WRITER — not ship a thin scaffold SUCCESS."""
    from app.factory.build import roles_handlers

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")

    def _boom(**kwargs):
        raise coder.CoderTimeout("coder LLM timed out on primary after retry")

    monkeypatch.setattr(
        "app.factory.coder.generate_platform_handler", _boom
    )
    monkeypatch.setattr("app.factory.coder.coder_enabled", lambda: True)
    monkeypatch.setattr(
        "app.factory.build.roles_handlers._budget_too_low", lambda ctx, what: False
    )

    notes = []

    def _progress(detail, payload):
        notes.append((detail, payload))

    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=None,
        blueprint=type("B", (), {"product_name": "Vet", "vertical": "vet"})(),
        plan=None,
        progress=_progress,
    )
    cap = type(
        "C",
        (),
        {"capability_id": "appointment_scheduling", "notes": "book visits"},
    )()

    with pytest.raises(RoleError) as exc:
        roles_handlers._coder_body(ctx, cap, [], {"fields": []})

    assert "coder LLM timed out writing handler appointment_scheduling" in str(
        exc.value
    )
    assert notes, "must emit a calling-NOTE before the model call"
    detail, payload = notes[0]
    assert detail.startswith("calling coder LLM")
    assert payload.get("model_call") is True
    assert payload.get("stage") == "coder"
    assert "appointment_scheduling" not in detail or "wrote handler" not in detail


def test_calling_note_does_not_claim_handler_progress(tmp_path):
    """(d) In-flight model call keeps done at the last real write."""
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="vet", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="wrote handler appointment_scheduling (coder LLM (kimi-k2.7-code))",
        payload={"stage": "handlers", "done": 2, "total": 6},
    )
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="calling coder LLM for clinical_treatment_notes",
        payload={
            "stage": "handlers",
            "capability": "clinical_treatment_notes",
            "model_call": True,
            "deadline_s": 390,
        },
    )

    with _hold_build_thread(out):
        status = build_status(out)
    assert status["state"] == "building"
    assert status["model_call_in_progress"] is True
    assert status["model_call_deadline_s"] == 390
    assert "calling coder LLM" in status["last_event"]
    assert "clinical_treatment_notes" in status["last_event"]
    # No done/total on the calling NOTE — must not look like handler 3/6.
    assert "phase_progress" not in status
    assert status["activity_done"] is None


def test_overdue_model_call_fails_the_build_status(tmp_path):
    """Past the watchdog wall the Floor must stop, not climb 'quiet for N'."""
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="vet", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="calling coder LLM for clinical_treatment_notes",
        payload={
            "stage": "handlers",
            "model_call": True,
            "deadline_s": 30,
        },
    )
    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    ).isoformat(timespec="seconds")
    aged = []
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = stale_ts
        aged.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text("\n".join(aged) + "\n", encoding="utf-8")

    status = build_status(out)
    assert status["state"] == "failed", status
    assert "coder LLM timed out" in status["detail"]
    assert "clinical_treatment_notes" in status["detail"]
    assert status["pilot_ready"] is False


def test_overdue_uses_calling_note_age_not_a_later_event(tmp_path):
    """A later ledger event must not reset the 480s wall (1965s class)."""
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="makers", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="calling coder LLM for class_and_event_scheduling",
        payload={
            "stage": "coder",
            "model_call": True,
            "deadline_s": 480,
        },
    )
    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=1965)
    ).isoformat(timespec="seconds")
    aged = []
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = stale_ts
        aged.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text(
        "\n".join(aged) + "\n", encoding="utf-8"
    )
    # Fresh event after the calling NOTE — used to reset idle_s.
    ledger.append(
        EventKind.PHASE_STARTED,
        role=BuildRole.WRITER,
        detail="heartbeat",
    )

    status = build_status(out)
    assert status["state"] == "failed", status
    assert "deadline 480s" in status["detail"], status
    assert "class_and_event_scheduling" in status["detail"]
    elapsed_s = int(status["detail"].split("after ", 1)[1].split("s", 1)[0])
    assert 1964 <= elapsed_s <= 1975, status["detail"]
    assert status["pilot_ready"] is False


def test_overdue_1085s_vs_480s_fails_the_build_status(tmp_path):
    """Live sess_ab446de: 1085s elapsed vs 480s deadline is still a timeout."""
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="lettings", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="calling coder LLM for maintenance_tickets",
        payload={
            "stage": "coder",
            "model_call": True,
            "deadline_s": 480,
        },
    )
    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=1085)
    ).isoformat(timespec="seconds")
    aged = []
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = stale_ts
        aged.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text(
        "\n".join(aged) + "\n", encoding="utf-8"
    )

    status = build_status(out)
    assert status["state"] == "failed", status
    assert "deadline 480s" in status["detail"], status
    assert "maintenance_tickets" in status["detail"]
    elapsed_s = int(status["detail"].split("after ", 1)[1].split("s", 1)[0])
    assert 1084 <= elapsed_s <= 1095, status["detail"]
    assert status["pilot_ready"] is False


def test_legacy_160s_timeout_is_the_live_510s_wall(monkeypatch):
    """Live Floor copy: coder LLM timed out after 510s.

    attempt_wall_s (old) = call_timeout_s × MAX_LEGS(3) + grace(30).
    160 × 3 + 30 = 510 — Render FACTORY_CODER_TIMEOUT_S is 160, not the
    code default 120 and not the earlier 150 guess (480s + poll lag).
    That leftover must remap so a dashboard pin cannot keep killing WRITER.
    """
    from app.factory import llm_watchdog as wd

    monkeypatch.delenv("FACTORY_CODER_TIMEOUT_S", raising=False)
    monkeypatch.delenv("FACTORY_CODER_ATTEMPT_WALL_S", raising=False)
    assert wd.call_timeout_s() == wd.DEFAULT_CALL_TIMEOUT_S
    assert wd.attempt_wall_s() == wd.DEFAULT_ATTEMPT_WALL_S
    assert wd.DEFAULT_CALL_TIMEOUT_S >= 1200.0
    assert wd.DEFAULT_ATTEMPT_WALL_S >= 2400.0
    assert wd.DEFAULT_ATTEMPT_WALL_S > 510.0
    assert wd.MAX_LEGS * 160 + wd.MODEL_CALL_GRACE_S == 510

    for leftover in ("120", "150", "160"):
        monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", leftover)
        assert wd.call_timeout_s() == wd.DEFAULT_CALL_TIMEOUT_S, leftover
        assert wd.attempt_wall_s() == wd.DEFAULT_ATTEMPT_WALL_S, leftover
        assert wd.attempt_wall_s() != wd.MAX_LEGS * float(leftover) + wd.MODEL_CALL_GRACE_S

    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "0.2")
    assert wd.call_timeout_s() == 0.2
    monkeypatch.setenv("FACTORY_CODER_TIMEOUT_S", "1800")
    assert wd.call_timeout_s() == 1800.0
    assert wd.attempt_wall_s() >= 1830.0


def test_in_flight_call_at_510s_is_still_building(tmp_path):
    """Live MakersHub Leeds: Floor showed STOPPED at ~510s. That age is
    still inside the 40-minute calling-NOTE wall — Building, not failed.
    """
    from app.factory import llm_watchdog as wd

    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="makers", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="calling coder LLM for class_and_event_scheduling",
        payload={
            "stage": "coder",
            "model_call": True,
            "deadline_s": wd.DEFAULT_ATTEMPT_WALL_S,
        },
    )
    aged_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=510)
    ).isoformat(timespec="seconds")
    aged = []
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = aged_ts
        aged.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text("\n".join(aged) + "\n", encoding="utf-8")

    with _hold_build_thread(out):
        status = build_status(out)
    assert status["state"] == "building", status
    assert status["model_call_in_progress"] is True
    assert status["model_call_deadline_s"] == wd.DEFAULT_ATTEMPT_WALL_S
    assert status.get("pilot_ready") is not True


def test_in_flight_call_does_not_stall_before_the_watchdog_wall(tmp_path):
    """_STALL_AFTER_S is 30 min. A 35-min handler write inside a 40-min
    wall must stay Building — stall means process gone, not 'still coding'.
    """
    from app.factory import llm_watchdog as wd

    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="lettings", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="calling coder LLM for tenancy_application_pipeline",
        payload={
            "stage": "coder",
            "model_call": True,
            "deadline_s": wd.DEFAULT_ATTEMPT_WALL_S,
        },
    )
    aged_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=2100)
    ).isoformat(timespec="seconds")
    aged = []
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = aged_ts
        aged.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text("\n".join(aged) + "\n", encoding="utf-8")

    with _hold_build_thread(out):
        status = build_status(out)
    assert status["state"] == "building", status
    assert "timed out" not in str(status.get("detail") or "")
    assert status["pilot_ready"] is not True


def test_cli_dispatch_1896s_against_7230s_is_still_building(tmp_path):
    """sess_2fba31ab: quiet kimi at 1896s must not fail a 7230s C-BRIEF wall."""
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="insurehub", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="dispatching compiled brief via FACTORY_CODE_CLI (/usr/local/bin/kimi)",
        payload={
            "stage": "dispatch",
            "source": "coder CLI",
            "model_call": True,
            "deadline_s": 7230.0,
            "done": 0,
            "total": 1,
        },
    )
    aged_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=1896)
    ).isoformat(timespec="seconds")
    aged = []
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = aged_ts
        aged.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text("\n".join(aged) + "\n", encoding="utf-8")

    with _hold_build_thread(out):
        status = build_status(out)
    assert status["state"] == "building", status
    assert status["model_call_in_progress"] is True
    assert status["model_call_deadline_s"] == 7230.0
    assert "timed out" not in str(status.get("detail") or "")
    assert status["pilot_ready"] is not True


def test_cli_dispatch_past_7230s_fails_with_named_timeout(tmp_path):
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="insurehub", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="dispatching compiled brief via FACTORY_CODE_CLI (/usr/local/bin/kimi)",
        payload={
            "stage": "dispatch",
            "source": "coder CLI",
            "model_call": True,
            "deadline_s": 7230.0,
        },
    )
    stale_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=7300)
    ).isoformat(timespec="seconds")
    aged = []
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = stale_ts
        aged.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text("\n".join(aged) + "\n", encoding="utf-8")

    status = build_status(out)
    assert status["state"] == "failed", status
    assert "coder LLM timed out" in status["detail"]
    assert "deadline 7230s" in status["detail"]
    assert status["pilot_ready"] is False


def test_cli_stdout_note_does_not_drop_7230s_watchdog(tmp_path):
    out = tmp_path / "build"
    out.mkdir()
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id="insurehub", inputs_hash="abc")
    ledger.append(EventKind.PHASE_STARTED, role=BuildRole.WRITER, detail="WRITER")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="dispatching compiled brief via FACTORY_CODE_CLI (/usr/local/bin/kimi)",
        payload={
            "stage": "dispatch",
            "source": "coder CLI",
            "model_call": True,
            "deadline_s": 7230.0,
            "done": 0,
            "total": 1,
        },
    )
    aged_ts = (
        datetime.now(timezone.utc) - timedelta(seconds=1896)
    ).isoformat(timespec="seconds")
    aged = []
    for line in (out / "build_ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        payload["ts"] = aged_ts
        aged.append(json.dumps(payload, sort_keys=True))
    (out / "build_ledger.jsonl").write_text("\n".join(aged) + "\n", encoding="utf-8")
    ledger.append(
        EventKind.NOTE,
        role=BuildRole.WRITER,
        detail="thinking…",
        payload={"stage": "dispatch", "source": "coder CLI"},
    )

    with _hold_build_thread(out):
        status = build_status(out)
    assert status["state"] == "building", status
    assert status["model_call_in_progress"] is True
    assert status["model_call_deadline_s"] == 7230.0
    assert "timed out" not in str(status.get("detail") or "")


def test_generic_coder_error_still_templates(monkeypatch):
    """Timeout is fatal; a refused/empty completion still ships the template."""
    from app.factory.build import roles_handlers

    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setattr("app.factory.coder.coder_enabled", lambda: True)
    monkeypatch.setattr(
        "app.factory.build.roles_handlers._budget_too_low", lambda ctx, what: False
    )
    monkeypatch.setattr(
        "app.factory.coder.generate_platform_handler",
        lambda **kw: (_ for _ in ()).throw(coder.CoderError("model refused")),
    )
    ctx = RoleContext(
        role=BuildRole.WRITER,
        workspace=None,
        blueprint=type("B", (), {"product_name": "Vet", "vertical": "vet"})(),
        plan=None,
    )
    cap = type("C", (), {"capability_id": "x", "notes": ""})()
    assert roles_handlers._coder_body(ctx, cap, [], {}) is None
    assert "model refused" in ctx.state["coder_failures"]["x"]
