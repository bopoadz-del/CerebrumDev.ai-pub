"""A block must ACCEPT the payload the coder wrote.

F11 says "declared means invoked". This says "invoked means accepted"
(owner's ruling R1b/R1d, 2026-09-01).

THE INCIDENT. ``residential-lettings`` (session
``sess_6400b6c273414352``) was the first unambiguously post-#254 build: it
booted, ``alembic upgrade head`` returned 0, ``/health`` answered 200, and
its own gate passed honestly (``pytest -m "not pilot"`` reproduced the
ledger's ``22 passed, 3 deselected`` byte for byte). It could not persist a
single record:

    unit_registry_and_vacancy_tracking  {'analytics': 'metric and value required'}
    viewing_management                  team: Team access denied; team: Team not
                                        found; workflow: workflow unknown
                                        field(s): action
    maintenance_issue_tracking          team: Team access denied
    tenancy_application_pipeline        team create_team failed: Unknown action: None
    every GET list                      {"items": [], "total": 0}

Every one of those answers passed through ``_recording_execute`` in the
WRITER gate's baseline phase -- the real blocks were being executed with the
real payloads -- and the gate recorded only the block *id*. The evidence was
in the room and nobody read it.

WHAT WAS VERIFIED LITERALLY, on ``Cerebrum-Blocks@68a145a``, not inferred:

* ``AnalyticsBlock._track_event`` reads ``data.get("metric")`` and
  ``data.get("value")`` off the payload it is handed, and answers the exact
  string ``"metric and value required"`` when either is missing.
* ``AnalyticsBlock.process`` answers ``f"Unknown action: {action}"``.
* ``analytics``' ``block.json`` declares ``input`` as a config slot, and the
  block's source contains **zero** ``.get("input")`` -- so do ``database``,
  ``team`` and ``storage``. ``workflow`` contains exactly one. That single
  fact is what separates "the wrapper is the contract" from "the wrapper is
  the bug", and it is why the fix is contract-driven rather than a
  block-by-block special case.
"""

from __future__ import annotations

import ast
from typing import Any, Dict

import pytest

from app.factory.build.roles import _DISPATCH_RUNTIME
from app.factory.build.writer_behaviour import (
    BEHAVIOUR_PROBE,
    KIND_CONTRACT,
    _render_probe,
    banner_from_records,
)

# Harvested contracts as the shipped platform carries them. Verbatim.
LIVE_CONTRACTS: Dict[str, Any] = {
    "analytics": {
        "block_id": "analytics",
        "default_action": "track_event",
        "declared_inputs": [
            {"name": "input", "type": "json", "required": False},
            {"name": "window_size", "type": "number", "required": False},
        ],
        "input_keys_read_by_block": [
            "action", "metric", "tags", "timestamp", "value",
        ],
    },
    "workflow": {
        "block_id": "workflow",
        "declared_inputs": [{"name": "input", "type": "json", "required": False}],
        # workflow is the one block that READS an "input" key.
        "input_keys_read_by_block": ["action", "block", "input", "steps"],
    },
    "database": {
        "block_id": "database",
        "declared_inputs": [{"name": "input", "type": "json", "required": False}],
        "input_keys_read_by_block": ["action", "table", "values", "where"],
    },
}


@pytest.fixture
def dispatch():
    ns: Dict[str, Any] = {"__file__": "/tmp/probe/app/dispatch.py"}
    exec(_DISPATCH_RUNTIME, ns)
    ns["BLOCK_CONTRACTS"].update(LIVE_CONTRACTS)
    return ns


# --------------------------------------------------------------------------
# R1d -- _adapt_input never passes a mismatched envelope through silently
# --------------------------------------------------------------------------

