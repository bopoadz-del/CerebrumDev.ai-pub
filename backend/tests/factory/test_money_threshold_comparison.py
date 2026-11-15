"""A dimensionless factor whose product is only compared is a threshold, not
money scaling (owner ruling 00:25Z).

The money contract refuses a money value scaled by a factor not read from
``app.money_settings``. But ``alert_ratio * cap_amount > balance`` is a
threshold comparison: the scaled amount is COMPARED (feeds a boolean, an
alert) and is never stored, returned or summed as money, so it is not money
scaling and the gate must not refuse it. The exemption is the product's USE,
never the factor's name: the same ``alert_ratio`` stored, returned or summed
as money stays red, and a literal rate stays red even inside a comparison.

Everything here is invented: the ZorblatCap product, its fields and its locale.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build.money_contract import (
    DECLARED_LOCALE_REL,
    MONEY_SETTINGS_REL,
    declared_locale,
    money_verdict,
    render_declared_locale,
    render_money_settings,
)

MODELS = '''
class ZorblatCap:
    FIELDS = ["cap_amount", "balance", "memo"]
    CONSTRAINTS = {}
    MONEY_FIELDS = ["cap_amount", "balance"]
'''

AE = declared_locale("ae", "aed")


def _product(root: Path, action: str) -> Path:
    for rel, body in {"app/models.py": MODELS, "app/actions/zorblat_cap.py": action}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (root / DECLARED_LOCALE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / DECLARED_LOCALE_REL).write_text(render_declared_locale(AE), encoding="utf-8")
    (root / MONEY_SETTINGS_REL).write_text(render_money_settings(), encoding="utf-8")
    return root


@pytest.mark.parametrize("body", [
    "def handle(payload, alert_ratio):\n    return payload['cap_amount'] * alert_ratio > payload['balance']\n",
    "def handle(payload, alert_ratio):\n    return payload['balance'] <= payload['cap_amount'] / alert_ratio\n",
])
def test_a_dimensionless_factor_whose_product_is_only_compared_passes(tmp_path, body):
    verdict = money_verdict(_product(tmp_path, body))
    assert verdict.status == "PASS", verdict.findings


@pytest.mark.parametrize("body", [
    "def handle(payload, alert_ratio):\n    threshold = payload['cap_amount'] * alert_ratio\n    return {'ok': True, 'threshold': threshold}\n",
    "def handle(payload, fx_rate):\n    return payload['cap_amount'] * fx_rate\n",
])
def test_a_factor_whose_product_is_stored_or_returned_still_fails(tmp_path, body):
    verdict = money_verdict(_product(tmp_path, body))
    assert verdict.status == "FAIL"
    assert verdict.findings


@pytest.mark.parametrize("body", [
    "def handle(payload):\n    return payload['cap_amount'] * 0.2 > payload['balance']\n",
    "def handle(payload):\n    return payload['cap_amount'] / 3 <= payload['balance']\n",
])
def test_a_literal_rate_stays_refused_even_when_only_compared(tmp_path, body):
    verdict = money_verdict(_product(tmp_path, body))
    assert verdict.status == "FAIL"
    assert any("literal" in f for f in verdict.findings)
