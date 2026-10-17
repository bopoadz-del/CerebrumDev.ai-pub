"""The Store gate's workflow on a build branch is ``main``'s, never the workspace's.

Live 2026-10-05 (248fda0b, cerebrum-builds build/sess_8926aa2d47144f22-*):
the seed commit replaced main's current store-gate.yml (which runs bandit and
exports STORE_AUDIT_CLEAN) with a stale copy the workspace carried, so the
dispatched gate ran the old workflow and ``audit_clean`` read "unmeasured" --
21/22 on a product nobody had audited. ``workflow_dispatch`` runs the workflow
file of the dispatched ref, so whatever the overlay leaves at STORE_GATE_PATH
IS the gate.
"""

from __future__ import annotations

from app.factory.build import branch_attach
from app.factory.build.builds_push import STORE_GATE_PATH, _sync_workspace_onto_tree

MAIN_GATE = "name: Store acceptance gate\n# main's current gate: runs bandit\n"
STALE_GATE = "name: Store acceptance gate\n# a stale copy without the audit step\n"


def _main_tree(dest):
    gate = dest / STORE_GATE_PATH
    gate.parent.mkdir(parents=True)
    gate.write_text(MAIN_GATE, encoding="utf-8")
    (dest / ".github" / "workflows" / "ci.yml").write_text("main's ci\n", encoding="utf-8")


def test_a_workspace_copy_never_replaces_mains_gate_workflow(tmp_path):
    src, dest = tmp_path / "ws", tmp_path / "branch"
    _main_tree(dest)
    stale = src / STORE_GATE_PATH
    stale.parent.mkdir(parents=True)
    stale.write_text(STALE_GATE, encoding="utf-8")
    (src / "app").mkdir()
    (src / "app" / "main.py").write_text("x", encoding="utf-8")

    _sync_workspace_onto_tree(src, dest)

    assert (dest / STORE_GATE_PATH).read_text(encoding="utf-8") == MAIN_GATE


def test_the_products_own_ci_workflow_still_ships(tmp_path):
    # The merge exists so the product's stamped ci.yml (tests + pip-audit +
    # bandit) reaches the branch; protecting the gate must not undo that.
    src, dest = tmp_path / "ws", tmp_path / "branch"
    _main_tree(dest)
    ci = src / ".github" / "workflows" / "ci.yml"
    ci.parent.mkdir(parents=True)
    ci.write_text("product ci: pytest + pip-audit + bandit\n", encoding="utf-8")

    _sync_workspace_onto_tree(src, dest)

    assert (dest / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").startswith("product ci")
    assert (dest / STORE_GATE_PATH).read_text(encoding="utf-8") == MAIN_GATE


def test_the_gate_path_has_one_definition():
    assert branch_attach.STORE_GATE_PATH is STORE_GATE_PATH
