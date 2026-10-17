"""C-BRIEF must ground PRODUCT event_bus workflow accept-payload.

Live sess_e94ddfa797ea4a45 (VetCare Hub / veterinary-care) on tip f090030
(#317 schema-accept after #316 C-BRIEF):

    PRODUCT (pilot-marked suite): suite is red: schema sample refused;
    schema sample refused (event_bus workflow step);
    accept-payload persisted nothing — FAILED

    reminders_and_notifications rejected a payload built from its own
    schema: workflow: step_2 (event_bus): error.

After #323 the wall moved (sess_d70c18ef58ab48e6 / tip 205f957):
appointment_booking rejected a payload at workflow: step_2 (event_bus).
The compiler + harness must fail that class — not a VetCare product patch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.brief_compiler import brief_fingerprint, compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.roles_handlers import _sample_payload, _sample_value
from app.factory.build.schema_accept import (
    CHANNEL_SAMPLE,
    DATETIME_SAMPLE,
    DATE_SAMPLE,
    ENVELOPE_STATUS_SAMPLE,
    GENERIC_STR_SAMPLE,
    TIME_SAMPLE,
)
from app.factory.build.workflow_accept import (
    PRODUCT_EVENT_BUS_STEP_0_HALT,
    EVENT_BUS_STEP_ACTION,
    EVENT_BUS_STEP_CHANNEL,
    prepared_event_bus_step_example,
    PRODUCT_ACCEPT_CHECK,
    PRODUCT_ACCEPT_TEST,
    PRODUCT_EMAIL_SAMPLE,
    PRODUCT_EVENT_BUS_STEP_1_HALT,
    PRODUCT_EVENT_BUS_STEP_2_HALT,
    PRODUCT_EVENT_BUS_STEP_CLASS,
    PRODUCT_EVENT_BUS_STEP_HALT,
    PRODUCT_WORKFLOW_RESULT_HALT,
    WRITER_EVENT_BUS_WORKFLOW_HALT,
    EventBusWorkflowHalt,
    assert_event_bus_workflow_handlers,
    declares_event_bus_workflow,
    event_bus_steps_from_handler,
    event_bus_workflow_capability_ids,
    event_bus_workflow_handler_errors,
    grounded_event_bus_handler_body,
    grounded_event_bus_topic,
    handler_builds_unparsed_event_bus_workflow,
    handler_has_factory_event_bus_wrap,
    handler_has_prepared_event_bus_step,
    handler_satisfies_event_bus_contract,
    needs_grounded_event_bus_handler,
    workflow_accept_acceptance_line,
    workflow_accept_brief_contract,
    workflow_accept_forbidden_lines,
    workflow_accept_rules_text,
)
from app.factory.build.writer_brief import CODING_AGENT_BRIEF
from app.factory.coder import _WHOLE_JOB_SYSTEM
from app.factory.product_architect import (
    plan_blueprint,
)

#: Sample capability ids for these tests -- test data only. Factory code
#: holds no product's names; these are what a sample plan calls its rows.
SCHEDULING_CAP = "appointment_scheduling"
BOOKING_CAP = "appointment_booking"
REMINDERS_CAP = "automated_reminders"

LETTINGS_GOLDEN = Path(__file__).resolve().parents[3] / "blueprints/lettings/residential_lettings.v1.yaml"


ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
LETTINGS = ROOT / "blueprints/lettings/residential_lettings.v1.yaml"
LIVE_LETTINGS_CAPS = {
    "unit_registry_and_vacancy_tracking",
    "viewing_management",
    "maintenance_issue_tracking",
    "tenancy_application_pipeline",
}


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


PREPARED_HANDLER = (
    "def handle(payload):\n"
    "    steps = [{\n"
    "        'block': 'event_bus',\n"
    "        'action': 'publish',\n"
    "        'input': {\n"
    "            'topic': 'appointment.scheduled',\n"
    "            'payload': {'reference': payload.get('reference')},\n"
    "                        'message': 'appointment recorded',\n"
            "            'channel': 'mcp',\n"
            "            'tool': 'event_bus',\n"
            "        },\n"
            "    }]\n"
            "    return execute('workflow', {'steps': steps}, action='run')\n"
        )

UNPREPARED_HANDLER = (
    "def handle(payload):\n"
    "    steps = [{'block': 'event_bus', 'input': payload}]\n"
    "    return execute('workflow', {'steps': steps})\n"
)

#: Live after #323: step_1 looks prepared; step_2 forwards the sample.
MIXED_STEP2_HANDLER = (
    "def handle(payload):\n"
    "    steps = [\n"
    "        {\n"
    "            'block': 'database',\n"
    "            'input': {'table': 'appointments', 'values': payload},\n"
    "        },\n"
    "        {'block': 'event_bus', 'input': payload},\n"
    "    ]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)

MIXED_PREPARED_THEN_RAW = (
    "def handle(payload):\n"
    "    steps = [\n"
    "        {\n"
    "            'block': 'event_bus',\n"
    "            'action': 'publish',\n"
    "            'input': {\n"
    "                'topic': 'appointment.scheduled',\n"
    "                'payload': {'reference': payload.get('reference')},\n"
    "                'message': 'appointment recorded',\n"
    "                'channel': 'mcp',\n"
    "                'tool': 'event_bus',\n"
    "            },\n"
    "        },\n"
    "        {'block': 'event_bus', 'input': payload},\n"
    "    ]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)

FACTORY_WRAP_UNPREPARED = (
    "def _watched(block_id, *a, **kw):\n"
    "    prepared = _prepare_block_input(block_id, data, action=action)\n"
    "    return _dispatch.execute(block_id, prepared, action=action)\n"
    "def handle(payload):\n"
    "    steps = [{'block': 'event_bus', 'input': payload}]\n"
    "    return execute('workflow', {'steps': steps})\n"
)

#: Both event_bus children prepared — WRITER green. Mutation tests strip step_2.
TWO_PREPARED_EVENT_BUS_STEPS = (
    "def handle(payload):\n"
    "    steps = [\n"
    "        {\n"
    "            'block': 'event_bus',\n"
    "            'action': 'publish',\n"
    "            'input': {\n"
    "                'topic': 'appointment.scheduled',\n"
    "                'payload': {'reference': payload.get('reference')},\n"
    "                'message': 'appointment recorded',\n"
    "                'channel': 'mcp',\n"
    "                'tool': 'event_bus',\n"
    "            },\n"
    "        },\n"
    "        {\n"
    "            'block': 'event_bus',\n"
    "            'action': 'publish',\n"
    "            'input': {\n"
    "                'topic': 'appointment.confirmed',\n"
    "                'payload': {'reference': payload.get('reference')},\n"
    "                'message': 'booking confirmed',\n"
    "                'channel': 'mcp',\n"
    "                'tool': 'event_bus',\n"
    "            },\n"
    "        },\n"
    "    ]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)

#: step_2 looks keyed but uses PRODUCT sample channel=email (no `to`).
STEP2_PRODUCT_CHANNEL_HANDLER = (
    "def handle(payload):\n"
    "    steps = [\n"
    "        {\n"
    "            'block': 'event_bus',\n"
    "            'action': 'publish',\n"
    "            'input': {\n"
    "                'topic': 'appointment.scheduled',\n"
    "                'payload': {'reference': payload.get('reference')},\n"
    "                'message': 'appointment recorded',\n"
    "                'channel': 'mcp',\n"
    "                'tool': 'event_bus',\n"
    "            },\n"
    "        },\n"
    "        {\n"
    "            'block': 'event_bus',\n"
    "            'action': 'publish',\n"
    "            'input': {\n"
    "                'topic': 'appointment.confirmed',\n"
    "                'payload': {'reference': payload.get('reference')},\n"
    "                'message': 'booking confirmed',\n"
    "                'channel': 'email',\n"
    "            },\n"
    "        },\n"
    "    ]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)


def _binds_workflow_and_event_bus(plan):
    """The structural rule, computed from the plan -- never from names."""
    return [
        c.capability_id
        for c in plan.capabilities
        if {"workflow", "event_bus"} <= set(c.block_ids)
    ]


def _vetcare_plan():
    return _Plan(
        _Cap("appointment_scheduling", ["event_bus", "workflow"], "COMPOSE"),
        _Cap("appointment_booking", ["database"], "COMPOSE"),
        _Cap("reminders_and_notifications", ["notification", "workflow", "event_bus"], "COMPOSE"),
        _Cap("reminders_notifications", ["notification", "workflow", "event_bus"], "COMPOSE"),
        _Cap("clinic_intake", [], "GENERATE"),
    )


def test_product_sample_literals_match_tester_payload():
    """Drift between the brief contract and PRODUCT _sample_value is the next
    halt. The samples follow what each field DECLARES (type, format,
    vocabulary, the record envelope); a name alone shapes nothing."""
    from app.factory.build.block_obligations import ensure_record_envelope

    assert _sample_value(
        {"name": "zorblat", "type": "str", "allowed_values": ["open", "closed"]}
    ) == ENVELOPE_STATUS_SAMPLE == "open"
    assert _sample_value(
        {"name": "zorblat", "type": "str", "allowed_values": ["sms", "email"]}
    ) == CHANNEL_SAMPLE == "email"
    assert _sample_value({"name": "pet_name", "type": "str"}) == GENERIC_STR_SAMPLE
    assert _sample_value({"name": "zorblat", "type": "str", "format": "email"}) == PRODUCT_EMAIL_SAMPLE
    assert _sample_value({"name": "zorblat", "type": "datetime"}) == DATETIME_SAMPLE
    assert _sample_value({"name": "zorblat", "type": "date"}) == DATE_SAMPLE
    assert _sample_value({"name": "zorblat", "type": "time"}) == TIME_SAMPLE
    spec, _ = ensure_record_envelope(
        {
            "fields": [
                {"name": "reference", "type": "str"},
                {"name": "status", "type": "str"},
                {"name": "channel", "type": "str", "allowed_values": ["sms", "email"]},
                {"name": "owner_email", "type": "str", "format": "email"},
            ]
        }
    )
    sample = _sample_payload(spec)
    assert sample["status"] == "open"
    assert sample["channel"] == "email"
    assert sample["owner_email"] == PRODUCT_EMAIL_SAMPLE
    assert sample["channel"] != GENERIC_STR_SAMPLE


def test_vetcare_inventory_declares_event_bus_workflows():
    plan = _vetcare_plan()
    compiled = compile_brief(
        _VetCare(),
        plan,
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    ids = event_bus_workflow_capability_ids(compiled)
    # Exactly the capabilities that bind both blocks; a database-only
    # capability is not one, whatever it is called.
    assert sorted(ids) == sorted(_binds_workflow_and_event_bus(plan))
    assert declares_event_bus_workflow(compiled) is True


def test_vetcare_compiled_brief_grounds_event_bus_workflow_accept():
    compiled = compile_brief(
        _VetCare(),
        _vetcare_plan(),
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    text = compiled.text
    assert PRODUCT_ACCEPT_TEST in text
    assert PRODUCT_EVENT_BUS_STEP_HALT in text
    assert PRODUCT_EVENT_BUS_STEP_CLASS in text
    assert workflow_accept_acceptance_line(
        capability_ids=_binds_workflow_and_event_bus(_vetcare_plan())
    ) in text
    assert f"[check:{PRODUCT_ACCEPT_CHECK}]" in text
    assert EVENT_BUS_STEP_CHANNEL in text
    assert EVENT_BUS_STEP_ACTION in text
    assert "never the raw schema sample" in text
    assert PRODUCT_EVENT_BUS_STEP_1_HALT in text
    assert PRODUCT_EVENT_BUS_STEP_2_HALT in text
    assert "every event_bus" in text
    assert "keep/done" in text
    assert "reminders_notifications" in text
    assert prepared_event_bus_step_example() in text
    assert "'input': payload" in text
    assert '"channel": "mcp"' in text
    assert '"action": "publish"' in text
    assert "input.topic" in text
    assert "payload dict" in text
    rules = workflow_accept_rules_text(
        capability_ids=["appointment_scheduling", "reminders_notifications"]
    )
    assert PRODUCT_ACCEPT_TEST in rules
    assert "appointment_scheduling" in rules
    assert "reminders_notifications" in rules
    assert prepared_event_bus_step_example() in rules
    assert workflow_accept_forbidden_lines() in text


def test_lettings_golden_roster_and_fingerprint_unchanged():
    bp = load_blueprint(LETTINGS_GOLDEN)
    assert {c.id for c in bp.capabilities} == LIVE_LETTINGS_CAPS
    golden = load_blueprint(LETTINGS_GOLDEN)
    assert {c.id for c in golden.capabilities} == LIVE_LETTINGS_CAPS
    plan = plan_blueprint(bp)
    compiled = compile_brief(bp, plan)
    fp = brief_fingerprint(compiled)
    assert set(fp["capabilities"]) == LIVE_LETTINGS_CAPS
    assert fp["missing_reuse"] == []
    assert declares_event_bus_workflow(compiled) is False
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert LETTINGS.is_file()


def test_lettings_and_smoke_still_lint_with_workflow_accept():
    for path in (SMOKE, LETTINGS):
        bp = load_blueprint(path)
        compiled = compile_brief(bp, plan_blueprint(bp))
        assert PRODUCT_ACCEPT_TEST in compiled.text
        result = lint_brief(compiled)
        assert result.ok, (path.name, result.errors)


def test_system_brief_and_oneshot_name_the_event_bus_step_halt():
    contract = workflow_accept_brief_contract()
    assert PRODUCT_ACCEPT_TEST in contract
    assert PRODUCT_EVENT_BUS_STEP_HALT in contract
    assert EVENT_BUS_STEP_CHANNEL in contract
    assert "'input': payload" in contract
    assert PRODUCT_EVENT_BUS_STEP_0_HALT in contract
    assert PRODUCT_EVENT_BUS_STEP_1_HALT in contract
    assert PRODUCT_EVENT_BUS_STEP_2_HALT in contract
    assert PRODUCT_WORKFLOW_RESULT_HALT in contract
    assert "SyntaxError: cannot assign to function call" in contract
    assert "fail-closed keep original must still rewrite reads" in contract
    assert "keep/done" in contract
    assert contract in CODING_AGENT_BRIEF
    assert PRODUCT_ACCEPT_TEST in _WHOLE_JOB_SYSTEM
    assert PRODUCT_EVENT_BUS_STEP_HALT in _WHOLE_JOB_SYSTEM
    assert PRODUCT_EVENT_BUS_STEP_0_HALT in _WHOLE_JOB_SYSTEM
    assert PRODUCT_EVENT_BUS_STEP_1_HALT in _WHOLE_JOB_SYSTEM
    assert PRODUCT_EVENT_BUS_STEP_2_HALT in _WHOLE_JOB_SYSTEM
    assert "appointment_booking" in _WHOLE_JOB_SYSTEM
    assert "channel=mcp" in _WHOLE_JOB_SYSTEM
    assert "'input': payload" in _WHOLE_JOB_SYSTEM or "input to payload" in _WHOLE_JOB_SYSTEM


def test_event_bus_alone_owes_no_workflow_contract():
    """Binding event_bus without workflow is not a workflow capability. The
    old rule gave the contract to any capability whose NAME looked like an
    appointment; the Store's workflow block declares no such requirement."""
    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("zorblat_intake", ["event_bus"], "REUSE")),
        store_ids={"event_bus"},
    )
    assert event_bus_workflow_capability_ids(compiled) == []
    assert declares_event_bus_workflow(compiled) is False
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_prepared_handler_contract_helpers():
    assert handler_has_prepared_event_bus_step(PREPARED_HANDLER) is True
    assert handler_satisfies_event_bus_contract(PREPARED_HANDLER) is True
    assert handler_has_prepared_event_bus_step(UNPREPARED_HANDLER) is False
    assert handler_satisfies_event_bus_contract(UNPREPARED_HANDLER) is False
    # Import-only prepare_block_input must not green-light a raw step_2.
    wrapped = "from app.block_inputs import prepare_block_input\n" + UNPREPARED_HANDLER
    assert handler_satisfies_event_bus_contract(wrapped) is False
    assert handler_has_factory_event_bus_wrap(FACTORY_WRAP_UNPREPARED) is True
    assert handler_satisfies_event_bus_contract(FACTORY_WRAP_UNPREPARED) is False


