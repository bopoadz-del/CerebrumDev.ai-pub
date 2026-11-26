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


# -- 0. the run's writer time counts every pass -------------------------------


@pytest.mark.parametrize("passes", [[3600.0], [1200.0, 900.5, 30.0]])
def test_every_writer_pass_adds_its_time_to_the_run_total(tmp_path, monkeypatch, passes):
    runner, _ = _runner(tmp_path, monkeypatch, cut_steps=1)
    record = None
    for seconds in passes:
        started = runner.clock()
        runner.clock.t += seconds
        record = runner._count_writer_time(started)
    assert record is runner.state[wc.STATE_KEY], "the total lives on the run, not in a copy"
    assert record["seconds"] == pytest.approx(sum(passes))
    assert record["passes"] == 0, "counting time never grants a pass"


@pytest.mark.parametrize("ahead", [0.0, 50.0])
def test_a_pass_never_takes_time_off_the_total(tmp_path, monkeypatch, ahead):
    runner, _ = _runner(tmp_path, monkeypatch, cut_steps=1)
    runner.state[wc.STATE_KEY] = {"passes": 1, "seconds": 120.0}
    record = runner._count_writer_time(runner.clock() + ahead)
    assert record["seconds"] == pytest.approx(120.0)
    assert record["passes"] == 1


@pytest.mark.parametrize("cuts, total", [(0, 600.0), (1, 4200.0)])
def test_a_finished_build_records_the_writer_time_of_every_pass(tmp_path, monkeypatch, cuts, total):
    runner, _ = _runner(tmp_path, monkeypatch, cut_steps=3, cuts=cuts)
    outcome = runner.run()
    assert outcome.ok, outcome.detail
    assert runner.state[wc.STATE_KEY]["seconds"] == pytest.approx(total)


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


@pytest.mark.parametrize("minutes_ago, counted", [(5, 1), (10, 1), (15, 0)])
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


@pytest.mark.parametrize("raw", ["inf", "nan", "-inf"])
def test_a_non_finite_operator_setting_reads_as_the_default(monkeypatch, raw):
    """'inf' parses as a float; a pass limit or window of infinity would
    raise inside the run loop's error handler, so it reads as unset."""
    for name in ("FACTORY_WRITER_MAX_PASSES", "FACTORY_WRITER_PROGRESS_WINDOW_S", "FACTORY_WRITER_MIN_PASS_S"):
        monkeypatch.setenv(name, raw)
    assert wc.max_writer_passes() == wc.DEFAULT_MAX_PASSES
    assert wc.progress_window_s() == wc.DEFAULT_PROGRESS_WINDOW_S
    assert wc.min_pass_s() == wc.DEFAULT_MIN_PASS_S
    now = datetime(2026, 11, 24, 7, 24, tzinfo=timezone.utc)
    assert wc.progress_at_cut([], now=now, window_s=wc.progress_window_s()) == {"steps": 0, "files_grown": 0}


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


@pytest.mark.parametrize(
    "log, steps",
    [
        ("STEP 1: models\nSTEP 2: routes\n", ("STEP 1: models", "STEP 2: routes")),
        (
            "STEP 1: models\n\nSTEP 1: models\nchecked the tests\nSTEP 2: routes\n",
            ("STEP 1: models", "checked the tests", "STEP 2: routes"),
        ),
    ],
)
def test_a_resumed_pass_keeps_its_files_byte_for_byte(tmp_path, monkeypatch, log, steps):
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
    assert wc.done_steps(staging) == steps


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
        "STEP 1: models\nSTEP 2: routes\nSTEP 2: routes\nSTEP 2: routes, tests green\n",
        encoding="utf-8",
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
        assert prompt.count("  STEP 2: routes\n") == 1, "an exact repeat is listed once"
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
    # The merge-back's view is wider on purpose: it carries every file
    # the agent wrote, artefacts included (only vendor/VCS/bytecode out).
    assert set(agent_file_paths(tmp_path)) == source | set(artefacts)
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
    "line, name",
    [
        # git reads a line starting with '#' as a comment, never a pattern
        ("#keep.py", "#keep.py"),
        # a negation re-includes; it never hides a file, even one named '!...'
        ("!keep.py", "!keep.py"),
    ],
)
def test_gitignore_comments_and_negations_never_hide_source(tmp_path, line, name):
    from app.factory.build.codewhale_worker import iter_authored_source

    _tree(tmp_path, {".gitignore": (line + "\n").encode(), "app/" + name: b"x = 2\n"})
    assert set(iter_authored_source(tmp_path)) == {
        ".gitignore",
        "app/main.py",
        "tests/test_main.py",
        "app/" + name,
    }


