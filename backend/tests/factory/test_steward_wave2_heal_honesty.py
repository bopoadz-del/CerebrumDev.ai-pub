"""Wave 2: Resident Engineer simulated heal honesty tests."""

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
async def test_heal_requires_approval_id():
    with pytest.raises(HealRejected, match="approval_id required"):
        await execute_heal("resident.rebuild_index", confirmed=True)


@pytest.mark.asyncio
async def test_simulated_heal_never_reports_operational_success():
    result = await execute_heal(
        "resident.retry_ingestion",
        confirmed=True,
        approval_id="test-approval",
    )
    assert result["ok"] is False
    assert result["executed"] is False
    assert result["simulation"] is True
    assert result["result"]["ok"] is False
    assert result["result"]["simulation"] is True
    assert result["result"]["executed"] is False


@pytest.mark.asyncio
async def test_simulated_heal_updates_in_memory_state_without_claiming_execution():
    result = await execute_heal(
        "resident.rebuild_index",
        confirmed=True,
        approval_id="test-approval",
    )
    assert result["ok"] is False
    assert result["state"]["index_version"] == 2
    assert "simulation" in result["detail"].lower() or "simulation" in str(result["result"]).lower()
