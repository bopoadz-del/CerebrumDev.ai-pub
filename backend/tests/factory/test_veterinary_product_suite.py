"""PRODUCT-suite regressions for veterinary-care / VetCare-class pilots.

Live sess_66a387b5c9b0495c (VetCare Hub / domain veterinary-care) went
TERMINAL on Factory Floor after #305/#306/#307:

    CODING AGENT STOPPED
    rework budget of 3 exhausted; TESTER gate still failing:
    PRODUCT (pilot-marked suite)

WRITER/compile had cleared. The PRODUCT suite then executed prepared
payloads against Store blocks / Alembic / domain_acceptance and reported:

1. Missing required field ``reference`` on pet_records_management,
   appointment_scheduling, automated_reminders,
   veterinarian_availability_tracking
2. no such table records
3. Missing PDF parser package
4. Workflow result error
5. Queue str/int type error
6. event_bus notification errors

sess_5dfb4a317c8f4516 (tip e86f197, after #308/#309/#310) then failed
PRODUCT on a fresh VetCare Hub. Live build-status findings (not hunches):

7. ``appointment_scheduling`` accept-payload:
   ``status must be one of: open, in_progress, closed``
8. ``test_every_capability_executes_end_to_end``:
   ``TypeError: Object of type bytes is not JSON serializable``
9. Floor banner only showed ``suite is red`` (findings were on the
   verdict). Caps: client_pet_records, appointment_scheduling,
   staff_dashboard, visit_notes, automated_reminders.

These tests lock the factory construction that would have caught that
PRODUCT red without a live run. They do not weaken honesty: unrepaired
domain JSON still fails the live Store refusals, and export stays refused
when the pilot suite fails.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.block_inputs import (
    STORE_NOTIFICATION_CHANNELS,
    align_spec_to_handler_source,
    extract_capability_route_source,
    prepare_block_input,
)
from app.factory.build.block_obligations import (
    ENVELOPE_STATUS_VALUES,
    augment_model_spec,
    ensure_record_envelope,
)
from app.factory.build.data_lifecycle import first_entity_sample
from app.factory.build.domain_acceptance import (
    first_capability_id,
    render_domain_ops,
)
from app.factory.build.gates import classify_suite_red, suite_assertion_classes
from app.factory.build.offline_adapters import (
    DOC_PARSE_UNWIRED_MARKER,
    DOC_PARSERS_PACKAGE_MARKER,
    DOCUMENT_ENGINE_PARSERS_STUB,
    QUERY_CREATE_MARKER,
    SKLEARN_UNWIRED_MARKER,
    emit_database_query,
    emit_document_engine_parse,
    emit_runtime_module,
    emit_vector_search_sklearn,
    needs_document_engine_parsers_package,
)
from app.factory.build.roles import _constraint_guard, _sample_payload, _sample_value

#: The four capabilities named on the live Floor / suite.
_VETCARE_CAPS = (
    (
        "pet_records_management",
        "pet_record",
        ["document_engine", "database", "workflow"],
        {
            "pet_name": "Nala",
            "owner_name": "Lee",
            "notes": "annual exam",
        },
    ),
    (
        "appointment_scheduling",
        "appointment",
        ["database", "event_bus", "queue"],
        {
            "pet_name": "Rex",
            "appointment_date": "2026-09-10",
            "veterinarian_name": "Dr Lee",
        },
    ),
    (
        "automated_reminders",
        "reminder",
        ["event_bus", "notification", "queue"],
        {
            "pet_name": "Nala",
            "reminder_type": "vaccination",
            "channel": "email",
        },
    ),
    (
        "veterinarian_availability_tracking",
        "availability",
        ["database", "team", "workflow"],
        {
            "veterinarian_name": "Dr Lee",
            "service_date": "2026-09-10",
            "status": "open",
        },
    ),
    # sess_a69c8ce Platforms card (same PRODUCT red, extra caps).
    (
        "pet_record_management",
        "pet_record",
        ["document_engine", "database"],
        {
            "pet_name": "Nala",
            "owner_name": "Lee",
        },
    ),
    (
        "universal_search",
        "search_hit",
        ["vector_search", "database"],
        {
            "query": "vaccination",
            "pet_name": "Nala",
        },
    ),
    # sess_5dfb4a317c8f4516 (2026-09-04 tip e86f197): WRITER last seen
    # calling the coder for this capability, then PRODUCT stayed red.
    (
        "client_pet_records",
        "pet_record",
        ["document_engine", "database", "workflow"],
        {
            "pet_name": "Nala",
            "owner_name": "Lee",
            "notes": "annual exam",
        },
    ),
    (
        "clinic_dashboard",
        "dashboard",
        ["dashboard", "analytics"],
        {
            "clinic_name": "VetCare Hub",
            "metric": "visits",
        },
    ),
)

#: Alphabetically-first cap is client_pet_records; alphabetically-first
#: entity is availability. That split is the sess_5dfb4a3 S12 miss.
_SESS_5DFB4A3_CAPS = (
    (
        "client_pet_records",
        "pet_record",
        ["document_engine", "database"],
        {"pet_name": "Nala", "owner_name": "Lee"},
    ),
    (
        "veterinarian_availability_tracking",
        "availability",
        ["database", "team"],
        {"veterinarian_name": "Dr Lee", "service_date": "2026-09-10"},
    ),
    (
        "vaccination_reminders",
        "reminder",
        ["event_bus", "notification"],
        {"pet_name": "Nala", "reminder_type": "vaccination"},
    ),
)


def _llm_spec(entity: str, domain: dict) -> dict:
    """Shape generate_model_spec emitted for veterinary-care: no ``reference``."""
    return {
        "entity": entity,
        "fields": [
            {"name": name, "type": "str", "required": True}
            for name in domain
        ],
    }


def _run_guard(spec: dict, payload: dict) -> dict:
    ns: dict = {}
    exec("def handle(payload):\n" + _constraint_guard(spec) + "\n    return {'ok': True}\n", ns)
    return ns["handle"](payload)


def test_llm_veterinary_specs_fail_product_guard_until_envelope():
    """Live miss: schema-built samples omitted ``reference``; PRODUCT refused."""
    for cid, entity, _blocks, domain in _VETCARE_CAPS:
        raw = _llm_spec(entity, domain)
        # Obligation augment must stay identity when no schema obligation
        # applies — envelope is a separate seam.
        assert augment_model_spec(raw, ["workflow"]) is raw
        sample = _sample_payload(raw)
        assert "reference" not in sample
        enveloped, added = ensure_record_envelope(raw)
        assert "reference" in added, cid
        if "status" not in domain:
            assert "status" in added, cid
        refused = _run_guard(enveloped, sample)
        assert refused.get("ok") is False, (cid, sample, refused)
        assert "reference" in refused.get("error", ""), (cid, refused)
        accepted = _run_guard(enveloped, _sample_payload(enveloped))
        assert accepted.get("ok") is True, (cid, accepted)
        assert _sample_payload(enveloped)["reference"]


def test_veterinary_product_prepare_repairs_live_product_symptoms():
    """Each live PRODUCT symptom is constructible from a domain record."""
    for cid, entity, roster, domain in _VETCARE_CAPS:
        enveloped, _ = ensure_record_envelope(_llm_spec(entity, domain))
        payload = _sample_payload(enveloped)
        assert payload["reference"]
        assert payload["status"] == "open"

        db = prepare_block_input("database", payload, entity=entity, roster=roster)
        assert db["table"] == entity
        assert db["table"] != "records"
        assert entity in (db.get("sql") or db["table"])

        bus = prepare_block_input(
            "event_bus", payload, product_name="VetCare Hub", roster=roster
        )
        assert isinstance(bus["topic"], str) and bus["topic"]
        # Schema-sample ``channel=sample`` is not a Store channel (live
        # sess_67fe60f7 Unknown channel: sample). Missing / placeholder /
        # delivery channels without extra fields become mcp.
        assert bus["channel"] in STORE_NOTIFICATION_CHANNELS
        assert bus["channel"] != "sample"
        if "channel" not in domain:
            assert bus["channel"] == "mcp"
        assert isinstance(bus.get("payload"), dict)
        assert isinstance(bus.get("message"), str) and bus["message"]

        queued = prepare_block_input(
            "queue", {**payload, "id": "3", "priority": "1", "label": "id-1"}
        )
        assert queued["id"] == 3
        assert queued["item_id"] == 3
        assert queued["priority"] == 1
        assert queued["label"] == "id-1"

        doc = prepare_block_input("document_engine", payload)
        assert Path(doc["pdf_path"]).is_file()
        assert Path(doc["file_path"]).is_file()

        flow = prepare_block_input(
            "workflow",
            payload,
            roster=roster,
            entity=entity,
            product_name="VetCare Hub",
        )
        assert isinstance(flow["steps"], list) and flow["steps"]
        by_block = {step["block"]: step["input"] for step in flow["steps"]}
        if "database" in by_block:
            assert by_block["database"]["table"] == entity
        if "event_bus" in by_block:
            assert by_block["event_bus"]["channel"] in STORE_NOTIFICATION_CHANNELS
            assert by_block["event_bus"]["channel"] != "sample"
            assert by_block["event_bus"]["message"]
        if "notification" in by_block:
            assert by_block["notification"]["channel"] in STORE_NOTIFICATION_CHANNELS
            assert by_block["notification"]["channel"] != "sample"
            assert by_block["notification"]["message"]
            if "channel" not in domain:
                assert by_block["notification"]["channel"] == "mcp"
        if "queue" in by_block:
            assert isinstance(by_block["queue"].get("priority"), int)


def test_query_adapter_creates_missing_table_instead_of_records_error():
    """Live: SELECT * FROM records → no such table records."""
    query = (
        "        \"\"\"Execute SELECT query\"\"\"\n"
        "        sql = data.get(\"sql\")\n"
        "        params = data.get(\"params\", ())\n"
        "        \n"
        "        try:\n"
        "            cursor = self._connection.cursor()\n"
        "            cursor.execute(sql, params)\n"
    )
    out = emit_database_query(query)
    assert QUERY_CREATE_MARKER in out
    assert "no such table" in out
    assert "CREATE TABLE IF NOT EXISTS" in out
    # Default last-resort table name must not be the only path PRODUCT can take.
    assert "pet_record" not in out or "table" in out


def test_document_engine_parse_adapter_stubs_pdf_libs():
    # The stub covers the modules the code reads PdfReader from, decided on
    # the syntax tree; a module that reads PdfReader from nowhere is untouched
    # whatever it is called.
    plain = "def parse(self, data):\n    return data\n"
    assert emit_document_engine_parse(plain) == plain
    assert emit_runtime_module("document_engine", plain) == plain
    reader = "from PyPDF2 import PdfReader\n\ndef parse(self, data):\n    return PdfReader(data)\n"
    src = emit_document_engine_parse(reader)
    assert DOC_PARSE_UNWIRED_MARKER in src
    assert "for _pdf_name in ('PyPDF2',):" in src
    assert emit_runtime_module("zorblat_docs", reader) == src


def test_unrepaired_veterinary_domain_json_still_refused():
    """Honesty: construction is required. Domain JSON is not a Store record."""
    domain = {"pet_name": "Nala", "appointment_date": "2026-09-10"}
    # event_bus without topic
    bus = domain
    assert not (isinstance(bus.get("topic"), str) and bus.get("topic", "").strip())
    # database without sql/table
    assert not domain.get("sql") and not domain.get("table")
    # document_engine without a real file
    assert not any(
        isinstance(domain.get(k), str) and Path(domain[k]).is_file()
        for k in ("file_path", "pdf_path", "docx_path", "xlsx_path")
    )
    # After prepare, the same record is constructible — that is the fix,
    # not a fake ok over a failed block.
    prepared_bus = prepare_block_input("event_bus", domain)
    prepared_db = prepare_block_input("database", domain, entity="appointment")
    prepared_doc = prepare_block_input("document_engine", domain)
    assert prepared_bus["topic"]
    assert prepared_db["table"] == "appointment"
    assert Path(prepared_doc["pdf_path"]).is_file()


def test_sess_a69c8ce_leftover_records_and_sample_priority():
    """Platforms card: a leftover handler table + priority='sample' on
    accept-payload. The table is the DECLARED entity's (Alembic creates
    exactly that one), whatever name the handler carried; SQL a handler
    passes is its own and is never rewritten from its text."""
    leftover = prepare_block_input(
        "database",
        {"table": "records", "pet_name": "Nala"},
        entity="pet_record",
    )
    assert leftover["table"] == "pet_record"
    assert leftover["values"] == {"pet_name": "Nala"}
    own_sql = "SELECT * FROM records"
    passed = prepare_block_input(
        "database", {"table": "records", "sql": own_sql}, entity="pet_record"
    )
    assert passed["sql"] == own_sql
    queued = prepare_block_input("queue", {"priority": "sample", "id": "id-1"})
    assert queued["priority"] == 0
    assert "id" not in queued


def test_sess_a69c8ce_parsers_package_and_sklearn_stubs():
    assert needs_document_engine_parsers_package(
        "from vendor.cerebrum.blocks.document_engine.parsers import Parser\n"
    )
    assert DOC_PARSERS_PACKAGE_MARKER in DOCUMENT_ENGINE_PARSERS_STUB
    src = "from sklearn.feature_extraction.text import TfidfVectorizer\n"
    assert SKLEARN_UNWIRED_MARKER in emit_vector_search_sklearn(src)
    assert SKLEARN_UNWIRED_MARKER in emit_runtime_module("vector_search", src)


def _sess_5dfb4a3_specs() -> dict:
    specs = {}
    for cid, entity, _blocks, domain in _SESS_5DFB4A3_CAPS:
        specs[cid], _ = ensure_record_envelope(_llm_spec(entity, domain))
    return specs


def _assign_from_source(src: str, name: str):
    import ast

    tree = ast.parse(src)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if getattr(target, "id", None) == name:
                return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not assigned in rendered source")


def test_sess_5dfb4a3_s12_sample_follows_default_capability_not_first_entity():
    """Live PRODUCT class: S12 SAMPLE was the first *table*, not DEFAULT.

    client_pet_records sorts first as a capability id; availability sorts
    first as an entity. first_entity_sample still returns the availability
    row (S10 lifecycle inserts a table). domain_ops SAMPLE must be the
    pet_record row perform_all actually writes, or create_persists stays
    red through the rework budget.
    """
    specs = _sess_5dfb4a3_specs()
    assert first_capability_id(specs) == "client_pet_records"
    first_entity, first_sample = first_entity_sample(specs)
    assert first_entity == "availability"
    assert "veterinarian_name" in first_sample
    assert "pet_name" not in first_sample

    src = render_domain_ops(specs)
    assert _assign_from_source(src, "DEFAULT_CAPABILITY") == "client_pet_records"
    assert _assign_from_source(src, "DEFAULT_ENTITY") == "pet_record"
    sample = _assign_from_source(src, "SAMPLE")
    assert sample["pet_name"] == "s10-row"
    assert sample["owner_name"] == "s10-row"
    assert "reference" in sample
    assert "veterinarian_name" not in sample
    # Old SAMPLE vs the row create actually persists: create_persists red.
    persisted = {"id": 1, **sample}
    assert all(persisted.get(k) == sample[k] for k in sample)
    assert not all(persisted.get(k) == first_sample[k] for k in first_sample)


def test_sess_5dfb4a3_product_detail_names_create_persists_not_just_suite_is_red():
    """Floor banner is verdict.detail only — 'suite is red' hid the class."""
    findings = [
        "FAILED tests/test_domain_acceptance.py::"
        "test_ten_business_outcomes_are_performed_through_the_kernel",
        "E   AssertionError: {'ok': False, 'failed': ['create_persists'], "
        "'capability_id': 'client_pet_records'}",
    ]
    detail = classify_suite_red(findings)
    assert detail.startswith("suite is red: domain acceptance: create_persists")
    assert "create_persists" in detail
    assert "client_pet_records" in detail
    assert suite_assertion_classes(findings) == [
        "domain acceptance: create_persists"
    ]


def test_sess_5dfb4a3_accept_payload_records_list_shape_instead_of_keyerror(tmp_path):
    """Hard-coded listed.json()['items'] aborted the whole pilot test."""
    from pathlib import Path

    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext, run_tester
    from app.factory.build.workspace import RoleWorkspace

    class _Cap:
        def __init__(self, cid):
            self.capability_id = cid
            self.block_ids = ()

    class _Plan:
        capabilities = tuple(_Cap(cid) for cid, *_ in _SESS_5DFB4A3_CAPS)

    class _Blueprint:
        product_name = "VetCare Hub"
        product_id = "veterinary-care"
        vertical = "veterinary-care"

    root = tmp_path / "ws"
    (root / "app" / "actions").mkdir(parents=True)
    ws = RoleWorkspace(BuildRole.TESTER, root)
    specs = _sess_5dfb4a3_specs()
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(),
        work_list=("PRODUCT (pilot-marked suite): suite is red",),
        state={
            "build_cycle": "pilot",
            "vendored_blocks": (),
            "model_specs": specs,
        },
    )
    result = run_tester(ctx)
    assert result.ok, result.detail
    routes = ws.read_text(Path("tests") / "test_routes.py")
    assert "def _listed(payload):" in routes
    assert 'listed.json()["items"]' not in routes
    assert "list refused:" in routes
    assert "client_pet_records" in routes

    start = routes.index("def _listed(payload):")
    end = routes.index("\ndef test_health():")
    ns: dict = {}
    exec(routes[start:end], ns)  # noqa: S102 — lock the emitted helper
    listed = ns["_listed"]
    assert listed({"ok": False, "error": "unknown query field(s): stale"}) == []
    assert listed({"records": [{"id": 1}]}) == [{"id": 1}]
    assert listed({"items": [{"id": 2}]}) == [{"id": 2}]

    keyerror_findings = [
        "FAILED tests/test_routes.py::test_every_capability_route_accepts_payload",
        "E   KeyError: 'items'",
    ]
    detail = classify_suite_red(keyerror_findings)
    assert "accept-payload list shape (KeyError: 'items')" in detail
    assert suite_assertion_classes(keyerror_findings) == [
        "accept-payload list shape (KeyError: 'items')"
    ]


_FACTORY_STATUS = ("open", "in_progress", "closed")
_LIVE_STATUS_ERROR = "status must be one of: open, in_progress, closed"
_LIVE_BYTES_ERROR = "Object of type bytes is not JSON serializable"


def test_sess_5dfb4a3_appointment_status_vocab_follows_the_handler_guard():
    """Live accept-payload: LLM status vocab lost to the factory envelope.

    Caps on the run: appointment_scheduling (this class), plus
    client_pet_records, staff_dashboard, visit_notes, automated_reminders.

    #311 aligned the handler after the fact. sess_1fd1d54c on tip a626167
    showed that is too late: the envelope itself must rewrite the LLM list
    so ``_constraint_guard`` and ``_sample_payload`` agree before TESTER
    bakes the accept-payload.
    """
    llm = {
        "entity": "appointment",
        "fields": [
            {"name": "pet_name", "type": "str", "required": True},
            {"name": "appointment_date", "type": "str", "required": True},
            {
                "name": "status",
                "type": "str",
                "required": True,
                "allowed_values": ["scheduled", "completed", "cancelled"],
            },
        ],
    }
    stale = _sample_payload(llm)
    assert stale["status"] == "scheduled"
    refused = _run_guard(
        {
            **llm,
            "fields": [
                {**f, "allowed_values": list(_FACTORY_STATUS)}
                if f.get("name") == "status"
                else f
                for f in llm["fields"]
            ],
        },
        stale,
    )
    assert refused.get("ok") is False
    assert _LIVE_STATUS_ERROR in refused.get("error", "")

    llm, _ = ensure_record_envelope(llm)
    sample = _sample_payload(llm)
    assert sample["status"] == "open"
    accepted = _run_guard(llm, sample)
    assert accepted.get("ok") is True, (sample, accepted)

    handler = (
        "def handle(payload):\n"
        "    if payload.get('status') not in ('open', 'in_progress', 'closed'):\n"
        "        return {'ok': False, 'error': "
        f"'{_LIVE_STATUS_ERROR}'}}\n"
        "    return {'ok': True}\n"
    )
    aligned, _changed = align_spec_to_handler_source(llm, handler)
    sample = _sample_payload(aligned)
    assert sample["status"] == "open"
    accepted = _run_guard(aligned, sample)
    assert accepted.get("ok") is True, (sample, accepted)


def test_sess_5dfb4a3_bare_status_samples_open_not_the_word_sample():
    """The record envelope DECLARES the status vocabulary; the sample follows
    the declaration, never the field's name."""
    field = {"name": "status", "type": "str", "required": True}
    spec, _ = ensure_record_envelope({"fields": [field]})
    assert _sample_payload(spec)["status"] == "open"
    # Undeclared, a name samples neutrally -- whatever it is called.
    assert _sample_value({"name": "job_status", "type": "str"}) == "sample"


