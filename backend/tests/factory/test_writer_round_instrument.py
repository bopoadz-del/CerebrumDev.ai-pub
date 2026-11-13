"""F-S1: the writer session's per-round timing, measured from the CLI's exec
stream, and a heartbeat that never reports an unreadable tree as empty.

Every rule is pinned by two inputs that must disagree: a 3-round stream vs a
1-round stream, a stream with usage vs one without, overlapping specialists
vs sequential ones, a readable tree vs one whose walk raises.
"""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from app.factory.build import codewhale_worker as worker_mod
from app.factory.build.codewhale_worker import (
    EXEC_STREAM_SCHEMA,
    FILES_ON_DISK,
    FILES_UNREADABLE,
    WRITER_PROGRESS_FIELDS,
    _parse_writer_receipt,
    authored_files_or_none,
    heartbeat_line,
    rounds_summary_line,
    stream_progress_record,
    writer_round_stats,
)


def _ev(kind: str, **fields):
    return {"type": kind, "schema": EXEC_STREAM_SCHEMA, "schema_version": 1, **fields}


def _meta(status="completed", **extra):
    return _ev(
        "metadata",
        meta={
            "receipt_kind": "terminal",
            "provider": "prov",
            "model": "mdl",
            "status": status,
            "termination_reason": "resolved",
            "error_category": None,
            "error": None,
            **extra,
        },
    )


# --------------------------------------------------------------------------
# Round counts and walls: 3 rounds vs 1 round.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "times,end_t,rounds,p50,p95",
    [
        ([0.0, 10.0, 40.0], 50.0, 3, 10.0, 30.0),  # walls 10, 30, 10
        ([5.0], 25.0, 1, 20.0, 20.0),  # one wall: to the session end
    ],
)
def test_round_count_and_walls_follow_the_stream(times, end_t, rounds, p50, p95):
    timed = [(t, _ev("tool_use", name="write_file", input="x")) for t in times]
    timed.append((end_t - 0.5, _ev("done")))
    stats = writer_round_stats(timed, end_t)
    assert stats["rounds"] == rounds
    assert stats["round_p50_s"] == p50
    assert stats["round_p95_s"] == p95


def test_no_rounds_reports_zero_and_no_walls():
    stats = writer_round_stats([(1.0, _ev("done"))], 2.0)
    assert stats["rounds"] == 0
    assert "round_p50_s" not in stats and "round_p95_s" not in stats


# --------------------------------------------------------------------------
# Tokens: a stream with usage vs one without.
# --------------------------------------------------------------------------


def test_usage_fields_give_last_prompt_tokens():
    timed = [
        (0.0, _ev("tool_use", name="read")),
        (1.0, _ev("turn_usage", turn=1, input_tokens=1200, output_tokens=80)),
        (2.0, _ev("tool_use", name="write")),
        (3.0, _ev("turn_usage", turn=2, usage={"prompt_tokens": 4800, "completion_tokens": 90})),
    ]
    stats = writer_round_stats(timed, 4.0)
    assert stats["rounds"] == 2
    assert stats["prompt_tokens_last"] == 4800
    assert stats["completion_tokens_last"] == 90
    assert stats["usage_turns"] == 2


def test_stream_without_usage_gives_counts_without_tokens():
    timed = [
        (0.0, _ev("tool_use", name="read")),
        (1.0, _ev("turn_usage", turn=1)),  # reports no token fields
        (2.0, _ev("tool_use", name="write")),
    ]
    stats = writer_round_stats(timed, 4.0)
    assert stats["rounds"] == 2
    assert "prompt_tokens_last" not in stats
    assert "usage_turns" not in stats
    assert "prompt_tokens_last=-" in rounds_summary_line(stats)


# --------------------------------------------------------------------------
# Specialists: overlapping vs sequential, labels read from the event.
# --------------------------------------------------------------------------


def _specialists(spans):
    timed = []
    for label, run_id, start, stop in spans:
        timed.append((start, _ev("agent_spawned", label=label, run_id=run_id)))
        timed.append((stop, _ev("workflow_event", run_id=run_id)))
    return timed


def test_overlapping_specialists_count_concurrency():
    timed = _specialists([("ALPHA", "r1", 0.0, 50.0), ("BETA", "r2", 10.0, 30.0)])
    stats = writer_round_stats(timed, 60.0)
    assert stats["specialists"] == 2
    assert stats["max_concurrent"] == 2
    walls = {w["label"]: w["wall_s"] for w in stats["specialist_walls"]}
    assert walls == {"ALPHA": 50.0, "BETA": 20.0}
    assert stats["specialists_unended"] == 0


def test_sequential_specialists_run_one_at_a_time():
    timed = _specialists([("ALPHA", "r1", 0.0, 20.0), ("BETA", "r2", 20.0, 45.0)])
    stats = writer_round_stats(timed, 60.0)
    assert stats["specialists"] == 2
    assert stats["max_concurrent"] == 1
    line = rounds_summary_line(stats)
    assert "specialists=2" in line and "max_concurrent=1" in line
    assert "ALPHA:20.0s" in line and "BETA:25.0s" in line


def test_specialist_never_named_again_runs_to_session_end():
    timed = [(5.0, _ev("agent_spawned", label="ALPHA", run_id="r1"))]
    stats = writer_round_stats(timed, 65.0)
    assert stats["specialist_walls"] == [{"label": "ALPHA", "wall_s": 60.0, "ended": False}]
    assert stats["specialists_unended"] == 1


