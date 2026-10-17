"""The Factory's provenance fields are the Factory's, whoever wrote the file.

Live 2026-10-08 (cycle 2, the rotation pick that sends country/currency,
build/plt_464389e32e544810, commit 22e6be03 "factory: WRITER passed"): the
writer authored its own ``docs/provenance/provenance.json`` (product_id,
schema_version, sources, bindings -- no commit fields; the writer cannot know
them). The CodeWhale path converges in gap-fill mode, saw the file present and
kept it, so STORE_MANAGER failed ``provenance_complete``: "factory_commit=
unknown, blocks_commit=unknown". Only the Factory knows which Factory and which
Store produced a build, so those fields are always the Factory's to write; the
writer's own keys are kept beside them.

Same evidence, other builds of that cycle (build/plt_5b8166c1a43c4622):
``blocks_commit`` read the FACTORY's sha. ``git_head`` falls back to the
deploy's RENDER_GIT_COMMIT when a directory has no .git -- right for the
Factory, a silent lie for a Store checkout without one.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.build_provenance import resolve_blocks_commit
from app.factory.build.converge import converge_writer_emitters
from app.factory.build.gates import GateContext, gate_provenance_complete
from app.factory.build.roles_models import RoleContext
from app.factory.build.workspace import RoleWorkspace

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
PROV = Path("docs") / "provenance" / "provenance.json"

WRITER_DOC = {
    "product_id": "product",
    "schema_version": "1.0.0",
    "sources": ["audit"],
    "bindings": [{"capability_id": "c", "block_ids": ["audit"], "strategy": "REUSE"}],
}


def _ctx(tmp_path: Path) -> RoleContext:
    from app.factory.product_architect import plan_blueprint as _plan

    blueprint = load_blueprint(SMOKE)
    out = tmp_path / "build"
    out.mkdir(parents=True, exist_ok=True)
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=RoleWorkspace(BuildRole.WRITER, out),
        blueprint=blueprint,
        plan=_plan(blueprint),
        state={"factory_commit": "f" * 40, "blocks_commit": "b" * 40},
    )


def _writer_wrote_provenance(ctx: RoleContext) -> Path:
    path = Path(ctx.workspace.destination) / PROV
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(WRITER_DOC, indent=2) + "\n", encoding="utf-8")
    return path


def test_a_writer_authored_provenance_still_says_what_produced_the_build(tmp_path):
    ctx = _ctx(tmp_path)
    path = _writer_wrote_provenance(ctx)

    converge_writer_emitters(ctx, fill_gaps_only=True)

    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["factory_commit"] == "f" * 40
    assert doc["blocks_commit"] == "b" * 40
    # The writer's own keys survive beside the Factory's.
    assert doc["bindings"] == WRITER_DOC["bindings"]
    assert doc["sources"] == WRITER_DOC["sources"]
    gate = gate_provenance_complete(GateContext(role=BuildRole.STORE_MANAGER,
                                                workspace=Path(ctx.workspace.destination)))
    assert gate.ok, gate.detail


def test_converging_twice_changes_nothing(tmp_path):
    ctx = _ctx(tmp_path)
    path = _writer_wrote_provenance(ctx)
    converge_writer_emitters(ctx, fill_gaps_only=True)
    once = path.read_bytes()

    converge_writer_emitters(ctx, fill_gaps_only=True)

    assert path.read_bytes() == once


def test_the_store_commit_is_never_the_factorys_deploy_sha(tmp_path, monkeypatch):
    deploy_sha = "d" * 40
    monkeypatch.setenv("RENDER_GIT_COMMIT", deploy_sha)
    store = tmp_path / "store"  # a Store tree with no .git of its own
    store.mkdir()

    class _C:
        state: dict = {}
        blocks_root = store

    assert resolve_blocks_commit(_C()) != deploy_sha