@pytest.mark.parametrize(
    "gitignore, kept, hidden",
    [
        # a directory's contents excluded, one file re-included
        ("config/*\n!config/settings.py\n", "config/settings.py", "config/cache.py"),
        # a pattern excluded, one name re-included
        ("*.json\n!package.json\n", "package.json", "data.json"),
        # the last matching rule decides: re-excluded after the negation
        ("*.json\n!package.json\npackage.json\n", "app/main.py", "package.json"),
    ],
)
def test_a_gitignore_negation_re_includes_source(tmp_path, gitignore, kept, hidden):
    from app.factory.build.codewhale_worker import iter_authored_source

    _tree(tmp_path, {".gitignore": gitignore.encode(), kept: b"x = 1\n", hidden: b"x = 2\n"})
    authored = set(iter_authored_source(tmp_path))
    assert kept in authored
    assert hidden not in authored


@pytest.mark.parametrize("line, name", [("\\!secret.py", "!secret.py"), ("\\#notes.py", "#notes.py")])
def test_an_escaped_first_character_is_a_literal_name(tmp_path, line, name):
    from app.factory.build.codewhale_worker import iter_authored_source

    _tree(tmp_path, {".gitignore": (line + "\n").encode(), "app/" + name: b"x = 2\n"})
    assert "app/" + name not in set(iter_authored_source(tmp_path))


