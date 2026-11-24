"""The grader's findings are written down and shown, never guessed.

Every grader verdict writes one row per finding (what, where, which rule, whose)
to the ledger and to the backend log with the session id. The build status
carries the rows and how many items the hand-back sent the writer. Nothing here
changes what the grader checks or decides.
"""

from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest

from app.factory.build import grader_findings
from app.factory.build.grader_findings import (
    FACTORY,
    ITEM_SHAPES,
    NOT_CHECKED,
    RUN,
    WRITER,
    finding_row,
    finding_rows,
)

WRITER_RUN = {"provider": "writerco", "model": "writer-model-1"}


@pytest.mark.parametrize(
    "item, rule, where",
    [
        ("function grade FAIL: zorblat_total", "function_check", "zorblat_total"),
        ("function grade FAIL: plinket_count", "function_check", "plinket_count"),
        ("UI step FAIL: open the list", "screen_walk", "open the list"),
        ("UI step FAIL: save a record", "screen_walk", "save a record"),
        ('re-run writer on: does zorblat move? -- grader FAIL: "constant"', "review_question", "does zorblat move?"),
        ("re-run writer on: is plinket read?", "review_question", "is plinket read?"),
        ("de-hardwire zorblat_total: CONSTANT", "no_fixed_answers", "zorblat_total"),
        ("remove baked literals from app/a.py: ['x']", "no_typed_in_values", "app/a.py"),
        ("remove copied brief from app/b.py: ['y']", "no_copied_brief", "app/b.py"),
        ("FACTORY owes baked literals in app/c.py (stamped): ['z']", "factory_file", "baked literals in app/c.py"),
        ("something the table has not seen", "grader_finding", "-"),
    ],
)
def test_each_item_shape_becomes_one_plain_row(item, rule, where):
    row = finding_row(item, WRITER)
    assert row["rule"] == rule
    assert row["where"] == where
    assert row["quote"] == item
    assert row["what"] and row["what"] != item


@pytest.mark.parametrize("owner, rule", [(RUN, "not_checked"), (WRITER, "grader_finding")])
def test_an_unshaped_item_the_grader_could_not_check_says_so(owner, rule):
    row = finding_row("Layer 2 did not run: no model", owner)
    assert row["rule"] == rule
    assert (row["what"] == NOT_CHECKED) is (owner == RUN)


@pytest.mark.parametrize("prefix", [shape[0] for shape in ITEM_SHAPES])
def test_every_shape_is_one_the_grader_writes(prefix):
    """Drift guard: a prefix the grader no longer writes fails here."""
    from app.factory.build import grader
    from app.factory.build.product_grader import grade_record

    source = inspect.getsource(grader) + inspect.getsource(grade_record)
    if prefix in source:
        return
    # The grader writes "remove {what} from {rel}" with what = "<phrase>".
    first, *middle, last = prefix.split()
    assert f'f"{first} {{what}} {last} ' in source
    assert f'"{" ".join(middle)}"' in source


@pytest.mark.parametrize("cap", ["zorblat_total", "plinket_count"])
def test_owner_comes_from_the_verdicts_own_lists(cap):
    from app.factory.build.grader import NOT_CERTIFIED, GraderVerdict

    mine = f"function grade FAIL: {cap}"
    owed = f"FACTORY owes baked literals in app/{cap}.py (stamped): ['v']"
    gap = "UI walk not graded: no browser"
    verdict = GraderVerdict(
        tier=NOT_CERTIFIED,
        items=(mine, owed, gap),
        ungraded=(gap,),
        writer_items=(mine,),
        factory_owed=(owed,),
    )
    rows = finding_rows(verdict)
    assert [r["owner"] for r in rows] == [WRITER, FACTORY, RUN]
    assert rows[0]["where"] == cap
    assert rows[2]["what"] == NOT_CHECKED


@pytest.fixture()
def smoke_blueprint():
    from app.factory.blueprint import load_blueprint

    return load_blueprint(
        Path(__file__).resolve().parents[3] / "blueprints" / "examples" / "runner_smoke.yaml"
    )


def _fake_pass(ctx):
    from app.factory.build.roles import RoleResult
    from app.factory.build.writer_prompt import record_writer_dispatch, record_writer_run

    record_writer_dispatch(ctx.state, supplied="", brief="invented brief", prompt="invented prompt")
    record_writer_run(ctx.state, **WRITER_RUN)
    return RoleResult(ok=True, detail="fake pass")


