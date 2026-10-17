"""Local block dispatch. No network, no store, no callback.

The old generated platforms answered a capability by POSTing to the operator's
block store. This imports the block that was vendored into this repository at
build time and calls it in-process, which is what makes the platform runnable
offline and independent of the factory's uptime.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict

_VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "blocks"
_CACHE: Dict[str, Any] = {}


class BlockNotVendored(RuntimeError):
    """Asked for a block this platform does not carry."""


def load_block(block_id: str):
    if block_id in _CACHE:
        return _CACHE[block_id]
    path = _VENDOR / block_id / "block.py"
    if not path.is_file():
        raise BlockNotVendored(
            f"{block_id} is not vendored in this platform (looked in {path})"
        )
    spec = importlib.util.spec_from_file_location(f"vendored_{block_id}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _CACHE[block_id] = module
    return module


BLOCK_CONTRACTS: Dict[str, Any] = {'analytics': {'block_id': 'analytics'}, 'dashboard': {'block_id': 'dashboard'}}


class DispatchContractError(ValueError):
    """Caller payload failed the harvested contract. Do not invent fields."""


def _required_fields(block_id: str) -> list:
    contract = BLOCK_CONTRACTS.get(block_id) or {}
    required = list(contract.get("input_required_fields") or [])
    for item in contract.get("declared_inputs") or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name and item.get("required") and name not in required:
            required.append(name)
    return [field for field in required if field != "action"]


def _known_fields(block_id: str) -> set:
    """Closed field set from the harvested contract. Empty = no allow-list."""
    contract = BLOCK_CONTRACTS.get(block_id) or {}
    names: set = set()
    for item in contract.get("declared_inputs") or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name and name != "action":
            names.add(name)
    for field in contract.get("input_required_fields") or []:
        if field != "action":
            names.add(field)
    return names


def _adapt_input(block_id: str, payload: Any, action: str | None) -> Dict[str, Any]:
    """Pass the caller payload through. Never invent Store fields (F18)."""
    data = dict(payload) if isinstance(payload, dict) else {"value": payload}
    known = _known_fields(block_id)
    # Store blocks are action-dispatched: the domain record travels in the
    # block's declared ``input`` slot, with block params beside it. A handler
    # that passes the record flat gets every domain key refused as unknown --
    # measured on the warehouse `audit` capability, which declares
    # reference/status/quantity in its own model and had all three rejected.
    #
    # This is adaptation, not F18 fabrication. No value is invented and no
    # field is conjured to satisfy a validator: the caller's own record is
    # placed in the field the block declares for exactly it. If the caller
    # already supplied ``input``, nothing is moved.
    if known and "input" in known and "input" not in data:
        stray = {k: v for k, v in data.items() if k not in known}
        if stray:
            data = {k: v for k, v in data.items() if k in known}
            data["input"] = stray
    missing = [
        field for field in _required_fields(block_id)
        if field not in data or data[field] in (None, "")
    ]
    if missing:
        raise DispatchContractError(
            f"{block_id} missing required field(s): {', '.join(missing)}"
        )
    if known:
        unknown = sorted(str(key) for key in data if key not in known)
        if unknown:
            raise DispatchContractError(
                f"{block_id} unknown field(s): {', '.join(unknown)}"
            )
    return data


def _error_envelope(block_id: str, action: str | None, error: str) -> Dict[str, Any]:
    return {
        "status": "error",
        "block": block_id,
        "action": action,
        "error": error,
        "ok": False,
    }


def _force_utf8_stdio() -> None:
    """F13: a checkmark/emoji print must not become a charmap crash.

    Windows cp1252 consoles encode print() with the console codepage. A
    vendored block that prints one checkmark used to raise UnicodeEncodeError;
    execute() then swallowed that into a generic error envelope that looked
    like a domain refusal. Force UTF-8 on this process before the block runs.
    """
    import os
    import sys

    os.environ["PYTHONIOENCODING"] = "utf-8"
    os.environ["PYTHONUTF8"] = "1"
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError, AttributeError):
            continue


_force_utf8_stdio()


def execute(
    block_id: str,
    payload: Dict[str, Any],
    action: str | None = None,
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Run a vendored block locally and return its result envelope.

    Real Store blocks are action-dispatched: the domain data travels as
    ``input`` and the operation name as ``action`` (each block declares a
    default in its block.json). A call with no action reaches blocks that
    answer "Unknown action" -- pass the one the capability needs.

    Missing required fields and unknown fields/params become an error
    envelope. A block refusal (status=error) is returned as-is — never
    rewritten to ok.
    """
    module = load_block(block_id)
    run = getattr(module, "run", None)
    if run is None:
        raise BlockNotVendored(f"{block_id} exposes no run() entry point")
    contract = BLOCK_CONTRACTS.get(block_id) or {}
    declared = set()
    for item in contract.get("declared_inputs") or []:
        name = item.get("name") if isinstance(item, dict) else None
        if name:
            declared.add(name)
    if (
        params
        and (contract.get("declared_inputs") or contract.get("input_required_fields"))
    ):
        extra = sorted(
            str(key) for key in params if key not in declared and key != "action"
        )
        if extra:
            return _error_envelope(
                block_id, action, f"unknown param(s): {', '.join(extra)}"
            )
    kwargs = dict(params or {})
    if action is not None:
        kwargs["action"] = action
    try:
        adapted = _adapt_input(block_id, payload, action)
    except DispatchContractError as exc:
        return _error_envelope(block_id, action, str(exc))
    # A block-level failure comes back as data, not as an exception. The
    # Store's shim raises RuntimeError on an error envelope, which destroys
    # the diagnosis: a handler (and a failing test) sees "Input validation
    # failed" with no block name and no field list. Structural failures --
    # a block that is not vendored -- still raise above.
    _force_utf8_stdio()
    try:
        result = run(input=adapted, **kwargs)
    except BlockNotVendored:
        raise
    except UnicodeEncodeError as exc:
        return _error_envelope(
            block_id,
            action,
            f"UnicodeEncodeError on block stdout (encoding, not domain): {exc}",
        )
    except Exception as exc:
        return _error_envelope(
            block_id, action, f"{type(exc).__name__}: {exc}"
        )
    if isinstance(result, dict) and (
        result.get("status") == "error" or result.get("ok") is False
    ):
        refused = dict(result)
        refused.setdefault("status", "error")
        refused.setdefault("block", block_id)
        refused["ok"] = False
        return refused
    return result
