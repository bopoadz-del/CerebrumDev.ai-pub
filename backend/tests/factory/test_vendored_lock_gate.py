"""F-2 (grader 19:35Z, item e): through the real build entry point, a lock
row the writer edits is refused.

The coding agent may write the product directory itself (its confinement
grants it), so it can rewrite ``blocks.lock.json`` and the vendored files
without passing the lane check on its staging tree. The runner records the
vendored blocks in the build's Factory-held, signed proof store at the end of
CLONER, and checks the lock after every later role. A forged override row,
over bytes the writer changed, has no proof and fails the build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import store_override as so
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.runner import BuildBudget, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _factory_holds_a_key(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setattr(so, "_process_key", b"factory-test-key")
    monkeypatch.setenv(so.PROOFS_DIR_ENV, str(tmp_path / "factory-data"))


def _forge_an_override(product: Path) -> str:
    """What a writer with the product directory open can do: change a
    vendored block and write an override row claiming a passed exam."""
    lock_path = product / "blocks.lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    block_id = sorted(lock["blocks"])[0]
    block_dir = product / "vendor" / "blocks" / block_id
    vendored = so.tree_hash(block_dir)
    (block_dir / "planted_by_writer.py").write_text("PLANTED = True\n", encoding="utf-8")
    lock.setdefault(so.OVERRIDES_KEY, {})[block_id] = so.OverrideRecord(
        block_id=block_id,
        replaces_hash=vendored,
        override_hash=so.tree_hash(block_dir),
        reason="writer says so",
        exam_id="exam:forged",
        exam_passed=True,
    ).to_dict()
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    return block_id


def _lock_gate_failures(runner: RoleRunner) -> list:
    return [
        e
        for e in runner.ledger.events()
        if e.kind is EventKind.GATE_FAILED
        and (e.payload or {}).get("reason") == so.LOCK_GATE_REASON
    ]


def test_a_writer_edited_lock_row_is_refused_by_the_real_build(tmp_path, stub_coder):
    product = tmp_path / "volume" / "build"
    real_writer = ROLE_IMPLEMENTATIONS[BuildRole.WRITER]
    forged = []

    def editing_writer(ctx):
        result = real_writer(ctx)
        forged.append(_forge_an_override(product))
        return result

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = editing_writer
    runner = RoleRunner(
        load_blueprint(SMOKE), product, roles=roles, budget=BuildBudget(max_rework=1)
    )
    outcome = runner.run()

    assert not outcome.ok
    failures = _lock_gate_failures(runner)
    assert failures and failures[0].role is BuildRole.WRITER
    assert f"{forged[0]}: no Factory-held proof of its exam" in failures[0].payload["findings"][0]
    store = runner.exam_proofs()
    assert store is not None and not so.is_inside(store.root, product.parent)
    assert forged[0] in (so.vendored_record(store) or {})


@pytest.mark.parametrize("product_rel", ["volume/build", "volume-two/product-two"])
def test_the_same_build_without_the_edit_passes_the_lock_gate(tmp_path, product_rel, stub_coder):
    product = tmp_path / product_rel
    runner = RoleRunner(load_blueprint(SMOKE), product, budget=BuildBudget(max_rework=1))
    outcome = runner.run()

    assert outcome.ok, outcome.to_dict()
    assert _lock_gate_failures(runner) == []
    assert so.vendored_record(runner.exam_proofs())


# -- F-2 (grader 20:30Z) -----------------------------------------------------


def _edit_a_vendored_block(product: Path) -> str:
    """A writer's unrecorded edit: a vendored file changes, the lock does not."""
    lock = json.loads((product / "blocks.lock.json").read_text(encoding="utf-8"))
    block_id = sorted(lock["blocks"])[0]
    (product / "vendor" / "blocks" / block_id / "planted_by_writer.py").write_text(
        "PLANTED = True\n", encoding="utf-8"
    )
    return block_id


def _runner_with_writer(product: Path, after_writer=None) -> RoleRunner:
    real_writer = ROLE_IMPLEMENTATIONS[BuildRole.WRITER]

    def writer(ctx):
        result = real_writer(ctx)
        if after_writer is not None:
            after_writer(product)
        return result

    roles = dict(ROLE_IMPLEMENTATIONS)
    roles[BuildRole.WRITER] = writer
    return RoleRunner(load_blueprint(SMOKE), product, roles=roles, budget=BuildBudget(max_rework=1))