def test_sess_5dfb4a3_e2e_smoke_dumps_bytes_without_typeerror(tmp_path):
    """Live smoke: document_engine / visit_notes results include PDF bytes."""
    import json
    from pathlib import Path

    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext, run_tester
    from app.factory.build.workspace import RoleWorkspace

    class _Cap:
        def __init__(self, cid):
            self.capability_id = cid
            self.block_ids = ()

    class _Plan:
        capabilities = (_Cap("visit_notes"), _Cap("client_pet_records"))

    class _Blueprint:
        product_name = "VetCare Hub"
        product_id = "veterinary-care"
        vertical = "veterinary-care"

    root = tmp_path / "ws"
    (root / "app" / "actions").mkdir(parents=True)
    ws = RoleWorkspace(BuildRole.TESTER, root)
    specs = {
        "visit_notes": ensure_record_envelope(
            _llm_spec("visit_note", {"notes": "annual exam"})
        )[0],
        "client_pet_records": ensure_record_envelope(
            _llm_spec("pet_record", {"pet_name": "Nala"})
        )[0],
    }
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(),
        work_list=("PRODUCT (pilot-marked suite): suite is red",),
        state={
            "build_cycle": "pilot",
            "vendored_blocks": (),
            "model_specs": specs,
        },
    )
    assert run_tester(ctx).ok
    smoke = ws.read_text(Path("tests") / "test_smoke.py")
    assert "test_every_capability_executes_end_to_end" in smoke
    assert "_json.dumps(out, default=str)" in smoke
    assert "_json.dumps(out)" not in smoke.replace("_json.dumps(out, default=str)", "")

    out = {
        "ok": True,
        "capability": "visit_notes",
        "results": {"document_engine": {"content": b"%PDF-1.1\n"}},
    }
    dumped = json.dumps(out, default=str)
    assert '"ok": true' in dumped.lower() or '"ok": True' in dumped or "ok" in dumped


