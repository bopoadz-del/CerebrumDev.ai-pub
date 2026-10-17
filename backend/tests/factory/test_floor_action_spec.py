"""The shared typed-action spec cannot drift from what the backend accepts.

Owner ruling 2026-10-05: the post-deploy smoke and the browser e2e share ONE
typed-action client, built from one committed file
(``frontend/src/api/floor_actions.json``) that the SPA's own ``FloorAction``
type is derived from too. This test is the drift gate: the file must equal
``floor_actions.action_spec()`` -- the actions ``parse_action`` accepts and
the value each one takes. Regenerate with:

    cd backend && python -c "import json; from app.factory.floor_actions import action_spec; \
open('../frontend/src/api/floor_actions.json', 'w', encoding='utf-8', newline='\\n')\
.write(json.dumps(action_spec(), indent=2) + '\\n')"
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory import floor_actions as fa

REPO = Path(__file__).resolve().parents[3]
SPEC_FILE = REPO / fa.ACTION_SPEC_PATH


def _committed() -> dict:
    return json.loads(SPEC_FILE.read_text(encoding="utf-8"))


def test_the_committed_spec_is_exactly_what_the_backend_accepts():
    assert _committed() == fa.action_spec(), (
        f"{fa.ACTION_SPEC_PATH} drifted from app.factory.floor_actions; "
        "regenerate it (see this module's docstring)"
    )


def test_every_spec_action_parses_and_nothing_else_does():
    names = set(_committed()["actions"])
    assert names == {a.value for a in fa.FloorAction}
    for name in names:
        assert fa.parse_action(name).value == name
    with pytest.raises(fa.FloorActionError):
        fa.parse_action("zorblat_the_platform")


def test_value_shapes_match_the_backend_rules():
    for name, rule in _committed()["actions"].items():
        action = fa.FloorAction(name)
        assert (rule["value"] is not None) == (action in fa.VALUE_REQUIRED), name
    levels = _committed()["actions"][fa.FloorAction.SET_BUILD_LEVEL.value]["value"]["one_of"]
    assert levels == [level.value for level in fa.BuildLevel]
    for level in levels:
        assert fa.parse_level(level).value == level
    with pytest.raises(fa.FloorActionError):
        fa.parse_level("standard")


def test_the_smoke_and_the_e2e_build_requests_from_the_spec_file():
    """Both clients build typed requests from the spec file, so neither can
    send an action the backend does not define."""
    smoke = (REPO / "scripts" / "post_deploy_smoke.py").read_text(encoding="utf-8")
    e2e = (REPO / "frontend" / "e2e" / "floorActionClient.ts").read_text(encoding="utf-8")
    assert "floor_actions.json" in smoke
    assert "floor_actions.json" in e2e
