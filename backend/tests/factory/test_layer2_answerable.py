"""FG-6: Layer 2 is shown the writer's own files first, each question leads
with the files it is about, and only answerable questions are asked.

Every case runs on two inputs: two invented blueprints and two workspaces
whose names differ, so no answer can come from a baked name.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.build import grader
from app.factory.build.factory_owned import factory_owned_paths
from app.factory.build.product_grader import grade_record
from app.factory.build.product_grader import layer2 as l2
from app.factory.build.ui_surface import _safe

INPUTS = {"compiled_brief": "brief", "rendered_product": "plain render", "layer1": {"ok": True}}

#: Two invented blueprints: different capabilities, different screens.
BPS = [
    {
        "capabilities": [{"id": "tally-bins", "description": "Tally the bins."}],
        "ui_modules": ["Yard Board"],
    },
    {
        "capabilities": [{"id": "route_vans", "description": "Route the vans."}],
        "ui_modules": ["dispatch"],
    },
]


def _factory_py() -> str:
    """A Factory-owned path the render shows (a .py file), read from the one
    declared list -- never named here."""
    return next(p for p in factory_owned_paths() if p.endswith(".py"))


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _tree(root: Path, bp, *, screens: bool = True) -> Path:
    for c in bp["capabilities"]:
        _write(root, f"app/actions/{c['id'].replace('-', '_')}.py", "def handle(payload):\n    return payload\n")
    if screens:
        for m in bp["ui_modules"]:
            _write(root, f"frontend/src/modules/{_safe(m)}.tsx", "export default function S() { return null; }\n")
    _write(root, _factory_py(), "# factory\n" * 50)
    return root


def _blocks(view: str):
    return [line[len("## file: "):] for line in view.splitlines() if line.startswith("## file: ")]


# --- the render budget goes by ownership ---------------------------------


@pytest.mark.parametrize("writer_rel", ["zz_late/handler.py", "app/zeta/writer_mod.py"])
def test_the_writers_files_are_rendered_before_the_factorys(writer_rel, tmp_path):
    _write(tmp_path, writer_rel, "def f():\n    return 1\n")
    _write(tmp_path, _factory_py(), "# factory\n")
    order = _blocks(grader.render_product(tmp_path))
    assert order.index(writer_rel) < order.index(_factory_py())


@pytest.mark.parametrize("writer_rel", ["zz_late/handler.py", "app/zeta/writer_mod.py"])
def test_a_tight_budget_keeps_the_writers_file_and_drops_the_factorys(writer_rel, tmp_path, monkeypatch):
    body = "def f():\n    return 1\n"
    _write(tmp_path, writer_rel, body)
    _write(tmp_path, _factory_py(), body)
    # Room for exactly one of the two equal-sized blocks.
    one = len(f"## file: {max(writer_rel, _factory_py(), key=len)}\n{body}\n")
    monkeypatch.setattr(grader, "_TOTAL_CHARS", one)
    order = _blocks(grader.render_product(tmp_path))
    assert order == [writer_rel]


# --- each question leads with the files it is about -----------------------


@pytest.mark.parametrize("bp", BPS)
def test_each_screen_question_leads_with_its_screen_file(bp, tmp_path):
    root = _tree(tmp_path, bp)
    seen = {}

    def complete(messages):
        body = json.loads(messages[-1]["content"])
        for q in body["questions"]:
            seen[q["id"]] = body["rendered_product"]
        return "[]"

    l2.run_layer2(INPUTS, bp, complete, render=lambda stems: grader.render_product(root, first=stems))
    screens = [q for q in l2.layer2_questions(bp) if q["about"] and not q["capability"]]
    assert screens
    for q, m in zip(screens, bp["ui_modules"]):
        assert _blocks(seen[q["id"]])[0] == f"frontend/src/modules/{_safe(m)}.tsx"


@pytest.mark.parametrize("bp", BPS)
def test_every_question_is_its_own_call(bp):
    calls = []
    l2.run_layer2(INPUTS, bp, lambda m: calls.append(json.loads(m[-1]["content"])["questions"]) or "[]")
    assert all(len(c) == 1 for c in calls)
    assert len(calls) == len(l2.layer2_questions(bp))


# --- only answerable questions are asked -----------------------------------


@pytest.mark.parametrize("bp", BPS)
def test_a_screen_with_no_file_in_view_is_not_asked_and_blocks_as_unasked(bp, tmp_path):
    root = _tree(tmp_path, bp, screens=False)
    asked = []

    def complete(messages):
        qs = json.loads(messages[-1]["content"])["questions"]
        asked.extend(q["id"] for q in qs)
        return json.dumps([{"id": q["id"], "verdict": "PASS", "evidence": "quoted"} for q in qs])

    out = l2.run_layer2(INPUTS, bp, complete, render=lambda stems: grader.render_product(root, first=stems))
    screen_q = [q for q in l2.layer2_questions(bp) if q["about"] == _safe(bp["ui_modules"][0])]
    assert screen_q and screen_q[0]["id"] not in asked
    assert out["ran"] is True
    assert [u for u in out["unasked"] if screen_q[0]["text"] in u]
    assert screen_q[0]["text"] not in out["open_fails"]
    grade = grade_record.build_grade(
        product_id="p", functions={"ok": True}, ui={"ok": True}, layer2=out
    )
    assert grade["certified"] is False
    assert grade_record.open_fails(grade["layer2"]) == []


@pytest.mark.parametrize("bp", BPS)
def test_with_every_file_in_view_every_question_is_asked(bp, tmp_path):
    root = _tree(tmp_path, bp)
    asked = []

    def complete(messages):
        qs = json.loads(messages[-1]["content"])["questions"]
        asked.extend(q["id"] for q in qs)
        return json.dumps([{"id": q["id"], "verdict": "PASS", "evidence": "quoted"} for q in qs])

    out = l2.run_layer2(INPUTS, bp, complete, render=lambda stems: grader.render_product(root, first=stems))
    assert sorted(asked) == sorted(q["id"] for q in l2.layer2_questions(bp))
    assert out["unasked"] == []
    grade = grade_record.build_grade(product_id="p", functions={"ok": True}, ui={"ok": True}, layer2=out)
    assert grade["certified"] is True


@pytest.mark.parametrize(
    ("files", "about", "expected"),
    [
        ({"app/x.py": "body"}, "y_mod", True),
        ({"frontend/src/modules/y_mod.tsx": "body"}, "y_mod", False),
        ({"app/x.py": "import y_mod"}, "y_mod", False),
        ({}, "y_mod", True),
        ({"app/x.py": "body"}, "", False),
    ],
)
def test_unanswerable_reads_only_the_files_in_view(files, about, expected):
    view = grader.RenderedProduct("".join(f"{rel}\n{text}\n" for rel, text in files.items()), files)
    assert l2.unanswerable(view, about) is expected


@pytest.mark.parametrize("view", ["a fixed render that names y_mod nowhere", None])
def test_a_view_that_does_not_say_what_it_shows_is_always_asked(view):
    assert l2.unanswerable(view, "y_mod") is False


# --- the record and the reason classes ------------------------------------


@pytest.mark.parametrize("question", ["layer 2 not asked (no rendered file is about 'a'): q1", "layer 2 not asked: q2"])
def test_an_unasked_question_is_refused_by_the_store_gate(question, tmp_path):
    layer2 = {"ran": True, "verdicts": [{"question": "other", "verdict": "PASS", "evidence": "e"}], "open_fails": []}
    grade = grade_record.build_grade(
        product_id="p", functions={"ok": True}, ui={"ok": True}, layer2=dict(layer2, unasked=[question])
    )
    grade_record.write_grade_json(tmp_path, grade)
    assert grade_record.certification_refusal(tmp_path) == "product grade Layer 2 left 1 question(s) unasked: handover blocked"
    clean = grade_record.build_grade(product_id="p", functions={"ok": True}, ui={"ok": True}, layer2=layer2)
    grade_record.write_grade_json(tmp_path, clean)
    assert grade_record.certification_refusal(tmp_path) is None


@pytest.mark.parametrize("bad", [[1], "q1"])
def test_an_unasked_list_that_is_not_strings_is_not_a_record(bad, tmp_path):
    layer2 = {"ran": True, "verdicts": [{"question": "q", "verdict": "PASS", "evidence": "e"}], "open_fails": [], "unasked": bad}
    grade = {
        "schema": grade_record.SCHEMA, "product_id": "p", "certified": False, "next_tasks": [],
        "layer1": {"ok": True, "functions": {"ok": True}, "ui": {"ok": True}}, "layer2": layer2,
    }
    grade_record.write_grade_json(tmp_path, grade)
    assert grade_record.certification_refusal(tmp_path) == "product grade unreadable: build not certified"


@pytest.mark.parametrize("cid", ["tally-bins", "route_vans"])
def test_an_unasked_question_is_ungraded_never_a_layer2_fail(cid):
    text = f"layer 2 not asked (no rendered file is about 'x'): Does the product deliver capability '{cid}' as asked?"
    grade = {
        "layer1": {"functions": {"functions": [{"capability_id": cid, "ok": True}], "ungraded": []}, "ui": {"ran": True, "steps": []}},
        "layer2": {"ran": True, "verdicts": [], "open_fails": [], "unasked": [text]},
    }
    rows = grader.reason_classes(grade, [cid])
    assert {"capability": cid, "class": "ungraded", "layer": "layer2"} in rows
    assert not [r for r in rows if r["class"] == "layer2_fail"]
