"""money_contract is a VETO again, decided by structure (owner ruling 2026-10-05).

FinOps (sess_065fc3eac75c4f62) froze UK VAT and GBP for a Dubai business.
The locale is now the USER's typed answer (Floor -> blueprint.locale ->
docs/declared_locale.json), money fields are declared by type (MONEY_FIELDS),
and the gate reads the syntax tree:

* nothing declared -> WITHHELD(no currency declared), never a frozen guess;
* declared, VAT computed from app.money_settings -> PASS;
* a literal rate on a money value, or the declared code as a literal -> FAIL
  with file:line.

Everything here is invented: the Zorblat product, its fields and its locale.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import ProductBlueprint
from app.factory.build import money_contract
from app.factory.build.money_contract import (
    DECLARED_LOCALE_REL,
    MONEY_SETTINGS_REL,
    WITHHELD_NO_CURRENCY,
    declared_locale,
    emit_money_artifacts,
    money_brief_lines,
    money_verdict,
    render_declared_locale,
    render_money_settings,
)

MODELS = '''
class ZorblatInvoice:
    FIELDS = ["net_amount", "memo"]
    CONSTRAINTS = {}
    MONEY_FIELDS = ["net_amount"]
'''


def _product(root: Path, locale, **files: str) -> Path:
    files = {"app/models.py": MODELS, **files}
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (root / DECLARED_LOCALE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / DECLARED_LOCALE_REL).write_text(render_declared_locale(locale), encoding="utf-8")
    (root / MONEY_SETTINGS_REL).write_text(render_money_settings(), encoding="utf-8")
    return root


GB = declared_locale("gb", "gbp")

READS_SETTINGS = '''
from app.money_settings import tax_rate


def handle(payload):
    net = payload["net_amount"]
    vat = net * tax_rate()
    return {"ok": True, "vat": vat}
'''

LITERAL_RATE = '''
def handle(payload):
    net = payload["net_amount"]
    return {"ok": True, "vat": net * 0.2}
'''

LITERAL_CODE = '''
from app.money_settings import tax_rate


def handle(payload):
    return {"ok": True, "currency": "GBP", "vat": payload["net_amount"] * tax_rate()}
'''


def test_a_declared_pair_with_vat_read_from_settings_passes(tmp_path):
    ws = _product(tmp_path, GB, **{"app/actions/zorblat_invoice.py": READS_SETTINGS})
    verdict = money_verdict(ws)
    assert verdict.status == "PASS", verdict.findings
    assert "GB/GBP" in verdict.reason


def test_nothing_declared_is_withheld_never_guessed(tmp_path):
    ws = _product(tmp_path, None, **{"app/actions/zorblat_invoice.py": READS_SETTINGS})
    verdict = money_verdict(ws)
    assert verdict.status == "WITHHELD"
    assert verdict.reason == WITHHELD_NO_CURRENCY


def test_a_literal_rate_in_code_is_red_with_file_and_line(tmp_path):
    ws = _product(tmp_path, GB, **{"app/actions/zorblat_invoice.py": LITERAL_RATE})
    verdict = money_verdict(ws)
    assert verdict.status == "FAIL"
    assert verdict.findings[0].startswith("app/actions/zorblat_invoice.py:4:")
    assert "0.2" in verdict.findings[0]


def test_the_declared_code_as_a_literal_is_red(tmp_path):
    ws = _product(tmp_path, GB, **{"app/actions/zorblat_invoice.py": LITERAL_CODE})
    verdict = money_verdict(ws)
    assert verdict.status == "FAIL"
    assert any("'GBP'" in f for f in verdict.findings)


def test_a_factor_not_taken_from_settings_is_red(tmp_path):
    src = "def handle(payload, rate):\n    return payload['net_amount'] * rate\n"
    ws = _product(tmp_path, GB, **{"app/actions/zorblat_invoice.py": src})
    assert money_verdict(ws).status == "FAIL"


def test_identity_factors_are_not_rates(tmp_path):
    src = "def handle(payload):\n    return payload['net_amount'] * 1\n"
    ws = _product(tmp_path, GB, **{"app/actions/zorblat_invoice.py": src})
    assert money_verdict(ws).status == "PASS"


def test_no_money_and_no_locale_is_not_applicable(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "models.py").write_text("class Zorblat:\n    MONEY_FIELDS = []\n", encoding="utf-8")
    assert money_verdict(tmp_path).status == "n/a"


def test_the_locale_is_validated_by_shape_only():
    assert declared_locale("gb", "gbp") == {"country": "GB", "currency": "GBP"}
    assert declared_locale("Britain", "GBP") is None
    assert declared_locale("GB", "pounds") is None
    with pytest.raises(ValueError):
        ProductBlueprint.model_validate({
            "schema_version": "product_blueprint.v1", "product_id": "zorblat",
            "product_name": "Zorblat", "vertical": "product", "summary": "s",
            "capabilities": [], "locale": {"country": "Britain", "currency": "GBP"},
        })


def test_the_factory_stamps_the_declared_pair_and_a_settings_module(tmp_path):
    bp = {"locale": {"country": "GB", "currency": "GBP"}}
    emit_money_artifacts(tmp_path, bp)
    record = json.loads((tmp_path / DECLARED_LOCALE_REL).read_text(encoding="utf-8"))
    assert record["country"] == "GB" and record["currency"] == "GBP"
    probe = (
        "import sys; sys.path.insert(0, '.')\n"
        "from app import money_settings as m\n"
        "print(m.country(), m.currency())\n"
        "try:\n    m.tax_rate()\nexcept m.MoneySettingMissing:\n    print('no rate')\n"
    )
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_")}
    out = subprocess.run([sys.executable, "-c", probe], cwd=tmp_path, capture_output=True, text=True, env=env)
    assert out.stdout.split() == ["GB", "GBP", "no", "rate"], out.stderr
    env["PLATFORM_TAX_RATE"] = "0.05"
    env["PLATFORM_CURRENCY"] = "AED"
    out = subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0,'.')\nfrom app import money_settings as m\nprint(m.currency(), m.tax_rate())"],
        cwd=tmp_path, capture_output=True, text=True, env=env,
    )
    assert out.stdout.split() == ["AED", "0.05"], out.stderr


def test_no_currency_country_or_rate_value_lives_in_the_module_or_its_renders():
    """The values come only from the user's typed fields at run time."""
    sources = [
        Path(money_contract.__file__).read_text(encoding="utf-8"),
        render_money_settings(),
    ]
    for src in sources:
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert not (len(node.value) == 3 and node.value.isupper() and node.value.isalpha()), node.value
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                assert node.value in (0.0, 1.0), node.value


def test_the_brief_states_what_to_build_with_and_without_a_locale():
    with_pair = "\n".join(money_brief_lines({"locale": {"country": "GB", "currency": "GBP"}}))
    assert "country GB and currency GBP" in with_pair
    assert "app.money_settings" in with_pair
    without = "\n".join(money_brief_lines({}))
    assert "WITHHELD (no currency declared)" in without


def test_the_emitted_model_declares_money_fields_from_the_declared_type():
    from app.factory.build.roles_handlers import _render_models

    src = _render_models({"zorblat_invoice": {"entity": "zorblat_invoice", "fields": [
        {"name": "net_amount", "type": "money"}, {"name": "memo", "type": "str"},
    ]}})
    assert "MONEY_FIELDS = ['net_amount']" in src


def test_the_floor_choice_rides_onto_the_stored_blueprint():
    from types import SimpleNamespace

    from app.factory.locale_choice import apply_locale_choice

    pd = SimpleNamespace(country=None, currency=None, blueprint={"product_id": "zorblat"})
    apply_locale_choice(pd, "gb", "gbp")
    assert pd.blueprint["locale"] == {"country": "GB", "currency": "GBP"}
    apply_locale_choice(pd, "", None)
    assert pd.blueprint["locale"] is None
