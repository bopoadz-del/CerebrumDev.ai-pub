"""U6: the write path exists, refuses unsafe checkouts, and never pushes.

Every guard here is checked by making it fail, not by asserting it is
configured. A write path whose safety rails are only declared is the
advisory-gate shape this pipeline exists to remove.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.factory.build.harvest import evaluate_harvest
from app.factory.build.store_write import (
    BLOCKS_REPO,
    PROTECTED_BRANCHES,
    StoreWriteError,
    checkout_is_writable,
    execute_harvest,
    plan_harvest,
    store_write_capability,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _blocks_checkout(tmp_path: Path, *, diverging: bool = True) -> Path:
    """A miniature Cerebrum-Blocks: one block whose class out-declares its manifest."""
    root = tmp_path / "Cerebrum-Blocks"
    reg = root / "block_registry" / "widget"
    mods = root / "app" / "blocks"
    reg.mkdir(parents=True)
    mods.mkdir(parents=True)

    schema = [{"name": "action", "label": "Action", "widget": "select"}]
    if not diverging:
        schema.append({"name": "input", "label": "Input", "widget": "json"})
    (reg / "block.json").write_text(
        json.dumps({"id": "widget", "ui_schema": schema}), encoding="utf-8"
    )
    (mods / "widget.py").write_text(
        "class Widget:\n"
        "    ui_schema = {'input': {'type': 'json'}, 'params': []}\n",
        encoding="utf-8",
    )

    _git(root, "init", "-q", "-b", "main")
    _git(root, "remote", "add", "origin", f"https://github.com/{BLOCKS_REPO}.git")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init")
    return root


# -- the capability is real, not declared ----------------------------------


def test_the_write_path_reports_itself_as_implemented():
    """harvest.py used to hardcode store_write_exists = False."""
    cap = store_write_capability()
    assert cap["implemented"] is True
    assert cap["writes_to_default_branch"] is False
    assert cap["pushes"] is False


# -- refusals, each proven by causing it -----------------------------------


def test_a_dirty_checkout_is_refused(tmp_path):
    root = _blocks_checkout(tmp_path)
    (root / "block_registry" / "widget" / "block.json").write_text(
        '{"id": "widget", "tampered": true}', encoding="utf-8"
    )
    state = checkout_is_writable(root)

    assert state["writable"] is False
    assert "uncommitted changes" in state["reason"]


def test_a_checkout_pointing_elsewhere_is_refused(tmp_path):
    root = _blocks_checkout(tmp_path)
    _git(root, "remote", "set-url", "origin", "https://github.com/someone/else.git")

    state = checkout_is_writable(root)
    assert state["writable"] is False
    assert BLOCKS_REPO in state["reason"]


def test_a_missing_checkout_is_refused():
    state = checkout_is_writable(None)
    assert state["writable"] is False
    assert "no Cerebrum-Blocks checkout" in state["reason"]


def test_harvesting_onto_a_protected_branch_raises(tmp_path):
    root = _blocks_checkout(tmp_path)
    plan = plan_harvest(root)
    for protected in sorted(PROTECTED_BRANCHES):
        with pytest.raises(StoreWriteError, match="protected branch"):
            execute_harvest(root, plan, apply=True, branch=protected)


# -- planning and writing ---------------------------------------------------


def test_the_plan_is_computed_from_the_survey_not_hand_listed(tmp_path):
    root = _blocks_checkout(tmp_path)
    plan = plan_harvest(root)

    assert plan["finding"] == "F16"
    assert plan["edit_count"] == 1
    assert plan["edits"][0]["block_id"] == "widget"
    assert plan["edits"][0]["fields"] == ["input"]


def test_a_dry_run_writes_nothing(tmp_path):
    root = _blocks_checkout(tmp_path)
    before = (root / "block_registry" / "widget" / "block.json").read_text(encoding="utf-8")

    result = execute_harvest(root, plan_harvest(root), apply=False)

    assert result["applied"] is False
    assert result["would_write"] == ["block_registry/widget/block.json"]
    assert (root / "block_registry" / "widget" / "block.json").read_text(
        encoding="utf-8"
    ) == before


def test_apply_commits_to_a_branch_and_leaves_main_untouched(tmp_path):
    root = _blocks_checkout(tmp_path)
    main_before = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()

    result = execute_harvest(root, plan_harvest(root), apply=True, stamp="t")

    assert result["applied"] is True
    assert result["branch"].startswith("factory-harvest/")
    assert result["branch"] not in PROTECTED_BRANCHES
    assert result["commit"]
    assert result["pushed"] is False

    main_after = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "main"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert main_after == main_before, "main must be untouched"

    current = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert current == "main", "the checkout is handed back on its original branch"


def test_the_harvest_resolves_the_divergence_it_was_computed_from(tmp_path):
    """The loop closes: after harvesting, the survey finds nothing."""
    from app.factory.build.ui_schema import survey

    root = _blocks_checkout(tmp_path)
    execute_harvest(root, plan_harvest(root), apply=True, stamp="t")
    _git(root, "checkout", "-q", "factory-harvest/f16-t")

    remaining = survey(root / "block_registry", root / "app" / "blocks")
    assert remaining["diverging"] == []
    assert plan_harvest(root)["edit_count"] == 0


def test_a_second_harvest_finds_nothing_to_do(tmp_path):
    root = _blocks_checkout(tmp_path, diverging=False)
    result = execute_harvest(root, plan_harvest(root), apply=True)

    assert result["applied"] is False
    assert "nothing to harvest" in result["reason"]


# -- authorization ----------------------------------------------------------


def test_harvest_stays_blocked_without_the_committed_marker(tmp_path):
    """The write path existing is not permission to use it."""
    result = evaluate_harvest(_blocks_checkout(tmp_path))

    assert result["verdict"] == "BLOCKED"
    assert result["authorized_write_path"] is False
    assert "NOT authorized" in result["reason"]
    # The reason must say the mechanism exists, so the block is not mistaken
    # for the old "unbuilt" one.
    assert "store_write" in result["reason"]
