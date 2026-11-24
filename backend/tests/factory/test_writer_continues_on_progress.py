"""A writer cut at its wall while still working continues; a silent one stops.

Owner, 24 Nov 07:35Z, on a build whose writer was cut at its wall while it was
still reporting steps (about 160 files on disk, rework 0): "it should be able
to continue". The rule, from the run's own facts:

1. A pass cut at its wall that reported steps or grew its files in its last
   minutes is continued: its staging tree is kept and the WRITER runs again
   on it, bounded by a maximum number of passes and the run's hard ceiling,
   both stated on the Floor line ("continuing, pass 2 of 3").
2. A pass cut with no progress stops exactly as before.
3. A resumed pass (a continuation or the user's Try again) is told what is
   already done -- the steps it reported and the files on disk -- and its
   kept files are not rewritten or re-seeded by the Factory.
4. The ``codewhale_worker_failed`` reason has its own plain sentence.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import writer_continuation as wc
from app.factory.build.authority import BuildRole
from app.factory.build.codewhale_worker import (
    FILES_ON_DISK,
    STEPS_REPORTED,
    WorkerError,
    WorkerWallTimeout,
)
from app.factory.build.gates import GateResult
from app.factory.build.ledger import EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.roles_models import RoleError
from app.factory.build.runner import BuildBudget, Outcome, RoleRunner, role_staging_dir

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
BRIEF = '{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}'


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _cut(cls=WorkerWallTimeout):
    """The RoleError the writer handler raises for a worker failure."""
    try:
        try:
            raise cls("worker_timed_out: headless job exceeded its 3585s wall")
        except WorkerError as inner:
            raise RoleError(
                f"codewhale_worker_failed: {inner}",
                reason="codewhale_worker_failed",
                location="WRITER",
            ) from inner
    except RoleError as exc:
        return exc


def _working(ledger, steps):
    """The coding agent's typed progress record, as the writer relay writes it."""
    for n in range(1, steps + 1):
        ledger.append(
            EventKind.NOTE,
            role=BuildRole.WRITER,
            detail=f"writer: STEP {n}: did a thing",
            payload={"source": "codewhale_worker", STEPS_REPORTED: n},
        )


def _runner(tmp_path, monkeypatch, *, cut_steps, cuts=1, ceiling=20000.0, writer_seconds=3600.0):
    """A run whose WRITER is cut at its wall ``cuts`` times, reporting
    ``cut_steps`` steps before each cut, then succeeds."""
    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "1")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setenv("FACTORY_WRITER_MIN_PASS_S", "600")
    clock = _Clock()
    runner = RoleRunner(
        load_blueprint(SMOKE),
        tmp_path / "build",
        budget=BuildBudget(
            max_rework=2,
            wall_clock_s=4000.0,
            phase_wall_clock_s=3600.0,
            hard_ceiling_s=ceiling,
        ),
        clock=clock,
        brief=BRIEF,
    )
    calls = {"writer": 0, "resumed": []}
    staging = role_staging_dir(tmp_path / "build", BuildRole.WRITER)

    def fake_phase(role, work_list):
        if role is BuildRole.WRITER:
            calls["writer"] += 1
            if calls["writer"] <= cuts:
                clock.t += writer_seconds
                (staging / "docs").mkdir(parents=True, exist_ok=True)
                (staging / "docs" / "writer_progress.log").write_text(
                    "".join(f"STEP {n}: did a thing\n" for n in range(1, cut_steps + 1)) or "",
                    encoding="utf-8",
                )
                _working(runner.ledger, cut_steps)
                raise _cut()
            clock.t += 600.0  # the continuation finishes what was left
            return GateResult(ok=True, gate="writer_gate", detail="ok")
        clock.t += 1.0
        return GateResult(ok=True, gate=f"{role.value.lower()}_gate", detail="ok")

    runner._run_phase = fake_phase  # type: ignore[assignment]
    runner._maybe_stage_inspect = lambda *a, **k: None  # type: ignore[assignment]
    return runner, calls


