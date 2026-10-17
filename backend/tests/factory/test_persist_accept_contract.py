"""C-BRIEF must ground PRODUCT one-record persist / re-read.

Photographed Floor (2026-09-05, platforms-poll5 / sess_108e101) after a
successful Kimi FACTORY_CODE_CLI WRITER:

    PRODUCT (one-record round-trip): 3 capability(ies) did not remember
    a record they were given.

    audit: POST raised OperationalError: no such table: audit
    dashboard: POST raised OperationalError: no such table: dashboard
    veterinary_care_core: POST raised OperationalError: no such table:
        veterinary_care_core

Those ids are the keyword-fallback architect roster. This module is the
compiler + emit + harness contract — not a VetCare product patch.
Export honesty stays refused when the pilot suite / PRODUCT is red.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.brief_compiler import brief_fingerprint, compile_brief
from app.factory.build.brief_lint import lint_brief
from app.factory.build.data_lifecycle import (
    render_revision_0001,
    render_store,
)
from app.factory.build.persist_accept import (
    FACTORY_GROUNDED_PERSIST_SOURCE,
    PERSIST_ISOLATE_NEEDLE,
    PRODUCT_NO_SUCH_TABLE_HALT,
    PRODUCT_ROUND_TRIP_CHECK,
    PRODUCT_ROUND_TRIP_HALT,
    WRITER_PERSIST_HALT,
    PersistRoundTripHalt,
    assert_persist_round_trip_ready,
    persist_accept_acceptance_line,
    persist_accept_brief_contract,
    persist_accept_forbidden_lines,
    persist_entity_of,
    persist_handler_rel,
    persist_round_trip_errors,
    persist_workspace_root,
    bind_generate_artifacts,
    emit_factory_grounded_generate_persist,
    wipe_workspace_runtime_db,
)
from app.factory.build.product_gate import ROUND_TRIP_PROBE
from app.factory.build.roles_handlers import (
    _capability_handler_body,
    _fallback_spec,
    _handler_module,
    _templated_route_body,
)
from app.factory.build.writer_behaviour import BEHAVIOUR_PROBE
from app.factory.build.writer_brief import CODING_AGENT_BRIEF
from app.factory.coder import _WHOLE_JOB_SYSTEM
from app.factory.product_architect import (
    plan_blueprint,
)

#: A sample keyword-fallback plan -- test data only.
SAMPLE_FALLBACK_CAPS = ("veterinary_care_core", "audit", "dashboard")

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
    product_name = "Veterinary Care Platform"
    product_id = "veterinary-care"
    vertical = "veterinary_care"
    summary = "Clinic appointments, reminders, and pet records."


def _keyword_fallback_plan():
    return _Plan(
        _Cap("veterinary_care_core", [], "GENERATE"),
        _Cap("audit", ["audit"], "REUSE"),
        _Cap("dashboard", ["dashboard"], "REUSE"),
    )


def test_vetcare_compiled_brief_grounds_persist_round_trip():
    compiled = compile_brief(
        _VetCare(),
        _keyword_fallback_plan(),
        store_ids={"audit", "dashboard", "database"},
    )
    text = compiled.text
    assert persist_accept_acceptance_line() in text
    assert persist_accept_forbidden_lines() in text
    assert persist_accept_brief_contract() in CODING_AGENT_BRIEF


def test_lettings_and_smoke_still_lint_with_persist_accept():
    for path in (SMOKE, LETTINGS):
        bp = load_blueprint(path)
        compiled = compile_brief(bp, plan_blueprint(bp))
        assert PRODUCT_ROUND_TRIP_HALT in compiled.text
        assert persist_accept_acceptance_line() in compiled.text
        result = lint_brief(compiled)
        assert result.ok, (path.name, result.errors)


def test_lettings_golden_roster_and_fingerprint_unchanged():
    bp = load_blueprint(LETTINGS_GOLDEN)
    assert {c.id for c in bp.capabilities} == LIVE_LETTINGS_CAPS
    golden = load_blueprint(LETTINGS_GOLDEN)
    assert {c.id for c in golden.capabilities} == LIVE_LETTINGS_CAPS
    compiled = compile_brief(bp, plan_blueprint(bp))
    fp = brief_fingerprint(compiled)
    assert set(fp["capabilities"]) == LIVE_LETTINGS_CAPS
    assert fp["missing_reuse"] == []
    assert lint_brief(compiled).ok, lint_brief(compiled).errors


def test_system_brief_and_oneshot_name_the_persist_halt():
    contract = persist_accept_brief_contract()
    assert PRODUCT_ROUND_TRIP_HALT in contract or "store.save(ENTITY, payload)" in contract
    assert PRODUCT_NO_SUCH_TABLE_HALT in contract
    assert contract in CODING_AGENT_BRIEF
    assert "tenant-scoped save(payload)" in _WHOLE_JOB_SYSTEM
    assert PRODUCT_NO_SUCH_TABLE_HALT in _WHOLE_JOB_SYSTEM
    assert f"[check:{PRODUCT_ROUND_TRIP_CHECK}]" in persist_accept_acceptance_line()


def test_product_probe_and_writer_probe_both_isolate_storage():
    assert PERSIST_ISOLATE_NEEDLE in ROUND_TRIP_PROBE
    assert 'tempfile.mkdtemp(prefix="writer-gate-")' in BEHAVIOUR_PROBE
    # A missing persist table is a typed halt the probe emits (kind
    # "persist"), decided by the exception's class -- not a phrase in stderr.
    from app.factory.build.writer_behaviour import HALT_SENTENCES, KIND_PERSIST

    assert '_halt("persist"' in BEHAVIOUR_PROBE
    assert "_db_error_types()" in BEHAVIOUR_PROBE
    assert HALT_SENTENCES[KIND_PERSIST]


def test_factory_generate_body_is_pure_dispatch_not_no_block_bound():
    body = _capability_handler_body("veterinary_care_core", [])
    assert "no_block_bound" not in body
    assert "_persist_record" not in body
    assert "store.save" not in body
    assert 'return {"ok": True, "capability": CAPABILITY_ID}' in body
    module = _handler_module(
        "veterinary_care_core", [], body, "deterministic contract template",
        entity="veterinary_care_core",
    )
    assert "def _persist_record" not in module
    assert "store.save" not in module
    assert "from app import store" not in module
    assert "def handle" in module


def test_factory_reuse_body_dispatches_after_blocks():
    body = _capability_handler_body("audit", ["audit"])
    assert "_persist_record" not in body
    assert "store.save" not in body
    assert "no_block_bound" not in body
    assert '"results": results' in body


def test_templated_route_wraps_save_so_post_does_not_raise():
    spec = _fallback_spec(_Cap("audit", ["audit"]))
    body = _templated_route_body(spec)
    assert "stored = save(payload)" in body
    assert "except Exception as exc" in body


def _write_persist_workspace(tmp_path: Path, specs: dict, *, persist_handlers=True):
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    (tmp_path / "app" / "actions").mkdir(parents=True)
    (tmp_path / "alembic" / "versions" / "0001_baseline.py").write_text(
        render_revision_0001(specs), encoding="utf-8"
    )
    (tmp_path / "app" / "store.py").write_text(render_store(specs), encoding="utf-8")
    routes = []
    for cid, spec in specs.items():
        name = cid.replace("-", "_")
        entity = persist_entity_of(spec, cid)
        if persist_handlers:
            body = _capability_handler_body(cid, list(spec.get("block_ids") or []))
        else:
            body = '    return {"ok": True, "capability": CAPABILITY_ID}'
        (tmp_path / "app" / "actions" / f"{name}.py").write_text(
            _handler_module(cid, spec.get("block_ids") or [], body, "test", entity=entity),
            encoding="utf-8",
        )
        # A real (parseable) route: persist_accept reads routes.py's syntax
        # tree, so the fixture must be Python, not an indented fragment.
        routes.append(
            f"\n\ndef _route_{name}(payload):\n"
            f'    save = lambda record: store.save("{entity}", record)\n'
            f"    stored = save(payload)\n"
            f"    return stored\n"
        )
    (tmp_path / "app" / "routes.py").write_text(
        "from app import store\n" + "".join(routes), encoding="utf-8"
    )


def test_harness_fails_when_alembic_omits_a_persist_entity(tmp_path):
    specs = {
        "audit": {"entity": "audit", "fields": [{"name": "reference", "type": "str"}]},
        "dashboard": {
            "entity": "dashboard",
            "fields": [{"name": "reference", "type": "str"}],
        },
        "veterinary_care_core": {
            "entity": "veterinary_care_core",
            "fields": [{"name": "reference", "type": "str"}],
        },
    }
    dropped = dict(specs)
    dropped.pop("audit")
    _write_persist_workspace(tmp_path, dropped)
    # Routes/handlers still declare audit, but 0001 does not.
    (tmp_path / "app" / "actions" / "audit.py").write_text(
        _handler_module(
            "audit", ["audit"],
            _capability_handler_body("audit", ["audit"]),
            "test",
            entity="audit",
        ),
        encoding="utf-8",
    )
    specs_all = dict(specs)
    errors = persist_round_trip_errors(tmp_path, specs_all)
    assert any("audit" in e for e in errors)
    with pytest.raises(PersistRoundTripHalt) as halted:
        assert_persist_round_trip_ready(tmp_path, specs_all)
    assert WRITER_PERSIST_HALT in str(halted.value)
    assert "audit" in str(halted.value)


def test_harness_fails_when_handler_persists_to_the_wrong_table(tmp_path):
    specs = {
        "audit": {"entity": "audit_event", "fields": [{"name": "reference", "type": "str"}]},
    }
    _write_persist_workspace(tmp_path, specs)
    (tmp_path / "app" / "actions" / "audit.py").write_text(
        "def handle(payload):\n"
        "    from app import store\n"
        "    return store.save('audit', payload)\n",
        encoding="utf-8",
    )
    errors = persist_round_trip_errors(tmp_path, specs)
    assert any("audit" in e and "audit_event" in e for e in errors)


def test_harness_passes_factory_grounded_keyword_fallback(tmp_path):
    specs = {
        cid: _fallback_spec(_Cap(cid, [] if cid.endswith("_core") else [cid]))
        for cid in SAMPLE_FALLBACK_CAPS
    }
    _write_persist_workspace(tmp_path, specs)
    assert persist_round_trip_errors(tmp_path, specs) == []
    assert_persist_round_trip_ready(tmp_path, specs)


def test_wipe_workspace_runtime_db_removes_stale_platform_db(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "platform.db").write_bytes(b"stale")
    wipe_workspace_runtime_db(tmp_path)
    assert not data.exists()


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "0")
    monkeypatch.setenv("FACTORY_CODE_CLI", "")


def test_emitted_keyword_fallback_vetcare_round_trips(tmp_path):
    """0.5: factory-LLM GENERATE fallthrough writes zero coding-agent
    artifacts, so the WRITER refuses with writer_no_output.

    The persist-emit contract itself (persist + PRODUCT round-trip) is
    covered by the direct emit tests below --
    test_emit_factory_grounded_generate_persist_* -- because a refused
    writer commits nothing to round-trip against.
    """
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext, RoleError, run_writer
    from app.factory.build.workspace import RoleWorkspace

    os.environ["FACTORY_CODER_ENABLED"] = "0"
    out = tmp_path / "vetcare"
    generate_plan = _Plan(
        _Cap("veterinary_care_core", [], "GENERATE"),
        _Cap("audit", [], "GENERATE"),
        _Cap("dashboard", [], "GENERATE"),
    )
    ws = RoleWorkspace(BuildRole.WRITER, out)
    with pytest.raises(RoleError, match="writer_no_output"):
        run_writer(
            RoleContext(
                role=BuildRole.WRITER,
                workspace=ws,
                blueprint=_VetCare(),
                plan=generate_plan,
                state={
                    "resolved_blocks": (),
                    "vendored_blocks": (),
                    "gaps": list(SAMPLE_FALLBACK_CAPS),
                },
            )
        )


def test_persist_workspace_root_prefers_staging_not_destination(tmp_path):
    from app.factory.build.authority import BuildRole
    from app.factory.build.workspace import RoleWorkspace

    dest = tmp_path / "dest"
    staging = tmp_path / "stage"
    dest.mkdir()
    ws = RoleWorkspace(BuildRole.WRITER, dest, staging=staging)
    assert persist_workspace_root(ws) == staging.resolve()
    assert persist_handler_rel("veterinary_care_core") == Path(
        "app/actions/veterinary_care_core.py"
    )


def test_emit_factory_grounded_generate_persist_wraps_llm_body(tmp_path):
    class _Item:
        def __init__(self, cid, gap):
            self.capability_id = cid
            self.is_gap = gap
            self.block_ids = []
            self.verified_present = []

    class _Compiled:
        inventory = (
            _Item("veterinary_care_core", True),
            _Item("audit", False),
        )

    written = emit_factory_grounded_generate_persist(
        tmp_path,
        _Compiled(),
        handlers={
            "veterinary_care_core": '    return {"ok": True, "capability": CAPABILITY_ID}',
        },
        specs={
            "veterinary_care_core": {
                "entity": "veterinary_care_core",
                "fields": [{"name": "reference", "type": "str"}],
            }
        },
        source="coder LLM (factory)",
    )
    assert written == ["veterinary_care_core"]
    text = (tmp_path / "app" / "actions" / "veterinary_care_core.py").read_text(
        encoding="utf-8"
    )
    assert "_persist_record(" not in text
    assert "def handle" in text
    assert "coder LLM (factory)" in text
    empty = emit_factory_grounded_generate_persist(
        tmp_path / "empty",
        _Compiled(),
        handlers={},
        specs={},
    )
    assert empty == ["veterinary_care_core"]
    empty_text = (
        tmp_path / "empty" / "app" / "actions" / "veterinary_care_core.py"
    ).read_text(encoding="utf-8")
    assert "_persist_record(" not in empty_text
    assert "def handle" in empty_text
    assert FACTORY_GROUNDED_PERSIST_SOURCE in empty_text
    assert "deterministic contract template" not in empty_text


def test_bind_generate_artifacts_sess_336246_alias_and_unique_leftover():
    """Architect GENERATE id vs factory-LLM veterinary_care_core key."""
    bound = bind_generate_artifacts(
        ["vetcare_hub_veterinary_core"],
        {"veterinary_care_core": "    return {'ok': True}"},
    )
    assert list(bound) == ["vetcare_hub_veterinary_core"]
    hyphen = bind_generate_artifacts(
        ["vetcare_hub_veterinary_core"],
        {"vetcare-hub-veterinary-core": {"entity": "vetcare_hub_veterinary_core"}},
    )
    assert "vetcare_hub_veterinary_core" in hyphen
    exact = bind_generate_artifacts(
        ["vetcare_hub_veterinary_core"],
        {"vetcare_hub_veterinary_core": "body", "noise": "x"},
    )
    assert exact == {"vetcare_hub_veterinary_core": "body"}
    two = bind_generate_artifacts(
        ["alpha_core", "beta_core"],
        {"veterinary_care_core": "nope"},
    )
    assert two == {}
    noisy = bind_generate_artifacts(
        ["vetcare_hub_veterinary_core"],
        {
            "veterinary_care_core": "body",
            "readme": "ignore",
        },
    )
    assert noisy == {"vetcare_hub_veterinary_core": "body"}
