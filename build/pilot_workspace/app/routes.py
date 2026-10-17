"""HTTP surface for the platform's kernels and capabilities.

Kernel routes publish each build role's job. Capability routes run
entirely in-process: the handler dispatches to a vendored block and
the result is persisted locally. No outbound call.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request

from app import jobs, store
from app.domain_ops import perform as perform_domain
from app.kernel_bridge import run_capability

router = APIRouter()


@router.get("/jobs")
def list_jobs() -> Dict[str, Any]:
    """Roster of every kernel job description."""
    return {"jobs": jobs.JOBS}


@router.get("/catalog")
def catalog() -> Dict[str, Any]:
    """COLLECTOR — Binding surveyor."""
    return jobs.CATALOG


@router.get("/inventory")
def inventory() -> Dict[str, Any]:
    """CLONER — Block stocker. Pinned vendor lock, read live."""
    return jobs.inventory()


@router.get("/capabilities")
def capabilities() -> Dict[str, Any]:
    """WRITER — Platform manufacturer. Capability HTTP surface."""
    return {"items": jobs.CAPABILITIES}


@router.get("/gates")
def gates() -> Dict[str, Any]:
    """TESTER — Acceptance inspector. Coverage only; does not run tests."""
    return jobs.GATES


@router.get("/provenance")
def provenance() -> Dict[str, Any]:
    """STORE_MANAGER — Store registrar. Clone register and provenance."""
    return jobs.provenance()


# --- analytics_surface (kernel execute_action template) ---
from app.actions import analytics_surface as _analytics_surface_action  # noqa: E402


def _analytics_surface_handle(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        result = _analytics_surface_action.handle(payload)
    except Exception as exc:  # Store/runtime refusal is not HTTP 500
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, dict):
        return {"ok": False, "error": f"handle() returned {type(result).__name__}"}
    return result


@router.post("/analytics_surface")
async def analytics_surface_create(payload: Dict[str, Any]) -> Dict[str, Any]:
    CAPABILITY_ID = "analytics_surface"
    handle = _analytics_surface_handle
    save = lambda record: store.save("analytics_surface", record)
    list_all = lambda: store.list_all("analytics_surface")
    if not isinstance(payload, dict):
        return {"ok": False, "error": "payload must be an object"}
    constraints = {'status': {'allowed_values': ['open', 'in_progress', 'closed']}, 'quantity': {'min': 0, 'max': 10000}}
    for name, rules in constraints.items():
        if name not in payload:
            continue
        value = payload[name]
        allowed = rules.get("allowed_values")
        if allowed is not None and value not in allowed:
            return {"ok": False,
                    "error": name + " must be one of: " + ", ".join(allowed)}
        low, high = rules.get("min"), rules.get("max")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if low is not None and value < low:
            return {"ok": False, "error": name + " is below the minimum"}
        if high is not None and value > high:
            return {"ok": False, "error": name + " is above the maximum"}
    result = await run_capability(CAPABILITY_ID, payload)
    if isinstance(result, dict) and result.get('status') != 'success':
        return {'ok': False,
                'error': result.get('error_message') or result.get('status'),
                'result': result}
    stored = save(payload)
    return {"ok": True, "capability": CAPABILITY_ID, "result": result,
            "stored": stored}


@router.get("/analytics_surface")
def analytics_surface_list(request: Request) -> Dict[str, Any]:
    # F7: filter/sort/page from the entity's own declared columns.
    # An unrecognised query field is refused rather than ignored --
    # silently dropping ?staus=open returns the whole table and looks
    # like a match.
    CONTROLS = {"limit", "offset", "sort", "order"}
    allowed = set(store.COLUMNS["analytics_surface"]) | CONTROLS
    given = dict(request.query_params)
    unknown = sorted(k for k in given if k not in allowed)
    if unknown:
        return {"ok": False,
                "error": "unknown query field(s): " + ", ".join(unknown)}
    try:
        return store.query("analytics_surface",
            filters={k: v for k, v in given.items() if k not in CONTROLS},
            sort=given.get("sort"),
            order=given.get("order", "asc"),
            limit=int(given.get("limit", 50)),
            offset=int(given.get("offset", 0)))
    except (store.QueryError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/analytics_surface/{item_id}")
def analytics_surface_get(item_id: int) -> Dict[str, Any]:
    record = store.get("analytics_surface", item_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    return record


@router.put("/analytics_surface/{item_id}")
async def analytics_surface_update(item_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    result = await perform_domain("update", "analytics_surface", {**(payload or {}), 'id': item_id})
    if result.get('status') != 'success':
        return {'ok': False,
                'error': result.get('error_message') or result.get('status'),
                'result': result}
    return {'ok': True, 'capability': "analytics_surface", 'result': result}


@router.delete("/analytics_surface/{item_id}")
async def analytics_surface_delete(item_id: int) -> Dict[str, Any]:
    result = await perform_domain("delete", "analytics_surface", {'id': item_id})
    if result.get('status') != 'success':
        return {'ok': False,
                'error': result.get('error_message') or result.get('status'),
                'result': result}
    return {'ok': True, 'capability': "analytics_surface", 'result': result}


# --- dashboard_surface (kernel execute_action template) ---
from app.actions import dashboard_surface as _dashboard_surface_action  # noqa: E402


def _dashboard_surface_handle(payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        result = _dashboard_surface_action.handle(payload)
    except Exception as exc:  # Store/runtime refusal is not HTTP 500
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(result, dict):
        return {"ok": False, "error": f"handle() returned {type(result).__name__}"}
    return result


@router.post("/dashboard_surface")
async def dashboard_surface_create(payload: Dict[str, Any]) -> Dict[str, Any]:
    CAPABILITY_ID = "dashboard_surface"
    handle = _dashboard_surface_handle
    save = lambda record: store.save("dashboard_surface", record)
    list_all = lambda: store.list_all("dashboard_surface")
    if not isinstance(payload, dict):
        return {"ok": False, "error": "payload must be an object"}
    constraints = {'status': {'allowed_values': ['open', 'in_progress', 'closed']}, 'quantity': {'min': 0, 'max': 10000}}
    for name, rules in constraints.items():
        if name not in payload:
            continue
        value = payload[name]
        allowed = rules.get("allowed_values")
        if allowed is not None and value not in allowed:
            return {"ok": False,
                    "error": name + " must be one of: " + ", ".join(allowed)}
        low, high = rules.get("min"), rules.get("max")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if low is not None and value < low:
            return {"ok": False, "error": name + " is below the minimum"}
        if high is not None and value > high:
            return {"ok": False, "error": name + " is above the maximum"}
    result = await run_capability(CAPABILITY_ID, payload)
    if isinstance(result, dict) and result.get('status') != 'success':
        return {'ok': False,
                'error': result.get('error_message') or result.get('status'),
                'result': result}
    stored = save(payload)
    return {"ok": True, "capability": CAPABILITY_ID, "result": result,
            "stored": stored}


@router.get("/dashboard_surface")
def dashboard_surface_list(request: Request) -> Dict[str, Any]:
    # F7: filter/sort/page from the entity's own declared columns.
    # An unrecognised query field is refused rather than ignored --
    # silently dropping ?staus=open returns the whole table and looks
    # like a match.
    CONTROLS = {"limit", "offset", "sort", "order"}
    allowed = set(store.COLUMNS["dashboard_surface"]) | CONTROLS
    given = dict(request.query_params)
    unknown = sorted(k for k in given if k not in allowed)
    if unknown:
        return {"ok": False,
                "error": "unknown query field(s): " + ", ".join(unknown)}
    try:
        return store.query("dashboard_surface",
            filters={k: v for k, v in given.items() if k not in CONTROLS},
            sort=given.get("sort"),
            order=given.get("order", "asc"),
            limit=int(given.get("limit", 50)),
            offset=int(given.get("offset", 0)))
    except (store.QueryError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}


@router.get("/dashboard_surface/{item_id}")
def dashboard_surface_get(item_id: int) -> Dict[str, Any]:
    record = store.get("dashboard_surface", item_id)
    if record is None:
        raise HTTPException(status_code=404, detail="not found")
    return record


@router.put("/dashboard_surface/{item_id}")
async def dashboard_surface_update(item_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
    result = await perform_domain("update", "dashboard_surface", {**(payload or {}), 'id': item_id})
    if result.get('status') != 'success':
        return {'ok': False,
                'error': result.get('error_message') or result.get('status'),
                'result': result}
    return {'ok': True, 'capability': "dashboard_surface", 'result': result}


@router.delete("/dashboard_surface/{item_id}")
async def dashboard_surface_delete(item_id: int) -> Dict[str, Any]:
    result = await perform_domain("delete", "dashboard_surface", {'id': item_id})
    if result.get('status') != 'success':
        return {'ok': False,
                'error': result.get('error_message') or result.get('status'),
                'result': result}
    return {'ok': True, 'capability': "dashboard_surface", 'result': result}


@router.post("/work_queue")
async def work_queue_enqueue(payload: Dict[str, Any]) -> Dict[str, Any]:
    result = await perform_domain(
        "enqueue", str((payload or {}).get("capability_id") or ""), payload or {}
    )
    if result.get('status') != 'success':
        return {'ok': False,
                'error': result.get('error_message') or result.get('status'),
                'result': result}
    return {'ok': True, 'result': result}


@router.post("/work_queue/{item_id}/process")
async def work_queue_process(item_id: int) -> Dict[str, Any]:
    result = await perform_domain("process", "", {"id": item_id})
    if result.get('status') != 'success':
        return {'ok': False,
                'error': result.get('error_message') or result.get('status'),
                'result': result}
    return {'ok': True, 'result': result}


@router.get("/work_queue")
def work_queue_list() -> Dict[str, Any]:
    from app import work_queue as _work_queue
    return {"items": _work_queue.list_all()}

