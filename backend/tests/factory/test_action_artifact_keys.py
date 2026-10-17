"""An action handler counts whether its artifact key is an id or a path.

``artifact_sources`` in docs/build_provenance.json is keyed two ways in
practice: by bare capability id, and by the handler's file path
(``app/actions/<id>.py``) -- the factory's own CodeWhale manifest writes
paths, and so do coding agents when nothing tells them otherwise. The
grader accepted only the bare id, so every path-keyed handler was
rejected. Live build sess_617f60024df24a4e (bakery-operations) went 13/13
with CODE/PRODUCT/STORE all PASS and 8 agent-written handlers on disk,
graded action_py=0, fell "below the full-pilot floor", and had
pilot_ready overwritten to False -- a Store-green build shown as a
code-cycle prototype. Whether a build graded pilot-ready depended on how
the agent happened to spell its manifest keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build.authorship import (
    action_artifact_id,
    action_artifact_ids,
    full_pilot_authorship_from,
)
from app.factory.build_jobs import _authorship

CAPS = [
    "branch_and_consolidated_operations",
    "branch_books_and_accounting",
    "delivery_and_dispatch",
    "document_grounded_knowledge",
    "events_supply",
    "inventory_and_replenishment",
    "order_follow_up",
    "outlook_branch_messaging_integration",
]
AGENT = "coder CLI (codewhale exec)"


@pytest.mark.parametrize(
    "key, expected",
    [
        ("record_checkin", "record_checkin"),
        ("app/actions/record_checkin.py", "record_checkin"),
        ("app" + "\\" + "actions" + "\\" + "record_checkin.py", "record_checkin"),
        ("  app/actions/record_checkin.py  ", "record_checkin"),
        ("app/actions/__init__.py", None),
        ("app/models.py", None),
        ("app/routes.py", None),
        ("app/actions/record_checkin.tsx", None),
        ("vendor/blocks/app/actions/x.py", None),
        ("readme", None),
        ("", None),
        (None, None),
    ],
)
def test_both_spellings_name_the_same_capability(key, expected):
    assert action_artifact_id(key) == expected


def test_ids_are_deduplicated_across_spellings():
    keys = ["record_checkin", "app/actions/record_checkin.py", "app/models.py", "list_today"]
    assert action_artifact_ids(keys) == ["record_checkin", "list_today"]


def _write_manifest(root: Path, manifest: dict) -> Path:
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "build_provenance.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_the_live_bakery_manifest_grades_eight_handlers(tmp_path):
    """The exact shape the agent shipped on sess_617f60024df24a4e."""
    out = _write_manifest(
        tmp_path,
        {
            "schema_version": "build_provenance.v1",
            "written_by": "the factory WRITER role (codewhale exec)",
            "engine": "codewhale_worker",
            "n_required": 8,
            "artifact_sources": {f"app/actions/{c}.py": AGENT for c in CAPS},
            "handlers": {c: f"app/actions/{c}.py" for c in CAPS},
        },
    )

    record = _authorship(out)["authorship"]

    assert record["action_py"] == 8, record
    assert record["agent_written"] == 8
    assert record["templated"] == 0


def test_the_factory_written_manifest_grades_its_handlers(tmp_path):
    """roles_handlers writes path keys when the agent emits no manifest."""
    out = _write_manifest(
        tmp_path,
        {
            "schema_version": "build_provenance.v1",
            "engine": "codewhale_worker",
            "artifact_sources": {f"app/actions/{c}.py": AGENT for c in CAPS[:3]},
            "authorship": {"action_py": 3, "agent_artifacts": CAPS[:3]},
            "n_required": 3,
            "written_by": "factory (the agent did not emit a manifest)",
        },
    )

    assert _authorship(out)["authorship"]["action_py"] == 3


def test_a_path_keyed_build_meets_the_full_pilot_floor(tmp_path):
    """The demotion itself: full_pilot_authorship_from re-filters the ids."""
    out = _write_manifest(
        tmp_path,
        {
            "n_required": 8,
            "artifact_sources": {f"app/actions/{c}.py": AGENT for c in CAPS},
        },
    )
    status = {"state": "succeeded", "pilot_ready": True, **_authorship(out)}

    floor = full_pilot_authorship_from(status, out)

    assert floor.action_py == 8
    assert floor.meets_floor, floor
    assert not floor.below_floor


def test_templated_handlers_still_do_not_count(tmp_path):
    """Normalising the spelling must not launder templated output."""
    out = _write_manifest(
        tmp_path,
        {
            "n_required": 2,
            "artifact_sources": {
                "app/actions/a.py": "template (deterministic)",
                "app/actions/b.py": "factory-grounded persist",
            },
        },
    )

    record = _authorship(out)["authorship"]

    assert record["action_py"] == 0
    assert record["agent_written"] == 0


def test_a_green_path_keyed_build_keeps_pilot_ready(tmp_path):
    """End state on the Floor: the ledger's pilot_ready must survive grading."""
    from app.factory.build.level_grade import attach_level_grade

    out = _write_manifest(
        tmp_path,
        {
            "n_required": 8,
            "artifact_sources": {f"app/actions/{c}.py": AGENT for c in CAPS},
        },
    )
    status = {
        "state": "succeeded",
        "cycle": "pilot",
        "pilot_ready": True,
        **_authorship(out),
    }

    attach_level_grade(status, out)

    blockers = status["level_grade"].get("blockers") or []
    assert not any("full-pilot floor" in b for b in blockers), blockers
    assert status["pilot_ready"] is True, blockers