@pytest.mark.parametrize(
    "rule, source_file, artefact",
    [
        ("build/", "tools/build", "build/x.js"),
        ("out/", "src/out", "web/out/a.js"),
    ],
)
def test_a_directory_rule_hides_directories_never_a_file(tmp_path, rule, source_file, artefact):
    from app.factory.build.codewhale_worker import iter_authored_source

    _tree(tmp_path, {".gitignore": (rule + "\n").encode(), source_file: b"run\n", artefact: b"x"})
    authored = set(iter_authored_source(tmp_path))
    assert source_file in authored, "a rule ending in '/' matches directories only"
    assert artefact not in authored


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
        # fell, then rose above the earlier peak: only the rise above it is
        # growth (the count is authored source only, so these are new files)
        ([(30, 165), (5, 55), (1, 170)], 5),
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
        # the same report said again three times in the window: not new work
        (
            [
                (30, "STEP 4: wiring"),
                (5, "STEP 4: wiring"),
                (3, "STEP 4:  wiring"),
                (1, "STEP 4: wiring"),
            ],
            0,
        ),
        # three times inside the window only: one report
        ([(5, "STEP 4: a"), (3, "STEP 4: a"), (1, "STEP 4: a")], 1),
        # two new reports
        ([(30, "STEP 4: a"), (5, "STEP 5: b"), (1, "STEP 6: c")], 2),
    ],
)
def test_a_step_counts_once_and_only_when_first_reported(lines, counted):
    now = datetime(2026, 11, 24, 7, 24, tzinfo=timezone.utc)
    events = [
        _at(now, m, "writer: " + line, **{STEPS_REPORTED: i + 1})
        for i, (m, line) in enumerate(lines)
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


def _note(go):
    return SimpleNamespace(
        kind=EventKind.NOTE,
        role=BuildRole.WRITER,
        ts="2026-11-24T07:24:00+00:00",
        detail="continuing, pass 2 of 3: x" if go else "writer not continued: x",
        payload={wc.NOTE_KEY: {"go": go, "pass_next": 2, "max_passes": 3, "writer_seconds": 3600, "ceiling_s": 7200}},
    )


def _ev(kind, role):
    return SimpleNamespace(kind=kind, role=role, ts="", detail="", payload={})


@pytest.mark.parametrize(
    "after, continuing",
    [
        # the continued pass has started and is running: still continuing
        ([(EventKind.PHASE_STARTED, BuildRole.WRITER)], True),
        # its gate gave a verdict: the pass is over
        ([(EventKind.PHASE_STARTED, BuildRole.WRITER), (EventKind.GATE_PASSED, BuildRole.WRITER)], None),
        ([(EventKind.PHASE_STARTED, BuildRole.WRITER), (EventKind.GATE_FAILED, BuildRole.WRITER)], None),
        # the next step started: the pass is over
        ([(EventKind.PHASE_STARTED, BuildRole.TESTER)], None),
    ],
)
def test_continuing_ends_when_the_continued_pass_ends(after, continuing):
    view = wc.floor_view([_note(True)] + [_ev(k, r) for k, r in after])
    if continuing is None:
        assert view is None, "the card would say 'continuing' after the pass ended"
    else:
        assert view["continuing"] is True


@pytest.mark.parametrize("kind", [EventKind.GATE_FAILED, EventKind.PHASE_STARTED])
def test_a_stop_decision_is_kept_after_later_events(kind):
    view = wc.floor_view([_note(False), _ev(kind, BuildRole.TESTER)])
    assert view is not None and view["continuing"] is False


@pytest.mark.parametrize("timeout_s", [0.3, 0.4])
def test_the_real_worker_wall_raises_the_typed_cut(tmp_path, timeout_s):
    """run_worker_job's own wall raises WorkerWallTimeout, the type the
    continuation rule reads; a plain WorkerError would never continue."""
    import time

    from tests.factory.test_worker_wall_reads_live_deadline import _run, _SlowProc

    box = {"at": time.monotonic() + timeout_s}
    with pytest.raises(WorkerWallTimeout) as caught:
        _run(tmp_path, _SlowProc(5.0), timeout_s=timeout_s, live_time_left=lambda: box["at"] - time.monotonic())
    assert wc.is_wall_cut(caught.value)


# -- OPUSGRADE F297 3d12544 answers ---------------------------------------------


def _git_untracked(root, scratch):
    """What real git keeps from ``root``: untracked files, not ignored, with
    no global or system config (an empty excludes file)."""
    import os
    import subprocess

    empty = scratch / "empty_excludes"
    empty.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=str(empty))
    subprocess.run(["git", "init", "-q", str(root)], check=True, env=env)
    out = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            f"core.excludesFile={empty}",
            "ls-files",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        env=env,
        capture_output=True,
    ).stdout
    return {p for p in out.decode("utf-8").split("\0") if p}


