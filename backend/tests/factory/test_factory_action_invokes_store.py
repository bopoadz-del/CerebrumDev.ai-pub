"""Anti-hollowness guard #2: the generated ACTION must invoke real blocks.

Root cause of the second layer of the hollow retail product (2026-07-26): even
after the vendoring fix (#119), every generated action's handle() returned a
canned string — `"<cap> executed via Factory template"` with `ok: True` — and
called nothing. The product reported success while doing zero work.

The live-proof export confirmed it: all four retail actions still carried the
canned string and zero store calls.

These tests pin the contract that would have caught it:
- The generated action source must NOT contain the canned template string and
  MUST run its bound blocks in-process through app/block_runtime (the product
  carries its blocks; no remote Store URL is read).
- A block the product does not carry degrades to an error -- never ok:True.
- With the blocks present, handle() runs each bound block once and returns
  the REAL block output, not a template.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator

ROOT = Path(__file__).resolve().parents[3]
BLUEPRINT = ROOT / "blueprints" / "examples" / "basic_product.yaml"

_CANNED = "executed via Factory template"


def _generate_reuse_action(tmp_path: Path):
    """Generate a product and return (source_text, loaded_module) for the first
    REUSE action that references at least one block."""
    bp = load_blueprint(BLUEPRINT)
    out = tmp_path / "product"
    ProductGenerator(bp, factory_commit="t", blocks_commit="t").generate(out)

    actions_dir = out / "app" / "actions"
    for py in sorted(actions_dir.glob("*.py")):
        if py.name == "__init__.py":
            continue
        text = py.read_text(encoding="utf-8")
        if 'STRATEGY = "REUSE"' in text and "BLOCK_IDS: List[str] = []" not in text:
            mod = types.ModuleType("generated_action_under_test")
            # The generated module imports app.cerebrum_product_kernel.* — the
            # SAME package the backend ships, so it resolves in-process. httpx
            # and os are real imports too.
            exec(compile(text, str(py), "exec"), mod.__dict__)  # noqa: S102
            return text, mod
    pytest.fail("no REUSE action with blocks was generated — test needs one")


def _fake_runtime(monkeypatch, execute_block):
    mod = types.ModuleType("app.block_runtime")
    mod.execute_block = execute_block
    monkeypatch.setitem(sys.modules, "app.block_runtime", mod)


def test_generated_action_source_is_not_a_canned_template(tmp_path):
    text, _ = _generate_reuse_action(tmp_path)
    assert _CANNED not in text, "generated action still returns the canned template string"
    assert "app.block_runtime" in text, "generated action does not run its blocks"
    assert "CEREBRUM_API_URL" not in text and "/v1/execute" not in text, (
        "generated action still depends on a remote Store")


def test_handle_degrades_honestly_when_a_block_is_not_vendored(monkeypatch, tmp_path):
    _, mod = _generate_reuse_action(tmp_path)

    def missing(block_id, payload):
        raise RuntimeError(block_id + " is not vendored in this product")

    _fake_runtime(monkeypatch, missing)
    result = asyncio.run(mod.handle({"tenant_id": "t1"}, {"q": 1}))
    assert result["status"] == "execution_error", result
    assert result.get("error_code") == "block_invocation_failed", result
    assert result.get("output") is None or "result" not in (result.get("output") or {})


def test_handle_runs_blocks_in_process_and_returns_real_output(monkeypatch, tmp_path):
    _, mod = _generate_reuse_action(tmp_path)
    calls = []

    def run(block_id, payload):
        calls.append((block_id, payload))
        return {"block_id": block_id, "result": {"real": True, "block": block_id}}

    _fake_runtime(monkeypatch, run)
    result = asyncio.run(mod.handle({"tenant_id": "t1"}, {"q": 1}))
    assert result["status"] == "success", result
    assert [c[0] for c in calls] == list(mod.BLOCK_IDS), calls
    block_results = result["output"]["result"]["block_results"]
    assert set(block_results) == set(mod.BLOCK_IDS)
    assert all(block_results[b]["result"]["real"] is True for b in mod.BLOCK_IDS)
    assert _CANNED not in str(result)
