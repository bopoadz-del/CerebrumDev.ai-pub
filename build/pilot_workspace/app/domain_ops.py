"""S12 domain outcomes performed through execute_action.

CRUD, list, queue process, refusal, idempotency, unauthorized, and
missing-field rejection are kernel ActionSpecs. HTTP ok:true is not
acceptance. LLM-authored route bodies are forbidden on this path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app import store, work_queue
from app.cerebrum_product_kernel.contract.models import (
    ActionContext,
    ActionOutcome,
    ActionSpec,
    ActionStatus,
)
from app.cerebrum_product_kernel.contract.runtime import execute_action

SPECS: Dict[str, Dict[str, Any]] = {'analytics_surface': {'entity': 'analytics_surface', 'fields': [{'name': 'reference', 'type': 'str', 'required': True}, {'name': 'status', 'type': 'str', 'required': True, 'allowed_values': ['open', 'in_progress', 'closed']}, {'name': 'quantity', 'type': 'int', 'required': False, 'min': 0, 'max': 10000}]}, 'dashboard_surface': {'entity': 'dashboard_surface', 'fields': [{'name': 'reference', 'type': 'str', 'required': True}, {'name': 'status', 'type': 'str', 'required': True, 'allowed_values': ['open', 'in_progress', 'closed']}, {'name': 'quantity', 'type': 'int', 'required': False, 'min': 0, 'max': 10000}]}}
DEFAULT_CAPABILITY = 'analytics_surface'
DEFAULT_ENTITY = 'analytics_surface'
SAMPLE = {'reference': 's10-row', 'status': 'open', 'quantity': 0}

WRITE_PERMISSION = 'product.write'
READ_PERMISSION = 'product.read'
PROCESS_PERMISSION = 'product.process'

OUTCOME_CREATE_PERSISTS = 'create_persists'
OUTCOME_READ_RETURNS_PERSISTED = 'read_returns_persisted'
OUTCOME_UPDATE_PERSISTS = 'update_persists'
OUTCOME_DELETE_PERSISTS = 'delete_persists'
OUTCOME_LIST_ONLY_PERSISTED = 'list_only_persisted'
OUTCOME_QUEUE_ITEM_PROCESSED = 'queue_item_processed'
OUTCOME_REFUSED_ACTION_ERRORS = 'refused_action_errors'
OUTCOME_IDEMPOTENT_DUPLICATE_SAFE = 'idempotent_duplicate_safe'
OUTCOME_UNAUTHORIZED_REJECTED = 'unauthorized_rejected'
OUTCOME_MISSING_FIELD_REJECTED = 'missing_field_rejected'

OUTCOMES = (
    OUTCOME_CREATE_PERSISTS,
    OUTCOME_READ_RETURNS_PERSISTED,
    OUTCOME_UPDATE_PERSISTS,
    OUTCOME_DELETE_PERSISTS,
    OUTCOME_LIST_ONLY_PERSISTED,
    OUTCOME_QUEUE_ITEM_PROCESSED,
    OUTCOME_REFUSED_ACTION_ERRORS,
    OUTCOME_IDEMPOTENT_DUPLICATE_SAFE,
    OUTCOME_UNAUTHORIZED_REJECTED,
    OUTCOME_MISSING_FIELD_REJECTED,
)

WRITE_OPS = frozenset({"create", "update", "delete", "enqueue", "refuse"})
READ_OPS = frozenset({"read", "list"})


def entity_of(capability_id: str) -> str:
    resolved = str(capability_id or DEFAULT_CAPABILITY)
    spec = SPECS.get(resolved)
    if spec is None:
        raise ValueError(f"unknown capability_id: {resolved}")
    return str(spec.get("entity") or resolved.replace("-", "_"))


def fields_of(capability_id: str) -> List[Dict[str, Any]]:
    resolved = str(capability_id or DEFAULT_CAPABILITY)
    spec = SPECS.get(resolved)
    if spec is None:
        raise ValueError(f"unknown capability_id: {resolved}")
    return list(spec.get("fields") or [])


def sample_payload(capability_id: str) -> Dict[str, Any]:
    if capability_id == DEFAULT_CAPABILITY and SAMPLE:
        return dict(SAMPLE)
    payload: Dict[str, Any] = {}
    for field in fields_of(capability_id):
        name = field["name"]
        allowed = field.get("allowed_values") or []
        ftype = field.get("type") or "str"
        if allowed:
            payload[name] = allowed[0]
        elif ftype == "int":
            payload[name] = int(field["min"]) if field.get("min") is not None else 1
        elif ftype == "float":
            payload[name] = float(field["min"]) if field.get("min") is not None else 1.5
        elif ftype == "bool":
            payload[name] = True
        else:
            payload[name] = "s12-row"
    return payload


def required_field_names(capability_id: str) -> List[str]:
    return [f["name"] for f in fields_of(capability_id) if f.get("required")]


def enum_field(capability_id: str) -> tuple[str, List[Any]] | None:
    for field in fields_of(capability_id):
        allowed = field.get("allowed_values") or []
        if len(allowed) >= 1:
            return field["name"], list(allowed)
    return None


def record_fields(capability_id: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    names = {f["name"] for f in fields_of(capability_id)}
    return {k: arguments[k] for k in names if k in arguments}


def _json_type(ftype: str) -> str:
    return {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}.get(
        ftype, "string"
    )


def input_schema_for(op: str, capability_id: str) -> Dict[str, Any]:
    properties: Dict[str, Any] = {
        "capability_id": {"type": "string"},
        "idempotency_key": {"type": "string"},
        "id": {"type": "integer"},
        "q": {"type": "string"},
        "payload": {"type": "object"},
        "reason": {"type": "string"},
    }
    required: List[str] = []
    if op in {"create", "update", "read", "delete", "list", "enqueue"}:
        required.append("capability_id")
    if op in {"read", "update", "delete"}:
        required.append("id")
    if op == "process":
        required = ["id"]
    if op == "refuse":
        required = ["reason"]
    if op in {"create", "update"}:
        for field in fields_of(capability_id):
            prop: Dict[str, Any] = {"type": _json_type(field.get("type") or "str")}
            if field.get("allowed_values"):
                prop["enum"] = list(field["allowed_values"])
            properties[field["name"]] = prop
            if op == "create" and field.get("required"):
                required.append(field["name"])
    return {"type": "object", "properties": properties, "required": required}


def authorized_context() -> ActionContext:
    return ActionContext(
        user_id="operator",
        tenant_id="local",
        organisation_id="local",
        project_id="local",
        permissions=[WRITE_PERMISSION, READ_PERMISSION, PROCESS_PERMISSION],
        allowed_domains=["product"],
    )


def unauthorized_context() -> ActionContext:
    return ActionContext(
        user_id="stranger",
        tenant_id="local",
        organisation_id="local",
        project_id="local",
        permissions=[],
        allowed_domains=["product"],
    )


def _permissions_for(op: str) -> List[str]:
    if op in WRITE_OPS:
        return [WRITE_PERMISSION]
    if op == "process":
        return [PROCESS_PERMISSION]
    return [READ_PERMISSION]


async def _handle_create(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    entity = entity_of(capability_id)
    key = arguments.get("idempotency_key")
    if key:
        hit = work_queue.recall(str(key))
        if hit:
            row = store.get(hit["entity"], int(hit["record_id"]))
            if row is not None:
                return ActionOutcome.success({"stored": row, "replayed": True})
    stored = store.save(entity, record_fields(capability_id, arguments))
    if key:
        work_queue.remember(str(key), entity, int(stored["id"]))
    return ActionOutcome.success({"stored": stored, "replayed": False})


async def _handle_read(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    row = store.get(entity_of(capability_id), int(arguments["id"]))
    if row is None:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_found",
            error_message="record not found",
        )
    return ActionOutcome.success({"stored": row})


async def _handle_update(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    entity = entity_of(capability_id)
    current = store.get(entity, int(arguments["id"]))
    if current is None:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_found",
            error_message="record not found",
        )
    merged = {**current, **record_fields(capability_id, arguments)}
    updated = store.update(entity, int(arguments["id"]), merged)
    if updated is None:
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="update_failed",
            error_message="update did not persist",
        )
    return ActionOutcome.success({"stored": updated})


async def _handle_delete(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    removed = store.delete(entity_of(capability_id), int(arguments["id"]))
    if not removed:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_found",
            error_message="record not found",
        )
    return ActionOutcome.success({"deleted": True, "id": int(arguments["id"])})


async def _handle_list(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    items = store.list_all(entity_of(capability_id))
    query = arguments.get("q")
    if query:
        needle = str(query)
        items = [item for item in items if needle in json_blob(item)]
    return ActionOutcome.success({"items": items})


async def _handle_enqueue(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    payload = arguments.get("payload")
    if not isinstance(payload, dict):
        payload = record_fields(capability_id, arguments) or sample_payload(capability_id)
    item = work_queue.enqueue(
        capability_id,
        payload,
        idempotency_key=arguments.get("idempotency_key"),
    )
    if item.get("status") != work_queue.PENDING:
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="hollow_queue",
            error_message="enqueue did not persist a pending item",
        )
    return ActionOutcome.success({"item": item})


async def _handle_process(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    item_id = int(arguments["id"])
    claimed = work_queue.claim_pending(item_id)
    if claimed is None:
        item = work_queue.get(item_id)
        if item is None:
            return ActionOutcome(
                status=ActionStatus.VALIDATION_ERROR,
                error_code="not_found",
                error_message="queue item not found",
            )
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_pending",
            error_message="queue item is not pending",
            output={"item": item},
        )
    from app.kernel_bridge import product_context, spec_for

    payload = claimed.get("payload") if isinstance(claimed.get("payload"), dict) else {}
    result = await execute_action(
        spec_for(claimed["capability_id"]),
        product_context(),
        payload,
    )
    status = (
        work_queue.PROCESSED
        if result.status == ActionStatus.SUCCESS
        else work_queue.FAILED
    )
    marked = work_queue.mark(
        int(claimed["id"]),
        status,
        result.to_dict(),
        from_status=work_queue.PROCESSING,
    )
    if marked is None or marked.get("status") in {work_queue.PENDING, work_queue.PROCESSING}:
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="hollow_queue",
            error_message="process did not change the persisted queue status",
        )
    return ActionOutcome.success({"item": marked, "result": result.to_dict()})


async def _handle_refuse(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    return ActionOutcome(
        status=ActionStatus.VALIDATION_ERROR,
        error_code="refused",
        error_message=str(arguments.get("reason") or "refused"),
    )


HANDLERS = {
    "create": _handle_create,
    "read": _handle_read,
    "update": _handle_update,
    "delete": _handle_delete,
    "list": _handle_list,
    "enqueue": _handle_enqueue,
    "process": _handle_process,
    "refuse": _handle_refuse,
}


def spec_for_op(op: str, capability_id: str) -> ActionSpec:
    if op not in HANDLERS:
        raise ValueError(f"unknown domain op: {op}")
    return ActionSpec(
        action_id=f"product.{op}",
        domain="product",
        name=op,
        description=f"S12 {op} for {capability_id}",
        input_schema=input_schema_for(op, capability_id),
        output_schema={},
        required_context=[],
        permissions=_permissions_for(op),
        read_only=op in READ_OPS,
        handler=HANDLERS[op],
    )


async def perform(
    op: str,
    capability_id: str,
    arguments: Optional[Dict[str, Any]] = None,
    *,
    context: Optional[ActionContext] = None,
) -> Dict[str, Any]:
    resolved = capability_id or DEFAULT_CAPABILITY
    if resolved not in SPECS:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="unknown_capability",
            error_message=f"unknown capability_id: {resolved}",
        ).to_dict()
    payload = dict(arguments or {})
    if op != "process" and op != "refuse":
        payload["capability_id"] = resolved
    result = await execute_action(
        spec_for_op(op, resolved),
        context or authorized_context(),
        payload,
    )
    return result.to_dict()


def json_blob(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, default=str)


def _ok_true(result: Dict[str, Any]) -> bool:
    return result.get("ok") is True or result.get("status") == "success"


def _record(status: str, **detail: Any) -> Dict[str, Any]:
    return {"status": status, **detail}


async def perform_all(capability_id: Optional[str] = None) -> Dict[str, Any]:
    """Perform every named outcome through execute_action. No HTTP ok:true."""
    cap = capability_id or DEFAULT_CAPABILITY
    entity = entity_of(cap)
    sample = sample_payload(cap)
    ctx = authorized_context()
    outcomes: Dict[str, Any] = {}

    created = await perform("create", cap, dict(sample), context=ctx)
    stored = (created.get("output") or {}).get("stored") or {}
    fetched = store.get(entity, stored["id"]) if stored.get("id") is not None else None
    if created.get("status") == "success" and fetched is not None:
        field_ok = all(fetched.get(k) == sample[k] for k in sample)
        outcomes[OUTCOME_CREATE_PERSISTS] = _record(
            "performed" if field_ok else "failed",
            id=stored.get("id"),
            fields_match=field_ok,
        )
    else:
        outcomes[OUTCOME_CREATE_PERSISTS] = _record(
            "failed", error=created.get("error_message") or created.get("status")
        )

    read = await perform("read", cap, {"id": stored.get("id")}, context=ctx)
    read_row = (read.get("output") or {}).get("stored") or {}
    if (
        read.get("status") == "success"
        and stored.get("id") is not None
        and read_row.get("id") == stored.get("id")
    ):
        outcomes[OUTCOME_READ_RETURNS_PERSISTED] = _record(
            "performed", id=read_row.get("id")
        )
    else:
        outcomes[OUTCOME_READ_RETURNS_PERSISTED] = _record(
            "failed", error=read.get("error_message") or read.get("status")
        )

    mutated = dict(sample)
    enum = enum_field(cap)
    if enum and len(enum[1]) > 1:
        mutated[enum[0]] = enum[1][1]
    elif "quantity" in mutated and isinstance(mutated["quantity"], int):
        mutated["quantity"] = int(mutated["quantity"]) + 1
    elif "reference" in mutated:
        mutated["reference"] = str(mutated["reference"]) + "-upd"
    else:
        first = next(iter(mutated), None)
        if first and isinstance(mutated[first], str):
            mutated[first] = str(mutated[first]) + "-upd"
    updated = await perform(
        "update", cap, {**mutated, "id": stored.get("id")}, context=ctx
    )
    after = store.get(entity, stored["id"]) if stored.get("id") is not None else None
    update_ok = (
        updated.get("status") == "success"
        and after is not None
        and all(after.get(k) == mutated[k] for k in mutated)
    )
    outcomes[OUTCOME_UPDATE_PERSISTS] = _record(
        "performed" if update_ok else "failed",
        error=None if update_ok else (updated.get("error_message") or "update did not persist"),
    )

    listed = await perform("list", cap, {}, context=ctx)
    listed_ids = {
        item.get("id")
        for item in (listed.get("output") or {}).get("items") or []
    }
    persisted_ids = {row["id"] for row in store.list_all(entity)}
    list_ok = (
        listed.get("status") == "success"
        and listed_ids == persisted_ids
        and stored.get("id") in listed_ids
    )
    outcomes[OUTCOME_LIST_ONLY_PERSISTED] = _record(
        "performed" if list_ok else "failed",
        listed=sorted(x for x in listed_ids if x is not None),
        persisted=sorted(persisted_ids),
    )

    enq = await perform("enqueue", cap, {"payload": dict(sample)}, context=ctx)
    item = (enq.get("output") or {}).get("item") or {}
    before_status = item.get("status")
    proc = await perform("process", cap, {"id": item.get("id")}, context=ctx)
    after_item = (proc.get("output") or {}).get("item") or work_queue.get(item["id"]) if item.get("id") else {}
    queue_ok = (
        enq.get("status") == "success"
        and before_status == work_queue.PENDING
        and proc.get("status") == "success"
        and after_item
        and after_item.get("status") in {work_queue.PROCESSED, work_queue.FAILED}
        and after_item.get("status") != before_status
        and after_item.get("result")
    )
    outcomes[OUTCOME_QUEUE_ITEM_PROCESSED] = _record(
        "performed" if queue_ok else "failed",
        before=before_status,
        after=(after_item or {}).get("status"),
        error=None if queue_ok else (proc.get("error_message") or "hollow queue"),
    )

    refused = await perform("refuse", cap, {"reason": "s12-refused"}, context=ctx)
    invalid = None
    if enum:
        bad = dict(sample)
        bad[enum[0]] = "__not_in_contract__"
        invalid = await perform("create", cap, bad, context=ctx)
    refuse_ok = (
        refused.get("status") != "success"
        and not _ok_true(refused)
        and refused.get("error_message")
        and (invalid is None or (invalid.get("status") != "success" and not _ok_true(invalid)))
    )
    outcomes[OUTCOME_REFUSED_ACTION_ERRORS] = _record(
        "performed" if refuse_ok else "failed",
        refused_status=refused.get("status"),
        invalid_status=(invalid or {}).get("status"),
    )

    key = "s12-idempotent"
    first = await perform(
        "create", cap, {**sample, "idempotency_key": key}, context=ctx
    )
    second = await perform(
        "create", cap, {**sample, "idempotency_key": key}, context=ctx
    )
    first_id = ((first.get("output") or {}).get("stored") or {}).get("id")
    second_id = ((second.get("output") or {}).get("stored") or {}).get("id")
    replayed = ((second.get("output") or {}).get("replayed")) is True
    idem_ok = (
        first.get("status") == "success"
        and second.get("status") == "success"
        and first_id is not None
        and first_id == second_id
        and replayed
    )
    outcomes[OUTCOME_IDEMPOTENT_DUPLICATE_SAFE] = _record(
        "performed" if idem_ok else "failed",
        first_id=first_id,
        second_id=second_id,
        replayed=replayed,
    )

    denied = await perform("create", cap, dict(sample), context=unauthorized_context())
    unauth_ok = (
        denied.get("status") == "permission_denied"
        and not _ok_true(denied)
        and denied.get("error_code") == "permission_denied"
    )
    outcomes[OUTCOME_UNAUTHORIZED_REJECTED] = _record(
        "performed" if unauth_ok else "failed",
        action_status=denied.get("status"),
        error_code=denied.get("error_code"),
    )

    required = required_field_names(cap)
    missing_name = required[0] if required else None
    if missing_name:
        incomplete = {k: v for k, v in sample.items() if k != missing_name}
        missing = await perform("create", cap, incomplete, context=ctx)
        missing_ok = (
            missing.get("status") == "validation_error"
            and not _ok_true(missing)
            and missing.get("error_code") == "invalid_input"
        )
        outcomes[OUTCOME_MISSING_FIELD_REJECTED] = _record(
            "performed" if missing_ok else "failed",
            field=missing_name,
            action_status=missing.get("status"),
            error=missing.get("error_message"),
        )
    else:
        outcomes[OUTCOME_MISSING_FIELD_REJECTED] = _record(
            "failed", error="capability contract declares no required field"
        )

    doomed = await perform("create", cap, dict(sample), context=ctx)
    doomed_id = ((doomed.get("output") or {}).get("stored") or {}).get("id")
    deleted = await perform("delete", cap, {"id": doomed_id}, context=ctx)
    gone = store.get(entity, doomed_id) if doomed_id is not None else "no-id"
    delete_ok = (
        doomed.get("status") == "success"
        and deleted.get("status") == "success"
        and gone is None
    )
    outcomes[OUTCOME_DELETE_PERSISTS] = _record(
        "performed" if delete_ok else "failed",
        id=doomed_id,
        remaining=gone,
    )

    failed = [name for name in OUTCOMES if outcomes.get(name, {}).get("status") != "performed"]
    return {
        "ok": not failed,
        "capability_id": cap,
        "entity": entity,
        "outcomes": outcomes,
        "failed": failed,
        "performed": [name for name in OUTCOMES if name not in failed],
        "kernel": "execute_action",
    }