@pytest.mark.parametrize(
    "files, kept, hidden",
    [
        (
            {
                ".gitignore": "app/*.json\n**/generated\n/build\n*.log\n",
                "app/data/seed.json": "{}",
                "app/x.json": "{}",
                "generated/i.py": "x",
                "lib/generated/j.py": "x",
                "build/a.py": "x",
                "src/build/b.py": "x",
                "run.log": "x",
                "app/deep/run.log": "x",
                "app/routes.py": "x",
            },
            {"app/data/seed.json", "src/build/b.py", "app/routes.py"},
            {"app/x.json", "generated/i.py", "lib/generated/j.py", "run.log", "app/deep/run.log"},
        ),
        (
            {
                ".gitignore": "docs/**\n!docs/keep.md\na/**/b.py\n[ab]c?.txt\n[!x]y.py\n",
                "docs/x.md": "x",
                "docs/keep.md": "x",
                "a/b.py": "x",
                "a/x/y/b.py": "x",
                "ac1.txt": "x",
                "bc22.txt": "x",
                "dc1.txt": "x",
                "ay.py": "x",
                "xy.py": "x",
                "sub/.gitignore": "*.tmp\n/only_here.py\n",
                "sub/a.tmp": "x",
                "sub/deep/b.tmp": "x",
                "sub/only_here.py": "x",
                "sub/deep/only_here.py": "x",
                "a.tmp": "x",
            },
            {"docs/keep.md", "bc22.txt", "dc1.txt", "xy.py", "sub/deep/only_here.py", "a.tmp"},
            {"docs/x.md", "a/b.py", "a/x/y/b.py", "ac1.txt", "ay.py", "sub/a.tmp", "sub/deep/b.tmp", "sub/only_here.py"},
        ),
    ],
)
def test_gitignore_is_read_the_way_git_reads_it(tmp_path, files, kept, hidden):
    """Differential against real git (grader probe ``gi``): ``*`` never
    crosses ``/`` and ``**/`` matches at any depth."""
    from app.factory.build.builds_push import is_exported
    from app.factory.build.codewhale_worker import iter_authored_source

    root = tmp_path / "tree"
    for rel, body in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body, encoding="utf-8")
    authored = set(iter_authored_source(root))
    assert kept <= authored
    assert not (hidden & authored)
    git_kept = {p for p in _git_untracked(root, tmp_path) if is_exported(Path(p))}
    assert authored == git_kept


@pytest.mark.parametrize(
    "gitignore, kept, hidden",
    [
        # leading spaces belong to the pattern; trailing ones do not
        ("  lead.txt\n foo.txt   \n", {"lead.txt", "foo.txt"}, {" foo.txt"}),
        # a backslash escapes any character, not only a first # or !
        ("\\[x].txt\n\\#hash.txt\n", {"x.txt", "hash.txt"}, {"[x].txt", "#hash.txt"}),
        # a ] first in a (negated) class is literal
        ("[!]a]z.txt\n[]]q.txt\n", {"az.txt", "]z.txt", "aq.txt"}, {"bz.txt", "]q.txt"}),
        # an unclosed class and a trailing lone backslash match nothing
        ("un[closed.txt\nlone\\\n", {"un[closed.txt", "unxclosed.txt", "lone"}, set()),
        # ? and a negated class never match / in an anchored pattern
        ("d/a?b.txt\nd/c[!x]e.txt\n", {"d/a/b.txt", "d/c/e.txt", "d/cxe.txt"}, {"d/aXb.txt", "d/cye.txt"}),
        # a trailing /** takes everything inside and nothing beside it
        ("m/**\n", {"mm.py"}, {"m/1/2/3.py", "m/top.py"}),
        # a POSIX class names its characters; a reversed range matches nothing
        ("[[:digit:]]*.py\n[z-a].py\n", {"a1.py", "b.py"}, {"1a.py", "9.py"}),
    ],
)
def test_gitignore_edge_patterns_match_git(tmp_path, gitignore, kept, hidden):
    """Differential against real git on the lines the grader's probe
    ``gi_diff2`` found read differently: whitespace, escapes, ``]`` first in
    a class, an unclosed class, ``?``/``[!x]`` facing ``/`` and a trailing
    ``/**``."""
    from app.factory.build.builds_push import is_exported
    from app.factory.build.codewhale_worker import iter_authored_source

    root = tmp_path / "tree"
    root.mkdir()
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    for rel in kept | hidden:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x", encoding="utf-8")
    authored = set(iter_authored_source(root))
    assert kept <= authored
    assert not (hidden & authored)
    git_kept = {p for p in _git_untracked(root, tmp_path) if is_exported(Path(p))}
    assert authored == git_kept


