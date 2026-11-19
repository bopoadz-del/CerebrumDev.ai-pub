"""F-3c (b): the Store repairer and installer are built for a real build only
behind FACTORY_STORE_REPAIR. With the setting off the build is exactly
today's: the runner is not touched, the proof key is not taken, and a build
whose grade owes only a Store defect runs the same ledger as one never armed.
"""

# ruff: noqa: F811 -- smoke_blueprint is the imported fixture
from __future__ import annotations

import json

import pytest

from tests.factory.test_a_grader import _fake_pass, _fake_pass_harness, smoke_blueprint  # noqa: F401
from tests.factory.test_store_repair import (
    _bound_block,
    _finding,
    _runner,
    _store_verdict,
)


@pytest.mark.parametrize("value", [None, "", "0", "off", "no", "false", "maybe"])
def test_the_setting_is_off_unless_set_on(value):
    from app.factory.build import store_repair_wiring as w

    env = {} if value is None else {w.SETTING_ENV: value}
    assert w.setting_on(env) is False


@pytest.mark.parametrize("value", ["1", "true", "ON", " yes "])
def test_the_setting_is_on_for_an_on_value(value):
    from app.factory.build import store_repair_wiring as w

    assert w.setting_on({w.SETTING_ENV: value}) is True


def _snapshot(runner):
    return {k: id(v) for k, v in vars(runner).items()}


@pytest.mark.parametrize("value", [None, "0"])
def test_off_leaves_the_runner_untouched_and_takes_no_key(smoke_blueprint, tmp_path, monkeypatch, value):  # noqa: F811
    from app.factory.build import store_override as so
    from app.factory.build import store_repair_wiring as w
    from app.factory.build.runner import RoleRunner

    env = {} if value is None else {w.SETTING_ENV: value}
    runner = RoleRunner(smoke_blueprint, tmp_path / "build")
    taken = []
    monkeypatch.setattr(so, "take_proof_key", lambda *a, **k: taken.append(a) or b"k")
    before = _snapshot(runner)
    assert w.arm(runner, env) == w.OFF
    assert _snapshot(runner) == before
    assert runner.store_repairer is None and runner.store_installer is None
    assert taken == []


@pytest.mark.parametrize("value", ["1", "on"])
def test_on_without_a_proof_key_stays_off(smoke_blueprint, tmp_path, monkeypatch, value):  # noqa: F811
    from app.factory.build import store_override as so
    from app.factory.build import store_repair_wiring as w
    from app.factory.build.runner import RoleRunner

    monkeypatch.setattr(so, "_process_key", None)
    runner = RoleRunner(smoke_blueprint, tmp_path / "build")
    assert w.arm(runner, {w.SETTING_ENV: value}) == w.NO_KEY
    assert runner.store_repairer is None and runner.store_installer is None


@pytest.mark.parametrize("value", ["1", "true"])
def test_on_with_a_proof_key_arms_both(smoke_blueprint, tmp_path, monkeypatch, value):  # noqa: F811
    from app.factory.build import store_override as so
    from app.factory.build import store_repair_wiring as w
    from app.factory.build.runner import RoleRunner

    monkeypatch.setattr(so, "_process_key", b"factory-test-key")
    runner = RoleRunner(smoke_blueprint, tmp_path / "build")
    assert w.arm(runner, {w.SETTING_ENV: value}) is None
    assert callable(runner.store_repairer) and callable(runner.store_installer)


def _ledger_lines(runner):
    root = str(runner.workspace)
    return [
        (e.kind, e.role, str(e.detail or "").replace(root, "<ws>")) for e in runner.ledger.events()
    ]


@pytest.mark.parametrize("index", [0, 1])
def test_a_store_defect_build_with_the_setting_off_runs_todays_ledger(
    smoke_blueprint,
    tmp_path,
    monkeypatch,
    index,  # noqa: F811
):
    """Byte-for-byte: the same Store-only verdict, once on a runner never
    armed (today) and once on a runner armed with the setting off, gives the
    same outcome, the same ledger lines and the same grade file."""
    from app.factory.build import store_repair_wiring as w

    monkeypatch.delenv(w.SETTING_ENV, raising=False)

    def verdicts(runner):
        block, _ = _bound_block(runner, index)
        return [_store_verdict(_finding(block))]

    results = []
    for name, armed in (("today", False), ("off", True)):
        runner, _ = _runner(smoke_blueprint, tmp_path / name, monkeypatch, verdicts, None, None)
        if armed:
            assert w.arm(runner) == w.OFF
        outcome = runner.run()
        grade = runner.workspace / "grade.json"
        results.append(
            (
                outcome.ok,
                outcome.outcome,
                _ledger_lines(runner),
                json.loads(grade.read_text(encoding="utf-8")) if grade.is_file() else None,
            )
        )
    assert results[0] == results[1]


_ECHO = (
    "import os\n"
    "def run(**kwargs):\n"
    "    return {'status': 'success', 'got': kwargs, 'env': sorted(os.environ)}\n"
)
_CRASH = "def run(**kwargs):\n    raise ValueError('refused')\n"


