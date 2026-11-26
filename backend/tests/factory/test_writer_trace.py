"""Writer trace: one action per tool call and where the pass's time went.

Each rule is pinned by two inputs that must give different answers, and by a
case where it must not fire.
"""

from __future__ import annotations

import pytest

from app.factory.build.writer_trace import SLOWEST_SHOWN, writer_trace


def _use(name, command=None, **extra):
    event = {"type": "tool_use", "name": name, **extra}
    if command is not None:
        event["input"] = {"command": command}
    return event


def _result(**extra):
    return {"type": "tool_result", **extra}


@pytest.mark.parametrize(
    "call_t, result_t, end_t, model, tool, tail",
    [(4.0, 10.0, 12.0, 4.0, 6.0, 2.0), (1.0, 31.0, 40.0, 1.0, 30.0, 9.0)],
)
def test_time_splits_into_model_tool_and_tail(call_t, result_t, end_t, model, tool, tail):
    trace = writer_trace(
        [(call_t, _use("bash", "pytest -q tests")), (result_t, _result(exit_code=0))],
        start_t=0.0,
        end_t=end_t,
    )
    summary = trace["summary"]
    assert summary["model_s"] == model
    assert summary["tool_s"] == {"bash: pytest": tool}
    assert summary["after_last_action_s"] == tail
    assert summary["unsplit_s"] == 0.0
    assert summary["model_s"] + tool + tail == summary["wall_s"] == end_t


@pytest.mark.parametrize("command, group", [
    ("pip install -r requirements.txt", "bash: pip"),
    ("FOO=1 /usr/bin/npm run build", "bash: npm"),
])
def test_a_shell_call_is_grouped_by_the_program_it_starts(command, group):
    trace = writer_trace([(0.0, _use("bash", command)), (5.0, _result())], 0.0, 5.0)
    assert list(trace["summary"]["tool_s"]) == [group]


@pytest.mark.parametrize("name", ["write_file", "edit"])
def test_a_call_without_a_command_is_grouped_by_its_tool_alone(name):
    trace = writer_trace([(0.0, _use(name)), (2.0, _result())], 0.0, 2.0)
    assert trace["summary"]["tool_s"] == {name: 2.0}
    assert "command" not in trace["actions"][0]


@pytest.mark.parametrize("gap, end_t", [(5.0, 20.0), (9.0, 9.0)])
def test_a_call_whose_result_never_came_is_unsplit_not_model(gap, end_t):
    events = [
        (0.0, _use("bash", "make", id="m")),
        (gap, _use("bash", "ls", id="l")),
        (gap + 1, _result(id="l")),
    ]
    summary = writer_trace(events, 0.0, end_t + 1)["summary"]
    assert summary["unsplit_s"] == gap
    assert summary["model_s"] == 0.0
    assert summary["tool_s"] == {"bash: ls": 1.0}


@pytest.mark.parametrize("ids, first_run, second_run", [
    ((("id", "a"), ("id", "b")), 8.0, 2.0),   # result for b arrives first
    ((None, None), 2.0, 8.0),                 # no ids: earliest open call
])
def test_a_result_closes_the_call_with_its_id_else_the_earliest(ids, first_run, second_run):
    def with_id(event, pair):
        return {**event, pair[0]: pair[1]} if pair else event

    events = [
        (0.0, with_id(_use("bash", "a"), ids[0])),
        (0.0, with_id(_use("bash", "b"), ids[1])),
        (2.0, with_id(_result(), ids[1])),
        (8.0, with_id(_result(), ids[0])),
    ]
    actions = writer_trace(events, 0.0, 8.0)["actions"]
    assert [a["run_s"] for a in actions] == [first_run, second_run]


@pytest.mark.parametrize("code", [0, 2])
def test_the_exit_code_is_kept_when_the_stream_reports_it(code):
    actions = writer_trace(
        [(0.0, _use("bash", "x")), (1.0, _result(exit_code=code))], 0.0, 1.0
    )["actions"]
    assert actions[0]["exit_code"] == code


