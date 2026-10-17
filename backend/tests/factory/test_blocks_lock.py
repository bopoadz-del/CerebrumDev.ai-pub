"""Factory store pin: blocks.lock.json is the only resolution path.

A recorded hash that does not match the store tree is a HARD FAILURE.
The message names the block and both hashes. There is no warn path and
no fall-through to latest.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.blocks_lock import (
    LOCK_REGENERATE_HINT,
    BlocksLockError,
    assert_block_matches_lock,
    block_content_hash,
    enforce_store_lock,
    generate_lock,
)
from app.factory.build.authority import BuildRole
from app.factory.build.roles import RoleContext, RoleError, run_cloner
from app.factory.build.workspace import RoleWorkspace
from app.factory.generator import ProductGenerator

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
BASIC = ROOT / "blueprints/examples/basic_product.yaml"


def _store_with(root: Path, block_id: str, body: str = "VALUE = 1\n") -> Path:
    store = root / "store"
    d = store / "block_registry" / block_id
    d.mkdir(parents=True)
    (d / "block.py").write_text(body, encoding="utf-8")
    (d / "block.json").write_text(
        json.dumps({"id": block_id, "version": "1.0.0"}) + "\n",
        encoding="utf-8",
    )
    return store


def _clone(tmp_path: Path, store: Path, block_ids, lock):
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=store,
        blocks_lock=lock,
        state={"resolved_blocks": tuple(block_ids)},
    )
    return ws, run_cloner(ctx)


def test_recorded_hash_mismatch_fails_the_build_naming_both_hashes(tmp_path):
    """The failing-first contract: a fixture lock whose hash does not match
    must abort the build and name the block plus both hashes."""
    store = _store_with(tmp_path, "analytics", "VALUE = 1\n")
    lock = generate_lock(store, consumed_ids=["analytics"])
    actual = lock["blocks"]["analytics"]["content_hash"]
    recorded = "sha256:" + ("0" * 64)
    lock["blocks"]["analytics"]["content_hash"] = recorded
    assert actual != recorded

    with pytest.raises((BlocksLockError, RoleError)) as exc:
        _clone(tmp_path, store, ("analytics",), lock)

    msg = str(exc.value)
    assert "analytics" in msg, msg
    assert recorded in msg, msg
    assert actual in msg, msg


def test_assert_helper_names_block_and_both_hashes(tmp_path):
    store = _store_with(tmp_path, "audit", "BODY = 'real'\n")
    source = store / "block_registry" / "audit"
    actual = block_content_hash(source)
    recorded = "sha256:" + ("ab" * 32)
    lock = {
        "schema": "factory.blocks.lock.v1",
        "blocks": {
            "audit": {
                "id": "audit",
                "version": "1.0.0",
                "content_hash": recorded,
            }
        },
    }
    with pytest.raises(BlocksLockError) as exc:
        assert_block_matches_lock("audit", source, lock)
    msg = str(exc.value)
    assert "audit" in msg
    assert recorded in msg
    assert actual in msg
    assert LOCK_REGENERATE_HINT in msg


def test_unlocked_consumed_block_fails_hard(tmp_path):
    store = _store_with(tmp_path, "dashboard", "X = 1\n")
    lock = {"schema": "factory.blocks.lock.v1", "blocks": {}}
    with pytest.raises((BlocksLockError, RoleError), match="dashboard") as exc:
        _clone(tmp_path, store, ("dashboard",), lock)
    assert LOCK_REGENERATE_HINT in str(exc.value)


def test_matching_lock_allows_the_build(tmp_path):
    store = _store_with(tmp_path, "analytics", "VALUE = 1\n")
    lock = generate_lock(store, consumed_ids=["analytics"])
    ws, result = _clone(tmp_path, store, ("analytics",), lock)
    assert result.ok, result.detail
    assert (ws.destination / "vendor" / "blocks" / "analytics" / "block.py").is_file()


def test_generator_refuses_mismatched_store_hash(tmp_path):
    store = _store_with(tmp_path, "audit", "REAL = True\n")
    lock = generate_lock(store, consumed_ids=["audit"])
    recorded = "sha256:" + ("f" * 64)
    actual = lock["blocks"]["audit"]["content_hash"]
    lock["blocks"]["audit"]["content_hash"] = recorded

    bp = load_blueprint(BASIC)
    with pytest.raises(BlocksLockError) as exc:
        ProductGenerator(
            bp,
            blocks_root=store,
            blocks_lock=lock,
            factory_commit="t",
            blocks_commit="t",
        ).generate(tmp_path / "out")
    msg = str(exc.value)
    assert "audit" in msg
    assert recorded in msg
    assert actual in msg


# Live sess_5782f226 CLONER computed this hash from Store pin a372e76 and
# then died because the production image had no lock file to read. The lock
# is regenerated against the pinned Store (a967e0a1) after each Store change;
# this constant pins the database block hash for that snapshot. The hash is
# platform-deterministic (posix-path ordering + LF-normalized bytes).


def test_committed_lock_lists_every_consumed_block():
    from app.factory.blocks_lock import consumed_block_ids, default_lock_path, load_lock

    lock = load_lock(default_lock_path())
    assert lock["schema"] == "factory.blocks.lock.v1"
    # Shape, not value: the lock records WHICH Store it was built from, and
    # the Factory now follows the Store's head rather than this number. Naming
    # a literal here made every Store move a code change in the Factory.
    sha = lock["store"]["sha"]
    assert len(sha) == 40 and set(sha) <= set("0123456789abcdef"), sha
    assert consumed_block_ids(), "factory shelf is empty"
    for bid, rec in lock["blocks"].items():
        assert rec["id"] == bid
        assert rec["version"]
        assert rec["content_hash"].startswith("sha256:")
        assert len(rec["content_hash"]) == len("sha256:") + 64
    assert "database" in lock["blocks"]
    assert lock["blocks"]["database"]["source"] == "cerebrum-blocks"
    # The pinned hash is checked against the Store it names, not against a
    # literal: a literal broke the Factory on every Store re-sign.
    store = os.environ.get("CEREBRUM_BLOCKS_ROOT", "").strip()
    if store and (Path(store) / ".git").exists():
        from app.factory.blocks_lock import _block_dir_in_store, block_content_hash

        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=store, capture_output=True, text=True
        ).stdout.strip()
        if head == sha:
            block_dir = _block_dir_in_store(Path(store), "database")
            assert block_dir is not None
            assert lock["blocks"]["database"]["content_hash"] == block_content_hash(block_dir)


def test_committed_lock_covers_steward_blueprint_blocks():
    """GATE1 Steward clone set must resolve through the committed pin."""
    from app.factory.blocks_lock import default_lock_path, load_lock

    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    lock = load_lock(default_lock_path())
    needed = sorted({bid for cap in bp.capabilities for bid in cap.block_ids})
    assert "database" in needed
    missing = [bid for bid in needed if bid not in lock["blocks"]]
    assert not missing, f"Steward block(s) missing from lock: {missing}"


def test_cloner_accepts_unlocked_database_when_lock_matches(tmp_path):
    """The live failure mode: Steward clones database first among store blocks."""
    store = _store_with(tmp_path, "database", "TABLE = 'registry'\n")
    lock = generate_lock(store, consumed_ids=["database"])
    ws, result = _clone(tmp_path, store, ("database",), lock)
    assert result.ok, result.detail
    assert (ws.destination / "vendor" / "blocks" / "database" / "block.py").is_file()


def test_missing_lock_file_names_path_and_regenerate_command(tmp_path, monkeypatch):
    """A missing pin must not look like 'database is not in the lock'."""
    store = _store_with(tmp_path, "database", "TABLE = 'registry'\n")
    source = store / "block_registry" / "database"
    missing = tmp_path / "no-such-blocks.lock.json"
    monkeypatch.setenv("FACTORY_BLOCKS_LOCK", str(missing))
    with pytest.raises(BlocksLockError) as exc:
        enforce_store_lock("database", source, store, None)
    msg = str(exc.value)
    assert "lock file missing" in msg
    assert "database" in msg
    assert str(missing) in msg
    assert LOCK_REGENERATE_HINT in msg
    assert "not in the lock" not in msg


def test_update_lock_refresh_is_mechanical(tmp_path):
    from app.factory.blocks_lock import consumed_block_ids
    from app.factory.cli import main

    store = _store_with(tmp_path, "analytics", "PIN = 1\n")
    (store / "block_registry" / "dashboard").mkdir(parents=True)
    (store / "block_registry" / "dashboard" / "block.py").write_text(
        "PIN = 2\n", encoding="utf-8"
    )
    (store / "block_registry" / "dashboard" / "block.json").write_text(
        json.dumps({"id": "dashboard", "version": "2.0.0"}) + "\n",
        encoding="utf-8",
    )
    for bid in consumed_block_ids():
        d = store / "block_registry" / bid
        if (d / "block.json").is_file():
            continue
        d.mkdir(parents=True, exist_ok=True)
        (d / "block.py").write_text(f"STUB = {bid!r}\n", encoding="utf-8")
        (d / "block.json").write_text(
            json.dumps({"id": bid, "version": "1.0.0"}) + "\n", encoding="utf-8"
        )
    out = tmp_path / "blocks.lock.json"
    rc = main(
        [
            "update-lock",
            "--blocks-root",
            str(store),
            "--output",
            str(out),
        ]
    )
    assert rc == 0
    lock = json.loads(out.read_text(encoding="utf-8"))
    assert lock["schema"] == "factory.blocks.lock.v1"
    assert lock["blocks"]["analytics"]["id"] == "analytics"
    assert lock["blocks"]["analytics"]["content_hash"] == block_content_hash(
        store / "block_registry" / "analytics"
    )
    assert lock["blocks"]["dashboard"]["version"] == "2.0.0"
    assert lock["blocks"]["dashboard"]["content_hash"] == block_content_hash(
        store / "block_registry" / "dashboard"
    )


class TestTheInventoryIsTheStores:
    """The Factory reads what the Store publishes.

    The committed lock is a snapshot of INTEGRITY, not the inventory. Treating
    it as the membership list meant a block the Store had certified was
    unbuildable until somebody regenerated and committed the lock -- live, the
    twelve marketplace_ops blocks plus ``vendor_catalog``.
    """

    def test_a_store_block_the_lock_never_saw_is_locked_from_the_store(self, tmp_path):
        from app.factory.blocks_lock import top_up_from_store

        store = _store_with(tmp_path, "vendor_catalog", "CATALOG = []\n")
        lock = {"schema": "factory.blocks.lock.v1", "blocks": {}}

        merged = top_up_from_store(lock, store, consumed=["vendor_catalog"])

        assert "vendor_catalog" in merged["blocks"]
        entry = merged["blocks"]["vendor_catalog"]
        assert entry["content_hash"] == block_content_hash(
            store / "block_registry" / "vendor_catalog"
        )
        assert entry["source"] == "cerebrum-blocks"
        assert merged["topped_up"] == ["vendor_catalog"]

    def test_a_recorded_block_is_never_overwritten(self, tmp_path):
        """Tamper detection is unchanged: only 'never met this block' moves."""
        from app.factory.blocks_lock import top_up_from_store

        store = _store_with(tmp_path, "analytics", "VALUE = 1\n")
        recorded = "sha256:" + ("0" * 64)
        lock = {
            "schema": "factory.blocks.lock.v1",
            "blocks": {
                "analytics": {"id": "analytics", "version": "1.0.0", "content_hash": recorded}
            },
        }

        merged = top_up_from_store(lock, store, consumed=["analytics"])

        assert merged["blocks"]["analytics"]["content_hash"] == recorded
        assert "topped_up" not in merged

    def test_a_tampered_store_tree_still_fails_hard_after_a_top_up(self, tmp_path):
        """The whole point of the lock survives: a changed tree is refused."""
        from app.factory.blocks_lock import top_up_from_store

        store = _store_with(tmp_path, "audit", "REAL = True\n")
        lock = top_up_from_store(
            {"schema": "factory.blocks.lock.v1", "blocks": {}}, store, consumed=["audit"]
        )
        assert "audit" in lock["blocks"]

        (store / "block_registry" / "audit" / "block.py").write_text(
            "REAL = False  # swapped\n", encoding="utf-8"
        )

        with pytest.raises(BlocksLockError, match="audit"):
            assert_block_matches_lock("audit", store / "block_registry" / "audit", lock)

    def test_a_block_in_neither_store_nor_mirror_is_left_for_the_gate_to_name(self, tmp_path):
        from app.factory.blocks_lock import top_up_from_store

        store = _store_with(tmp_path, "present", "X = 1\n")

        merged = top_up_from_store(
            {"schema": "factory.blocks.lock.v1", "blocks": {}},
            store,
            consumed=["present", "no_such_block"],
        )

        assert "present" in merged["blocks"]
        assert "no_such_block" not in merged["blocks"]

    def test_no_lock_at_all_stays_none(self):
        from app.factory.blocks_lock import top_up_from_store

        assert top_up_from_store(None) is None