def test_mixed_step2_handler_fails_contract():
    """PRODUCT-red class: prepared/other step_1 + raw event_bus step_2."""
    steps = event_bus_steps_from_handler(MIXED_STEP2_HANDLER)
    assert steps == [(2, False)]
    assert handler_satisfies_event_bus_contract(MIXED_STEP2_HANDLER) is False
    mixed_prepared = event_bus_steps_from_handler(MIXED_PREPARED_THEN_RAW)
    assert (1, True) in mixed_prepared
    assert (2, False) in mixed_prepared
    assert handler_satisfies_event_bus_contract(MIXED_PREPARED_THEN_RAW) is False
    assert handler_has_prepared_event_bus_step(MIXED_PREPARED_THEN_RAW) is True


def test_harness_fails_unprepared_event_bus_handlers_before_writer_done(tmp_path):
    compiled = compile_brief(
        _VetCare(),
        _vetcare_plan(),
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        UNPREPARED_HANDLER, encoding="utf-8"
    )
    (actions / "reminders_notifications.py").write_text(
        PREPARED_HANDLER, encoding="utf-8"
    )
    errors = event_bus_workflow_handler_errors(tmp_path, compiled)
    assert any("appointment_scheduling" in e for e in errors)
    assert not any("reminders_notifications" in e for e in errors)
    with pytest.raises(EventBusWorkflowHalt) as halted:
        assert_event_bus_workflow_handlers(tmp_path, compiled)
    assert WRITER_EVENT_BUS_WORKFLOW_HALT in str(halted.value)
    assert "appointment_scheduling" in str(halted.value)