@pytest.mark.parametrize("result", [_result(), _result(exit_code=True), _result(exit_code="1")])
def test_no_exit_code_is_invented(result):
    actions = writer_trace([(0.0, _use("bash", "x")), (1.0, result)], 0.0, 1.0)["actions"]
    assert "exit_code" not in actions[0]


@pytest.mark.parametrize("n", [SLOWEST_SHOWN - 3, SLOWEST_SHOWN + 4])
def test_slowest_lists_at_most_the_cap_longest_first(n):
    events = []
    t = 0.0
    for i in range(n):
        events += [(t, _use("bash", f"cmd{i}")), (t + i + 1, _result())]
        t += i + 2
    slowest = writer_trace(events, 0.0, t)["summary"]["slowest"]
    assert len(slowest) == min(n, SLOWEST_SHOWN)
    assert [s["command"] for s in slowest][0] == f"cmd{n - 1}"
    assert [s["run_s"] for s in slowest] == sorted((s["run_s"] for s in slowest), reverse=True)


@pytest.mark.parametrize("commands, repeated", [
    (["pytest a", "pytest a", "pytest b"], {"pytest a": 2}),
    (["pytest a", "pytest b", "pytest c"], {}),
])
def test_a_command_run_more_than_once_is_counted(commands, repeated):
    events = []
    for i, command in enumerate(commands):
        events += [(2.0 * i, _use("bash", command)), (2.0 * i + 1, _result())]
    assert writer_trace(events, 0.0, 10.0)["summary"]["repeated"] == repeated


@pytest.mark.parametrize("secret", [
    "sk-or-v1-" + "a1b2c3d4" * 8,
    "ghp_" + "Zx9Yw8Vu7Ts6" * 3,
])
def test_a_secret_in_a_command_line_is_never_recorded(secret):
    trace = writer_trace(
        [(0.0, _use("bash", f"curl -H 'Authorization: Bearer {secret}' x")), (1.0, _result())],
        0.0,
        1.0,
    )
    assert secret not in repr(trace)


@pytest.mark.parametrize("end_t", [0.0, 30.0])
def test_an_empty_stream_is_all_tail(end_t):
    summary = writer_trace([], 0.0, end_t)["summary"]
    assert summary["actions"] == 0
    assert summary["after_last_action_s"] == summary["wall_s"] == end_t
    assert summary["tool_s"] == {} and summary["slowest"] == [] and summary["repeated"] == {}


# --------------------------------------------------------------------------
# Slice 2: the trace is kept, logged and put on the ledger at the end of
# every pass, through the worker's public run path.
# --------------------------------------------------------------------------

import io  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

from app.factory.build import codewhale_worker as worker_mod  # noqa: E402
from app.factory.build.builds_push import is_exported  # noqa: E402
from app.factory.build.writer_trace import (  # noqa: E402
    LEDGER_KEYS,
    WRITER_TRACE_REL,
    record_writer_trace,
)


def _stream_ev(kind, **fields):
    return {
        "type": kind,
        "schema": worker_mod.EXEC_STREAM_SCHEMA,
        "schema_version": 1,
        **fields,
    }


def _meta_line():
    return json.dumps(
        _stream_ev(
            "metadata",
            meta={
                "receipt_kind": "terminal",
                "provider": "prov",
                "model": "mdl",
                "status": "completed",
                "termination_reason": "resolved",
                "error_category": None,
                "error": None,
            },
        )
    )


def _run(monkeypatch, tmp_path, lines, session_id):
    from app.factory.build.tenant_bind import TenantStoreBinding

    stdout_text = "".join(line + "\n" for line in lines)

    class _FakePopen:
        def __init__(self, argv, **kwargs):
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
        tenant_key="key_trace", store_dir=Path("/tmp/tenant-trace"), bound_at=0.0
    )
    return worker_mod.run_worker_job(
        "build it", tmp_path, tenant_store=binding, session_id=session_id
    )


