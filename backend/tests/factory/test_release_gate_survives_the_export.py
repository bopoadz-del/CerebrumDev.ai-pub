"""The release gate must pass on the tree the customer actually receives.

Live, sess_51acb4ce7102491f -- STORE_MANAGER reported:

    The coding agent stopped: acceptance.py in Docker 0/13.

Acceptance never ran. The product's Dockerfile ends with
``RUN python3 scripts/release_gate.py``; that script printed

    108 passed, 9 skipped
    docs/build_provenance.json: MISSING
    VERDICT: FAIL

so the IMAGE failed to build, the acceptance step was skipped, and the
parser reported a skipped run as 0/13. Two Factory decisions disagreed:
``builds_push.FACTORY_INTERNAL_PATHS`` withholds docs/build_provenance.json
from everything that ships (owner: "they stay in house"), while the release
gate the Factory writes into every product REQUIRED it.

It had happened before. The earlier fix guarded that the file exists in the
WORKSPACE -- it always does. Nothing checked the tree that SHIPS. So these
tests put a workspace through the real export filter and run the real
rendered gate on what comes out: whatever is withheld, the gate must survive.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.build.builds_push import (
    FACTORY_INTERNAL_PATHS,
    _sync_workspace_onto_tree,
)
from app.factory.build.roles_handlers import _render_release_gate

# The stamp the WRITER actually emits. This fixture used to carry
# "CODER_MODEL: authored by the coding agent", which only ever matched the
# release gate's own private marker list -- so the test passed while every
# real CodeWhale-stamped product reported 0 (live 2026-10-01).
_HANDLER = '''"""Record a thing.

Written by the factory WRITER role (codewhale exec)
"""
AUTHORED_BY = "codewhale exec"

CAPABILITY_ID = "record_thing"
'''


def _workspace(root: Path, *, suite: str = "def test_ok():\n    assert True\n") -> Path:
    files = {
        "blocks.lock.json": '{"blocks": {}}',
        "tests/test_ok.py": suite,
        "app/actions/record_thing.py": _HANDLER,
        "app/actions/__init__.py": "",
        "scripts/release_gate.py": _render_release_gate("Probe"),
    }
    # Everything the Factory keeps in house, present in the workspace exactly
    # as it is in production -- the point is what the export does with it.
    for rel in FACTORY_INTERNAL_PATHS:
        files.setdefault(rel, "{}")
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def _run_gate(tree: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/release_gate.py"],
        cwd=str(tree),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_the_gate_passes_on_the_tree_that_ships(tmp_path):
    ws = _workspace(tmp_path / "ws")
    shipped = tmp_path / "shipped"
    _sync_workspace_onto_tree(ws, shipped)

    # the premise: the in-house record really is gone from what ships
    assert not (shipped / "docs" / "build_provenance.json").exists()

    proc = _run_gate(shipped)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "VERDICT: PASS" in proc.stdout
    assert "MISSING" not in proc.stdout


def test_nothing_the_factory_withholds_is_something_the_gate_requires(tmp_path):
    """The general form. A future in-house file added to the withheld list, or
    a future requirement added to the gate, must not reopen this: the gate is
    run with EVERY withheld path absent, not just today's culprit."""
    ws = _workspace(tmp_path / "ws")
    shipped = tmp_path / "shipped"
    _sync_workspace_onto_tree(ws, shipped)

    leaked = [rel for rel in FACTORY_INTERNAL_PATHS if (shipped / rel).exists()]
    assert not leaked, leaked

    assert _run_gate(shipped).returncode == 0


def test_authorship_is_reported_from_the_tree_the_customer_has(tmp_path):
    """The stamp in each handler's own source is in the tree; the factory's
    record is not. Reported, not judged -- authorship_floor on the acceptance
    floor is the one place that is decided."""
    ws = _workspace(tmp_path / "ws")
    shipped = tmp_path / "shipped"
    _sync_workspace_onto_tree(ws, shipped)

    out = _run_gate(shipped).stdout

    assert "handlers: 1 total, 1 stamped by the coding agent" in out


def test_a_red_suite_still_fails_the_gate(tmp_path):
    """The guard on the tests above: the gate was not blunted, it stopped
    asking for a file nobody ships. What it exists to measure still decides."""
    ws = _workspace(tmp_path / "ws", suite="def test_no():\n    assert False\n")
    shipped = tmp_path / "shipped"
    _sync_workspace_onto_tree(ws, shipped)

    proc = _run_gate(shipped)

    assert proc.returncode == 1
    assert "VERDICT: FAIL" in proc.stdout


