"""K-2 (Advisor 12:13Z, rules R4 + R5): a defective Store block is repaired
under its OWN allowance, and the defect is the Store's, never the writer's.

R4: a repair never spends the writer's grader rework budget; it is counted
from NOTE events per block; a failed repair marks only the capabilities bound
to that block as not delivered, and the run completes. R5: grade.json lists
the Store defects in their own section, and no writer item is billed.

Every block id, capability id, file and literal below is invented, or read
from the plan the test builds.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.factory.test_a_grader import _fake_pass, _fake_pass_harness, smoke_blueprint  # noqa: F401


def _finding(
    block_id: str, name: str = "x.py", *, owner: str = "", root: str = "vendor/blocks"
) -> dict:
    from app.factory.build.factory_receipt import FACTORY

    return {
        "file": f"{root}/{block_id}/{name}",
        "kind": "forbidden_literal",
        "owner": owner or FACTORY,
        "literals": ["Qux"],
    }


def _store_verdict(*findings: dict, writer_items: tuple = (), ungraded: tuple = ()):
    from app.factory.build import grader as grader_mod

    owed = tuple(f"FACTORY owes baked literals in {f['file']}: {f['literals']}" for f in findings)
    return grader_mod.GraderVerdict(
        tier=grader_mod.NOT_CERTIFIED,
        items=tuple(writer_items) + owed + tuple(ungraded),
        ungraded=tuple(ungraded),
        grade={"product_id": "zorblat", "hardwiring": {"findings": list(findings)}},
        writer_items=tuple(writer_items),
        factory_owed=owed,
    )


def _note(payload: dict):
    from app.factory.build.ledger import EventKind

    return SimpleNamespace(kind=EventKind.NOTE, payload=payload)


def _rework(payload: dict):
    from app.factory.build.ledger import EventKind

    return SimpleNamespace(kind=EventKind.REWORK, payload=payload)


def _cap(cid: str, *blocks: str):
    return SimpleNamespace(capability_id=cid, block_ids=list(blocks))


def _passes(block_id, defects):
    from app.factory.build.store_repair import RepairResult

    return RepairResult(block_id, True, exam_id=f"exam-{block_id}", exam_score=1.0)


def _fails(block_id, defects):
    from app.factory.build.store_repair import RepairResult

    return RepairResult(
        block_id, False, exam_id=f"exam-{block_id}", exam_score=0.25, detail="sealed case red"
    )


def _installs(result, defects):
    """An installer that puts every passed repair in place (F-3)."""
    return None


def _certified():
    from app.factory.build import grader as grader_mod

    return grader_mod.GraderVerdict(tier=grader_mod.CERTIFIED)


# -- R4: the allowance is the block's own, counted from NOTE events ------------


@pytest.mark.parametrize(
    "events, block, expected",
    [
        ([], "zorblat", 0),
        ([lambda: _note({"store_repair": "zorblat"})], "zorblat", 1),
        (
            [
                lambda: _note({"store_repair": "zorblat"}),
                lambda: _note({"store_repair": "zorblat"}),
            ],
            "zorblat",
            2,
        ),
        ([lambda: _note({"store_repair": "plinket"})], "zorblat", 0),
        ([lambda: _note({"store_defect": "zorblat"})], "zorblat", 0),
        ([lambda: _rework({"store_repair": "zorblat"})], "zorblat", 0),
    ],
)
def test_repairs_spent_counts_only_this_blocks_repair_notes(events, block, expected):
    from app.factory.build.store_repair import repairs_spent

    assert repairs_spent([make() for make in events], block) == expected


@pytest.mark.parametrize(
    "caps, block, expected",
    [
        ([_cap("alpha", "zorblat"), _cap("beta", "plinket")], "zorblat", ("alpha",)),
        (
            [_cap("alpha", "zorblat"), _cap("beta", "plinket", "zorblat")],
            "zorblat",
            ("alpha", "beta"),
        ),
        ([_cap("alpha", "plinket")], "zorblat", ()),
    ],
)
def test_capabilities_of_reads_the_plans_block_binding(caps, block, expected):
    from app.factory.build.store_repair import capabilities_of

    assert capabilities_of(caps, block) == expected


@pytest.mark.parametrize(
    "verdict",
    [
        # the writer owns a row: the writer's rework path decides it
        lambda: _store_verdict(_finding("zorblat"), writer_items=("function grade FAIL: alpha",)),
        # a layer did not run: a run-level gap, never a repair
        lambda: _store_verdict(
            _finding("zorblat"), ungraded=("function ungraded: alpha: no route",)
        ),
        # a Factory finding outside any vendored block
        lambda: _store_verdict(_finding("zorblat", root="app/stamped")),
        # nothing owed at all
        lambda: _store_verdict(),
    ],
)
def test_settle_leaves_any_verdict_that_is_not_only_store_defects(verdict):
    from app.factory.build.store_repair import settle

    called: list = []

    def repairer(block_id, defects):
        called.append(block_id)
        return _passes(block_id, defects)

    assert (
        settle(verdict(), plan_capabilities=[], events=[], repairer=repairer, installer=_installs)
        is None
    )
    assert called == []


@pytest.mark.parametrize(
    "extra", ["FACTORY owes baked literals in plinket/stamped.py", "refused: layer2"]
)
def test_settle_leaves_a_verdict_with_an_owed_item_no_finding_explains(extra):
    from dataclasses import replace

    from app.factory.build.store_repair import settle

    base = _store_verdict(_finding("zorblat"))
    verdict = replace(base, items=base.items + (extra,), factory_owed=base.factory_owed + (extra,))
    assert (
        settle(verdict, plan_capabilities=[], events=[], repairer=_passes, installer=_installs)
        is None
    )


@pytest.mark.parametrize(
    "owner_lists",
    [
        {"writer_items": ("function grade FAIL: alpha",)},
        {"writer_items": ("ui grade FAIL: beta", "function grade FAIL: gamma")},
        {"ungraded": ("function ungraded: alpha: no route",)},
        {"ungraded": ("layer2 ungraded: delta",)},
    ],
)
def test_the_owner_lists_alone_keep_a_verdict_from_repair(owner_lists):
    """The owner lists decide, not the quoted list: a verdict whose writer or
    ungraded list is open is never a Store repair, even when ``items`` quotes
    only the Store defects."""
    from dataclasses import replace

    from app.factory.build.store_repair import settle

    base = _store_verdict(_finding("zorblat"))
    verdict = replace(base, **owner_lists)
    assert verdict.items == verdict.factory_owed
    called: list = []

    def repairer(block_id, defects):
        called.append(block_id)
        return _passes(block_id, defects)

    assert (
        settle(verdict, plan_capabilities=[], events=[], repairer=repairer, installer=_installs)
        is None
    )
    assert called == []
    # the same quoted list with both owner lists empty IS repaired
    assert (
        settle(base, plan_capabilities=[], events=[], repairer=repairer, installer=_installs)
        is not None
    )
    assert called == ["zorblat"]


@pytest.mark.parametrize("block", ["zorblat", "plinket"])
def test_a_passed_repair_delivers_every_capability(block):
    from app.factory.build.store_repair import settle

    caps = [_cap("alpha", block), _cap("beta", "frobnic")]
    out = settle(
        _store_verdict(_finding(block)),
        plan_capabilities=caps,
        events=[],
        repairer=_passes,
        installer=_installs,
    )
    assert len(out) == 1
    assert out[0].block_id == block
    assert out[0].attempted is True
    assert out[0].repaired is True
    assert out[0].not_delivered == ()
    assert out[0].reason == ""
    assert out[0].exam_id == f"exam-{block}"


@pytest.mark.parametrize(
    "repairer, detail",
    [
        (_fails, "sealed case red"),
        (
            lambda b, d: (_ for _ in ()).throw(RuntimeError("exam harness down")),
            "repair crashed: RuntimeError: exam harness down",
        ),
        (lambda b, d: {"passed": True}, "repairer returned no RepairResult"),
    ],
)
def test_a_failed_repair_marks_only_its_blocks_capabilities_not_delivered(repairer, detail):
    from app.factory.build.store_override import REPAIR_FAILED_REASON
    from app.factory.build.store_repair import settle

    caps = [_cap("alpha", "zorblat"), _cap("beta", "plinket"), _cap("gamma", "zorblat")]
    out = settle(
        _store_verdict(_finding("zorblat")),
        plan_capabilities=caps,
        events=[],
        repairer=repairer,
        installer=_installs,
    )
    assert [r.block_id for r in out] == ["zorblat"]
    assert out[0].attempted is True
    assert out[0].repaired is False
    assert out[0].not_delivered == ("alpha", "gamma")
    assert out[0].reason == REPAIR_FAILED_REASON
    assert out[0].detail == detail


@pytest.mark.parametrize("allowance, spent", [(1, 1), (2, 2), (1, 3)])
def test_a_block_whose_allowance_is_spent_gets_no_repair_round(allowance, spent):
    from app.factory.build.store_override import REPAIR_FAILED_REASON
    from app.factory.build.store_repair import ALLOWANCE_SPENT, settle

    called: list = []

    def repairer(block_id, defects):
        called.append(block_id)
        return _passes(block_id, defects)

    events = [_note({"store_repair": "zorblat"}) for _ in range(spent)]
    out = settle(
        _store_verdict(_finding("zorblat"), _finding("plinket")),
        plan_capabilities=[_cap("alpha", "zorblat"), _cap("beta", "plinket")],
        events=events,
        repairer=repairer,
        installer=_installs,
        allowance=allowance,
    )
    assert called == ["plinket"]
    by_block = {r.block_id: r for r in out}
    assert by_block["zorblat"].attempted is False
    assert by_block["zorblat"].repaired is False
    assert by_block["zorblat"].detail == ALLOWANCE_SPENT
    assert by_block["zorblat"].reason == REPAIR_FAILED_REASON
    assert by_block["zorblat"].not_delivered == ("alpha",)
    assert by_block["plinket"].repaired is True


@pytest.mark.parametrize("allowance", [1, 2])
def test_a_block_with_allowance_left_is_repaired(allowance):
    from app.factory.build.store_repair import settle

    events = [_note({"store_repair": "zorblat"}) for _ in range(allowance - 1)]
    out = settle(
        _store_verdict(_finding("zorblat")),
        plan_capabilities=[],
        events=events,
        repairer=_passes,
        installer=_installs,
        allowance=allowance,
    )
    assert [(r.block_id, r.attempted, r.repaired) for r in out] == [("zorblat", True, True)]


@pytest.mark.parametrize("names", [("a.py", "b.py"), ("x.py", "y.py", "z.py")])
def test_one_repair_per_block_whatever_the_number_of_findings(names):
    from app.factory.build.store_repair import settle

    called: list = []

    def repairer(block_id, defects):
        called.append(block_id)
        return _passes(block_id, defects)

    findings = [_finding("zorblat", n) for n in names]
    out = settle(
        _store_verdict(*findings),
        plan_capabilities=[],
        events=[],
        repairer=repairer,
        installer=_installs,
    )
    assert called == ["zorblat"]
    assert [r.block_id for r in out] == ["zorblat"]


# -- R5: the defect is the Store's ---------------------------------------------


@pytest.mark.parametrize("attempted, has_round", [(True, True), (False, False)])
def test_only_an_attempted_repair_spends_the_allowance(attempted, has_round):
    from app.factory.build.store_repair import STORE_DEFECT_KEY, STORE_REPAIR_KEY, BlockRepair

    note = BlockRepair("zorblat", ("forbidden_literal",), attempted, False).note(4)
    assert note[STORE_DEFECT_KEY] == "zorblat"
    assert (STORE_REPAIR_KEY in note) is has_round
    assert (note.get("repair_round") == 4) is has_round


@pytest.mark.parametrize("repaired, not_delivered", [(True, ()), (False, ("alpha", "beta"))])
def test_grade_record_gains_the_store_defects_section(repaired, not_delivered):
    from app.factory.build.store_repair import GRADE_SECTION, BlockRepair, grade_with_section

    grade = {"product_id": "zorblat", "certified": False}
    repair = BlockRepair(
        "plinket", ("forbidden_literal",), True, repaired, "exam-plinket", 0.5, not_delivered, "why"
    )
    out = grade_with_section(grade, [repair])
    assert out["product_id"] == "zorblat"
    assert out["certified"] is False
    assert out[GRADE_SECTION] == [
        {
            "block_id": "plinket",
            "kinds": ["forbidden_literal"],
            "repaired": repaired,
            "exam_id": "exam-plinket",
            "exam_score": 0.5,
            "not_delivered": list(not_delivered),
            "reason": "why",
        }
    ]
    assert GRADE_SECTION not in grade


# -- the runner ----------------------------------------------------------------


def _bound_block(runner, index: int):
    caps = [c for c in runner.plan.capabilities if c.block_ids]
    block = caps[index % len(caps)].block_ids[0]
    bound = tuple(c.capability_id for c in runner.plan.capabilities if block in c.block_ids)
    return block, bound


def _counting_writer(calls: list):
    def writer(ctx):
        calls.append(tuple(str(i) for i in ctx.work_list))
        return _fake_pass(ctx)

    return writer


def _grader_reworks(runner):
    from app.factory.build.grader import GRADER_GATE
    from app.factory.build.ledger import EventKind

    return [
        e
        for e in runner.ledger.events()
        if e.kind is EventKind.REWORK and (e.payload or {}).get("gate") == GRADER_GATE
    ]


def _repair_notes(runner):
    from app.factory.build.ledger import EventKind
    from app.factory.build.store_repair import STORE_DEFECT_KEY

    return [
        e
        for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and STORE_DEFECT_KEY in (e.payload or {})
    ]


def _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, repairer, installer=_installs):  # noqa: F811
    from app.factory.build import grader as grader_mod
    from app.factory.build.authority import BuildRole
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(grader_mod.DISABLE_ENV, raising=False)
    roles = _fake_pass_harness(monkeypatch)
    writes: list = []
    roles[BuildRole.WRITER] = _counting_writer(writes)
    runner = RoleRunner(smoke_blueprint, tmp_path / "build", roles=roles, brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}')
    runner.store_repairer = repairer
    runner.store_installer = installer
    queue = list(verdicts(runner))

    def grade(**kwargs):
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(grader_mod, "run_grader", grade)
    return runner, writes


@pytest.mark.parametrize("index", [0, 1])
def test_a_build_with_a_store_defect_is_repaired_and_finishes(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
):
    from app.factory.build.store_repair import GRADE_SECTION, STORE_REPAIR_KEY

    holder: dict = {}

    def verdicts(runner):
        holder["block"], holder["bound"] = _bound_block(runner, index)
        return [_store_verdict(_finding(holder["block"])), _certified()]

    runner, writes = _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, _passes)
    outcome = runner.run()
    assert outcome.ok, outcome.detail
    assert len(writes) == 1
    assert _grader_reworks(runner) == []
    notes = _repair_notes(runner)
    assert [n.payload[STORE_REPAIR_KEY] for n in notes] == [holder["block"]]
    assert notes[0].payload["repaired"] is True
    grade = json.loads((runner.workspace / "grade.json").read_text(encoding="utf-8"))
    assert [(r["block_id"], r["repaired"], r["not_delivered"]) for r in grade[GRADE_SECTION]] == [
        (holder["block"], True, [])
    ]


@pytest.mark.parametrize("index", [0, 1])
def test_a_failed_repair_still_completes_the_rest_of_the_build(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
):
    from app.factory.build.store_override import REPAIR_FAILED_REASON
    from app.factory.build.store_repair import GRADE_SECTION

    holder: dict = {}

    def verdicts(runner):
        holder["block"], holder["bound"] = _bound_block(runner, index)
        return [_store_verdict(_finding(holder["block"]))]

    runner, writes = _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, _fails)
    outcome = runner.run()
    assert outcome.ok, outcome.detail
    assert len(writes) == 1
    assert _grader_reworks(runner) == []
    every = {c.capability_id for c in runner.plan.capabilities}
    assert set(holder["bound"]) < every
    notes = _repair_notes(runner)
    assert len(notes) == 1
    assert notes[0].payload["repaired"] is False
    assert notes[0].payload["reason"] == REPAIR_FAILED_REASON
    assert tuple(notes[0].payload["not_delivered"]) == holder["bound"]
    grade = json.loads((runner.workspace / "grade.json").read_text(encoding="utf-8"))
    rows = grade[GRADE_SECTION]
    assert [(r["block_id"], r["repaired"], tuple(r["not_delivered"])) for r in rows] == [
        (holder["block"], False, holder["bound"])
    ]


@pytest.mark.parametrize("index", [0, 1])
def test_a_repair_never_consumes_the_writers_rework_budget(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
):
    """The writer spends its one grader round on its own row; the Store
    defect that is left is then repaired under the block's own allowance,
    and the run completes -- the writer budget is untouched by the repair."""
    from app.factory.build import grader as grader_mod
    from app.factory.build.runner import REWORK_BUDGET

    holder: dict = {}

    def verdicts(runner):
        holder["block"], _ = _bound_block(runner, index)
        writer_row = "function grade FAIL: zorblat_check"
        return [
            grader_mod.GraderVerdict(
                tier=grader_mod.NOT_CERTIFIED, items=(writer_row,), writer_items=(writer_row,)
            ),
            _store_verdict(_finding(holder["block"])),
            _certified(),
        ]

    runner, writes = _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, _passes)
    outcome = runner.run()
    assert outcome.ok, outcome.detail
    assert len(writes) == 2
    assert len(_grader_reworks(runner)) == REWORK_BUDGET
    assert len(_repair_notes(runner)) == 1


@pytest.mark.parametrize("index", [0, 1])
def test_without_a_repairer_a_store_defect_still_ends_the_run_naming_the_factory(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
):
    holder: dict = {}

    def verdicts(runner):
        holder["block"], _ = _bound_block(runner, index)
        return [_store_verdict(_finding(holder["block"]))]

    runner, writes = _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, None)
    outcome = runner.run()
    assert not outcome.ok
    assert "FACTORY_FAULT" in outcome.detail
    assert len(writes) == 1
    assert _repair_notes(runner) == []


@pytest.mark.parametrize("root", ["app/stamped", "docs/generated"])
def test_a_factory_finding_outside_a_vendored_block_is_not_repaired(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    root,
):
    called: list = []

    def repairer(block_id, defects):
        called.append(block_id)
        return _passes(block_id, defects)

    runner, writes = _runner(
        smoke_blueprint,
        tmp_path,
        monkeypatch,
        lambda r: [_store_verdict(_finding("zorblat", root=root))],
        repairer,
    )
    outcome = runner.run()
    assert not outcome.ok
    assert "FACTORY_FAULT" in outcome.detail
    assert called == []


# -- F-3: a repair settles only when installed and re-graded -------------------


@pytest.mark.parametrize(
    "installer, detail",
    [
        (lambda r, d: "lock check refused", "sealed ok; not installed: lock check refused"),
        (lambda r, d: "staged copy missing", "sealed ok; not installed: staged copy missing"),
        (
            lambda r, d: (_ for _ in ()).throw(OSError("disk full")),
            "sealed ok; install crashed: OSError: disk full",
        ),
    ],
)
def test_a_passed_exam_that_is_not_installed_is_not_a_repair(installer, detail):
    from app.factory.build.store_override import REPAIR_FAILED_REASON
    from app.factory.build.store_repair import RepairResult, settle

    def repairer(block_id, defects):
        return RepairResult(block_id, True, exam_id=f"exam-{block_id}", detail="sealed ok")

    caps = [_cap("alpha", "zorblat"), _cap("beta", "plinket")]
    out = settle(
        _store_verdict(_finding("zorblat")),
        plan_capabilities=caps,
        events=[],
        repairer=repairer,
        installer=installer,
    )
    assert [(r.block_id, r.attempted, r.repaired) for r in out] == [("zorblat", True, False)]
    assert out[0].not_delivered == ("alpha",)
    assert out[0].reason == REPAIR_FAILED_REASON
    assert out[0].detail == detail


@pytest.mark.parametrize(
    "block, refusal",
    [("plinket", "no lock entry"), ("zorblat", "staged copy missing")],
)
def test_an_install_refusal_with_no_exam_detail_is_named_alone(block, refusal):
    from app.factory.build.store_repair import RepairResult, settle

    out = settle(
        _store_verdict(_finding(block)),
        plan_capabilities=[],
        events=[],
        repairer=lambda b, d: RepairResult(b, True, exam_id=f"exam-{b}"),
        installer=lambda r, d: refusal,
    )
    assert out[0].block_id == block
    assert out[0].repaired is False
    assert out[0].detail == f"not installed: {refusal}"


@pytest.mark.parametrize("passes", [True, False])
def test_only_a_passed_exam_is_handed_to_the_installer(passes):
    from app.factory.build.store_repair import settle

    seen: list = []

    def installer(result, defects):
        seen.append((result.block_id, tuple(d.block_id for d in defects)))
        return None

    out = settle(
        _store_verdict(_finding("zorblat")),
        plan_capabilities=[],
        events=[],
        repairer=_passes if passes else _fails,
        installer=installer,
    )
    assert seen == ([("zorblat", ("zorblat",))] if passes else [])
    assert out[0].repaired is passes


def _repair(block_id: str, repaired: bool):
    from app.factory.build.store_repair import BlockRepair

    return BlockRepair(block_id, ("forbidden_literal",), True, repaired)


@pytest.mark.parametrize(
    "verdict, repairs, settles",
    [
        # the re-grade is certified
        (lambda: _certified(), [("zorblat", True)], True),
        # the defect is still in a block reported repaired
        (lambda: _store_verdict(_finding("zorblat")), [("zorblat", True)], False),
        # what is left is in the block whose repair failed (marked)
        (
            lambda: _store_verdict(_finding("plinket")),
            [("zorblat", True), ("plinket", False)],
            True,
        ),
        # one left in a failed block, one in a repaired block
        (
            lambda: _store_verdict(_finding("plinket"), _finding("zorblat")),
            [("zorblat", True), ("plinket", False)],
            False,
        ),
        # a defect in a block no repair covered
        (lambda: _store_verdict(_finding("frobnic")), [("zorblat", True)], False),
        # the re-grade opened a writer item
        (
            lambda: _store_verdict(_finding("plinket"), writer_items=("function grade FAIL: x",)),
            [("plinket", False)],
            False,
        ),
        # a Factory finding outside any vendored block
        (lambda: _store_verdict(_finding("plinket", root="app/x")), [("plinket", False)], False),
    ],
)
def test_the_regrade_settles_only_on_what_the_repairs_explain(verdict, repairs, settles):
    from app.factory.build.store_repair import regrade_settles

    assert regrade_settles(verdict(), [_repair(b, ok) for b, ok in repairs]) is settles


def _regrade_notes(runner):
    from app.factory.build.ledger import EventKind

    return [
        e
        for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and (e.payload or {}).get("store_regrade") is True
    ]


@pytest.mark.parametrize("index", [0, 1])
def test_a_repair_the_regrade_still_finds_does_not_settle_the_run(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
):
    holder: dict = {}

    def verdicts(runner):
        holder["block"], _ = _bound_block(runner, index)
        return [_store_verdict(_finding(holder["block"]))]

    runner, writes = _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, _passes)
    outcome = runner.run()
    assert not outcome.ok
    assert "FACTORY_FAULT" in outcome.detail
    assert len(writes) == 1
    notes = _regrade_notes(runner)
    assert len(notes) == 1
    assert notes[0].payload["settled"] is False


@pytest.mark.parametrize(
    "index, crash, named",
    [
        (0, RuntimeError("grader down"), "RuntimeError: grader down"),
        (1, OSError("socket closed"), "OSError: socket closed"),
    ],
)
def test_a_crashed_regrade_settles_nothing(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
    crash,
    named,
):
    holder: dict = {}

    def verdicts(runner):
        holder["block"], _ = _bound_block(runner, index)
        return [_store_verdict(_finding(holder["block"])), crash]

    runner, _ = _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, _passes)
    outcome = runner.run()
    assert not outcome.ok
    notes = _regrade_notes(runner)
    assert len(notes) == 1
    assert notes[0].payload["settled"] is False
    assert f"re-grade crashed: {named}" in notes[0].detail


@pytest.mark.parametrize("index", [0, 1])
def test_a_settling_regrade_is_recorded_and_its_grade_kept(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
):
    from app.factory.build import grader as grader_mod
    from app.factory.build.store_repair import GRADE_SECTION

    holder: dict = {}

    def verdicts(runner):
        holder["block"], _ = _bound_block(runner, index)
        regraded = grader_mod.GraderVerdict(
            tier=grader_mod.CERTIFIED, grade={"product_id": "plinket", "regraded": True}
        )
        return [_store_verdict(_finding(holder["block"])), regraded]

    runner, _ = _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, _passes)
    outcome = runner.run()
    assert outcome.ok, outcome.detail
    notes = _regrade_notes(runner)
    assert [n.payload["settled"] for n in notes] == [True]
    grade = json.loads((runner.workspace / "grade.json").read_text(encoding="utf-8"))
    assert grade["regraded"] is True
    assert [r["block_id"] for r in grade[GRADE_SECTION]] == [holder["block"]]


@pytest.mark.parametrize("index", [0, 1])
def test_a_repair_the_installer_refuses_is_marked_and_never_regraded(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
):
    from app.factory.build.store_repair import GRADE_SECTION

    holder: dict = {}
    calls: list = []

    def verdicts(runner):
        holder["block"], holder["bound"] = _bound_block(runner, index)
        return [_store_verdict(_finding(holder["block"]))]

    def installer(result, defects):
        calls.append(result.block_id)
        return "lock check refused"

    runner, _ = _runner(
        smoke_blueprint, tmp_path, monkeypatch, verdicts, _passes, installer=installer
    )
    outcome = runner.run()
    assert outcome.ok, outcome.detail
    assert calls == [holder["block"]]
    assert _regrade_notes(runner) == []
    notes = _repair_notes(runner)
    assert notes[0].payload["repaired"] is False
    assert "not installed: lock check refused" in notes[0].payload["detail"]
    grade = json.loads((runner.workspace / "grade.json").read_text(encoding="utf-8"))
    assert [tuple(r["not_delivered"]) for r in grade[GRADE_SECTION]] == [holder["bound"]]


@pytest.mark.parametrize("index", [0, 1])
def test_without_an_installer_a_store_defect_still_ends_the_run_naming_the_factory(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
):
    called: list = []

    def repairer(block_id, defects):
        called.append(block_id)
        return _passes(block_id, defects)

    def verdicts(runner):
        block, _ = _bound_block(runner, index)
        return [_store_verdict(_finding(block))]

    runner, _ = _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, repairer, installer=None)
    outcome = runner.run()
    assert not outcome.ok
    assert "FACTORY_FAULT" in outcome.detail
    assert called == []
    assert _repair_notes(runner) == []


# -- F-3 end to end: a deliberately broken vendored block, the real installer --


_BROKEN_BLOCK = {"block.py": "def run(x):\n    return x['missing']\n", "block.json": "{}\n"}
_FIXED_BLOCK = {"block.py": "def run(x):\n    return x.get('missing')\n"}


def _write_tree(root, files: dict) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("proof_passes", [True, False], ids=["installed", "refused"])
def test_a_broken_vendored_block_through_the_runner_and_the_real_installer(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    proof_passes,
):
    """CLONER vendors a broken block (lock saved, the Factory records it); the
    grader finds its defect; the repairer stages a fix and the Factory's proof
    of its exam; the real ``install_repair`` puts it in place or refuses.

    Installed: the fix stands in vendor/blocks, the override is in the lock on
    disk, the product is graded again and the run completes. Refused (the
    Factory's proof says the exam failed): the vendored Store copy is left
    byte for byte, the saved lock carries no override, the block's
    capabilities are listed not delivered in grade.json (marked, not
    stripped: the block's code, imports and routes stay as vendored) and the
    run still completes. Either way the writer is dispatched once and no
    grader REWORK is spent: the repair is a NOTE under the block's own
    allowance, never the writer's rework count."""
    from app.factory.build import store_override as so
    from app.factory.build import store_proposal as sp
    from app.factory.build.authority import BuildRole
    from app.factory.build.store_repair import GRADE_SECTION, STORE_REPAIR_KEY, RepairResult
    from app.factory.build.store_repair_exam import REPAIR_DIR

    monkeypatch.setattr(so, "_process_key", b"factory-test-key")
    holder: dict = {}

    def verdicts(runner):
        holder["runner"] = runner
        holder["block"], holder["bound"] = _bound_block(runner, 0)
        found = _store_verdict(_finding(holder["block"], "block.py"))
        return [found, _certified()] if proof_passes else [found]

    def cloner(ctx):
        ws, block = holder["runner"].workspace, holder["block"]
        vendored = ws / "vendor" / "blocks" / block
        _write_tree(vendored, _BROKEN_BLOCK)
        row = {"source": "store:origin-one", "commit": "c0ffee1", "path": f"vendor/blocks/{block}"}
        sp.save_lock(ws, {"blocks": {block: {**row, so.VENDORED_HASH_KEY: so.tree_hash(vendored)}}})
        return _fake_pass(ctx)

    def repairer(block_id, defects):
        runner = holder["runner"]
        staging = runner.workspace / REPAIR_DIR / block_id
        _write_tree(staging, _BROKEN_BLOCK)
        _write_tree(staging, _FIXED_BLOCK)
        exam_id = f"exam-{block_id}"
        so.write_proof(
            runner.exam_proofs(),
            so.ExamProof(exam_id, block_id, so.tree_hash(staging), proof_passes, 1.0),
        )
        return RepairResult(block_id, True, exam_id=exam_id, exam_score=1.0)

    def installer(result, defects):
        ws = holder["runner"].workspace
        lock = json.loads((ws / sp.LOCK_FILE).read_text(encoding="utf-8"))
        return sp.install_repair(
            ws, lock, result, proofs=holder["runner"].exam_proofs(), defects=defects
        )

    runner, writes = _runner(
        smoke_blueprint, tmp_path, monkeypatch, verdicts, repairer, installer=installer
    )
    runner.roles[BuildRole.CLONER] = cloner
    outcome = runner.run()

    assert outcome.ok, outcome.detail
    ws, block = runner.workspace, holder["block"]
    vendored = ws / "vendor" / "blocks" / block
    saved = json.loads((ws / sp.LOCK_FILE).read_text(encoding="utf-8"))
    grade = json.loads((ws / "grade.json").read_text(encoding="utf-8"))
    (row,) = grade[GRADE_SECTION]
    notes = _repair_notes(runner)
    assert [n.payload[STORE_REPAIR_KEY] for n in notes] == [block]
    assert len(writes) == 1 and _grader_reworks(runner) == []
    if proof_passes:
        staged = so.tree_hash(ws / REPAIR_DIR / block)
        assert so.tree_hash(vendored) == staged
        assert saved[so.OVERRIDES_KEY][block]["override_hash"] == staged
        assert (row["repaired"], row["not_delivered"]) == (True, [])
        assert [r["block_id"] for r in sp.filed_proposals(ws)] == [block]
    else:
        assert (vendored / "block.py").read_text(encoding="utf-8") == _BROKEN_BLOCK["block.py"]
        assert so.OVERRIDES_KEY not in saved
        assert (row["repaired"], tuple(row["not_delivered"])) == (False, holder["bound"])
        assert "not installed" in notes[0].payload["detail"]
        assert sp.filed_proposals(ws) == []


# -- F-3b: "repaired" is said only after a settling re-grade -------------------


@pytest.mark.parametrize(
    "repaired, settled, word",
    [
        (False, None, "repair failed"),
        (False, True, "repair failed"),
        (True, True, "repaired"),
        (True, False, "installed, but the re-grade did not settle the run"),
        (True, None, "installed, but the re-grade did not settle the run"),
    ],
)
def test_the_note_word_is_repaired_only_after_a_settling_regrade(repaired, settled, word):
    from app.factory.build.store_repair import BlockRepair, outcome_word

    repair = BlockRepair(
        "zorblat", ("crash",), True, repaired, reason="" if repaired else "repair failed"
    )
    assert outcome_word(repair, settled) == word


@pytest.mark.parametrize(
    "index, regraded, word, settled",
    [
        (0, "certified", "repaired", True),
        (1, "still-open", "installed, but the re-grade did not settle the run", False),
    ],
)
def test_the_repair_note_is_written_after_the_regrade_and_says_what_it_settled(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    index,
    regraded,
    word,
    settled,
):
    from app.factory.build.ledger import EventKind

    holder: dict = {}

    def verdicts(runner):
        holder["block"], _ = _bound_block(runner, index)
        first = _store_verdict(_finding(holder["block"]))
        return [first, _certified() if regraded == "certified" else first]

    runner, _ = _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, _passes)
    runner.run()
    events = [e for e in runner.ledger.events() if e.kind is EventKind.NOTE]
    regrade_at = [i for i, e in enumerate(events) if (e.payload or {}).get("store_regrade")]
    repair_at = [i for i, e in enumerate(events) if "store_defect" in (e.payload or {})]
    assert len(regrade_at) == 1 and len(repair_at) == 1
    assert regrade_at[0] < repair_at[0]
    note = events[repair_at[0]]
    assert note.detail == f"STORE DEFECT {holder['block']}: {word}"
    assert note.payload["regrade_settled"] is settled


@pytest.mark.parametrize(
    "files, partial",
    [
        ({"block.py": "def run(x):\n    return x\n"}, {"half.py": "PARTIAL = 1\n"}),
        ({"block.py": "VALUE = 1\n", "lib/u.py": "X = 2\n"}, None),
    ],
    ids=["half-copied", "moved-aside"],
)
def test_a_build_that_starts_over_a_killed_install_restores_the_vendored_copy(
    smoke_blueprint,  # noqa: F811
    tmp_path,
    monkeypatch,
    files,
    partial,
):
    from app.factory.build.ledger import EventKind
    from app.factory.build.store_override import VENDORED_HASH_KEY, tree_hash
    from app.factory.build.store_proposal import INSTALL_BACKUP_SUFFIX, LOCK_FILE

    runner, _ = _runner(smoke_blueprint, tmp_path, monkeypatch, lambda r: [_certified()], None)
    vendor = runner.workspace / "vendor" / "blocks"
    backup = vendor / ("zorblat" + INSTALL_BACKUP_SUFFIX)
    for rel, text in files.items():
        (backup / rel).parent.mkdir(parents=True, exist_ok=True)
        (backup / rel).write_text(text, encoding="utf-8")
    original = tree_hash(backup)
    for rel, text in (partial or {}).items():
        (vendor / "zorblat" / rel).parent.mkdir(parents=True, exist_ok=True)
        (vendor / "zorblat" / rel).write_text(text, encoding="utf-8")
    lock = {"blocks": {"zorblat": {"path": "vendor/blocks/zorblat", VENDORED_HASH_KEY: original}}}
    (runner.workspace / LOCK_FILE).write_text(json.dumps(lock), encoding="utf-8")

    runner.run()

    assert not backup.exists()
    assert tree_hash(vendor / "zorblat") == original
    recovered = [
        e.detail
        for e in runner.ledger.events()
        if e.kind is EventKind.NOTE and "store_install_recovery" in (e.payload or {})
    ]
    assert recovered == [
        "STORE INSTALL RECOVERED zorblat: a half-done install was undone; the vendored copy is back"
    ]