def _harness(monkeypatch):
    from app.factory.build import runner as runner_mod
    from app.factory.build.gates import GateResult
    from app.factory.build.roles import ROLE_IMPLEMENTATIONS

    monkeypatch.setattr(runner_mod, "gate_for", lambda role: (lambda ctx: GateResult(ok=True, gate="fake_gate")))
    return {role: _fake_pass for role in ROLE_IMPLEMENTATIONS}


@pytest.mark.parametrize("cap, session", [("zorblat_total", "sess_aaa111"), ("plinket_count", "sess_bbb222")])
def test_a_send_back_is_in_the_ledger_the_log_and_the_build_status(
    smoke_blueprint, tmp_path, monkeypatch, caplog, cap, session
):
    from app.factory.build import grader as grader_mod
    from app.factory.build.ledger import EventKind
    from app.factory.build.runner import RoleRunner
    from app.factory.build_jobs import build_status

    monkeypatch.delenv(grader_mod.DISABLE_ENV, raising=False)
    mine = f"function grade FAIL: {cap}"
    owed = f"FACTORY owes baked literals in app/{cap}.py (stamped): ['v']"

    def not_certified(**kwargs):
        return grader_mod.GraderVerdict(
            tier=grader_mod.NOT_CERTIFIED,
            items=(mine, owed),
            writer_items=(mine,),
            factory_owed=(owed,),
        )

    monkeypatch.setattr(grader_mod, "run_grader", not_certified)
    runner = RoleRunner(
        smoke_blueprint,
        tmp_path / "build",
        roles=_harness(monkeypatch),
        session_id=session,
        brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}',
    )
    with caplog.at_level(logging.INFO, logger=grader_findings.__name__):
        outcome = runner.run()
    assert not outcome.ok

    notes = [
        e for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("grader_tier") == grader_mod.NOT_CERTIFIED
    ]
    assert notes
    rows = notes[0].payload["grader_findings"]
    assert [(r["rule"], r["where"], r["owner"]) for r in rows] == [
        ("function_check", cap, WRITER),
        ("factory_file", f"baked literals in app/{cap}.py", FACTORY),
    ]

    lines = [r.getMessage() for r in caplog.records if r.name == grader_findings.__name__]
    assert f"grader verdict session={session} tier=NOT_CERTIFIED findings=2 writer_owned=1" in lines
    assert any(
        line.startswith(f"grader finding session={session} 1/2 rule=function_check where={cap} owner=writer what=")
        for line in lines
    )
    assert any(f"session={session} 2/2 rule=factory_file" in line and "owner=factory" in line for line in lines)
    # What went back is logged where it is decided: one hand-back, then the
    # spent budget stops the run and nothing more goes to the writer.
    sent = [line for line in lines if line.startswith(f"grader handback session={session} ")]
    assert sent == [f"grader handback session={session} to_writer=1 round=1/1"]
    assert lines[-1] == f"grader stop session={session} to_writer=0 reason=grader_not_certified"
    assert not any("to_writer=yes" in line for line in lines)

    view = build_status(runner.ledger.path.parent)["grader"]
    assert view["state"] == "stopped"
    assert [r["where"] for r in view["findings"]] == [cap, f"baked literals in app/{cap}.py"]
    assert [r["owner"] for r in view["findings"]] == [WRITER, FACTORY]
    assert view["sent_to_writer"] == 1
    assert view["headline"] == "The quality check did not pass the build. It found 2 problems:"


