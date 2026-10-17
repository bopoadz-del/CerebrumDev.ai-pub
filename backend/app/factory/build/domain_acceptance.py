"""S12 domain acceptance emitters for RoleRunner products.

Ten named business outcomes must be PERFORMED through the shipped kernel
(``cerebrum_product_kernel`` / ``execute_action``). Unconditional HTTP
``ok: true`` is F1. Outcomes are capability/contract driven so they apply
to any RoleRunner product, not one vertical.

LotDesk-class fixtures fail this gate (always-200, missing update/delete,
hollow queue). The fixture is inspected, never patched.
"""

from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.factory.build import probe_set
from app.factory.build.data_lifecycle import (
    IDEMPOTENCY_TABLE,
    WORK_QUEUE_TABLE,
    sample_for_spec,
)
from app.factory.build.lotdesk_gate import inspect_path, resolve_lotdesk_fixture

WRITE_PERMISSION = "product.write"
READ_PERMISSION = "product.read"
PROCESS_PERMISSION = "product.process"

OUTCOME_CREATE_PERSISTS = "create_persists"
OUTCOME_READ_RETURNS_PERSISTED = "read_returns_persisted"
OUTCOME_UPDATE_PERSISTS = "update_persists"
OUTCOME_DELETE_PERSISTS = "delete_persists"
OUTCOME_LIST_ONLY_PERSISTED = "list_only_persisted"
OUTCOME_QUEUE_ITEM_PROCESSED = "queue_item_processed"
OUTCOME_REFUSED_ACTION_ERRORS = "refused_action_errors"
OUTCOME_IDEMPOTENT_DUPLICATE_SAFE = "idempotent_duplicate_safe"
OUTCOME_UNAUTHORIZED_REJECTED = "unauthorized_rejected"
OUTCOME_MISSING_FIELD_REJECTED = "missing_field_rejected"