# --------------------------------------------------------------------------
# The progress-log record per event.
# --------------------------------------------------------------------------


def test_progress_record_per_round_and_usage():
    rec = stream_progress_record(_ev("tool_use", name="write_file", input="abcd"), 7)
    assert rec == {"round": 7, "tool": "write_file", "bytes": 4}
    with_usage = stream_progress_record(_ev("turn_usage", input_tokens=10), 1)
    without = stream_progress_record(_ev("turn_usage"), 1)
    assert with_usage == {"turn_usage": True, "prompt_tokens": 10}
    assert without == {"turn_usage": True}
    assert stream_progress_record(_ev("done"), 1) is None


# --------------------------------------------------------------------------
# Receipt: the exec stream vs the legacy --json summary.
# --------------------------------------------------------------------------


def test_receipt_from_exec_stream_reads_metadata_and_tool_rounds():
    lines = [
        json.dumps(_ev("tool_use", name="write_file", input="x" * 50)),
        "not json at all",
        json.dumps(_ev("tool_result", completed_at="t")),
        json.dumps(_ev("tool_use", name="shell", input={"cmd": "ls"})),
        json.dumps(_meta()),
        json.dumps(_ev("done")),
    ]
    receipt = _parse_writer_receipt(lines, 0)
    assert receipt.status == "completed"
    assert receipt.termination_reason == "resolved"
    assert (receipt.provider, receipt.model) == ("prov", "mdl")
    assert [t["name"] for t in receipt.tools] == ["write_file", "shell"]
    assert receipt.tools[0]["input_bytes"] == 50
    assert all("input" not in t and "schema" not in t for t in receipt.tools)


def test_receipt_from_failed_stream_keeps_error_category():
    lines = [
        json.dumps(_ev("error", error="boom")),
        json.dumps(_meta(status="failed", error_category="network", error="boom")),
    ]
    receipt = _parse_writer_receipt(lines, 75)
    assert receipt.status == "failed"
    assert receipt.error_category == "network"
    assert receipt.tools == []


def test_stream_without_metadata_is_a_named_failure():
    lines = [json.dumps(_ev("error", error="auth refused"))]
    with pytest.raises(worker_mod.WorkerError, match="auth refused"):
        _parse_writer_receipt(lines, 1)


def test_legacy_summary_still_parses():
    lines = ['{"status": "completed", "termination_reason": "resolved", "tools": [{"tool": "write"}]}']
    receipt = _parse_writer_receipt(lines, 0)
    assert receipt.status == "completed"
    assert receipt.tools == [{"tool": "write"}]
    assert receipt.rounds == {}


# --------------------------------------------------------------------------
# Heartbeat: readable tree vs a tree whose walk raises.
# --------------------------------------------------------------------------


def test_readable_tree_counts_files(tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "b.py").write_text("y")
    assert authored_files_or_none(tmp_path) == 2


def test_walk_that_raises_is_no_sample(monkeypatch, tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.py").write_text("y")

    def _raising_rglob(self, pattern):
        yield tmp_path / "a.py"
        raise OSError("tree changed under the walk")

    monkeypatch.setattr(Path, "rglob", _raising_rglob)
    assert authored_files_or_none(tmp_path) is None


def test_unreadable_heartbeat_names_the_last_good_count():
    readable = heartbeat_line(600.0, 137, 16)
    unreadable = heartbeat_line(600.0, 137, 16, unreadable=True)
    assert "137 file(s) on disk" in readable and "unreadable" not in readable
    assert "unreadable" in unreadable and "137" in unreadable
    assert FILES_UNREADABLE in WRITER_PROGRESS_FIELDS


# --------------------------------------------------------------------------
# End to end through run_worker_job: a 3-round stream lands in the receipt
# and in docs/writer_progress.jsonl.
# --------------------------------------------------------------------------


def _run_with_stdout(monkeypatch, tmp_path, stdout_text):
    from app.factory.build.tenant_bind import TenantStoreBinding

    class _FakePopen:
        def __init__(self, argv, **kwargs):
            self.argv = argv
            self.stdout = io.StringIO(stdout_text)
            self.stderr = io.StringIO("")
            self.returncode = 0

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = 9

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(worker_mod, "worker_cli_path", lambda: "/nonexistent/codewhale")
    binding = TenantStoreBinding(
        tenant_key="key_fs1", store_dir=Path("/tmp/tenant-fs1"), bound_at=0.0
    )
    return worker_mod.run_worker_job("build it", tmp_path, tenant_store=binding)


@pytest.mark.parametrize("n_rounds", [3, 1])
def test_run_worker_job_records_rounds(monkeypatch, tmp_path, n_rounds):
    lines = [json.dumps(_ev("tool_use", name=f"t{i}", input="ab")) for i in range(n_rounds)]
    lines += [json.dumps(_meta()), json.dumps(_ev("done"))]
    receipt = _run_with_stdout(monkeypatch, tmp_path, "\n".join(lines) + "\n")
    assert receipt.status == "completed"
    assert len(receipt.tools) == n_rounds
    assert receipt.rounds["rounds"] == n_rounds
    records = [
        json.loads(line)
        for line in (tmp_path / "docs" / "writer_progress.jsonl").read_text().splitlines()
    ]
    logged = [r for r in records if "round" in r]
    assert [r["round"] for r in logged] == list(range(1, n_rounds + 1))
    assert all(r["bytes"] == 2 and "elapsed_s" in r for r in logged)


def test_files_on_disk_field_is_unchanged():
    assert FILES_ON_DISK in WRITER_PROGRESS_FIELDS
