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
    NOT_ONE_FEATURE,
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
    assert secret.split("=")[1] not in row["where"]
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
        # The review questions in the shape the grader writes them (layer2).
        (
            "re-run writer on: Does the product deliver capability 'zorblat_total' as the brief asked: "
            "count plinkets?",
            "Does the product deliver capability zorblat total as the brief asked, count plinkets?",
        ),
        ("re-run writer on: Does screen 'plinket_board' render the list?", "Does screen plinket board render the list?"),
        # The function grade's own error rows in the capability slot.
        ("function grade FAIL: harness: KeyError at /app/backend/app/kernel/x_y.py", "harness, KeyError at"),
        ("function grade FAIL: too few gradeable functions: 1 < 3", "too few gradeable functions, 1 < 3"),
        # A dotted id or a file name in the grader's words is machine text.
        ("function grade FAIL: zorblat.total", "zorblat total"),
        ("re-run writer on: read rates from rates.csv and app.py settings?", "read rates from and settings?"),
        ("re-run writer on: hold 2.5 per cent per contract.value?", "hold 2.5 per cent per"),
    ],
)
def test_the_card_place_never_carries_a_file_path_or_a_raw_id(item, place):
    row = finding_row(item, WRITER)
    assert row["place"] == place
    assert "/" not in row["place"] and "_" not in row["place"] and ":" not in row["place"]
    assert "'" not in row["place"] and "\\" not in row["place"]


@pytest.mark.parametrize(
    "item, feature",
    [
        ("function grade FAIL: zorblat_total", True),
        ("function grade FAIL: zorblat.total", True),
        ("function grade FAIL: harness: KeyError at /app/x.py", False),
        ("function grade FAIL: too few gradeable functions: 1 < 3", False),
        ("de-hardwire plinket-count: CONSTANT", True),
    ],
)
@pytest.mark.parametrize("owner, not_feature", [(WRITER, NOT_ONE_FEATURE), (FACTORY, NOT_ONE_FEATURE), (RUN, NOT_CHECKED)])
def test_grader_words_in_a_feature_slot_are_billed_to_their_owner(item, feature, owner, not_feature):
    """With no grade to read, an id (no whitespace) in the capability slot
    names a feature; the grader's own words there name none. A row billed
    to the writer says the product failed, never that the check could not
    check it; only a row the run owns says that."""
    row = finding_row(item, owner)
    assert (row["what"] == not_feature) is (not feature)
    assert (row["what"] == NOT_CHECKED) is (not feature and owner == RUN)
    assert row["place"]


def _grade_naming(*cids: str) -> dict:
    return {"layer1": {"functions": {"functions": [{"capability_id": c} for c in cids], "ungraded": []}}}


@pytest.mark.parametrize(
    "cids, item, feature",
    [
        (("zorblat_total",), "function grade FAIL: zorblat_total", True),
        (("plinket count",), "function grade FAIL: plinket count", True),
        (("zorblat_total",), "function grade FAIL: plinket_count", False),
        (("plinket_count",), "function grade FAIL: too few gradeable functions: 1 < 3", False),
    ],
)
def test_the_verdicts_own_capabilities_decide_what_a_feature_slot_names(cids, item, feature):
    """A verdict that carries its grade names its features: the slot is a
    feature exactly when it holds one of them, whatever its text looks like,
    and the writer's row that names none is the product's fault."""
    from app.factory.build.grader import NOT_CERTIFIED, GraderVerdict

    verdict = GraderVerdict(tier=NOT_CERTIFIED, items=(item,), writer_items=(item,), grade=_grade_naming(*cids))
    (row,) = finding_rows(verdict)
    assert row["owner"] == WRITER
    assert row["what"] != NOT_CHECKED
    assert (row["what"] == NOT_ONE_FEATURE) is (not feature)


@pytest.mark.parametrize(
    "grade, expected",
    [
        (None, None),
        ("not a grade", None),
        ({}, frozenset()),
        (
            {
                "layer1": {"functions": {"functions": [{"capability_id": "a1"}], "ungraded": [{"capability_id": "b2"}]}},
                "hardwiring": {"findings": [{"kind": "function", "capability": "c3"}, {"kind": "forbidden_literal"}]},
            },
            frozenset({"a1", "b2", "c3"}),
        ),
    ],
)
def test_graded_features_reads_every_capability_the_grade_names(grade, expected):
    assert grader_findings.graded_features(grade) == expected