def test_harness_passes_prepared_and_wrapped_handlers(tmp_path):
    compiled = compile_brief(
        _VetCare(),
        _vetcare_plan(),
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        PREPARED_HANDLER, encoding="utf-8"
    )
    (actions / "reminders_and_notifications.py").write_text(
        PREPARED_HANDLER, encoding="utf-8"
    )
    (actions / "reminders_notifications.py").write_text(
        PREPARED_HANDLER, encoding="utf-8"
    )
    (actions / "appointment_booking.py").write_text(
        PREPARED_HANDLER, encoding="utf-8"
    )
    assert event_bus_workflow_handler_errors(tmp_path, compiled) == []
    assert_event_bus_workflow_handlers(tmp_path, compiled)


def test_appointment_booking_step2_cannot_pass_writer_while_product_red(tmp_path):
    """The live 205f957 class must fail [check:event_bus_workflow]."""
    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("appointment_booking", ["database", "event_bus", "workflow"], "COMPOSE")),
        store_ids={"database", "event_bus", "workflow"},
    )
    assert "appointment_booking" in event_bus_workflow_capability_ids(compiled)
    assert PRODUCT_EVENT_BUS_STEP_2_HALT in compiled.text
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_booking.py").write_text(
        MIXED_STEP2_HANDLER, encoding="utf-8"
    )
    errors = event_bus_workflow_handler_errors(tmp_path, compiled)
    assert any("appointment_booking" in e and "step_2" in e for e in errors)
    with pytest.raises(EventBusWorkflowHalt) as halted:
        assert_event_bus_workflow_handlers(tmp_path, compiled)
    assert WRITER_EVENT_BUS_WORKFLOW_HALT in str(halted.value)
    assert "appointment_booking" in str(halted.value)
    assert "step_2" in str(halted.value)


