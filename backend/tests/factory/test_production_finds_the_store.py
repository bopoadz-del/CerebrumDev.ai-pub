"""Production has no CEREBRUM_BLOCKS_ROOT and no sibling checkout.

It builds from the pinned Store clone. ``dual_registry._default_blocks_root``
never looked there -- the vendor mirror covered for it -- so deleting the
mirror left production's architect with an empty Store: every capability
drafted GENERATE with no blocks. CI sets the env var and saw nothing.
"""

from __future__ import annotations

import json

from app.factory import blocks_source, dual_registry


def _store(tmp_path):
    reg = tmp_path / "pinned" / "block_registry" / "notification"
    reg.mkdir(parents=True)
    (reg / "block.json").write_text(json.dumps({"id": "notification", "version": "1"}), encoding="utf-8")
    (reg / "block.py").write_text("def run(**kw):\n    return {}\n", encoding="utf-8")
    return tmp_path / "pinned"


def test_with_no_env_and_no_sibling_the_pinned_clone_is_used(tmp_path, monkeypatch):
    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)
    pinned = _store(tmp_path)
    # production: no sibling checkout anywhere
    monkeypatch.setattr(dual_registry.Path, "exists", lambda self: False)
    monkeypatch.setattr(blocks_source, "resolve_blocks_root", lambda: pinned)
    monkeypatch.setattr(dual_registry.Path, "is_dir", lambda self: str(self).endswith("block_registry"))

    assert dual_registry._default_blocks_root() == pinned


def test_the_architect_sees_blocks_in_a_production_shaped_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)
    pinned = _store(tmp_path)
    monkeypatch.setattr(dual_registry, "_default_blocks_root", lambda: pinned)

    assert "notification" in dual_registry.load_blocks_registry()
    assert "notification" in dual_registry.dual_registered_ids(), (
        "the architect would draft every capability as GENERATE"
    )
