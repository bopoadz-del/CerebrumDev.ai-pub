"""U7: documents alone cannot flip PILOT_READY — a pilot cycle must have run."""

from __future__ import annotations

import json
from pathlib import Path

from app.factory.build.package import IDENTITY_REL, write_identity
from app.factory.build.pilot_cycle_proof import (
    WORKSPACE_ENV,
    inspect_pilot_cycle,
)
from app.factory.build.promotion import evaluate_promotion


def _ledger(root: Path, *events) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "build_ledger.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8"
    )
    return root


def test_no_workspace_is_unproven_not_assumed(monkeypatch):
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    result = inspect_pilot_cycle(None)

    assert result["ok"] is False
    assert WORKSPACE_ENV in str(result["reason"])
    assert result["succeeded_pilot_runs"] == 0


def test_a_code_cycle_success_does_not_count(tmp_path, monkeypatch):
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    ws = _ledger(
        tmp_path / "ws",
        {"kind": "RUN_SUCCEEDED", "payload": {"cycle": "code", "pilot_ready": False}},
    )
    result = inspect_pilot_cycle(ws)

    assert result["ok"] is False
    assert "cycle=pilot" in str(result["reason"])


def test_a_failed_pilot_run_does_not_count(tmp_path, monkeypatch):
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    ws = _ledger(
        tmp_path / "ws",
        {"kind": "RUN_FAILED", "payload": {"cycle": "pilot"}},
    )
    result = inspect_pilot_cycle(ws)
    assert result["ok"] is False


def test_a_succeeded_pilot_cycle_counts(tmp_path, monkeypatch):
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    ws = _stamped_pilot_workspace(tmp_path)
    _ledger(
        ws,
        {"kind": "RUN_SUCCEEDED", "payload": {"cycle": "code"}},
        {"kind": "RUN_SUCCEEDED", "payload": {"cycle": "pilot", "pilot_ready": True}},
    )
    result = inspect_pilot_cycle(ws)

    assert result["ok"] is True
    assert result["succeeded_pilot_runs"] == 1


def test_a_pilot_ledger_without_an_identity_stamp_is_refused(tmp_path, monkeypatch):
    """A bare ledger proves a cycle ran somewhere, not that it ran on this tree."""
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    ws = _ledger(
        tmp_path / "bare",
        {"kind": "RUN_SUCCEEDED", "payload": {"cycle": "pilot"}},
    )
    result = inspect_pilot_cycle(ws)

    assert result["ok"] is False
    assert result["succeeded_pilot_runs"] == 1
    assert "package_identity.json" in str(result["reason"])


def test_the_env_var_is_honoured(tmp_path, monkeypatch):
    ws = _stamped_pilot_workspace(tmp_path)
    monkeypatch.setenv(WORKSPACE_ENV, str(ws))
    assert inspect_pilot_cycle(None)["ok"] is True


def test_promotion_fails_when_no_pilot_cycle_is_proven(tmp_path, monkeypatch):
    """Stage evidence that all passes must still not promote on its own."""
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    stages = tmp_path / "stages"
    stages.mkdir()
    for stage in ("S10_data", "S11_deploy", "S12_domain"):
        body = {
            "stage": stage.split("_", 1)[0],
            "verdict": "PASS",
            "performed": {"drill": {"result": "PASS"}},
        }
        (stages / f"{stage}.json").write_text(json.dumps(body), encoding="utf-8")
        (stages / f"{stage}.reread.json").write_text(
            json.dumps({**body, "disagreements": []}), encoding="utf-8"
        )

    result = evaluate_promotion(stages, include_harvest=False)

    assert result["PILOT_READY"] is False
    assert result["pilot_cycle"]["ok"] is False
    assert "pilot_cycle" in str(result["first_failing_criterion"])

# -- identity binding ------------------------------------------------------


class _Stampable:
    """Minimal RoleWorkspace shape: write_identity needs .workspace + write_text."""

    def __init__(self, root: Path) -> None:
        self.workspace = root

    def write_text(self, rel, text: str) -> None:
        dest = self.workspace / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")


def _stamped_pilot_workspace(tmp_path: Path) -> Path:
    """A workspace with real payload, a pilot ledger, and stamped identity."""
    ws = tmp_path / "ws"
    (ws / "app").mkdir(parents=True)
    (ws / "app" / "routes.py").write_text("# payload\n", encoding="utf-8")
    write_identity(_Stampable(ws))
    _ledger(ws, {"kind": "RUN_SUCCEEDED", "payload": {"cycle": "pilot"}})
    return ws


def test_an_intact_stamped_workspace_is_accepted(tmp_path, monkeypatch):
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    result = inspect_pilot_cycle(_stamped_pilot_workspace(tmp_path))

    assert result["ok"] is True
    assert result["identity"]["matches"] is True


def test_a_tree_edited_after_the_cycle_is_refused(tmp_path, monkeypatch):
    """The ledger must describe the bytes that are actually there."""
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    ws = _stamped_pilot_workspace(tmp_path)
    (ws / "app" / "routes.py").write_text("# swapped after the cycle\n", encoding="utf-8")

    result = inspect_pilot_cycle(ws)

    assert result["ok"] is False
    assert result["identity"]["matches"] is False
    assert "does not match the identity" in str(result["reason"])


def test_a_workspace_with_no_identity_stamp_is_refused(tmp_path, monkeypatch):
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    ws = _stamped_pilot_workspace(tmp_path)
    (ws / IDENTITY_REL).unlink()

    result = inspect_pilot_cycle(ws)

    assert result["ok"] is False
    assert "no docs/package_identity.json" in str(result["reason"])


def test_appending_to_the_ledger_does_not_break_identity(tmp_path, monkeypatch):
    """build_ledger.jsonl is residue: a growing ledger is not a changed artifact."""
    monkeypatch.delenv(WORKSPACE_ENV, raising=False)
    ws = _stamped_pilot_workspace(tmp_path)
    with (ws / "build_ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('{"kind": "NOTE"}\n')

    assert inspect_pilot_cycle(ws)["ok"] is True