def _decisions(runner):
    return [
        (e.detail, (e.payload or {})[wc.NOTE_KEY])
        for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and wc.NOTE_KEY in (e.payload or {})
    ]


# -- 1. a working writer cut at its wall continues ----------------------------


@pytest.mark.parametrize("steps", [3, 7])
def test_a_writer_cut_while_working_continues_and_the_build_finishes(tmp_path, monkeypatch, steps):
    runner, calls = _runner(tmp_path, monkeypatch, cut_steps=steps)
    outcome = runner.run()

    assert calls["writer"] == 2, "the cut pass must be followed by a continuation pass"
    assert outcome.ok, outcome.detail
    (detail, payload), = _decisions(runner)
    assert payload["go"] is True
    assert payload["pass_next"] == 2 and payload["max_passes"] == wc.DEFAULT_MAX_PASSES
    assert payload["steps_in_window"] == steps
    assert detail.startswith(f"continuing, pass 2 of {wc.DEFAULT_MAX_PASSES}:")
    assert "60 min" in detail and "ceiling" in detail
    assert not [e for e in runner.ledger.events() if e.kind is EventKind.PHASE_ABORTED]


@pytest.mark.parametrize("limit", [2, 3])
def test_the_passes_are_bounded_and_the_last_cut_stops_the_build(tmp_path, monkeypatch, limit):
    monkeypatch.setenv("FACTORY_WRITER_MAX_PASSES", str(limit))
    # Short passes, so the pass limit (not the ceiling) is what binds.
    runner, calls = _runner(tmp_path, monkeypatch, cut_steps=4, cuts=10, writer_seconds=1200.0)
    outcome = runner.run()

    assert calls["writer"] == limit, "a looping writer must still stop"
    assert outcome.outcome is Outcome.FAILED_ROLE_ERROR
    decisions = _decisions(runner)
    assert [p["go"] for _, p in decisions] == [True] * (limit - 1) + [False]
    assert decisions[-1][0] == f"writer not continued: the writer has used all {limit} of its passes."


# -- 2. a writer cut with no progress stops as today --------------------------


@pytest.mark.parametrize("seconds", [3600.0, 1800.0])
def test_a_silent_writer_cut_at_its_wall_stops_as_before(tmp_path, monkeypatch, seconds):
    runner, calls = _runner(tmp_path, monkeypatch, cut_steps=0, writer_seconds=seconds)
    outcome = runner.run()

    assert calls["writer"] == 1
    assert outcome.outcome is Outcome.FAILED_ROLE_ERROR
    (detail, payload), = _decisions(runner)
    assert payload["go"] is False
    assert "no step" in detail


@pytest.mark.parametrize("cls", [WorkerError, RuntimeError])
def test_a_writer_failure_that_is_not_the_wall_is_never_continued(tmp_path, monkeypatch, cls):
    runner, calls = _runner(tmp_path, monkeypatch, cut_steps=5)
    original = runner._run_phase

    def broken(role, work_list):
        if role is BuildRole.WRITER:
            _working(runner.ledger, 5)
            try:
                raise cls("worker_exec_failed: exit 2")
            except Exception as inner:
                raise RoleError("codewhale_worker_failed: x", reason="codewhale_worker_failed") from inner
        return original(role, work_list)

    runner._run_phase = broken  # type: ignore[assignment]
    outcome = runner.run()
    assert outcome.outcome is Outcome.FAILED_ROLE_ERROR
    assert _decisions(runner) == [], "only the wall's typed cut is a continuation candidate"