OUTCOMES: tuple[str, ...] = (
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

#: What each outcome performs and which defects it closes -- data in the
#: probe set, so the defects an outcome closes are never written here.
OUTCOME_CATALOG: tuple[Dict[str, str], ...] = probe_set.acceptance_outcomes()
assert tuple(item["id"] for item in OUTCOME_CATALOG) == OUTCOMES, "probe set outcomes drifted"


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


def compact_specs(specs: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Capability contract used by the generated domain ops (no coder metadata)."""
    out: Dict[str, Dict[str, Any]] = {}
    for cap_id, spec in specs.items():
        fields = []
        for field in spec.get("fields") or []:
            item = {
                "name": field["name"],
                "type": field.get("type") or "str",
                "required": bool(field.get("required")),
            }
            if field.get("allowed_values"):
                item["allowed_values"] = list(field["allowed_values"])
            if field.get("min") is not None:
                item["min"] = field["min"]
            if field.get("max") is not None:
                item["max"] = field["max"]
            fields.append(item)
        out[cap_id] = {
            "entity": spec.get("entity") or cap_id.replace("-", "_"),
            "fields": fields,
        }
    return out


def first_capability_id(specs: Dict[str, Dict[str, Any]]) -> str:
    if not specs:
        return ""
    return sorted(specs)[0]


def render_work_queue() -> str:
    """The persisted work queue, as SQLAlchemy Core over the declared tables.

    No SQL text is assembled from identifiers (bandit B608 failed
    audit_clean on every product when it was): statements are built from
    table/column objects and compiled for the backend app.db is on.
    """
    return f'''"""Persisted work queue processed through execute_action.

Enqueue writes a pending row. Process runs the capability through the
vendored kernel and records processed|failed plus the ActionResult.
A handler that returns ok without changing status is a hollow queue (F5).

Statements are SQLAlchemy Core over the declared queue and idempotency
tables, compiled for whichever backend app.db is on (SQLite by default,
Postgres when DATABASE_URL is set). No SQL text is assembled from names.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import sqlalchemy as sa
from sqlalchemy.dialects import sqlite as _sqlite_dialect

from app import db as _db

TABLE = {WORK_QUEUE_TABLE!r}
IDEMPOTENCY = {IDEMPOTENCY_TABLE!r}
PENDING = "pending"
PROCESSING = "processing"
PROCESSED = "processed"
FAILED = "failed"

_QUEUE = sa.table(
    TABLE,
    sa.column("id"),
    sa.column("capability_id"),
    sa.column("tenant_id"),
    sa.column("payload"),
    sa.column("status"),
    sa.column("result"),
    sa.column("idempotency_key"),
)
_KEYS = sa.table(IDEMPOTENCY, sa.column("key"), sa.column("entity"), sa.column("record_id"))
_SQLITE = _sqlite_dialect.dialect(paramstyle="qmark")


def _execute(conn: Any, statement: Any) -> Any:
    """Run one Core statement on either backend's connection."""
    if _db.is_postgres():
        return conn.execute(statement)
    compiled = statement.compile(dialect=_SQLITE)
    params = [compiled.params[name] for name in (compiled.positiontup or ())]
    return conn.execute(str(compiled), params)


def enqueue(
    capability_id: str,
    payload: Dict[str, Any],
    *,
    idempotency_key: str | None = None,
    tenant_id: str,
) -> Dict[str, Any]:
    statement = sa.insert(_QUEUE).values(
        capability_id=capability_id,
        tenant_id=tenant_id,
        payload=json.dumps(payload, sort_keys=True),
        status=PENDING,
        result=None,
        idempotency_key=idempotency_key,
    )
    conn = _db.connect()
    try:
        if _db.is_postgres():
            item_id = int(_execute(conn, statement.returning(_QUEUE.c.id)).scalar_one())
        else:
            item_id = int(_execute(conn, statement).lastrowid)
        conn.commit()
    finally:
        conn.close()
    item = get(item_id)
    if item is None:
        raise RuntimeError("work_queue enqueue did not persist")
    return item


def _as_item_id(item_id: Any) -> int:
    """Queue ids are integers. JSON / sqlite / FastAPI may hand us a str."""
    return int(item_id)


def get(item_id: int, *, tenant_id: str | None = None) -> Dict[str, Any] | None:
    """One queue item. With ``tenant_id``, only that tenant's: another
    tenant's item reads as absent -- a 404, never someone else's work."""
    item_id = _as_item_id(item_id)
    statement = sa.select(_QUEUE).where(_QUEUE.c.id == item_id)
    if tenant_id is not None:
        statement = statement.where(_QUEUE.c.tenant_id == tenant_id)
    conn = _db.connect()
    try:
        row = _execute(conn, statement).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def list_all(*, tenant_id: str) -> List[Dict[str, Any]]:
    """The caller's queue. Required, never defaulted: an unscoped list
    returned every tenant's items to any authenticated caller."""
    statement = (
        sa.select(_QUEUE).where(_QUEUE.c.tenant_id == tenant_id).order_by(_QUEUE.c.id)
    )
    conn = _db.connect()
    try:
        return [_row(r) for r in _execute(conn, statement).fetchall()]
    finally:
        conn.close()


def claim_pending(item_id: int, *, tenant_id: str) -> Dict[str, Any] | None:
    """Claim a pending item the caller's tenant owns. Another tenant's id
    claims nothing -- it used to claim anything, and processing then ran
    that item as its owner on a stranger's request."""
    item_id = _as_item_id(item_id)
    statement = (
        sa.update(_QUEUE)
        .where(
            _QUEUE.c.id == item_id,
            _QUEUE.c.status == PENDING,
            _QUEUE.c.tenant_id == tenant_id,
        )
        .values(status=PROCESSING)
    )
    conn = _db.connect()
    try:
        changed = _execute(conn, statement).rowcount
        conn.commit()
        if changed == 0:
            return None
    finally:
        conn.close()
    return get(item_id, tenant_id=tenant_id)


def mark(
    item_id: int,
    status: str,
    result: Dict[str, Any] | None,
    *,
    from_status: str | None = None,
) -> Dict[str, Any] | None:
    item_id = _as_item_id(item_id)
    statement = (
        sa.update(_QUEUE)
        .where(_QUEUE.c.id == item_id)
        .values(
            status=status,
            result=json.dumps(result, sort_keys=True) if result is not None else None,
        )
    )
    if from_status is not None:
        statement = statement.where(_QUEUE.c.status == from_status)
    conn = _db.connect()
    try:
        changed = _execute(conn, statement).rowcount
        conn.commit()
        if changed == 0:
            return None
    finally:
        conn.close()
    return get(item_id)


def recall(key: str) -> Dict[str, Any] | None:
    statement = sa.select(_KEYS).where(_KEYS.c.key == key)
    conn = _db.connect()
    try:
        row = _execute(conn, statement).fetchone()
        return _as_dict(row) if row else None
    finally:
        conn.close()


def remember(key: str, entity: str, record_id: int) -> None:
    """Record (or replace) what an idempotency key produced, atomically."""
    conn = _db.connect()
    try:
        _execute(conn, sa.delete(_KEYS).where(_KEYS.c.key == key))
        _execute(
            conn,
            sa.insert(_KEYS).values(key=key, entity=entity, record_id=record_id),
        )
        conn.commit()
    finally:
        conn.close()


def _as_dict(row: Any) -> Dict[str, Any]:
    mapping = getattr(row, "_mapping", None)
    return dict(mapping) if mapping is not None else dict(row)


def _row(row: Any) -> Dict[str, Any]:
    item = _as_dict(row)
    if item.get("id") is not None:
        try:
            item["id"] = int(item["id"])
        except (TypeError, ValueError):
            pass
    raw_payload = item.get("payload")
    if isinstance(raw_payload, str):
        try:
            item["payload"] = json.loads(raw_payload)
        except json.JSONDecodeError:
            pass
    raw_result = item.get("result")
    if isinstance(raw_result, str) and raw_result:
        try:
            item["result"] = json.loads(raw_result)
        except json.JSONDecodeError:
            pass
    return item
'''


def render_domain_ops(specs: Dict[str, Dict[str, Any]]) -> str:
    compact = compact_specs(specs)
    cap_id = first_capability_id(specs)
    cap_spec = compact.get(cap_id) or {}
    # SAMPLE must be the DEFAULT capability's columns. first_entity_sample
    # is first *table* (sorted by entity name). Live VetCare Hub
    # sess_5dfb4a3: DEFAULT client_pet_records / pet_record, first table
    # availability — create_persists compared pet_record rows to an
    # availability SAMPLE and PRODUCT stayed red through rework.
    entity = str(
        cap_spec.get("entity") or (cap_id.replace("-", "_") if cap_id else "")
    )
    sample = sample_for_spec(cap_spec, placeholder="s10-row")
    return f'''"""S12 domain outcomes performed through execute_action.

CRUD, list, queue process, refusal, idempotency, unauthorized, and
missing-field rejection are kernel ActionSpecs. HTTP ok:true is not
acceptance. LLM-authored route bodies are forbidden on this path.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from app import store, work_queue
from app.cerebrum_product_kernel.contract.models import (
    ActionContext,
    ActionOutcome,
    ActionSpec,
    ActionStatus,
)
from app.cerebrum_product_kernel.contract.runtime import execute_action

SPECS: Dict[str, Dict[str, Any]] = {compact!r}
DEFAULT_CAPABILITY = {cap_id!r}
DEFAULT_ENTITY = {entity!r}
SAMPLE = {sample!r}

WRITE_PERMISSION = {WRITE_PERMISSION!r}
READ_PERMISSION = {READ_PERMISSION!r}
PROCESS_PERMISSION = {PROCESS_PERMISSION!r}

OUTCOME_CREATE_PERSISTS = {OUTCOME_CREATE_PERSISTS!r}
OUTCOME_READ_RETURNS_PERSISTED = {OUTCOME_READ_RETURNS_PERSISTED!r}
OUTCOME_UPDATE_PERSISTS = {OUTCOME_UPDATE_PERSISTS!r}
OUTCOME_DELETE_PERSISTS = {OUTCOME_DELETE_PERSISTS!r}
OUTCOME_LIST_ONLY_PERSISTED = {OUTCOME_LIST_ONLY_PERSISTED!r}
OUTCOME_QUEUE_ITEM_PROCESSED = {OUTCOME_QUEUE_ITEM_PROCESSED!r}
OUTCOME_REFUSED_ACTION_ERRORS = {OUTCOME_REFUSED_ACTION_ERRORS!r}
OUTCOME_IDEMPOTENT_DUPLICATE_SAFE = {OUTCOME_IDEMPOTENT_DUPLICATE_SAFE!r}
OUTCOME_UNAUTHORIZED_REJECTED = {OUTCOME_UNAUTHORIZED_REJECTED!r}
OUTCOME_MISSING_FIELD_REJECTED = {OUTCOME_MISSING_FIELD_REJECTED!r}

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

WRITE_OPS = frozenset({{"create", "update", "delete", "enqueue", "refuse"}})
READ_OPS = frozenset({{"read", "list"}})


def entity_of(capability_id: str) -> str:
    resolved = str(capability_id or DEFAULT_CAPABILITY)
    spec = SPECS.get(resolved)
    if spec is None:
        raise ValueError(f"unknown capability_id: {{resolved}}")
    return str(spec.get("entity") or resolved.replace("-", "_"))


def fields_of(capability_id: str) -> List[Dict[str, Any]]:
    resolved = str(capability_id or DEFAULT_CAPABILITY)
    spec = SPECS.get(resolved)
    if spec is None:
        raise ValueError(f"unknown capability_id: {{resolved}}")
    return list(spec.get("fields") or [])


def sample_payload(capability_id: str) -> Dict[str, Any]:
    if capability_id == DEFAULT_CAPABILITY and SAMPLE:
        return dict(SAMPLE)
    payload: Dict[str, Any] = {{}}
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
    names = {{f["name"] for f in fields_of(capability_id)}}
    return {{k: arguments[k] for k in names if k in arguments}}


def _json_type(ftype: str) -> str:
    return {{"str": "string", "int": "integer", "float": "number", "bool": "boolean"}}.get(
        ftype, "string"
    )


def input_schema_for(op: str, capability_id: str) -> Dict[str, Any]:
    properties: Dict[str, Any] = {{
        "capability_id": {{"type": "string"}},
        "idempotency_key": {{"type": "string"}},
        "id": {{"type": "integer"}},
        "q": {{"type": "string"}},
        "payload": {{"type": "object"}},
        "reason": {{"type": "string"}},
    }}
    required: List[str] = []
    if op in {{"create", "update", "read", "delete", "list", "enqueue"}}:
        required.append("capability_id")
    if op in {{"read", "update", "delete"}}:
        required.append("id")
    if op == "process":
        required = ["id"]
    if op == "refuse":
        required = ["reason"]
    if op in {{"create", "update"}}:
        for field in fields_of(capability_id):
            prop: Dict[str, Any] = {{"type": _json_type(field.get("type") or "str")}}
            if field.get("allowed_values"):
                prop["enum"] = list(field["allowed_values"])
            properties[field["name"]] = prop
            if op == "create" and field.get("required"):
                required.append(field["name"])
    return {{"type": "object", "properties": properties, "required": required}}


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
            row = store.get(hit["entity"], int(hit["record_id"]), tenant_id=_context.tenant_id)
            if row is not None:
                return ActionOutcome.success({{"stored": row, "replayed": True}})
    stored = store.save(entity, record_fields(capability_id, arguments), tenant_id=_context.tenant_id)
    if key:
        work_queue.remember(str(key), entity, int(stored["id"]))
    return ActionOutcome.success({{"stored": stored, "replayed": False}})


async def _handle_read(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    row = store.get(entity_of(capability_id), int(arguments["id"]), tenant_id=_context.tenant_id)
    if row is None:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_found",
            error_message="record not found",
        )
    return ActionOutcome.success({{"stored": row}})


async def _handle_update(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    entity = entity_of(capability_id)
    current = store.get(entity, int(arguments["id"]), tenant_id=_context.tenant_id)
    if current is None:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_found",
            error_message="record not found",
        )
    merged = {{**current, **record_fields(capability_id, arguments)}}
    updated = store.update(entity, int(arguments["id"]), merged, tenant_id=_context.tenant_id)
    if updated is None:
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="update_failed",
            error_message="update did not persist",
        )
    return ActionOutcome.success({{"stored": updated}})


async def _handle_delete(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    removed = store.delete(entity_of(capability_id), int(arguments["id"]), tenant_id=_context.tenant_id)
    if not removed:
        return ActionOutcome(
            status=ActionStatus.VALIDATION_ERROR,
            error_code="not_found",
            error_message="record not found",
        )
    return ActionOutcome.success({{"deleted": True, "id": int(arguments["id"])}})


async def _handle_list(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    items = store.list_all(entity_of(capability_id), tenant_id=_context.tenant_id)
    query = arguments.get("q")
    if query:
        needle = str(query)
        items = [item for item in items if needle in json_blob(item)]
    return ActionOutcome.success({{"items": items}})


async def _handle_enqueue(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    capability_id = str(arguments.get("capability_id") or DEFAULT_CAPABILITY)
    payload = arguments.get("payload")
    if not isinstance(payload, dict):
        payload = record_fields(capability_id, arguments) or sample_payload(capability_id)
    item = work_queue.enqueue(
        capability_id,
        payload,
        idempotency_key=arguments.get("idempotency_key"),
        tenant_id=_context.tenant_id,
    )
    if item.get("status") != work_queue.PENDING:
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="hollow_queue",
            error_message="enqueue did not persist a pending item",
        )
    return ActionOutcome.success({{"item": item}})


async def _handle_process(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    item_id = int(arguments["id"])
    claimed = work_queue.claim_pending(item_id, tenant_id=_context.tenant_id)
    if claimed is None:
        item = work_queue.get(item_id, tenant_id=_context.tenant_id)
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
            output={{"item": item}},
        )
    from app.kernel_bridge import spec_for

    payload = claimed.get("payload") if isinstance(claimed.get("payload"), dict) else {{}}
    # Phase 2 §0.2: the queue item carries its own tenant (stored at
    # enqueue from the authenticated principal). Processing runs the
    # capability as that tenant -- never as the caller, and never
    # through the HTTP-only product_context(request) helper.
    tenant_id = str(claimed.get("tenant_id") or "")
    if not tenant_id:
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="tenantless_queue_item",
            error_message="queue item has no tenant_id",
        )
    process_context = ActionContext(
        user_id=tenant_id,
        tenant_id=tenant_id,
        organisation_id=tenant_id,
        project_id=tenant_id,
        permissions=[PROCESS_PERMISSION],
        allowed_domains=["product"],
    )
    result = await execute_action(
        spec_for(claimed["capability_id"]),
        process_context,
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
    if marked is None or marked.get("status") in {{work_queue.PENDING, work_queue.PROCESSING}}:
        return ActionOutcome(
            status=ActionStatus.EXECUTION_ERROR,
            error_code="hollow_queue",
            error_message="process did not change the persisted queue status",
        )
    return ActionOutcome.success({{"item": marked, "result": result.to_dict()}})


async def _handle_refuse(_context: ActionContext, arguments: Dict[str, Any]) -> ActionOutcome:
    return ActionOutcome(
        status=ActionStatus.VALIDATION_ERROR,
        error_code="refused",
        error_message=str(arguments.get("reason") or "refused"),
    )


HANDLERS = {{
    "create": _handle_create,
    "read": _handle_read,
    "update": _handle_update,
    "delete": _handle_delete,
    "list": _handle_list,
    "enqueue": _handle_enqueue,
    "process": _handle_process,
    "refuse": _handle_refuse,
}}


def spec_for_op(op: str, capability_id: str) -> ActionSpec:
    if op not in HANDLERS:
        raise ValueError(f"unknown domain op: {{op}}")
    return ActionSpec(
        action_id=f"product.{{op}}",
        domain="product",
        name=op,
        description=f"S12 {{op}} for {{capability_id}}",
        input_schema=input_schema_for(op, capability_id),
        output_schema={{}},
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
            error_message=f"unknown capability_id: {{resolved}}",
        ).to_dict()
    payload = dict(arguments or {{}})
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
    return {{"status": status, **detail}}


async def perform_all(capability_id: Optional[str] = None) -> Dict[str, Any]:
    """Perform every named outcome through execute_action. No HTTP ok:true."""
    cap = capability_id or DEFAULT_CAPABILITY
    entity = entity_of(cap)
    sample = sample_payload(cap)
    ctx = authorized_context()
    outcomes: Dict[str, Any] = {{}}

    created = await perform("create", cap, dict(sample), context=ctx)
    stored = (created.get("output") or {{}}).get("stored") or {{}}
    fetched = store.get(entity, stored["id"], tenant_id=ctx.tenant_id) if stored.get("id") is not None else None
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

    read = await perform("read", cap, {{"id": stored.get("id")}}, context=ctx)
    read_row = (read.get("output") or {{}}).get("stored") or {{}}
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
        "update", cap, {{**mutated, "id": stored.get("id")}}, context=ctx
    )
    after = store.get(entity, stored["id"], tenant_id=ctx.tenant_id) if stored.get("id") is not None else None
    update_ok = (
        updated.get("status") == "success"
        and after is not None
        and all(after.get(k) == mutated[k] for k in mutated)
    )
    outcomes[OUTCOME_UPDATE_PERSISTS] = _record(
        "performed" if update_ok else "failed",
        error=None if update_ok else (updated.get("error_message") or "update did not persist"),
    )

    listed = await perform("list", cap, {{}}, context=ctx)
    listed_ids = {{
        item.get("id")
        for item in (listed.get("output") or {{}}).get("items") or []
    }}
    persisted_ids = {{row["id"] for row in store.list_all(entity, tenant_id=ctx.tenant_id)}}
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

    enq = await perform("enqueue", cap, {{"payload": dict(sample)}}, context=ctx)
    item = (enq.get("output") or {{}}).get("item") or {{}}
    before_status = item.get("status")
    proc = await perform("process", cap, {{"id": item.get("id")}}, context=ctx)
    after_item = (proc.get("output") or {{}}).get("item") or work_queue.get(item["id"]) if item.get("id") else {{}}
    queue_ok = (
        enq.get("status") == "success"
        and before_status == work_queue.PENDING
        and proc.get("status") == "success"
        and after_item
        and after_item.get("status") in {{work_queue.PROCESSED, work_queue.FAILED}}
        and after_item.get("status") != before_status
        and after_item.get("result")
    )
    outcomes[OUTCOME_QUEUE_ITEM_PROCESSED] = _record(
        "performed" if queue_ok else "failed",
        before=before_status,
        after=(after_item or {{}}).get("status"),
        error=None if queue_ok else (proc.get("error_message") or "hollow queue"),
    )

    refused = await perform("refuse", cap, {{"reason": "s12-refused"}}, context=ctx)
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
        invalid_status=(invalid or {{}}).get("status"),
    )

    key = "s12-idempotent"
    first = await perform(
        "create", cap, {{**sample, "idempotency_key": key}}, context=ctx
    )
    second = await perform(
        "create", cap, {{**sample, "idempotency_key": key}}, context=ctx
    )
    first_id = ((first.get("output") or {{}}).get("stored") or {{}}).get("id")
    second_id = ((second.get("output") or {{}}).get("stored") or {{}}).get("id")
    replayed = ((second.get("output") or {{}}).get("replayed")) is True
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
        incomplete = {{k: v for k, v in sample.items() if k != missing_name}}
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
    doomed_id = ((doomed.get("output") or {{}}).get("stored") or {{}}).get("id")
    deleted = await perform("delete", cap, {{"id": doomed_id}}, context=ctx)
    gone = store.get(entity, doomed_id, tenant_id=ctx.tenant_id) if doomed_id is not None else "no-id"
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

    failed = [name for name in OUTCOMES if outcomes.get(name, {{}}).get("status") != "performed"]
    return {{
        "ok": not failed,
        "capability_id": cap,
        "entity": entity,
        "outcomes": outcomes,
        "failed": failed,
        "performed": [name for name in OUTCOMES if name not in failed],
        "kernel": "execute_action",
    }}
'''


def render_product_tests(specs: Dict[str, Dict[str, Any]]) -> str:
    from app.factory.build.findings import render_capability_recorder

    cap_id = first_capability_id(specs)
    # Which capability failed is recorded as data (findings.py), not read
    # out of the assertion message.
    recorder = "\n".join(render_capability_recorder())
    return f'''"""S12 domain acceptance — performed through execute_action.

Factory code-phase names the ten outcomes. Pilot performs them against
the migrated store. HTTP ok:true is not acceptance.
"""

from __future__ import annotations

import asyncio

import pytest

from app.domain_ops import OUTCOMES, perform_all
from app.migrations import upgrade_head

EXPECTED = {list(OUTCOMES)!r}
CAPABILITY = {cap_id!r}


{recorder}


def test_ten_named_outcomes_are_the_contract():
    assert list(OUTCOMES) == EXPECTED
    assert len(OUTCOMES) == 10
    assert "create_persists" in OUTCOMES
    assert "queue_item_processed" in OUTCOMES
    assert "refused_action_errors" in OUTCOMES


@pytest.mark.pilot
def test_ten_business_outcomes_are_performed_through_the_kernel(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    upgrade_head()
    result = asyncio.run(perform_all(CAPABILITY or None))
    if result.get("ok") is not True or result.get("failed"):
        _record_capability_failure(result.get("capability_id") or CAPABILITY)
    assert result["kernel"] == "execute_action"
    assert result["ok"] is True, result
    assert result["failed"] == []
    assert result["performed"] == list(OUTCOMES)
    for name in OUTCOMES:
        assert result["outcomes"][name]["status"] == "performed", (name, result)
'''


def declaration(specs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "schema_version": "domain_acceptance.v1",
        "stage": probe_set.stage_id("DOMAIN_ACCEPTANCE"),
        "outcomes": [dict(item) for item in OUTCOME_CATALOG],
        "kernel": "cerebrum_product_kernel.contract.runtime.execute_action",
        "llm_route_authorship": "forbidden; _coder_route_body stays None",
        "http_ok_true_is": probe_set.code_for("http_ok_true"),
        "capability_id": first_capability_id(specs),
        "permissions": [WRITE_PERMISSION, READ_PERMISSION, PROCESS_PERMISSION],
        "queue": {
            "table": WORK_QUEUE_TABLE,
            "idempotency_table": IDEMPOTENCY_TABLE,
            "hollow_is": probe_set.code_for("hollow_queue"),
        },
        "lotdesk": "fixture only; not patched",
    }


def render_declaration(specs: Dict[str, Dict[str, Any]]) -> str:
    return json.dumps(declaration(specs), indent=2, sort_keys=True) + "\n"


def emit_writer_artifacts(workspace: Any, specs: Dict[str, Dict[str, Any]]) -> None:
    """Write kernel domain ops, work queue, and the outcome contract."""
    for rel, content in domain_substrate(specs):
        workspace.write_text(Path(rel), content)


def domain_substrate(specs: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str]]:
    """(relpath, content) for the spec-DEPENDENT half of the writer artifacts.

    The twin of :func:`data_lifecycle.platform_substrate`, and it exists for
    the same reason. ``run_writer`` returns at its CodeWhale branch, which
    ``FACTORY_CODEWHALE_WRITER=1`` makes the only path production takes, so
    :func:`emit_writer_artifacts` never runs there -- while ``run_tester``
    stamps ``tests/test_domain_acceptance.py`` regardless, and that file opens
    ``from app.domain_ops import OUTCOMES, perform_all``.

    ``backfill_platform_substrate`` closed this hole for ``app/backup.py``
    (sess_b6d51f9089e14176) but is explicitly the spec-INDEPENDENT half, so
    ``domain_ops`` stayed uncovered and the same failure came back as a
    collection error: live build sess_42d244d317f042b2 (Cerebrum VenueOps,
    run 2) went red on ``suite_red: missing module -- FAILED
    tests.test_domain_acceptance - collection failure``, with the agent
    spending rework rounds on a module the factory, not the agent, owns.
    """
    return [
        ("app/work_queue.py", render_work_queue()),
        ("app/domain_ops.py", render_domain_ops(specs)),
        ("docs/domain_acceptance.json", render_declaration(specs)),
    ]


def backfill_domain_substrate(
    workspace: Any, specs: Dict[str, Dict[str, Any]]
) -> Dict[str, List[str]]:
    """Write the domain substrate the agent was never asked for, AND repair a
    stub that cannot satisfy the import contract the factory itself stamps.

    Gap-fill preserves the agent's bytes; the repair is D2's fix: a stub
    ``app/domain_ops.py`` that provides none of the names
    ``tests/test_domain_acceptance.py`` imports is replaced with the canonical
    module the factory owns. A file that provides SOME but not all required
    names is a ``conflict`` — possibly real authored work — and the caller
    halts FACTORY rather than overwrite it.
    """
    from app.factory.build.substrate_contract import reconcile_substrate

    return reconcile_substrate(
        workspace,
        domain_substrate(specs),
        [render_product_tests(specs)],
    )


# -- the domain acceptance driver is the Factory's (owner rule, 2026-10-08) ----------
#
# ``tests/test_domain_acceptance.py`` is TESTER's suite and asserts what
# ``app/domain_ops.perform_all`` reports; domain_ops performs the ten outcomes
# through the vendored product kernel. Two live failures (cycle 8, 19fc746c):
#
# * vineyard (sess_180779be6a7c4f70, build/plt_a33ca5e4ea2643da): the CodeWhale
#   path wrote domain_ops (``from app.cerebrum_product_kernel...``) but never
#   vendored the kernel -- ``_vendor_product_kernel`` sits below the CodeWhale
#   return -- so the suite died at COLLECTION (ModuleNotFoundError), twice, and
#   the writer was billed a rework round for a module it was never asked for.
#   The module was also rendered with the WRITER's empty specs while TESTER
#   stamps the suite with the product's, so the two named different
#   capabilities.
# * construction (sess_4663392a9d904352): absent when the writer started, the
#   writer authored its own domain_ops (perform_all, no OUTCOMES) and the run
#   halted on factory_substrate_conflict.
#
# The driver of a Factory-stamped acceptance suite is the Factory's outright:
# rendered before every writer pass, re-rendered whole with the specs the
# suite is stamped from, listed as Factory-owned, and a writer that touches it
# is put back and told. The kernel it runs through ships with it. The two
# modules the product's own code also calls (the work queue, the kernel
# bridge) stay gap substrate: written where absent, never over the writer's.

#: The vendored product kernel, as every product carries it.
KERNEL_REL = "app/cerebrum_product_kernel"
#: The Factory-owned half of the domain substrate (plus the kernel files).
DOMAIN_OWNED_RELS: Tuple[str, ...] = ("app/domain_ops.py", "docs/domain_acceptance.json")
#: The gap half: written only where the product has none.
DOMAIN_GAP_RELS: Tuple[str, ...] = ("app/work_queue.py", "app/kernel_bridge.py")


def _host_kernel_root() -> Path:
    """The kernel package the Factory itself runs (the one ProductGenerator and
    the template path vendor)."""
    return Path(__file__).resolve().parents[2] / "cerebrum_product_kernel"


def product_kernel_files() -> List[Tuple[str, str]]:
    """``(relpath, text)`` for every file of the product kernel."""
    root = _host_kernel_root()
    out: List[Tuple[str, str]] = []
    for item in sorted(root.rglob("*")):
        if not item.is_file() or "__pycache__" in item.parts or item.suffix in {".pyc", ".pyo"}:
            continue
        rel = f"{KERNEL_REL}/{item.relative_to(root).as_posix()}"
        out.append((rel, item.read_text(encoding="utf-8")))
    return out


def domain_owned_files(specs: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str]]:
    """``(relpath, text)`` of every Factory-owned domain file for ``specs``."""
    rendered = dict(domain_substrate(specs))
    return [(rel, rendered[rel]) for rel in DOMAIN_OWNED_RELS] + product_kernel_files()


def domain_owned_paths() -> Tuple[str, ...]:
    """The Factory-owned domain paths (spec-independent)."""
    return tuple(rel for rel, _text in domain_owned_files({}))


def domain_gap_files() -> List[Tuple[str, str]]:
    """``(relpath, text)`` of the domain modules the Factory writes only where
    the product has none."""
    from app.factory.build.roles_handlers import _render_kernel_bridge

    rendered = dict(domain_substrate({}))
    return [("app/work_queue.py", rendered["app/work_queue.py"]), ("app/kernel_bridge.py", _render_kernel_bridge())]


def domain_specs(state_specs: Optional[Dict[str, Any]], root: Any) -> Dict[str, Dict[str, Any]]:
    """The specs TESTER stamps the domain suite from: the state spec merged with
    the product's declared models (declared_specs, one source)."""
    from app.factory.build.declared_specs import merge_declared_specs, specs_from_product_models

    specs = dict(state_specs or {})
    if root is not None and (Path(root) / "app" / "models.py").is_file():
        specs = merge_declared_specs(specs, specs_from_product_models(Path(root)))
    return specs


def stamp_domain_substrate(workspace: Any, specs: Dict[str, Dict[str, Any]]) -> List[str]:
    """Write every Factory-owned domain file whole; return the ones that changed."""
    changed: List[str] = []
    for rel, text in domain_owned_files(specs):
        try:
            current = workspace.read_text(rel) if workspace.exists(rel) else None
        except (OSError, UnicodeDecodeError):
            current = None
        if current == text:
            continue
        workspace.write_text(Path(rel), text)
        changed.append(rel)
    return changed


def backfill_domain_gaps(workspace: Any, specs: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    """Write the gap half of the domain substrate where the product has none."""
    from app.factory.build.substrate_contract import reconcile_substrate

    return reconcile_substrate(workspace, domain_gap_files(), [render_product_tests(specs)])


def _normalised_sources(path: Path) -> Dict[str, str]:
    findings_as_files: Dict[str, str] = {}
    target = Path(path)
    if target.is_file() and target.suffix == ".zip":
        import zipfile

        with zipfile.ZipFile(target) as zf:
            for name in zf.namelist():
                if name.endswith(".py"):
                    findings_as_files[name.replace("\\", "/")] = zf.read(name).decode(
                        "utf-8", errors="replace"
                    )
        return findings_as_files
    for item in target.rglob("*.py"):
        rel = str(item.relative_to(target)).replace("\\", "/")
        findings_as_files[rel] = item.read_text(encoding="utf-8", errors="replace")
    return findings_as_files


def _basename_map(files: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name, text in files.items():
        parts = name.replace("\\", "/").split("/")
        if "app" in parts:
            idx = parts.index("app")
            out["/".join(parts[idx:])] = text
        else:
            out[name.replace("\\", "/")] = text
    return out


def _module_shape(src: str) -> Dict[str, Any]:
    """What a module defines and uses, read from its syntax tree: function
    names, referenced names, string constants, ``@<obj>.<method>`` decorators,
    and whether any ``return`` hands back exactly ``{"status": "ok"}``."""
    shape: Dict[str, Any] = {
        "defs": set(), "names": set(), "consts": set(), "decorators": set(),
        "returns_status_ok": False,
    }
    try:
        tree = ast.parse(src or "")
    except SyntaxError:
        return shape
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            shape["defs"].add(node.name)
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name):
                    shape["decorators"].add((target.value.id, target.attr))
        elif isinstance(node, ast.Name):
            shape["names"].add(node.id)
        elif isinstance(node, ast.Attribute):
            shape["names"].add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            shape["consts"].add(node.value)
        elif isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            pairs = list(zip(node.value.keys, node.value.values))
            if len(pairs) == 1 and all(isinstance(x, ast.Constant) for x in pairs[0]):
                key, value = pairs[0][0].value, pairs[0][1].value
                if key == "status" and value == "ok":
                    shape["returns_status_ok"] = True
    return shape


def inspect_lotdesk_domain(explicit: Optional[Path] = None) -> Dict[str, Any]:
    """LotDesk cannot perform the ten outcomes on the kernel path."""
    path = resolve_lotdesk_fixture(explicit)
    files = _basename_map(_normalised_sources(path))
    store_src = files.get("app/store.py", "")
    routes_src = files.get("app/routes.py", "")
    main_src = files.get("app/main.py", "")
    queue_src = files.get("app/work_queue.py", "")
    domain_src = files.get("app/domain_ops.py", "")
    # Each module's shape, from its syntax tree -- never a search of its text.
    store_s, routes_s, main_s, queue_s, domain_s = (
        _module_shape(s) for s in (store_src, routes_src, main_src, queue_src, domain_src)
    )
    every_def: set = set()
    for src in files.values():
        every_def |= _module_shape(src)["defs"]
    has_kernel = "execute_action" in every_def or "execute_action" in domain_s["names"]
    has_update = "update" in store_s["defs"]
    has_delete = "delete" in store_s["defs"]
    has_put = ("router", "put") in routes_s["decorators"]
    has_http_delete = ("router", "delete") in routes_s["decorators"]
    has_process = "mark" in queue_s["defs"] and "PROCESSED" in (queue_s["names"] | queue_s["consts"])
    always_200 = main_s["returns_status_ok"]
    vendor_queue = any(
        name.endswith("vendor/blocks/queue/block.py")
        or name.endswith("blocks/queue/block.py")
        for name in files
    )
    findings: List[Finding] = []
    if always_200:
        findings.append(
            Finding(
                probe_set.code_for("health_unconditional_ok"),
                "app/main.py",
                "GET /health is unconditional ok / always-200",
            )
        )
    if not has_kernel:
        findings.append(
            Finding(
                probe_set.code_for("http_ok_true"),
                "app/",
                "no cerebrum_product_kernel execute_action path; HTTP ok:true is not acceptance",
            )
        )
    if not has_update or not has_delete or not has_put or not has_http_delete:
        findings.append(
            Finding(
                probe_set.code_for("missing_update_delete"),
                "app/store.py",
                "missing update/delete persist (LotDesk-class CRUD hole)",
            )
        )
    if vendor_queue and not has_process:
        findings.append(
            Finding(
                probe_set.code_for("hollow_queue"),
                "vendor/blocks/queue/block.py",
                "queue/workflow vendor is present but no persisted process transition",
            )
        )
    if not has_process:
        findings.append(
            Finding(
                probe_set.code_for("hollow_queue"),
                "app/work_queue.py",
                "hollow queue: no mark/process of pending items",
            )
        )
    lotdesk = inspect_path(path)
    for item in lotdesk:
        findings.append(Finding(item.code, item.path, item.detail))

    outcomes: Dict[str, Any] = {}
    for name in OUTCOMES:
        if name in {OUTCOME_UPDATE_PERSISTS, OUTCOME_DELETE_PERSISTS} and not (
            has_update and has_delete
        ):
            outcomes[name] = {"status": "failed", "reason": "missing update/delete"}
        elif name == OUTCOME_QUEUE_ITEM_PROCESSED and not has_process:
            outcomes[name] = {"status": "failed", "reason": "hollow queue"}
        elif name == OUTCOME_REFUSED_ACTION_ERRORS and always_200:
            outcomes[name] = {"status": "failed", "reason": "always-200 / ok:true"}
        elif not has_kernel:
            outcomes[name] = {
                "status": "failed",
                "reason": "cannot perform through execute_action",
            }
        else:
            outcomes[name] = {
                "status": "failed",
                "reason": "LotDesk fixture is not a kernel product",
            }

    codes = [item.code for item in findings]
    failed = [name for name, item in outcomes.items() if item["status"] != "performed"]
    return {
        "ok": False,
        "gate": "lotdesk_domain_acceptance",
        "fixture": str(path),
        "outcomes": outcomes,
        "failed": failed,
        "performed": [name for name in OUTCOMES if name not in failed],
        "codes": codes,
        "findings": [asdict(item) for item in findings],
        **probe_set.present_flags(
            codes,
            (
                probe_set.code_for("health_unconditional_ok"),
                probe_set.code_for("hollow_queue"),
                probe_set.code_for("missing_update_delete"),
            ),
        ),
        "lotdesk": "fixture only; not patched",
    }


def reject_lotdesk_domain(explicit: Optional[Path] = None) -> Dict[str, Any]:
    """LotDesk-as-shipped fails S12. A hollow accept is a factory defect."""
    result = inspect_lotdesk_domain(explicit)
    if result["ok"]:
        raise AssertionError("GATE HOLLOW: LotDesk-as-shipped was accepted by S12")
    if len(result["failed"]) < 10:
        raise AssertionError(
            "GATE HOLLOW: LotDesk performed an S12 outcome it cannot: "
            + ",".join(result["performed"])
        )
    return result