@pytest.mark.parametrize("message", ["grader exploded", "grader blew up"])
def test_a_grader_that_could_not_grade_is_logged_too(smoke_blueprint, tmp_path, monkeypatch, caplog, message):
    from app.factory.build import grader as grader_mod
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(grader_mod.DISABLE_ENV, raising=False)

    def crash(**kwargs):
        raise RuntimeError(message)

    monkeypatch.setattr(grader_mod, "run_grader", crash)
    runner = RoleRunner(
        smoke_blueprint,
        tmp_path / "build",
        roles=_harness(monkeypatch),
        session_id="sess_ccc333",
        brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}',
    )
    with caplog.at_level(logging.INFO, logger=grader_findings.__name__):
        outcome = runner.run()
    assert not outcome.ok
    lines = [r.getMessage() for r in caplog.records if r.name == grader_findings.__name__]
    assert "grader verdict session=sess_ccc333 tier=ERROR findings=1 writer_owned=0" in lines
    assert any(message in line and "owner=run" in line for line in lines)
    assert lines[-1] == "grader stop session=sess_ccc333 to_writer=0 reason=grader_error"

    # The card has the reason too: a crash is never an empty refusal.
    from app.factory.build_jobs import build_status

    view = build_status(runner.ledger.path.parent)["grader"]
    assert view["state"] == "stopped"
    assert [r["owner"] for r in view["findings"]] == [RUN]
    assert message in view["findings"][0]["quote"]
    assert view["headline"] == "The quality check could not finish, so it cannot pass the build:"


@pytest.mark.parametrize("cap, session", [("zorblat_total", "sess_ddd444"), ("plinket_count", "sess_eee555")])
def test_a_factory_fault_refusal_keeps_its_findings_on_the_card(
    smoke_blueprint, tmp_path, monkeypatch, caplog, cap, session
):
    """Only Factory-owed items: the run ends naming the Factory, and the card
    still shows what the grader found (the fault note carries no rows of its
    own and must not wipe the verdict's)."""
    from app.factory.build import grader as grader_mod
    from app.factory.build.runner import RoleRunner
    from app.factory.build_jobs import build_status

    monkeypatch.delenv(grader_mod.DISABLE_ENV, raising=False)
    owed = f"FACTORY owes baked literals in app/{cap}.py (stamped): ['v']"

    def factory_only(**kwargs):
        return grader_mod.GraderVerdict(tier=grader_mod.NOT_CERTIFIED, items=(owed,), factory_owed=(owed,))

    monkeypatch.setattr(grader_mod, "run_grader", factory_only)
    runner = RoleRunner(
        smoke_blueprint,
        tmp_path / "build",
        roles=_harness(monkeypatch),
        session_id=session,
        brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}',
    )
    with caplog.at_level(logging.INFO, logger=grader_findings.__name__):
        outcome = runner.run()
    assert not outcome.ok
    view = build_status(runner.ledger.path.parent)["grader"]
    assert view["state"] == "stopped"
    assert [(r["where"], r["owner"]) for r in view["findings"]] == [(f"baked literals in app/{cap}.py", FACTORY)]
    assert view["headline"] == "The quality check did not pass the build. It found 1 problem:"
    lines = [r.getMessage() for r in caplog.records if r.name == grader_findings.__name__]
    assert lines[-1] == f"grader stop session={session} to_writer=0 reason=grader_factory_fault"
    assert not any(line.startswith("grader handback") for line in lines)


@pytest.mark.parametrize(
    "follow, wheres",
    [
        ({}, ["zorblat_total"]),
        ({"grader_items": []}, []),
    ],
)
def test_an_event_with_no_verdict_of_its_own_keeps_the_rows_before_it(tmp_path, follow, wheres):
    from app.factory.build.grader import GRADER_GATE, NOT_CERTIFIED
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import _grader_view

    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="product", inputs_hash="h")
    base = {"grader_gate": GRADER_GATE, "grader_tier": NOT_CERTIFIED}
    first = [finding_row("function grade FAIL: zorblat_total", WRITER)]
    ledger.append(EventKind.NOTE, role="WRITER", detail="v", payload={**base, "grader_findings": first})
    ledger.append(EventKind.NOTE, role="WRITER", detail="next", payload={**base, **follow})
    view = _grader_view(list(ledger.events()))
    assert [r["where"] for r in view["findings"]] == wheres


@pytest.mark.parametrize(
    "payload, wheres",
    [
        ({"grader_items": ["function grade FAIL: zorblat_total"]}, ["zorblat_total"]),
        ({"grader_items": ["UI step FAIL: open the list", "de-hardwire plinket_count: CONSTANT"]},
         ["open the list", "plinket_count"]),
    ],
)
def test_a_verdict_recorded_before_rows_is_read_from_its_items(tmp_path, payload, wheres):
    from app.factory.build.grader import GRADER_GATE, NOT_CERTIFIED
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import _grader_view

    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="product", inputs_hash="h")
    ledger.append(
        EventKind.NOTE,
        role="WRITER",
        detail="old",
        payload={"grader_gate": GRADER_GATE, "grader_tier": NOT_CERTIFIED, **payload},
    )
    view = _grader_view(list(ledger.events()))
    assert [r["where"] for r in view["findings"]] == wheres
    assert all(r["owner"] == "" for r in view["findings"])


