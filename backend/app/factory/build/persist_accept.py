"""PRODUCT one-record persist / re-read contract for C-BRIEF.

Photographed Floor (2026-09-05, platforms-poll5 / sess_108e101): after a
successful Kimi FACTORY_CODE_CLI WRITER, TESTER stopped:

    PRODUCT (one-record round-trip): 3 capability(ies) did not remember
    a record they were given.

Verified findings (not assumed):

    audit: POST raised OperationalError: no such table: audit
    dashboard: POST raised OperationalError: no such table: dashboard
    veterinary_care_core: POST raised OperationalError: no such table:
        veterinary_care_core

Those ids are the keyword-fallback architect roster (``{vertical}_core``
GENERATE + mentioned Store blocks as REUSE; ``audit`` is always added).
WRITER ``writer_behaviour`` isolates ``STORAGE_PATH`` to a tempfile, so it
migrates the *current* alembic ``0001`` and can go green. PRODUCT's
round-trip probe used the workspace ``./data`` file. A leftover
``platform.db`` already stamped at ``0001_baseline`` (CLI ran alembic, or
a prior TestClient) makes ``upgrade_head()`` a no-op after the factory
overwrites ``0001`` with the persist entities — ``store.save(entity)``
then raises ``OperationalError``.

This module is the one brief + emit + harness contract. An LLM never
writes these rules. Isolated schema POST-raises must not let WRITER claim
done while PRODUCT will refuse the same persist path.

GENERATE-gap factory-LLM fallthrough (sess_f358c2e0, after #343 CLI billing
miss) must use the same persist envelope as REUSE keep-path. Routes without
``app/actions/{capability}.py`` fail ``[check:round_trip]`` with handler
missing.

sess_336246 (tip 5a530b3): GENERATE ``vetcare_hub_veterinary_core`` + REUSE
audit halted at WRITER ``handler missing`` because the factory LLM keyed
the body as ``veterinary_care_core`` (or returned empty) and the keep-path
skipped emit. Billing keep-path must still write the persist envelope —
never a deterministic contract template and never a claimed ≥2h CLI session.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from app.factory.build.data_lifecycle import (
    IDEMPOTENCY_TABLE,
    WORK_QUEUE_TABLE,
    migration_table_names,
)
from app.factory.build.product_gate import GATE_SCOPES

PRODUCT_ROUND_TRIP_CHECK = "round_trip"
PRODUCT_ROUND_TRIP_HALT = (
    "did not remember a record they were given"
)
PRODUCT_NO_SUCH_TABLE_HALT = "no such table"
PRODUCT_POST_RAISED_HALT = "POST raised"
WRITER_PERSIST_HALT = (
    "WRITER [check:round_trip] failed — persist entity missing from "
    "alembic 0001 or store.COLUMNS"
)
WRITER_REUSE_HANDLER_HALT = (
    "WRITER [check:reuse_handler] failed — REUSE capability has no "
    "loadable app.actions handler (registry-verified handler source miss)"
)
PERSIST_ISOLATE_NEEDLE = "tempfile.mkdtemp(prefix=\"product-gate-\")"
FACTORY_GROUNDED_PERSIST_SOURCE = "factory-grounded persist"

_SKIP_ALEMBIC_TABLES = {WORK_QUEUE_TABLE, IDEMPOTENCY_TABLE}


class PersistRoundTripHalt(ValueError):
    """WRITER must not claim done: persist entities are not durable."""


def persist_entity_of(
    spec: Optional[Mapping[str, Any]],
    capability_id: str,
) -> str:
    """Store / alembic table the PRODUCT probe will re-read."""
    raw = ""
    if isinstance(spec, Mapping):
        raw = str(spec.get("entity") or "").strip()
    if not raw:
        raw = str(capability_id or "record")
    return raw.replace("-", "_")


def normalize_capability_id(capability_id: str) -> str:
    """Hyphen/underscore-insensitive inventory id."""
    return str(capability_id or "").strip().lower().replace("-", "_")


def bind_generate_artifacts(
    known_ids: Sequence[str],
    raw: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Map factory-LLM spec/handler keys onto GENERATE inventory ids.

    Live sess_336246: architect named ``vetcare_hub_veterinary_core``;
    OpenRouter returned ``veterinary_care_core``. Exact-key emit then
    skipped the persist handler and WRITER halted at round_trip.

    Order: exact, normalize, then unique leftover pairing when exactly
    one known id and one unmatched key remain. Never invent extra ids.
    """
    known = [str(cid).strip() for cid in known_ids if str(cid).strip()]
    blob = {
        str(key).strip(): value
        for key, value in (raw or {}).items()
        if str(key).strip()
    } if isinstance(raw, Mapping) else {}
    bound: Dict[str, Any] = {}
    unused = dict(blob)

    def _take(cid: str, key: str) -> None:
        if key in unused:
            bound[cid] = unused.pop(key)

    for cid in known:
        if cid in unused:
            _take(cid, cid)
            continue
        norm = normalize_capability_id(cid)
        match = next(
            (key for key in list(unused) if normalize_capability_id(key) == norm),
            None,
        )
        if match:
            _take(cid, match)

    leftover_known = [cid for cid in known if cid not in bound]
    leftover_keys = list(unused)
    if len(leftover_known) == 1 and leftover_keys:
        cid = leftover_known[0]
        if len(leftover_keys) == 1:
            _take(cid, leftover_keys[0])
        else:
            norm = normalize_capability_id(cid)
            coreish = [
                key
                for key in leftover_keys
                if normalize_capability_id(key).endswith("_core")
                and norm.endswith("_core")
            ]
            if len(coreish) == 1:
                _take(cid, coreish[0])
    return bound