def test_disk_alias_booking_handler_is_scanned_even_if_plan_used_scheduling(tmp_path):
    """Plan said appointment_scheduling; CLI wrote appointment_booking.py."""
    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("appointment_scheduling", ["event_bus", "workflow"], "COMPOSE")),
        store_ids={"event_bus", "workflow"},
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        PREPARED_HANDLER, encoding="utf-8"
    )
    (actions / "appointment_booking.py").write_text(
        MIXED_STEP2_HANDLER, encoding="utf-8"
    )
    errors = event_bus_workflow_handler_errors(tmp_path, compiled)
    assert any("appointment_booking" in e and "step_2" in e for e in errors)


def _product_accept_sample():
    """Same payload PRODUCT bakes into test_every_capability_route_accepts_payload
    for a spec that DECLARES its status envelope, channel vocabulary, email
    format and date type."""
    from app.factory.build.block_obligations import ensure_record_envelope

    spec, _ = ensure_record_envelope(
        {
            "fields": [
                {"name": "reference", "type": "str"},
                {"name": "status", "type": "str"},
                {"name": "channel", "type": "str", "allowed_values": ["sms", "email"]},
                {"name": "owner_email", "type": "str", "format": "email"},
                {"name": "appointment_date", "type": "date"},
            ]
        }
    )
    return _sample_payload(spec)


