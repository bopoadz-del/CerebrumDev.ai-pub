"""One gated brief replaces per-capability WRITER shots."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authorship import (
    FULL_PILOT_MIN_AUTHORED_ACTIONS,
    full_pilot_authorship_acceptance_line,
    full_pilot_authorship_forbidden_lines,
    full_pilot_authorship_rules_text,
)
from app.factory.build.brief_compiler import (
    TEMPLATE_REVISION,
    InventoryHalt,
    compile_brief,
    compile_inventory,
    load_brief_template,
    verify_inventory,
)
from app.factory.build.brief_lint import lint_brief
from app.factory.product_architect import (
    plan_blueprint,
)

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


class _Blueprint:
    product_name = "VetCare Hub"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "Clinic appointments, reminders, and pet records."


def test_compiled_brief_has_the_gated_shape():
    bp = load_blueprint(SMOKE)
    plan = plan_blueprint(bp)
    compiled = compile_brief(bp, plan)
    text = compiled.text
    for heading in (
        "TARGET",
        "STEP 0 INVENTORY + STOP",
        "DO",
        "ACCEPTANCE",
        "FORBIDDEN",
    ):
        assert heading in text
    assert "analytics_surface" in text
    assert "dashboard_surface" in text
    assert "one handle()" in text.lower() or "not one handle()" in text.lower()
    assert "thin SUCCESS" in text
    n_required = len(plan.capabilities)
    n = min(FULL_PILOT_MIN_AUTHORED_ACTIONS, max(1, n_required))
    assert n == 2
    assert f"≥{n}" in text
    assert "dynamic floor" in text
    assert "full-pilot authorship" in text
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in text
    assert "cli_authored_ids" in text
    assert "app/actions/*.py" in text
    assert full_pilot_authorship_rules_text(n_required) in text
    assert full_pilot_authorship_acceptance_line(n_required) in text
    assert full_pilot_authorship_forbidden_lines(n_required) in text


def test_compiled_brief_forbids_sealed_vendor():
    """BA reads the sealed-vendor HARD RULE in C-BRIEF as well as LAUNCH_PROMPT."""
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("clinic_intake", [], "GENERATE")),
        store_ids={"event_bus"},
    )
    assert "llm_writes_brief: never" in load_brief_template()
    forbidden = compiled.text.split("\nFORBIDDEN\n", 1)[1]
    for needle in (
        "HARD RULE",
        "sealed vendor",
        "vendor/**",
        "vendor_blocks/**",
        "vendor_blocks_mirror/**",
        "blocks.lock.json",
        "build_ledger.jsonl",
        ".git/**",
        "SEALED_AFTER_CLONER",
        "ba_allowed_globs",
        "docs/openapi.json",
        "openapi.json",
        "execute(action=)",
    ):
        assert needle in forbidden
    assert "never patch vendored blocks" in forbidden.lower()
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_claimed_reuse_missing_from_store_halts():
    plan = _Plan(_Cap("appointments", ["not_a_real_block"], "REUSE"))
    compiled = compile_brief(_Blueprint(), plan, store_ids={"analytics", "dashboard"})
    assert compiled.missing_reuse == ["not_a_real_block"]
    assert compiled.inventory[0].missing == ["not_a_real_block"]
    with pytest.raises(InventoryHalt, match="not_a_real_block"):
        verify_inventory(compiled)


def test_verified_reuse_is_not_a_gap():
    items = compile_inventory(
        _Plan(_Cap("appointments", ["event_bus"], "REUSE")),
        {"event_bus", "database"},
    )
    assert items[0].verified_present == ["event_bus"]
    assert items[0].missing == []
    assert items[0].handler_source
    assert items[0].is_reuse


def test_all_reuse_inventory_lists_cbrief_work_items():
    """InsureDistribute-class all-REUSE must still name hole-fill work."""
    compiled = compile_brief(
        _Blueprint(),
        _Plan(
            _Cap("workflow", ["workflow"], "REUSE"),
            _Cap("validation", ["validation"], "REUSE"),
        ),
        store_ids={"workflow", "validation"},
    )
    assert all(not item.is_gap for item in compiled.inventory)
    assert "WORK ITEMS" in compiled.text
    assert "REUSE hole-fill" in compiled.text
    assert "do not skip because inventory_gaps is empty" in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_capability_without_blocks_is_a_named_gap():
    items = compile_inventory(_Plan(_Cap("custom_intake", [], "GENERATE")), {"analytics"})
    assert items[0].is_gap
    assert items[0].missing == []
    assert items[0].handler_source == ""


def test_false_reuse_without_handler_source_halts():
    """Architect REUSE with no registry-verified handler source is a halt."""
    items = compile_inventory(
        _Plan(_Cap("appointment_scheduling", [], "REUSE")),
        {"event_bus", "database"},
    )
    assert items[0].is_gap
    assert items[0].handler_source == ""
    assert items[0].strategy != "REUSE"

    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("clinic_intake", [], "GENERATE")),
        store_ids={"event_bus"},
    )
    verify_inventory(compiled)
    compiled.inventory[0].strategy = "REUSE"
    compiled.inventory[0].handler_source = ""
    compiled.inventory[0].verified_present = []
    compiled.inventory[0].missing = []
    compiled.missing_reuse = []
    with pytest.raises(InventoryHalt, match="no-handler-source"):
        verify_inventory(compiled)


def test_lettings_golden_is_unchanged_and_compiles():
    """The golden roster is the live capability set — compiler must not rewrite it."""
    bp = load_blueprint(LETTINGS_GOLDEN)
    assert {c.id for c in bp.capabilities} == LIVE_LETTINGS_CAPS
    golden = load_blueprint(LETTINGS_GOLDEN)
    assert {c.id for c in golden.capabilities} == LIVE_LETTINGS_CAPS
    plan = plan_blueprint(bp)
    compiled = compile_brief(bp, plan)
    verify_inventory(compiled)
    assert set(compiled.capabilities) == LIVE_LETTINGS_CAPS
    assert compiled.missing_reuse == []
    assert "residential_lettings" in compiled.text
    assert LETTINGS.is_file()
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert compiled.intake["schema_version"] == "intake_blueprint.v1"
    assert compiled.template_revision == TEMPLATE_REVISION
    assert full_pilot_authorship_rules_text(4) in compiled.text
    assert "≥4" in compiled.text
    assert "dynamic floor" in compiled.text
    assert "4 required capabilities" in compiled.text


def test_compiled_brief_names_the_full_pilot_authorship_floor():
    """#387 package refuse is useless if C-BRIEF never tells the coder the floor."""
    compiled = compile_brief(
        _Blueprint(),
        _Plan(
            _Cap("appointment_scheduling", ["event_bus"], "REUSE"),
            _Cap("clinic_intake", [], "GENERATE"),
        ),
        store_ids={"event_bus"},
    )
    text = compiled.text
    n_required = 2
    n = 2
    assert f"≥{n}" in text
    assert f"<{n}" in text
    assert "dynamic floor" in text
    assert "full-pilot authorship" in text
    assert "app/actions/*.py" in text
    assert "cli_authored_ids" in text
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in text
    assert "[check:full_pilot_authorship]" in text
    assert full_pilot_authorship_rules_text(n_required) in text
    assert full_pilot_authorship_acceptance_line(n_required) in text
    assert full_pilot_authorship_forbidden_lines(n_required) in text
    build = text.split("CUT 3", 1)[1].split("ACCEPTANCE", 1)[0]
    acceptance = text.split("ACCEPTANCE (harness", 1)[1].split("FORBIDDEN", 1)[0]
    forbidden = text.split("\nFORBIDDEN\n", 1)[1]
    assert f"≥{n}" in build
    assert f"≥{n}" in acceptance
    assert f"<{n}" in forbidden
    assert "dynamic floor" in forbidden
    assert "FACTORY_CODE_CLI_THIN_AUTHORSHIP" in forbidden
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_six_cap_brief_keeps_absolute_floor_of_five():
    """Products with ≥5 required caps still name need ≥5, not all six."""
    compiled = compile_brief(
        _Blueprint(),
        _Plan(
            *[_Cap(cid, ["event_bus"], "REUSE") for cid in (
                "audit",
                "workflow",
                "team",
                "document_engine",
                "validation",
                "notification",
            )],
        ),
        store_ids={"event_bus"},
    )
    assert "≥5" in compiled.text
    assert "dynamic floor" not in compiled.text
    assert full_pilot_authorship_rules_text(6) in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_vetcare_fresh_session_compiles_on_the_new_path():
    """A VetCare-shaped plan gets one brief; missing REUSE is the named halt."""
    plan = _Plan(
        _Cap("appointment_scheduling", ["event_bus", "workflow"], "COMPOSE"),
        _Cap("automated_reminders", ["notification"], "REUSE"),
        _Cap("clinic_intake", [], "GENERATE"),
    )
    compiled = compile_brief(
        _Blueprint(),
        plan,
        store_ids={"event_bus", "workflow", "notification", "database"},
    )
    verify_inventory(compiled)
    assert "VetCare Hub" in compiled.text
    assert "appointment_scheduling" in compiled.text
    assert "clinic_intake" in compiled.text
    assert "writer_behaviour" in compiled.text
    assert "no capability accepted its own schema" in compiled.text
    assert "test_every_capability_route_accepts_payload" in compiled.text
    assert "workflow: step_N (event_bus): error" in compiled.text
    assert "workflow: step_2 (event_bus): error" in compiled.text
    assert "action=publish" in compiled.text
    assert "payload dict" in compiled.text
    assert "input.topic" in compiled.text
    assert "'input': payload" in compiled.text
    assert '"channel": "mcp"' in compiled.text
    assert "factory-grounded" in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert any(item.capability_id == "clinic_intake" and item.is_gap for item in compiled.inventory)
    assert compiled.missing_reuse == []

    poisoned = compile_brief(
        _Blueprint(),
        _Plan(_Cap("appointment_scheduling", ["event_bus", "no_such_block"], "COMPOSE")),
        store_ids={"event_bus"},
    )
    with pytest.raises(InventoryHalt, match="no_such_block"):
        verify_inventory(poisoned)


