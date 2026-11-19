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
    _certified,
    _finding,
    _grader_reworks,
    _repair_notes,
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


def _answers_then_exits(code):
    return (
        "import sys\n"
        "def run(**kwargs):\n"
        "    sys.stdout.write('\\n' + sys.argv[2] + '{\"status\": \"success\"}\\n')\n"
        "    sys.stdout.flush()\n"
        f"    raise SystemExit({code})\n"
    )


@pytest.mark.parametrize("code", [3, 9])
def test_a_staged_block_that_exits_nonzero_is_refused_even_after_an_answer(tmp_path, code):
    from app.factory.build import store_repair_wiring as w

    (tmp_path / "block.py").write_text(_answers_then_exits(code), encoding="utf-8")
    with pytest.raises(RuntimeError, match=f"exited {code}"):
        w.invoke_staged(tmp_path)({"payload": {}})


@pytest.mark.parametrize(
    "source",
    [
        "def run(**kwargs):\n    raise SystemExit(0)\n",
        "import os\ndef run(**kwargs):\n    os._exit(0)\n",
    ],
)
def test_a_staged_block_that_exits_cleanly_without_an_answer_raises(tmp_path, source):
    from app.factory.build import store_repair_wiring as w

    (tmp_path / "block.py").write_text(source, encoding="utf-8")
    with pytest.raises(RuntimeError, match="printed no result"):
        w.invoke_staged(tmp_path)({"payload": {}})


@pytest.mark.parametrize(
    "name, value", [("PATH", "/opt/staged-bin"), ("SYSTEMROOT", "C:\\StagedRoot")]
)
def test_the_child_environment_carries_only_the_os_basics_that_are_set(monkeypatch, name, value):
    from app.factory.build import store_repair_wiring as w

    monkeypatch.setenv(name, value)
    monkeypatch.setenv("FACTORY_EXAM_PROOF_KEY", "must-not-reach-the-block")
    env = w._child_env()
    assert env[name] == value
    assert "FACTORY_EXAM_PROOF_KEY" not in env


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


# -- the ON path through the real runner (grader 00:30Z on #271) ---------------
#
# Each build below is armed by ``arm`` with the setting on and a proof key,
# then run by the real ``RoleRunner.run()``. The repairer, the exam, the
# staged-block invoker, the rescan, the proof store and the installer are the
# real ones ``arm`` built. Fakes stand only at the provider boundary: the
# phase roles and the grader's verdicts (as in every runner test here), and
# the coding-worker job. Every block id, input, output and literal is invented.

_CONTRACT = {
    "id": "the block",
    "inputs": [
        {"name": "rows", "type": "json", "required": True},
        {"name": "order", "type": "string", "required": False, "options": ["up", "down"]},
    ],
    "outputs": [{"name": "summary", "type": "json"}],
}
_VENDORED = "WIDTH = 2\ndef run(**kw):\n    raise RuntimeError('the vendored copy is broken')\n"
_REPAIRS = (
    "WIDTH = 2\n"
    "def run(**kw):\n"
    "    if 'rows' not in kw:\n"
    "        return {'status': 'error'}\n"
    "    if 'order' in kw and kw['order'] not in ('up', 'down'):\n"
    "        return {'status': 'error'}\n"
    "    return {'status': 'success', 'summary': {'count': len(kw), 'width': WIDTH}}\n",
    "WIDTH = 2\n"
    "_ALLOWED = {'up', 'down'}\n"
    "def run(**kw):\n"
    "    rows = kw.get('rows')\n"
    "    if rows is None or kw.get('order', 'up') not in _ALLOWED:\n"
    "        raise ValueError('refused')\n"
    "    return {'summary': [WIDTH, sorted(kw)]}\n",
)
_STUB = (
    "WIDTH = 2\n"
    "def run(**kw):\n"
    "    if 'rows' not in kw:\n"
    "        return {'status': 'error'}\n"
    "    return {'status': 'success'}\n"
)
_CLAUSE = "the tide tables are read aloud before the lantern count"