def test_the_lettings_analytics_call_is_normalised(dispatch):
    """The exact call from
    app/actions/unit_registry_and_vacancy_tracking.py:100-108."""
    out = dispatch["_adapt_input"](
        "analytics",
        {"input": {
            "name": "unit_registry_and_vacancy_tracking",
            "tags": ["flat", "vacant"],
            "metric": "monthly_rent_gbp",
            "value": 1450.0,
        }},
        "track_event",
    )
    assert out["metric"] == "monthly_rent_gbp"
    assert out["value"] == 1450.0
    assert out["tags"] == ["flat", "vacant"]
    # "name" is not a key analytics reads, so it stays a domain record.
    assert out["input"] == {"name": "unit_registry_and_vacancy_tracking"}


def test_a_flat_payload_is_left_exactly_as_written(dispatch):
    payload = {"metric": "m", "value": 1.0}
    assert dispatch["_adapt_input"]("analytics", dict(payload), "track_event") == payload


def test_the_one_block_that_reads_input_keeps_its_wrapper(dispatch):
    """workflow's source contains a .get("input"); analytics', database's,
    team's and storage's do not. Lifting workflow's record would break the
    block that is actually right."""
    payload = {"input": {"steps": [{"block": "database"}]}}
    assert dispatch["_adapt_input"]("workflow", dict(payload), "run") == payload


def test_database_record_one_level_too_deep_is_lifted(dispatch):
    out = dispatch["_adapt_input"](
        "database", {"input": {"table": "units", "values": {"a": 1}}}, "insert"
    )
    assert out == {"table": "units", "values": {"a": 1}}


def test_two_different_values_for_one_field_fails_loud(dispatch):
    """Normalising would have to choose between them, and choosing is
    inventing (F18)."""
    with pytest.raises(dispatch["DispatchContractError"]) as exc:
        dispatch["_adapt_input"](
            "analytics",
            {"metric": "top", "input": {"metric": "nested", "value": 2}},
            "track_event",
        )
    msg = str(exc.value)
    assert "envelope mismatch" in msg
    assert "metric" in msg
    assert "appears" in msg  # singular, one field


def test_the_same_value_in_both_places_is_not_a_collision(dispatch):
    out = dispatch["_adapt_input"](
        "analytics",
        {"metric": "m", "input": {"metric": "m", "value": 3}},
        "track_event",
    )
    assert out["metric"] == "m" and out["value"] == 3


def test_every_normalisation_is_recorded_not_absorbed(dispatch):
    """A silent fix is still a defect in the generated handler."""
    dispatch["_LIFTED"].clear()
    dispatch["_adapt_input"](
        "analytics", {"input": {"metric": "m", "value": 1}}, "track_event"
    )
    assert dispatch["_LIFTED"] == [("analytics", ("metric", "value"))]


def test_a_block_with_no_harvested_contract_is_untouched(dispatch):
    payload = {"input": {"anything": 1}}
    assert dispatch["_adapt_input"]("unheard_of", dict(payload), None) == payload


# --------------------------------------------------------------------------
# R1b -- the probe classifies each refusal by name
# --------------------------------------------------------------------------

def _lift_from_probe(names):
    """Exec named top-level defs/assignments out of the probe script.

    The probe is a source string run inside the GENERATED workspace, so its
    functions cannot be imported. Lifting them by AST tests the shipped
    source rather than a copy that can drift from it.
    """
    marker = "BEHAVIOUR_PROBE = r" + (chr(39) * 3)
    text = open(_probe_path(), encoding="utf-8").read()
    start = text.index(marker) + len(marker)
    src = text[start:text.index(chr(39) * 3, start)]
    tree = ast.parse(src)
    picked = [
        n for n in tree.body
        if (isinstance(n, ast.FunctionDef) and n.name in names)
        or (isinstance(n, ast.Assign)
            and any(getattr(t, "id", None) in names for t in n.targets))
    ]
    assert len(picked) == len(names), (
        "not all of %s found in the probe script" % sorted(names)
    )
    # Every miss the probe records is built by its record helpers (the miss
    # line carries its capability as a field, F1 typed findings). Lift them
    # with any function, from the same shipped source.
    helpers = [
        n for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in _MISS_HELPERS
    ]
    assert len(helpers) == len(_MISS_HELPERS), "the probe's miss helpers are missing"
    return compile(ast.Module(body=helpers + picked, type_ignores=[]), "<probe>", "exec")