def test_mutation_step1_only_prepared_fails_when_step2_forwards_product_sample(tmp_path):
    """Mutate a green two-step handler: step_2 gets the PRODUCT schema sample.

    PRODUCT POSTs ``_sample_payload`` (channel=email, status=open). A
    handler that only prepares step_1 and forwards that sample as step_2
    is the live appointment_booking class and must fail the WRITER check.
    """
    sample = _product_accept_sample()
    assert sample["channel"] == CHANNEL_SAMPLE == "email"
    assert sample["status"] == ENVELOPE_STATUS_SAMPLE == "open"
    assert handler_satisfies_event_bus_contract(TWO_PREPARED_EVENT_BUS_STEPS) is True
    steps_green = event_bus_steps_from_handler(TWO_PREPARED_EVENT_BUS_STEPS)
    assert steps_green == [(1, True), (2, True)]

    mutated = TWO_PREPARED_EVENT_BUS_STEPS.replace(
        "'topic': 'appointment.confirmed',\n"
        "                'payload': {'reference': payload.get('reference')},\n"
        "                'message': 'booking confirmed',\n"
        "                'channel': 'mcp',\n"
        "                'tool': 'event_bus',",
        f"'topic': 'appointment.confirmed',\n"
        f"                'payload': {sample!r},\n"
        f"                'message': payload,\n"
        f"                'channel': {sample['channel']!r},\n"
        f"                'tool': 'event_bus',",
    )
    # Stronger live shape: step_2 input is the raw PRODUCT sample.
    mutated_forward = TWO_PREPARED_EVENT_BUS_STEPS.replace(
        "        {\n"
        "            'block': 'event_bus',\n"
        "            'action': 'publish',\n"
        "            'input': {\n"
        "                'topic': 'appointment.confirmed',\n"
        "                'payload': {'reference': payload.get('reference')},\n"
        "                'message': 'booking confirmed',\n"
        "                'channel': 'mcp',\n"
        "                'tool': 'event_bus',\n"
        "            },\n"
        "        },",
        "        {'block': 'event_bus', 'input': payload},\n",
    )
    assert "appointment.confirmed" not in mutated_forward
    assert handler_has_prepared_event_bus_step(mutated_forward) is True
    assert handler_satisfies_event_bus_contract(mutated_forward) is False
    assert event_bus_steps_from_handler(mutated_forward) == [(1, True), (2, False)]

    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("appointment_booking", ["workflow", "event_bus"], "COMPOSE")),
        store_ids={"workflow", "event_bus"},
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_booking.py").write_text(mutated_forward, encoding="utf-8")
    errors = event_bus_workflow_handler_errors(tmp_path, compiled)
    assert any("appointment_booking" in e and "step_2" in e for e in errors)
    with pytest.raises(EventBusWorkflowHalt) as halted:
        assert_event_bus_workflow_handlers(tmp_path, compiled)
    assert "step_2" in str(halted.value)
    assert handler_satisfies_event_bus_contract(mutated) is False


