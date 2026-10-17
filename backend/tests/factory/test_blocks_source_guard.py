"""A configured blocks root must actually BE one, or the resolver moves on.

Live (2026-09-30, the automotive-platform build): Cerebrum-Blocks went
private, the live Factory's runtime clone had no token, and CLONER died with
``vendor_blocks_missing: nothing was cloned`` — while the Store's automotive
kit sat unreachable. The fix bakes the Store into the backend image at deploy
time and points CEREBRUM_BLOCKS_ROOT at it. That makes the env var
load-bearing in production, so it must be GUARDED: a configured path that is
missing or has no ``block_registry/`` (a CI image built with an empty
placeholder dir) is ignored with a warning and the resolver falls through to
the clone/mirror path — it must never return a dead root that CLONER then
trusts.
"""

from __future__ import annotations

from pathlib import Path


def _no_clone(monkeypatch):
    """The clone leg is not under test; make it unavailable."""
    from app.core import engine_discovery

    monkeypatch.setattr(engine_discovery, "find_engine_root", lambda: None)


def test_a_configured_root_with_a_registry_is_used(tmp_path, monkeypatch):
    from app.factory.blocks_source import resolve_blocks_root

    root = tmp_path / "store"
    (root / "block_registry").mkdir(parents=True)
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(root))
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)
    assert resolve_blocks_root() == Path(root)


def test_an_empty_configured_root_falls_through(tmp_path, monkeypatch):
    """The CI docker-build ships a placeholder store-blocks/ dir; an image
    run with it must not hand CLONER a root with nothing in it."""
    from app.factory.blocks_source import resolve_blocks_root

    empty = tmp_path / "store-blocks"
    empty.mkdir()
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(empty))
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)
    _no_clone(monkeypatch)
    assert resolve_blocks_root() is None


def test_a_missing_configured_root_falls_through(tmp_path, monkeypatch):
    from app.factory.blocks_source import resolve_blocks_root

    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(tmp_path / "nope"))
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)
    _no_clone(monkeypatch)
    assert resolve_blocks_root() is None
