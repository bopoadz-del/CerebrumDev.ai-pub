"""A unit rate is a derivation, not a typed rate (owner ruling 0825Z).

The money contract refuses a money value scaled by a rate typed into code
(``cost / hours`` read as "a factor not from money_settings" blocked the live
Fleet Maintenance build at WRITER). But a per-unit rate -- a money field
divided by a QUANTITY -- is a legitimate derivation, and a money field
multiplied by a QUANTITY is a total. The ruling:

* money / a declared quantity field  -> ALLOWED (a unit rate);
* money * a declared quantity field  -> ALLOWED (a total);
* money / money                       -> ALLOWED (a dimensionless ratio);
* money scaled by a plain number or an undeclared variable -> still REFUSED.

The allowance is driven by the field's DECLARED type (QUANTITY_FIELDS), never
its name: the same arithmetic on an undeclared divisor stays red.

Everything here is invented: the WidgetJob product, its fields and its locale.
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
class WidgetJob:
    FIELDS = ["labor_cost", "part_cost", "hours", "team_size", "memo"]
    CONSTRAINTS = {}
    MONEY_FIELDS = ["labor_cost", "part_cost"]
    QUANTITY_FIELDS = ["hours"]
'''

AE = declared_locale("ae", "aed")


def _product(root: Path, action: str) -> Path:
    for rel, body in {"app/models.py": MODELS, "app/actions/widget_job.py": action}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (root / DECLARED_LOCALE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / DECLARED_LOCALE_REL).write_text(render_declared_locale(AE), encoding="utf-8")
    (root / MONEY_SETTINGS_REL).write_text(render_money_settings(), encoding="utf-8")
    return root


MONEY_OVER_QUANTITY = '''
def handle(payload):
    cost = payload["labor_cost"]
    hours = payload["hours"]
    return {"ok": True, "hourly_rate": cost / hours}
'''

MONEY_TIMES_QUANTITY = '''
def handle(payload):
    rate = payload["labor_cost"]
    hours = payload["hours"]
    return {"ok": True, "total": rate * hours}
'''

MONEY_OVER_UNDECLARED = '''
def handle(payload):
    cost = payload["labor_cost"]
    return {"ok": True, "per_head": cost / payload["team_size"]}
'''

MONEY_OVER_LITERAL = '''
def handle(payload):
    return {"ok": True, "split": payload["labor_cost"] / 3}
'''

MONEY_OVER_MONEY = '''
def handle(payload):
    return {"ok": True, "share": payload["part_cost"] / payload["labor_cost"]}
'''


def test_money_divided_by_a_declared_quantity_is_a_rate(tmp_path):
    verdict = money_verdict(_product(tmp_path, MONEY_OVER_QUANTITY))
    assert verdict.status == "PASS", verdict.findings


def test_money_times_a_declared_quantity_is_a_total(tmp_path):
    verdict = money_verdict(_product(tmp_path, MONEY_TIMES_QUANTITY))
    assert verdict.status == "PASS", verdict.findings


def test_money_divided_by_an_undeclared_field_is_still_red(tmp_path):
    # Same arithmetic as the rate test, but team_size is not a declared
    # quantity -- the allowance is the TYPE, not the shape of the expression.
    verdict = money_verdict(_product(tmp_path, MONEY_OVER_UNDECLARED))
    assert verdict.status == "FAIL"
    assert any("team_size" in f or "not taken from" in f for f in verdict.findings)


def test_money_divided_by_a_literal_is_still_red(tmp_path):
    verdict = money_verdict(_product(tmp_path, MONEY_OVER_LITERAL))
    assert verdict.status == "FAIL"
    assert any("3" in f for f in verdict.findings)


def test_money_over_money_is_a_ratio_and_passes(tmp_path):
    verdict = money_verdict(_product(tmp_path, MONEY_OVER_MONEY))
    assert verdict.status == "PASS", verdict.findings


def test_the_emitted_model_declares_quantity_fields_from_the_declared_type():
    from app.factory.build.roles_handlers import _render_models

    src = _render_models({"widget_job": {"entity": "widget_job", "fields": [
        {"name": "labor_cost", "type": "money"},
        {"name": "hours", "type": "quantity"},
        {"name": "memo", "type": "str"},
    ]}})
    assert "MONEY_FIELDS = ['labor_cost']" in src
    assert "QUANTITY_FIELDS = ['hours']" in src


def test_the_brief_tells_the_writer_a_unit_rate_is_a_quantity_derivation():
    lines = "\n".join(money_brief_lines({"locale": {"country": "AE", "currency": "AED"}}))
    assert "QUANTITY_FIELDS" in lines
    assert "quantity" in lines
