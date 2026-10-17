"""The spec must be able to express every rule the route enforces.

New-shape tests for the defect a realistic 5-capability build exposed. The
model spec carried only name+type, so the agent wrote route validation the
spec could not express -- `severity must be one of: Critical, High,
Informational, Low, Medium` -- while the tester built its payload from generic
type samples and sent `severity="sample"`. The route correctly rejected it and
the gate correctly failed.

That failure is unrecoverable by design: the only way for the WRITER to pass
would be to delete its own validation, i.e. write worse code to satisfy a
weaker test. Three rework rounds confirmed it. The fix is not a looser gate --
it is a spec rich enough to hold the constraint, so the route enforces and the
tester satisfies the same declaration.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.roles import (
    _constraints_of,
    _field_default,
    _render_models,
    _sample_payload,
    _sample_value,
)
from app.factory.build.runner import RoleRunner
from app.factory.coder import _clean_constraints, describe_fields, generate_model_spec

ROOT = Path(__file__).resolve().parents[3]
FIELD_OPS = ROOT / "blueprints/examples/field_ops.yaml"


# -- the sample payload must satisfy what the field declares --------------


def test_a_vocabulary_field_samples_from_its_own_vocabulary():
    """The exact bug: a generic "sample" is type-valid and domain-invalid."""
    field = {
        "name": "severity",
        "type": "str",
        "allowed_values": ["Critical", "High", "Low"],
    }
    assert _sample_value(field) == "Critical"
    assert _sample_value(field) in field["allowed_values"]


def test_a_declared_email_field_samples_an_address():
    """Live winery-hospitality zip: club_waitlist.guest_email='sample'
    failed the writer's 'must be a valid email' check. The declaration
    (type or format ``email``) carries that constraint -- never the name."""
    assert "@" in _sample_value({"name": "zorblat", "type": "email"})
    assert "@" in _sample_value({"name": "zorblat", "type": "str", "format": "email"})
    # A name implies nothing the spec did not declare.
    assert _sample_value({"name": "guest_email", "type": "str"}) == "sample"
    assert _sample_value({"name": "tasting_note", "type": "str"}) == "sample"


def test_a_declared_temporal_field_samples_iso_not_the_word_sample():
    """Live veterinary-care: scheduled_time='sample' is not a time. The spec
    declares the type or format; the name decides nothing."""
    assert _sample_value({"name": "zorblat", "type": "time"}) == "10:00:00"
    assert _sample_value({"name": "zorblat", "type": "date"}) == "2026-09-03"
    assert _sample_value({"name": "visit", "type": "datetime"}) == "2026-09-03T10:00:00"
    assert _sample_value({"name": "when", "type": "str", "format": "datetime"}) == (
        "2026-09-03T10:00:00"
    )
    assert _sample_value({"name": "contact", "type": "email"}) == "guest@example.com"
    assert (
        _sample_value({"name": "listing_uid", "type": "uuid"})
        == "00000000-0000-4000-8000-000000000001"
    )
    assert _sample_value({"name": "duration_minutes", "type": "int", "min": 1}) == 1
    # Names alone: neutral samples.
    assert _sample_value({"name": "scheduled_time", "type": "str"}) == "sample"
    assert _sample_value({"name": "created_at", "type": "str"}) == "sample"
    assert _sample_value({"name": "service_type", "type": "str"}) == "sample"


def test_a_declared_vocabulary_samples_a_store_deliverable_value():
    """Live sess_67fe60f7: automated_reminders schema sample was channel=sample.
    A declared vocabulary that holds a Store-deliverable channel samples it,
    whatever the field is called; an undeclared field samples neutrally."""
    assert _sample_value(
        {"name": "zorblat", "type": "str", "allowed_values": ["sms", "email"]}
    ) == "email"
    assert _sample_value(
        {"name": "zorblat", "type": "str", "allowed_values": ["sms", "push"]}
    ) == "sms"
    assert _sample_value({"name": "channel", "type": "str"}) == "sample"
    assert _sample_value({"name": "notify_channel", "type": "str"}) == "sample"


def test_coder_route_that_saves_the_handle_envelope_is_rewritten_to_payload():
    """Live zip POSTed ok:true then stored all-null rows because the coder
    persisted handle()'s {ok, data} envelope instead of the request."""
    from app.factory.build.roles import _ensure_route_persists_payload

    guest = "    result = handle(payload)\n    record = save(result)\n"
    assert "save(payload)" in _ensure_route_persists_payload(guest)
    assert "save(result)" not in _ensure_route_persists_payload(guest)
    waitlist = "    handled = handle(payload)\n    saved = save(handled)\n"
    assert "save(payload)" in _ensure_route_persists_payload(waitlist)
    assert "save(handled)" not in _ensure_route_persists_payload(waitlist)
    already = "    stored = save(payload)\n"
    assert _ensure_route_persists_payload(already) == already


