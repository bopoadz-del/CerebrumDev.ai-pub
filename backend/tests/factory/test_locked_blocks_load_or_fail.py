"""A locked block loads from the vendored tree or the platform refuses to start.

Live (revoked smoke B, 2026-10-08): the product's loader fell back to a local
copy whenever a vendored block was absent, so an image without vendor/ booted
and one block "loaded". The rendered loader now loads every block
blocks.lock.json lists when it is imported and raises, naming each one, if any
cannot be loaded. The product here is synthetic: nothing names a real block.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.factory.build.roles_handlers import _render_dispatch


def _platform(root: Path, locked, vendored) -> Path:
    (root / "app").mkdir(parents=True)
    (root / "app" / "dispatch.py").write_text(_render_dispatch({}), encoding="utf-8")
    for block in vendored:
        d = root / "vendor" / "blocks" / block
        d.mkdir(parents=True)
        (d / "block.py").write_text("def run(input=None, **kw):\n    return {'ok': True}\n", encoding="utf-8")
    if locked is not None:
        (root / "blocks.lock.json").write_text(
            json.dumps({"blocks": {b: {"path": f"vendor/blocks/{b}"} for b in locked}}), encoding="utf-8"
        )
    return root


def _import(root: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, root / "app" / "dispatch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def test_every_locked_block_present_starts_and_is_loaded(tmp_path):
    root = _platform(tmp_path, ["alpha_store", "beta_queue"], ["alpha_store", "beta_queue"])
    module = _import(root, "dispatch_all_present")
    assert set(module._CACHE) == {"alpha_store", "beta_queue"}


def test_a_missing_locked_block_refuses_to_start_naming_it(tmp_path):
    root = _platform(tmp_path, ["alpha_store", "beta_queue"], ["alpha_store"])
    with pytest.raises(RuntimeError) as err:
        _import(root, "dispatch_one_missing")
    assert type(err.value).__name__ == "BlockNotVendored"
    assert "beta_queue" in str(err.value) and "refusing to start" in str(err.value)


def test_a_broken_locked_block_refuses_to_start(tmp_path):
    root = _platform(tmp_path, ["alpha_store"], ["alpha_store"])
    (root / "vendor" / "blocks" / "alpha_store" / "block.py").write_text("raise ImportError('x')\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="alpha_store"):
        _import(root, "dispatch_broken")


def test_no_lockfile_means_nothing_to_verify(tmp_path):
    root = _platform(tmp_path, None, [])
    _import(root, "dispatch_no_lock")


def test_the_loader_has_no_fallback_path():
    """The only place a block is read from is the vendored tree."""
    source = _render_dispatch({})
    assert "import_module" not in source.split("def load_block", 1)[1].split("\ndef ", 1)[0]
