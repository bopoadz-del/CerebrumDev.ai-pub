"""F3: COLLECTOR runs the import-closure rule before CLONER.

The plan must fail at the COLLECTOR (planning) with the block and module
named, so the architect re-selects instead of the build dying later in
CLONER. The COLLECTOR reuses the SAME closure rule CLONER runs
(``_closure_over_runtime``) -- it is not a second scanner.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.roles import RoleContext, RoleError, run_collector
from app.factory.build.workspace import RoleWorkspace

pytestmark = pytest.mark.usefixtures("_no_paid_calls")


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


_SHIM = '''\
"""Auto-generated adapter for Cerebrum block: greeting."""

from app.blocks import get_block


def run(**kwargs):
    block_cls = get_block("greeting")
    return block_cls()(kwargs)
'''

_BLOCKS_INIT = '''\
_EXTENDED_BLOCK_DEFS = {
    "greeting": ("app.blocks.greeting", "GreetingBlock"),
}


def get_block(name):
    return _EXTENDED_BLOCK_DEFS[name]
'''


def _faux_store(root: Path, greeting_source: str) -> Path:
    """A Store checkout shaped like the real one, with one runtime block."""
    store = root / "store"
    reg = store / "block_registry" / "greeting"
    reg.mkdir(parents=True)
    (reg / "block.json").write_text(
        json.dumps({"id": "greeting", "name": "Greeting"}), encoding="utf-8"
    )
    (reg / "block.py").write_text(_SHIM, encoding="utf-8")

    blocks = store / "app" / "blocks"
    blocks.mkdir(parents=True)
    (blocks / "__init__.py").write_text(_BLOCKS_INIT, encoding="utf-8")
    (blocks / "greeting.py").write_text(greeting_source, encoding="utf-8")

    core = store / "app" / "core"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    return store


def _collect(tmp_path: Path, store: Path, block_ids=("greeting",)):
    ws = RoleWorkspace(BuildRole.COLLECTOR, tmp_path / "build")
    plan = types.SimpleNamespace(
        capabilities=[
            types.SimpleNamespace(capability_id="greet", block_ids=list(block_ids))
        ]
    )
    ctx = RoleContext(
        role=BuildRole.COLLECTOR,
        workspace=ws,
        blueprint=None,
        plan=plan,
        blocks_root=store,
        state={},
    )
    return run_collector(ctx)


def test_a_selected_block_whose_imports_close_passes_collector(tmp_path):
    """The gate must not reject a block that resolves cleanly."""
    store = _faux_store(
        tmp_path,
        "class GreetingBlock:\n"
        "    async def execute(self, input_data, params):\n"
        "        return {'status': 'ok'}\n",
    )
    result = _collect(tmp_path, store)
    assert result.ok, result.detail
    assert "greeting" in result.vendored_blocks


def test_a_selected_block_with_an_unresolvable_import_fails_the_plan(tmp_path):
    """Prompt 2 / F3: an ``app.*`` import that does not close inside the
    Store fails the COLLECTOR with the module named, before CLONER runs."""
    store = _faux_store(
        tmp_path,
        "from app.core.does_not_exist import Missing\n\n"
        "class GreetingBlock:\n"
        "    async def execute(self, input_data, params):\n"
        "        return {'status': 'ok'}\n",
    )
    with pytest.raises(RoleError, match="does_not_exist"):
        _collect(tmp_path, store)
