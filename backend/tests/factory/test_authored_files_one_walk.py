"""The three authored-file views (count, count-or-none, path set) read one walk.

They were three copies of the same skip rule. These tests pin that every
view sees the same files on the same tree, that the skip rule holds for each
excluded kind, and that a walk raising part-way keeps each view's own
meaning: a partial count, no sample, a partial set.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build import codewhale_worker as worker_mod
from app.factory.build.codewhale_worker import (
    WORKER_CLI_HOME_DIR,
    agent_file_paths,
    authored_files_or_none,
    count_authored_files,
)


def _plant(root: Path, rels):
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")


@pytest.mark.parametrize(
    "authored,skipped",
    [
        (
            ["app.py", "pkg/mod.py"],
            ["vendor/blk/a.py", ".git/HEAD", "docs/writer_progress.jsonl"],
        ),
        (
            ["src/ui/index.html", "README.md", "docs/plan.md"],
            [
                "node_modules/x/i.js",
                "pkg/__pycache__/m.pyc",
                f"{WORKER_CLI_HOME_DIR}/state.json",
            ],
        ),
    ],
)
def test_every_view_sees_the_same_authored_files(tmp_path, authored, skipped):
    _plant(tmp_path, authored + skipped)
    assert count_authored_files(tmp_path) == len(authored)
    assert authored_files_or_none(tmp_path) == len(authored)
    assert agent_file_paths(tmp_path) == frozenset(authored)


@pytest.mark.parametrize(
    "authored,audit",
    [
        (["app.py"], ["docs/writer_prompt.txt"]),
        (["a.py", "b/c.py"], ["docs/writer_argv.json", "docs/writer_trace.json"]),
    ],
)
def test_only_the_path_set_drops_the_dispatch_audit(tmp_path, authored, audit):
    """The counts include the worker's audit files (the baseline taken before
    the CLI starts subtracts them); the path set leaves them out."""
    _plant(tmp_path, authored + audit)
    total = len(authored) + len(audit)
    assert count_authored_files(tmp_path) == total
    assert authored_files_or_none(tmp_path) == total
    assert agent_file_paths(tmp_path) == frozenset(authored)


@pytest.mark.parametrize("reached", [["a.py"], ["a.py", "b.py"]])
def test_a_walk_that_raises_keeps_each_views_meaning(monkeypatch, tmp_path, reached):
    _plant(tmp_path, reached + ["late.py"])

    def _raising_rglob(self, pattern):
        for rel in reached:
            yield tmp_path / rel
        raise OSError("tree changed under the walk")

    monkeypatch.setattr(Path, "rglob", _raising_rglob)
    assert count_authored_files(tmp_path) == len(reached)
    assert authored_files_or_none(tmp_path) is None
    assert agent_file_paths(tmp_path) == frozenset(reached)


@pytest.mark.parametrize("rels", [["one.py"], ["x/one.py", "x/two.py", "three.md"]])
def test_the_walk_yields_relative_posix_paths(tmp_path, rels):
    _plant(tmp_path, rels)
    assert sorted(worker_mod._authored_paths(tmp_path)) == sorted(rels)


@pytest.mark.parametrize("rels", [[], ["vendor/only.py", ".git/config"]])
def test_a_tree_with_nothing_authored_counts_zero(tmp_path, rels):
    _plant(tmp_path, rels)
    assert count_authored_files(tmp_path) == 0
    assert authored_files_or_none(tmp_path) == 0
    assert agent_file_paths(tmp_path) == frozenset()