def test_sess_5dfb4a3_product_detail_names_live_status_and_bytes_classes():
    status_findings = [
        "FAILED tests/test_routes.py::test_every_capability_route_accepts_payload",
        "E   AssertionError: appointment_scheduling rejected a payload "
        f"built from its own schema: {_LIVE_STATUS_ERROR}",
    ]
    status_detail = classify_suite_red(status_findings)
    assert "schema sample refused" in status_detail
    assert "status vocabulary" in status_detail
    assert "appointment_scheduling" in status_detail
    assert _LIVE_STATUS_ERROR in status_detail

    bytes_findings = [
        "ERROR tests/test_smoke.py::test_every_capability_executes_end_to_end",
        f"E   TypeError: {_LIVE_BYTES_ERROR}",
    ]
    bytes_detail = classify_suite_red(bytes_findings)
    assert "not JSON serializable" in bytes_detail
    assert _LIVE_BYTES_ERROR in bytes_detail


def _llm_appointment_status_spec():
    return {
        "entity": "appointment",
        "fields": [
            {"name": "pet_name", "type": "str", "required": True},
            {
                "name": "status",
                "type": "str",
                "required": True,
                "allowed_values": ["scheduled", "completed", "cancelled"],
            },
        ],
    }


def test_sess_1fd1d54c_route_factory_status_wins_over_llm_schema_sample():
    """#311 mined the handler; tip a626167 still failed on the route.

    Live assertion (sess_1fd1d54c, VetCare Hub / veterinary-care):

        appointment_scheduling rejected a payload built from its own schema:
        status must be one of: open, in_progress, closed

    The handler had no mineable ``not in (...)`` check. The refuse was
    ``_constraint_guard`` baked into ``app/routes.py`` with the factory
    envelope. Schema-sample used the LLM list (scheduled). Intersection
    empty through the rework budget.
    """
    llm = _llm_appointment_status_spec()
    handler = "def handle(payload):\n    return {'ok': True}\n"
    factory_spec = {
        **llm,
        "fields": [
            {**f, "allowed_values": list(_FACTORY_STATUS)}
            if f.get("name") == "status"
            else f
            for f in llm["fields"]
        ],
    }
    route = _constraint_guard(factory_spec) + "\n    return {'ok': True}\n"

    aligned_handler_only, changed = align_spec_to_handler_source(llm, handler)
    assert "status" not in changed
    stale = _sample_payload(aligned_handler_only)
    assert stale["status"] == "scheduled"
    refused = _run_guard(factory_spec, stale)
    assert refused.get("ok") is False
    assert _LIVE_STATUS_ERROR in refused.get("error", "")
    assert (
        "appointment_scheduling rejected a payload built from its own schema: "
        + _LIVE_STATUS_ERROR
    )

    aligned, changed = align_spec_to_handler_source(llm, handler + "\n" + route)
    assert "status" in changed
    sample = _sample_payload(aligned)
    assert sample["status"] == "open"
    assert _run_guard(factory_spec, sample).get("ok") is True
    assert list(
        next(f for f in aligned["fields"] if f["name"] == "status")["allowed_values"]
    ) == list(ENVELOPE_STATUS_VALUES)


