"""Every clone must record where it came from and at what revision.

New-shape tests for the gap that blocked the Store registrar. Clones were
recorded as ``source_commit: "unpinned"`` -- see any older ledger,
``cloned analytics@unpinned`` -- because run_cloner wrote no revision into
blocks.lock.json.

That makes staleness unanswerable, and unrecoverably so: once a platform
ships, the commit a block came from cannot be reconstructed, because the
vendored files look identical whether they are current or a year behind. The
registrar's whole question -- "which platform took which block, and is it
stale against store head" -- depends on this being captured at build time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.ledger import EventKind
from app.factory.build.roles import _content_digest, _pin_source
from app.factory.build.runner import RoleRunner
from tests.factory.store_paths import store_registry  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
MIRROR = store_registry()


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_nothing_is_recorded_as_unpinned(tmp_path, stub_coder):
    out = tmp_path / "build"
    runner = RoleRunner(load_blueprint(SMOKE), out)
    assert runner.run().ok

    lock = json.loads((out / "blocks.lock.json").read_text(encoding="utf-8"))
    assert lock["blocks"], "nothing was vendored"
    for bid, meta in lock["blocks"].items():
        assert meta.get("commit"), f"{bid} has no revision"
        assert meta["commit"] != "unpinned", bid
        assert meta["source"] in ("cerebrum-blocks", "factory-vendor-mirror")

    clones = {c["block_id"]: c for c in runner.ledger.clones()}
    assert set(clones) == set(lock["blocks"])
    for bid, clone in clones.items():
        assert clone["source_commit"] == lock["blocks"][bid]["commit"], bid
        assert clone["source_commit"] != "unpinned"


def test_the_ledger_carries_the_revision_for_the_registrar(tmp_path, stub_coder):
    """iter_ledgers() is how the registrar answers its question."""
    out = tmp_path / "build"
    runner = RoleRunner(load_blueprint(SMOKE), out)
    assert runner.run().ok

    events = [e for e in runner.ledger.events() if e.kind is EventKind.CLONE]
    assert events
    for e in events:
        assert e.payload["source_commit"] not in ("", "unpinned", None)
        assert e.payload["vendored_path"].startswith("vendor/blocks/")


def test_a_mirror_source_is_pinned_by_content(tmp_path):
    """The mirror is not a git repo, so content is the only honest revision."""
    origin, revision = _pin_source(MIRROR / "analytics", None)
    assert origin == "factory-vendor-mirror"
    assert revision.startswith("sha256:")


def test_the_content_digest_moves_when_the_block_does(tmp_path):
    """A revision that never changes cannot detect staleness."""
    block = tmp_path / "b"
    block.mkdir()
    (block / "block.py").write_text("VALUE = 1\n", encoding="utf-8")
    first = _content_digest(block)

    assert _content_digest(block) == first, "digest must be stable"

    (block / "block.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert _content_digest(block) != first, "digest did not follow the content"


def test_the_digest_ignores_bytecode(tmp_path):
    """__pycache__ would make the digest depend on whether it had been run."""
    block = tmp_path / "b"
    (block / "__pycache__").mkdir(parents=True)
    (block / "block.py").write_text("VALUE = 1\n", encoding="utf-8")
    before = _content_digest(block)
    (block / "__pycache__" / "block.cpython-311.pyc").write_bytes(b"\x00\x01")
    assert _content_digest(block) == before


def test_a_store_checkout_is_pinned_by_commit(tmp_path):
    """A real checkout pins to its HEAD, not to a content hash."""
    import subprocess

    repo = tmp_path / "store"
    (repo / "block_registry" / "web").mkdir(parents=True)
    (repo / "block_registry" / "web" / "block.py").write_text("x = 1\n", encoding="utf-8")
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "init"],
    ):
        r = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
        if r.returncode != 0 and cmd[1] == "init":
            pytest.skip(reason="git unavailable in this environment")

    origin, revision = _pin_source(repo / "block_registry" / "web", repo)
    assert origin == "cerebrum-blocks"
    assert len(revision) == 40 and not revision.startswith("sha256:")


def test_a_non_git_checkout_falls_back_to_content_not_a_lie(tmp_path):
    """git_head answers "unknown" outside a repo; recording that would be
    a revision that means nothing."""
    fake = tmp_path / "store"
    (fake / "block_registry" / "web").mkdir(parents=True)
    (fake / "block_registry" / "web" / "block.py").write_text("x = 1\n", encoding="utf-8")

    origin, revision = _pin_source(fake / "block_registry" / "web", fake)
    assert origin == "cerebrum-blocks"
    assert revision.startswith("sha256:"), revision
    assert revision != "unknown"
