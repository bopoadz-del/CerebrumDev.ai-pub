"""FG-3: a clean composed tree certifies.

A tree composed by the Factory's own code plus an invented writer layer that
computes every capability from its declared inputs must CERTIFY. This was the
red-on-master proof for FG-4 (word-shape forbidden set) and FG-2 (UI layer);
it is collected now that those fixes have landed (not skipped, not xfail).
"""

from __future__ import annotations

import pytest

from tests.factory import grader_replay as gr
from tests.factory.test_grader_replay_clean_tree import composed, tree  # noqa: F401,F811


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_a_clean_composed_tree_certifies(tree, monkeypatch):  # noqa: F811
    replay = gr.grade(tree, monkeypatch)
    assert replay.certified, replay.items()