def test_sess_1fd1d54c_veterinarian_availability_same_status_class():
    """UI truncated a second capability starting with veterinarian_…"""
    llm = {
        "entity": "availability",
        "fields": [
            {"name": "veterinarian_name", "type": "str", "required": True},
            {
                "name": "status",
                "type": "str",
                "required": True,
                "allowed_values": ["scheduled", "booked", "blocked"],
            },
        ],
    }
    stale = _sample_payload(llm)
    assert stale["status"] == "scheduled"
    enveloped, added = ensure_record_envelope(llm)
    assert "reference" in added
    sample = _sample_payload(enveloped)
    assert sample["status"] == "open"
    assert sample["veterinarian_name"]
    accepted = _run_guard(enveloped, sample)
    assert accepted.get("ok") is True, (sample, accepted)
    from app.factory.build.data_lifecycle import sample_for_spec

    # The status vocabulary is DECLARED by the record envelope; the lifecycle
    # sample follows the declaration, never the field's name.
    lifecycle_spec, _added = ensure_record_envelope(
        {"fields": [{"name": "status", "type": "str", "required": True}]}
    )
    lifecycle = sample_for_spec(lifecycle_spec, placeholder="s10-row")
    assert lifecycle["status"] == "open"
    assert lifecycle["status"] != "s10-row"
    bare = sample_for_spec(
        {"fields": [{"name": "status", "type": "str", "required": True}]},
        placeholder="s10-row",
    )
    assert bare["status"] == "s10-row"  # undeclared: the placeholder