@pytest.mark.parametrize("gitignore, extra",[("app/*.json\n", "app/data/seed.json"), ("*.json\n", "app/seed.json")])
def test_the_specialist_merge_back_carries_files_the_product_ignores(tmp_path, gitignore, extra):
    """The merge-back is not narrowed by the product's .gitignore (grader
    probe ``merge_probe``): every file a specialist wrote reaches the tree."""
    from app.factory.build import specialist_fanout as sf

    dest, tree = tmp_path / "dest", tmp_path / "api"
    for root in (dest, tree):
        (root / "app").mkdir(parents=True)
        (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    baseline = sf.tree_manifest(dest)
    (tree / extra).parent.mkdir(parents=True, exist_ok=True)
    (tree / extra).write_text("{}", encoding="utf-8")
    (tree / "app" / "routes.py").write_text("r = 1\n", encoding="utf-8")
    out = sf.merge_back(dest, baseline, {"API": tree}, owned=(), writer="WRITER")
    assert sorted(out["applied"]["API"]) == sorted([extra, "app/routes.py"])
    assert (dest / extra).read_text(encoding="utf-8") == "{}"


@pytest.mark.parametrize(
    "nodeids, lastfailed, failing",
    [
        # a -x run: test_1 failed, test_2 and test_3 never ran
        (
            ["tests/test_main.py::test_1", "tests/test_main.py::test_2", "tests/test_main.py::test_3"],
            {"tests/test_main.py::test_1": True},
            ("tests/test_main.py::test_1",),
        ),
        # skips and xfails are never in lastfailed; nothing is named at all
        (["tests/test_main.py::test_ok", "tests/test_main.py::test_skipped"], {}, ()),
        # a collection error is named as its file; a gone file is dropped
        (
            ["tests/test_main.py::test_a"],
            {"tests/test_main.py": True, "tests/test_gone.py::test_c": True},
            ("tests/test_main.py",),
        ),
    ],
)
def test_a_resumed_pass_is_told_only_the_tests_that_failed(tmp_path, nodeids, lastfailed, failing):
    _tree(tmp_path, {})
    _pytest_cache(tmp_path, nodeids, lastfailed)
    assert wc.failing_tests(tmp_path) == failing
    text = wc.render_done_section(["STEP 1: a"], ["app/main.py"], failing=wc.failing_tests(tmp_path))
    assert "passed" not in text, "no test is ever named as passing"
    assert (f"Tests that failed on their last run ({len(failing)}); fix these first:" in text) is bool(failing)
    for test in failing:
        assert f"  {test}\n" in text
    for node in nodeids:
        if node not in failing:
            assert node not in text


@pytest.mark.parametrize(
    "body, args, expected",
    [
        (
            "def test_1():\n    assert 0\n\n\ndef test_2():\n    pass\n\n\ndef test_3():\n    pass\n",
            ["-x"],
            ("test_a.py::test_1",),
        ),
        (
            "import pytest\n\n\ndef test_ok():\n    pass\n\n\n@pytest.mark.skip\ndef test_skipped():\n"
            "    pass\n\n\n@pytest.mark.xfail\ndef test_xfail():\n    assert 0\n",
            [],
            (),
        ),
    ],
)
def test_failing_tests_reads_a_real_pytest_record(tmp_path, body, args, expected):
    """Real pytest (grader probes ``dt/x`` and ``dt/s``): a test a ``-x``
    run never reached, a skip and an xfail are never named."""
    import os
    import subprocess
    import sys

    (tmp_path / "test_a.py").write_text(body, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTEST_")}
    subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args, "test_a.py"],
        cwd=tmp_path,
        capture_output=True,
        timeout=180,
        env=env,
    )
    assert (tmp_path / ".pytest_cache").is_dir()
    assert wc.failing_tests(tmp_path) == expected
    text = wc.render_done_section(["STEP 1: a"], ["test_a.py"], failing=wc.failing_tests(tmp_path))
    for name in ("test_2", "test_3", "test_ok", "test_skipped", "test_xfail"):
        assert f"::{name}" not in text


