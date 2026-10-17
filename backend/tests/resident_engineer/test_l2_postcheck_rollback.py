"""Failed post-check rolls heal state back."""

from __future__ import annotations

import pytest

from app.resident_engineer.heal.catalog import get_heal_state, reset_heal_state_for_tests
from app.resident_engineer.heal.executor import execute_heal
from app.resident_engineer.heal.validate import HealValidationError


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
    reset_heal_state_for_tests()
    monkeypatch.setenv("RESIDENT_ENGINEER_ENABLED", "true")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))


@pytest.mark.asyncio
async def test_postcheck_failure_rolls_back():
    before = get_heal_state()
    with pytest.raises(HealValidationError, match="rolled back"):
        await execute_heal(
            "resident.rebuild_index",
            confirmed=True,
            approval_id="test-approval",
            force_post_check_fail=True,
        )
    after = get_heal_state()
    assert after["index_version"] == before["index_version"]