def test_a_bounded_number_samples_inside_its_bounds():
    assert _sample_value({"name": "n", "type": "int", "min": 5, "max": 9}) == 5
    assert _sample_value({"name": "n", "type": "int", "max": 0}) == 0
    # No bounds declared -> the generic sample is fine.
    assert _sample_value({"name": "n", "type": "int"}) == 1


def test_a_whole_payload_satisfies_every_declared_constraint():
    spec = {
        "entity": "defect",
        "fields": [
            {"name": "ref", "type": "str"},
            {"name": "severity", "type": "str", "allowed_values": ["High", "Low"]},
            {"name": "days_open", "type": "int", "min": 1, "max": 30},
            {"name": "is_open", "type": "bool"},
        ],
    }
    payload = _sample_payload(spec)
    assert payload["severity"] in ("High", "Low")
    assert 1 <= payload["days_open"] <= 30
    assert isinstance(payload["is_open"], bool)


def test_the_dataclass_default_is_itself_valid():
    """A default-constructed model must not violate its own constraints."""
    assert _field_default({"name": "s", "type": "str", "allowed_values": ["a", "b"]}) == "'a'"
    assert _field_default({"name": "n", "type": "int", "min": 3}) == "3"
    assert _field_default({"name": "n", "type": "int"}) == "0"
    assert _field_default({"name": "zorblat", "type": "time"}) == "'10:00:00'"


def test_datetime_typed_field_renders_as_compilable_str():
    """LLM type=datetime must not emit `scheduled_time: datetime` (NameError)."""
    spec = {
        "end_to_end_appointment_workflow": {
            "entity": "appointment",
            "fields": [
                {"name": "scheduled_time", "type": "datetime"},
                {"name": "duration_minutes", "type": "integer"},
                {"name": "status", "type": "TEXT"},
                {"name": "service_type", "type": "str"},
            ],
        }
    }
    src = _render_models(spec)
    assert "scheduled_time: str =" in src
    assert "scheduled_time: datetime" not in src
    assert "duration_minutes: int =" in src
    ns: dict = {}
    exec(compile(src, "<models>", "exec"), ns)
    cls = ns["MODELS"]["end_to_end_appointment_workflow"]
    row = cls()
    assert row.scheduled_time == "2026-09-03T10:00:00"


# -- the spec validator ---------------------------------------------------


def test_coder_keeps_datetime_fields_as_iso_text(monkeypatch):
    """A new-domain LLM spec that says type=datetime must not drop the field."""
    from app.factory import coder as coder_mod

    monkeypatch.setattr(
        coder_mod,
        "_llm_code_call",
        lambda _messages: (
            '{"entity": "appointment", "fields": ['
            '{"name": "scheduled_time", "type": "datetime", "required": true},'
            '{"name": "duration_minutes", "type": "int", "required": true, "min": 1},'
            '{"name": "status", "type": "str", "required": true},'
            '{"name": "service_type", "type": "TEXT", "required": true}'
            "]}",
            "stub",
        ),
    )
    spec = generate_model_spec(
        capability_id="end_to_end_appointment_workflow",
        description="book a clinic appointment",
        product_name="Manchester Vet Clinic",
        vertical="veterinary-care",
    )
    by_name = {f["name"]: f for f in spec["fields"]}
    assert "scheduled_time" in by_name
    assert by_name["scheduled_time"]["type"] == "str"
    assert by_name["scheduled_time"]["format"] == "datetime"
    assert by_name["duration_minutes"]["type"] == "int"
    assert by_name["service_type"]["type"] == "str"
    # Live sess_66a387b: LLM veterinary-care specs omitted the factory envelope.
    assert "reference" in by_name
    assert by_name["reference"]["required"] is True


