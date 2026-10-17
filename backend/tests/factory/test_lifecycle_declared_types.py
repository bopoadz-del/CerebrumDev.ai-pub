"""The Factory-emitted lifecycle suite writes values the product DECLARED.

Live 2026-10-07 (9d382ae7, co-op repro, build/plt_2f85a61278494b64): the
product's MemberDuesTracking model declares ``dues_amount`` and
``amount_paid`` with CONSTRAINTS type ``"money"`` (stored as float). The
lifecycle sampler recognised only the literal spellings int/float/bool, so a
``money`` field fell through to the text placeholder and the emitted suite
inserted ``'s10-row'`` into a numeric column:
``ValueError: could not convert string to float: 's10-row'`` in four lifecycle
tests -> SAME_FAILURE_TWICE, the writer's rework spent on a Factory test it may
not edit. The column type had the same blind spot (``money`` -> TEXT).

One type resolver decides both the sample and the column; a sample whose
value does not fit its declared type is refused at RENDER time as a Factory
fault, never handed to the writer.
"""

from __future__ import annotations

import sqlite3

import pytest

from app.factory.build.data_lifecycle import (
    EmittedSuiteContractError,
    check_sample_fits_declared_types,
    first_entity_sample,
    render_product_tests,
    sample_for_spec,
    _field_sa_type,
)

#: The live co-op shape, as declared_specs reads it: money, date, datetime,
#: email and vocabulary fields beside plain text.
DUES_SPEC = {
    "member_dues_tracking": {
        "entity": "member_dues_tracking",
        "fields": [
            {"name": "reference", "type": "str", "required": True},
            {"name": "status", "type": "str", "allowed_values": ["open", "in_progress", "closed"]},
            {"name": "member_id", "type": "str", "required": True},
            {"name": "email", "type": "email", "format": "email"},
            {"name": "due_date", "type": "date", "format": "date"},
            {"name": "dues_amount", "type": "money", "min": 0.0},
            {"name": "amount_paid", "type": "money", "min": 0.0},
            {"name": "paid_at", "type": "datetime", "format": "datetime"},
            {"name": "note", "type": "str"},
        ],
    }
}


def _numeric(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def test_a_money_field_gets_a_number_never_the_text_marker():
    sample = sample_for_spec(DUES_SPEC["member_dues_tracking"])
    assert _numeric(sample["dues_amount"]), sample
    assert _numeric(sample["amount_paid"]), sample
    assert sample["dues_amount"] >= 0.0


def test_every_sample_value_fits_its_declared_type():
    entity, sample = first_entity_sample(DUES_SPEC)
    assert entity == "member_dues_tracking"
    check_sample_fits_declared_types(entity, sample, DUES_SPEC[entity])  # no raise
    # Plain undeclared text keeps the marker; formatted text gets a value of
    # its declared format (one shared sampler).
    assert sample["note"] == "s10-row"
    assert "@" in sample["email"]
    assert sample["due_date"][:4].isdigit()


def test_a_money_column_is_numeric_so_the_value_reads_back_equal():
    money = {"name": "dues_amount", "type": "money"}
    assert _field_sa_type(money) != "sa.Text()"
    # The live reason it matters: SQLite TEXT affinity turns 1.0 into '1.0',
    # so a numeric sample in a TEXT column would never read back equal.
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (1.0,))
    assert conn.execute("SELECT v FROM t").fetchone()[0] == "1.0"


def test_a_sample_that_does_not_fit_its_declared_type_is_refused_at_render():
    spec = DUES_SPEC["member_dues_tracking"]
    with pytest.raises(EmittedSuiteContractError):
        check_sample_fits_declared_types(
            "member_dues_tracking", {"dues_amount": "s10-row"}, spec
        )


def test_the_emitted_suite_renders_numbers_for_money():
    src = render_product_tests(DUES_SPEC)
    sample_line = next(line for line in src.splitlines() if line.startswith("SAMPLE = "))
    assert "'dues_amount': 's10-row'" not in sample_line
    assert "'amount_paid': 's10-row'" not in sample_line