#: The probe's record helpers every miss-producing function calls.
_MISS_HELPERS = ("_Miss", "_miss", "_cap_of")


@pytest.fixture
def fake_dispatch(monkeypatch):
    """A stand-in app.dispatch, RESTORED afterwards.

    Writing sys.modules directly poisoned the rest of the session: twelve
    unrelated tests failed and nine errored because they imported the fake.
    monkeypatch.setitem undoes it.
    """
    import sys
    import types

    mod = types.ModuleType("app.dispatch")
    mod.BLOCK_CONTRACTS = LIVE_CONTRACTS
    if "app" not in sys.modules:
        monkeypatch.setitem(sys.modules, "app", types.ModuleType("app"))
    monkeypatch.setitem(sys.modules, "app.dispatch", mod)
    return mod


@pytest.fixture
def classify(fake_dispatch):
    """The probe's classifier, lifted out of the script and given the
    contracts and obligations it is rendered with."""
    from app.factory.build.block_obligations import resource_obligations

    ns: Dict[str, Any] = {"RESOURCE_OBLIGATIONS": dict(resource_obligations())}
    exec(_lift_from_probe({"_classify_refusal", "_default_action"}), ns)
    return ns["_classify_refusal"]


def _probe_path() -> str:
    import app.factory.build.writer_behaviour as wb
    return wb.__file__


def test_analytics_envelope_shape_is_named(classify):
    note = classify(
        "analytics",
        {"input": {"metric": "monthly_rent_gbp", "value": 1450.0}},
        "track_event",
        {"status": "error", "error_kind": "input", "error": "metric and value required"},
    )
    assert note is not None
    assert "envelope shape" in note
    assert "metric" in note and "value" in note
    assert "CONTRACT: envelope shape" in note


def test_action_inside_the_payload_is_named(classify):
    """tenancy_application_pipeline, eleven sites, all from the free
    fallback coder model."""
    note = classify(
        "team",
        {"action": "create_team", "user_id": "system", "name": "T", "slug": "t"},
        None,
        {"status": "error", "error_kind": "input", "error": "Unknown action: None"},
    )
    assert note is not None
    assert "the action travelled inside the payload" in note
    assert "CONTRACT: unknown action" in note


def test_action_in_both_places_is_named(classify):
    """viewing_management's belt-and-braces call: the payload's "action"
    reaches a strict block as an undeclared field."""
    note = classify(
        "workflow",
        {"action": "create_team", "steps": []},
        "create_team",
        {"status": "error", "error_kind": "input", "error": "workflow unknown field(s): action"},
    )
    assert note is not None
    assert "the action travelled inside the payload" in note


def test_missing_precondition_is_named(classify):
    note = classify(
        "team", {"user_id": "u7"}, "get_team_context",
        {"status": "error", "error_kind": "input", "error": "Team access denied"},
    )
    assert note is not None
    assert "without team_id" in note
    assert "create_team" in note
    assert "CONTRACT: missing precondition" in note


def test_the_precondition_being_met_is_not_a_miss(classify):
    assert classify(
        "team",
        {"user_id": "u7", "team_id": "team_4f473e37589a69bb"},
        "get_team_context",
        {"role": "owner", "permissions": ["*"]},
    ) is None


def test_a_successful_answer_is_never_a_miss(classify):
    assert classify(
        "analytics", {"metric": "m", "value": 1}, "track_event",
        {"tracked": True, "metric": "m", "timestamp": 1788197118.7},
    ) is None


def test_a_non_dict_answer_is_never_a_miss(classify):
    assert classify("analytics", {}, "track_event", ["ok"]) is None
    assert classify("analytics", {}, "track_event", None) is None


def test_an_unrecognised_refusal_still_reports_the_block_and_the_words(classify):
    note = classify(
        "storage", {"file_id": "x"}, "retrieve", {"status": "error", "error_kind": "input", "error": "file_not_found"},
    )
    assert note is not None
    assert "storage" in note and "file_not_found" in note
    assert "(CONTRACT)" in note