def persist_accept_rules_text(capability_ids: Sequence[str] = ()) -> str:
    """BUILD cut: what PRODUCT one-record will POST and what persist means.

    ``capability_ids`` is THIS build's capability set, from its compiled plan.
    Nothing else is named: a roster from another product is not a contract
    for this one.
    """
    caps = ", ".join(str(c) for c in capability_ids if str(c).strip())
    return "\n".join(
        [
            "PRODUCT gate one-record round-trip (after WRITER, before STORE):",
            "The harness boots the product (TestClient lifespan runs alembic)",
            "on an isolated STORAGE_PATH, then POSTs /v1/{capability_id} with a",
            "payload built from that capability's own FIELDS + CONSTRAINTS.",
            "Accept means HTTP 200, not ok:false, store.list_all(entity) holds",
            "the record, and GET /v1/{capability_id} returns it.",
            f"A miss is reported as: {PRODUCT_ROUND_TRIP_HALT}",
            f"POST raising OperationalError: {PRODUCT_NO_SUCH_TABLE_HALT}: "
            "<entity> is that miss — the persist table was not migrated.",
            "",
            "factory-grounded persist (not an LLM stub):",
            "- every capability has an alembic 0001 table named spec.entity",
            "  (capability_id with '-' → '_' when the spec omits entity)",
            "- store.COLUMNS and store.save use that same entity",
            "- handle() returns ok:true with its result; persistence is the",
            "  ROUTE's job (tenant-scoped save(payload)) \u2014 handlers must not",
            "  call store.save directly: they have no tenant (Phase 2 \u00a70.2)",
            "- the route save(payload) writes the request, not handle()'s envelope",
            "- do not persist to 'records' or a capability id that is not the entity",
            "- do not rely on a leftover ./data/platform.db; PRODUCT isolates",
            "  STORAGE_PATH the same way writer_behaviour does",
            "",
            "This build's capabilities:",
            f"  {caps or '(none planned)'}",
            "Every one of them is persistable, not 'just blocks'. Each",
            "must remember one record. Templated execute(block_id, payload)",
            "or no_block_bound without store.save is not done.",
            "",
            "GENERATE-gap factory-LLM fallthrough (after CLI billing/auth miss):",
            "- write app/actions/{capability}.py through the same pure-",
            "  dispatch envelope as REUSE keep-path (the ROUTE persists)",
            "- alembic 0001 and store.COLUMNS still use spec.entity",
            "- bind factory-LLM keys onto inventory GENERATE ids (exact,",
            "  normalize, unique leftover) so a capability is not dropped",
            "  when the LLM writes a shortened form of its id",
            "- empty / mismatched factory-LLM still emits the persist",
            "  envelope — not a deterministic contract template and not a",
            "  ≥2h CLI session. WRITER [check:round_trip] must not HALT",
            "  solely because the billing keep-path omitted the handler",
        ]
    )


