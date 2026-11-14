"""FG-3 expected-failure proof: RED ON MASTER by design, not collected yet.

pytest collects ``test_*.py`` only, so this file does not run in the suite
today. It is the acceptance bar the grader fixes are held to: the FG-1 PR
renames it to ``test_grader_replay_clean_tree.py`` (it is not skipped and
not xfail), and each fix turns its cases green.

What it proves on master: a tree composed by the Factory's own code (the
CLONER's vendoring from the pinned Store, every registered stamp) plus an
invented writer layer that computes every capability from its declared
inputs, graded through ``RoleRunner._writer_grader`` with the grader on, is
NOT_CERTIFIED -- and the findings that name a file name Factory-written
paths, which no writer can change.

Nothing here names a product, capability, block, stamped file or literal.
"""

from __future__ import annotations

import pytest

from tests.factory import grader_replay as gr


@pytest.fixture(scope="module")
def composed(tmp_path_factory):
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
def test_a_clean_composed_tree_certifies(tree, monkeypatch):
    replay = gr.grade(tree, monkeypatch)
    assert replay.certified, replay.items()


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_no_finding_names_a_factory_written_path(tree, monkeypatch):
    replay = gr.grade(tree, monkeypatch)
    factory_named = {f: tree.layer_of(f) for f in replay.finding_files() if tree.layer_of(f) != gr.WRITER}
    assert factory_named == {}, replay.items()