def test_an_error_free_message_is_not_forced_into_a_class(classify):
    assert classify("analytics", {}, "track_event", {"note": "fine"}) is None


# --------------------------------------------------------------------------
# The gate keeps its classes apart
# --------------------------------------------------------------------------

def test_a_contract_miss_is_typed_contract_whatever_its_words():
    """The probe emits contract misses with kind "contract"; the host reads the
    kind. Text that happens to contain another class's marker changes nothing."""
    assert '("contract", contract_misses)' in BEHAVIOUR_PROBE
    line = "cap: analytics: envelope shape (CONTRACT: envelope shape) (F1) (F11)"
    recs = [{"gate_record": "halt", "kind": KIND_CONTRACT, "text": "every capability wrote a payload its blocks refuse"},
            {"gate_record": "finding", "kind": KIND_CONTRACT, "text": line}]
    assert banner_from_records(recs) == "every capability wrote a payload its blocks refuse"


def test_an_unavailable_block_is_not_the_coders_contract(classify):
    """A provider that is down answers error_kind "unavailable": not a refusal
    of the payload the coder wrote."""
    assert classify(
        "analytics", {"metric": "m"}, "track_event",
        {"status": "error", "error_kind": "unavailable", "error": "provider unreachable"},
    ) is None


# --------------------------------------------------------------------------
# The probe as rendered
# --------------------------------------------------------------------------

def test_rendered_probe_carries_the_real_obligations():
    """The probe runs inside the generated workspace, which carries no
    factory code, so the table has to be baked in."""
    rendered = _render_probe()
    assert "RESOURCE_OBLIGATIONS = {}" not in rendered
    assert "'create_team'" in rendered or '"create_team"' in rendered
    assert "team_id" in rendered
    ast.parse(rendered)


def test_rendered_probe_is_still_valid_python():
    ast.parse(_render_probe())


def test_the_probe_records_the_block_answer_not_only_the_id():
    """The regression this whole file exists for: the answers were already
    passing through _recording_execute and were being discarded."""
    assert "_classify_refusal(block_id, payload, act, result)" in BEHAVIOUR_PROBE
    assert "result = _real_execute(block_id, *a, **kw)" in BEHAVIOUR_PROBE


def test_storage_missing_precondition_is_named(classify):
    """store MINTS its own file_id and ignores the caller's, so a retrieve by
    the caller's id answers file_not_found after a store that reported
    success. Underscored literal -- it slipped every refusal marker until a
    test caught it."""
    note = classify(
        "storage", {"filename": "a.pdf"}, "retrieve",
        {"status": "error", "error_kind": "input", "error": "file_not_found"},
    )
    assert note is not None
    assert "without file_id" in note
    assert "store" in note
    assert "CONTRACT: missing precondition" in note


# --------------------------------------------------------------------------
# The probe's WIRING, not just its classifier
# --------------------------------------------------------------------------
# The classifier being right is worth nothing if _recording_execute throws the
# answer away -- which is precisely what the shipped gate did. These drive the
# real recording function with a fake block layer.

def _probe_flow(answers, cap="unit_registry_and_vacancy_tracking"):
    """Exec the probe's recording machinery against canned block answers.

    Returns ``(namespace, calls)``. ``answers`` maps block_id to the envelope
    the block returns. Requires the ``fake_dispatch`` fixture to be active.
    """
    from app.factory.build.block_obligations import resource_obligations

    calls = []

    def _fake_real_execute(block_id, *a, **kw):
        calls.append((block_id, a, kw))
        return answers.get(block_id, {"ok": True})

    ns: Dict[str, Any] = {
        "RESOURCE_OBLIGATIONS": dict(resource_obligations()),
        "_real_execute": _fake_real_execute,
    }
    exec(_lift_from_probe({"_classify_refusal", "_recording_execute",
                           "_default_action", "contract_misses", "_seen"}), ns)
    ns["_seen"]["cap"] = cap
    return ns, calls