def persist_accept_acceptance_line() -> str:
    """ACCEPTANCE cut: harness check, not a coder decorative test."""
    return (
        "- every emitted capability persists one record to its alembic "
        "entity and GET returns it "
        f"({GATE_SCOPES['PRODUCT']})  "
        f"[check:{PRODUCT_ROUND_TRIP_CHECK}]"
    )


def persist_accept_forbidden_lines() -> str:
    """FORBIDDEN cut: the live CLI inventions that PRODUCT then refuses."""
    return "\n".join(
        [
            "- persist to a table alembic 0001 did not create "
            f"({PRODUCT_NO_SUCH_TABLE_HALT}: <entity>)",
            "- leaving PRODUCT on a leftover ./data/platform.db stamped at "
            "0001_baseline after the factory rewrote 0001 (upgrade_head no-op)",
            "- execute(block_id, payload) or no_block_bound stubs that never "
            "reach the route's tenant-scoped save(payload) (keyword-fallback "
            "{vertical}_core class)",
            "- persist to table=records or to the capability id when spec.entity "
            "is a different name",
            "- treating WRITER writer_behaviour green on a tempfile DB as "
            f"done while PRODUCT still {PRODUCT_ROUND_TRIP_HALT}",
        ]
    )


def persist_accept_brief_contract() -> str:
    """System-brief paragraph shared by WRITER seat + HTTP oneshot."""
    return (
        "PRODUCT one-record round-trip POSTs a schema-sample payload then "
        "re-reads store.list_all(entity) and GET /v1/{capability_id}. A miss "
        f"is {PRODUCT_ROUND_TRIP_HALT!r}. Every capability must persist that "
        "record to its alembic entity via the ROUTE's tenant-scoped "
        "save(payload) \u2014 handlers must not persist directly (no tenant). "
        f"POST {PRODUCT_POST_RAISED_HALT} "
        f"OperationalError: {PRODUCT_NO_SUCH_TABLE_HALT}: <entity> is a miss. "
        "WRITER isolates STORAGE_PATH; PRODUCT must too — a leftover "
        "./data/platform.db already at 0001_baseline is not a pass. "
        "Every capability the build plans is persistable."
    )


def persist_entities_from_specs(
    specs: Mapping[str, Mapping[str, Any]],
) -> Dict[str, str]:
    """capability_id → persist entity."""
    return {
        str(cid): persist_entity_of(spec, str(cid))
        for cid, spec in (specs or {}).items()
        if str(cid).strip()
    }


def missing_alembic_persist_entities(
    revision_0001_source: str,
    entities: Iterable[str],
) -> List[str]:
    created = migration_table_names(revision_0001_source or "")
    want = {str(e).strip() for e in entities if str(e).strip()}
    return sorted(want - created)


def wipe_workspace_runtime_db(workspace: Any) -> None:
    """Drop leftover ./data so the next boot migrates current 0001."""
    root = Path(getattr(workspace, "workspace", workspace))
    data = root / "data"
    if data.is_dir():
        shutil.rmtree(data)


def persist_workspace_root(workspace: Any) -> Path:
    """Directory persist_accept scans — WRITER staging, not destination.

    RoleRunner stages WRITER writes. ``RoleWorkspace.exists`` also sees
    the destination (and store_root). Round-trip must judge the tree
    that ``commit()`` will copy, or a leftover destination handler makes
    GENERATE look present while staging is empty (sess_f358c2e0).
    """
    return Path(getattr(workspace, "workspace", workspace))