def test_reserved_keyword_field_names_render_and_round_trip():
    """Live veterinary-care: mined ``and`` (and LLM ``class`` / ``for``)
    must become valid attributes and still map JSON keys."""
    spec = {
        "secure_multi_user_dashboard": {
            "entity": "dashboard",
            "fields": [
                {"name": "and", "type": "str", "required": True},
                {"name": "class", "type": "str", "required": True},
                {"name": "for", "type": "str", "required": True},
                {"name": "pet-id", "type": "str", "required": True},
            ],
        }
    }
    src = _render_models(spec)
    assert "    and:" not in src
    assert "    class:" not in src
    assert "    for:" not in src
    assert "    and_:" in src
    assert "    class_:" in src
    assert "    for_:" in src
    assert "    pet_id:" in src
    ns: dict = {}
    exec(compile(src, "<models>", "exec"), ns)
    cls = ns["MODELS"]["secure_multi_user_dashboard"]
    payload = {
        "and": "conjunction",
        "class": "mammals",
        "for": "clinic",
        "pet-id": "p-1",
    }
    row = cls.from_dict(payload)
    assert row.and_ == "conjunction"
    assert row.class_ == "mammals"
    assert row.for_ == "clinic"
    assert row.pet_id == "p-1"
    assert row.to_dict()["and"] == "conjunction"
    assert row.to_dict()["class"] == "mammals"
    assert row.to_dict()["pet-id"] == "p-1"


def test_generate_model_spec_sanitizes_keyword_and_illegal_names(monkeypatch):
    from app.factory import coder as coder_mod

    monkeypatch.setattr(
        coder_mod,
        "_llm_code_call",
        lambda _messages: (
            '{"entity": "record", "fields": ['
            '{"name": "and", "type": "str", "required": true},'
            '{"name": "class", "type": "str", "required": true},'
            '{"name": "pet-name", "type": "str", "required": true},'
            '{"name": "status", "type": "str", "required": true}'
            "]}",
            "stub",
        ),
    )
    spec = generate_model_spec(
        capability_id="secure_multi_user_dashboard",
        description="role dashboard",
        product_name="VetCare Hub",
        vertical="veterinary-care",
    )
    names = [f["name"] for f in spec["fields"]]
    assert "and" not in names
    assert "class" not in names
    assert "and_" in names
    assert "class_" in names
    assert "pet_name" in names
    assert "status" in names
    assert "reference" in names
    src = _render_models({"secure_multi_user_dashboard": spec})
    exec(compile(src, "<models>", "exec"), {})


def test_a_vocabulary_is_accepted_and_deduped():
    out = _clean_constraints(
        {"allowed_values": ["open", "closed", "open", " done "]}, "str"
    )
    assert out["allowed_values"] == ["open", "closed", "done"]


@pytest.mark.parametrize(
    "raw",
    (
        {"allowed_values": ["only_one"]},          # a constant, not an enum
        {"allowed_values": []},                     # nothing declared
        {"allowed_values": "open,closed"},          # not a list
        {"allowed_values": [1, 2, 3]},              # not strings
    ),
)
def test_a_malformed_vocabulary_is_dropped_not_trusted(raw):
    """A constraint the platform cannot enforce is worse than none — the route
    would reject payloads the tests are entitled to send."""
    assert "allowed_values" not in _clean_constraints(raw, "str")


def test_an_inverted_range_is_dropped():
    """min > max makes every value invalid and every build unpassable."""
    assert _clean_constraints({"min": 10, "max": 2}, "int") == {}


def test_bounds_are_typed_and_booleans_are_not_numbers():
    assert _clean_constraints({"min": 1, "max": 9}, "int") == {"min": 1, "max": 9}
    assert _clean_constraints({"min": True}, "int") == {}
    # A vocabulary is meaningless on a numeric field.
    assert _clean_constraints({"allowed_values": ["a", "b"]}, "int") == {}