def test_mutation_step2_product_email_channel_fails_writer_check(tmp_path):
    """Harvest sees publish keys on step_1; step_2 channel=email is PRODUCT-red."""
    assert handler_has_prepared_event_bus_step(STEP2_PRODUCT_CHANNEL_HANDLER) is True
    assert handler_satisfies_event_bus_contract(STEP2_PRODUCT_CHANNEL_HANDLER) is False
    assert event_bus_steps_from_handler(STEP2_PRODUCT_CHANNEL_HANDLER) == [
        (1, True),
        (2, False),
    ]
    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("appointment_booking", ["workflow", "event_bus"], "COMPOSE")),
        store_ids={"workflow", "event_bus"},
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_booking.py").write_text(
        STEP2_PRODUCT_CHANNEL_HANDLER, encoding="utf-8"
    )
    errors = event_bus_workflow_handler_errors(tmp_path, compiled)
    assert any("appointment_booking" in e and "step_2" in e for e in errors)


PAYLOAD_GET_TOPIC_HANDLER = (
    "def handle(payload):\n"
    "    steps = [{\n"
    "        'block': 'event_bus',\n"
    "        'action': 'publish',\n"
    "        'input': {\n"
    "            'topic': payload.get('event'),\n"
    "            'payload': {'reference': payload.get('reference')},\n"
    "            'message': payload.get('status'),\n"
    "            'channel': 'mcp',\n"
    "        },\n"
    "    }]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)

DYNAMIC_UNPARSED_STEP1 = (
    "def handle(payload):\n"
    "    step = {}\n"
    "    step['block'] = 'event_bus'\n"
    "    step['input'] = payload\n"
    "    steps = [step]\n"
    "    return execute('workflow', {'steps': steps}, action='run')\n"
)


def test_factory_wrap_does_not_green_unprepared_step1():
    """#325 wrap free-pass let WRITER done while PRODUCT refused step_1."""
    assert handler_has_factory_event_bus_wrap(FACTORY_WRAP_UNPREPARED) is True
    assert handler_satisfies_event_bus_contract(FACTORY_WRAP_UNPREPARED) is False
    steps = event_bus_steps_from_handler(FACTORY_WRAP_UNPREPARED)
    assert (1, False) in steps


def test_appointment_scheduling_step1_cannot_pass_writer_while_product_red(tmp_path):
    """Live sess_14e690829d1f4282 class must fail [check:event_bus_workflow]."""
    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("appointment_scheduling", ["event_bus", "workflow"], "COMPOSE")),
        store_ids={"event_bus", "workflow"},
    )
    assert "appointment_scheduling" in event_bus_workflow_capability_ids(compiled)
    assert SCHEDULING_CAP in compiled.text
    assert PRODUCT_EVENT_BUS_STEP_1_HALT in compiled.text
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        FACTORY_WRAP_UNPREPARED, encoding="utf-8"
    )
    errors = event_bus_workflow_handler_errors(tmp_path, compiled)
    assert any("appointment_scheduling" in e and "step_1" in e for e in errors)
    with pytest.raises(EventBusWorkflowHalt) as halted:
        assert_event_bus_workflow_handlers(tmp_path, compiled)
    assert WRITER_EVENT_BUS_WORKFLOW_HALT in str(halted.value)
    assert "appointment_scheduling" in str(halted.value)
    assert "step_1" in str(halted.value)


