"""The CodeWhale writer leaves a complete platform, graded on its own work.

Two grade blockers that every CodeWhale build carried, found on FinOps
(sess_065fc3eac75c4f62), which passed 13/13 in Docker:

1. "missing founding files: app/agents/manifests, app/workflows,
   app/connectors, product-dna, docs/provenance, docs/certification".
   run_writer converges those ProductGenerator classes far below its
   CodeWhale branch, so production never reached the call. Converge also
   owns the frontend tree and the agent writes its own App.tsx, so the
   CodeWhale path fills gaps only.

2. "authorship is overwhelmingly templated (near-zero agent_written)" at
   13 vs 13 -- the whole-artifact comparison counted the factory's own
   substrate against the agent, and the factory writes more of it every
   release. The rule now judges the capability work.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import BuildRole
from app.factory.build.converge import converge_writer_emitters
from app.factory.build.level_grade import _thin_templated_authorship
from app.factory.build.roles_models import RoleContext
from app.factory.build.workspace import RoleWorkspace

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

AGENT_APP_TSX = "// authored by the coding agent, not the generator stub\n"


def _ctx(tmp_path: Path) -> RoleContext:
    from app.factory.product_architect import plan_blueprint as _plan

    blueprint = load_blueprint(SMOKE)
    out = tmp_path / "build"
    out.mkdir(parents=True, exist_ok=True)
    workspace = RoleWorkspace(BuildRole.WRITER, out)
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=workspace,
        blueprint=blueprint,
        plan=_plan(blueprint),
        state={"factory_commit": "test", "blocks_commit": "test"},
    )


# -- founding classes ---------------------------------------------------------

def test_gap_fill_writes_the_missing_founding_classes(tmp_path):
    ctx = _ctx(tmp_path)
    out = Path(ctx.workspace.destination)

    result = converge_writer_emitters(ctx, fill_gaps_only=True)

    assert result["ok"], result
    for rel in (
        "app/agents/manifests",
        "app/workflows",
        "app/connectors",
        "product-dna",
        "docs/provenance",
        "docs/certification",
    ):
        assert (out / rel).exists(), f"{rel} still missing: {result}"


def test_gap_fill_never_overwrites_what_the_agent_wrote(tmp_path):
    ctx = _ctx(tmp_path)
    out = Path(ctx.workspace.destination)
    app_tsx = out / "frontend" / "src" / "App.tsx"
    app_tsx.parent.mkdir(parents=True, exist_ok=True)
    app_tsx.write_text(AGENT_APP_TSX, encoding="utf-8")

    converge_writer_emitters(ctx, fill_gaps_only=True)

    assert app_tsx.read_text(encoding="utf-8") == AGENT_APP_TSX, (
        "converge overwrote the agent's frontend with the generator stub"
    )


def test_the_in_process_path_still_converges_wholesale(tmp_path):
    """Default behaviour is unchanged: it owns those trees on that path."""
    ctx = _ctx(tmp_path)
    out = Path(ctx.workspace.destination)
    app_tsx = out / "frontend" / "src" / "App.tsx"
    app_tsx.parent.mkdir(parents=True, exist_ok=True)
    app_tsx.write_text(AGENT_APP_TSX, encoding="utf-8")

    converge_writer_emitters(ctx)

    assert app_tsx.read_text(encoding="utf-8") != AGENT_APP_TSX


def test_the_codewhale_path_calls_converge_in_gap_fill_mode():
    from app.factory.build import roles_handlers

    src = inspect.getsource(roles_handlers._run_writer_via_codewhale_worker)
    assert "converge_writer_emitters(ctx, fill_gaps_only=True)" in src, (
        "the CodeWhale writer does not converge -- every build it produces "
        "grades 'missing founding files'"
    )


# -- the templated rule -------------------------------------------------------

def _thin(**authorship) -> bool:
    return _thin_templated_authorship({"authorship": authorship})


def test_a_pass_that_authored_every_handler_is_not_thin():
    """FinOps: 8 of 8 handlers agent-written, 13 factory substrate files."""
    assert not _thin(
        artifacts=26, agent_written=13, templated=13,
        action_artifacts=8, action_py=8, templated_actions=0,
    )


def test_more_factory_substrate_alone_never_makes_a_pass_thin():
    assert not _thin(
        artifacts=90, agent_written=8, templated=82,
        action_artifacts=8, action_py=8, templated_actions=0,
    )


def test_authoring_no_handler_is_still_thin():
    assert _thin(
        artifacts=11, agent_written=9, templated=2,
        action_artifacts=8, action_py=0, templated_actions=8,
    )


def test_a_templated_majority_of_handlers_is_still_thin():
    assert _thin(
        artifacts=11, agent_written=9, templated=2,
        action_artifacts=8, action_py=3, templated_actions=5,
    )


def test_a_near_silent_writer_is_still_thin():
    assert _thin(artifacts=24, agent_written=1, templated=23)


def test_the_old_whole_artifact_rule_still_applies_without_action_counts():
    assert _thin(artifacts=24, agent_written=8, templated=16)


def test_the_counts_the_rule_needs_are_published(tmp_path):
    import json

    from app.factory.build_jobs import _authorship

    docs = tmp_path / "docs"
    docs.mkdir(parents=True)
    (docs / "build_provenance.json").write_text(
        json.dumps(
            {
                "artifact_sources": {
                    "app/actions/a.py": "coder CLI (codewhale exec)",
                    "app/actions/b.py": "template (deterministic)",
                    "app/authority.py": "factory WRITER grounded emit",
                }
            }
        ),
        encoding="utf-8",
    )

    rec = _authorship(tmp_path)["authorship"]

    assert rec["action_artifacts"] == 2
    assert rec["action_py"] == 1
    assert rec["templated_actions"] == 1
