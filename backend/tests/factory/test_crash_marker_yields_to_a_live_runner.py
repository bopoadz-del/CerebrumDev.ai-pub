"""A live runner outranks a stale crash marker in the status the UI reads.

A deploy kills a build and writes ``.build_thread_crashed``. The owner's
Continue resumes it (RUN_STARTED) while the marker stays on disk. The status
used to return failed / BUILD_THREAD_CRASHED for that live run, so the Floor
showed nothing running while the writer was writing — and only DELETE (which
checks the live thread) knew better. A marker with no live thread still
means crashed.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

import pytest

from app.factory.build.ledger import BuildLedger
from app.factory.build_jobs import build_status, register_runner_thread


@contextmanager
def _live_runner(output_dir):
    """Register a genuinely alive runner thread for this workspace."""
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, daemon=True)
    thread.start()
    register_runner_thread(output_dir, thread)
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


def _resumed_workspace(out, product: str):
    out.mkdir(parents=True, exist_ok=True)
    ledger = BuildLedger(out / "build_ledger.jsonl")
    ledger.start_run(product_id=product, inputs_hash="hash-1")
    # The deploy's crash record is still on disk; the resume does not clear it.
    (out / ".build_thread_crashed").write_text("killed by a deploy\n", encoding="utf-8")
    return out


@pytest.mark.parametrize("product", ["crash-probe", "crash-probe-two"])
def test_a_live_run_reads_as_building_over_a_stale_crash_marker(tmp_path, product):
    out = _resumed_workspace(tmp_path / "build", product)
    with _live_runner(out):
        assert build_status(out)["state"] == "building"


@pytest.mark.parametrize("product", ["crash-probe", "crash-probe-two"])
def test_a_marker_with_no_live_thread_is_still_a_crash(tmp_path, product):
    out = _resumed_workspace(tmp_path / "build", product)
    status = build_status(out)
    assert status["state"] == "failed"
    assert status["honesty"] == "BUILD_THREAD_CRASHED"
