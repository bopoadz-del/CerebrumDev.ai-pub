"""FG-4 on the FG-3 replay harness: the hardwiring rule by provenance.

The forbidden set comes from what the user supplied (identities, quoted
values, documents), never from capitalisation or bare numbers, and never
from a value the Factory defaulted or the contract requires. The prompt-leak
check compares against the prompt the writer was actually given, recorded
at dispatch; a missing record is an error about the run.

Every case is paired: the same harness tree, a writer file planted with
ordinary words (not a finding) or with what the user supplied (a finding).
Client values are minted at test time; every other value is read from the
composed tree, its blueprint or the recorded dispatch.
"""

from __future__ import annotations

import uuid

import pytest

from tests.factory import grader_replay as gr
from tests.factory.test_grader_replay_clean_tree import client_brief, composed, tree  # noqa: F401

PLANTED = "app/writer_planted_values.py"


def _rows(replay, kind):
    hard = replay.grade.get("hardwiring") or {}
    return {f["file"]: f for f in hard.get("findings") or [] if f.get("kind") == kind}


def _plant(case, values):
    case.write_writer_file(PLANTED, "".join(f"V{i} = {v!r}\n" for i, v in enumerate(values)))


def _blueprint(case):
    from app.factory.blueprint import load_blueprint

    return load_blueprint(case.blueprint_path)


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_ordinary_words_numbers_defaults_and_contract_ids_are_not_findings(tree, monkeypatch):  # noqa: F811
    bp = _blueprint(tree)
    number = str(len(tree.capability_ids))
    cid = tree.capability_ids[0]
    # The user's words carry a sentence-initial capitalised word, a bare
    # small number, the product's own (Factory-assigned) vertical and a
    # contract-required capability id -- none of them is a client value.
    said = f"Tracks {number} entries for the {bp.vertical} team through {cid}."
    _plant(tree, ["Tracks", number, number + "0", bp.vertical, cid])
    replay = gr.grade(tree, monkeypatch, supplied=client_brief(tree, said))
    assert PLANTED not in _rows(replay, "forbidden_literal"), _rows(replay, "forbidden_literal")
    assert PLANTED not in _rows(replay, "prompt_leak"), _rows(replay, "prompt_leak")
    assert not any(PLANTED in item for item in tuple(getattr(replay.verdict, "writer_items", ()) or ()))


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_a_client_identity_and_a_quoted_client_value_are_findings(tree, monkeypatch):  # noqa: F811
    from app.factory.build.factory_receipt import WRITER

    quoted = f"wing {uuid.uuid4().hex[:6]} annex"
    supplied = client_brief(tree, f'The site is "{quoted}".')
    identity = next(t.strip(".") for t in supplied.split() if t.startswith("acct_"))
    _plant(tree, [identity, f"Site: {quoted}"])
    replay = gr.grade(tree, monkeypatch, supplied=supplied)
    row = _rows(replay, "forbidden_literal").get(PLANTED)
    assert row is not None and set(row["literals"]) == {identity, quoted}, row
    assert row["owner"] == WRITER
    assert not replay.certified
    assert any(PLANTED in item for item in replay.verdict.writer_items), replay.verdict.writer_items


def _a_factory_clause(case):
    """A multi-word clause of the Factory's own prompt text, read from the
    dispatch the harness records the production way."""
    from app.factory.build.grader import _factory_part
    from app.factory.build.product_grader.scan_generated import _clauses

    runner = gr._runner(case.blueprint_path, case.root, case.store)
    told = gr.record_dispatch(runner, "")
    clauses = _clauses(_factory_part(told["prompt"], told["brief"], told["supplied"]))
    assert clauses, "the rendered writer prompt has no Factory clause"
    return clauses[0]


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_the_leak_check_fires_on_a_copied_brief_span_and_not_on_factory_text(tree, monkeypatch):  # noqa: F811
    from app.factory.build.factory_receipt import WRITER

    factory_clause = _a_factory_clause(tree)
    copied = f"remind the owner {uuid.uuid4().hex[:6]} days ahead"
    # The user's brief echoes a Factory clause and adds its own; the writer
    # copies both into values.
    supplied = client_brief(tree, f"{copied}.", f"{factory_clause}.")
    _plant(tree, [copied, factory_clause])
    replay = gr.grade(tree, monkeypatch, supplied=supplied)
    row = _rows(replay, "prompt_leak").get(PLANTED)
    assert row is not None and row["literals"] == [copied], _rows(replay, "prompt_leak")
    assert row["owner"] == WRITER
    assert any(f"remove copied brief from {PLANTED}" in item for item in replay.verdict.writer_items)


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_a_missing_dispatch_record_is_a_run_error_not_a_fallback(tree, monkeypatch):  # noqa: F811
    from app.factory.build.grader import ERROR

    replay = gr.grade(tree, monkeypatch, dispatched=False)
    assert replay.verdict is not None and replay.verdict.tier == ERROR
    assert any("no writer dispatch was recorded" in item for item in replay.verdict.items)
    assert [n for n in replay.notes if n["payload"].get("grader_tier") == ERROR]