def test_reuse_http_present_false_drops_invented_reuse_claim(monkeypatch):
    """STEP 0 trusts the Blocks exact-id 200 body, not a local shelf hit.

    CI has no CEREBRUM_API_URL (unlike a local .env). The injected getter
    must still run. present:false is an invented REUSE claim — drop it to
    a named GAP. Do not HALT the session; do not keep the REUSE line.
    """
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def fake_get(block_id, base_url=None):
        return ReuseRecord(block_id=block_id, present=False, source="registry/blocks")

    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("appointment_scheduling", ["event_bus"], "REUSE")),
        store_ids={"event_bus"},
        reuse_http_get=fake_get,
    )
    assert compiled.missing_reuse == []
    assert compiled.reuse_records["event_bus"]["present"] is False
    item = compiled.inventory[0]
    assert item.dropped_reuse == ["event_bus"]
    assert item.verified_present == []
    assert item.missing == []
    assert item.is_gap
    verify_inventory(compiled)
    assert "unverified REUSE dropped" in compiled.text
    assert "Store exact-id present=false" in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    reuse_section = compiled.text.split("REUSE (verified present):", 1)[1].split(
        "GAPS (you author", 1
    )[0]
    assert "event_bus" not in reuse_section


def test_planted_reuse_claim_for_absent_id_still_rejects():
    """Mutation: a brief that still claims REUSE for a ghost id is refused."""
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("clinic_intake", [], "GENERATE")),
        store_ids={"event_bus"},
    )
    verify_inventory(compiled)
    compiled.missing_reuse = ["not_a_real_block"]
    compiled.inventory[0].missing = ["not_a_real_block"]
    compiled.inventory[0].strategy = "REUSE"
    with pytest.raises(InventoryHalt, match="not_a_real_block"):
        verify_inventory(compiled)
    planted = lint_brief(compiled)
    assert planted.ok is False
    assert any("unresolved block id" in e for e in planted.errors)


