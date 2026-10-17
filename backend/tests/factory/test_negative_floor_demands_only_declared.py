"""The negative floor may demand refusals only for what the spec declares.

Live failure (Floor, 2026-09-28): ``mega_event_history_explorer accepted a
payload with no question`` -- FACTORY_FAULT, build halted. The generator's
``_required_field`` had a fallback: when NO field was marked required it
picked ``fields[0]`` anyway, and the emitted test then demanded the handler
REFUSE a payload missing an OPTIONAL field, under an assertion message that
misstated it as required. Accepting an optional-field-omitted payload is
correct handler behaviour, so the counter-case was unwinnable by
construction and every all-optional capability failed the floor.

The rule: a drop-field case exists only for a field the spec marks
``required``. A spec with fields but none required gets the empty-payload
case instead -- the same policy the generator already applies to a spec
with no fields at all ("Nothing at all is not a record").
"""

from __future__ import annotations

from app.factory.build.negative_floor import render_negative_tests


def _render(spec):
    cid = "mega-event-history-explorer"
    sample = {f["name"]: "x" for f in spec.get("fields", [])}
    return render_negative_tests({cid: spec}, {cid: sample})


def test_no_drop_field_case_when_nothing_is_required():
    suite = _render(
        {
            "entity": "history_query",
            "fields": [
                {"name": "question", "type": "str", "required": False},
                {"name": "event_id", "type": "str"},
            ],
        }
    )
    assert "refuses_a_missing_required_field" not in suite, (
        "the floor demands refusal of an OPTIONAL field -- unwinnable by "
        "construction (live: 'accepted a payload with no question')"
    )
    assert "refuses_an_empty_payload" in suite, (
        "an all-optional spec still owes a counter-case; the empty payload "
        "is the one that needs no field name"
    )


def test_drop_field_case_kept_for_a_declared_required_field():
    suite = _render(
        {
            "entity": "history_query",
            "fields": [
                {"name": "question", "type": "str", "required": True},
                {"name": "event_id", "type": "str"},
            ],
        }
    )
    assert "refuses_a_missing_required_field" in suite
    assert "with no question" in suite


def test_no_fields_at_all_still_gets_the_empty_payload_case():
    suite = _render({"entity": "history_query", "fields": []})
    assert "refuses_an_empty_payload" in suite
