"""C-BRIEF: REUSE keep-path must harvest vector_search default action.

Photographed Floor after #350 (tip 467c83e, sess_8259e197749b4441,
VetClinic Hub / veterinary-care, all-REUSE): appointment workflow
``result`` key did not recur. Export refuse PASS; pilot_zip=no.
WRITER stopped at [check:reuse_accept]:

    patient_records_management: vector_search: reuse/accept miss —
      no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)

#348 harvested formula_executor from factory vendor_blocks_mirror /
CEREBRUM_BLOCKS_ROOT block.json (including Store _v2 alias). Registry
vector_search/block.json has no inputs[].name == action (Store runtime
defaults params.operation to search). Factory vendor mirror and the
documented Store map both omitted that harvest. Same compiler class —
not a per-cap handle() micro-shot.

Do not enable FACTORY_BRIEF_HTTP_ONESHOT. Do not claim pilot_zip.
"""

from __future__ import annotations



from app.factory.build.reuse_accept import (
    default_action_from_block_json,
    default_action_from_source,
    harvest_block_default_action,
)

LIVE_SESS = "sess_8259e197749b4441"
LIVE_VECTOR_SEARCH_MISS = (
    "patient_records_management: vector_search: reuse/accept miss — "
    "no BLOCK_DEFAULT_ACTIONS entry (Unknown action: None)"
)


def test_registry_shaped_block_json_without_action_falls_to_factory_vendor():
    """Cerebrum-Blocks vector_search/block.json has only an ``input`` field.

    Harvest must still fill search from factory vendor / Store map rather
    than inventing an unknown id.
    """
    registry_shaped = {
        "id": "vector_search",
        "inputs": [
            {
                "description": "Search knowledge base...",
                "name": "input",
                "required": False,
                "type": "string",
            }
        ],
    }
    assert default_action_from_block_json(registry_shaped) is None
    store_source = (
        "async def process(self, input_data, params=None):\n"
        "    params = params or {}\n"
        '    operation = params.get("operation", "search")\n'
        "    return operation\n"
    )
    assert default_action_from_source(store_source) == "search"
    assert harvest_block_default_action("vector_search") == "search"
    assert harvest_block_default_action("not_a_real_block") is None
