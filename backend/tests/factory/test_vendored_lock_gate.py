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


def test_the_same_build_without_the_edit_passes_the_lock_gate(tmp_path, stub_coder):
    product = tmp_path / "volume" / "build"
    runner = RoleRunner(load_blueprint(SMOKE), product, budget=BuildBudget(max_rework=1))
    outcome = runner.run()

    assert outcome.ok, outcome.to_dict()
    assert _lock_gate_failures(runner) == []
    assert so.vendored_record(runner.exam_proofs())