def test_the_recording_function_captures_a_refusal(fake_dispatch):
    ns, calls = _probe_flow({"analytics": {"status": "error", "error_kind": "input", "error": "metric and value required"}})
    ns["_recording_execute"](
        "analytics",
        {"input": {"metric": "monthly_rent_gbp", "value": 1450.0}},
        action="track_event",
    )
    assert len(calls) == 1, "the real block must still be executed"
    assert len(ns["contract_misses"]) == 1
    line = ns["contract_misses"][0]
    assert line.startswith("unit_registry_and_vacancy_tracking: ")
    assert "envelope shape" in line
    # The capability travels as a field of the miss (typed record), not
    # only as text the host would have to split.
    assert ns["_cap_of"](line) == "unit_registry_and_vacancy_tracking"


def test_the_recording_function_still_returns_the_block_answer(fake_dispatch):
    ns, _ = _probe_flow({"analytics": {"status": "error", "error_kind": "input", "error": "metric and value required"}})
    out = ns["_recording_execute"]("analytics", {"input": {"metric": "m"}},
                                   action="track_event")
    assert out == {"status": "error", "error_kind": "input", "error": "metric and value required"}


def test_a_healthy_call_records_nothing_and_still_records_the_block_id(fake_dispatch):
    ns, _ = _probe_flow({"analytics": {"tracked": True}})
    ns["_recording_execute"]("analytics", {"metric": "m", "value": 1},
                             action="track_event")
    assert ns["contract_misses"] == []
    assert "analytics" in ns["_seen"]["blocks"]["unit_registry_and_vacancy_tracking"]


def test_the_same_refusal_twice_is_recorded_once(fake_dispatch):
    ns, _ = _probe_flow({"team": {"status": "error", "error_kind": "input", "error": "Team access denied"}})
    for _ in range(3):
        ns["_recording_execute"]("team", {"user_id": "u"},
                                 action="get_team_context")
    assert len(ns["contract_misses"]) == 1


def test_a_positional_action_is_read_too(fake_dispatch):
    """execute(block, payload, action) as well as action=."""
    ns, _ = _probe_flow({"team": {"status": "error", "error_kind": "input", "error": "Team access denied"}})
    ns["_recording_execute"]("team", {"user_id": "u"}, "get_team_context")
    assert ns["contract_misses"]
    assert "without team_id" in ns["contract_misses"][0]


def test_calls_outside_a_capability_are_not_attributed(fake_dispatch):
    ns, _ = _probe_flow({"team": {"status": "error", "error_kind": "input", "error": "Team access denied"}})
    ns["_seen"]["cap"] = None
    ns["_recording_execute"]("team", {"user_id": "u"}, action="get_team_context")
    assert ns["contract_misses"] == []


# --------------------------------------------------------------------------
# R1e -- the one-record round trip
# --------------------------------------------------------------------------
# "Boots and passes its own tests" is no longer enough; the product must
# remember one thing it was told. residential-lettings booted, served all
# seventeen routes, passed its own gate -- and answered every GET with
# {"items": [], "total": 0}.


def _round_trip_flow(rows, listed, get_status=200, entity="unit"):
    """Drive the probe's round-trip check against a canned store and route.

    ``rows`` is what store.list_all returns; ``listed`` is what GET answers
    with; ``entity=None`` makes the entity unreadable (no such table), which
    is how a generate-only capability presents.
    """
    ns = {}
    # The probe now authenticates its GET; the lifted check references AUTH.
    ns["AUTH"] = {"Authorization": "Bearer dev-local-token"}
    exec(_lift_from_probe({"_record_matches", "_listed_records",
                           "_check_round_trip", "_ROUND_TRIP_CHECKED"}), ns)

    class _Resp:
        status_code = get_status
        content = b"x"

        def json(self):
            return listed

    class _Client:
        def get(self, path, **kwargs):
            return _Resp()

    class _Store:
        @staticmethod
        def list_all(name, tenant_id=None):
            if rows is None:
                raise RuntimeError("no such table: %s" % name)
            return list(rows)

    ns["roundtrip_misses"] = []
    ns["findings"] = []
    ns["client"] = _Client()
    ns["store"] = _Store()
    ns["_entity_of"] = lambda cap, cls: entity
    ns["_rows"] = lambda e: (None if rows is None else len(rows))
    # Rows are read through the probe's entity resolver (the tenant the
    # route wrote under), stubbed here like the store it wraps.
    ns["_list_entity"] = lambda e: _Store.list_all(e)
    return ns


