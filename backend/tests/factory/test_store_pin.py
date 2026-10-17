"""The Factory builds from the Store commit in store.pin, and its lock agrees.

CI checks the Store out at the pin (workflows read store.pin), so on CI the
block-hash comparison below always runs: a lock that names another Store, or
a block whose bytes differ from the pinned Store, fails the build here --
never in a customer's CLONER (2026-10-04).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.factory.blocks_lock import (
    _block_dir_in_store,
    block_content_hash,
    default_lock_path,
    load_lock,
)
from app.factory.store_pin import StorePinError, read_pin, write_pin


def _store_head(root: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True
    ).stdout.strip()


def test_the_pin_is_a_full_store_commit():
    sha = read_pin()
    assert len(sha) == 40


def test_the_lock_names_the_pinned_store():
    assert load_lock(default_lock_path())["store"]["sha"] == read_pin()


def test_every_locked_block_matches_the_pinned_store():
    root = os.environ.get("CEREBRUM_BLOCKS_ROOT", "").strip()
    pin = read_pin()
    on_ci = bool(os.environ.get("CI"))
    if not root or not (Path(root) / ".git").exists():
        assert not on_ci, "CI must check the Store out (at store.pin) for this comparison"
        pytest.skip(reason="no Store checkout here; CI runs this comparison")
    head = _store_head(Path(root))
    if head != pin:
        assert not on_ci, f"CI's Store checkout is at {head}, not the pin {pin}"
        pytest.skip(reason=f"local Store checkout is at {head[:12]}, not the pin")
    lock = load_lock(default_lock_path())
    disagree = []
    for block_id, record in lock["blocks"].items():
        if record.get("source") != "cerebrum-blocks":
            continue
        block_dir = _block_dir_in_store(Path(root), block_id)
        if block_dir is None:
            disagree.append(f"{block_id}: absent from the pinned Store")
        elif record["content_hash"] != block_content_hash(block_dir):
            disagree.append(f"{block_id}: hash differs from the pinned Store")
    assert not disagree, disagree[:10]


def test_a_malformed_pin_is_refused(tmp_path):
    bad = tmp_path / "store.pin"
    bad.write_text("main\n", encoding="utf-8")
    with pytest.raises(StorePinError):
        read_pin(bad)
    with pytest.raises(StorePinError):
        write_pin("main", bad)


def test_bump_store_refuses_a_checkout_at_another_commit(tmp_path):
    from app.factory.cli import bump_store

    store = tmp_path / "store"
    (store / "block_registry").mkdir(parents=True)
    git = lambda *a: subprocess.run(["git", *a], cwd=store, check=True, capture_output=True)  # noqa: E731
    git("init", "-q")
    git("-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    with pytest.raises(StorePinError, match="not"):
        bump_store("a" * 40, store, repo_root=tmp_path)
    assert not (tmp_path / "store.pin").exists(), "the pin must not move when the bump is refused"