def test_reuse_http_l2_fields_appear_in_brief_when_declared(monkeypatch):
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def fake_get(block_id, base_url=None):
        return ReuseRecord(
            block_id=block_id,
            present=True,
            source="registry/blocks",
            reads=["topic"],
            writes=["event"],
            never=["channel"],
            acceptance=["publish succeeds"],
            scope_declared=True,
        )

    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("appointment_scheduling", ["event_bus"], "REUSE")),
        store_ids={"event_bus"},
        reuse_http_get=fake_get,
    )
    verify_inventory(compiled)
    assert "topic" in compiled.text
    assert "publish succeeds" in compiled.text
    assert not any(
        "event_bus" in line and "pre-flip" in line
        for line in compiled.text.splitlines()
    )
    assert any(
        "event_bus" in line and "READS=" in line and "topic" in line
        for line in compiled.text.splitlines()
    )
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


@pytest.fixture
def preflip_store(tmp_path, monkeypatch):
    """A Store whose estate_registry declares NO L2 scopes.

    The pre-flip honesty path ("not declared on block.json -- do not invent
    scopes") used to be proven on the Factory's estate stub, which had no L2
    keys. That stub is gone and the Store's real block declares its scopes,
    so the premise is rebuilt explicitly instead of borrowed from a fake.
    """
    import json as _json

    block = tmp_path / "block_registry" / "estate_registry"
    block.mkdir(parents=True)
    (block / "block.json").write_text(
        _json.dumps({"id": "estate_registry", "version": "1.0.0"}), encoding="utf-8"
    )
    (block / "block.py").write_text("def run(**kw):\n    return {}\n", encoding="utf-8")
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(tmp_path))
    return tmp_path