_ROW = {"rule": "function_check", "what": "w", "where": "x", "owner": WRITER, "quote": "q"}


@pytest.mark.parametrize(
    "state, rows, sent, expected",
    [
        ("certified", [], 0, ""),
        ("certified", [_ROW], 0, ""),
        ("refused", [], 0, ""),
        ("sent_back", [_ROW], 1, "The quality check found 1 problem and sent it back to the writer to fix:"),
        ("sent_back", [_ROW, _ROW], 2, "The quality check found 2 problems and sent them back to the writer to fix:"),
        ("sent_back", [_ROW, _ROW, _ROW], 1, "The quality check found 3 problems and sent 1 of them back to the writer to fix:"),
        ("stopped", [_ROW], 1, "The quality check did not pass the build. It found 1 problem:"),
        ("refused", [_ROW, _ROW], 0, "The quality check did not pass the build. It found 2 problems:"),
    ],
)
def test_the_headline_is_read_from_the_view_state(state, rows, sent, expected):
    assert grader_findings.headline(state, rows, sent) == expected


@pytest.mark.parametrize(
    "tier, expected",
    [
        ("ERROR", "The quality check could not finish, so it cannot pass the build:"),
        ("NOT_CERTIFIED", "The quality check did not pass the build. It found 1 problem:"),
    ],
)
def test_a_grader_that_could_not_finish_says_so_in_the_headline(tier, expected):
    from app.factory.build import grader

    assert grader_findings.ERROR_TIER == grader.ERROR
    assert grader_findings.headline("stopped", [_ROW], 0, tier) == expected


@pytest.mark.parametrize("secret", ["OPENROUTER_API_KEY=planted-one", "DEEPSEEK_API_KEY=planted-two"])
def test_a_secret_in_the_graders_words_never_reaches_the_log(secret):
    line = grader_findings.log_line(finding_row(f"grader crashed: RuntimeError: {secret}", RUN))
    assert secret.split("=")[1] not in line
    assert "[redacted]" in line


@pytest.mark.parametrize("secret", ["OPENROUTER_API_KEY=planted-three", "DEEPSEEK_API_KEY=planted-four"])
def test_a_secret_in_the_place_never_reaches_the_log(secret):
    row = finding_row(f"function grade FAIL: {secret}", WRITER)
    assert secret in row["where"]
    line = grader_findings.log_line(row)
    assert secret.split("=")[1] not in line
    assert line.count("[redacted]") == 2


@pytest.mark.parametrize(
    "item, place",
    [
        ("function grade FAIL: zorblat_total", "zorblat total"),
        ("de-hardwire plinket-count: CONSTANT", "plinket count"),
        ("UI step FAIL: open the list", "open the list"),
        ("UI step FAIL: route:/v1/zorblat_ledger", "the zorblat ledger feature"),
        ("UI step FAIL: honest_error:/v1/plinket-sheet", "the error message of the plinket sheet feature"),
        ("UI step FAIL: screen:zorblat_board", "the zorblat board screen"),
        ("UI step FAIL: load:/", "opening the app"),
        ("UI step FAIL: attach:pdf", "attaching a pdf file"),
        ("UI step FAIL: newkind:/v1/plinket_list", "plinket list"),
        ("UI step FAIL: route:/", ""),
        ("UI step FAIL: newkind:/", "newkind"),
        ("re-run writer on: is plinket read?", "is plinket read?"),
        ("remove baked literals from app/a.py: ['x']", ""),
        ("FACTORY owes copied brief in app/c.py (stamped): ['z']", ""),
        ("something the table has not seen", ""),
    ],
)
def test_the_card_place_never_carries_a_file_path_or_a_raw_id(item, place):
    row = finding_row(item, WRITER)
    assert row["place"] == place
    assert "/" not in row["place"] and "_" not in row["place"] and ":" not in row["place"]


