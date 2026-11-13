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


@pytest.mark.parametrize("tool_times,rounds,has_walls", [([], 0, False), ([1.0], 1, True)])
def test_no_rounds_reports_zero_and_no_walls(tool_times, rounds, has_walls):
    timed = [(t, _ev("tool_use", name="w")) for t in tool_times] + [(1.5, _ev("done"))]
    stats = writer_round_stats(timed, 2.0)
    assert stats["rounds"] == rounds
    assert ("round_p50_s" in stats) is has_walls
    assert ("round_p95_s" in stats) is has_walls


# --------------------------------------------------------------------------
# Tokens: a stream with usage vs one without.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "usage,prompt_last,completion_last,turns",
    [
        (
            [
                {"input_tokens": 1200, "output_tokens": 80},
                {"usage": {"prompt_tokens": 4800, "completion_tokens": 90}},
            ],
            4800,
            90,
            2,
        ),
        ([{}, {}], None, None, 0),  # turn_usage events that report no tokens
    ],
)
def test_usage_fields_give_last_prompt_tokens(usage, prompt_last, completion_last, turns):
    timed = []
    for i, fields in enumerate(usage):
        timed.append((2.0 * i, _ev("tool_use", name="w")))
        timed.append((2.0 * i + 1, _ev("turn_usage", turn=i + 1, **fields)))
    stats = writer_round_stats(timed, 4.0)
    assert stats["rounds"] == 2
    assert stats.get("prompt_tokens_last") == prompt_last
    assert stats.get("completion_tokens_last") == completion_last
    assert stats.get("usage_turns", 0) == turns
    shown = prompt_last if prompt_last is not None else "-"
    assert f"prompt_tokens_last={shown}" in rounds_summary_line(stats)


# --------------------------------------------------------------------------
# Specialists: overlapping vs sequential, labels read from the event.
# --------------------------------------------------------------------------


def _specialists(spans):
    timed = []
    for label, run_id, start, stop in spans:
        timed.append((start, _ev("agent_spawned", label=label, run_id=run_id)))
        timed.append((stop, _ev("workflow_event", run_id=run_id)))
    return timed


@pytest.mark.parametrize(
    "spans,peak,walls",
    [
        ([("ALPHA", "r1", 0.0, 50.0), ("BETA", "r2", 10.0, 30.0)], 2, {"ALPHA": 50.0, "BETA": 20.0}),
        ([("ALPHA", "r1", 0.0, 20.0), ("BETA", "r2", 20.0, 45.0)], 1, {"ALPHA": 20.0, "BETA": 25.0}),
    ],
)
def test_specialist_concurrency_follows_overlap(spans, peak, walls):
    stats = writer_round_stats(_specialists(spans), 60.0)
    assert stats["specialists"] == 2
    assert stats["max_concurrent"] == peak
    assert {w["label"]: w["wall_s"] for w in stats["specialist_walls"]} == walls
    assert stats["specialists_unended"] == 0
    line = rounds_summary_line(stats)
    assert "specialists=2" in line and f"max_concurrent={peak}" in line
    for label, wall in walls.items():
        assert f"{label}:{wall}s" in line


@pytest.mark.parametrize("named_again,wall,unended", [(False, 60.0, 1), (True, 15.0, 0)])
def test_specialist_never_named_again_runs_to_session_end(named_again, wall, unended):
    timed = [(5.0, _ev("agent_spawned", label="ALPHA", run_id="r1"))]
    if named_again:
        timed.append((20.0, _ev("workflow_event", run_id="r1")))
    stats = writer_round_stats(timed, 65.0)
    assert stats["specialist_walls"] == [
        {"label": "ALPHA", "wall_s": wall, "ended": named_again}
    ]
    assert stats["specialists_unended"] == unended


# --------------------------------------------------------------------------
# The progress-log record per event.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "event,expected",
    [
        (_ev("tool_use", name="write_file", input="abcd"), {"round": 7, "tool": "write_file", "bytes": 4}),
        (_ev("turn_usage", input_tokens=10), {"turn_usage": True, "prompt_tokens": 10}),
        (_ev("turn_usage"), {"turn_usage": True}),
        (_ev("done"), None),
    ],
)
def test_progress_record_per_round_and_usage(event, expected):
    assert stream_progress_record(event, 7) == expected


# --------------------------------------------------------------------------
# Receipt: the exec stream vs the legacy --json summary.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tools,first_bytes",
    [
        ([("write_file", "x" * 50), ("shell", {"cmd": "ls"})], 50),
        ([("read_file", "abc")], 3),
    ],
)
def test_receipt_from_exec_stream_reads_metadata_and_tool_rounds(tools, first_bytes):
    lines = [json.dumps(_ev("tool_use", name=n, input=i)) for n, i in tools]
    lines[1:1] = ["not json at all", json.dumps(_ev("tool_result", completed_at="t"))]
    lines += [json.dumps(_meta()), json.dumps(_ev("done"))]
    receipt = _parse_writer_receipt(lines, 0)
    assert receipt.status == "completed"
    assert receipt.termination_reason == "resolved"
    assert (receipt.provider, receipt.model) == ("prov", "mdl")
    assert [t["name"] for t in receipt.tools] == [n for n, _ in tools]
    assert receipt.tools[0]["input_bytes"] == first_bytes
    assert all("input" not in t and "schema" not in t for t in receipt.tools)