def test_payload_get_topic_without_fallback_is_unprepared():
    """PRODUCT sample has no event key — payload.get('event') is None."""
    assert handler_has_prepared_event_bus_step(PAYLOAD_GET_TOPIC_HANDLER) is True
    assert handler_satisfies_event_bus_contract(PAYLOAD_GET_TOPIC_HANDLER) is False
    assert event_bus_steps_from_handler(PAYLOAD_GET_TOPIC_HANDLER) == [(1, False)]


def test_dynamic_unparsed_step1_fails_contract():
    """CLI step['block'] = 'event_bus' + input=payload must not vacuous-pass."""
    assert handler_builds_unparsed_event_bus_workflow(DYNAMIC_UNPARSED_STEP1) is True
    assert handler_satisfies_event_bus_contract(DYNAMIC_UNPARSED_STEP1) is False


def test_wrapped_prepared_step1_still_passes():
    wrapped = (
        "def _watched(block_id, *a, **kw):\n"
        "    prepared = _prepare_block_input(block_id, data, action=action)\n"
        "    return _dispatch.execute(block_id, prepared, action=action)\n"
        + PREPARED_HANDLER
    )
    assert handler_has_factory_event_bus_wrap(wrapped) is True
    assert handler_satisfies_event_bus_contract(wrapped) is True


TEMPLATED_STUB_LOOP = (
    "    results = {}\n"
    "    for block_id in BLOCK_IDS:\n"
    "        result = execute(\n"
    "            block_id, payload, action=BLOCK_DEFAULT_ACTIONS.get(block_id)\n"
    "        )\n"
    "        results[block_id] = result\n"
    "    return {'ok': True, 'capability': CAPABILITY_ID, 'results': results}\n"
)


def test_templated_stub_loop_fails_when_prepared_step_required():
    """#331 hole: execute(block_id, payload) vacuous-passed, PRODUCT still red."""
    assert handler_satisfies_event_bus_contract(TEMPLATED_STUB_LOOP) is True
    assert handler_satisfies_event_bus_contract(
        TEMPLATED_STUB_LOOP, require_prepared_step=True
    ) is False


def test_factory_grounded_body_is_prepared_step1_and_store_ready():
    """WRITER emit for appointment_scheduling — not an LLM stub."""
    assert needs_grounded_event_bus_handler(
        "appointment_scheduling", ["event_bus", "workflow"]
    )
    body = grounded_event_bus_handler_body(
        "appointment_scheduling", ["database", "event_bus", "workflow"]
    )
    wrapped = "def handle(payload):\n" + body + "\n"
    assert handler_satisfies_event_bus_contract(wrapped, require_prepared_step=True)
    assert handler_satisfies_event_bus_contract(body, require_prepared_step=True)
    steps = event_bus_steps_from_handler(body)
    assert steps == [(1, True)]
    assert grounded_event_bus_topic("appointment_scheduling") in body
    assert '"tool": "event_bus"' in body
    assert "channel" in body and "mcp" in body
    assert "'result':" in body or '"result":' in body


