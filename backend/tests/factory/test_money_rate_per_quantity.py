"""money ÷ a declared quantity-typed field is a unit rate (owner ruling 0825Z).

Live, a repeat fintech/logistics pick: the WRITER computed a cost-per-km /
cost-per-hour by dividing a money amount by a declared quantity, and the money
gate failed it as ``a money value is scaled by a factor not taken from
app.money_settings`` -- a false positive. A money value divided by a declared
non-money quantity (hours, km, units, days -- a field the model declares with
type ``quantity`` or ``meter``) yields a RATE, unit-typed (money per
quantity-unit), and is allowed.

The guardrails stay, tested both ways: a bare literal divisor, or an unsourced
scalar divisor, is still red; and the allowance is keyed on the DECLARED
quantity type, never on a name -- the same divisor is red when the model does
not declare it a quantity. Nothing here is keyed on a product, brief or
session. The Zorblat product, its fields and its locale are invented.
"""

from __future__ import annotations

from pathlib import Path

from app.factory.build.money_contract import (
    DECLARED_LOCALE_REL,
    MONEY_SETTINGS_REL,
    declared_locale,
    money_brief_lines,
    money_verdict,
    render_declared_locale,
    render_money_settings,
)

MODELS = '''
class ZorblatDelivery:
    FIELDS = ["fee", "distance_km"]
    CONSTRAINTS = {}
    MONEY_FIELDS = ["fee"]
    QUANTITY_FIELDS = ["distance_km"]
'''

RATE_PER_QUANTITY = '''
def handle(payload):
    cost_per_km = payload["fee"] / payload["distance_km"]
    return {"ok": True, "rate": cost_per_km}
'''

LITERAL_DIVISOR = '''
def handle(payload):
    return {"ok": True, "rate": payload["fee"] / 3}
'''

UNSOURCED_DIVISOR = '''
def handle(payload, divisor):
    return {"ok": True, "rate": payload["fee"] / divisor}
'''


def _product(root: Path, models: str, **files: str) -> Path:
    for rel, body in {"app/__init__.py": "", "app/models.py": models, **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (root / DECLARED_LOCALE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / DECLARED_LOCALE_REL).write_text(
        render_declared_locale(declared_locale("ae", "aed")), encoding="utf-8"
    )
    (root / MONEY_SETTINGS_REL).write_text(render_money_settings(), encoding="utf-8")
    return root


def test_money_divided_by_a_declared_quantity_is_a_rate_and_passes(tmp_path):
    verdict = money_verdict(
        _product(tmp_path, MODELS, **{"app/actions/zorblat_delivery.py": RATE_PER_QUANTITY})
    )
    assert verdict.status == "PASS", verdict.findings


def test_a_literal_divisor_on_money_is_still_red(tmp_path):
    verdict = money_verdict(
        _product(tmp_path, MODELS, **{"app/actions/zorblat_delivery.py": LITERAL_DIVISOR})
    )
    assert verdict.status == "FAIL"
    assert any("3" in f for f in verdict.findings), verdict.findings


def test_an_unsourced_divisor_on_money_is_still_red(tmp_path):
    verdict = money_verdict(
        _product(tmp_path, MODELS, **{"app/actions/zorblat_delivery.py": UNSOURCED_DIVISOR})
    )
    assert verdict.status == "FAIL", verdict.findings


def test_the_allowance_is_keyed_on_the_declared_type_not_the_name(tmp_path):
    # Identical handler, but the model no longer declares distance_km a
    # quantity: the divisor is then an unsourced scalar and the gate stays red.
    models = MODELS.replace('    QUANTITY_FIELDS = ["distance_km"]\n', "")
    verdict = money_verdict(
        _product(tmp_path, models, **{"app/actions/zorblat_delivery.py": RATE_PER_QUANTITY})
    )
    assert verdict.status == "FAIL", "an undeclared divisor is not a rate"


def test_the_model_emits_quantity_fields_from_the_declared_type():
    from app.factory.build.roles_handlers import _render_models

    src = _render_models({"zd": {"entity": "zorblat_delivery", "fields": [
        {"name": "fee", "type": "money"},
        {"name": "distance_km", "type": "quantity"},
        {"name": "note", "type": "str"},
    ]}})
    assert "MONEY_FIELDS = ['fee']" in src
    assert "QUANTITY_FIELDS = ['distance_km']" in src


def test_meter_typed_fields_are_quantities_too():
    from app.factory.build.roles_handlers import _render_models

    src = _render_models({"m": {"entity": "meter_reading", "fields": [
        {"name": "charge", "type": "money"},
        {"name": "kwh", "type": "meter"},
    ]}})
    assert "QUANTITY_FIELDS = ['kwh']" in src


def test_the_brief_states_the_rate_per_quantity_rule():
    lines = "\n".join(money_brief_lines({"locale": {"country": "AE", "currency": "AED"}}))
    assert "quantity" in lines.lower()
    assert "rate" in lines.lower()