def persist_handler_rel(capability_id: str) -> Path:
    """Workspace-relative handler path persist_accept requires."""
    name = str(capability_id or "").replace("-", "_")
    return Path("app") / "actions" / f"{name}.py"


def _persist_calls(text: str) -> Optional[List[ast.Call]]:
    """The calls in a module that persist a record, read from its syntax
    tree: ``_persist_record(...)``, ``store.save(...)`` or any ``save`` call
    handed the request ``payload``. None when the source does not parse."""
    try:
        tree = ast.parse(text or "")
    except SyntaxError:
        return None
    found: List[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        receiver = func.value.id if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) else ""
        passes_payload = any(isinstance(a, ast.Name) and a.id == "payload" for a in node.args)
        if name == "_persist_record" or (name == "save" and (receiver == "store" or passes_payload)):
            found.append(node)
    return found


def handler_declares_persist(text: str, entity: str) -> bool:
    """True when the handler body persists directly (a Phase 2 §0.2 violation).

    Persistence is route-scoped: the ROUTE's tenant-scoped save(payload)
    writes the request after ActionStatus.SUCCESS. A handler that still
    calls _persist_record / store.save / save(payload) has no tenant and
    must be regenerated through the pure-dispatch envelope. Read from the
    handler's syntax tree, never its text.
    """
    return bool(_persist_calls(text))


def _saved_tables(text: str) -> List[str]:
    """Constant table names a module hands to ``store.save``."""
    out: List[str] = []
    for call in _persist_calls(text) or []:
        if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
            out.append(call.args[0].value)
    return out


def _string_constants(text: str) -> set:
    try:
        tree = ast.parse(text or "")
    except SyntaxError:
        return set()
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def persist_round_trip_errors(
    root: Path,
    specs: Mapping[str, Mapping[str, Any]],
) -> List[str]:
    """Scan emitted alembic + store + handlers/routes. Empty = green."""
    base = persist_workspace_root(root)
    errors: List[str] = []
    entities = persist_entities_from_specs(specs)
    revision = base / "alembic" / "versions" / "0001_baseline.py"
    if not revision.is_file():
        return ["alembic/versions/0001_baseline.py is missing"]
    try:
        src = revision.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"alembic 0001 unreadable: {exc}"]
    missing = missing_alembic_persist_entities(src, entities.values())
    if missing:
        errors.append(
            "alembic 0001 missing persist table(s): " + ", ".join(missing)
        )

    store_py = base / "app" / "store.py"
    if store_py.is_file():
        try:
            store_src = store_py.read_text(encoding="utf-8")
        except OSError:
            store_src = ""
        declared = _string_constants(store_src)
        for entity in sorted(set(entities.values())):
            if entity not in declared:
                errors.append(f"store.COLUMNS missing persist entity {entity}")
    else:
        errors.append("app/store.py is missing")

    routes = base / "app" / "routes.py"
    route_src = ""
    if routes.is_file():
        try:
            route_src = routes.read_text(encoding="utf-8")
        except OSError:
            route_src = ""
    if route_src and not any(
        any(isinstance(a, ast.Name) and a.id == "payload" for a in call.args)
        for call in (_persist_calls(route_src) or [])
    ):
        errors.append("app/routes.py does not persist via save(payload)")

    for cid, entity in entities.items():
        handler = base / persist_handler_rel(cid)
        if not handler.is_file():
            errors.append(f"handler missing for persist capability {cid}")
            continue
        try:
            text = handler.read_text(encoding="utf-8")
        except OSError as exc:
            errors.append(f"handler {cid} unreadable: {exc}")
            continue
        if handler_declares_persist(text, entity):
            wrong = _saved_tables(text)
            tables = sorted({t for t in wrong if t not in _SKIP_ALEMBIC_TABLES})
            if not tables:
                tables = [entity]
            for table in tables:
                errors.append(
                    f"{cid}: handler persists directly to {table!r}; "
                    f"persistence is route-scoped to entity {entity!r}"
                )
    return errors