def test_preflip_block_json_says_scopes_not_declared(preflip_store):
    """Estate lock-era pins have no L2.2 keys — brief must say so, not invent.

    Store-sourced pins (notification, event_bus, …) now harvest reads/writes
    from the lock-matching block.json. Pre-flip honesty is proven on an
    estate stub that still has no L2 keys.
    """
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("estate_roll", ["estate_registry"], "REUSE")),
        store_ids={"estate_registry"},
    )
    verify_inventory(compiled)
    assert compiled.reuse_records["estate_registry"]["present"] is True
    assert compiled.reuse_records["estate_registry"]["scope_declared"] is False
    assert compiled.reuse_records["estate_registry"]["reads"] == []
    assert "not declared on block.json (pre-flip)" in compiled.text
    assert "do not invent scopes" in compiled.text
    # Must not invent a clinic/reminder scope the mirror never declared.
    assert "appointment_slot" not in compiled.text
    assert "sms_body" not in compiled.text
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_compiler_does_not_invent_scopes_when_http_omits_l2(monkeypatch, preflip_store):
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def fake_get(block_id, base_url=None):
        return ReuseRecord(
            block_id=block_id,
            present=True,
            source="registry/blocks",
            scope_declared=False,
        )

    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("estate_roll", ["estate_registry"], "REUSE")),
        store_ids={"estate_registry"},
        reuse_http_get=fake_get,
    )
    assert "not declared on block.json (pre-flip)" in compiled.text
    rec = compiled.reuse_records["estate_registry"]
    assert rec["reads"] == []
    assert rec["writes"] == []
    assert rec["never"] == []
    assert rec["acceptance"] == []


def test_case_sensitive_claimed_reuse_does_not_match_folded_id():
    compiled = compile_brief(
        _Blueprint(),
        _Plan(_Cap("docs", ["DocumentEngine"], "REUSE")),
        store_ids={"document_engine"},
    )
    assert compiled.missing_reuse == ["DocumentEngine"]
    with pytest.raises(InventoryHalt, match="DocumentEngine"):
        verify_inventory(compiled)