@pytest.mark.parametrize("ceiling", [4000.0, 4100.0])
def test_no_continuation_when_the_ceiling_leaves_no_room(tmp_path, monkeypatch, ceiling):
    runner, calls = _runner(tmp_path, monkeypatch, cut_steps=5, ceiling=ceiling)
    outcome = runner.run()

    assert calls["writer"] == 1
    assert outcome.outcome is Outcome.FAILED_ROLE_ERROR
    (detail, payload), = _decisions(runner)
    assert payload["go"] is False
    assert "ceiling" in detail
    assert runner._plan_wall_passes == 1, "a refused continuation does not keep the re-armed pass"


@pytest.mark.parametrize("halt", [{"reason": "inspect stop"}, {"reason": "stage over"}])
def test_a_stage_the_budget_check_stopped_is_not_continued(tmp_path, monkeypatch, halt):
    runner, calls = _runner(tmp_path, monkeypatch, cut_steps=5)
    original = runner._run_phase

    def halted(role, work_list):
        if role is BuildRole.WRITER:
            runner._stage_halt = halt
        return original(role, work_list)

    runner._run_phase = halted  # type: ignore[assignment]
    runner.run()
    assert calls["writer"] == 1
    (detail, payload), = _decisions(runner)
    assert payload["go"] is False and "budget check" in detail


@pytest.mark.parametrize("how", ["removed", "log_emptied"])
def test_no_continuation_when_the_cut_pass_files_are_gone(tmp_path, monkeypatch, how):
    runner, calls = _runner(tmp_path, monkeypatch, cut_steps=5)
    original = runner._run_phase
    staging = role_staging_dir(tmp_path / "build", BuildRole.WRITER)

    def wiped(role, work_list):
        try:
            return original(role, work_list)
        except RoleError:
            import shutil

            if how == "removed":
                shutil.rmtree(staging, ignore_errors=True)
            else:
                (staging / "docs" / "writer_progress.log").write_text("", encoding="utf-8")
            raise

    runner._run_phase = wiped  # type: ignore[assignment]
    runner.run()
    assert calls["writer"] == 1
    (detail, payload), = _decisions(runner)
    assert payload["go"] is False and "no longer on disk" in detail


# -- the decision and its inputs ----------------------------------------------


def _event(ts, **payload):
    return SimpleNamespace(ts=ts.isoformat(), payload={"source": "codewhale_worker", **payload})


@pytest.mark.parametrize("minutes_ago, counted", [(5, 1), (15, 0)])
def test_progress_is_read_only_inside_the_window_before_the_cut(minutes_ago, counted):
    now = datetime(2026, 11, 24, 7, 24, tzinfo=timezone.utc)
    events = [_event(now - timedelta(minutes=minutes_ago), **{STEPS_REPORTED: 7})]
    assert wc.progress_at_cut(events, now=now, window_s=600)["steps"] == counted


@pytest.mark.parametrize("before, after, grown", [(100, 160, 60), (160, 150, 0)])
def test_files_grown_compares_the_window_with_the_count_before_it(before, after, grown):
    now = datetime(2026, 11, 24, 7, 24, tzinfo=timezone.utc)
    events = [
        _event(now - timedelta(minutes=30), **{FILES_ON_DISK: before, STEPS_REPORTED: 3}),
        _event(now - timedelta(minutes=2), **{FILES_ON_DISK: after, STEPS_REPORTED: 3}),
        _event(now + timedelta(minutes=1), **{FILES_ON_DISK: 999}),
    ]
    progress = wc.progress_at_cut(events, now=now, window_s=600)
    assert progress["files_grown"] == grown
    assert progress["steps"] == 0, "a heartbeat (it carries a file count) is not a STEP"


@pytest.mark.parametrize("source", ["factory", "tester"])
def test_only_the_coding_agent_record_is_evidence(source):
    now = datetime(2026, 11, 24, 7, 24, tzinfo=timezone.utc)
    event = SimpleNamespace(ts=now.isoformat(), payload={"source": source, STEPS_REPORTED: 2})
    bad = SimpleNamespace(ts="not a time", payload={"source": "codewhale_worker", STEPS_REPORTED: 2})
    assert wc.progress_at_cut([event, bad], now=now, window_s=600) == {"steps": 0, "files_grown": 0}


