"""Accept-payload contracts for the blocks that failed residential-lettings pilot.

Live Platforms card (hash b36090a424db) refused export because
``test_every_capability_route_accepts_payload`` collected:

1. unit_registry — Missing required field: property_reference_code
2. notification — missing channel/message
3. team — NoneType has no attribute lower
4. workflow — missing steps
5. document_engine — expected str/bytes, got dict

These tests lock the shared ``prepare_block_input`` / sample-payload helpers
so NEW platforms inherit the fix. They do not weaken the pilot suite.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

from app.factory.build.block_inputs import (
    STORE_NOTIFICATION_CHANNELS,
    align_spec_to_handler_fields,
    align_spec_to_handler_source,
    ensure_workflow_result,
    handler_field_contracts,
    notification_channel,
    prepare_block_input,
    render_block_inputs_module,
    sample_channel_value,
    sanitize_python_identifier,
    split_execute_action,
    workflow_result_payload,
)
from app.factory.build.roles import (
    _constraint_guard,
    _handler_module,
    _render_models,
    _sample_payload,
    _sample_value,
    _templated_body,
)
from app.factory.build.roles_constants import _DISPATCH_RUNTIME
from app.factory.build.workflow_accept import event_bus_step_is_store_ready


# -- notification ---------------------------------------------------------


def test_notification_payload_has_channel_and_message():
    """Caller domain record must not need channel/message (coder contract)."""
    domain = {"reference": "V1", "status": "open", "quantity": 1}
    out = prepare_block_input("notification", domain, roster=["notification", "team"])
    assert out["channel"] == "mcp"
    assert isinstance(out["message"], str) and out["message"]
    assert out["block"] == "team"
    # Domain columns preserved for adapters that fold them into input.
    assert out["reference"] == "V1"


def test_notification_does_not_overwrite_caller_supplied_fields():
    domain = {"channel": "mcp", "message": "hello", "block": "database"}
    out = prepare_block_input("notification", domain, roster=["notification"])
    assert out["message"] == "hello"
    assert out["block"] == "database"
    assert out["channel"] == "mcp"


def test_sample_channel_value_prefers_store_known():
    assert sample_channel_value() == "email"
    assert sample_channel_value(["sms", "email"]) == "email"
    assert sample_channel_value(["sms", "push"]) == "sms"
    assert notification_channel("sample") == "mcp"
    assert notification_channel("in_process") == "mcp"
    assert notification_channel("sms") == "mcp"
    assert notification_channel("email") == "mcp"
    assert notification_channel("email", {"to": "clinic@example.com"}) == "email"
    assert notification_channel("mcp") == "mcp"


def test_notification_rewrites_sample_channel_to_mcp():
    """Live sess_67fe60f7: schema sample ``channel=sample`` hit the Store.

    ``RuntimeError: Unknown channel: sample`` on automated_reminders
    accept-payload and on workflow step_0 (notification).
    """
    domain = {
        "pet_name": "Nala",
        "reminder_type": "vaccination",
        "channel": "sample",
        "message": "vaccination due",
    }
    out = prepare_block_input(
        "notification", domain, roster=["notification", "event_bus"]
    )
    assert out["channel"] == "mcp"
    assert out["channel"] != "sample"
    assert out["channel"] in STORE_NOTIFICATION_CHANNELS
    assert out["message"] == "vaccination due"
    assert out["block"] == "event_bus"


def test_notification_keeps_formed_email_channel():
    domain = {
        "channel": "email",
        "to": "clinic@example.com",
        "message": "vaccination due",
    }
    out = prepare_block_input("notification", domain, roster=["notification"])
    assert out["channel"] == "email"
    assert out["to"] == "clinic@example.com"


# -- workflow -------------------------------------------------------------


def test_workflow_payload_has_steps_built_from_roster():
    domain = {"reference": "T1", "status": "open"}
    out = prepare_block_input(
        "workflow",
        domain,
        roster=["workflow", "database", "notification"],
        entity="pet_record",
    )
    assert isinstance(out["steps"], list) and out["steps"]
    assert out["steps"][0]["block"] == "database"
    assert "block_id" not in out["steps"][0]
    # Nested prepare: database step must not inherit raw domain JSON
    # (live PRODUCT: workflow result error → no such table records).
    assert out["steps"][0]["input"]["table"] == "pet_record"
    assert out["steps"][1]["block"] == "notification"
    assert out["steps"][1]["input"]["channel"] == "mcp"
    assert out["steps"][1]["input"]["message"]
    # sess_07dff0eaf8f64186: schema-sample has no result key; Store workflow
    # reads input['result'] / out['result'] and wraps KeyError as RuntimeError.
    assert out.get("result") not in (None, "")


def test_workflow_keeps_explicit_steps():
    steps = [{"block": "team", "input": {"user_id": "u1"}}]
    out = prepare_block_input("workflow", {"steps": steps}, roster=["workflow", "team"])
    assert len(out["steps"]) == 1
    assert out["steps"][0]["block"] == "team"
    assert out["steps"][0]["input"]["user_id"] == "u1"
    # Existing coder-built steps must still be prepared (sess_4fba2a2).
    assert isinstance(out["steps"][0]["input"]["name"], str)
    assert out["steps"][0]["input"]["name"]
    assert out.get("result") not in (None, "")


def test_schema_sample_workflow_prepare_supplies_result_key():
    """Live sess_07dff0eaf8f64186: schema-sample POST has no result key."""
    sample = _sample_payload(
        {
            "fields": [
                {"name": "reference", "type": "str", "required": True},
                {"name": "status", "type": "str", "required": True},
            ]
        }
    )
    assert "result" not in sample
    out = prepare_block_input(
        "workflow",
        sample,
        roster=["workflow", "event_bus"],
    )
    assert out.get("result") not in (None, "")
    assert workflow_result_payload(sample) not in (None, "")
    filled = ensure_workflow_result({"steps": [{"block": "event_bus", "input": {"topic": "t"}}]})
    assert filled["result"] == {"topic": "t"}
    kept = ensure_workflow_result({"result": {"kept": True}, "steps": []})
    assert kept["result"] == {"kept": True}


def test_team_payload_never_passes_none_to_lowercased_fields():
    domain = {"name": None, "slug": None, "role": None, "user_id": None}
    out = prepare_block_input("team", domain, product_name="Residential Lettings")
    for key in ("user_id", "name", "slug"):
        assert isinstance(out[key], str) and out[key]
        out[key].lower()  # must not raise — the live AttributeError


def test_team_injects_resource_id_when_preconditions_exist(monkeypatch):
    fake = types.ModuleType("app.preconditions")
    fake.resource_id = lambda block_id: "team_abc123ff" if block_id == "team" else None
    monkeypatch.setitem(sys.modules, "app.preconditions", fake)
    out = prepare_block_input("team", {"reference": "x"})
    assert out["team_id"] == "team_abc123ff"


def test_team_replaces_domain_looking_team_id(monkeypatch):
    """Live veterinarian_availability: domain string as team_id → access denied."""
    fake = types.ModuleType("app.preconditions")
    fake.resource_id = lambda block_id: "team_4f473e37589a69bb"
    monkeypatch.setitem(sys.modules, "app.preconditions", fake)
    out = prepare_block_input("team", {"team_id": "sample", "name": "Dr Lee"})
    assert out["team_id"] == "team_4f473e37589a69bb"


def test_team_ensure_all_when_resource_id_empty(monkeypatch):
    state = {"id": None}

    def _resource_id(block_id):
        return state["id"] if block_id == "team" else None

    def _ensure_all():
        state["id"] = "team_deadbeefcafe01"
        return {"ids": {"team": state["id"]}, "errors": {}}

    fake = types.ModuleType("app.preconditions")
    fake.resource_id = _resource_id
    fake.ensure_all = _ensure_all
    monkeypatch.setitem(sys.modules, "app.preconditions", fake)
    out = prepare_block_input("team", {"reference": "x"})
    assert out["team_id"] == "team_deadbeefcafe01"


# -- document_engine ------------------------------------------------------


def test_document_engine_rejects_dict_path_and_supplies_text():
    """Live TypeError: expected str/bytes/PathLike, got dict."""
    domain = {
        "reference": "A1",
        "file_path": {"name": "lease.pdf", "meta": True},
        "attachment_path": {"nested": True},
    }
    out = prepare_block_input("document_engine", domain)
    for key in ("file_path", "pdf_path", "attachment_path", "path"):
        if key in out:
            assert isinstance(out[key], (str, bytes)), (key, out[key])
    assert isinstance(out.get("text"), str) and out["text"]
    # Live veterinary-care: text alone is refused — a real pdf path is required.
    assert Path(out["pdf_path"]).is_file()
    assert Path(out["file_path"]).is_file()


def test_document_engine_keeps_existing_attachment_path(tmp_path):
    real = tmp_path / "lease.pdf"
    real.write_bytes(b"%PDF-1.1\n")
    domain = {"attachment_path": str(real), "reference": "A1"}
    out = prepare_block_input("document_engine", domain)
    assert out["attachment_path"] == str(real)
    assert out["file_path"] == str(real)
    assert out["pdf_path"] == str(real)


def test_document_engine_replaces_sample_placeholder_path():
    """_sample_value emits 'sample' for obligated attachment_path — not a file."""
    out = prepare_block_input(
        "document_engine", {"attachment_path": "sample", "reference": "A1"}
    )
    assert out["attachment_path"] == "sample"
    assert Path(out["pdf_path"]).is_file()
    assert out["pdf_path"] != "sample"
    assert Path(out["file_path"]).is_file()


# -- analytics (historical envelope miss) ---------------------------------


def test_analytics_lifts_metric_value_and_defaults():
    out = prepare_block_input(
        "analytics",
        {"input": {"metric": "monthly_rent_gbp", "value": 1450.0}, "status": "vacant"},
    )
    assert out["metric"] == "monthly_rent_gbp"
    assert out["value"] == 1450.0


# -- veterinary-care CONTRACT record fields (sess_a4aa977d2dff4c55) -------

_LIVE_TOPIC = "RuntimeError: topic required"
_LIVE_DOC = (
    "RuntimeError: No input files provided (pdf/docx/xlsx). "
    "Pass file_path as pdf_path, docx_path, or xlsx_path."
)
_LIVE_SQL = "Query failed: missing sql or table"
_LIVE_TEAM = "Team access denied"


def _refuse_like_live(block_id, payload):
    """The four live Store answers, used to prove prepare repairs them."""
    data = payload if isinstance(payload, dict) else {}
    if block_id == "event_bus":
        if not (isinstance(data.get("topic"), str) and data["topic"].strip()):
            return {"status": "error", "error": _LIVE_TOPIC}
    if block_id == "document_engine":
        paths = [
            data.get(k)
            for k in ("file_path", "pdf_path", "docx_path", "xlsx_path")
        ]
        if not any(isinstance(p, str) and Path(p).is_file() for p in paths):
            return {"status": "error", "error": _LIVE_DOC}
    if block_id == "database":
        sql = data.get("sql")
        table = data.get("table") or data.get("table_name")
        if not ((isinstance(sql, str) and sql.strip()) or (isinstance(table, str) and table.strip())):
            return {"status": "error", "error": _LIVE_SQL}
    if block_id == "team":
        tid = data.get("team_id")
        if not (isinstance(tid, str) and tid.startswith("team_") and len(tid) > 8):
            return {"status": "error", "error": _LIVE_TEAM}
    return {"status": "ok", "block": block_id}


def test_event_bus_synthesizes_topic_from_domain_record():
    domain = {"reminder_type": "vaccination", "pet_name": "Nala", "status": "open"}
    raw = _refuse_like_live("event_bus", domain)
    assert raw["error"] == _LIVE_TOPIC
    out = prepare_block_input("event_bus", domain)
    assert out["topic"] == "vaccination"
    assert _refuse_like_live("event_bus", out)["status"] == "ok"


def test_event_bus_topic_from_record_summary_when_no_event_field():
    out = prepare_block_input("event_bus", {"reference": "R-1", "status": "open"})
    assert isinstance(out["topic"], str) and out["topic"]
    assert _refuse_like_live("event_bus", out)["status"] == "ok"


def test_database_synthesizes_table_from_domain_record():
    domain = {"reference": "dash-1", "status": "open", "quantity": 2}
    raw = _refuse_like_live("database", domain)
    assert raw["error"] == _LIVE_SQL
    # The table is the declared schema's (the capability entity) ...
    out = prepare_block_input("database", domain, entity="zorblat_ticket")
    assert out["table"] == "zorblat_ticket"
    assert out["values"]["reference"] == "dash-1"
    assert _refuse_like_live("database", out)["status"] == "ok"
    # ... and with no entity and no named table, none is invented.
    assert "table" not in prepare_block_input("database", domain)


def test_database_uses_capability_entity_not_records():
    """Live sess_66a387b: table=records is not an Alembic entity table."""
    domain = {"pet_name": "Nala", "status": "open"}
    out = prepare_block_input("database", domain, entity="pet_record")
    assert out["table"] == "pet_record"
    assert out["table"] != "records"
    assert out["values"]["pet_name"] == "Nala"


def test_event_bus_carries_payload_and_channel():
    """Live PRODUCT: topic alone still failed notify (payload/message/channel)."""
    out = prepare_block_input(
        "event_bus",
        {"pet_name": "Rex", "appointment_date": "2026-09-10", "reminder_type": "vaccination"},
        product_name="VetCare Hub",
    )
    assert out["topic"] == "vaccination"
    assert out["event"] == "vaccination"
    assert out["channel"] == "mcp"
    assert out["payload"]["pet_name"] == "Rex"
    assert out["data"]["pet_name"] == "Rex"
    assert isinstance(out["message"], str) and out["message"]
    assert out["block"] == "event_bus"
    assert out["tool"] == "event_bus"
    assert event_bus_step_is_store_ready(out) is True
    assert "pet_name" not in out


def test_event_bus_mcp_target_is_not_a_peer_block():
    """Live sess_d5789a91: roster had database; notify used tool=database."""
    out = prepare_block_input(
        "event_bus",
        {"pet_name": "Nala", "reminder_type": "vaccination", "status": "open"},
        roster=["event_bus", "database", "workflow"],
        product_name="VetCare Hub",
    )
    assert out["tool"] == "event_bus"
    assert out["block"] == "event_bus"
    assert event_bus_step_is_store_ready(out) is True

    flow = prepare_block_input(
        "workflow",
        {"pet_name": "Nala", "reminder_type": "vaccination", "status": "open"},
        roster=["event_bus", "database", "workflow"],
        entity="reminder",
        product_name="VetCare Hub",
        default_actions={"event_bus": "publish"},
    )
    bus = flow["steps"][0]
    assert bus["block"] == "event_bus"
    assert bus["input"]["tool"] == "event_bus"
    assert bus["input"]["block"] == "event_bus"
    assert event_bus_step_is_store_ready(bus["input"]) is True


def test_event_bus_rewrites_sample_channel_to_mcp():
    """Live sess_67fe60f7: workflow step_2 (event_bus) used channel=sample."""
    out = prepare_block_input(
        "event_bus",
        {
            "pet_name": "Nala",
            "reminder_type": "vaccination",
            "channel": "sample",
        },
        product_name="VetCare Hub",
    )
    assert out["topic"] == "vaccination"
    assert out["channel"] == "mcp"
    assert out["channel"] != "sample"
    assert isinstance(out["message"], str) and out["message"]
    assert isinstance(out["payload"], dict)


def _event_bus_like_live_sess_4fba2a2(payload):
    """Store event_bus as PRODUCT saw it after #314.

    Missing topic used to raise ``topic required``. After #314 a schema
    sample may carry ``channel=email`` (not ``sample``); the notify path
    then runs and the Store workflow records only ``status=error``.
    """
    data = payload if isinstance(payload, dict) else {}
    topic = data.get("topic")
    if not (isinstance(topic, str) and topic.strip()):
        return {"status": "error", "error": "error"}
    channel = str(data.get("channel") or "").strip().lower()
    if channel == "sample":
        return {"status": "error", "error": "Unknown channel: sample"}
    if channel == "email":
        to = data.get("to") or data.get("email")
        if not (isinstance(to, str) and "@" in to):
            return {"status": "error"}
    if channel and channel not in STORE_NOTIFICATION_CHANNELS:
        return {"status": "error", "error": f"Unknown channel: {channel}"}
    if not isinstance(data.get("payload"), dict) and not (
        isinstance(data.get("message"), str) and data.get("message")
    ):
        return {"status": "error"}
    if channel == "mcp" and not (
        (isinstance(data.get("block"), str) and data["block"].strip())
        or (isinstance(data.get("tool"), str) and data["tool"].strip())
    ):
        return {"status": "error", "error": "block or tool name required for MCP channel"}
    return {"status": "ok", "block": "event_bus"}


def test_sess_4fba2a2_coder_built_workflow_steps_prepare_event_bus():
    """Live appointment_scheduling: coder steps forwarded the schema sample.

    sess_4fba2a2865044a82 / VetCare Hub on tip 6b6e1e5 (#314):

        appointment_scheduling rejected a payload built from its own schema:
        workflow: step_1 (event_bus): error
    """
    sample = {
        "pet_name": "Rex",
        "appointment_date": "2026-09-10",
        "veterinarian_name": "Dr Lee",
        "status": "open",
        "reference": "APT-1",
        "channel": "email",
    }
    stale = {
        "steps": [
            {"block": "database", "input": dict(sample)},
            {"block": "event_bus", "input": dict(sample)},
            {"block": "queue", "input": dict(sample)},
        ]
    }
    assert _event_bus_like_live_sess_4fba2a2(stale["steps"][1]["input"])[
        "status"
    ] == "error"

    out = prepare_block_input(
        "workflow",
        stale,
        roster=["database", "event_bus", "queue", "workflow"],
        entity="appointment",
        product_name="VetCare Hub",
        default_actions={"event_bus": "publish", "database": "insert", "queue": "enqueue"},
    )
    by_block = {step["block"]: step for step in out["steps"]}
    bus = by_block["event_bus"]["input"]
    assert by_block["event_bus"].get("action") == "publish"
    assert bus["topic"]
    assert bus["channel"] == "mcp"
    assert bus["channel"] != "sample"
    assert bus["channel"] != "email"
    assert isinstance(bus.get("payload"), dict)
    assert bus["payload"]["pet_name"] == "Rex"
    assert isinstance(bus.get("message"), str) and bus["message"]
    assert _event_bus_like_live_sess_4fba2a2(bus)["status"] == "ok"
    assert by_block["database"]["input"]["table"] == "appointment"
    assert by_block["database"]["input"]["table"] != "records"
    assert event_bus_step_is_store_ready(bus) is True
    assert bus["block"]
    assert "pet_name" not in bus or bus.get("topic")


def test_sess_4fba2a2_block_id_steps_are_normalized_and_prepared():
    """Coder steps often use block_id; workflow only reads step.get('block')."""
    sample = {"pet_name": "Rex", "appointment_date": "2026-09-10", "status": "open"}
    out = prepare_block_input(
        "workflow",
        {"steps": [{"block_id": "event_bus", "input": sample}]},
        roster=["event_bus", "workflow"],
        entity="appointment",
        product_name="VetCare Hub",
        default_actions={"event_bus": "publish"},
    )
    step = out["steps"][0]
    assert step["block"] == "event_bus"
    assert step["action"] == "publish"
    assert _event_bus_like_live_sess_4fba2a2(step["input"])["status"] == "ok"


def test_queue_coerces_numeric_strings():
    """Live PRODUCT: Store queue / work_queue refused str where they want int."""
    out = prepare_block_input(
        "queue",
        {"id": "42", "priority": "1", "delay": "0", "label": "id-1"},
    )
    assert out["id"] == 42
    assert out["item_id"] == 42
    assert out["priority"] == 1
    assert out["delay"] == 0
    assert out["label"] == "id-1"
    assert "id-1" not in {out["id"], out["priority"], out["item_id"]}


def test_queue_non_numeric_priority_becomes_zero():
    """Live sess_a69c8ce: ``priority > n`` TypeError when priority is 'sample'."""
    out = prepare_block_input(
        "queue",
        {"priority": "sample", "id": "id-1", "pet_name": "Nala"},
    )
    assert out["priority"] == 0
    assert "id" not in out
    assert "item_id" not in out
    assert out["pet_name"] == "Nala"
    assert 0 > -1  # the compare Store performs, now both ints


def test_database_records_table_retargets_to_entity():
    """#306 leftover table=records must not survive when ENTITY is known."""
    out = prepare_block_input(
        "database",
        {"table": "records", "pet_name": "Nala"},
        entity="pet_record",
    )
    assert out["table"] == "pet_record"
    assert out["table"] != "records"


def test_database_sql_a_handler_passes_is_never_rewritten_from_its_text():
    """The text-rewrite of 'FROM records' is gone: the emitted store builds
    SQLAlchemy Core statements from the declared tables, and a handler's own
    SQL is its own -- judged by the harness, never patched by a regex."""
    sql = "SELECT * FROM zorblat_legacy"
    out = prepare_block_input(
        "database",
        {"sql": sql, "pet_name": "Nala"},
        entity="pet_record",
    )
    assert out["sql"] == sql
    assert "table" not in out


def test_database_keeps_caller_sql():
    out = prepare_block_input("database", {"sql": "SELECT 1", "status": "open"})
    assert out["sql"] == "SELECT 1"
    assert _refuse_like_live("database", out)["status"] == "ok"


def test_document_engine_domain_record_is_refused_until_prepared():
    domain = {"pet_name": "Nala", "notes": "annual exam"}
    raw = _refuse_like_live("document_engine", domain)
    assert raw["error"] == _LIVE_DOC
    out = prepare_block_input("document_engine", domain)
    assert _refuse_like_live("document_engine", out)["status"] == "ok"


def test_emitted_module_matches_factory_for_live_contract_blocks(tmp_path, monkeypatch):
    """Generated app/block_inputs.py must repair the same four refusals."""
    fake = types.ModuleType("app.preconditions")
    fake.resource_id = lambda block_id: "team_4f473e37589a69bb"
    monkeypatch.setitem(sys.modules, "app.preconditions", fake)
    path = tmp_path / "block_inputs.py"
    path.write_text(render_block_inputs_module(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("emitted_block_inputs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    domain = {"reference": "V1", "status": "open", "quantity": 1}
    for bid in ("event_bus", "document_engine", "database", "team"):
        # The handler wrapper always supplies the capability's entity; the
        # database table is that declared table, never an invented default.
        extra = {"entity": "pet_record"} if bid == "database" else {}
        factory = prepare_block_input(bid, domain, product_name="VetCare Hub", **extra)
        emitted = mod.prepare_block_input(bid, domain, product_name="VetCare Hub", **extra)
        assert _refuse_like_live(bid, factory)["status"] == "ok", (bid, factory)
        assert _refuse_like_live(bid, emitted)["status"] == "ok", (bid, emitted)
        if bid == "event_bus":
            assert factory["topic"] and emitted["topic"]
            assert factory["channel"] == emitted["channel"] == "mcp"
            assert factory["payload"] and emitted["payload"]
            assert factory["block"] == emitted["block"] == "event_bus"
            assert event_bus_step_is_store_ready(factory)
            assert event_bus_step_is_store_ready(emitted)
        if bid == "database":
            assert factory["table"] == emitted["table"] == "pet_record"
        if bid == "document_engine":
            assert Path(factory["pdf_path"]).is_file()
            assert Path(emitted["pdf_path"]).is_file()
        if bid == "team":
            assert factory["team_id"] == emitted["team_id"] == "team_4f473e37589a69bb"
    entity_factory = prepare_block_input(
        "database", domain, product_name="VetCare Hub", entity="pet_record"
    )
    entity_emitted = mod.prepare_block_input(
        "database", domain, product_name="VetCare Hub", entity="pet_record"
    )
    assert entity_factory["table"] == entity_emitted["table"] == "pet_record"
    queue_domain = {"id": "7", "priority": "2", "label": "id-1"}
    queue_factory = prepare_block_input("queue", queue_domain)
    queue_emitted = mod.prepare_block_input("queue", queue_domain)
    assert queue_factory["id"] == queue_emitted["id"] == 7
    assert queue_factory["item_id"] == queue_emitted["item_id"] == 7
    assert queue_factory["priority"] == queue_emitted["priority"] == 2
    sample_domain = {
        "pet_name": "Nala",
        "reminder_type": "vaccination",
        "channel": "sample",
    }
    for bid in ("notification", "event_bus"):
        factory = prepare_block_input(
            bid, sample_domain, roster=["notification", "event_bus"]
        )
        emitted = mod.prepare_block_input(
            bid, sample_domain, roster=["notification", "event_bus"]
        )
        assert factory["channel"] == emitted["channel"] == "mcp", (bid, factory, emitted)
    assert queue_factory["label"] == queue_emitted["label"] == "id-1"
    coder_steps = {
        "steps": [
            {
                "block": "event_bus",
                "input": {
                    "pet_name": "Rex",
                    "appointment_date": "2026-09-10",
                    "channel": "email",
                },
            }
        ]
    }
    factory_flow = prepare_block_input(
        "workflow",
        coder_steps,
        roster=["event_bus", "workflow"],
        entity="appointment",
        default_actions={"event_bus": "publish"},
    )
    emitted_flow = mod.prepare_block_input(
        "workflow",
        coder_steps,
        roster=["event_bus", "workflow"],
        entity="appointment",
        default_actions={"event_bus": "publish"},
    )
    assert factory_flow["steps"][0]["input"]["channel"] == "mcp"
    assert emitted_flow["steps"][0]["input"]["channel"] == "mcp"
    assert factory_flow["steps"][0]["input"]["topic"]
    assert emitted_flow["steps"][0]["input"]["topic"]
    assert factory_flow["steps"][0]["action"] == emitted_flow["steps"][0]["action"] == "publish"


# -- property_reference_code / sample ↔ handler alignment -----------------


def test_a_handler_check_never_makes_a_field_required():
    """Owner ruling 2026-10-05: required fields come ONLY from the declared
    schema. ``"k" not in payload`` in a handler declares nothing."""
    body = (
        "    if 'property_reference_code' not in payload:\n"
        "        return {'ok': False, 'error': 'Missing required field: property_reference_code'}\n"
    )
    assert handler_field_contracts(body) == {}
    spec = {"entity": "unit", "fields": [{"name": "unit_reference", "type": "str", "required": True}]}
    aligned, changed = align_spec_to_handler_source(spec, body)
    assert changed == []
    assert [f["name"] for f in aligned["fields"]] == ["unit_reference"]


def test_an_invented_handler_s_parameters_and_key_reads_are_never_required():
    """Owner ruling 2026-10-05, with a handler no product has: it takes
    parameters and reads keys the schema does not declare. Live, the generated
    block wrapper's ``kw.get("params")`` made every route demand ``params``.
    Only the schema's declared required fields are required."""
    handler = (
        "def zorblat_handle(payload, params=None, *a, **kw):\n"
        "    quux = kw.get('quux_option')\n"
        "    if quux is None and len(a) > 1:\n"
        "        quux = a[1]\n"
        "    if params is None:\n"
        "        params = payload.get('frob_params')\n"
        "    if 'wibble_ref' not in payload or not payload.get('wobble_id'):\n"
        "        return {'ok': False, 'error': 'Missing required field: wibble_ref'}\n"
        "    for name in ('flim_a', 'flim_b'):\n"
        "        if name not in payload:\n"
        "            return {'ok': False}\n"
        "    return {'ok': True}\n"
    )
    declared = {
        "entity": "zorblat",
        "fields": [
            {"name": "reference", "type": "str", "required": True},
            {"name": "zorb_note", "type": "str", "required": False},
        ],
    }
    aligned, changed = align_spec_to_handler_source(declared, handler)
    assert changed == []
    assert aligned["fields"] == declared["fields"]
    required = [f["name"] for f in aligned["fields"] if f.get("required")]
    assert required == ["reference"]
    guard = _constraint_guard(aligned)
    for undeclared in ("params", "quux_option", "frob_params", "wibble_ref",
                       "wobble_id", "flim_a", "flim_b", "kw", "a"):
        assert repr(undeclared) not in guard, undeclared


def test_rosters_and_is_missing_checks_add_no_field():
    """Live VetConnect named several fields in one roster and message. Under
    the owner's rule none of them is declared by the handler: the schema
    declares fields. No junk name (``and``, ``steps``) appears either."""
    body = (
        "    needed = ['pet_name', 'owner_name', 'appointment_date', "
        "'veterinarian_name']\n"
        "    missing = [n for n in needed if n not in payload]\n"
        "    if missing:\n"
        "        return {'ok': False, 'error': 'Missing required fields: ' "
        "+ ', '.join(missing)}\n"
        "    if not payload.get('owner_id'):\n"
        "        return {'ok': False, 'error': 'owner_id is missing and "
        "must be a non-empty string'}\n"
    )
    contracts = handler_field_contracts(body)
    for junk in ("and", "steps", "team_id"):
        assert junk not in contracts
    aligned, changed = align_spec_to_handler_source({"fields": []}, body)
    assert aligned["fields"] == [] and changed == []


def test_handler_contracts_keep_domain_status_and_channel():
    """``status`` / ``channel`` are domain columns on new-domain pilots.

    The envelope skip list used to drop them, so reminder ``channel`` never
    reached the sample payload. The vocabulary the code checks is mined; the
    roster loop makes nothing required.
    """
    body = (
        "        for name in ('service_date', 'service_type', 'capacity', 'status'):\n"
        "            if name not in payload:\n"
        "                return {'ok': False, 'error': 'Missing required fields'}\n"
        "        if payload.get('channel') not in ('email', 'sms', 'push'):\n"
        "            return {'ok': False, 'error': 'channel is invalid'}\n"
    )
    contracts = handler_field_contracts(body)
    assert contracts["channel"]["allowed_values"] == ["email", "sms", "push"]
    assert "status" not in contracts
    assert "ok" not in contracts
    assert "error" not in contracts
    assert "steps" not in contracts
    assert all("required" not in c for c in contracts.values())


def test_handler_field_contracts_mine_vetconnect_enums_types_bounds():
    """Lock the 2026-09-04 Platforms card: enums, bool, int >= 0, non-empty ids."""
    body = (
        "    if 'clinic_id' not in payload or not payload.get('clinic_id'):\n"
        "        return {'ok': False, 'error': 'clinic_id is missing and "
        "must be a non-empty string'}\n"
        "    if payload.get('role') not in ('veterinarian', 'technician', "
        "'receptionist', 'practice_manager', 'pet_owner'):\n"
        "        return {'ok': False, 'error': 'role must be one of "
        "{veterinarian, technician, receptionist, practice_manager, "
        "pet_owner}'}\n"
        "    if payload.get('access_level') not in ('admin', 'standard', "
        "'read_only'):\n"
        "        return {'ok': False, 'error': 'access_level must be one of "
        "{admin, standard, read_only}'}\n"
        "    if not isinstance(payload.get('is_active'), bool):\n"
        "        return {'ok': False, 'error': 'is_active must be a boolean'}\n"
        "    if not isinstance(payload.get('login_count'), int) "
        "or payload.get('login_count') < 0:\n"
        "        return {'ok': False, 'error': 'login_count must be an "
        "integer >= 0'}\n"
        "    if payload.get('reminder_type') not in ('appointment', 'vaccine', "
        "'follow_up'):\n"
        "        return {'ok': False, 'error': 'reminder_type is invalid'}\n"
        "    if payload.get('channel') not in ('email', 'sms', 'push'):\n"
        "        return {'ok': False, 'error': 'channel is invalid'}\n"
        "    if not payload.get('message_template'):\n"
        "        return {'ok': False, 'error': 'message_template must be a "
        "non-empty string'}\n"
    )
    contracts = handler_field_contracts(body)
    assert contracts["role"]["allowed_values"][0] == "veterinarian"
    assert set(contracts["role"]["allowed_values"]) == {
        "veterinarian",
        "technician",
        "receptionist",
        "practice_manager",
        "pet_owner",
    }
    assert contracts["access_level"]["allowed_values"] == [
        "admin",
        "standard",
        "read_only",
    ]
    assert contracts["is_active"]["type"] == "bool"
    assert contracts["login_count"]["type"] == "int"
    assert contracts["login_count"]["min"] == 0
    assert all("required" not in c for c in contracts.values())
    assert "and" not in contracts
    assert "or" not in contracts
    assert "class" not in contracts
    assert contracts["channel"]["allowed_values"] == ["email", "sms", "push"]
    assert contracts["reminder_type"]["allowed_values"] == [
        "appointment",
        "vaccine",
        "follow_up",
    ]
    assert "message_template" not in contracts  # a presence check declares nothing


def test_align_spec_merges_vocab_and_types_onto_bare_str_fields():
    """Declared spec fields pick up handler enums -- not stay bare str. An
    undeclared contract (clinic_id) adds no field, and required stays the
    schema's."""
    spec = {
        "entity": "dashboard",
        "fields": [
            {"name": "role", "type": "str", "required": True},
            {"name": "is_active", "type": "str", "required": True},
            {"name": "login_count", "type": "str", "required": False},
        ],
    }
    contracts = {
        "role": {
            "name": "role",
            "type": "str",
            "allowed_values": [
                "veterinarian",
                "technician",
                "receptionist",
                "practice_manager",
                "pet_owner",
            ],
        },
        "is_active": {"name": "is_active", "type": "bool"},
        "login_count": {"name": "login_count", "type": "int", "min": 0},
        "clinic_id": {"name": "clinic_id", "type": "str"},
    }
    aligned, changed = align_spec_to_handler_fields(spec, contracts)
    assert "clinic_id" not in changed
    assert "role" in changed
    by_name = {f["name"]: f for f in aligned["fields"]}
    assert "clinic_id" not in by_name
    assert by_name["login_count"]["required"] is False
    payload = _sample_payload(aligned)
    assert payload["role"] == "veterinarian"
    assert payload["is_active"] is True
    assert isinstance(payload["login_count"], int) and payload["login_count"] >= 0
    assert by_name["login_count"]["type"] == "int"
    assert by_name["is_active"]["type"] == "bool"


def test_align_spec_to_handler_source_covers_vetconnect_caps():
    """Synthetic multi-capability VetConnect miss → sample satisfies guards."""
    handlers = {
        "appointment_scheduling": (
            "    needed = ['pet_name', 'owner_name', 'appointment_date', "
            "'veterinarian_name']\n"
            "    missing = [n for n in needed if n not in payload "
            "or payload[n] in (None, '')]\n"
            "    if missing:\n"
            "        return {'ok': False, 'error': 'Missing required fields: ' "
            "+ ', '.join(missing)}\n"
            "    return {'ok': True}\n"
        ),
        "veterinarian_availability_management": (
            "    required = ['service_date', 'service_type', 'capacity', 'status']\n"
            "    missing = [n for n in required if n not in payload "
            "or payload[n] in (None, '')]\n"
            "    if missing:\n"
            "        return {'ok': False, 'error': 'Missing required fields: "
            "service_date, service_type, capacity, status'}\n"
            "    return {'ok': True}\n"
        ),
        "pet_records": (
            "    if not payload.get('owner_id'):\n"
            "        return {'ok': False, 'error': 'owner_id is missing and "
            "must be a non-empty string'}\n"
            "    return {'ok': True}\n"
        ),
        "automated_reminders": (
            "    if not payload.get('pet_id'):\n"
            "        return {'ok': False, 'error': 'pet_id is missing and "
            "must be a non-empty string'}\n"
            "    if payload.get('reminder_type') not in ('appointment', 'vaccine'):\n"
            "        return {'ok': False, 'error': 'reminder_type is invalid'}\n"
            "    if payload.get('channel') not in ('email', 'sms'):\n"
            "        return {'ok': False, 'error': 'channel is invalid'}\n"
            "    if not payload.get('message_template'):\n"
            "        return {'ok': False, 'error': 'message_template must be a "
            "non-empty string'}\n"
            "    return {'ok': True}\n"
        ),
        "secure_multi_user_dashboard": (
            "    if not payload.get('clinic_id'):\n"
            "        return {'ok': False, 'error': 'clinic_id is missing and "
            "must be a non-empty string'}\n"
            "    if payload.get('role') not in ('veterinarian', 'technician', "
            "'receptionist', 'practice_manager', 'pet_owner'):\n"
            "        return {'ok': False, 'error': 'role must be one of "
            "{veterinarian, technician, receptionist, practice_manager, "
            "pet_owner}'}\n"
            "    if payload.get('access_level') not in ('admin', 'standard', "
            "'read_only'):\n"
            "        return {'ok': False, 'error': 'access_level must be one of "
            "{admin, standard, read_only}'}\n"
            "    if not isinstance(payload.get('is_active'), bool):\n"
            "        return {'ok': False, 'error': 'is_active must be a boolean'}\n"
            "    if not isinstance(payload.get('login_count'), int) "
            "or payload['login_count'] < 0:\n"
            "        return {'ok': False, 'error': 'login_count must be an "
            "integer >= 0'}\n"
            "    return {'ok': True}\n"
        ),
    }
    # The SCHEMA declares the fields (owner ruling 2026-10-05); the handler
    # only enriches their type / vocabulary / bounds.
    declared_names = {
        "appointment_scheduling": ["pet_name", "owner_name", "appointment_date", "veterinarian_name"],
        "veterinarian_availability_management": ["service_date", "service_type", "capacity", "status"],
        "pet_records": ["owner_id"],
        "automated_reminders": ["pet_id", "reminder_type", "channel", "message_template"],
        "secure_multi_user_dashboard": ["clinic_id", "role", "access_level", "is_active", "login_count"],
    }
    for cid, source in handlers.items():
        declared = {
            "entity": "record",
            "fields": [{"name": "reference", "type": "str", "required": True}]
            + [{"name": n, "type": "str", "required": True} for n in declared_names[cid]],
        }
        aligned, _changed = align_spec_to_handler_source(declared, source)
        assert [f["name"] for f in aligned["fields"]] == [f["name"] for f in declared["fields"]]
        payload = _sample_payload(aligned)
        ns: dict = {"payload": None}

        def _run(body: str, sample: dict, env: dict) -> dict:
            env = dict(env)
            exec("def handle(payload):\n" + body, env)
            return env["handle"](sample)

        out = _run(source, payload, ns)
        assert out.get("ok") is True, (cid, payload, out)
        guard = _constraint_guard(aligned)
        gns: dict = {}
        exec("def handle(payload):\n" + guard + "\n    return {'ok': True}\n", gns)
        guarded = gns["handle"](payload)
        assert guarded.get("ok") is True, (cid, payload, guarded)


def test_is_missing_and_must_be_does_not_mine_english_and():
    """Live sess_e04e9cd: ``and: str = ""`` came from this exact phrase. The
    handler's checks add no field at all; the declared ones render cleanly."""
    body = (
        "    if not payload.get('clinic_id'):\n"
        "        return {'ok': False, 'error': 'clinic_id is missing and "
        "must be a non-empty string'}\n"
        "    if not payload.get('owner_id'):\n"
        "        return {'ok': False, 'error': 'owner_id is missing and "
        "must be a non-empty string'}\n"
    )
    assert "and" not in handler_field_contracts(body)
    aligned, changed = align_spec_to_handler_source(
        {"entity": "record", "fields": [{"name": "reference", "type": "str"}]},
        body,
    )
    assert [f["name"] for f in aligned["fields"]] == ["reference"]
    assert changed == []
    src = _render_models({"secure_multi_user_dashboard": aligned})
    assert "    and:" not in src
    exec(compile(src, "<models>", "exec"), {})


def test_sanitize_python_identifier_remaps_keywords_and_illegal_chars():
    assert sanitize_python_identifier("and") == "and_"
    assert sanitize_python_identifier("class") == "class_"
    assert sanitize_python_identifier("for") == "for_"
    assert sanitize_python_identifier("pet-id") == "pet_id"
    assert sanitize_python_identifier("2fa") == "field_2fa"
    used = {"and_"}
    assert sanitize_python_identifier("and", used=used) == "and__2"


def test_constraint_guard_enforces_required_fields_from_spec():
    spec = {
        "fields": [
            {"name": "property_reference_code", "type": "str", "required": True},
            {"name": "status", "type": "str", "required": True,
             "allowed_values": ["vacant", "occupied"]},
        ]
    }
    body = _constraint_guard(spec)
    ns: dict = {}
    exec("def handle(payload):\n" + body + "\n    return {'ok': True}\n", ns)
    refused = ns["handle"]({"status": "vacant"})
    assert refused["ok"] is False
    assert "property_reference_code" in refused["error"]
    accepted = ns["handle"](
        {"property_reference_code": "P-1", "status": "vacant"}
    )
    assert accepted["ok"] is True


# -- emitted module + wrapper end-to-end ----------------------------------


def test_rendered_block_inputs_module_is_importable(tmp_path):
    path = tmp_path / "block_inputs.py"
    path.write_text(render_block_inputs_module(), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("gen_block_inputs", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = mod.prepare_block_input(
        "notification", {"reference": "x"}, roster=["notification", "workflow"]
    )
    assert out["channel"] == "mcp" and out["message"]
    action, clean = mod.split_execute_action(
        {"action": "render", "name": "lathe"}, default_action="draw"
    )
    assert action == "render" and "action" not in clean


def test_split_execute_action_lifts_and_strips_payload_action():
    """Live makerspace: action in the dict must become the keyword and leave."""
    action, clean = split_execute_action(
        {"action": "render", "name": "lathe", "status": "open"},
        action=None,
        default_action="draw",
    )
    assert action == "render"
    assert "action" not in clean
    assert clean["name"] == "lathe"

    action, clean = split_execute_action(
        {"input": {"action": "insert", "table": "tools"}},
        action=None,
    )
    assert action == "insert"
    assert "action" not in clean
    assert "action" not in clean["input"]
    assert clean["input"]["table"] == "tools"

    action, clean = split_execute_action(
        {"action": "buried", "metric": "x"},
        action="track_event",
    )
    assert action == "track_event"
    assert "action" not in clean

    action, clean = split_execute_action({"name": "press"}, default_action="register")
    assert action == "register"
    assert "action" not in clean


def test_prepare_block_input_never_forwards_action_field():
    out = prepare_block_input(
        "estate_registry",
        {"action": "register", "name": "mill"},
        action="register",
    )
    assert "action" not in out
    assert out["name"] == "mill"


def test_templated_handler_wrapper_prepares_notification_before_execute(tmp_path):
    """Even a naive ``execute(block_id, payload)`` body must send channel/message."""
    module_text = _handler_module(
        "viewing_management",
        ["notification", "workflow", "team"],
        _templated_body(["notification", "workflow", "team"]),
        "deterministic contract template",
        {
            "notification": "send",
            "workflow": "run",
            "team": "get_team_context",
        },
    )
    # Provide the generated helper the wrapper imports without replacing the
    # real ``app`` package (that would break later tests' ``app.factory``).
    block_inputs_mod = types.ModuleType("app.block_inputs")
    exec(render_block_inputs_module(), block_inputs_mod.__dict__)
    handler_path = tmp_path / "handler.py"
    handler_path.write_text(module_text, encoding="utf-8")

    calls = []
    fake_dispatch = types.ModuleType("app.dispatch")

    def _fake_execute(block_id, payload, action=None, params=None):
        calls.append({"block_id": block_id, "payload": dict(payload), "action": action})
        return {"status": "ok", "block": block_id}

    fake_dispatch.execute = _fake_execute
    previous_dispatch = sys.modules.get("app.dispatch")
    previous_block_inputs = sys.modules.get("app.block_inputs")
    sys.modules["app.dispatch"] = fake_dispatch
    sys.modules["app.block_inputs"] = block_inputs_mod
    try:
        spec = importlib.util.spec_from_file_location("viewing_handler", handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.handle({"reference": "V9", "status": "open", "quantity": 2})
    finally:
        if previous_dispatch is None:
            sys.modules.pop("app.dispatch", None)
        else:
            sys.modules["app.dispatch"] = previous_dispatch
        if previous_block_inputs is None:
            sys.modules.pop("app.block_inputs", None)
        else:
            sys.modules["app.block_inputs"] = previous_block_inputs

    assert out.get("ok") is True, out
    by_block = {c["block_id"]: c["payload"] for c in calls}
    assert by_block["notification"]["channel"] == "mcp"
    assert by_block["notification"]["message"]
    assert isinstance(by_block["workflow"]["steps"], list) and by_block["workflow"]["steps"]
    assert isinstance(by_block["team"]["user_id"], str)
    assert isinstance(by_block["team"]["name"], str)
    assert isinstance(by_block["team"]["slug"], str)
    for call in calls:
        assert "action" not in call["payload"], call
        assert call["action"], call


def test_handler_wrapper_lifts_action_out_of_payload_before_dispatch(tmp_path):
    """Regression: execute(block, {action: ...}) must become action= keyword.

    Live Your Platforms findings for makerspace-management named
    ``the action travelled inside the payload`` on dashboard/analytics/
    database/estate_registry. The wrapper is the last seam before
    dispatch; if action still rides in the payload here, WRITER halts.
    """
    buried = (
        "    results = {}\n"
        "    for block_id in BLOCK_IDS:\n"
        "        body = dict(payload)\n"
        "        body['action'] = BLOCK_DEFAULT_ACTIONS.get(block_id) or 'run'\n"
        "        results[block_id] = execute(block_id, body)\n"
        "    return {'ok': True, 'capability': CAPABILITY_ID, 'results': results}\n"
    )
    module_text = _handler_module(
        "dashboards_and_reports",
        ["dashboard", "analytics", "database"],
        buried,
        "coder LLM (makerspace live miss)",
        {
            "dashboard": "render",
            "analytics": "track_event",
            "database": "insert",
        },
    )
    block_inputs_mod = types.ModuleType("app.block_inputs")
    exec(render_block_inputs_module(), block_inputs_mod.__dict__)
    handler_path = tmp_path / "handler.py"
    handler_path.write_text(module_text, encoding="utf-8")

    calls = []
    fake_dispatch = types.ModuleType("app.dispatch")

    def _fake_execute(block_id, payload, action=None, params=None):
        assert "action" not in (payload or {}), (block_id, payload)
        if isinstance(payload, dict) and isinstance(payload.get("input"), dict):
            assert "action" not in payload["input"], (block_id, payload)
        assert action, (block_id, payload)
        calls.append({"block_id": block_id, "payload": dict(payload), "action": action})
        return {"status": "ok", "block": block_id}

    fake_dispatch.execute = _fake_execute
    previous_dispatch = sys.modules.get("app.dispatch")
    previous_block_inputs = sys.modules.get("app.block_inputs")
    sys.modules["app.dispatch"] = fake_dispatch
    sys.modules["app.block_inputs"] = block_inputs_mod
    try:
        spec = importlib.util.spec_from_file_location("makerspace_handler", handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.handle({"name": "lathe", "status": "open"})
    finally:
        if previous_dispatch is None:
            sys.modules.pop("app.dispatch", None)
        else:
            sys.modules["app.dispatch"] = previous_dispatch
        if previous_block_inputs is None:
            sys.modules.pop("app.block_inputs", None)
        else:
            sys.modules["app.block_inputs"] = previous_block_inputs

    assert out.get("ok") is True, out
    assert {c["block_id"] for c in calls} == {"dashboard", "analytics", "database"}
    by_block = {c["block_id"]: c["action"] for c in calls}
    assert by_block == {
        "dashboard": "render",
        "analytics": "track_event",
        "database": "insert",
    }


def test_wrapper_prepares_live_veterinary_care_contract_fields(tmp_path, monkeypatch):
    """Naive execute(block, payload) must not reach the four live refusals."""
    fake_pre = types.ModuleType("app.preconditions")
    fake_pre.resource_id = lambda block_id: "team_4f473e37589a69bb"
    monkeypatch.setitem(sys.modules, "app.preconditions", fake_pre)

    body = _templated_body(
        ["event_bus", "document_engine", "database", "team"]
    )
    module_text = _handler_module(
        "veterinary_care",
        ["event_bus", "document_engine", "database", "team"],
        body,
        "deterministic contract template",
        {
            "event_bus": "publish",
            "document_engine": "parse",
            "database": "query",
            "team": "get_team_context",
        },
    )
    block_inputs_mod = types.ModuleType("app.block_inputs")
    exec(render_block_inputs_module(), block_inputs_mod.__dict__)
    handler_path = tmp_path / "handler.py"
    handler_path.write_text(module_text, encoding="utf-8")

    calls = []
    fake_dispatch = types.ModuleType("app.dispatch")

    def _fake_execute(block_id, payload, action=None, params=None):
        answer = _refuse_like_live(block_id, payload)
        calls.append({"block_id": block_id, "payload": dict(payload), "answer": answer})
        return answer

    fake_dispatch.execute = _fake_execute
    previous_dispatch = sys.modules.get("app.dispatch")
    previous_block_inputs = sys.modules.get("app.block_inputs")
    sys.modules["app.dispatch"] = fake_dispatch
    sys.modules["app.block_inputs"] = block_inputs_mod
    try:
        spec = importlib.util.spec_from_file_location("vet_handler", handler_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.handle({"reference": "V1", "status": "open", "quantity": 1})
    finally:
        if previous_dispatch is None:
            sys.modules.pop("app.dispatch", None)
        else:
            sys.modules["app.dispatch"] = previous_dispatch
        if previous_block_inputs is None:
            sys.modules.pop("app.block_inputs", None)
        else:
            sys.modules["app.block_inputs"] = previous_block_inputs

    assert out.get("ok") is True, (out, calls)
    by_block = {c["block_id"]: c for c in calls}
    assert set(by_block) == {"event_bus", "document_engine", "database", "team"}
    for bid, call in by_block.items():
        assert call["answer"]["status"] == "ok", (bid, call)
        assert _LIVE_TOPIC not in str(call["answer"])
        assert _LIVE_DOC not in str(call["answer"])
        assert _LIVE_SQL not in str(call["answer"])
        assert _LIVE_TEAM not in str(call["answer"])


def test_dispatch_runtime_still_has_no_f18_fabrication_helpers():
    """Construction lives in block_inputs, never in dispatch (LotDesk F18)."""
    assert "_default_block_field" not in _DISPATCH_RUNTIME
    assert "_ALWAYS_FILL" not in _DISPATCH_RUNTIME
    assert "_ensure_offline_block_input" not in _DISPATCH_RUNTIME
    assert "prepare_block_input" not in _DISPATCH_RUNTIME


def test_sample_payload_covers_every_required_field_type():
    spec = {
        "fields": [
            {"name": "property_reference_code", "type": "str", "required": True},
            {"name": "channel_note", "type": "str", "required": False},
            {"name": "beds", "type": "int", "required": True, "min": 1, "max": 8},
            {"name": "furnished", "type": "bool", "required": True},
        ]
    }
    payload = _sample_payload(spec)
    assert set(payload) == {
        "property_reference_code",
        "channel_note",
        "beds",
        "furnished",
    }
    assert isinstance(payload["property_reference_code"], str)
    assert payload["beds"] == 1
    assert isinstance(payload["furnished"], bool)


def test_sample_value_uses_truthy_int_when_min_is_zero():
    """``min=0`` is valid; 0 is falsy and fails ``if not payload.get(...)``."""
    assert _sample_value({"name": "login_count", "type": "int", "min": 0}) == 1
    # A name decides nothing: an undeclared str field samples neutrally.
    assert _sample_value({"name": "owner_id", "type": "str"}) == "sample"


def test_tester_late_aligns_vetconnect_handlers_into_accept_payload(tmp_path):
    """Pilot rework must refresh samples from on-disk handler contracts."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext, run_tester
    from app.factory.build.workspace import RoleWorkspace

    class _Cap:
        def __init__(self, cid):
            self.capability_id = cid
            self.block_ids = ()

    class _Plan:
        capabilities = (_Cap("secure_multi_user_dashboard"),)

    class _Blueprint:
        product_name = "VetConnect"
        product_id = "veterinary-services"
        vertical = "veterinary_services"

    root = tmp_path / "ws"
    handler = root / "app" / "actions" / "secure_multi_user_dashboard.py"
    handler.parent.mkdir(parents=True, exist_ok=True)
    handler.write_text(
        "def handle(payload):\n"
        "    if not payload.get('clinic_id'):\n"
        "        return {'ok': False, 'error': 'clinic_id is missing and "
        "must be a non-empty string'}\n"
        "    if payload.get('role') not in ('veterinarian', 'technician', "
        "'receptionist', 'practice_manager', 'pet_owner'):\n"
        "        return {'ok': False, 'error': 'role must be one of "
        "{veterinarian, technician, receptionist, practice_manager, "
        "pet_owner}'}\n"
        "    if payload.get('access_level') not in ('admin', 'standard', "
        "'read_only'):\n"
        "        return {'ok': False, 'error': 'access_level must be one of "
        "{admin, standard, read_only}'}\n"
        "    if not isinstance(payload.get('is_active'), bool):\n"
        "        return {'ok': False, 'error': 'is_active must be a boolean'}\n"
        "    if not isinstance(payload.get('login_count'), int) "
        "or payload['login_count'] < 0:\n"
        "        return {'ok': False, 'error': 'login_count must be an "
        "integer >= 0'}\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    ws = RoleWorkspace(BuildRole.TESTER, root)
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(),
        work_list=(
            "secure_multi_user_dashboard rejected a payload built from "
            "its own schema: clinic_id is missing",
        ),
        state={
            "build_cycle": "pilot",
            "vendored_blocks": (),
            "model_specs": {
                "secure_multi_user_dashboard": {
                    "entity": "dashboard",
                    # The schema declares the fields (owner ruling
                    # 2026-10-05); late alignment enriches their bare
                    # types / vocabularies from the on-disk handler.
                    "fields": [
                        {"name": "role", "type": "str", "required": True},
                        {"name": "is_active", "type": "str", "required": True},
                        {"name": "access_level", "type": "str", "required": True},
                        {"name": "clinic_id", "type": "str", "required": True},
                        {"name": "login_count", "type": "str", "required": True},
                    ],
                }
            },
        },
    )
    result = run_tester(ctx)
    assert result.ok, result.detail
    routes = ws.read_text(Path("tests") / "test_routes.py")
    assert "test_every_capability_route_accepts_payload" in routes
    assert "'role': 'veterinarian'" in routes
    assert "'access_level': 'admin'" in routes
    assert "'is_active': True" in routes
    assert "'clinic_id': 'sample'" in routes  # non-empty; the name implies no shape
    assert "login_count" in routes
    assert "'role': 'sample'" not in routes


def test_field_ops_role_runner_emits_block_inputs_and_pilot_accepts(
    tmp_path, monkeypatch, stub_coder
):
    """End-to-end: the five field-ops capability shapes.

    Residential-lettings failed those shapes at pilot. A field_ops build
    (estate stubs; Store pins are get_block shims) must emit
    ``app/block_inputs.py`` and pass
    ``test_every_capability_route_accepts_payload``. 0.5: the build runs a
    stubbed coding agent (a keyless deterministic walk is writer_no_output).
    """
    from app.factory.blueprint import load_blueprint
    from app.factory.build.runner import RoleRunner

    root = Path(__file__).resolve().parents[3]
    out = tmp_path / "field_ops_build"
    outcome = RoleRunner(load_blueprint(root / "blueprints/examples/field_ops.yaml"), out).run()
    assert outcome.ok, outcome.to_dict()
    assert (out / "app" / "block_inputs.py").is_file()
    handler = (out / "app" / "actions" / "stakeholder_notification.py").read_text(
        encoding="utf-8"
    )
    assert "prepare_block_input" in handler

    import os
    import subprocess
    import sys

    env = os.environ.copy()
    env["STORAGE_PATH"] = str(out / "data")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_routes.py::test_every_capability_route_accepts_payload",
            "-q",
            "--tb=line",
        ],
        cwd=out,
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "passed" in proc.stdout


# --- a required-field roster, whatever the constant is called -----------------
#
# Live build: job_and_site_tracking and commercials_and_valuations both answered
# HTTP 422 "missing required field: reference". TESTER mines the handler to
# learn what the route demands, but it only recognised a roster literally named
# required/required_fields/needed, and only a message that named its field as a
# literal. A loop over a differently-named constant, raising an f-string, was
# invisible -- so model_specs never learned `reference`, _sample_payload never
# sent it, and every rework round handed the writer the identical 422 to "fix".


@pytest.mark.parametrize(
    "body",
    [
        'REQUIRED_KEYS = ["reference", "site_id"]\n'
        "def handle(payload):\n"
        "    for field in REQUIRED_KEYS:\n"
        "        if field not in payload:\n"
        '            raise HTTPException(422, f"missing required field: {field}")\n',
        'MANDATORY = ("reference", "valuation_date")\n'
        "def handle(payload):\n"
        "    missing = set(MANDATORY) - set(payload)\n"
        "    if missing:\n"
        '        raise HTTPException(422, "missing required field: " + sorted(missing)[0])\n',
        '_NEEDED_COLUMNS = ["reference"]\n'
        "def handle(payload):\n"
        "    gaps = [k for k in _NEEDED_COLUMNS if k not in payload]\n"
        "    if gaps:\n        raise ValueError(gaps)\n",
        'ALLOWED_STATUSES = ["open", "closed"]\n'
        "def handle(payload):\n"
        '    if payload.get("status") not in ALLOWED_STATUSES:\n'
        '        raise HTTPException(422, "bad status")\n',
        'HEADINGS = ["Ref", "Date"]\n'
        "def render():\n    for h in HEADINGS:\n        print(h)\n",
        'raise ValueError(f"Missing required field: {field}")',
        'raise ValueError("Missing required fields: " + ", ".join(sorted(missing)))',
        'raise ValueError("Missing required fields: zorblat_name, quux_name")',
        "def handle(payload):\n"
        "    for name in ('zorblat_name', 'quux_name'):\n"
        "        if name not in payload:\n"
        "            raise ValueError('Missing required fields: zorblat_name, quux_name')\n",
    ],
)
def test_no_roster_message_or_check_in_code_adds_a_field(body):
    """Live build: two capabilities answered 422 "missing required field:
    reference" that model_specs never declared. The fix is the schema
    declaring the field -- never a roster, a set difference, a comprehension
    or a message in the handler (owner ruling 2026-10-05). Vocabulary
    values and interpolations never become fields either."""
    aligned, changed = align_spec_to_handler_source({"fields": []}, body)
    assert aligned["fields"] == [] and changed == []


def test_a_format_placeholder_in_a_refusal_is_never_a_field():
    """Live 2026-10-04: a handler refused with ``"%s must be an integer >= %d"
    % (key, lo)``; the contract miner read ``s`` as a required int field, and
    the TESTER's own model round-trip test died on ``KeyError: 's'``. A word
    captured from a message is a field only when the code reads it as a key."""
    from app.factory.build.block_inputs import handler_field_contracts

    src = '''
def as_int(data, key, lo=0):
    value = data.get(key)
    if not isinstance(value, int):
        raise ValueError("%s must be an integer >= %d" % (key, lo))
    return value

def handle(payload):
    as_int(payload, "zorblat_count", lo=0)
    if not isinstance(payload.get("quux_flag"), bool):
        return {"ok": False, "error": "%s must be a boolean" % "quux_flag"}
    if payload.get("grade") not in ("low", "high"):
        return {"ok": False, "error": "grade must be one of: low, high"}
    return {"ok": True}
'''
    contracts = handler_field_contracts(src)
    assert "s" not in contracts
    assert contracts["quux_flag"]["type"] == "bool"
    assert contracts["grade"].get("allowed_values") == ["low", "high"]