def test_a_missing_blocks_lock_still_fails_the_gate(tmp_path):
    """blocks.lock.json DOES ship, so requiring it is legitimate and stays."""
    ws = _workspace(tmp_path / "ws")
    (ws / "blocks.lock.json").unlink()
    shipped = tmp_path / "shipped"
    _sync_workspace_onto_tree(ws, shipped)

    proc = _run_gate(shipped)

    assert proc.returncode == 1
    assert "blocks.lock.json: MISSING" in proc.stdout


def test_in_house_the_factory_record_is_still_read_when_present(tmp_path):
    """Inside the Factory workspace the manifest exists, and the richer
    per-artifact report it gives is kept."""
    ws = _workspace(tmp_path / "ws")
    (ws / "docs" / "build_provenance.json").write_text(
        '{"artifact_sources": {"app/actions/a.py": "coder CLI", "app/main.py": "factory"}}',
        encoding="utf-8",
    )

    out = _run_gate(ws).stdout

    assert "artifacts: 2 total, 1 written by the coding agent" in out


@pytest.mark.parametrize("rel", sorted(FACTORY_INTERNAL_PATHS))
def test_the_rendered_gate_never_fails_on_a_withheld_path(rel):
    """Static twin of the run above: no withheld path may sit next to an
    ``ok = False`` in the gate's source."""
    source = _render_release_gate("Probe")
    name = rel.rsplit("/", 1)[-1]
    for i, line in enumerate(source.splitlines()):
        if name in line and "MISSING" in line:
            pytest.fail(f"release gate line {i + 1} still treats {rel} as required: {line.strip()}")


def test_the_template_paths_gate_requires_only_a_file_that_ships():
    """There are two emitters of scripts/release_gate.py. The template path's
    (app/factory/generator.py) requires docs/provenance/provenance.json -- a
    different file, which DOES ship, so it never had this defect. Pinned so
    that withholding that file later fails here and not in a customer's
    image build."""
    from app.factory.build.builds_push import is_exported

    assert "docs/provenance/provenance.json" not in FACTORY_INTERNAL_PATHS
    assert is_exported(Path("docs/provenance/provenance.json"))


def test_the_products_ci_reaches_the_branch_without_evicting_the_store_gate(tmp_path):
    """The gate grades the ci.yml that is ON THE BRANCH. Until 2026-09-26 the
    export skipped the workspace's .github/ wholesale to protect the inherited
    store-gate.yml, so every branch carried main's pytest-only ci.yml and
    audit_clean failed every fresh build by construction. Merge, not skip."""
    ws = _workspace(tmp_path / "ws")
    (ws / ".github" / "workflows").mkdir(parents=True)
    stamped = "name: product-ci\njobs:\n  audit:\n    steps:\n      - run: pip-audit\n      - run: bandit -ll -r app\n"
    (ws / ".github" / "workflows" / "ci.yml").write_text(stamped, encoding="utf-8")

    shipped = tmp_path / "shipped"
    (shipped / ".github" / "workflows").mkdir(parents=True)
    gate = "name: store-gate\n"
    (shipped / ".github" / "workflows" / "store-gate.yml").write_text(gate, encoding="utf-8")
    (shipped / ".github" / "workflows" / "ci.yml").write_text("name: inherited-18-line\n", encoding="utf-8")

    _sync_workspace_onto_tree(ws, shipped)

    assert (shipped / ".github" / "workflows" / "store-gate.yml").read_text(encoding="utf-8") == gate, (
        "the Store gate's own workflow must survive the overlay"
    )
    assert (shipped / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8") == stamped, (
        "the branch must carry the product's stamped ci.yml, not main's inherited one"
    )


def test_a_workspace_without_github_leaves_the_inherited_gate_untouched(tmp_path):
    """Control on the merge: nothing to overlay means nothing changes."""
    ws = _workspace(tmp_path / "ws")
    shipped = tmp_path / "shipped"
    (shipped / ".github" / "workflows").mkdir(parents=True)
    (shipped / ".github" / "workflows" / "store-gate.yml").write_text("name: store-gate\n", encoding="utf-8")

    _sync_workspace_onto_tree(ws, shipped)

    assert sorted(p.name for p in (shipped / ".github" / "workflows").iterdir()) == ["store-gate.yml"]