def test_vetcare_invented_readiness_engine_reuse_dropped_on_store_miss(monkeypatch):
    """Live sess_8ef2f7b7ae594b85: architect claimed readiness_engine REUSE.

    The Factory vendor mirror / dual-registry lists readiness_engine (estate
    property-readiness). The Blocks exact-id surface does not. STEP 0 must
    drop the claim — veterinarian_availability_tracking is a GAP — not HALT
    before WRITER with BRIEF_LINT_REJECTED.
    """
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def fake_get(block_id, base_url=None):
        return ReuseRecord(block_id=block_id, present=False, source="registry/blocks")

    compiled = compile_brief(
        _Blueprint(),
        _Plan(
            _Cap(
                "veterinarian_availability_tracking",
                ["readiness_engine"],
                "REUSE",
            )
        ),
        store_ids={"readiness_engine", "event_bus"},
        reuse_http_get=fake_get,
    )
    assert compiled.missing_reuse == []
    item = compiled.inventory[0]
    assert item.capability_id == "veterinarian_availability_tracking"
    assert item.dropped_reuse == ["readiness_engine"]
    assert item.block_ids == []
    assert item.verified_present == []
    assert item.missing == []
    assert item.is_gap
    verify_inventory(compiled)
    result = lint_brief(compiled)
    assert result.ok, result.errors
    assert "unverified REUSE dropped" in compiled.text
    assert "readiness_engine" in compiled.text
    reuse_section = compiled.text.split("REUSE (verified present):", 1)[1].split(
        "GAPS (you author", 1
    )[0]
    assert "readiness_engine" not in reuse_section
    assert "veterinarian_availability_tracking:readiness_engine" not in compiled.text


def test_vetcare_readiness_engine_reuse_kept_when_exact_id_hits(monkeypatch):
    """If the Store exact-id endpoint confirms the id, REUSE stands."""
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

    def fake_get(block_id, base_url=None):
        return ReuseRecord(block_id=block_id, present=True, source="registry/blocks")

    compiled = compile_brief(
        _Blueprint(),
        _Plan(
            _Cap(
                "veterinarian_availability_tracking",
                ["readiness_engine"],
                "REUSE",
            )
        ),
        store_ids={"readiness_engine"},
        reuse_http_get=fake_get,
    )
    verify_inventory(compiled)
    item = compiled.inventory[0]
    assert item.verified_present == ["readiness_engine"]
    assert item.dropped_reuse == []
    assert item.missing == []
    assert item.is_reuse
    assert compiled.missing_reuse == []
    assert lint_brief(compiled).ok, lint_brief(compiled).errors
    assert "REUSE ['readiness_engine']" in compiled.text


def test_setup_error_findings_name_the_shared_break_not_a_test_list():
    # Live automotive build: every rework finding was "<nodeid> [error]" --
    # pytest died during SETUP (a missing shared fixture), one break, not
    # sixty defects. The writer was handed the raw list, chased it test by
    # test, and G5 stopped the run on SAME_FAILURE_TWICE. The brief must
    # diagnose the shape: fix the shared setup first.
    bp = load_blueprint(SMOKE)
    plan = plan_blueprint(bp)
    findings = [
        "tests/test_platform_routes.py::test_capability_accepts_and_returns_its_own_record[email] [error]",
        "tests/test_negative_floor_counter_cases.py::test_negative_email_refuses_malformed_address [error]",
        "tests/test_routes.py::test_every_capability_route_answers [failure]",
    ]
    compiled = compile_brief(bp, plan, work_list=findings)
    text = compiled.text
    assert "died during SETUP" in text
    assert "conftest" in text
    assert "fixture" in text
    # The raw rows still ride along -- the diagnosis is added, not replacing.
    assert "test_every_capability_route_answers [failure]" in text


def test_plain_failures_get_no_setup_diagnosis():
    bp = load_blueprint(SMOKE)
    plan = plan_blueprint(bp)
    findings = ["tests/test_routes.py::test_every_capability_route_answers [failure]"]
    compiled = compile_brief(bp, plan, work_list=findings)
    assert "died during SETUP" not in compiled.text
