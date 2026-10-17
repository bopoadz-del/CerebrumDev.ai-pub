"""Deterministic pre/post validation and rollback for L2 heals."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Dict, Tuple

from app.resident_engineer.heal.catalog import get_heal_state


class HealValidationError(RuntimeError):
    pass


PreCheck = Callable[[str, Dict[str, Any]], Tuple[bool, str]]
PostCheck = Callable[[str, Dict[str, Any], Dict[str, Any]], Tuple[bool, str]]


def default_pre_check(action_id: str, state: Dict[str, Any]) -> Tuple[bool, str]:
    if action_id == "resident.restart_worker" and state.get("worker_status") == "disabled":
        return False, "worker disabled — refuse restart"
    if action_id == "resident.rebuild_index" and int(state.get("index_version", 0)) < 0:
        return False, "corrupt index_version"
    return True, "ok"


def default_post_check(
    action_id: str, before: Dict[str, Any], after: Dict[str, Any]
) -> Tuple[bool, str]:
    if action_id == "resident.retry_ingestion":
        if after.get("ingestion_attempts", 0) <= before.get("ingestion_attempts", 0):
            return False, "ingestion_attempts did not increase"
    if action_id == "resident.restart_worker":
        if after.get("worker_status") != "restarted":
            return False, "worker_status not restarted"
    if action_id == "resident.rebuild_index":
        if after.get("index_version", 0) <= before.get("index_version", 0):
            return False, "index_version did not increase"
    if action_id == "resident.restore_config_default":
        if (after.get("config") or {}).get("mode") != "approved_default":
            return False, "config not restored to approved_default"
    return True, "ok"


def snapshot_state() -> Dict[str, Any]:
    return deepcopy(get_heal_state())


def restore_state(snapshot: Dict[str, Any]) -> None:
    """Rollback heal state to a prior snapshot (in-memory simulator)."""
    from app.resident_engineer.heal import catalog

    catalog._HEAL_STATE.clear()
    catalog._HEAL_STATE.update(deepcopy(snapshot))