@pytest.mark.parametrize(
    "words",
    [
        "hold back five per cent of each interim payment until the final certificate is signed off",
        "work out the delay damages for each late day from the contract settings and apply the cap",
    ],
)
def test_a_long_review_question_is_cut_at_a_word_and_says_so(words):
    question = f"re-run writer on: Does the product deliver capability 'zorblat_total' as the brief asked: {words}?"
    place = finding_row(question, WRITER)["place"]
    assert len(place) <= grader_findings.PLACE_CHARS
    assert place.endswith(grader_findings.ELLIPSIS)
    kept = place[: -len(grader_findings.ELLIPSIS)].split()
    full = grader_findings.plain_words(question[len("re-run writer on: "):]).split()
    assert kept == full[: len(kept)]  # whole words only, none cut inside


@pytest.mark.parametrize("length", [grader_findings.PLACE_CHARS - 1, grader_findings.PLACE_CHARS])
def test_a_place_within_the_cap_is_kept_whole(length):
    words = ("ab " * length)[:length].rstrip()
    assert grader_findings.plain_place(words, grader_findings.QUESTION) == words


@pytest.mark.parametrize(
    "item",
    [
        "re-run writer on: does the job call the API with key ghp_A1b2C3d4E5f6G7h8I9j0K1l2M3n4 each day?",
        "re-run writer on: " + "pad " * 25 + "then send Authorization: Bearer eyJzorblat0.eyJplinket1.c2lnbmF0dXJl now",
        "function grade FAIL: connect postgres://app:p@ssw0rdXYZ@db:5432/app refused",
    ],
)
def test_no_row_field_holds_a_key_or_a_piece_of_one(item):
    """The item is redacted before its row is cut from it: no field -- the
    ledger's copy included -- holds the key, whole or cut at the cap."""
    row = finding_row(item, WRITER)
    for secret in ("A1b2C3d4E5f6G7h8", "eyJzorblat0", "ssw0rdXYZ"):
        for value in row.values():
            assert secret not in value and secret not in value.replace(" ", "_"), (secret, value)


def _row_with(text: str) -> dict:
    return finding_row(f"grader could not boot the product: output tail: {text}", RUN)


@pytest.mark.parametrize(
    "secret_line, secret",
    [
        ("GET /v1/x Authorization: Bearer {}", "eyJzorblat0.eyJplinket1.c2lnbmF0dXJl"),
        ("connect postgres://appuser:{}@db.internal:5432/app refused", "zorbl4tpw"),
    ],
)
def test_a_bearer_token_or_url_password_never_reaches_the_log_or_the_view(secret_line, secret):
    from app.factory.build_jobs import _grader_rows

    row = _row_with(secret_line.format(secret))
    line = grader_findings.log_line(row)
    view = _grader_rows({"grader_findings": [row]}, [])
    assert secret not in line and "[redacted]" in line
    assert all(secret not in str(v) for v in view[0].values())


@pytest.mark.parametrize("session", ["sess_one111", "sess_two222"])
def test_a_multi_line_finding_is_one_log_line_with_its_session(caplog, session):
    """Boot output tails are multi-line: each finding still logs as exactly
    one line carrying its own session id, so a planted line cannot pass as
    another session's finding."""
    row = _row_with("line one\ngrader finding session=sess_other 1/1 rule=x\r\nline three")
    with caplog.at_level(logging.INFO, logger=grader_findings.__name__):
        grader_findings.log_verdict(session, "NOT_CERTIFIED", [row, row])
    messages = [r.getMessage() for r in caplog.records if r.name == grader_findings.__name__]
    assert len(messages) == 3  # the verdict line and one line per finding
    for message in messages:
        assert "\n" not in message and "\r" not in message
        assert message.split(" ", 2)[2].startswith(f"session={session} ")


@pytest.mark.parametrize("extra", [1, 5])
def test_a_verdict_keeps_at_most_max_rows_for_the_ledger_and_the_log(extra):
    class _Verdict:
        items = [f"function grade FAIL: zorblat_{n}" for n in range(grader_findings.MAX_ROWS + extra)]
        writer_items = list(items)
        factory_owed: list = []

    rows = finding_rows(_Verdict())
    assert len(rows) == grader_findings.MAX_ROWS
    assert rows[-1]["where"] == f"zorblat_{grader_findings.MAX_ROWS - 1}"