def test_no_key_and_an_unrecorded_edit_is_refused_by_the_real_build(tmp_path, stub_coder, monkeypatch):
    monkeypatch.setattr(so, "_process_key", None)
    product = tmp_path / "volume" / "build"
    runner = _runner_with_writer(product, _edit_a_vendored_block)
    outcome = runner.run()

    assert not outcome.ok
    failures = _lock_gate_failures(runner)
    assert failures and failures[0].role is BuildRole.WRITER
    assert "differs from the vendored Store copy and no override is recorded" in (
        failures[0].payload["findings"][0]
    )
    assert runner.exam_proofs().key is None


@pytest.mark.parametrize("product_rel", ["volume/build", "volume-two/product-two"])
def test_no_key_and_no_edit_passes_the_real_build(tmp_path, product_rel, stub_coder, monkeypatch):
    monkeypatch.setattr(so, "_process_key", None)
    product = tmp_path / product_rel
    runner = _runner_with_writer(product)
    outcome = runner.run()

    assert outcome.ok, outcome.to_dict()
    assert _lock_gate_failures(runner) == []
    assert so.vendored_record(runner.exam_proofs())


@pytest.mark.parametrize("product_rel", ["volume/build", "other-volume/product-two"])
def test_the_placement_check_gets_the_agents_real_confinement_roots(tmp_path, product_rel):
    from types import SimpleNamespace

    from app.factory.build.coder_session import agent_rw_extras

    product = tmp_path / product_rel
    runner = RoleRunner(load_blueprint(SMOKE), product, budget=BuildBudget(max_rework=1))
    roots = runner.agent_rw_roots()
    staging = runner._staging_for(BuildRole.WRITER)
    assert staging is not None and staging != runner.workspace
    # The spawn grants a role its working directory plus agent_rw_extras of
    # its role workspace: every one of those is a root the check is given.
    for cwd in (runner.workspace, staging):
        ws = SimpleNamespace(workspace=cwd, destination=runner.workspace)
        assert {cwd, *agent_rw_extras(ws)} <= set(roots)


@pytest.mark.parametrize("attrs", [("destination", "workspace"), ("workspace",)])
def test_the_spawns_extra_roots_are_the_role_workspaces_trees(tmp_path, attrs):
    from types import SimpleNamespace

    from app.factory.build.coder_session import agent_rw_extras

    values = {a: tmp_path / a for a in attrs}
    assert agent_rw_extras(SimpleNamespace(**values)) == [
        tmp_path / a for a in ("destination", "workspace") if a in attrs
    ]
    assert agent_rw_extras(None) == []


@pytest.mark.parametrize("under", ["staging", "staging/deeper"])
def test_a_proofs_dir_the_writer_may_write_is_refused_and_the_build_says_why(
    tmp_path, monkeypatch, under
):
    product = tmp_path / "volume" / "build"
    runner = RoleRunner(load_blueprint(SMOKE), product, budget=BuildBudget(max_rework=1))
    staging = runner._staging_for(BuildRole.WRITER)
    target = staging if under == "staging" else staging / "deeper"
    monkeypatch.setenv(so.PROOFS_DIR_ENV, str(target))
    assert runner.exam_proofs() is None
    assert runner._exam_proofs_refusal
    (product / "blocks.lock.json").write_text(json.dumps({"blocks": {}}), encoding="utf-8")
    verdict = runner._vendored_lock_gate()
    assert verdict is not None and not verdict.ok
    assert verdict.findings[0] == so.NO_RECORD
    assert verdict.findings[-1] == runner._exam_proofs_refusal


