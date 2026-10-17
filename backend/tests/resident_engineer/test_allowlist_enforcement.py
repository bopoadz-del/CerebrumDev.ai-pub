"""Forbidden heal actions are rejected."""

from __future__ import annotations

import pytest

from app.resident_engineer.heal.catalog import reset_heal_state_for_tests
from app.resident_engineer.heal.executor import HealRejected, execute_heal


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    reset_heal_state_for_tests()
    monkeypatch.setenv("RESIDENT_ENGINEER_ENABLED", "true")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))


@pytest.mark.asyncio
async def test_forbidden_action_rejected():
    with pytest.raises(HealRejected, match="not allowlisted"):
        await execute_heal("resident.rm_rf_root", confirmed=True)


@pytest.mark.asyncio
async def test_unconfirmed_allowlisted_rejected():
    with pytest.raises(HealRejected, match="confirmation required"):
        await execute_heal("resident.rebuild_index", confirmed=False, approval_id="test-approval")


@pytest.mark.asyncio
async def test_flag_off_rejects():
    import os

    os.environ["RESIDENT_ENGINEER_ENABLED"] = "false"
    with pytest.raises(HealRejected, match="RESIDENT_ENGINEER_ENABLED"):
        await execute_heal(
            "resident.rebuild_index",
            confirmed=True,
            approval_id="test-approval",
        )


@pytest.mark.asyncio
async def test_allowlisted_heal_returns_simulation_not_success():
    result = await execute_heal(
        "resident.retry_ingestion",
        confirmed=True,
        approval_id="test-approval",
    )
    assert result["ok"] is False
    assert result["executed"] is False
    assert result["simulation"] is True
    assert result["result"]["attempts"] == 1
