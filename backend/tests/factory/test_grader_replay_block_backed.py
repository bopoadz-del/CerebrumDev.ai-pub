"""FG-7c on the FG-3 replay harness: a block-backed capability in the
invented writer layer is judged only when its declared block can run.

The tree is composed by the Factory's own code (the CLONER's vendoring from
the pinned Store, every registered stamp) plus the invented writer layer.
One capability's handler is replaced by a block-backed one: it declares a
block the CLONER vendored (read from the build's own ``blocks.lock.json``),
reaches it through the product's own loader, and fails closed. Two inputs:

* the block runs in the probe environment -- the handler is judged, and its
  honest error is the writer's handler_error;
* the block cannot run there (a package it imports is not installed) -- the
  capability is not judgeable: ungraded, owed by the Factory, no writer row,
  and the tree is not certified.

Nothing here names a product, capability, block or package the grader
knows: the capability and block are read from the composed tree, and the
missing package is invented.
"""

from __future__ import annotations

import pytest

from app.factory.build.image_sufficiency import locked_paths
from app.factory.build.product_grader import function_grader as fg
from tests.factory import grader_replay as gr
from tests.factory.test_grader_replay_clean_tree import composed, tree  # noqa: F401,F811

#: The loader's per-block entry file (``app.dispatch.load_block``).
ENTRY = "block.py"


def _vendored_block(composed_tree):
    """A block the CLONER vendored whose entry file the tree carries."""
    for bid, rel in sorted(locked_paths(composed_tree.root).items()):
        if (composed_tree.root / rel / ENTRY).is_file():
            return bid, f"{rel}/{ENTRY}"
    pytest.fail("the composed tree vendors no loadable block")


def _with_factory_loader(composed_tree):
    """The loader every Factory-rendered product carries, written by the
    Factory's own renderer the way the handlers role writes it in a live
    build (the replay composes no handlers role, so its tree has none)."""
    from app.factory.build.roles_handlers import _render_dispatch

    rel = "app/dispatch.py"
    (composed_tree.root / rel).write_text(_render_dispatch(), encoding="utf-8")
    composed_tree.layers[rel] = gr.RUNNER


def _block_backed(bid):
    # It reaches its block through the product's own loader and never runs
    # it, so the case does not depend on what a Store block does at run time
    # (run-time causes are covered with invented blocks in
    # test_function_grader_block_runnable.py).
    return (
        "from app.dispatch import load_block\n"
        f"BLOCK_IDS = [{bid!r}]\n"
        "def handle(payload):\n"
        "    try:\n"
        f"        load_block({bid!r})\n"
        "    except Exception as exc:\n"
        "        return {'ok': False, 'error': 'block failed: %s' % type(exc).__name__}\n"
        "    return {'ok': False, 'error': 'block answer not accepted'}\n"
    )


@pytest.mark.parametrize("tree", [gr.MEETS_FLOOR], indirect=True)
@pytest.mark.parametrize("block_runs", [True, False])
def test_a_fail_closed_block_backed_handler_is_judged_only_when_its_block_runs(tree, monkeypatch, block_runs):  # noqa: F811
    cid = tree.capability_ids[0]
    bid, entry = _vendored_block(tree)
    _with_factory_loader(tree)
    tree.write_writer_file(gr.handler_rel(cid), _block_backed(bid))
    if not block_runs:
        path = tree.root / entry
        path.write_text("import grader_probe_absent_package\n" + path.read_text(encoding="utf-8"), encoding="utf-8")

    replay = gr.grade(tree, monkeypatch)

    assert not replay.certified, "a fail-closed handler never certifies"
    verdict = replay.verdict
    row = f"function grade FAIL: {cid}"
    classes = {r.get("class") for r in verdict.reasons if r.get("capability") == cid and r.get("layer") == "function"}
    functions = replay.grade["layer1"]["functions"]
    if block_runs:
        assert row in verdict.writer_items, verdict.items
        assert classes == {"handler_error"}, verdict.reasons
        (judged,) = [f for f in functions["functions"] if f["capability_id"] == cid]
        assert judged["detail"] == fg.BLOCK_UNAVAILABLE and judged["blocks_cannot_run"] == {}
    else:
        assert not any(cid in item for item in verdict.writer_items), verdict.writer_items
        ungraded = [u for u in verdict.ungraded if u.startswith(f"function ungraded: {cid}: ")]
        assert ungraded and fg.NOT_JUDGEABLE in ungraded[0], verdict.ungraded
        assert classes == {"ungraded"}, verdict.reasons
        (gap,) = [f for f in functions["ungraded"] if f["capability_id"] == cid]
        assert list(gap["blocks_cannot_run"]) == [bid]
        assert gap["blocks_cannot_run"][bid].startswith(fg.CANNOT_LOAD)
