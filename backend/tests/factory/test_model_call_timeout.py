"""A per-call connect/read timeout is a named ``model_call_timeout`` stall.

The writer's headless harness hung because a black-holed request had no
per-call bound — only the wall. ``post_with_deadline`` now splits connect
from read and retries once on a fresh connection, so a fake endpoint that
never answers must fail fast with the named event, never silently burn the
wall.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

from app.factory.build.failure_kinds import (
    MODEL_CALL_TIMEOUT,
    TIMEOUT,
    failure_kind,
)
from app.factory.llm_watchdog import (
    MODEL_CALL_RETRY_MAX,
    ModelCallTimeout,
    connect_timeout_s,
    model_call_retries,
    per_call_timeout,
    post_with_deadline,
    read_timeout_s,
)


class _SilentEndpoint:
    """A TCP server that accepts connections and never sends a byte back."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.05)
        self.accepted = 0
        self._conns: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self._sock.getsockname()[1])

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.accepted += 1
            self._conns.append(conn)

    def close(self) -> None:
        self._stop.set()
        for conn in self._conns:
            try:
                conn.close()
            except OSError:
                pass
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2)


def test_silent_endpoint_fails_fast_with_named_event(monkeypatch):
    """A never-answering endpoint is a per-call read timeout: it must raise
    ``ModelCallTimeout`` (named ``model_call_timeout``) fast, and retry once
    on a fresh connection — two accepts, not a silent wall burn."""
    monkeypatch.setenv("FACTORY_CODER_READ_TIMEOUT_S", "0.2")
    monkeypatch.setenv("FACTORY_CODER_CONNECT_TIMEOUT_S", "0.5")
    endpoint = _SilentEndpoint()
    try:
        url = f"http://127.0.0.1:{endpoint.port}/v1/chat/completions"
        started = time.monotonic()
        with pytest.raises(ModelCallTimeout) as exc:
            post_with_deadline(
                url,
                json={"model": "m", "messages": []},
                timeout=2.0,
            )
        elapsed = time.monotonic() - started

        assert exc.value.named == MODEL_CALL_TIMEOUT
        assert elapsed < 3.0, elapsed
        # one retry with a fresh connection -> exactly two accepts
        assert endpoint.accepted == 2, endpoint.accepted
    finally:
        endpoint.close()


def test_model_call_timeout_is_a_distinct_failure_kind():
    """The named stall maps to its own failure kind, not the generic wall
    ``timeout`` — readers can tell a per-call hang from a wall kill."""
    stall = ModelCallTimeout("no answer within per-call timeouts")
    assert failure_kind(stall) == MODEL_CALL_TIMEOUT
    assert failure_kind(TimeoutError("wall")) == TIMEOUT


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [("7.5", 7.5), ("0.2", 0.2)],
)
def test_connect_timeout_s_honours_the_env_override(monkeypatch, env_value, expected):
    """The connect timeout is its own, overridable phase: a dropped guard
    would pin it to the 30s default and a black-holed SYN could never fail
    fast."""
    monkeypatch.setenv("FACTORY_CODER_CONNECT_TIMEOUT_S", env_value)
    assert connect_timeout_s() == expected


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [("11", 11.0), ("0.3", 0.3)],
)
def test_read_timeout_s_honours_the_env_override(monkeypatch, env_value, expected):
    """The read timeout is longer than connect and still overridable."""
    monkeypatch.setenv("FACTORY_CODER_READ_TIMEOUT_S", env_value)
    assert read_timeout_s() == expected


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [("2", 2), ("99", MODEL_CALL_RETRY_MAX)],
)
def test_model_call_retries_honours_override_and_cap(monkeypatch, env_value, expected):
    """Retries are env-driven and capped at MODEL_CALL_RETRY_MAX: a dropped
    guard would ignore the override and never bound a retry storm."""
    monkeypatch.setenv("FACTORY_CODER_MODEL_CALL_RETRIES", env_value)
    assert model_call_retries() == expected


@pytest.mark.parametrize(
    ("connect_s", "read_s"),
    [("3", "60"), ("0.5", "0.2")],
)
def test_per_call_timeout_splits_connect_from_read(monkeypatch, connect_s, read_s):
    """The split is the point: connect and read are distinct phases so a
    stalled connect fails fast instead of sharing the whole post wall."""
    monkeypatch.setenv("FACTORY_CODER_CONNECT_TIMEOUT_S", connect_s)
    monkeypatch.setenv("FACTORY_CODER_READ_TIMEOUT_S", read_s)
    timeout = per_call_timeout()
    assert timeout.connect == float(connect_s)
    assert timeout.read == float(read_s)
    assert timeout.write == float(read_s)
    assert timeout.pool == float(connect_s)


def test_a_responsive_endpoint_returns_the_response():
    """The ``_run`` worker's success path: a responsive endpoint hands the
    response back through ``post_with_deadline``. This is the other half of
    the connect/read split — the stall side is the silent endpoint above. A
    worker that returns without a result must fail fast, not wait out the
    wall."""
    import http.server

    body = b'{"ok": true}'

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        resp = post_with_deadline(
            url,
            json={"model": "m", "messages": []},
            timeout=1.0,
        )
        assert 200 <= resp.status_code < 300
        assert resp.json()["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