@pytest.mark.parametrize("category,code", [("network", 75), ("model", 1)])
def test_receipt_from_failed_stream_keeps_error_category(category, code):
    lines = [
        json.dumps(_ev("error", error="boom")),
        json.dumps(_meta(status="failed", error_category=category, error="boom")),
    ]
    receipt = _parse_writer_receipt(lines, code)
    assert receipt.status == "failed"
    assert receipt.error_category == category
    assert receipt.tools == []


def test_stream_without_metadata_is_a_named_failure():
    lines = [json.dumps(_ev("error", error="auth refused"))]
    with pytest.raises(worker_mod.WorkerError, match="auth refused"):
        _parse_writer_receipt(lines, 1)


@pytest.mark.parametrize("tools", [[{"tool": "write"}], [{"tool": "read"}, {"tool": "write"}]])
def test_legacy_summary_still_parses(tools):
    lines = [json.dumps({"status": "completed", "termination_reason": "resolved", "tools": tools})]
    receipt = _parse_writer_receipt(lines, 0)
    assert receipt.status == "completed"
    assert receipt.tools == tools
    assert receipt.rounds == {}


# --------------------------------------------------------------------------
# Heartbeat: readable tree vs a tree whose walk raises.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("files,count", [(["a.py", "pkg/b.py"], 2), (["a.py"], 1)])
def test_readable_tree_counts_files(tmp_path, files, count):
    for rel in files:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    assert authored_files_or_none(tmp_path) == count


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


@pytest.mark.parametrize(
    "command,suite_runs",
    [('python -m pytest -m "not pilot" -q', 1), ("ls -la", 0)],
)
def test_run_worker_job_counts_suite_runs(monkeypatch, tmp_path, command, suite_runs):
    lines = [
        json.dumps(_ev("tool_use", name="bash", input={"command": command})),
        json.dumps(_meta()),
        json.dumps(_ev("done")),
    ]
    stdout = "".join(line + "\n" for line in lines)
    receipt = _run_with_stdout(monkeypatch, tmp_path, stdout)
    assert receipt.rounds["suite_runs"] == suite_runs
    assert f"suite_runs={suite_runs}" in rounds_summary_line(receipt.rounds)


def test_files_on_disk_field_is_unchanged():
    assert FILES_ON_DISK in WRITER_PROGRESS_FIELDS


# --------------------------------------------------------------------------
# suite_runs: a pytest run outside the targeted set vs one inside it.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,expected",
    [
        ('python -m pytest -m "not pilot" -q', [[]]),
        ("cd /w && pytest tests/test_a.py::test_x -k foo", [["tests/test_a.py::test_x"]]),
        ("ls -la && cat app/main.py", []),
        ("pytest tests && python -m pytest tests/test_b.py", [["tests"], ["tests/test_b.py"]]),
    ],
)
def test_pytest_selections_by_shape(command, expected):
    assert worker_mod.pytest_selections(command) == expected


@pytest.mark.parametrize(
    "selection,targeted,suite",
    [
        ([], ["tests/test_a.py"], True),  # no path: the whole collection
        (["tests"], ["tests/test_a.py"], True),  # a directory
        (["tests/test_b.py"], ["tests/test_a.py"], True),  # outside the set
        (["./tests/test_a.py::test_x"], ["tests/test_a.py"], False),  # inside
        (["tests/test_b.py"], None, False),  # set unknown: shape only
    ],
)
def test_is_suite_run_against_the_targeted_set(selection, targeted, suite):
    assert worker_mod.is_suite_run(selection, targeted) is suite


@pytest.mark.parametrize(
    "command,targeted,count",
    [
        ('python -m pytest -m "not pilot" -q', ["tests/test_a.py"], 1),
        ("python -m pytest -q tests/test_a.py", ["tests/test_a.py"], 0),
    ],
)
def test_suite_runs_in_counts_only_non_targeted(command, targeted, count):
    calls = []

    def _targeted():
        calls.append(1)
        return targeted

    event = _ev("tool_use", name="bash", input={"command": command})
    assert worker_mod.suite_runs_in(event, _targeted) == count
    # A tool that runs no pytest never computes the set.
    quiet = _ev("tool_use", name="bash", input={"command": "ls"})
    assert worker_mod.suite_runs_in(quiet, _targeted) == 0
    assert len(calls) == 1


def _tree(root: Path, touched: str) -> None:
    from app.factory.build.writer_behaviour import SELF_CHECK_REL

    (root / "app" / "actions").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "tests" / "test_alpha.py").write_text("from app.actions import alpha\n")
    (root / "tests" / "test_beta.py").write_text("from app.actions import beta\n")
    for name in ("alpha", "beta"):
        (root / "app" / "actions" / f"{name}.py").write_text("x = 1\n")
    stamp = root / SELF_CHECK_REL
    stamp.parent.mkdir(parents=True)
    stamp.write_text("# stamp\n")
    import os
    import time

    marker = os.path.getmtime(stamp)
    for name in ("alpha", "beta"):
        p = root / "app" / "actions" / f"{name}.py"
        os.utime(p, (time.time(), marker - 10))
    changed = root / "app" / "actions" / f"{touched}.py"
    os.utime(changed, (time.time(), marker + 10))


@pytest.mark.parametrize("touched", ["alpha", "beta"])
def test_targeted_tests_now_follows_what_changed(tmp_path, touched):
    _tree(tmp_path, touched)
    assert worker_mod.targeted_tests_now(tmp_path) == [f"tests/test_{touched}.py"]


def test_suite_runs_rides_the_summary_line():
    assert "suite_runs=2" in rounds_summary_line({"rounds": 3, "suite_runs": 2})
    assert "suite_runs=-" in rounds_summary_line({"rounds": 3})