@pytest.mark.parametrize(
    "passes, progress, kept, go",
    [
        (1, {"steps": 2, "files_grown": 0}, True, True),
        (1, {"steps": 0, "files_grown": 5}, True, True),
        (1, {"steps": 0, "files_grown": 0}, True, False),
        (3, {"steps": 2, "files_grown": 0}, True, False),
        (1, {"steps": 2, "files_grown": 0}, False, False),
    ],
)
def test_the_decision(passes, progress, kept, go):
    decision = wc.decide(passes_done=passes, max_passes=3, progress=progress, staging_kept=kept)
    assert decision.go is go
    assert decision.pass_next == passes + 1
    assert decision.reason.startswith("the writer")


@pytest.mark.parametrize("raw, expected", [("1", 1), ("0", 1), ("5", 5), ("x", wc.DEFAULT_MAX_PASSES)])
def test_the_pass_limit_reads_the_operator_setting(monkeypatch, raw, expected):
    monkeypatch.setenv("FACTORY_WRITER_MAX_PASSES", raw)
    assert wc.max_writer_passes() == expected


@pytest.mark.parametrize("raw, expected", [("30", 60.0), ("900", 900.0)])
def test_the_window_and_minimum_pass_have_a_floor(monkeypatch, raw, expected):
    monkeypatch.setenv("FACTORY_WRITER_PROGRESS_WINDOW_S", raw)
    monkeypatch.setenv("FACTORY_WRITER_MIN_PASS_S", raw)
    assert wc.progress_window_s() == expected
    assert wc.min_pass_s() == expected


@pytest.mark.parametrize("cls, cut", [(WorkerWallTimeout, True), (WorkerError, False)])
def test_the_cut_is_read_by_type_not_by_words(cls, cut):
    assert wc.is_wall_cut(_cut(cls)) is cut
    assert wc.is_wall_cut(None) is False


# -- 3. the resumed pass is told what is done and its files stay --------------


def _staged(tmp_path, files):
    out = tmp_path / "build"
    out.mkdir()
    staging = role_staging_dir(out, BuildRole.WRITER)
    for rel, text in files.items():
        path = staging / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    return out, staging


class _Stop(BaseException):
    pass


@pytest.mark.parametrize("log", ["STEP 1: models\nSTEP 2: routes\n", "STEP 1: models\nnoise\nSTEP 2: routes\n"])
def test_a_resumed_pass_keeps_its_files_byte_for_byte(tmp_path, monkeypatch, log):
    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "1")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    files = {"app/models.py": "# green model\n", "docs/writer_progress.log": log}
    out, staging = _staged(tmp_path, files)
    seen = {}

    def looking_writer(ctx):
        seen["resumed"] = ctx.state.get(wc.RESUMED_KEY)
        seen["model"] = (staging / "app/models.py").read_bytes()
        raise _Stop()

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = looking_writer
    runner = RoleRunner(load_blueprint(SMOKE), out, roles=roles, brief=BRIEF)
    with pytest.raises(_Stop):
        runner._run_phase(BuildRole.WRITER, [])
    assert seen["resumed"] is True
    assert seen["model"] == b"# green model\n", "the kept work must not be rewritten"
    assert wc.done_steps(staging) == ("STEP 1: models", "STEP 2: routes")


@pytest.mark.parametrize("leftover", [None, {"app/models.py": "# torn\n"}])
def test_a_fresh_pass_is_not_marked_resumed(tmp_path, monkeypatch, leftover):
    monkeypatch.setenv("FACTORY_CODEWHALE_WRITER", "1")
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    out, _staging = _staged(tmp_path, leftover or {})
    seen = {}

    def looking_writer(ctx):
        seen["resumed"] = ctx.state.get(wc.RESUMED_KEY)
        raise _Stop()

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = looking_writer
    runner = RoleRunner(load_blueprint(SMOKE), out, roles=roles, brief=BRIEF)
    runner.state[wc.RESUMED_KEY] = True  # a stale mark from an earlier pass
    with pytest.raises(_Stop):
        runner._run_phase(BuildRole.WRITER, [])
    assert seen["resumed"] is False


