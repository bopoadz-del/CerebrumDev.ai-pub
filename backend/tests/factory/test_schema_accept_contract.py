"""C-BRIEF must ground writer_behaviour schema-accept (not a wall-fixer).

Live sess_91553364089d4970 (VetCare Hub): COLLECTOR+CLONER green, WRITER
stopped at writer_behaviour with ``no capability accepted its own schema``.
The compiled brief never named that gate or the probe's sampling rules, so
FACTORY_CODE_CLI / oneshot handlers invented a stricter contract than the
spec. This module is the compiler + harness contract — not a VetCare
product patch.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.brief_compiler import compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.schema_accept import (
    CHANNEL_SAMPLE,
    DATETIME_SAMPLE,
    DATE_SAMPLE,
    EMAIL_SAMPLE,
    ENVELOPE_ACCEPT_SAMPLE,
    ENVELOPE_STATUS_SAMPLE,
    GENERIC_STR_SAMPLE,
    SCHEMA_ACCEPT_GATE,
    SCHEMA_ACCEPT_HALT,
    TIME_SAMPLE,
    probe_sample_payload,
    probe_sample_value,
    schema_accept_acceptance_line,
    schema_accept_brief_contract,
    schema_accept_rules_text,
)
from app.factory.build.writer_behaviour import BEHAVIOUR_PROBE, SCHEMA_HALT
from app.factory.build.writer_brief import CODING_AGENT_BRIEF
from app.factory.coder import _WHOLE_JOB_SYSTEM
from app.factory.product_architect import plan_blueprint
from app.factory.blueprint import load_blueprint


ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
LETTINGS = ROOT / "blueprints/lettings/residential_lettings.v1.yaml"


class _Cap:
    def __init__(self, cid, block_ids=(), strategy="REUSE"):
        self.capability_id = cid
        self.block_ids = list(block_ids)
        self.strategy = strategy
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _VetCare:
    product_name = "VetCare Hub"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "Clinic appointments, reminders, and pet records."


def test_probe_sample_rules_match_writer_behaviour_literals():
    """Drift between the brief contract and the baked probe is the next halt.

    Every non-generic sample follows a DECLARATION -- a vocabulary, a bound,
    a type or a format -- never the field's name.
    """
    from app.factory.build.block_obligations import ENVELOPE_STATUS_VALUES

    import ast

    from app.factory.build.payload_helpers import PROBE_PAYLOAD_SLOT
    from app.factory.build.roles_handlers import _sample_value

    assert SCHEMA_ACCEPT_HALT == SCHEMA_HALT
    # ONE source: the probe carries no sample rules of its own -- only the
    # slot the shared builder renders into -- and every contract literal IS
    # what that builder (TESTER's sampler) returns for the declaration.
    probe = BEHAVIOUR_PROBE
    assert PROBE_PAYLOAD_SLOT in probe
    own = {n.name for n in ast.parse(probe).body if isinstance(n, ast.FunctionDef)}
    assert not {"_ann", "_value", "_payload"} & own, own
    for literal, field in (
        (GENERIC_STR_SAMPLE, {"name": "zorblat", "type": "str"}),
        (DATETIME_SAMPLE, {"name": "zorblat", "type": "datetime"}),
        (DATE_SAMPLE, {"name": "zorblat", "type": "date"}),
        (TIME_SAMPLE, {"name": "zorblat", "type": "str", "format": "time"}),
        (EMAIL_SAMPLE, {"name": "zorblat", "type": "str", "format": "email"}),
    ):
        assert _sample_value(field) == literal, (literal, field)
    # Declared vocabularies: the envelope status, a channel list, any enum.
    assert probe_sample_value(
        "status", constraints={"allowed_values": list(ENVELOPE_STATUS_VALUES)}
    ) == ENVELOPE_STATUS_SAMPLE == "open"
    assert probe_sample_value(
        "channel", constraints={"allowed_values": [CHANNEL_SAMPLE, "sms"]}
    ) == CHANNEL_SAMPLE
    assert probe_sample_value(
        "priority", constraints={"allowed_values": ["Critical", "Low"]}
    ) == "Critical"
    # Declared formats and types.
    assert probe_sample_value("scheduled_time", constraints={"format": "time"}) == TIME_SAMPLE
    assert probe_sample_value("appointment_date", constraints={"format": "date"}) == DATE_SAMPLE
    assert probe_sample_value("created_at", annotation="datetime") == DATETIME_SAMPLE
    assert probe_sample_value("owner_email", constraints={"format": "email"}) == EMAIL_SAMPLE
    # A declared bound is honoured (the builder's value, inside the bound).
    for con in ({"min": 0}, {"min": 3}, {"max": 0}, {"min": -5, "max": -2}):
        got = probe_sample_value("quantity", annotation="int", constraints=con)
        assert got == _sample_value({"name": "quantity", "type": "int", **con}), con
        assert con.get("min", got) <= got <= con.get("max", got), (con, got)
    # Undeclared: the type's neutral value, whatever the field is called.
    for name in ("status", "channel", "created_at", "owner_email", "scheduled_time", "zorblat_date"):
        assert probe_sample_value(name) == GENERIC_STR_SAMPLE, name


def test_envelope_accept_sample_is_what_the_gate_will_post():
    payload = probe_sample_payload(
        [
            {"name": "reference", "type": "str"},
            {
                "name": "status",
                "type": "str",
                "allowed_values": ["open", "in_progress", "closed"],
            },
        ]
    )
    assert payload == ENVELOPE_ACCEPT_SAMPLE


def test_vetcare_compiled_brief_grounds_schema_accept():
    compiled = compile_brief(
        _VetCare(),
        _Plan(
            _Cap("appointment_scheduling", ["event_bus", "workflow"], "COMPOSE"),
            _Cap("automated_reminders", ["notification"], "REUSE"),
            _Cap("clinic_intake", [], "GENERATE"),
        ),
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    text = compiled.text
    assert SCHEMA_ACCEPT_GATE in text
    assert SCHEMA_ACCEPT_HALT in text
    assert schema_accept_acceptance_line() in text
    assert "[check:writer_behaviour]" in text
    for needle in (
        CHANNEL_SAMPLE,
        GENERIC_STR_SAMPLE,
        ENVELOPE_STATUS_SAMPLE,
        DATETIME_SAMPLE,
        "FIELDS + CONSTRAINTS",
        "before execute()",
    ):
        assert needle in text, needle
    rules = schema_accept_rules_text()
    assert "WRITER gate writer_behaviour" in rules
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_lettings_and_smoke_still_lint_with_schema_accept():
    for path in (SMOKE, LETTINGS):
        bp = load_blueprint(path)
        compiled = compile_brief(bp, plan_blueprint(bp))
        assert SCHEMA_ACCEPT_GATE in compiled.text
        assert SCHEMA_ACCEPT_HALT in compiled.text
        result = lint_brief(compiled)
        assert result.ok, (path.name, result.errors)


def test_system_brief_and_oneshot_name_the_halt():
    contract = schema_accept_brief_contract()
    assert SCHEMA_ACCEPT_GATE in contract
    assert SCHEMA_ACCEPT_HALT in contract
    assert contract in CODING_AGENT_BRIEF
    assert "no capability accepted its own schema" in _WHOLE_JOB_SYSTEM
    assert "writer_behaviour" in _WHOLE_JOB_SYSTEM