@pytest.mark.parametrize("key", [None, b"factory-test-key"])
def test_a_restarted_build_finds_its_record_or_refuses_with_a_plain_reason(
    tmp_path, stub_coder, monkeypatch, key
):
    import shutil

    monkeypatch.setattr(so, "_process_key", key)
    product = tmp_path / "volume" / "build"
    first = RoleRunner(load_blueprint(SMOKE), product, budget=BuildBudget(max_rework=1))
    assert first.run().ok

    # A restart is a new orchestrator over the same product: the record is
    # found under the same build id and the lock gate holds.
    again = RoleRunner(load_blueprint(SMOKE), product, budget=BuildBudget(max_rework=1))
    assert again.build_id == first.build_id
    assert again.exam_proofs().root == first.exam_proofs().root
    assert again._vendored_lock_gate() is None

    # The record gone: the resumed build is refused, and says why.
    shutil.rmtree(first.exam_proofs().root)
    lost = RoleRunner(load_blueprint(SMOKE), product, budget=BuildBudget(max_rework=1))
    verdict = lost._vendored_lock_gate()
    assert verdict is not None and verdict.findings == [so.NO_RECORD]


@pytest.mark.parametrize("body", ["[]", '"a string"'])
def test_a_lock_that_is_not_a_json_object_is_refused(tmp_path, body):
    product = tmp_path / "volume" / "build"
    runner = RoleRunner(load_blueprint(SMOKE), product, budget=BuildBudget(max_rework=1))
    product.mkdir(parents=True, exist_ok=True)
    (product / "blocks.lock.json").write_text(body, encoding="utf-8")
    verdict = runner._vendored_lock_gate()
    assert verdict is not None and verdict.findings == ["blocks.lock.json is not a JSON object"]


@pytest.mark.parametrize("role", [BuildRole.CLONER, BuildRole.TESTER])
def test_only_the_writer_is_staged(tmp_path, role):
    runner = RoleRunner(load_blueprint(SMOKE), tmp_path / "volume" / "build")
    assert runner._staging_for(role) is None
    assert runner._staging_for(BuildRole.WRITER) is not None


# -- F-2 (grader 21:25Z): an unusable proofs directory refuses, never crashes --


def _unusable_reason(name: str) -> str:
    return f"the exam proofs directory is not usable ({name})"


@pytest.mark.parametrize("below_file", ["", "deeper/still"])
def test_an_unwritable_proofs_directory_ends_the_build_refused_not_crashed(
    tmp_path, stub_coder, monkeypatch, below_file
):
    # A path through a regular file cannot be created on any OS: writing the
    # vendored record there raises OSError at the end of CLONER.
    blocker = tmp_path / "factory-data-is-a-file"
    blocker.write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setenv(so.PROOFS_DIR_ENV, str(blocker / below_file if below_file else blocker))
    product = tmp_path / "volume" / "build"
    runner = RoleRunner(load_blueprint(SMOKE), product, budget=BuildBudget(max_rework=1))

    outcome = runner.run()

    assert not outcome.ok
    assert runner.exam_proofs() is None
    reason = runner._exam_proofs_refusal
    # FileExistsError or NotADirectoryError, by OS: both are OSError.
    assert reason in {_unusable_reason(n) for n in ("FileExistsError", "NotADirectoryError")}
    notes = [
        e
        for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("vendored_lock") == "unrecorded"
    ]
    assert [n.payload["reason"] for n in notes] == [reason]
    failures = _lock_gate_failures(runner)
    assert failures and failures[0].payload["findings"][-1] == reason


@pytest.mark.parametrize(
    "crash",
    [RuntimeError("Could not determine home directory."), PermissionError(13, "denied")],
    ids=["no-home", "permission"],
)
def test_a_proofs_directory_that_cannot_be_resolved_refuses_with_a_plain_reason(
    tmp_path, stub_coder, monkeypatch, crash
):
    def unresolvable(environ=None):
        raise crash

    monkeypatch.setattr(so, "proofs_base", unresolvable)
    product = tmp_path / "volume" / "build"
    runner = RoleRunner(load_blueprint(SMOKE), product, budget=BuildBudget(max_rework=1))

    outcome = runner.run()

    assert not outcome.ok
    assert runner.exam_proofs() is None
    assert runner._exam_proofs_refusal == _unusable_reason(type(crash).__name__)
    failures = _lock_gate_failures(runner)
    assert failures and failures[0].payload["findings"] == [
        so.NO_RECORD,
        _unusable_reason(type(crash).__name__),
    ]
