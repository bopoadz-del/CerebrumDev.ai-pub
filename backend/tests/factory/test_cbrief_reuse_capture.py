"""C-BRIEF: REUSE keep-path must harvest capture default action.

Photographed Floor after tip 3b9261b (sess_e8e4ab66e6dd4765,
estate-operations / Private Estate Steward Platform): WRITER stopped
at [check:reuse_accept]:

    maintenance_and_work_order_management: capture: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)
    security_and_access_logging: capture: reuse/accept miss —

Live InsureDistribute Store-green zip (sess_d10dfc28):
app/actions/capture.py sets BLOCK_DEFAULT_ACTIONS = {'capture': 'extract'}.
vendor/blocks/capture/block.json has id capture but inputs[] are OCR
config only (no name==action / operation). Factory vendor block.py is
an adapter (get_block + execute) with no action == dispatch, so
harvest-from-source misses. Factory-known map fallback is capture →
extract (capture_v2 alias). Same compiler class as #348 / #351 —
not a per-cap handle() micro-shot.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations

import json


from app.factory.build.authority import BuildRole
from app.factory.build.reuse_accept import (
    default_action_from_block_json,
    default_action_from_source,
    harvest_block_default_action,
    harvest_block_default_actions,
)
from app.factory.build.workspace import RoleWorkspace
from tests.factory.store_paths import store_block  # noqa: E402

LIVE_SESS = "sess_e8e4ab66e6dd4765"
LIVE_INSURE_SESS = "sess_d10dfc28"
LIVE_CAPTURE_KEYWORD = "extract"
#: What the Store's capture code declares: params.get("action", "capture").
#: The old Factory table said "extract", which that code answers with
#: "Unknown action".
STORE_CAPTURE_DEFAULT = "capture"
LIVE_CAPTURE_CAPS = (
    "maintenance_and_work_order_management",
    "security_and_access_logging",
)
LIVE_CAPTURE_MISS = (
    "maintenance_and_work_order_management: capture: reuse/accept miss — "
    "no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)"
)
_FACTORY_CAPTURE_PY = (store_block("capture") / "block.py")
#: Live Store vendor / InsureDistribute zip: OCR config only, no action input.
_REGISTRY_SHAPED_CAPTURE = {
    "id": "capture",
    "inputs": [
        {
            "description": "Upload or push an image to capture and structure...",
            "name": "input",
            "required": False,
            "type": "file",
        },
        {
            "default": "tesseract",
            "name": "ocr_engine",
            "required": False,
            "type": "string",
        },
    ],
}


def test_path_and_role_workspace_harvest_capture(tmp_path):
    """Path / RoleWorkspace harvest capture from planted vendor block.json."""
    dest = tmp_path / "dest"
    dest.mkdir()
    planted = dest / "vendor" / "blocks" / "capture"
    planted.mkdir(parents=True)
    (planted / "block.json").write_text(
        json.dumps(
            {
                "id": "capture",
                "inputs": [
                    {
                        "name": "action",
                        "type": "string",
                        "default": "extract",
                        "options": ["extract", "ocr"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert harvest_block_default_action("capture", dest) == "extract"
    assert harvest_block_default_actions(["capture"], dest) == {"capture": "extract"}

    ws = RoleWorkspace(BuildRole.WRITER, dest)
    assert harvest_block_default_action("capture", workspace=ws) == "extract"
    assert harvest_block_default_actions(["capture"], workspace=ws) == {
        "capture": "extract"
    }
    # No planted vendor: the Store's own capture block decides.
    assert harvest_block_default_action("capture", workspace=tmp_path) == STORE_CAPTURE_DEFAULT


def test_registry_shaped_block_json_without_action_falls_to_factory_vendor():
    """Live Store / registry capture/block.json has no action input.

    Harvest-from-block.json misses. In-repo adapter source also misses.
    Factory vendor / Store map must fill extract rather than inventing
    an unknown id.
    """
    assert default_action_from_block_json(_REGISTRY_SHAPED_CAPTURE) is None
    assert default_action_from_source(_FACTORY_CAPTURE_PY.read_text()) is None
    assert harvest_block_default_action("capture") == STORE_CAPTURE_DEFAULT
    assert harvest_block_default_action("not_a_real_block") is None