def test_sess_1fd1d54c_tester_bakes_open_from_route_constraints(tmp_path):
    """TESTER late-align must read routes.py, not only the handler."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext, run_tester
    from app.factory.build.workspace import RoleWorkspace

    class _Cap:
        def __init__(self, cid):
            self.capability_id = cid
            self.block_ids = ()

    class _Plan:
        capabilities = (_Cap("appointment_scheduling"),)

    class _Blueprint:
        product_name = "VetCare Hub"
        product_id = "veterinary-care"
        vertical = "veterinary-care"

    root = tmp_path / "ws"
    actions = root / "app" / "actions"
    actions.mkdir(parents=True)
    (actions / "appointment_scheduling.py").write_text(
        "def handle(payload):\n    return {'ok': True}\n",
        encoding="utf-8",
    )
    routes = (
        "# --- appointment_scheduling (coder LLM) ---\n"
        "async def appointment_scheduling_create(payload):\n"
        + _constraint_guard(
            {
                "fields": [
                    {
                        "name": "status",
                        "allowed_values": list(_FACTORY_STATUS),
                    }
                ]
            }
        )
        + "\n    return {'ok': True}\n"
        "# --- veterinarian_availability_tracking (coder LLM) ---\n"
        "async def veterinarian_availability_tracking_create(payload):\n"
        "    return {'ok': True}\n"
    )
    (root / "app" / "routes.py").write_text(routes, encoding="utf-8")
    sliced = extract_capability_route_source(routes, "appointment_scheduling")
    assert "appointment_scheduling" in sliced
    assert "veterinarian_availability_tracking" not in sliced

    ws = RoleWorkspace(BuildRole.TESTER, root)
    ctx = RoleContext(
        role=BuildRole.TESTER,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=_Plan(),
        work_list=(
            "FAILED tests/test_routes.py::test_every_capability_route_accepts_payload\n"
            "E   AssertionError: appointment_scheduling rejected a payload "
            f"built from its own schema: {_LIVE_STATUS_ERROR}",
        ),
        state={
            "build_cycle": "pilot",
            "vendored_blocks": (),
            "model_specs": {
                "appointment_scheduling": _llm_appointment_status_spec(),
            },
        },
    )
    assert run_tester(ctx).ok
    baked = ws.read_text(Path("tests") / "test_routes.py")
    assert "'status': 'open'" in baked
    assert "'status': 'scheduled'" not in baked
    assert "test_every_capability_route_accepts_payload" in baked


def test_sess_f1fe691_clinic_dashboard_envelope_adds_status():
    """Live clinic_dashboard: Missing required field: status.

    #312 rewrote an existing status vocabulary. The LLM clinic_dashboard
    spec omitted the column, so the schema sample never sent status.
    """
    raw = _llm_spec("dashboard", {"clinic_name": "VetCare Hub", "metric": "visits"})
    stale = _sample_payload(raw)
    assert "status" not in stale
    enveloped, added = ensure_record_envelope(raw)
    assert "status" in added
    sample = _sample_payload(enveloped)
    assert sample["status"] == "open"
    almost = {k: v for k, v in sample.items() if k != "status"}
    refused = _run_guard(enveloped, almost)
    assert refused.get("ok") is False
    assert refused.get("error") == "Missing required field: status"
    accepted = _run_guard(enveloped, sample)
    assert accepted.get("ok") is True, (sample, accepted)
    dash = prepare_block_input("dashboard", {"clinic_name": "VetCare Hub"})
    assert dash["status"] == "open"


def test_sess_f1fe691_reminders_result_key_is_not_required_on_envelope():
    """Live automated_reminders: RuntimeError: 'result'."""
    from app.factory.build.offline_adapters import emit_result_key_access
    from app.factory.build.roles import _rewrite_shim_constructors

    shim = (
        "envelope = {'status': 'ok'}\n"
        "if envelope.get('status') == 'error':\n"
        "    raise RuntimeError(envelope['result'])\n"
        "return envelope['result']\n"
    )
    out = emit_result_key_access(shim)
    assert 'envelope.get("result", envelope)' in out
    assert "envelope['result']" not in out

    workflow = (
        "output = {'status': 'success'}\n"
        "step = output['result']\n"
    )
    rewritten = emit_result_key_access(workflow)
    assert 'output.get("result", output)' in rewritten

    # sess_07dff0eaf8f64186: Store kit shim used ``out`` / ``step_result``,
    # not the first whitelist (envelope/output/payload).
    live_out = (
        "out = {'status': 'success', 'results': []}\n"
        "try:\n"
        "    return out['result']\n"
        "except KeyError as exc:\n"
        "    raise RuntimeError(exc) from exc\n"
    )
    live_rewritten = emit_result_key_access(live_out)
    assert 'out.get("result", out)' in live_rewritten
    assert "out['result']" not in live_rewritten
    step_result = emit_result_key_access("value = step_result['result']\n")
    assert 'step_result.get("result", step_result)' in step_result

    # sess_c63cc1a274994b33: assignment targets must stay subscripts.
    assigned = emit_result_key_access("result['result'] = payload\n")
    assert "result['result'] =" in assigned or 'result["result"] =' in assigned
    assert "result.get(" not in assigned

    # Constructor rewrite must not invent a cause — only rewrite zero-arg
    # Store construction that produces the live TypeError.
    live = "instance = DatabaseBlock()\n"
    assert "_instantiate_store_block(DatabaseBlock)" in _rewrite_shim_constructors(live)


_LIVE_CHANNEL_ERROR = "Unknown channel: sample"
_SESS_67FE60F_ROSTER = ["notification", "database", "event_bus"]


def test_sess_67fe60f_automated_reminders_schema_sample_is_not_channel_sample():
    """Live accept-payload: automated_reminders sent channel='sample'.

    sess_67fe60f7b40a4ac3 on tip d0d6d42 (#313). PRODUCT refused:

        automated_reminders rejected a payload built from its own schema:
        RuntimeError: Unknown channel: sample

    The LLM spec declared ``channel: str`` with no vocabulary, so
    ``_sample_value`` emitted the generic word. Notification and
    event_bus then forwarded it unchanged.
    """
    llm = {
        "entity": "reminder",
        "fields": [
            {"name": "pet_name", "type": "str", "required": True},
            {"name": "reminder_type", "type": "str", "required": True},
            {"name": "channel", "type": "str", "required": True},
        ],
    }
    stale = {"pet_name": "Nala", "reminder_type": "vaccination", "channel": "sample"}
    assert stale["channel"] == "sample"

    llm, _ = ensure_record_envelope(llm)
    sample = _sample_payload(llm)
    # The spec declared no vocabulary, so the schema sample is neutral; the
    # block boundary (prepare_block_input) owns the channel rewrite -- see
    # test_sess_67fe60f_notification_and_event_bus_rewrite_sample_channel.
    assert sample["channel"] == "sample"
    declared = {"name": "channel", "type": "str", "allowed_values": ["sms", "email"]}
    assert _sample_value(declared) in STORE_NOTIFICATION_CHANNELS


def test_sess_67fe60f_notification_and_event_bus_rewrite_sample_channel():
    """Workflow step_0 (notification) and step_2 (event_bus) on the live run."""
    domain = {
        "reference": "R1",
        "pet_name": "Nala",
        "reminder_type": "vaccination",
        "channel": "sample",
        "status": "open",
    }
    note = prepare_block_input(
        "notification", domain, roster=_SESS_67FE60F_ROSTER
    )
    assert note["channel"] == "mcp"
    assert note["channel"] != "sample"
    assert note["message"]

    bus = prepare_block_input("event_bus", domain, product_name="VetCare Hub")
    assert bus["channel"] == "mcp"
    assert bus["channel"] != "sample"
    assert bus["topic"]
    assert bus["message"]
    assert isinstance(bus["payload"], dict)

    flow = prepare_block_input(
        "workflow",
        domain,
        roster=_SESS_67FE60F_ROSTER,
        entity="reminder",
        product_name="VetCare Hub",
    )
    by_block = {step["block"]: step["input"] for step in flow["steps"]}
    assert by_block["notification"]["channel"] == "mcp"
    assert by_block["event_bus"]["channel"] == "mcp"
    assert "sample" not in (
        by_block["notification"]["channel"],
        by_block["event_bus"]["channel"],
    )


def test_sess_67fe60f_product_detail_names_unknown_channel_class():
    findings = [
        "FAILED tests/test_routes.py::test_every_capability_route_accepts_payload",
        "E   AssertionError: automated_reminders rejected a payload "
        f"built from its own schema: RuntimeError: {_LIVE_CHANNEL_ERROR}",
    ]
    detail = classify_suite_red(findings)
    assert "schema sample refused" in detail
    assert "notification channel" in detail
    assert "automated_reminders" in detail
    assert _LIVE_CHANNEL_ERROR in detail


_LIVE_EVENT_BUS_STEP_ERROR = "workflow: step_1 (event_bus): error"
_SESS_4FBA2A2_ROSTER = ["database", "event_bus", "queue"]


def test_sess_4fba2a2_appointment_schema_sample_through_coder_workflow_steps():
    """Live sess_4fba2a2865044a82: TESTER sample → workflow → event_bus error.

    Floor / Platforms (tip 6b6e1e5, after #314):

        appointment_scheduling rejected a payload built from its own schema:
        workflow: step_1 (event_bus): error

    The coder handler already had a ``steps`` list, so prepare used to
    forward the schema sample into event_bus unchanged.
    """
    llm = {
        "entity": "appointment",
        "fields": [
            {"name": "pet_name", "type": "str", "required": True},
            {"name": "appointment_date", "type": "str", "required": True},
            {"name": "veterinarian_name", "type": "str", "required": True},
        ],
    }
    enveloped, _ = ensure_record_envelope(llm)
    sample = _sample_payload(enveloped)
    assert sample["status"] == "open"
    assert sample["pet_name"]

    stale_steps = [
        {"block": "database", "input": dict(sample)},
        {"block": "event_bus", "input": dict(sample)},
        {"block": "queue", "input": dict(sample)},
    ]
    stale_bus = stale_steps[1]["input"]
    assert not stale_bus.get("topic")
    assert stale_bus.get("channel") in (None, "email", "sample")

    flow = prepare_block_input(
        "workflow",
        {"steps": stale_steps},
        roster=_SESS_4FBA2A2_ROSTER + ["workflow"],
        entity="appointment",
        product_name="VetCare Hub",
        default_actions={"event_bus": "publish"},
    )
    by_block = {step["block"]: step for step in flow["steps"]}
    bus = by_block["event_bus"]["input"]
    assert by_block["event_bus"]["action"] == "publish"
    assert isinstance(bus["topic"], str) and bus["topic"]
    assert bus["channel"] in STORE_NOTIFICATION_CHANNELS
    assert bus["channel"] == "mcp"
    assert isinstance(bus.get("payload"), dict)
    assert bus["payload"]["pet_name"] == sample["pet_name"]
    assert isinstance(bus.get("message"), str) and bus["message"]
    assert by_block["database"]["input"]["table"] == "appointment"


def test_sess_4fba2a2_product_detail_names_event_bus_workflow_step():
    findings = [
        "FAILED tests/test_routes.py::test_every_capability_route_accepts_payload",
        "E   AssertionError: appointment_scheduling rejected a payload "
        f"built from its own schema: {_LIVE_EVENT_BUS_STEP_ERROR}",
        "E   assert not ['appointment_scheduling rejected a payload built "
        f"from its own schema: {_LIVE_EVENT_BUS_STEP_ERROR}']",
    ]
    detail = classify_suite_red(findings)
    assert "schema sample refused" in detail
    assert "event_bus workflow step" in detail
    assert "appointment_scheduling" in detail
    assert _LIVE_EVENT_BUS_STEP_ERROR in detail