def _armed_build(smoke_blueprint, tmp_path, monkeypatch, index, worker, *, regrade=True):  # noqa: F811
    """A build armed with the setting on, whose CLONER vendors a broken block
    (with its declared contract) and whose grader finds that block's defect,
    then (``regrade``) certifies the product graded again."""
    from app.factory.build import codewhale_worker
    from app.factory.build import store_override as so
    from app.factory.build import store_proposal as sp
    from app.factory.build import store_repair_wiring as w
    from app.factory.build.authority import BuildRole
    from app.factory.build.writer_prompt import record_writer_dispatch

    monkeypatch.setattr(so, "_process_key", b"factory-test-key")
    holder: dict = {"jobs": [], "writes": []}

    def verdicts(runner):
        holder["runner"] = runner
        holder["block"], holder["bound"] = _bound_block(runner, index)
        found = _store_verdict(_finding(holder["block"], "block.py"))
        return [found, _certified()] if regrade else [found]

    def cloner(ctx):
        ws, block = holder["runner"].workspace, holder["block"]
        vendored = ws / "vendor" / "blocks" / block
        vendored.mkdir(parents=True, exist_ok=True)
        (vendored / "block.json").write_text(json.dumps(_CONTRACT), encoding="utf-8")
        (vendored / "block.py").write_text(_VENDORED, encoding="utf-8")
        row = {"source": "store:origin-two", "commit": "fe11a00", "path": f"vendor/blocks/{block}"}
        sp.save_lock(ws, {"blocks": {block: {**row, so.VENDORED_HASH_KEY: so.tree_hash(vendored)}}})
        return _fake_pass(ctx)

    def writer(ctx):
        record_writer_dispatch(
            holder["runner"].state,
            supplied=_CLAUSE,
            brief=_CLAUSE,
            prompt="Preamble.\n\n" + _CLAUSE,
        )
        holder["writes"].append(1)
        return _fake_pass(ctx)

    def job(prompt, checkout_dir, **kwargs):
        holder["jobs"].append((prompt, checkout_dir, kwargs))
        return worker(checkout_dir)

    monkeypatch.setattr(codewhale_worker, "run_worker_job", job)
    runner, _ = _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, None, None)
    runner.roles[BuildRole.CLONER] = cloner
    runner.roles[BuildRole.WRITER] = writer
    assert w.arm(runner, {w.SETTING_ENV: "1"}) is None
    return runner, holder


def _writes(source):
    def worker(staging):
        (staging / "block.py").write_text(source, encoding="utf-8")
        return "receipt"

    return worker


def _crashes(staging):
    raise RuntimeError("worker exited 2")


def _store_row(runner):
    from app.factory.build.store_repair import GRADE_SECTION

    grade = json.loads((runner.workspace / "grade.json").read_text(encoding="utf-8"))
    (row,) = grade[GRADE_SECTION]
    return row


@pytest.mark.parametrize("index, source", [(0, _REPAIRS[0]), (1, _REPAIRS[1])])
def test_on_a_repaired_block_is_installed_and_the_regrade_certifies(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
    source,
):
    from app.factory.build import store_override as so
    from app.factory.build import store_proposal as sp
    from app.factory.build.store_repair import STORE_REPAIR_KEY
    from app.factory.build.store_repair_exam import REPAIR_DIR

    runner, holder = _armed_build(smoke_blueprint, tmp_path, monkeypatch, index, _writes(source))
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    ws, block = runner.workspace, holder["block"]
    staging = ws / REPAIR_DIR / block
    (job,) = holder["jobs"]
    assert job[1] == staging
    vendored = ws / "vendor" / "blocks" / block
    assert (vendored / "block.py").read_text(encoding="utf-8") == source
    saved = json.loads((ws / sp.LOCK_FILE).read_text(encoding="utf-8"))
    assert saved[so.OVERRIDES_KEY][block]["override_hash"] == so.tree_hash(staging)
    assert [r["block_id"] for r in sp.filed_proposals(ws)] == [block]
    row = _store_row(runner)
    assert (row["repaired"], row["not_delivered"]) == (True, [])
    notes = _repair_notes(runner)
    assert [n.payload[STORE_REPAIR_KEY] for n in notes] == [block]
    assert notes[0].detail == f"STORE DEFECT {block}: repaired"
    assert len(holder["writes"]) == 1 and _grader_reworks(runner) == []