def test_writer_replaces_unprepared_scheduling_with_grounded(tmp_path):
    """Live #332 class: do not keep a stub that PRODUCT will refuse at step_1."""
    from app.factory.build.roles_handlers import _capability_handler_body

    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("appointment_scheduling", ["event_bus", "workflow"], "COMPOSE")),
        store_ids={"event_bus", "workflow"},
    )
    body = _capability_handler_body(
        "appointment_scheduling", ["event_bus", "workflow"]
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        "CAPABILITY_ID = 'appointment_scheduling'\n"
        "BLOCK_IDS = ['event_bus', 'workflow']\n"
        "def handle(payload):\n" + body + "\n",
        encoding="utf-8",
    )
    assert event_bus_workflow_handler_errors(tmp_path, compiled) == []
    assert_event_bus_workflow_handlers(tmp_path, compiled)


def test_brief_names_factory_grounded_emit():
    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("appointment_scheduling", ["event_bus", "workflow"], "COMPOSE")),
        store_ids={"event_bus", "workflow"},
    )
    assert "factory-grounded" in compiled.text
    assert 'execute("workflow", payload)' in compiled.text
    assert "input.tool" in compiled.text
    assert PRODUCT_EVENT_BUS_STEP_0_HALT in compiled.text
    assert PRODUCT_WORKFLOW_RESULT_HALT in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_grounded_event_bus_is_decided_by_bindings_not_names():
    """Same bindings, any name: the answer must not change."""
    for name in ("automated_reminders", "zorblat_intake"):
        assert needs_grounded_event_bus_handler(name, ["event_bus", "workflow"])
        assert not needs_grounded_event_bus_handler(name, ["workflow", "notification"])
        assert not needs_grounded_event_bus_handler(name, ["database"])


def test_factory_grounded_body_is_prepared_step0_for_automated_reminders():
    """WRITER emit for automated_reminders — Store first child is step_0."""
    from app.factory.build.workflow_accept import event_bus_step_is_store_ready

    body = grounded_event_bus_handler_body(
        "automated_reminders", ["event_bus", "workflow"]
    )
    wrapped = "def handle(payload):\n" + body + "\n"
    assert handler_satisfies_event_bus_contract(wrapped, require_prepared_step=True)
    steps = event_bus_steps_from_handler(body)
    assert steps == [(1, True)]
    assert grounded_event_bus_topic("automated_reminders") in body
    assert '"tool": "event_bus"' in body
    assert EVENT_BUS_STEP_CHANNEL in body
    # Runtime shape Store accepts (topic/payload/message/channel/tool).
    sample_input = {
        "topic": grounded_event_bus_topic("automated_reminders"),
        "payload": {"reference": "R1"},
        "message": "reminder due",
        "channel": EVENT_BUS_STEP_CHANNEL,
        "tool": "event_bus",
    }
    assert event_bus_step_is_store_ready(sample_input)


def test_writer_replaces_unprepared_reminders_with_grounded(tmp_path):
    """Live sess_d5789a91 class: do not keep a stub PRODUCT refuses at step_0."""
    from app.factory.build.roles_handlers import _capability_handler_body

    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("automated_reminders", ["event_bus", "workflow"], "COMPOSE")),
        store_ids={"event_bus", "workflow"},
    )
    assert PRODUCT_EVENT_BUS_STEP_0_HALT in compiled.text
    body = _capability_handler_body(
        "automated_reminders", ["event_bus", "workflow"]
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "automated_reminders.py").write_text(
        "CAPABILITY_ID = 'automated_reminders'\n"
        "BLOCK_IDS = ['event_bus', 'workflow']\n"
        "def handle(payload):\n" + body + "\n",
        encoding="utf-8",
    )
    assert event_bus_workflow_handler_errors(tmp_path, compiled) == []
    assert_event_bus_workflow_handlers(tmp_path, compiled)


def test_unprepared_first_child_names_store_step_0(tmp_path):
    """Store 0-indexes; factory AST still labels the first list child step_1."""
    compiled = compile_brief(
        _VetCare(),
        _Plan(_Cap("automated_reminders", ["event_bus", "workflow"], "COMPOSE")),
        store_ids={"event_bus", "workflow"},
    )
    actions = tmp_path / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "automated_reminders.py").write_text(
        UNPREPARED_HANDLER, encoding="utf-8"
    )
    errors = event_bus_workflow_handler_errors(tmp_path, compiled)
    assert any("automated_reminders" in e and "step_0" in e for e in errors)
    with pytest.raises(EventBusWorkflowHalt) as halted:
        assert_event_bus_workflow_handlers(tmp_path, compiled)
    assert "step_0" in str(halted.value)
    assert PRODUCT_EVENT_BUS_STEP_0_HALT.split(":")[0] in str(halted.value) or "step_0" in str(halted.value)
