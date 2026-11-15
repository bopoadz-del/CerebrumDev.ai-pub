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

    assert settle(verdict(), plan_capabilities=[], events=[], repairer=repairer) is None
    assert called == []


@pytest.mark.parametrize(
    "extra", ["FACTORY owes baked literals in plinket/stamped.py", "refused: layer2"]
)
def test_settle_leaves_a_verdict_with_an_owed_item_no_finding_explains(extra):
    from dataclasses import replace

    from app.factory.build.store_repair import settle

    base = _store_verdict(_finding("zorblat"))
    verdict = replace(base, items=base.items + (extra,), factory_owed=base.factory_owed + (extra,))
    assert settle(verdict, plan_capabilities=[], events=[], repairer=_passes) is None


@pytest.mark.parametrize("block", ["zorblat", "plinket"])
def test_a_passed_repair_delivers_every_capability(block):
    from app.factory.build.store_repair import settle

    caps = [_cap("alpha", block), _cap("beta", "frobnic")]
    out = settle(
        _store_verdict(_finding(block)), plan_capabilities=caps, events=[], repairer=_passes
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
        _store_verdict(_finding("zorblat")), plan_capabilities=caps, events=[], repairer=repairer
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
    out = settle(_store_verdict(*findings), plan_capabilities=[], events=[], repairer=repairer)
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


def _runner(smoke_blueprint, tmp_path, monkeypatch, verdicts, repairer):  # noqa: F811
    from app.factory.build import grader as grader_mod
    from app.factory.build.authority import BuildRole
    from app.factory.build.runner import RoleRunner

    monkeypatch.delenv(grader_mod.DISABLE_ENV, raising=False)
    roles = _fake_pass_harness(monkeypatch)
    writes: list = []
    roles[BuildRole.WRITER] = _counting_writer(writes)
    runner = RoleRunner(smoke_blueprint, tmp_path / "build", roles=roles)
    runner.store_repairer = repairer
    queue = list(verdicts(runner))

    def grade(**kwargs):
        return queue.pop(0) if len(queue) > 1 else queue[0]

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
        return [_store_verdict(_finding(holder["block"]))]

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
