"""Allowlisted L2 heal actions — registered, confirmation-gated, no shell."""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, List, Set

from app.cerebrum_product_kernel.contract.models import ActionSpec
from app.cerebrum_product_kernel.contract.registry import ActionRegistry

# Closed allowlist — only these action_ids may run in L2.
ALLOWLISTED_HEAL_ACTIONS: tuple[str, ...] = (
    "resident.retry_ingestion",
    "resident.restart_worker",
    "resident.rebuild_index",
    "resident.restore_config_default",
)

HealHandler = Callable[[Dict[str, Any], Dict[str, Any]], Awaitable[Dict[str, Any]]]

# In-memory heal state used by deterministic handlers + rollback.
_HEAL_STATE: Dict[str, Any] = {
    "ingestion_attempts": 0,
    "worker_status": "running",
    "index_version": 1,
    "config": {"mode": "approved_default"},
    "config_backup": None,
}


def reset_heal_state_for_tests() -> None:
    _HEAL_STATE.clear()
    _HEAL_STATE.update(
        {
            "ingestion_attempts": 0,
            "worker_status": "running",
            "index_version": 1,
            "config": {"mode": "approved_default"},
            "config_backup": None,
        }
    )


def get_heal_state() -> Dict[str, Any]:
    return dict(_HEAL_STATE)


async def _retry_ingestion(context: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    _HEAL_STATE["ingestion_attempts"] = int(_HEAL_STATE.get("ingestion_attempts", 0)) + 1
    return {
        "action": "resident.retry_ingestion",
        "attempts": _HEAL_STATE["ingestion_attempts"],
        "executed": False,
        "simulation": True,
        "ok": False,
        "detail": "In-memory simulation only — no real ingestion retry performed",
    }


async def _restart_worker(context: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    _HEAL_STATE["worker_status"] = "restarted"
    return {
        "action": "resident.restart_worker",
        "worker_status": "restarted",
        "executed": False,
        "simulation": True,
        "ok": False,
        "detail": "In-memory simulation only — no real worker restart performed",
    }


async def _rebuild_index(context: Dict[str, Any], arguments: Dict[str, Any]) -> Dict[str, Any]:
    _HEAL_STATE["index_version"] = int(_HEAL_STATE.get("index_version", 1)) + 1
    return {
        "action": "resident.rebuild_index",
        "index_version": _HEAL_STATE["index_version"],
        "executed": False,
        "simulation": True,
        "ok": False,
        "detail": "In-memory simulation only — no real index rebuild performed",
    }


async def _restore_config_default(
    context: Dict[str, Any], arguments: Dict[str, Any]
) -> Dict[str, Any]:
    current = dict(_HEAL_STATE.get("config") or {})
    _HEAL_STATE["config_backup"] = current
    _HEAL_STATE["config"] = {"mode": "approved_default"}
    return {
        "action": "resident.restore_config_default",
        "config": dict(_HEAL_STATE["config"]),
        "previous": current,
        "executed": False,
        "simulation": True,
        "ok": False,
        "detail": "In-memory simulation only — no real config restore performed",
    }


_HANDLERS: Dict[str, HealHandler] = {
    "resident.retry_ingestion": _retry_ingestion,
    "resident.restart_worker": _restart_worker,
    "resident.rebuild_index": _rebuild_index,
    "resident.restore_config_default": _restore_config_default,
}


def _spec(action_id: str, name: str, description: str, handler: HealHandler) -> ActionSpec:
    return ActionSpec(
        action_id=action_id,
        domain="resident",
        name=name,
        description=description,
        version="1.0.0",
        kit_version="1.0.0",
        input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        output_schema={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "executed": {"type": "boolean"},
                "simulation": {"type": "boolean"},
                "ok": {"type": "boolean"},
            },
            "required": ["action", "executed", "simulation", "ok"],
        },
        required_context=["tenant_id", "user_id"],
        permissions=["resident:heal"],
        risk_classification="medium",
        read_only=False,
        confirmation_required=True,
        handler=handler,
    )


def build_heal_action_specs() -> List[ActionSpec]:
    return [
        _spec(
            "resident.retry_ingestion",
            "Retry ingestion",
            "Retry a failed ingestion job via registered heal path (no shell).",
            _retry_ingestion,
        ),
        _spec(
            "resident.restart_worker",
            "Restart worker",
            "Restart the allowlisted worker process handle (simulated; no shell).",
            _restart_worker,
        ),
        _spec(
            "resident.rebuild_index",
            "Rebuild index",
            "Rebuild the search index via registered heal path (no shell).",
            _rebuild_index,
        ),
        _spec(
            "resident.restore_config_default",
            "Restore approved config default",
            "Restore the last approved default configuration (no shell).",
            _restore_config_default,
        ),
    ]


def build_heal_registry() -> ActionRegistry:
    reg = ActionRegistry()
    reg.register_all(build_heal_action_specs())
    return reg


def is_allowlisted(action_id: str) -> bool:
    return action_id in ALLOWLISTED_HEAL_ACTIONS


def allowlist_set() -> Set[str]:
    return set(ALLOWLISTED_HEAL_ACTIONS)