BODY = {"unit_reference": "sample", "monthly_rent_gbp": 1, "status": "vacant"}


def test_a_record_that_survives_is_not_a_miss():
    ns = _round_trip_flow([dict(BODY)], {"items": [dict(BODY)], "total": 1})
    ns["_check_round_trip"]("unit_registry", object, BODY)
    assert ns["roundtrip_misses"] == []


def test_nothing_stored_is_named():
    ns = _round_trip_flow([], {"items": [], "total": 0})
    ns["_check_round_trip"]("unit_registry", object, BODY)
    assert len(ns["roundtrip_misses"]) == 1
    line = ns["roundtrip_misses"][0]
    assert "ROUND-TRIP: nothing stored" in line
    assert "did not remember what it was told" in line
    assert ns["_cap_of"](line) == "unit_registry"


def test_the_exact_lettings_answer_is_named():
    """Stored a row, and every GET answers {"items": [], "total": 0}."""
    ns = _round_trip_flow([dict(BODY)], {"items": [], "total": 0})
    ns["_check_round_trip"]("unit_registry", object, BODY)
    assert len(ns["roundtrip_misses"]) == 1
    assert "ROUND-TRIP: empty list" in ns["roundtrip_misses"][0]
    assert "residential-lettings" in ns["roundtrip_misses"][0]


def test_a_row_that_is_not_the_posted_record_is_named():
    """The STORE half, isolated. get_status=404 removes the list route so
    only the store check can fire -- and the class is asserted exactly,
    because "wrong record returned" contains "wrong record"."""
    ns = _round_trip_flow([{"unit_reference": "something else"}], {},
                          get_status=404)
    ns["_check_round_trip"]("unit_registry", object, BODY)
    assert len(ns["roundtrip_misses"]) == 1
    line = ns["roundtrip_misses"][0]
    assert line.endswith("(ROUND-TRIP: wrong record)"), line
    assert "grew to 1 row(s)" in line


def test_a_get_that_returns_other_records_is_named():
    """The GET half, isolated: the store DOES hold the record."""
    ns = _round_trip_flow([dict(BODY)], {"items": [{"unit_reference": "other"}]})
    ns["_check_round_trip"]("unit_registry", object, BODY)
    assert len(ns["roundtrip_misses"]) == 1
    assert ns["roundtrip_misses"][0].endswith("(ROUND-TRIP: wrong record returned)")


def test_a_get_that_errors_is_named():
    ns = _round_trip_flow([dict(BODY)], {}, get_status=500)
    ns["_check_round_trip"]("unit_registry", object, BODY)
    assert "ROUND-TRIP: not readable" in ns["roundtrip_misses"][0]
    assert "500" in ns["roundtrip_misses"][0]


def test_no_list_route_leaves_the_store_half_standing():
    """A capability without a list route is not failed for lacking one --
    the store half already proved the record survived."""
    for code in (404, 405):
        ns = _round_trip_flow([dict(BODY)], {}, get_status=code)
        ns["_check_round_trip"]("unit_registry", object, BODY)
        assert ns["roundtrip_misses"] == [], code


def test_an_unreadable_entity_is_unjudged_not_failed():
    """A generate-only capability has no table. SELECT * raises, and it must
    be reported as not judgeable rather than counted as a failure."""
    ns = _round_trip_flow(None, {})
    ns["_check_round_trip"]("pdf_export", object, BODY)
    assert ns["roundtrip_misses"] == []
    assert ns["findings"] == []


def test_each_capability_is_judged_once():
    ns = _round_trip_flow([], {"items": []})
    for _ in range(4):
        ns["_check_round_trip"]("unit_registry", object, BODY)
    assert len(ns["roundtrip_misses"]) == 1