@pytest.mark.parametrize("skipped_dir", ["vendor/blk", "node_modules/pkg"])
def test_a_vendored_test_record_is_not_the_products(tmp_path, skipped_dir):
    _tree(tmp_path, {f"{skipped_dir}/tests/test_v.py": b"def test_v():\n    assert 0\n"})
    _pytest_cache(tmp_path / skipped_dir, ["tests/test_v.py::test_v"], {"tests/test_v.py::test_v": True})
    _pytest_cache(tmp_path, ["tests/test_main.py::test_a"], {"tests/test_main.py::test_a": True})
    assert wc.failing_tests(tmp_path) == ("tests/test_main.py::test_a",)


@pytest.mark.parametrize("record", [[1, 2], "tests/test_main.py::test_a"])
def test_a_lastfailed_record_that_is_not_a_mapping_names_nothing(tmp_path, record):
    _tree(tmp_path, {})
    _pytest_cache(tmp_path, ["tests/test_main.py::test_a"], record)
    assert wc.failing_tests(tmp_path) == ()


@pytest.mark.parametrize("sub", ["svc", "pkg/api"])
def test_a_record_in_a_subdirectory_is_named_from_the_checkout(tmp_path, sub):
    _tree(tmp_path, {f"{sub}/tests/test_a.py": b"def test_x():\n    assert 0\n"})
    _pytest_cache(tmp_path / sub, ["tests/test_a.py::test_x"], {"tests/test_a.py::test_x": True})
    assert wc.failing_tests(tmp_path) == (f"{sub}/tests/test_a.py::test_x",)


@pytest.mark.parametrize("log", ["writer_progress.log", "app/writer_progress_2.txt"])
def test_the_merge_back_view_leaves_out_the_progress_log(tmp_path, log):
    from app.factory.build.codewhale_worker import agent_file_paths

    _tree(tmp_path, {log: b"STEP 1: a\n"})
    paths = set(agent_file_paths(tmp_path))
    assert log not in paths
    assert {"app/main.py", "tests/test_main.py"} <= paths


@pytest.mark.parametrize("audit", ["docs/writer_prompt.txt", "docs/writer_argv.json"])
def test_a_resumed_pass_is_not_told_the_dispatch_audit_files(tmp_path, audit):
    _tree(tmp_path, {audit: b"x", "docs/guide.md": b"# guide\n"})
    files = wc.done_files(tmp_path)
    assert audit not in files
    assert {"app/main.py", "docs/guide.md"} <= set(files)


@pytest.mark.parametrize("count", [wc.TESTS_LISTED, wc.TESTS_LISTED + 7])
def test_the_failing_tests_list_is_capped(count):
    tests = [f"tests/test_m.py::test_{i:04d}" for i in range(count)]
    text = wc.render_done_section([], [], failing=tests)
    assert f"Tests that failed on their last run ({count})" in text
    shown = [line for line in text.splitlines() if line.startswith("  tests/")]
    assert len(shown) == min(count, wc.TESTS_LISTED)
    assert ("  ... and 7 more" in text) is (count > wc.TESTS_LISTED)


