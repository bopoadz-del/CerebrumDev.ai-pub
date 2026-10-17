"""Anti-hollowness guard: the factory must vendor REAL block code.

Root cause of the hollow retail product (2026-07-26): the generator only
consulted CEREBRUM_BLOCKS_ROOT for a blocks checkout, so on any deploy without
a local path every referenced block was copied from the vendor MIRROR — echo
stubs whose block.py says "factory-vendor-mirror stub". The product looked
generated but did nothing.

These tests pin the contract that would have caught it:
- Given a real blocks_root carrying block_registry/<id>, the generated
  product's vendor/blocks/<id>/block.py is the REAL code, not the mirror stub.
- Given no blocks_root at all, the generator falls back to the mirror and says
  so honestly (the fallback is allowed, but it must be labeled, never silent).
- _blocks_root() prefers an explicit env path.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator
from app.factory import platform_chat_flow

ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT = ROOT / "blueprints" / "examples" / "basic_product.yaml"

_MIRROR_MARK = "factory-vendor-mirror stub"


def _synthetic_blocks_root(tmp: Path, block_ids) -> Path:
    """A minimal Store checkout: block_registry/<id>/{block.py,block.json}
    carrying an unmistakable REAL marker per block."""
    root = tmp / "store"
    for bid in block_ids:
        d = root / "block_registry" / bid
        d.mkdir(parents=True, exist_ok=True)
        (d / "block.py").write_text(
            f"# REAL-BLOCK-CODE::{bid}\n"
            f"def run(**kwargs):\n    return {{'block_id': '{bid}', 'real': True}}\n",
            encoding="utf-8",
        )
        (d / "block.json").write_text(
            json.dumps({"name": bid, "version": "1.0.0"}) + "\n", encoding="utf-8"
        )
    return root


def test_generator_vendors_real_code_when_registry_present(tmp_path):
    bp = load_blueprint(BLUEPRINT)
    # First plan against no root to learn which blocks the plan references.
    probe = ProductGenerator(bp, factory_commit="t", blocks_commit="t")
    block_ids = list(probe.plan.dual_registered_blocks)
    assert block_ids, "blueprint plan referenced no blocks — test needs a REUSE block"

    store = _synthetic_blocks_root(tmp_path, block_ids)
    out = tmp_path / "product"
    from app.factory.blocks_lock import generate_lock

    ProductGenerator(
        bp,
        blocks_root=store,
        blocks_lock=generate_lock(store, consumed_ids=block_ids),
        factory_commit="t",
        blocks_commit="t",
    ).generate(out)

    for bid in block_ids:
        vp = out / "vendor" / "blocks" / bid / "block.py"
        assert vp.exists(), f"block {bid} not vendored"
        text = vp.read_text(encoding="utf-8")
        assert f"REAL-BLOCK-CODE::{bid}" in text, f"{bid}: real code not vendored"
        assert _MIRROR_MARK not in text, f"{bid}: echo-stub vendored despite real registry"


def test_blocks_root_prefers_env_path(monkeypatch, tmp_path):
    # A VALID root: the resolver now ignores a configured path with no
    # block_registry/ (a CI image's empty placeholder must not become a
    # dead root handed to CLONER).
    (tmp_path / "block_registry").mkdir()
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(tmp_path))
    assert platform_chat_flow._blocks_root() == tmp_path


def test_blocks_root_clones_store_when_no_env_path(monkeypatch, tmp_path):
    """THE fix: with no local path set, _blocks_root() must resolve a real
    Store checkout via engine_discovery (which clones CEREBRUM_BLOCKS_REPO),
    so the generator vendors real code instead of echo stubs. Previously this
    returned None and every block was mirror-stubbed."""
    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)
    checkout = tmp_path / "cloned_store"
    (checkout / "block_registry").mkdir(parents=True)

    import app.core.engine_discovery as ed
    monkeypatch.setattr(ed, "find_engine_root", lambda anchor=None: checkout)
    assert platform_chat_flow._blocks_root() == checkout


def test_blocks_root_none_when_clone_fails(monkeypatch):
    """Clone failure must not crash generation — it returns None and the
    generator falls back to the vendor mirror (honestly), never raises."""
    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    monkeypatch.delenv("CEREBRUM_BLOCKS_PATH", raising=False)

    import app.core.engine_discovery as ed

    def _boom(anchor=None):
        raise RuntimeError("clone failed: no GITHUB_TOKEN")

    monkeypatch.setattr(ed, "find_engine_root", _boom)
    assert platform_chat_flow._blocks_root() is None  # graceful, no raise