@pytest.mark.parametrize("shape", [
    {"items": [{"unit_reference": "sample"}]},
    {"records": [{"unit_reference": "sample"}]},
    {"results": [{"unit_reference": "sample"}]},
    {"data": [{"unit_reference": "sample"}]},
    {"rows": [{"unit_reference": "sample"}]},
    [{"unit_reference": "sample"}],
])
def test_every_list_shape_the_emitter_uses_is_understood(shape):
    ns = _round_trip_flow([dict(BODY)], shape)
    ns["_check_round_trip"]("unit_registry", object, BODY)
    assert ns["roundtrip_misses"] == [], shape


def test_a_value_stored_with_a_different_type_still_matches():
    """sqlite hands an int back as an int and a str back as a str; a record
    must not be called wrong for that."""
    ns = _round_trip_flow([{"monthly_rent_gbp": "1"}],
                          {"items": [{"monthly_rent_gbp": "1"}]})
    ns["_check_round_trip"]("unit_registry", object, {"monthly_rent_gbp": 1})
    assert ns["roundtrip_misses"] == []


def test_round_trip_misses_are_emitted_with_their_own_kind():
    """A forgotten record is its own class: emitted as kind "roundtrip" and
    read by kind, never recognised by a marker inside its sentence."""
    from app.factory.build.writer_behaviour import BEHAVIOUR_PROBE, KIND_ROUNDTRIP
    assert KIND_ROUNDTRIP == "roundtrip"
    assert '("roundtrip", roundtrip_misses)' in BEHAVIOUR_PROBE
    assert '_halt("roundtrip", [("roundtrip", m) for m in roundtrip_misses])' in BEHAVIOUR_PROBE


def test_the_probe_runs_the_round_trip_on_the_baseline_post():
    """Wired, not merely defined. A second POST would create a second record
    and make the count assertion meaningless, so it rides the baseline one."""
    from app.factory.build.writer_behaviour import BEHAVIOUR_PROBE
    call = "        _check_round_trip(cap_id, cls, body)"
    assert call in BEHAVIOUR_PROBE
    assert BEHAVIOUR_PROBE.count(call) == 1, "one call site, on the baseline POST"
    assert "def _check_round_trip(cap_id, cls, body):" in BEHAVIOUR_PROBE


def test_all_capabilities_forgetting_is_a_halt():
    """The GUARD, not just its message: a message left behind an ``if
    False:`` reads identically in a substring check."""
    from app.factory.build.writer_behaviour import BEHAVIOUR_PROBE
    guard = ("if roundtrip_misses and all(cid in _rt_caps "
             "for cid, _cls in targets):")
    assert guard in BEHAVIOUR_PROBE
    from app.factory.build.writer_behaviour import HALT_SENTENCES, _render_probe
    assert HALT_SENTENCES["roundtrip"] == "no capability could read back a record it stored"
    assert HALT_SENTENCES["roundtrip"] in _render_probe()
    # The halting set is counted from each miss's capability FIELD (F1 typed
    # findings), never by splitting the miss text at its first colon.
    assert "_rt_caps = set(_cap_of(m) for m in roundtrip_misses)" in BEHAVIOUR_PROBE
    assert 'm.split(":", 1)[0]' not in BEHAVIOUR_PROBE
    ns: dict = {}
    exec(_lift_from_probe(set()), ns)
    misses = [ns["_miss"]("a_cap", "text: with: colons"), ns["_miss"]("b_cap", "other")]
    assert set(ns["_cap_of"](m) for m in misses) == {"a_cap", "b_cap"}


def test_an_isolated_round_trip_miss_does_not_halt():
    """One capability forgetting must not stop a mixed workspace shipping --
    the same rule F1, F11 and schema misses already follow."""
    from app.factory.build.writer_behaviour import BEHAVIOUR_PROBE
    assert "Isolated misses (below) record and continue." in BEHAVIOUR_PROBE
    assert '("roundtrip", roundtrip_misses)' in BEHAVIOUR_PROBE