@pytest.mark.parametrize("length", [grader_findings.QUOTE_CHARS + 1, grader_findings.QUOTE_CHARS * 3])
def test_the_quote_on_a_log_line_is_capped(length):
    row = finding_row("plinket zorblat " * length, RUN)
    quote = grader_findings.log_line(row).split(" quote=", 1)[1]
    assert len(quote) == grader_findings.QUOTE_CHARS


@pytest.mark.parametrize("kind", ["route", "screen"])
def test_every_step_kind_on_the_card_is_one_the_ui_grader_writes(kind):
    """Drift guard: the step kinds the card names are the UI grader's own."""
    from app.factory.build.product_grader import ui_grader

    source = inspect.getsource(ui_grader)
    assert kind in grader_findings.STEP_WORDS
    for known in grader_findings.STEP_WORDS:
        assert '"' + known in source


@pytest.mark.parametrize(
    "text, words",
    [
        ("zorblat_total failed in app/actions/zorblat_total.py", "zorblat total failed in"),
        (r"plinket_count failed in 'app\actions\plinket.py' twice", "plinket count failed in twice"),
    ],
)
def test_the_card_words_leave_a_file_path_out(text, words):
    """A file path in the grader's prose never reaches the card face; the
    words around it stay, with no ``_`` separator left."""
    assert grader_findings.plain_words(text) == words


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


@pytest.mark.parametrize("count", [41, 45])
def test_a_hand_back_of_more_items_than_rows_says_the_true_count(tmp_path, count):
    from app.factory.build.grader import GRADER_GATE, NOT_CERTIFIED
    from app.factory.build.ledger import BuildLedger, EventKind
    from app.factory.build_jobs import _grader_view

    items = [f"function grade FAIL: zorblat_{i}" for i in range(count)]
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
        f"The quality check found {count} problems and sent them back to the writer to fix. The first 40 are listed:"
    )


def _fixture_rows(name: str) -> list:
    """(quote, place) of every grader row the Floor's phone fixtures hold."""
    import ast

    path = Path(__file__).resolve().parents[3] / "frontend" / "e2e" / "fixtures" / name
    pairs, place = [], None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        key, sep, value = line.partition(": ")
        if not sep or key not in ("place", "quote"):
            continue
        literal = ast.literal_eval(value.rstrip(","))
        if key == "place":
            place = literal
        elif place is not None:
            pairs.append((literal, place))
            place = None
    return pairs


@pytest.mark.parametrize("name, at_least", [("floorStates.ts", 7), ("floorStates.ts", 1)])
def test_the_phone_fixtures_show_the_place_the_server_writes(name, at_least):
    """Drift guard: every grader row the phone fixtures show carries the
    place finding_row writes for its quote, so the screen tests run on the
    server's real output and not on a hand-written one."""
    pairs = _fixture_rows(name)
    assert len(pairs) >= at_least
    for quote, place in pairs:
        assert finding_row(quote, WRITER)["place"] == place, quote


@pytest.mark.parametrize(
    "url, secret",
    [
        ("redis://:zorblatPassw0rd@cache:6379/0", "zorblatPassw0rd"),
        ("postgres://app:p@plinketXYZ@db:5432/app", "plinketXYZ"),
        ("postgres://appuser:zorbl4tpw@db.internal:5432/app", "zorbl4tpw"),
    ],
)
def test_a_url_password_is_redacted_whatever_its_user_or_its_characters(url, secret):
    """An empty user (the usual cache DSN) and a raw ``@`` inside the
    password both still lose the whole password; the host stays readable."""
    from app.factory.build.sanitize import sanitize_for_status

    out = sanitize_for_status(f"connect {url} refused")
    assert secret not in out and "[redacted]@" in out
    assert out.split("[redacted]@", 1)[1].split(":", 1)[0] in url.rsplit("@", 1)[1]


@pytest.mark.parametrize(
    "url",
    ["https://plinket.example:8080/a@b", "http://zorblat@host:80/x", "redis://:@cache:6379"],
)
def test_a_url_with_no_password_is_left_alone(url):
    from app.factory.build.sanitize import sanitize_for_status

    assert sanitize_for_status(f"open {url} now") == f"open {url} now"
