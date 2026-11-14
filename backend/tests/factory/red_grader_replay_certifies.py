"""FG-3 expected-failure proof that FG-1 does not clear: RED ON MASTER by design.

pytest collects ``test_*.py`` only, so this file does not run in the suite.
A tree composed by the Factory's own code plus an invented writer layer that
computes every capability from its declared inputs must CERTIFY; on master it
does not (the word-shape forbidden set, FG-4, and the UI layer, FG-2). The
PR that clears the last blocker renames this file so it is collected (not
skipped, not xfail).
"""

from __future__ import annotations

import pytest

from tests.factory import grader_replay as gr
from tests.factory.test_grader_replay_clean_tree import composed, tree  # noqa: F401,F811


@pytest.mark.parametrize("tree", list(gr.CASES), indirect=True)
def test_a_clean_composed_tree_certifies(tree, monkeypatch):  # noqa: F811
    replay = gr.grade(tree, monkeypatch)
    assert replay.certified, replay.items()
