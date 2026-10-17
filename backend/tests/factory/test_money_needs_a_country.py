"""A platform that handles money is built for the user's country and currency.

FinOps (sess_065fc3eac75c4f62) was a finance platform for a Dubai business.
The brief never named a country, the Floor chat never asked, and the coding
agent hardcoded UK VAT (``STANDARD_VAT_RATE = 0.2``) and GBP -- so every
net/VAT split was wrong for the UAE (5%, AED). Two layers now: the chat asks
where they operate and in what currency, and the writer never guesses when
the brief still does not say.
"""

from __future__ import annotations

from app.factory.build.writer_prompt import PROMPT_VERSION, render_writer_prompt
from app.factory.platform_chat_llm import _SYSTEM


class _Blueprint:
    product_id = "finops"
    product_name = "FinOps Central"
    vertical = "finops"
    summary = "finance operations"


def _writer() -> str:
    return render_writer_prompt(_Blueprint(), brief="a finance platform")


def test_the_chat_asks_where_they_operate_and_in_what_currency():
    ask = _SYSTEM[_SYSTEM.index("- ask_user:"): _SYSTEM.index("- draft_platform:")]
    assert "country" in ask and "currency" in ask
    assert "never assume them" in ask


def test_the_drafted_brief_carries_the_answer_or_says_it_is_unknown():
    draft = _SYSTEM[_SYSTEM.index("- draft_platform:"): _SYSTEM.index("- start_coder:")]
    assert "country and currency" in draft
    assert "never a guess" in draft


def test_the_writer_takes_money_rules_from_the_brief():
    text = _writer()
    assert "MONEY (country, currency, tax):" in text
    assert "come\n  from the BRIEF" in text or "from the BRIEF" in text


def test_the_writer_never_invents_a_country_the_brief_did_not_give():
    text = _writer()
    assert "Never hardcode a country's tax" in text
    assert "no default value" in text
    assert "README.md" in text


def test_the_prompt_version_records_the_money_change():
    # MONEY arrived in v6; later versions must keep it.
    assert int(PROMPT_VERSION.rsplit(".v", 1)[1]) >= 6
    assert _writer().startswith(f"<!-- {PROMPT_VERSION} -->")