@pytest.mark.parametrize("first, cut", [(1200.0, 1800.0), (900.0, 2400.0)])
def test_writer_time_counts_every_pass_not_only_cut_ones(tmp_path, monkeypatch, first, cut):
    """Grader probe Q2: a writer pass, a grader send-back, then the rework
    pass cut at its wall while working -- the Floor's writer time is both
    writer passes, not only the cut one."""
    from app.factory.build.grader import NOT_CERTIFIED, GraderVerdict

    runner, calls = _runner(tmp_path, monkeypatch, cut_steps=4, cuts=0)
    clock = runner.clock
    staging = role_staging_dir(tmp_path / "build", BuildRole.WRITER)
    graded = {"n": 0}

    def grader():
        graded["n"] += 1
        if graded["n"] == 1:
            return GraderVerdict(tier=NOT_CERTIFIED, items=("a check failed",), writer_items=("a check failed",))
        return None

    def phase(role, work_list):
        if role is BuildRole.WRITER:
            calls["writer"] += 1
            if calls["writer"] == 1:
                clock.t += first
                return GateResult(ok=True, gate="writer_gate", detail="ok")
            if calls["writer"] == 2:
                clock.t += cut
                (staging / "docs").mkdir(parents=True, exist_ok=True)
                (staging / "docs" / "writer_progress.log").write_text("STEP 1: a\n", encoding="utf-8")
                _working(runner.ledger, 4)
                raise _cut()
            clock.t += 60.0
            return GateResult(ok=True, gate="writer_gate", detail="ok")
        clock.t += 1.0
        return GateResult(ok=True, gate=f"{role.value.lower()}_gate", detail="ok")

    runner._run_phase = phase  # type: ignore[assignment]
    runner._writer_grader = grader  # type: ignore[assignment]
    runner._grader_factory_fault = lambda *a, **k: None  # type: ignore[assignment]
    runner.run()
    assert calls["writer"] == 3, "the cut rework pass was continued"
    (detail, payload), *_ = _decisions(runner)
    assert payload["writer_seconds"] == first + cut
    assert f"Writer time so far {(first + cut) / 60.0:.0f} min" in detail


def _left_at(tmp_path, monkeypatch, ceiling):
    runner, _calls = _runner(tmp_path, monkeypatch, cut_steps=5, ceiling=ceiling)
    runner.run()
    (_detail, payload), *_ = _decisions(runner)
    return payload


@pytest.mark.parametrize("probe_ceiling", [4500.0, 5000.0])
def test_a_continuation_with_exactly_the_minimum_pass_left_goes(tmp_path, monkeypatch, probe_ceiling):
    """The boundary (mutant A6): ``left == min_pass_s`` continues, one
    second less does not. The ceiling at the boundary is derived from a
    probe run, never typed; both probes sit where the ceiling, not the
    phase allowance, binds the extended wall."""
    probe = _left_at(tmp_path / "probe", monkeypatch, probe_ceiling)
    assert probe["go"] is True and probe["time_left_s"] is not None
    boundary = probe_ceiling - probe["time_left_s"] + wc.min_pass_s()
    at = _left_at(tmp_path / "at", monkeypatch, boundary)
    below = _left_at(tmp_path / "below", monkeypatch, boundary - 1.0)
    assert (at["go"], at["time_left_s"]) == (True, wc.min_pass_s())
    assert (below["go"], below["time_left_s"]) == (False, wc.min_pass_s() - 1.0)


@pytest.mark.parametrize("pass_resumed, done", [(False, False), (True, True)])
def test_only_this_pass_resuming_gets_the_done_list(tmp_path, monkeypatch, pass_resumed, done):
    """Mutant A19: with the sticky ``writer_resumed`` set for the run (a
    rework pass after an earlier resume), only a pass that itself resumed
    gets ALREADY DONE."""
    from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker
    from tests.factory.test_codewhale_writer_dispatch import _ctx, _plant_authored_handler, _receipt

    ctx = _ctx(tmp_path)
    root = tmp_path / "build"
    _plant_authored_handler(root)
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "writer_progress.log").write_text("STEP 1: models\n", encoding="utf-8")
    ctx.state["writer_resumed"] = True
    ctx.state[wc.RESUMED_KEY] = pass_resumed
    prompts = []

    def fake_run(prompt, dest, **_kw):
        prompts.append(prompt)
        return _receipt(tools=[{"tool": "write", "path": "app/actions/cap.py"}])

    monkeypatch.setattr("app.factory.build.codewhale_worker.run_worker_job", fake_run)
    _run_writer_via_codewhale_worker(ctx)
    assert ("ALREADY DONE" in prompts[0]) is done
    assert ("  STEP 1: models" in prompts[0]) is done