def test_constraints_are_extracted_for_the_model():
    spec = {
        "fields": [
            {"name": "a", "type": "str"},
            {"name": "b", "type": "str", "allowed_values": ["x", "y"]},
            {"name": "c", "type": "int", "min": 0},
        ]
    }
    assert _constraints_of(spec) == {
        "b": {"allowed_values": ["x", "y"]},
        "c": {"min": 0},
    }


# -- what the route is told -----------------------------------------------


def test_the_route_prompt_states_every_permitted_constraint():
    """The route may enforce these and nothing else, so it must see them."""
    text = describe_fields(
        [
            {"name": "severity", "type": "str", "allowed_values": ["High", "Low"]},
            {"name": "days", "type": "int", "min": 1, "max": 30},
            {"name": "note", "type": "str", "required": False},
        ]
    )
    assert "one of 'High', 'Low'" in text
    assert "min 1" in text and "max 30" in text
    assert "optional" in text


def test_the_route_prompt_forbids_inventing_constraints():
    from app.factory.coder import _ROUTE_SYSTEM

    assert "VALIDATION IS BOUNDED BY THE SPEC" in _ROUTE_SYSTEM
    assert "Do NOT invent" in _ROUTE_SYSTEM


# -- end to end, keyless ---------------------------------------------------


def test_a_constrained_build_passes_its_own_route_test(tmp_path, stub_coder):
    """The regression, end to end: the five-capability blueprint that failed.

    0.5: the deterministic path refuses (writer_no_output), so the build runs
    a stubbed coding agent; models, routes and tests stay deterministic, which
    is what keeps the constraint assertions meaningful in CI with no key.
    """
    out = tmp_path / "build"
    outcome = RoleRunner(load_blueprint(FIELD_OPS), out).run()

    assert outcome.ok, outcome.to_dict()
    models = (out / "app" / "models.py").read_text(encoding="utf-8")
    assert "CONSTRAINTS = {" in models
    assert "'allowed_values': ['open', 'in_progress', 'closed']" in models

    routes = (out / "app" / "routes.py").read_text(encoding="utf-8")
    assert "must be one of" in routes, "the route must enforce the vocabulary"

    tests = (out / "tests" / "test_routes.py").read_text(encoding="utf-8")
    assert "'status': 'open'" in tests, "the payload must use a declared value"
    assert "'sample'" not in tests.split("status")[1][:80]


def test_the_route_actually_rejects_a_value_outside_the_vocabulary(
    tmp_path, stub_coder
):
    """The constraint must be real, not decorative.

    Without this, a route that ignored CONSTRAINTS entirely would pass every
    other test here -- the payload is valid, so nothing would notice.
    """
    out = tmp_path / "build"
    assert RoleRunner(load_blueprint(FIELD_OPS), out).run().ok

    import subprocess
    import sys

    probe = out / "probe.py"
    probe.write_text(
        "import os, sys\n"
        "sys.path.insert(0, r'%s')\n" % str(out).replace("\\", "\\\\")
        + "os.environ['STORAGE_PATH'] = r'%s'\n" % str(out / "d").replace("\\", "\\\\")
        # Runtime is fail-closed now (F1): a deployed product gets its token
        # from the environment, so the probe provisions one like an operator.
        + "os.environ['PLATFORM_TOKEN'] = 'dev-local-token'\n"
        + "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "c = TestClient(app)\n"
        "headers = {'Authorization': 'Bearer ' + os.environ.get("
        "'PLATFORM_TOKEN', 'dev-local-token')}\n"
        "r = c.post('/v1/site_inspection_log', json={'reference': 'x',"
        " 'status': 'NOT_A_REAL_STATUS', 'quantity': 1}, headers=headers)\n"
        "assert r.status_code == 422, r.text\n"
        "assert 'must be one of' in r.text, r.text\n"
        "print('rejected as expected')\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(probe)], cwd=out, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "rejected as expected" in proc.stdout
