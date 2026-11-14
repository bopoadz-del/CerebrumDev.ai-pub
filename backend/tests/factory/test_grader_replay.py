"""FG-3: the grader replay harness grades what a live build hands the grader.

These tests hold the HARNESS to account (its layer record is the Factory's
own, the grade goes through the runner's real call with the grader on, a
cassette miss is never a pass) and keep the true positives green: a defect
planted in the writer layer is still caught, a UI layer that did not run
never certifies.

The tests that are RED ON MASTER by design (a clean composed tree is not
certified; findings name Factory-written paths) live in
``red_grader_replay_clean_tree.py``, which pytest does not collect: the
FG-1 PR enables them and turns them green.

Nothing here names a product, a capability, a block, a stamped file or a
literal the grader is expected to report: each is read at run time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.factory import grader_replay as gr


@pytest.fixture(scope="module")
def composed(tmp_path_factory):
    """Each case composed ONCE per module (CLONER + stamps + writer layer);
    every test grades its own copy, because a grade writes into the tree."""
    from tests.factory.store_paths import store_root

    store = store_root()
    cache = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("FACTORY_CODER_ENABLED", "0")
        for case in gr.CASES:
            cache[case] = gr.compose_case(case, tmp_path_factory.mktemp(f"compose-{case}"), store)
    return cache


@pytest.fixture()
def tree(composed, tmp_path, request, monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    return composed[request.param].copy_to(tmp_path / "graded")


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_the_layer_record_is_the_factorys_own(tree):
    from app.factory.build.factory_owned import factory_owned_paths
    from app.factory.build.factory_receipt import _vendored_paths

    vendored = tree.paths(gr.VENDORED)
    locked = _vendored_paths(tree.root)
    assert vendored and locked
    # Every block the build's own lock records was written by the CLONER.
    unvendored = [v for v in locked if not any(
        p == v or p.startswith(v.rstrip("/") + "/") for p in vendored
    )]
    assert unvendored == []
    # Every Factory-owned path on disk was written by a Factory layer.
    on_disk = [p for p in factory_owned_paths() if (tree.root / p).is_file()]
    assert on_disk
    assert [p for p in on_disk if tree.layer_of(p) == gr.WRITER] == []
    # The writer layer is exactly the invented files.
    expected = {gr.handler_rel(c) for c in tree.capability_ids} | {
        "app/models.py",
        "app/store.py",
    }
    assert expected <= set(tree.paths(gr.WRITER))


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_the_grade_goes_through_the_runners_call_with_the_grader_on(tree, monkeypatch):
    from app.factory.build.grader import GRADER_GATE
    from app.factory.build.product_grader.layer2 import layer2_questions

    replay = gr.grade(tree, monkeypatch)
    assert replay.grade, "the runner's grader call wrote no grade record"
    assert [n for n in replay.notes if n["payload"].get("grader_gate") == GRADER_GATE]
    asked = [q["id"] for r in replay.cassette.requests for q in r["body"]["questions"]]
    import yaml

    blueprint = yaml.safe_load(tree.blueprint_path.read_text(encoding="utf-8"))
    assert sorted(asked) == sorted(q["id"] for q in layer2_questions(blueprint))
    assert replay.cassette.misses == []
    functions = replay.grade["layer1"]["functions"]
    assert functions["graded"] >= gr.function_floor()
    assert functions["failing"] == []


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_a_constant_handler_in_the_writer_layer_is_still_caught(tree, monkeypatch):
    target = tree.capability_ids[0]
    tree.write_writer_file(gr.handler_rel(target), "def handle(payload):\n    return {'ok': True}\n")
    replay = gr.grade(tree, monkeypatch)
    assert not replay.certified
    caught = [r for r in replay.verdict.reasons if r.get("capability") == target]
    assert caught, replay.items()


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_a_cassette_miss_is_an_open_question_never_a_pass(tree, monkeypatch):
    tape = gr.Cassette.load(gr.CASSETTE)
    fresh = tree.copy_to(tree.root.parent / "again")
    asked = gr.grade(tree, monkeypatch, cassette=tape).cassette.requests
    last = asked[-1]["body"]["questions"][-1]["id"]
    short = gr.Cassette({k: v for k, v in tape.answers.items() if k != last})
    replay = gr.grade(fresh, monkeypatch, cassette=short)
    assert replay.cassette.misses == [last]
    assert not replay.certified
    assert replay.grade["layer2"]["open_fails"]


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_a_ui_layer_that_did_not_run_never_certifies(tree, monkeypatch):
    reason = "UI run did not happen: replayed not-run shape"
    replay = gr.grade(tree, monkeypatch, ui_report=None, ui_not_run_reason=reason)
    assert not replay.certified
    assert reason in replay.verdict.ungraded


@pytest.mark.parametrize("module", ["grader_replay.py", "red_grader_replay_clean_tree.py"])
def test_the_harness_names_nothing_it_grades(module):
    """The harness reads every capability, block, stamped path and literal
    from the Factory at run time; its source carries none of them."""
    from app.factory.blueprint import load_blueprint
    from app.factory.build.factory_owned import factory_owned_paths

    source = (Path(__file__).parent / module).read_text(encoding="utf-8")
    named = []
    for path in gr.BLUEPRINTS:
        bp = load_blueprint(path)
        named += [c.id for c in bp.capabilities]
        named += [b for c in bp.capabilities for b in c.block_ids]
        named += [str(bp.product_id), str(bp.vertical)]
    named += [p for p in factory_owned_paths() if "/" in p]
    assert [n for n in dict.fromkeys(named) if n and (repr(n) in source or f'"{n}"' in source)] == []
