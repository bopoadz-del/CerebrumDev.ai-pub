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
from app.factory.llm_watchdog import ModelCallTimeout, post_with_deadline


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