@pytest.mark.parametrize(
    "commands, session_id",
    [(["ls -la", "pytest -q tests/a"], "sess_aaaa1111"), (["cat x"], "sess_bbbb2222")],
)
def test_a_pass_keeps_logs_and_ledgers_its_trace(
    monkeypatch, tmp_path, caplog, commands, session_id
):
    lines = []
    for command in commands:
        lines.append(json.dumps(_stream_ev("tool_use", name="bash", input={"command": command})))
        lines.append(json.dumps(_stream_ev("tool_result", exit_code=0)))
    lines += [_meta_line(), json.dumps(_stream_ev("done"))]
    with caplog.at_level(logging.INFO, logger="app.factory.build.writer_trace"):
        receipt = _run(monkeypatch, tmp_path, lines, session_id)

    kept = json.loads((tmp_path / WRITER_TRACE_REL).read_text(encoding="utf-8"))
    assert kept["session_id"] == session_id
    assert [a["command"] for a in kept["actions"]] == commands
    assert all(a["exit_code"] == 0 for a in kept["actions"])

    ledger = receipt.rounds["time"]
    assert tuple(ledger) == LEDGER_KEYS
    assert ledger["actions"] == len(commands)
    assert "slowest" not in ledger and "repeated" not in ledger

    messages = [r.getMessage() for r in caplog.records]
    action_lines = [m for m in messages if m.startswith("codewhale writer action:")]
    trace_lines = [m for m in messages if m.startswith("codewhale writer trace:")]
    assert len(action_lines) == len(commands)
    assert len(trace_lines) == 1
    # The log shows the first 12 characters, as the dispatch line does.
    assert all(f"session={session_id[:12]} " in m for m in action_lines + trace_lines)
    for command, line in zip(commands, action_lines):
        assert line.endswith(f"cmd={command}")
    assert f"actions={len(commands)}" in trace_lines[0]


def test_a_pass_with_no_stream_events_keeps_no_trace(monkeypatch, tmp_path):
    legacy = json.dumps(
        {"status": "completed", "termination_reason": "resolved", "tools": [{"tool": "write"}]}
    )
    receipt = _run(monkeypatch, tmp_path, [legacy], "sess_cccc3333")
    assert receipt.status == "completed"
    assert "time" not in receipt.rounds
    assert not (tmp_path / WRITER_TRACE_REL).exists()


@pytest.mark.parametrize(
    "rel, ships",
    [(WRITER_TRACE_REL, False), ("docs/architecture.json", True)],
)
def test_the_trace_file_never_ships(rel, ships):
    assert is_exported(Path(rel)) is ships


def test_the_trace_file_is_not_the_agents_output(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / WRITER_TRACE_REL).write_text("{}", encoding="utf-8")
    (tmp_path / "docs" / "notes.md").write_text("x", encoding="utf-8")
    assert worker_mod.agent_file_paths(tmp_path) == frozenset({"docs/notes.md"})


@pytest.mark.parametrize("session_id, shown", [(None, "-"), ("sess_dddd4444ffff", "sess_dddd444")])
def test_an_unwritable_checkout_still_returns_the_summary(
    tmp_path, caplog, session_id, shown
):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("file", encoding="utf-8")
    events = [(2.0, _use("bash", "make")), (5.0, _result())]
    with caplog.at_level(logging.INFO, logger="app.factory.build.writer_trace"):
        ledger = record_writer_trace(blocker, events, 0.0, 9.0, session_id)
    assert ledger == {
        "wall_s": 9.0,
        "actions": 1,
        "model_s": 2.0,
        "tool_s": {"bash: make": 3.0},
        "unsplit_s": 0.0,
        "after_last_action_s": 4.0,
    }
    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("writer trace persistence failed") for m in messages)
    assert any(f"session={shown} " in m for m in messages)


def test_an_action_without_a_result_logs_no_run_time(tmp_path, caplog):
    events = [(1.0, _use("bash", "sleep 9")), (3.0, _use("read")), (4.0, _result())]
    with caplog.at_level(logging.INFO, logger="app.factory.build.writer_trace"):
        record_writer_trace(tmp_path, events, 0.0, 6.0, "sess_eeee5555")
    lines = [
        r.getMessage()
        for r in caplog.records
        if r.getMessage().startswith("codewhale writer action:")
    ]
    assert len(lines) == 2
    # The id-less result closes the earliest open call; the second has none.
    assert " run=3.0s " in lines[0]
    assert " run=- " in lines[1]