@pytest.mark.parametrize("resumed", [True, False])
def test_the_resumed_writer_prompt_lists_the_done_steps_and_files(tmp_path, monkeypatch, resumed):
    from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
    from tests.factory.test_codewhale_writer_dispatch import (
        _ctx,
        _plant_authored_handler,
        _receipt,
    )

    ctx = _ctx(tmp_path)
    root = tmp_path / "build"
    _plant_authored_handler(root)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "writer_progress.log").write_text(
        "STEP 1: models\nSTEP 2: routes\nSTEP 2: routes, tests green\n", encoding="utf-8"
    )
    ctx.state[wc.RESUMED_KEY] = resumed
    ctx.state["writer_resumed"] = resumed
    ctx.state[wc.STATE_KEY] = {"pass_running": 2, "max_passes": 3}
    before = (root / "app/actions/cap.py").read_bytes()
    prompts = []

    def fake_run(prompt, dest, **_kw):
        prompts.append(prompt)
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    _run_writer_via_codewhale_worker(ctx)

    prompt = prompts[0]
    assert ("ALREADY DONE" in prompt) is resumed
    if resumed:
        assert "This is pass 2 of 3." in prompt
        assert "STEP 1: models" in prompt
        assert "STEP 2: routes, tests green" in prompt
        assert "  STEP 2: routes\n" not in prompt, "a re-reported step is listed once, latest line"
        assert "app/actions/cap.py" in prompt
        assert prompt.index("ALREADY DONE") < prompt.index("Everything below is the original brief") + 400
    assert (root / "app/actions/cap.py").read_bytes() == before


@pytest.mark.parametrize("count", [3, wc.FILES_LISTED + 5])
def test_the_done_section_lists_files_and_summarises_the_rest(count):
    files = [f"app/f{i:04d}.py" for i in range(count)]
    text = wc.render_done_section(["STEP 1: a"], files, pass_label="pass 2 of 3")
    assert text.startswith("ALREADY DONE")
    assert f"Files on disk ({count}):" in text
    assert ("more" in text) is (count > wc.FILES_LISTED)
    assert wc.render_done_section([], []) == ""


@pytest.mark.parametrize("record, label", [({"pass_running": 2, "max_passes": 3}, "pass 2 of 3"), ({"pass_running": 1, "max_passes": 3}, ""), ({}, "")])
def test_the_pass_label(record, label):
    assert wc.pass_label({wc.STATE_KEY: record}) == label


# -- 4. the plain sentence ----------------------------------------------------


@pytest.mark.parametrize("reason", ["codewhale_worker_failed", "codewhale_worker_failed: worker_timed_out: x"])
def test_the_worker_failure_has_its_own_sentence(reason):
    from app.factory.build_jobs import _floor_failure

    row = _floor_failure({"reason": reason, "phase": "WRITER", "location": "WRITER"})
    assert row["summary"] == "The coding agent's session ended before it finished this step."


# -- GROKGRADE F297 199e524: artefacts, the progress window, tests, the card --


_SQLITE = b"SQLite format 3\x00" + b"\x00" * 100


def _tree(root, artefacts):
    """Two source files plus the artefacts a test or build run leaves."""
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "app" / "main.py").write_text("x = 1\n", encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "test_main.py").write_text("def test_a():\n    pass\n", encoding="utf-8")
    for rel, body in artefacts.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)


