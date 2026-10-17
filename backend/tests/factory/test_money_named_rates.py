"""Every rate the brief sends to app.money_settings is one it can serve.

Live, release cycle 8 (fintech repeat pick, sess_3436e978c9e543b7): the WRITER
gate failed ``app/dispatch.py:357: a money value is scaled by a factor not
taken from app.money_settings``. The brief tells the writer to take "every
rate (tax, fx, discount)" from app.money_settings and to scale an amount only
by a factor read from it -- but the module the Factory stamps over the
writer's copy before every pass served only country(), currency() and
tax_rate(). A fee or an fx rate, the core of a money-transfer platform, had
no legal source: a rule the writer could not satisfy. And the rework line
named only file:line, not the expression it judged.

Now the settings module serves any named rate the operator sets
(``PLATFORM_RATE_<NAME>``), the brief says so, and the finding quotes the code.
The check itself is unchanged: a factor not read from the settings is still
red. The capability, field and rate names below are made up.
"""

from __future__ import annotations

import subprocess
import sys
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
class ZorblatTransfer:
    FIELDS = ["send_amount", "units"]
    CONSTRAINTS = {}
    MONEY_FIELDS = ["send_amount"]
'''

NAMED_RATE = '''
from app import money_settings


def handle(payload):
    fee = payload["send_amount"] * money_settings.rate("zorblat_fee")
    return {"ok": True, "fee": fee}
'''

UNSOURCED_FACTOR = '''
def handle(payload):
    units = int(payload["units"])
    return {"ok": True, "total": payload["send_amount"] * units}
'''


def _product(root: Path, **files: str) -> Path:
    for rel, body in {"app/__init__.py": "", "app/models.py": MODELS, **files}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    (root / DECLARED_LOCALE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / DECLARED_LOCALE_REL).write_text(render_declared_locale(declared_locale("ae", "aed")), encoding="utf-8")
    (root / MONEY_SETTINGS_REL).write_text(render_money_settings(), encoding="utf-8")
    return root


def _call(root: Path, expr: str, env: dict) -> str:
    code = (
        "import sys; sys.path.insert(0, '.')\n"
        "from app import money_settings as m\n"
        "try:\n"
        f"    print({expr})\n"
        "except m.MoneySettingMissing as exc:\n"
        "    print('missing:', exc)\n"
    )
    import os

    full = {k: v for k, v in os.environ.items() if not k.startswith("PLATFORM_")}
    full.update(env)
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(root), env=full, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_the_settings_module_serves_a_named_rate_the_operator_sets(tmp_path):
    root = _product(tmp_path)
    assert _call(root, "m.rate('zorblat_fee')", {"PLATFORM_RATE_ZORBLAT_FEE": "0.015"}) == "0.015"


def test_an_unset_named_rate_refuses_never_guesses(tmp_path):
    root = _product(tmp_path)
    out = _call(root, "m.rate('zorblat_fee')", {})
    assert out.startswith("missing:") and "PLATFORM_RATE_ZORBLAT_FEE" in out


def test_a_money_path_scaled_by_a_named_rate_passes(tmp_path):
    verdict = money_verdict(_product(tmp_path, **{"app/actions/zorblat_transfer.py": NAMED_RATE}))
    assert verdict.status == "PASS", verdict.findings


def test_an_unsourced_factor_is_still_red_and_the_finding_quotes_the_code(tmp_path):
    verdict = money_verdict(_product(tmp_path, **{"app/actions/zorblat_transfer.py": UNSOURCED_FACTOR}))
    assert verdict.status == "FAIL"
    assert len(verdict.findings) == 1, verdict.findings
    finding = verdict.findings[0]
    assert finding.startswith("app/actions/zorblat_transfer.py:4: a money value is scaled by a factor not taken")
    assert "payload['send_amount'] * units" in finding
    assert "rate('<name>')" in finding


def test_the_brief_names_the_named_rate_accessor():
    lines = "\n".join(money_brief_lines({"locale": {"country": "AE", "currency": "AED"}}))
    assert "rate('<name>')" in lines and "PLATFORM_RATE_<NAME>" in lines
