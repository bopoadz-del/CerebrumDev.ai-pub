"""The money rule has one source: what the writer is told is what the gate checks.

Line audit L-01 (22 Nov): one typed country/currency choice reached the writer
as texts that disagreed. The writer prompt said "when the brief names them,
use exactly those" -- an invitation to write the declared code into the
source -- while the C-BRIEF said to read it from app.money_settings, and the
writer_contract gate refused any literal equal to the declared code. A writer
that obeyed the prompt was refused by the gate.

Now money_contract holds ONE rule table (MONEY_RULES). The prompt's MONEY
section, the C-BRIEF MONEY lines, the acceptance-floor line and every finding
the gate hands a rework round are rendered from it. These tests fail when any
of the three texts is written by hand again. The codes below are synthetic,
shape-valid values, not any real country or currency.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build.brief_compiler import writer_gate_acceptance_lines
from app.factory.build.money_contract import (
    DECLARED_LOCALE_REL,
    MONEY_RULES,
    MONEY_SETTINGS_REL,
    RULE_CODE_LITERAL,
    RULE_RATE_LITERAL,
    RULE_UNSOURCED_FACTOR,
    declared_locale,
    money_acceptance_text,
    money_brief_lines,
    money_finding,
    money_prompt_section,
    money_rule,
    money_verdict,
    render_declared_locale,
    render_money_settings,
)
from app.factory.build.writer_prompt import render_writer_prompt

COUNTRY, CURRENCY = "QX", "QXZ"

MODELS = '''
class ZorblatLedger:
    FIELDS = ["net_amount", "units"]
    CONSTRAINTS = {}
    MONEY_FIELDS = ["net_amount"]
'''

#: One module that breaks each rule the gate checks, once.
BREAKS_EVERY_RULE = f'''
def handle(payload):
    region = "{COUNTRY}"
    taxed = payload["net_amount"] * 0.2
    spread = payload["net_amount"] * payload["units"]
    return {{"ok": True, "region": region, "taxed": taxed, "spread": spread}}
'''

#: The same module written to the rule: every code and factor from the settings.
OBEYS_EVERY_RULE = '''
from app import money_settings


def handle(payload):
    region = money_settings.country()
    taxed = payload["net_amount"] * money_settings.tax_rate()
    fee = payload["net_amount"] * money_settings.rate("zorblat_fee")
    return {"ok": True, "region": region, "taxed": taxed, "fee": fee}
'''


class _Blueprint:
    product_id = "zorblat"
    product_name = "Zorblat Ledger"
    vertical = "zorblat"
    summary = "a ledger"
    locale = {"country": COUNTRY, "currency": CURRENCY}


def _product(root: Path, body: str) -> Path:
    for rel, text in {"app/__init__.py": "", "app/models.py": MODELS, "app/actions/ledger.py": body}.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / DECLARED_LOCALE_REL).parent.mkdir(parents=True, exist_ok=True)
    (root / DECLARED_LOCALE_REL).write_text(
        render_declared_locale(declared_locale(COUNTRY, CURRENCY)), encoding="utf-8"
    )
    (root / MONEY_SETTINGS_REL).write_text(render_money_settings(), encoding="utf-8")
    return root


def _prompt(**kw) -> str:
    return render_writer_prompt(_Blueprint(), brief="a ledger", **kw)


def _prompt_money_section(text: str) -> str:
    start = text.index("MONEY (country, currency, tax):")
    end = text.index("\n\n", start)
    return text[start : end + 1]


def _rule_of(finding: str) -> str:
    """The table key whose sentence closes this finding ('' if none does)."""
    for rule in MONEY_RULES:
        if finding.endswith(" -- " + rule.rule):
            return rule.key
    return ""


# -- the three texts render from the table -------------------------------------


def test_the_prompt_money_section_is_the_table_rendering_and_nothing_else():
    assert _prompt_money_section(_prompt()) == money_prompt_section()


def test_every_rule_reaches_the_prompt_the_brief_and_the_acceptance_line():
    prompt = _prompt()
    with_pair = money_brief_lines(_Blueprint())
    without = money_brief_lines({})
    acceptance = "\n".join(writer_gate_acceptance_lines())
    for rule in MONEY_RULES:
        assert f"- {rule.rule}" in prompt, rule.key
        assert f"- MONEY: {rule.rule}" in with_pair, rule.key
        assert f"- MONEY: {rule.rule}" in without, rule.key
        assert rule.short in acceptance, rule.key
    assert money_acceptance_text() in acceptance


def test_every_gate_finding_carries_the_sentence_the_writer_was_told(tmp_path):
    verdict = money_verdict(_product(tmp_path, BREAKS_EVERY_RULE))
    assert verdict.status == "FAIL"
    keys = [_rule_of(f) for f in verdict.findings]
    assert sorted(keys) == sorted([RULE_CODE_LITERAL, RULE_RATE_LITERAL, RULE_UNSOURCED_FACTOR]), verdict.findings
    prompt = _prompt()
    for finding in verdict.findings:
        assert money_rule(_rule_of(finding)) in prompt


def test_the_fix_round_repeats_the_prompt_rule_word_for_word(tmp_path):
    verdict = money_verdict(_product(tmp_path, BREAKS_EVERY_RULE))
    rework = _prompt(findings=verdict.findings)
    for finding in verdict.findings:
        sentence = money_rule(_rule_of(finding))
        # once in the MONEY section, once in the REWORK FINDINGS row
        assert rework.count(sentence) == 2, sentence


def test_a_finding_is_where_what_and_the_rule():
    finding = money_finding(RULE_CODE_LITERAL, "app/x.py:3", "the declared code 'QX' is a literal")
    assert finding == "app/x.py:3: the declared code 'QX' is a literal -- " + money_rule(RULE_CODE_LITERAL)


# -- negative cases ------------------------------------------------------------


def test_the_prompt_never_invites_the_declared_value_into_the_source():
    section = money_prompt_section()
    assert "use exactly those" not in section
    assert COUNTRY not in _prompt_money_section(_prompt())
    assert CURRENCY not in _prompt_money_section(_prompt())


def test_the_prompt_says_the_typed_lines_override_the_customer_words():
    assert "override any country, currency or rate named in the customer's own words" in money_prompt_section()


def test_code_written_to_the_rule_passes_the_gate(tmp_path):
    verdict = money_verdict(_product(tmp_path, OBEYS_EVERY_RULE))
    assert verdict.status == "PASS", verdict.findings


def test_an_unknown_rule_is_an_error_not_a_blank_sentence():
    with pytest.raises(KeyError):
        money_rule("no_such_rule")


def test_the_rule_table_names_no_value():
    text = money_prompt_section() + "\n".join(r.rule + r.short for r in MONEY_RULES)
    assert COUNTRY not in text and CURRENCY not in text
    keys = [r.key for r in MONEY_RULES]
    assert len(keys) == len(set(keys))