@pytest.mark.parametrize(
    "artefacts",
    [
        {
            ".pytest_cache/.gitignore": b"# Created by pytest automatically.\n*\n",
            ".pytest_cache/v/cache/nodeids": b"[]",
            "htmlcov/.gitignore": b"# Created by coverage.py\n*\n",
            "htmlcov/index.html": b"<html></html>",
            ".coverage": _SQLITE,
            "test.db": _SQLITE,
        },
        {
            ".gitignore": b"dist/\n# generated\n/reports\n",
            "dist/assets/index.js": b"x",
            "dist/index.html": b"x",
            "reports/run.xml": b"<x/>",
            "app/store.sqlite3": _SQLITE,
            ".mypy_cache/.gitignore": b"*\n",
            ".mypy_cache/3.11/a.json": b"{}",
        },
    ],
)
def test_the_authored_count_skips_test_and_build_artefacts_by_rule(tmp_path, artefacts):
    from app.factory.build.codewhale_worker import (
        agent_file_paths,
        authored_files_or_none,
        count_authored_files,
    )

    _tree(tmp_path, {})
    before = count_authored_files(tmp_path)
    _tree(tmp_path, artefacts)
    source = {"app/main.py", "tests/test_main.py"} | (
        {".gitignore"} if ".gitignore" in artefacts else set()
    )
    assert set(agent_file_paths(tmp_path)) == source
    assert count_authored_files(tmp_path) == authored_files_or_none(tmp_path) == len(source)
    assert count_authored_files(tmp_path) >= before, "artefacts never move the count down"
    assert set(wc.done_files(tmp_path)) == source, "a resumed pass is never told to keep artefacts"


def _at(now, minutes, detail="", **payload):
    return SimpleNamespace(
        ts=(now - timedelta(minutes=minutes)).isoformat(),
        detail=detail,
        payload={"source": "codewhale_worker", **payload},
    )


@pytest.mark.parametrize(
    "samples, grown",
    [
        # dropped before the window, then crept back up inside it: below the peak
        ([(30, 165), (12, 55), (5, 55), (1, 62)], 0),
        # fell and came back inside the window, still below the earlier peak
        ([(8, 165), (5, 55), (1, 160)], 0),
        # one sample and nothing before it to compare with
        ([(2, 55)], 0),
        # a real rise above everything seen before
        ([(30, 100), (5, 120), (1, 140)], 40),
    ],
)
def test_files_grown_is_a_rise_above_the_pass_peak(samples, grown):
    now = datetime(2026, 11, 24, 7, 24, tzinfo=timezone.utc)
    events = [_at(now, m, **{FILES_ON_DISK: n, STEPS_REPORTED: 1}) for m, n in samples]
    assert wc.progress_at_cut(events, now=now, window_s=600)["files_grown"] == grown


@pytest.mark.parametrize("earlier_pass_files, grown", [(300, 20), (10, 20)])
def test_each_writer_pass_is_compared_with_its_own_counts(earlier_pass_files, grown):
    now = datetime(2026, 11, 24, 7, 24, tzinfo=timezone.utc)
    start = SimpleNamespace(
        ts=(now - timedelta(minutes=40)).isoformat(),
        kind=EventKind.PHASE_STARTED,
        role=BuildRole.WRITER,
        detail="",
        payload={},
    )
    events = [
        _at(now, 90, **{FILES_ON_DISK: earlier_pass_files, STEPS_REPORTED: 1}),
        start,
        _at(now, 30, **{FILES_ON_DISK: 5, STEPS_REPORTED: 1}),
        _at(now, 1, **{FILES_ON_DISK: 25, STEPS_REPORTED: 1}),
    ]
    assert wc.progress_at_cut(events, now=now, window_s=600)["files_grown"] == grown