@pytest.mark.parametrize("kind", ["route", "screen"])
def test_every_step_kind_on_the_card_is_one_the_ui_grader_writes(kind):
    """Drift guard: the step kinds the card names are the UI grader's own."""
    from app.factory.build.product_grader import ui_grader

    source = inspect.getsource(ui_grader)
    assert kind in grader_findings.STEP_WORDS
    for known in grader_findings.STEP_WORDS:
        assert '"' + known in source


@pytest.mark.parametrize("step, words", [("screen", "screen"), ("load", "load")])
def test_a_step_id_with_no_target_reads_as_its_own_words(step, words):
    """A step id with no ``kind:`` separator is named by its own words. It is
    never framed as a step kind with an empty target (which reads as nothing
    for ``screen``) and never borrows the kind's frame (``opening the app``)."""
    assert grader_findings.step_place(step) == words


@pytest.mark.parametrize(
    "found, rows, sent, expected",
    [
        (45, 40, 45, "The quality check found 45 problems and sent them back to the writer to fix. "
                     "The first 40 are listed:"),
        (45, 40, 3, "The quality check found 45 problems and sent 3 of them back to the writer to fix. "
                    "The first 40 are listed:"),
        (2, 2, 2, "The quality check found 2 problems and sent them back to the writer to fix:"),
        (0, 2, 2, "The quality check found 2 problems and sent them back to the writer to fix:"),
    ],
)
def test_a_shortened_list_counts_the_verdict_not_the_rows(found, rows, sent, expected):
    assert grader_findings.headline("sent_back", [_ROW] * rows, sent, "NOT_CERTIFIED", found) == expected


@pytest.mark.parametrize(
    "found, rows, expected",
    [
        (41, 40, "The quality check did not pass the build. It found 41 problems. The first 40 are listed:"),
        (1, 1, "The quality check did not pass the build. It found 1 problem:"),
    ],
)
def test_a_shortened_refusal_counts_the_verdict_not_the_rows(found, rows, expected):
    assert grader_findings.headline("refused", [_ROW] * rows, 0, "NOT_CERTIFIED", found) == expected


@pytest.mark.parametrize(
    "follow, found",
    [
        ({}, 45),
        ({"grader_items": ["function grade FAIL: zorblat_total"]}, 1),
        ({"grader_findings": [finding_row("function grade FAIL: zorblat_total", WRITER)]}, 1),
    ],
)
def test_the_view_counts_every_item_of_the_standing_verdict(tmp_path, follow, found):
    from app.factory.build.grader import GRADER_GATE, NOT_CERTIFIED
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import _grader_view

    items = [f"function grade FAIL: zorblat_{i}" for i in range(45)]
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="product", inputs_hash="h")
    base = {"grader_gate": GRADER_GATE, "grader_tier": NOT_CERTIFIED}
    rows = [finding_row(i, WRITER) for i in items][: grader_findings.MAX_ROWS]
    ledger.append(EventKind.NOTE, role="WRITER", detail="v",
                  payload={**base, "grader_items": items, "grader_findings": rows})
    ledger.append(EventKind.NOTE, role="WRITER", detail="next", payload={**base, **follow})
    view = _grader_view(list(ledger.events()))
    assert view["found"] == found


def test_a_hand_back_of_more_items_than_rows_says_the_true_count(tmp_path):
    from app.factory.build.grader import GRADER_GATE, NOT_CERTIFIED
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import _grader_view

    items = [f"function grade FAIL: zorblat_{i}" for i in range(45)]
    ledger = BuildLedger(tmp_path / "build_ledger.jsonl")
    ledger.start_run(product_id="product", inputs_hash="h")
    ledger.append(EventKind.NOTE, role="WRITER", detail="v", payload={
        "grader_gate": GRADER_GATE, "grader_tier": NOT_CERTIFIED, "grader_items": items,
        "grader_findings": [finding_row(i, WRITER) for i in items][: grader_findings.MAX_ROWS]})
    ledger.append(EventKind.REWORK, role="WRITER", detail="h", payload={
        "gate": GRADER_GATE, "work_list": items, "round": 1, "grader_tier": NOT_CERTIFIED})
    view = _grader_view(list(ledger.events()))
    assert len(view["findings"]) == grader_findings.MAX_ROWS == 40
    assert view["headline"] == (
        "The quality check found 45 problems and sent them back to the writer to fix. The first 40 are listed:"
    )