@pytest.mark.parametrize(
    "index, worker, named",
    [(0, _writes(_STUB), "empty"), (1, _crashes, "repair crashed: RuntimeError: worker exited 2")],
    ids=["exam-failed", "worker-crashed"],
)
def test_on_a_failed_repair_leaves_the_store_copy_and_is_not_delivered(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
    worker,
    named,
):
    from app.factory.build import store_override as so
    from app.factory.build import store_proposal as sp

    runner, holder = _armed_build(
        smoke_blueprint, tmp_path, monkeypatch, index, worker, regrade=False
    )
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    ws, block = runner.workspace, holder["block"]
    assert len(holder["jobs"]) == 1
    vendored = ws / "vendor" / "blocks" / block
    assert (vendored / "block.py").read_text(encoding="utf-8") == _VENDORED
    saved = json.loads((ws / sp.LOCK_FILE).read_text(encoding="utf-8"))
    assert so.OVERRIDES_KEY not in saved
    assert sp.filed_proposals(ws) == []
    row = _store_row(runner)
    assert (row["repaired"], tuple(row["not_delivered"])) == (False, holder["bound"])
    (note,) = _repair_notes(runner)
    assert named in note.payload["detail"]
    assert len(holder["writes"]) == 1 and _grader_reworks(runner) == []


@pytest.mark.parametrize("index", [0, 1])
def test_on_a_block_whose_allowance_is_spent_starts_no_worker(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
):
    """A build resumed after its block already had its repair round: the
    allowance is read from the ledger, no worker job is started, the Store
    copy stays and the block's capabilities are not delivered."""
    from app.factory.build import store_override as so
    from app.factory.build import store_proposal as sp
    from app.factory.build.ledger import EventKind
    from app.factory.build.store_repair import ALLOWANCE_SPENT, STORE_REPAIR_KEY

    runner, holder = _armed_build(
        smoke_blueprint, tmp_path, monkeypatch, index, _writes(_REPAIRS[0]), regrade=False
    )
    block = holder["block"]
    runner.workspace.mkdir(parents=True, exist_ok=True)
    runner.ledger.start_run(
        product_id=getattr(smoke_blueprint, "product_id", "unknown"),
        inputs_hash=runner.inputs_hash,
    )
    runner.ledger.append(
        EventKind.NOTE,
        detail=f"STORE DEFECT {block}: repair failed",
        payload={STORE_REPAIR_KEY: block},
    )
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    ws = runner.workspace
    assert holder["jobs"] == []
    vendored = ws / "vendor" / "blocks" / block
    assert (vendored / "block.py").read_text(encoding="utf-8") == _VENDORED
    assert so.OVERRIDES_KEY not in json.loads((ws / sp.LOCK_FILE).read_text(encoding="utf-8"))
    row = _store_row(runner)
    assert (row["repaired"], tuple(row["not_delivered"])) == (False, holder["bound"])
    assert _repair_notes(runner)[-1].payload["detail"] == ALLOWANCE_SPENT


# -- the build job calls the arming step --------------------------------------


@pytest.mark.parametrize("value, armed", [("1", True), ("0", False)])
def test_the_build_job_arms_the_runner_before_it_runs(tmp_path, monkeypatch, value, armed):
    """``build_jobs._run`` hands its runner to ``arm`` before ``run()``: with
    the setting on (and the key) the runner it runs holds both parts; off,
    neither."""
    from types import SimpleNamespace

    from app.factory import build_jobs
    from app.factory.blueprint import load_blueprint
    from app.factory.build import store_override as so
    from app.factory.build import store_repair_wiring as w
    from tests.factory.test_build_jobs_run_refunds_failed import SMOKE

    monkeypatch.setenv(w.SETTING_ENV, value)
    monkeypatch.setattr(so, "_process_key", b"factory-test-key")
    seen: list = []

    class _Planner:
        def __init__(self, root, factory_shelf=None):
            pass

        def plan(self, bp):
            return SimpleNamespace(budget_class="small")

    class _Runner:
        store_repairer = None
        store_installer = None

        def __init__(self, blueprint, output_dir, **kwargs):
            self.workspace = output_dir

        def run(self):
            from app.factory.build.runner import Outcome

            seen.append((callable(self.store_repairer), callable(self.store_installer)))
            return SimpleNamespace(outcome=Outcome.SUCCESS, rework_used=0)

    monkeypatch.setattr("app.factory.planner.CapabilityPlanner", _Planner)
    monkeypatch.setattr("app.factory.planner.assert_generatable", lambda p: p)
    monkeypatch.setattr("app.factory.build.runner.RoleRunner", _Runner)
    monkeypatch.setattr(build_jobs, "_maybe_clone_canonical", lambda *a, **k: None)
    monkeypatch.setattr(build_jobs, "_refund_generation_quota", lambda *a, **k: None)

    build_jobs._run(load_blueprint(SMOKE), tmp_path / "out", tmp_path / "store")

    assert seen == [(armed, armed)]