@pytest.mark.parametrize(
    "lines, counted",
    [
        # the same step said again three times in the window: not new work
        (
            [
                (30, "writer: STEP 4: wiring"),
                (5, "writer: STEP 4: wiring"),
                (3, "writer: STEP 4: x"),
                (1, "writer: STEP 4: y"),
            ],
            0,
        ),
        # three times inside the window only: one step
        ([(5, "writer: STEP 4: a"), (3, "writer: STEP 4: a"), (1, "writer: STEP 4: a")], 1),
        # two new steps
        ([(30, "writer: STEP 4: a"), (5, "writer: STEP 5: b"), (1, "writer: STEP 6: c")], 2),
    ],
)
def test_a_step_counts_once_and_only_when_first_reported(lines, counted):
    now = datetime(2026, 11, 24, 7, 24, tzinfo=timezone.utc)
    events = [
        _at(now, m, detail, **{STEPS_REPORTED: i + 1}) for i, (m, detail) in enumerate(lines)
    ]
    assert wc.progress_at_cut(events, now=now, window_s=600)["steps"] == counted


def _pytest_cache(root, nodeids, lastfailed):
    import json

    cache = root / ".pytest_cache" / "v" / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (root / ".pytest_cache" / ".gitignore").write_text("*\n", encoding="utf-8")
    (cache / "nodeids").write_text(json.dumps(nodeids), encoding="utf-8")
    if lastfailed is not None:
        (cache / "lastfailed").write_text(json.dumps(lastfailed), encoding="utf-8")


@pytest.mark.parametrize(
    "nodeids, lastfailed, passing",
    [
        (
            [
                "tests/test_main.py::test_a",
                "tests/test_main.py::test_b",
                "tests/test_gone.py::test_c",
            ],
            {"tests/test_main.py::test_b": True},
            ("tests/test_main.py::test_a",),
        ),
        (["tests/test_main.py::test_a"], None, ("tests/test_main.py::test_a",)),
    ],
)
def test_a_resumed_pass_is_told_the_tests_that_pass(tmp_path, nodeids, lastfailed, passing):
    _tree(tmp_path, {})
    _pytest_cache(tmp_path, nodeids, lastfailed)
    assert wc.done_tests(tmp_path) == passing
    text = wc.render_done_section(["STEP 1: a"], ["app/main.py"], tests=wc.done_tests(tmp_path))
    assert f"Tests that passed on their last run ({len(passing)})" in text
    for test in passing:
        assert f"  {test}" in text
    assert "test_b" not in text and "test_gone" not in text


@pytest.mark.parametrize("cut_steps, continuing", [(4, True), (0, False)])
def test_the_build_status_carries_the_pass_for_the_card(tmp_path, monkeypatch, cut_steps, continuing):
    from app.factory.build_jobs import build_status

    runner, _calls = _runner(tmp_path, monkeypatch, cut_steps=cut_steps)
    runner.run()
    view = build_status(tmp_path / "build")["writer_pass"]
    assert view["continuing"] is continuing
    if continuing:
        assert (view["pass"], view["max_passes"]) == (2, wc.DEFAULT_MAX_PASSES)
        assert view["writer_minutes"] == 60 and view["ceiling_minutes"] == round(20000.0 / 60)
        assert view["text"].startswith("continuing, pass 2 of")
    else:
        assert view["text"].startswith("writer not continued")


@pytest.mark.parametrize("restart, continuing", [(False, True), (True, None)])
def test_the_card_view_is_this_runs_newest_decision(restart, continuing):
    note = SimpleNamespace(
        kind=EventKind.NOTE,
        ts="2026-11-24T07:24:00+00:00",
        detail="continuing, pass 2 of 3: x",
        payload={
            wc.NOTE_KEY: {
                "go": True,
                "pass_next": 2,
                "max_passes": 3,
                "writer_seconds": 3600,
                "ceiling_s": 7200,
            }
        },
    )
    rerun = SimpleNamespace(kind=EventKind.RUN_STARTED, ts="", detail="", payload={})
    view = wc.floor_view([note, rerun] if restart else [note])
    if continuing is None:
        assert view is None
    else:
        assert view["continuing"] is True
        assert (view["pass"], view["max_passes"], view["ceiling_minutes"]) == (2, 3, 120)
