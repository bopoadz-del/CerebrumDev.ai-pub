"""BRIEF LINT rejects a broken brief before the coder session opens."""

from __future__ import annotations

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.brief_compiler import compile_brief
from app.factory.build.brief_lint import BriefLintError, lint_brief, lint_or_raise
from app.factory.product_architect import plan_blueprint
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
LETTINGS = ROOT / "blueprints/lettings/residential_lettings.v1.yaml"


def _compiled(path=SMOKE):
    bp = load_blueprint(path)
    return compile_brief(bp, plan_blueprint(bp))


def test_smoke_and_lettings_briefs_lint_clean():
    for path in (SMOKE, LETTINGS):
        compiled = _compiled(path)
        result = lint_brief(compiled)
        assert result.ok, result.errors


def test_mutation_unresolved_block_id_is_rejected():
    compiled = _compiled()
    compiled.missing_reuse = ["phantom_block_xyz"]
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("unresolved block id" in e for e in result.errors)
    with pytest.raises(BriefLintError, match="phantom_block_xyz"):
        lint_or_raise(compiled)


def test_mutation_missing_budget_is_rejected():
    compiled = _compiled()
    compiled.budget_s = 0
    compiled.text = compiled.text.replace("Budget wall:", "No wall named:")
    compiled.text = compiled.text.replace("1800s", "unset")
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("missing budget" in e for e in result.errors)


def test_mutation_acceptance_without_check_is_rejected():
    compiled = _compiled()
    # The lint reads the compiled ACCEPTANCE section by key, not by heading.
    compiled.slots["ACCEPTANCE"] = (
        compiled.slots["ACCEPTANCE"] + "\n- feel good about the screens"
    )
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("acceptance line without executable check" in e for e in result.errors)


def test_mutation_planted_unsourced_line_is_rejected():
    compiled = _compiled()
    compiled.text = compiled.text + "\nInvent a loyalty program the customer never asked for.\n"
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("orphan line" in e for e in result.errors)


def test_mutation_invented_scope_line_is_rejected():
    compiled = _compiled()
    compiled.text = (
        compiled.text
        + "\nInvent READS=loyalty_points the customer never declared on block.json.\n"
    )
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("orphan line" in e for e in result.errors)


def test_vetcare_dropped_readiness_engine_reuse_lints_clean(monkeypatch):
    """Invented readiness_engine REUSE is a GAP, not an unresolved-id lint fail."""
    from app.factory.build.brief_compiler import compile_brief
    from app.factory.build.reuse_lookup import ReuseRecord

    monkeypatch.delenv("CEREBRUM_API_URL", raising=False)

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

    def fake_get(block_id, base_url=None):
        return ReuseRecord(block_id=block_id, present=False, source="registry/blocks")

    compiled = compile_brief(
        _VetCare(),
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
    result = lint_brief(compiled)
    assert result.ok, result.errors
    assert result.checks["missing_reuse"] == []
    compiled.missing_reuse = ["readiness_engine"]
    planted = lint_brief(compiled)
    assert planted.ok is False
    assert any("unresolved block id" in e and "readiness_engine" in e for e in planted.errors)


def test_unfilled_template_slot_is_rejected():
    compiled = _compiled()
    compiled.text = compiled.text + "\n{{ORPHAN_SLOT}}\n"
    result = lint_brief(compiled)
    assert result.ok is False
    assert any("unfilled template slot" in e for e in result.errors)


# --- shape cases: each refuses a CLASS of leak, on invented names --------


def test_brief_citing_a_build_session_is_refused():
    compiled = _compiled()
    compiled.text += "\nsess_0a1b2c3d4e5f\n"
    errors = lint_brief(compiled).errors
    assert any("cites a build session" in e for e in errors), errors


def test_brief_naming_another_products_capability_is_refused():
    compiled = _compiled()
    compiled.text += "\nzorblat_intake\n"
    compiled.emitted_lines = frozenset(compiled.emitted_lines | {"zorblat_intake"})
    errors = lint_brief(compiled, known_literals=frozenset({"zorblat_intake"})).errors
    assert any("another product" in e and "zorblat_intake" in e for e in errors), errors


def test_brief_naming_its_own_capability_is_not_foreign():
    compiled = _compiled()
    own = compiled.inventory[0].capability_id
    assert lint_brief(compiled, known_literals=frozenset({own})).ok

