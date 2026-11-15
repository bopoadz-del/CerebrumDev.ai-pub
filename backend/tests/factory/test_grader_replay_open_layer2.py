"""FG-5 on the FG-3 replay harness: an answered Layer-2 FAIL is open.

The tree is composed by the Factory's own code and graded through the
runner's real call. Paired cassettes, both built at test time from the
questions the grader itself asks: every question answered FAIL (each
question reaches the writer with the grader's quoted evidence and the
record is not certified), and every question answered PASS with quoted
evidence (no Layer-2 question in the handback). Nothing here names a
product, a capability or an expected value.
"""

from __future__ import annotations

import pytest

from tests.factory import grader_replay as gr
from tests.factory.test_grader_replay_clean_tree import composed, tree  # noqa: F401


def _questions(case):
    import yaml

    from app.factory.build.product_grader.layer2 import layer2_questions

    return layer2_questions(yaml.safe_load(case.blueprint_path.read_text(encoding="utf-8")))


def _all(questions, verdict):
    return gr.Cassette({q["id"]: {"verdict": verdict, "evidence": f"recorded {verdict} for {q['id']}"} for q in questions})


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
@pytest.mark.parametrize("verdict", ["FAIL", "PASS"])
def test_every_answered_layer2_fail_is_handed_back_with_its_evidence(tree, monkeypatch, verdict):  # noqa: F811
    questions = _questions(tree)
    replay = gr.grade(tree, monkeypatch, cassette=_all(questions, verdict))
    assert replay.cassette.misses == []
    writer_items = tuple(getattr(replay.verdict, "writer_items", ()) or ())
    handed = [i for i in writer_items if i.startswith("re-run writer on: ")]
    open_ = replay.grade["layer2"]["open_fails"]
    if verdict == "FAIL":
        assert not replay.certified
        assert replay.grade["certified"] is False
        assert open_ == [q["text"] for q in questions]
        for q in questions:
            assert any(q["text"] in i and f"recorded FAIL for {q['id']}" in i for i in handed), (q, handed)
        assert len([r for r in replay.verdict.reasons if r["class"] == "layer2_fail"]) == len(questions)
    else:
        assert open_ == []
        assert handed == []
        assert not [r for r in getattr(replay.verdict, "reasons", ()) or () if r["class"] == "layer2_fail"]
