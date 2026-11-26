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