@pytest.mark.parametrize("call", [{"payload": {"n": 1}}, {"text": "abc", "k": [1, 2]}])
def test_the_invoker_runs_the_staged_block_without_the_factory_environment(
    tmp_path, monkeypatch, call
):
    from app.factory.build import store_repair_wiring as w

    monkeypatch.setenv("FACTORY_EXAM_PROOF_KEY", "must-not-reach-the-block")
    (tmp_path / "block.py").write_text(_ECHO, encoding="utf-8")
    out = w.invoke_staged(tmp_path)(call)
    assert out["got"] == call
    assert "FACTORY_EXAM_PROOF_KEY" not in out["env"]


@pytest.mark.parametrize("source", [_CRASH, "x = 1\n"])
def test_a_staged_block_that_cannot_answer_raises(tmp_path, source):
    from app.factory.build import store_repair_wiring as w

    (tmp_path / "block.py").write_text(source, encoding="utf-8")
    with pytest.raises(RuntimeError):
        w.invoke_staged(tmp_path)({"payload": {}})


@pytest.mark.parametrize(
    "clause",
    [
        "the quarterly ledger closes every ninth evening at the harbour office",
        "returns are weighed against the copper tariff before dispatch begins",
    ],
)
def test_the_rescan_is_the_graders_leak_scan_on_the_staged_copy(smoke_blueprint, tmp_path, clause):  # noqa: F811
    from app.factory.build import store_repair_wiring as w

    prompt = "Factory preamble.\n\n" + clause + "\n"
    rescan = w.hardwiring_rescan(smoke_blueprint, supplied=clause, brief=clause, prompt=prompt)
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "block.py").write_text("NOTE = 'nothing here'\n", encoding="utf-8")
    assert rescan(staged) == []
    (staged / "block.py").write_text(f"NOTE = {clause!r}\n", encoding="utf-8")
    found = rescan(staged)
    assert [(f["kind"], f["file"]) for f in found] == [("prompt_leak", "block.py")]


@pytest.mark.parametrize("left", [0.0, -5.0])
def test_the_writer_is_not_started_with_no_wall_left(smoke_blueprint, tmp_path, monkeypatch, left):  # noqa: F811
    from app.factory.build import codewhale_worker
    from app.factory.build import store_repair_wiring as w
    from app.factory.build.runner import RoleRunner

    started = []
    monkeypatch.setattr(codewhale_worker, "run_worker_job", lambda *a, **k: started.append(a))
    runner = RoleRunner(smoke_blueprint, tmp_path / "build")
    monkeypatch.setattr(runner, "_grantable_wall_left", lambda: left)
    with pytest.raises(RuntimeError, match="no build wall left"):
        w.worker_writer(runner)("blk", tmp_path, {"block_id": "blk"})
    assert started == []


@pytest.mark.parametrize("left", [30.0, 7.5])
def test_the_writer_runs_on_the_staged_copy_within_the_wall_left(
    smoke_blueprint, tmp_path, monkeypatch, left
):  # noqa: F811
    from app.factory.build import codewhale_worker
    from app.factory.build import store_repair_wiring as w
    from app.factory.build.runner import RoleRunner

    seen = {}

    def fake_job(prompt, checkout_dir, **kwargs):
        seen.update(prompt=prompt, dir=checkout_dir, **kwargs)
        return "receipt"

    monkeypatch.setattr(codewhale_worker, "run_worker_job", fake_job)
    monkeypatch.setattr(codewhale_worker, "worker_timeout_s", lambda: 3600.0)
    runner = RoleRunner(smoke_blueprint, tmp_path / "build")
    monkeypatch.setattr(runner, "_grantable_wall_left", lambda: left)
    staging = tmp_path / "staging"
    handback = {"block_id": "blk", "defects": [{"kind": "crash"}]}
    assert w.worker_writer(runner)("blk", staging, handback) == "receipt"
    assert seen["dir"] == staging
    assert seen["timeout_s"] == left
    assert json.dumps(handback, indent=2, sort_keys=True) in seen["prompt"]


@pytest.mark.parametrize("raw", ["[1, 2]", "not json"])
def test_the_installer_refuses_a_lock_that_is_not_an_object(
    smoke_blueprint, tmp_path, monkeypatch, raw
):  # noqa: F811
    from app.factory.build import store_override as so
    from app.factory.build import store_repair_wiring as w
    from app.factory.build.runner import RoleRunner
    from app.factory.build.store_repair import RepairResult

    monkeypatch.setattr(so, "_process_key", b"factory-test-key")
    runner = RoleRunner(smoke_blueprint, tmp_path / "build")
    runner.workspace.mkdir(parents=True, exist_ok=True)
    (runner.workspace / "blocks.lock.json").write_text(raw, encoding="utf-8")
    _, installer = w.build_parts(runner)
    assert installer(RepairResult("blk", True), ()) == "blocks.lock.json is not a JSON object"
