"""Every generation door must resolve the blocks inventory the same way.

New-shape tests for the PRR fix: the chat flow had an engine-clone fallback
("the fix for hollow products") while the HTTP plan/generate routes read only
the env vars — on a deploy with no local checkout, HTTP-generated products
silently vendored stub-mirror blocks while chat vendored real code.
"""

from __future__ import annotations

import inspect

from app.factory import platform_chat_flow
from app.factory.blocks_source import resolve_blocks_root


def test_env_path_wins(monkeypatch, tmp_path):
    (tmp_path / "block_registry").mkdir()  # a valid root, per the guard
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(tmp_path))
    assert resolve_blocks_root() == tmp_path


def test_clone_fallback_used_when_env_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)
    checkout = tmp_path / "engine"
    (checkout / "block_registry").mkdir(parents=True)

    from app.core import engine_discovery

    monkeypatch.setattr(engine_discovery, "find_engine_root", lambda: checkout)
    assert resolve_blocks_root() == checkout


def test_clone_without_registry_is_rejected(monkeypatch, tmp_path):
    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)
    bare = tmp_path / "not-a-blocks-checkout"
    bare.mkdir()

    from app.core import engine_discovery

    monkeypatch.setattr(engine_discovery, "find_engine_root", lambda: bare)
    assert resolve_blocks_root() is None


def test_clone_failure_never_raises(monkeypatch):
    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)

    from app.core import engine_discovery

    def _boom():
        raise RuntimeError("git unavailable")

    monkeypatch.setattr(engine_discovery, "find_engine_root", _boom)
    assert resolve_blocks_root() is None


def test_chat_flow_delegates_to_shared_resolver(monkeypatch, tmp_path):
    (tmp_path / "block_registry").mkdir()  # a valid root, per the guard
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(tmp_path))
    assert platform_chat_flow._blocks_root() == resolve_blocks_root() == tmp_path


def test_http_routes_use_the_shared_resolver_not_raw_env():
    """Pin the regression precisely: the routers must call resolve_blocks_root
    and must not re-grow their own env-only resolution."""
    import app.routers.session_product as sp

    src = inspect.getsource(sp)
    assert "resolve_blocks_root()" in src
    assert 'os.getenv("CEREBRUM_BLOCKS_ROOT")' not in src
