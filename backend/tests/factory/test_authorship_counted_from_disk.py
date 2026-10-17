"""Authorship is counted from the handlers on disk, never from the writer's own
manifest (FACTORY defect, release cycle 9).

Live: vineyard anchor (sess_a7c02f0cf81a4178, build/plt_7232365d00e34f53 @
d17fa2a0): CODE, PRODUCT and STORE all PASS (Store gate 22/22, 8 capabilities
round-tripped, the product's own acceptance.py "authorship_floor: authored=8"),
yet the build was graded "FACTORY_CODE_CLI_THIN_AUTHORSHIP: authorship is below
the full-pilot floor (action_py=0, cli_authored_ids=0, need >=5, n_required=8)"
and its package refused.

The eight handlers were on disk, each stamped ``AUTHORED_BY = "codewhale
exec"`` -- the WRITER gate counted them (it passed). But the build status read
authorship from docs/build_provenance.json, and the writer had written that
manifest itself (keys no Factory emitter writes; no "written by the factory"
note on the ledger), listing models, migrations and docs -- not one handler.
The Factory leaves an agent-written manifest alone, so the grade was whatever
the writer's self-description said. The WRITER gate's rule is the right one:
"counted from the workspace files the writer actually produced, never from the
writer's own status claim". The status now follows it: a handler's own
AUTHORED_BY marker is its attribution -- a stamped handler the manifest leaves
out counts, and one the manifest claims whose stamp names another source does
not.

Synthetic products only: nothing here is a real blueprint or capability.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.authorship import full_pilot_authorship_from
from app.factory.build_jobs import _authorship

AGENT = "codewhale exec"
CAPS = [f"cap_{c}" for c in "abcdefgh"]


def _product(root: Path, manifest: dict, handlers: dict) -> Path:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "build_provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    actions = root / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    (actions / "__init__.py").write_text("", encoding="utf-8")
    for cid, source in handlers.items():
        stamp = f"AUTHORED_BY = {source!r}\n" if source else ""
        (actions / f"{cid}.py").write_text(f'"""{cid}."""\n{stamp}CAPABILITY_ID = {cid!r}\n', encoding="utf-8")
    return root


def _writer_manifest() -> dict:
    """The live shape: the writer's own manifest, naming everything but the handlers."""
    return {
        "schema": "writer-manifest",
        "written_by": "the writer",
        "n_required": len(CAPS),
        "artifact_sources": {
            "app/models.py": f"coder CLI ({AGENT})",
            "app/jobs.py": f"coder CLI ({AGENT})",
            "alembic/versions/0001_baseline.py": f"coder CLI ({AGENT})",
            "docs/openapi.json": f"coder CLI ({AGENT})",
        },
    }


def test_handlers_the_writers_manifest_leaves_out_still_count(tmp_path):
    out = _product(tmp_path, _writer_manifest(), {c: AGENT for c in CAPS})

    record = _authorship(out)["authorship"]

    assert record["action_py"] == len(CAPS), record
    floor = full_pilot_authorship_from({"state": "succeeded", **_authorship(out)}, out)
    assert floor.meets_floor and not floor.below_floor, floor


def test_a_handler_the_manifest_claims_but_the_file_does_not_is_not_counted(tmp_path):
    manifest = {
        "n_required": 2,
        "artifact_sources": {f"app/actions/{c}.py": f"coder CLI ({AGENT})" for c in CAPS[:2]},
    }
    out = _product(
        tmp_path, manifest, {CAPS[0]: AGENT, CAPS[1]: "deterministic contract template"}
    )

    record = _authorship(out)["authorship"]

    assert record["action_py"] == 1, record
    assert record["templated_actions"] == 1, record


def test_an_unstamped_handler_the_manifest_does_not_claim_is_not_the_agents(tmp_path):
    out = _product(tmp_path, {"n_required": 1, "artifact_sources": {}}, {CAPS[0]: ""})
    assert _authorship(out)["authorship"]["action_py"] == 0