def assert_persist_round_trip_ready(
    root: Path,
    specs: Mapping[str, Mapping[str, Any]],
) -> None:
    errors = persist_round_trip_errors(root, specs)
    if errors:
        raise PersistRoundTripHalt(
            WRITER_PERSIST_HALT + ": " + "; ".join(errors[:8])
        )


def grounded_persist_assign() -> str:
    """One line: the ROUTE persists the request (tenant-scoped save)."""
    return "    stored = save(payload)"


def emit_factory_grounded_generate_persist(
    root: Path,
    compiled: Any,
    *,
    handlers: Optional[Mapping[str, str]] = None,
    specs: Optional[Mapping[str, Any]] = None,
    source: str = "coder LLM (factory)",
    force_ids: Optional[Sequence[str]] = None,
) -> List[str]:
    """Write persist-capable GENERATE handlers after factory-LLM fallthrough.

    Same ``_handler_module`` pure-dispatch envelope as REUSE keep-path
    (the ROUTE owns persistence via its tenant-scoped save). LLM bodies are
    remapped onto inventory GENERATE ids and
    wrapped when present. Empty / mismatched LLM still emits the persist
    envelope (factory-grounded persist) so WRITER ``[check:round_trip]``
    does not HALT for a missing handler — not a deterministic contract
    template and not a ≥2h CLI session.

    ``force_ids`` overwrite an existing persist-capable file (event_bus
    unkeepable harvest repair). Default still only fills holes.
    """
    from app.factory.build.roles_handlers import (
        _capability_handler_body,
        _handler_module,
    )
    from app.factory.build.reuse_accept import harvest_block_default_actions

    root = persist_workspace_root(root)
    written: List[str] = []
    gap_ids = [
        str(getattr(item, "capability_id", "") or "").strip()
        for item in getattr(compiled, "inventory", ()) or ()
        if getattr(item, "is_gap", False)
        and str(getattr(item, "capability_id", "") or "").strip()
    ]
    raw_handlers = bind_generate_artifacts(
        gap_ids, handlers if isinstance(handlers, Mapping) else {}
    )
    raw_specs = bind_generate_artifacts(
        gap_ids, specs if isinstance(specs, Mapping) else {}
    )
    actions = root / "app" / "actions"
    actions.mkdir(parents=True, exist_ok=True)
    force = {str(x) for x in (force_ids or ()) if str(x).strip()}
    for item in getattr(compiled, "inventory", ()) or ():
        if not getattr(item, "is_gap", False):
            continue
        cid = str(getattr(item, "capability_id", "") or "").strip()
        if not cid:
            continue
        bids = [
            str(b)
            for b in (
                getattr(item, "verified_present", None)
                or getattr(item, "block_ids", None)
                or ()
            )
            if str(b).strip()
        ]
        llm_body = raw_handlers.get(cid)
        if isinstance(llm_body, str) and llm_body.strip():
            body = llm_body
            authored = source
        else:
            body = _capability_handler_body(cid, bids)
            authored = FACTORY_GROUNDED_PERSIST_SOURCE
        spec = raw_specs.get(cid)
        spec_map = spec if isinstance(spec, Mapping) else {}
        entity = persist_entity_of(spec_map, cid)
        field_names = [
            str(f.get("name"))
            for f in (spec_map.get("fields") or [])
            if isinstance(f, dict) and f.get("name")
        ]
        path = root / persist_handler_rel(cid)
        if path.is_file() and cid not in force:
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError:
                existing = ""
            if not handler_declares_persist(existing, entity):
                # Keep a pure-dispatch handler (route owns persist). A
                # leftover direct-persist body has no tenant and is
                # overwritten with the new envelope. Only fill holes.
                written.append(cid)
                continue
        defaults = harvest_block_default_actions(bids, root)
        path.write_text(
            _handler_module(
                cid,
                bids,
                body,
                authored,
                defaults,
                entity=entity,
                field_names=field_names,
            ),
            encoding="utf-8",
        )
        written.append(cid)
    return written
