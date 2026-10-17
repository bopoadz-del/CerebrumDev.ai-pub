"""A handler's code and prose never declare a field; the schema does.

The miner once had an "X is missing" pattern (for error strings like
VetConnect's "pet_id is missing and must be a non-empty string"). It also
fired on prose: a handler whose refusal says "any of the four is missing"
yielded the field "four", and a docstring about "figures" yielded "figure".
Those junk fields went into the contract, the route then 422'd on
"unknown field(s): figure, four" (Cerebrum VenueOps, ledger STEP 21).

The rule (owner ruling 2026-10-05): required fields come ONLY from the
declared schema. A check in the handler (``"metric" not in payload``,
``if not pet_id``) and the words of its refusal declare nothing: the
alignment adds no field and makes none required.
"""

from __future__ import annotations

from app.factory.build.block_inputs import align_spec_to_handler_source, handler_field_contracts

HANDLER = '''
"""Ops dashboards. The four figures the venue director reads.

NEVER reports a figure it did not compute from stored records.
"""

def handle(payload):
    if "metric" not in payload:
        raise ValueError("metric is required")
    scope = payload.get("cost_scope")
    if scope is None:
        # Refusal prose the miner must not read as field names:
        raise ValueError("any of the four is missing; provide the figure")
    return {"ok": True}
'''


def test_prose_words_are_not_mined_as_fields():
    contracts = handler_field_contracts(HANDLER)
    assert "four" not in contracts and "figure" not in contracts, sorted(contracts)


def test_a_handler_check_declares_no_field_only_the_schema_does():
    declared = {"entity": "ops_dashboard", "fields": [{"name": "metric", "type": "str", "required": True}]}
    aligned, changed = align_spec_to_handler_source(declared, HANDLER)
    assert changed == []
    assert [f["name"] for f in aligned["fields"]] == ["metric"]
    # A check on an undeclared key adds nothing.
    aligned, _ = align_spec_to_handler_source({"fields": []}, HANDLER)
    assert aligned["fields"] == []


def test_a_vetconnect_error_string_field_is_not_made_required():
    handler = '''
def handle(payload):
    pet_id = payload.get("pet_id")
    if not pet_id:
        raise ValueError("pet_id is missing and must be a non-empty string")
    return {"ok": True}
'''
    declared = {"fields": [{"name": "pet_id", "type": "str", "required": False}]}
    aligned, _ = align_spec_to_handler_source(declared, handler)
    assert aligned["fields"] == [{"name": "pet_id", "type": "str", "required": False}]
