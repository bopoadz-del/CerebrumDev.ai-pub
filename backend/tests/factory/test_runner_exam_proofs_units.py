"""F-2 runner helpers, one fast unit test each (no build run).

Each helper the vendored-lock gate and the proofs probe rely on is called
directly and its exact result asserted: the staging tree, the agent's root
list, the build id, the proof store, the unusable-proofs reason, the lock as
read from disk, the vendored record and the lock gate itself. The full-build
tests in test_vendored_lock_gate.py cover the same paths end to end.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build import store_override as so
from app.factory.build.authority import BuildRole
from app.factory.build.ledger import EventKind
from app.factory.build.runner import (
    RoleRunner,
    agent_rw_roots_for,
    role_staging_dir,
)

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _factory_holds_a_key(monkeypatch, tmp_path):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setattr(so, "_process_key", b"factory-test-key")
    monkeypatch.setenv(so.PROOFS_DIR_ENV, str(tmp_path / "factory-data"))


def _runner(product: Path) -> RoleRunner:
    return RoleRunner(load_blueprint(SMOKE), product)


def _unrecorded_notes(runner: RoleRunner) -> list:
    return [
        e
        for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("vendored_lock") == "unrecorded"
    ]


def _vendor(product: Path, block_ids) -> dict:
    """A product with each block vendored and named in its lock."""
    blocks = {}
    for block_id in block_ids:
        block_dir = product.joinpath(*so.VENDOR_BLOCKS, block_id)
        block_dir.mkdir(parents=True, exist_ok=True)
        (block_dir / "block.py").write_text(f"NAME = {block_id!r}\n", encoding="utf-8")
        blocks[block_id] = {
            "source": "store",
            "commit": "c0ffee",
            "path": so.vendored_path(block_id),
        }
    (product / "blocks.lock.json").write_text(json.dumps({"blocks": blocks}), encoding="utf-8")
    return {b: so.tree_hash(product.joinpath(*so.VENDOR_BLOCKS, b)) for b in block_ids}


# -- role_staging_dir / _staging_for -----------------------------------------


@pytest.mark.parametrize("product_rel", ["volume/build", "other/product-two"])
def test_role_staging_dir_stages_only_the_writer(tmp_path, product_rel):
    product = tmp_path / product_rel
    expected = product.parent / f".{product.name}.staging-writer"
    assert role_staging_dir(product, BuildRole.WRITER) == expected
    for role in BuildRole:
        if role is not BuildRole.WRITER:
            assert role_staging_dir(product, role) is None
    runner = _runner(product)
    assert runner._staging_for(BuildRole.WRITER) == expected
    assert runner._staging_for(BuildRole.CLONER) is None


# -- agent_rw_roots_for / agent_rw_roots -------------------------------------


@pytest.mark.parametrize("product_rel", ["volume/build", "other/product-two"])
def test_agent_rw_roots_for_is_the_product_and_the_writers_staging(tmp_path, product_rel):
    product = tmp_path / product_rel
    staging = product.parent / f".{product.name}.staging-writer"
    assert agent_rw_roots_for(product) == (product, staging)
    assert agent_rw_roots_for(str(product)) == (product, staging)
    assert _runner(product).agent_rw_roots() == (product, staging)


# -- build_id ----------------------------------------------------------------


@pytest.mark.parametrize("pair", [("volume/a", "volume/b"), ("one/build", "two/build")])
def test_build_id_names_the_product_and_is_stable(tmp_path, pair):
    first, second = (_runner(tmp_path / rel) for rel in pair)
    again = _runner(tmp_path / pair[0])
    assert first.build_id.startswith("build:")
    assert len(first.build_id) == len("build:") + 24
    assert again.build_id == first.build_id
    assert second.build_id != first.build_id


# -- exam_proofs / _proofs_unusable ------------------------------------------


@pytest.mark.parametrize("product_rel", ["volume/build", "other/product-two"])
def test_exam_proofs_opens_one_store_outside_the_product(tmp_path, product_rel):
    product = tmp_path / product_rel
    runner = _runner(product)
    store = runner.exam_proofs()
    assert store is not None
    assert runner.exam_proofs() is store
    assert not so.is_inside(store.root, product.parent)
    assert runner._exam_proofs_refusal == ""


@pytest.mark.parametrize(
    "exc, name",
    [(OSError(5, "io"), "OSError"), (RuntimeError("no home"), "RuntimeError")],
)
def test_proofs_unusable_keeps_no_store_and_a_plain_reason(tmp_path, exc, name):
    runner = _runner(tmp_path / "volume" / "build")
    runner._exam_proofs_store = object()
    runner._proofs_unusable(exc)
    assert runner._exam_proofs_store is None
    assert runner._exam_proofs_refusal == f"the exam proofs directory is not usable ({name})"


# -- _product_lock -----------------------------------------------------------


@pytest.mark.parametrize(
    "body, expected",
    [
        (None, {}),
        (
            '{"blocks": {"b1": {"path": "vendor/blocks/b1"}}}',
            {"blocks": {"b1": {"path": "vendor/blocks/b1"}}},
        ),
        ("[]", None),
        ("not json {", None),
    ],
)
def test_product_lock_reads_the_lock_on_disk(tmp_path, body, expected):
    product = tmp_path / "volume" / "build"
    product.mkdir(parents=True)
    if body is not None:
        (product / "blocks.lock.json").write_text(body, encoding="utf-8")
    assert _runner(product)._product_lock() == expected


# -- _record_vendored --------------------------------------------------------


@pytest.mark.parametrize("block_ids", [("alpha",), ("alpha", "beta")])
def test_record_vendored_signs_each_vendored_block(tmp_path, block_ids):
    product = tmp_path / "volume" / "build"
    hashes = _vendor(product, block_ids)
    runner = _runner(product)
    runner._record_vendored()
    assert so.vendored_record(runner.exam_proofs()) == hashes
    assert _unrecorded_notes(runner) == []


@pytest.mark.parametrize("below", ["", "deeper"])
def test_record_vendored_without_a_store_says_why_in_the_ledger(tmp_path, monkeypatch, below):
    blocker = tmp_path / "factory-data-is-a-file"
    blocker.write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setenv(so.PROOFS_DIR_ENV, str(blocker / below if below else blocker))
    product = tmp_path / "volume" / "build"
    _vendor(product, ("alpha",))
    runner = _runner(product)
    runner._record_vendored()
    reason = runner._exam_proofs_refusal
    assert reason.startswith("the exam proofs directory is not usable (")
    notes = _unrecorded_notes(runner)
    assert [n.payload["reason"] for n in notes] == [reason]
    assert notes[0].detail == f"VENDORED LOCK not recorded: {reason}"


# -- _vendored_lock_gate -----------------------------------------------------


@pytest.mark.parametrize("block_ids", [("alpha",), ("alpha", "beta")])
def test_vendored_lock_gate_holds_then_refuses_an_unrecorded_edit(tmp_path, block_ids):
    product = tmp_path / "volume" / "build"
    _vendor(product, block_ids)
    runner = _runner(product)
    runner._record_vendored()
    assert runner._vendored_lock_gate() is None

    edited = block_ids[-1]
    (product.joinpath(*so.VENDOR_BLOCKS, edited) / "planted.py").write_text(
        "X = 1\n", encoding="utf-8"
    )
    verdict = runner._vendored_lock_gate()
    assert verdict is not None and not verdict.ok
    assert verdict.gate == so.LOCK_GATE and verdict.reason == so.LOCK_GATE_REASON
    assert verdict.findings and all(f.startswith(f"{edited}:") for f in verdict.findings)


@pytest.mark.parametrize("body", ["[]", "not json {"])
def test_vendored_lock_gate_refuses_a_lock_that_is_not_an_object(tmp_path, body):
    product = tmp_path / "volume" / "build"
    _vendor(product, ("alpha",))
    runner = _runner(product)
    runner._record_vendored()
    (product / "blocks.lock.json").write_text(body, encoding="utf-8")
    verdict = runner._vendored_lock_gate()
    assert verdict is not None
    assert verdict.findings == ["blocks.lock.json is not a JSON object"]
