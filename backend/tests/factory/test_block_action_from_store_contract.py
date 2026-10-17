"""A block's action is the block's own contract, read from the Store.

The Factory used to keep a hand-typed table of default actions, one entry per
failed build. It told every coder to call ``capture`` with ``extract`` and
``validation`` with ``validate``; the Store answers both with "Unknown action".
These tests pin the mechanism that replaced it, on invented blocks in a
throwaway Store, so they hold for blocks no build has met yet.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from app.factory.build import reuse_accept as ra
from app.factory.build.block_inputs import render_block_inputs_module
from app.factory.build.reuse_accept import (
    block_takes_action,
    default_action_from_source,
    default_block_action,
    harvest_block_default_action,
    reuse_accept_brief_contract,
    reuse_accept_handler_errors,
    reuse_accept_rules_text,
)


def _block(root: Path, bid: str, *, inputs=(), wrapper: str = "", code: str = "") -> None:
    reg = root / "block_registry" / bid
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "block.json").write_text(json.dumps({"name": bid, "inputs": list(inputs)}), encoding="utf-8")
    (reg / "block.py").write_text(wrapper or "def run(x):\n    return x\n", encoding="utf-8")
    if code:
        impl = root / "app" / "blocks"
        impl.mkdir(parents=True, exist_ok=True)
        (impl / f"{bid}.py").write_text(code, encoding="utf-8")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    root = tmp_path / "store"
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(root))
    monkeypatch.setattr("app.factory.blocks_source.resolve_blocks_root", lambda *a, **k: None)
    return root


# -- where the default comes from ------------------------------------------


def test_block_json_default_is_the_contract(store):
    _block(store, "zorblat", inputs=[{"name": "action", "default": "grind"}],
           code='def process(d, params):\n    a = params.get("action", "other")\n')
    assert harvest_block_default_action("zorblat") == "grind"


def test_code_declared_default_is_read_from_the_real_implementation(store):
    # The registry block.py is a thin wrapper; the dispatch lives in app/blocks.
    _block(store, "quillon", wrapper="from app.blocks.quillon import Q\n",
           code='class Q:\n    def process(self, d, params):\n'
                '        action = params.get("action", "weave")\n'
                '        if action == "weave": return 1\n'
                '        elif action == "unravel": return 2\n')
    assert harvest_block_default_action("quillon") == "weave"


def test_operation_default_counts_when_no_action_default(store):
    _block(store, "mernix", code='def p(d, params):\n    op = params.get("operation", "tally")\n')
    assert harvest_block_default_action("mernix") == "tally"


def test_action_default_beats_operation_default():
    src = 'a = params.get("operation", "x")\nb = kwargs.get("action", "y")\n'
    assert default_action_from_source(src) == "y"


def test_first_compared_action_when_nothing_is_declared():
    assert default_action_from_source('if action == "spin":\n    pass\n') == "spin"


def test_v2_alias_reads_the_base_blocks_contract(store):
    _block(store, "plinth", inputs=[{"name": "action", "default": "stack"}])
    assert harvest_block_default_action("plinth_v2") == "stack"


def test_handler_map_wins_over_the_store(store):
    _block(store, "zorblat", inputs=[{"name": "action", "default": "grind"}])
    assert default_block_action("zorblat", {"zorblat": "polish"}) == "polish"
    assert default_block_action("zorblat", {}) == "grind"


def test_no_table_of_answers_exists():
    assert not hasattr(ra, "STORE_BLOCK_DEFAULT_ACTIONS")
    assert not any(name.startswith("LIVE_") for name in vars(ra))


# -- a block that takes no action is not a miss ------------------------------


def test_block_without_action_dispatch_needs_none(store):
    _block(store, "fennimore", code="def process(d, params):\n    return {'pages': len(d)}\n")
    assert block_takes_action("fennimore") is False
    assert reuse_accept_handler_errors("BLOCK_IDS = ['fennimore']\n", ["fennimore"]) == []


def test_block_that_dispatches_but_declares_nothing_is_a_miss(store):
    _block(store, "grumwald", code="def process(d, params):\n    if params['action'] in X: pass\n")
    assert block_takes_action("grumwald") is True
    errors = reuse_accept_handler_errors("BLOCK_IDS = ['grumwald']\n", ["grumwald"])
    assert errors and "grumwald" in errors[0]


def test_unknown_block_fails_closed(store):
    assert block_takes_action("nowhere") is None
    assert reuse_accept_handler_errors("BLOCK_IDS = ['nowhere']\n", ["nowhere"])


# -- a product carries its own build's map, never another's ----------------


def test_rendered_product_carries_only_the_builds_map():
    text = render_block_inputs_module({"zorblat": "grind"})
    assert "STORE_BLOCK_DEFAULT_ACTIONS = {'zorblat': 'grind'}" in text
    assert "STORE_BLOCK_DEFAULT_ACTIONS = {}" in render_block_inputs_module()


# -- what the coder is told names no product --------------------------------


def test_coder_text_names_no_product_and_no_session():
    blob = "\n".join([reuse_accept_rules_text(), reuse_accept_brief_contract()])
    assert not re.search(r"sess_[0-9a-f]{6,}", blob)
    for word in ("VetCare", "VetClinic", "Steward", "patient_records", "appointment_scheduling",
                 "estate_registry", "InsureDistribute", "extract"):
        assert word not in blob, word


def test_rules_text_lists_this_builds_capabilities_only():
    text = reuse_accept_rules_text(["zorblat_intake"])
    assert "- zorblat_intake" in text


# -- against the real Store, when CI has it checked out ----------------------


_STORE = os.environ.get("CEREBRUM_BLOCKS_ROOT", "")


@pytest.mark.skipif(not (_STORE and Path(_STORE, "block_registry").is_dir()), reason="Store not checked out")
def test_every_harvested_default_is_an_action_the_block_accepts():
    """No block may be handed an action its own code rejects -- the failure
    the hand table caused. For every block whose code dispatches on explicit
    comparisons, the harvested default must be one of them or its declared
    default."""
    root = Path(_STORE)
    bad = []
    for reg in sorted((root / "block_registry").iterdir()):
        impl = root / "app" / "blocks" / f"{reg.name}.py"
        if not impl.is_file():
            continue
        code = impl.read_text(encoding="utf-8", errors="replace")
        compared = set(re.findall(r"""action\s*==\s*['"]([A-Za-z_]\w*)['"]""", code))
        if not compared:
            continue
        got = harvest_block_default_action(reg.name)
        declared = set(m[1] for m in re.findall(r"""\.get\(\s*['"](action|operation)['"]\s*,\s*['"]([A-Za-z_]\w*)['"]""", code))
        if got and got not in compared | declared:
            bad.append((reg.name, got, sorted(compared)[:6]))
    assert bad == [], bad
