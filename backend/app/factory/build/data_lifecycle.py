"""S10 data lifecycle emitters for RoleRunner products.

Unused kits already carry Alembic (steward_runtime/migrations). RoleRunner
did not emit it; generated products used ``CREATE TABLE IF NOT EXISTS`` on
every ``store.connect()``. This module is the WRITER/TESTER emission for
versioned up/down migrations, WAL durability, backup/restore/retention, and
the product-side tests that *perform* a restore drill.

SQLite on a single mounted volume is retained. That is a SPOF. Capacity is the
volume size (1 GiB in the emitted deploy/contract.json). Backups on the same
volume do not survive its loss; set BACKUP_DIR onto another volume if that is
in scope. The volume is not optional: on a runtime with ephemeral storage --
an ECS task, a plain container -- every deploy discards the database silently.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.factory.build.engine_switch import render_db_module
from app.factory.build.observability import (
    render_backup_script,
    render_bench_script,
    render_observability,
)
from app.factory.build.workspace import write_workspace_text

# anyio / Starlette default limiter for FastAPI sync def endpoints.
FASTAPI_SYNC_THREADPOOL = 40
SQLITE_BUSY_TIMEOUT_MS = 30_000
SQLITE_CONNECT_TIMEOUT_S = 30.0
#: The DECLARED persistence contract (owner, 2026-10-07): SQLite runs in this
#: journal mode, switched ONCE by the boot path (app.migrations.upgrade_head),
#: never per connection. The boot path, the store's enable_wal(), the
#: lifecycle doc and the emitted suite all render from this one constant --
#: the suite asserts the declaration, never a literal of its own.
SQLITE_JOURNAL_MODE = "WAL"
BACKUP_KEEP = 14
DISK_SIZE_GB = 1
REVISION_0001 = "0001_baseline"
REVISION_0002 = "0002_lifecycle_audit"
AUDIT_TABLE = "lifecycle_audit"
WORK_QUEUE_TABLE = "work_queue"
IDEMPOTENCY_TABLE = "idempotency"

_SA_TYPES = {
    "str": "sa.Text()",
    "int": "sa.Integer()",
    "float": "sa.Float()",
    "bool": "sa.Integer()",
}

def _declared_kind(field: Dict[str, Any]) -> str | None:
    """The field's canonical type, from the ONE resolver TESTER samples with.

    A second, narrower alias table lived here and knew only int/float/bool:
    a declared ``money`` (stored as float) fell through to TEXT and to the
    text marker, and the emitted lifecycle suite wrote ``'s10-row'`` into a
    numeric column (live 9d382ae7, co-op). None means an undeclared spelling.
    """
    from app.factory.build.roles_handlers import _resolve_known_field_type

    return _resolve_known_field_type(field.get("type") or "str")


def _field_sa_type(field: Dict[str, Any]) -> str:
    """SQLAlchemy column type for the declared type.

    Numbers and booleans get their storage type; everything else -- text,
    email, uuid, and datetime/date/time (persisted as ISO strings) -- is TEXT.
    """
    kind = _declared_kind(field)
    return _SA_TYPES.get(kind if kind in _SA_TYPES else "str", "sa.Text()")


#: Columns the STORE owns on every table: the row id it assigns and the
#: tenant it scopes by. A product model may list them among its FIELDS (it
#: carries the id it was given), but they are never declared data: the
#: store writes them, so they are not inserted from a record, not migrated
#: as declared columns, and not part of a sample a test expects to read back.
#: Live 2026-10-06 (879ed1e1, vineyard): the declared-model reader listed
#: ``id`` as a str field, the lifecycle test inserted ``id='s10-row'`` and
#: read back the store's own id -> ``assert 1 == 's10-row'``, a Factory
#: test the writer could never fix. One definition, used by the store
#: renderer below and by every helper that turns fields into columns.
STORE_MANAGED_COLUMNS: Tuple[str, ...] = ("id", "tenant_id")


def declared_fields(spec: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """A spec's declared data fields: named, and not a store-managed column."""
    return [
        field
        for field in (spec or {}).get("fields") or []
        if isinstance(field, dict)
        and field.get("name")
        and field["name"] not in STORE_MANAGED_COLUMNS
    ]


def table_specs(specs: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for spec in sorted(specs.values(), key=lambda s: s["entity"]):
        out.append({"entity": spec["entity"], "fields": declared_fields(spec)})
    return out


def columns_map(specs: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    return {
        spec["entity"]: [f["name"] for f in declared_fields(spec)]
        for spec in specs.values()
    }


def sample_for_spec(
    spec: Dict[str, Any] | None, *, placeholder: str = "s10-row"
) -> Dict[str, Any]:
    """A deterministic row from one capability/table spec.

    S10 lifecycle tests insert the first *table*. S12 domain acceptance
    must insert the DEFAULT capability's own columns — those two are not
    the same order (live sess_5dfb4a3: ``client_pet_records`` / pet_record
    vs alphabetically-first entity ``availability``).
    """
    from app.factory.build.roles_handlers import _looks_like_email_field, _sample_value

    sample: Dict[str, Any] = {}
    for field in declared_fields(spec):
        name = field["name"]
        if field.get("allowed_values"):
            sample[name] = field["allowed_values"][0]
        elif _declared_kind(field) in (None, "str") and not _looks_like_email_field(field):
            # Plain text (or a spelling nobody declared): the lifecycle marker.
            sample[name] = placeholder
        else:
            # Every typed field -- number, money, bool, email, uuid, temporal --
            # takes the value TESTER's one sampler gives its declaration.
            sample[name] = _sample_value(field)
    return sample


#: Python value types a declared canonical kind accepts on write and returns on
#: read. Temporal and identity kinds persist as ISO / string text.
_KIND_VALUE_TYPES = {
    "int": (int,),
    "float": (int, float),
    "bool": (bool,),
}


def check_sample_fits_declared_types(
    entity: str, sample: Dict[str, Any], spec: Dict[str, Any] | None
) -> None:
    """Every value the suite inserts fits its column's DECLARED type.

    The emitted suite is Factory-authored: a value its own declaration forbids
    (text into a number, a number into a bool) is the Factory's defect. Raised
    at render time so it ends the run as a Factory fault -- it never reaches
    the writer as a rework it may not fix (the test file is Factory-owned).
    """
    by_name = {f["name"]: f for f in declared_fields(spec)}
    wrong: List[str] = []
    for key, value in sample.items():
        field = by_name.get(key)
        if field is None or field.get("allowed_values"):
            continue
        kind = _declared_kind(field)
        accepted = _KIND_VALUE_TYPES.get(kind or "", (str,))
        is_bool = isinstance(value, bool)
        fits = isinstance(value, accepted) and (kind == "bool" or not is_bool)
        if not fits:
            wrong.append(f"{key}={value!r} (declared {field.get('type')!r})")
    if wrong:
        raise EmittedSuiteContractError(
            f"lifecycle sample for {entity!r} does not fit its declared types: {wrong}"
        )


def first_entity_sample(specs: Dict[str, Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """A deterministic row the generated lifecycle tests can insert."""
    if not specs:
        return "", {}
    spec = table_specs(specs)[0]
    return spec["entity"], sample_for_spec(spec, placeholder="s10-row")


def render_store(specs: Dict[str, Dict[str, Any]]) -> str:
    """Persistence for the domain models, as SQLAlchemy Core.

    Every statement is built from table/column objects for the DECLARED
    schema (``COLUMNS``) and compiled for the active dialect -- no SQL
    string is assembled from identifiers, so nothing here is a string-built
    query (bandit B608 failed audit_clean on every product when it was).
    Schema comes from Alembic, not connect().
    """
    columns = columns_map(specs)
    tables = tuple(sorted(columns))
    return (
        '"""Persistence for the domain models.\n'
        "\n"
        "Statements are SQLAlchemy Core built from the declared schema\n"
        "(COLUMNS), compiled for whichever backend app.db is on: the SQLite\n"
        "file by default, Postgres when DATABASE_URL is set. No SQL text is\n"
        "assembled from names; values always travel as bound parameters.\n"
        "Schema is applied by Alembic (app/migrations.py + alembic/versions/),\n"
        "never by this module.\n"
        "\n"
        "Durability on SQLite: WAL + busy_timeout matched to the FastAPI sync\n"
        f"threadpool ({FASTAPI_SYNC_THREADPOOL} workers). One writer; readers\n"
        "proceed. STORAGE_PATH relocates the file onto the mounted disk.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from pathlib import Path\n"
        "from typing import Any, Dict, List, Tuple\n"
        "\n"
        "import sqlalchemy as sa\n"
        "from sqlalchemy.dialects import sqlite as _sqlite_dialect\n"
        "\n"
        "from app import db as _db\n"
        "\n"
        f"TABLES: Tuple[str, ...] = {tables!r}\n"
        f"COLUMNS: Dict[str, List[str]] = {columns!r}\n"
        f"SQLITE_BUSY_TIMEOUT_MS = {SQLITE_BUSY_TIMEOUT_MS}\n"
        f"SQLITE_CONNECT_TIMEOUT_S = {SQLITE_CONNECT_TIMEOUT_S}\n"
        f"FASTAPI_SYNC_THREADPOOL = {FASTAPI_SYNC_THREADPOOL}\n"
        "\n"
        "_SQLITE = _sqlite_dialect.dialect(paramstyle=\"qmark\")\n"
        "\n"
        "\n"
        "class QueryError(ValueError):\n"
        "    \"\"\"A caller asked for an entity, column or ordering that is not declared.\"\"\"\n"
        "\n"
        "\n"
        "def _table(entity: str) -> Any:\n"
        "    \"\"\"The declared table. An undeclared entity is refused, never named.\"\"\"\n"
        "    if entity not in COLUMNS:\n"
        "        raise QueryError(\"unknown entity: \" + str(entity))\n"
        f"    names = [*{list(STORE_MANAGED_COLUMNS)!r}, *COLUMNS[entity]]\n"
        "    return sa.table(entity, *(sa.column(n) for n in dict.fromkeys(names)))\n"
        "\n"
        "\n"
        "def db_path() -> Path:\n"
        '    """The SQLite file app.db uses when DATABASE_URL is unset."""\n'
        "    return _db.sqlite_path()\n"
        "\n"
        "\n"
        "def connect() -> Any:\n"
        '    """A connection from app.db -- the one place that decides the backend.\n'
        "\n"
        "    This module never opens a database of its own: a store that did\n"
        "    wrote SQLite while DATABASE_URL said Postgres. app.db sets the\n"
        "    per-connection pragmas (busy_timeout) and does NOT switch\n"
        "    journal_mode per connection -- WAL is set once at boot.\n"
        '    """\n'
        "    return _db.connect()\n"
        "\n"
        "\n"
        "def enable_wal() -> None:\n"
        '    """Switch the SQLite file to WAL once, single-threaded, at boot.\n'
        "\n"
        "    WAL then persists in the file, so later connections inherit it\n"
        "    without a mode switch of their own. Postgres has no journal mode.\n"
        '    """\n'
        "    if _db.is_postgres():\n"
        "        return\n"
        "    conn = connect()\n"
        "    try:\n"
        f"        conn.execute(\"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}\")\n"
        "    finally:\n"
        "        conn.close()\n"
        "\n"
        "\n"
        "def _connect() -> Any:\n"
        "    return connect()\n"
        "\n"
        "\n"
        "def _execute(conn: Any, statement: Any) -> Any:\n"
        "    \"\"\"Run one Core statement on either backend's connection.\"\"\"\n"
        "    if _db.is_postgres():\n"
        "        return conn.execute(statement)\n"
        "    compiled = statement.compile(dialect=_SQLITE)\n"
        "    params = [compiled.params[name] for name in (compiled.positiontup or ())]\n"
        "    return conn.execute(str(compiled), params)\n"
        "\n"
        "\n"
        "def _as_dict(row: Any) -> Dict[str, Any]:\n"
        "    mapping = getattr(row, \"_mapping\", None)\n"
        "    return dict(mapping) if mapping is not None else dict(row)\n"
        "\n"
        "\n"
        "def save(entity: str, record: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:\n"
        '    """Insert a record for one tenant and return it with its assigned id."""\n'
        "    table = _table(entity)\n"
        "    cols = COLUMNS[entity]\n"
        "    values = {\"tenant_id\": tenant_id, **{c: record.get(c) for c in cols}}\n"
        "    statement = sa.insert(table).values(**values)\n"
        "    conn = _connect()\n"
        "    try:\n"
        "        if _db.is_postgres():\n"
        "            row_id = int(_execute(conn, statement.returning(table.c.id)).scalar_one())\n"
        "        else:\n"
        "            row_id = int(_execute(conn, statement).lastrowid)\n"
        "        conn.commit()\n"
        '        return {"id": row_id, "tenant_id": tenant_id, **{c: record.get(c) for c in cols}}\n'
        "    finally:\n"
        "        conn.close()\n"
        "\n"
        "\n"
        "def list_all(entity: str, tenant_id: str) -> List[Dict[str, Any]]:\n"
        "    table = _table(entity)\n"
        "    statement = sa.select(table).where(table.c.tenant_id == tenant_id).order_by(table.c.id)\n"
        "    conn = _connect()\n"
        "    try:\n"
        "        return [_as_dict(r) for r in _execute(conn, statement).fetchall()]\n"
        "    finally:\n"
        "        conn.close()\n"
        "\n"
        "\n"
        "def query(\n"
        "    entity: str,\n"
        "    tenant_id: str,\n"
        "    *,\n"
        "    filters: Dict[str, Any] | None = None,\n"
        "    sort: str | None = None,\n"
        "    order: str = \"asc\",\n"
        "    limit: int = 50,\n"
        "    offset: int = 0,\n"
        ") -> Dict[str, Any]:\n"
        "    \"\"\"Filter, sort and page one entity.\n"
        "\n"
        "    Columns are resolved against the declared table, never interpolated\n"
        "    from caller input: an unknown field raises QueryError instead of\n"
        "    reaching SQL. Values always travel as bound parameters.\n"
        "    \"\"\"\n"
        "    table = _table(entity)\n"
        "    cols = COLUMNS[entity]\n"
        "    conditions = [table.c.tenant_id == tenant_id]\n"
        "    for name, value in (filters or {}).items():\n"
        "        if name not in cols:\n"
        "            raise QueryError(\"unknown filter field: \" + str(name))\n"
        "        conditions.append(table.c[name] == value)\n"
        "    if sort is not None and sort not in cols and sort != \"id\":\n"
        "        raise QueryError(\"unknown sort field: \" + str(sort))\n"
        "    direction = str(order or \"asc\").lower()\n"
        "    if direction not in (\"asc\", \"desc\"):\n"
        "        raise QueryError(\"order must be asc or desc\")\n"
        "    if limit < 1 or limit > 500:\n"
        "        raise QueryError(\"limit must be between 1 and 500\")\n"
        "    if offset < 0:\n"
        "        raise QueryError(\"offset must be >= 0\")\n"
        "    column = table.c[sort or \"id\"]\n"
        "    ordering = column.desc() if direction == \"desc\" else column.asc()\n"
        "    counted = sa.select(sa.func.count()).select_from(table).where(*conditions)\n"
        "    page = (\n"
        "        sa.select(table).where(*conditions).order_by(ordering).limit(limit).offset(offset)\n"
        "    )\n"
        "    conn = _connect()\n"
        "    try:\n"
        "        total = int(_execute(conn, counted).fetchone()[0])\n"
        "        rows = _execute(conn, page).fetchall()\n"
        "        return {\n"
        "            \"items\": [_as_dict(r) for r in rows],\n"
        "            \"total\": total,\n"
        "            \"limit\": limit,\n"
        "            \"offset\": offset,\n"
        "        }\n"
        "    finally:\n"
        "        conn.close()\n"
        "\n"
        "\n"
        "def get(entity: str, record_id: int, tenant_id: str) -> Dict[str, Any] | None:\n"
        "    table = _table(entity)\n"
        "    statement = sa.select(table).where(\n"
        "        table.c.id == record_id, table.c.tenant_id == tenant_id\n"
        "    )\n"
        "    conn = _connect()\n"
        "    try:\n"
        "        row = _execute(conn, statement).fetchone()\n"
        "        return _as_dict(row) if row else None\n"
        "    finally:\n"
        "        conn.close()\n"
        "\n"
        "\n"
        "def update(entity: str, record_id: int, record: Dict[str, Any], tenant_id: str) -> Dict[str, Any] | None:\n"
        '    """Overwrite a persisted row. Returns None when the id does not exist."""\n'
        "    table = _table(entity)\n"
        "    statement = (\n"
        "        sa.update(table)\n"
        "        .where(table.c.id == record_id, table.c.tenant_id == tenant_id)\n"
        "        .values(**{c: record.get(c) for c in COLUMNS[entity]})\n"
        "    )\n"
        "    conn = _connect()\n"
        "    try:\n"
        "        changed = _execute(conn, statement).rowcount\n"
        "        conn.commit()\n"
        "        if changed == 0:\n"
        "            return None\n"
        "    finally:\n"
        "        conn.close()\n"
        "    return get(entity, record_id, tenant_id)\n"
        "\n"
        "\n"
        "def delete(entity: str, record_id: int, tenant_id: str) -> bool:\n"
        '    """Delete a persisted row. True when a row was removed."""\n'
        "    table = _table(entity)\n"
        "    statement = sa.delete(table).where(\n"
        "        table.c.id == record_id, table.c.tenant_id == tenant_id\n"
        "    )\n"
        "    conn = _connect()\n"
        "    try:\n"
        "        removed = _execute(conn, statement).rowcount\n"
        "        conn.commit()\n"
        "        return removed > 0\n"
        "    finally:\n"
        "        conn.close()\n"
    )


def render_migrations() -> str:
    return (
        '"""Apply and roll back Alembic revisions for this platform.\n'
        "\n"
        "Deploy (scripts/entrypoint.sh) and FastAPI lifespan both call\n"
        "upgrade_head() against whichever engine app.db resolves. Failure\n"
        "refuses boot.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from pathlib import Path\n"
        "\n"
        "from alembic import command\n"
        "from alembic.config import Config\n"
        "\n"
        "# app.db, never app.store: store.py is writer-authored, so what its\n"
        "# connect() returns is not this module's to assume -- assuming sqlite3\n"
        "# is how a SQLAlchemy connection met a raw string and every build with\n"
        "# DATABASE_URL set died (ObjectNotExecutableError, 2026-09-27).\n"
        "from app.db import is_postgres, resolved_url, sqlite_path\n"
        "\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "\n"
        "\n"
        "def alembic_config() -> Config:\n"
        '    cfg = Config(str(ROOT / "alembic.ini"))\n'
        '    cfg.set_main_option("script_location", str(ROOT / "alembic"))\n'
        "    return cfg\n"
        "\n"
        "\n"
        "def upgrade_head() -> str | None:\n"
        '    command.upgrade(alembic_config(), "head")\n'
        "    # Set WAL once, here, single-threaded, before the app serves any\n"
        "    # request -- so no connection under load has to switch journal mode\n"
        "    # (that race raised 'database is locked'). Done inline, not through\n"
        "    # app.store: store.py is writer-authored and only has to route\n"
        "    # through app.db, so it may not expose a WAL helper. sqlite only;\n"
        "    # Postgres has no journal mode to set.\n"
        "    if not is_postgres():\n"
        "        import sqlite3\n"
        "        _c = sqlite3.connect(str(sqlite_path()))\n"
        "        try:\n"
        f'            _c.execute("PRAGMA journal_mode={SQLITE_JOURNAL_MODE}")\n'
        "        finally:\n"
        "            _c.close()\n"
        "    return current_revision()\n"
        "\n"
        "\n"
        "def upgrade_to(revision: str) -> str | None:\n"
        "    command.upgrade(alembic_config(), revision)\n"
        "    return current_revision()\n"
        "\n"
        "\n"
        "def downgrade(revision: str) -> str | None:\n"
        "    command.downgrade(alembic_config(), revision)\n"
        "    return current_revision()\n"
        "\n"
        "\n"
        "def current_revision() -> str | None:\n"
        "    # One code path for both engines: a short-lived SQLAlchemy engine\n"
        "    # on the same URL alembic itself uses. The sqlite existence check\n"
        "    # stays -- connecting through SQLAlchemy CREATES the file, and an\n"
        '    # empty database appearing because someone asked "which revision?"\n'
        "    # is a side effect nobody ordered.\n"
        "    from sqlalchemy import create_engine, text\n"
        "    from sqlalchemy.exc import DatabaseError\n"
        "\n"
        "    if not is_postgres() and not sqlite_path().exists():\n"
        "        return None\n"
        "    engine = create_engine(resolved_url())\n"
        "    try:\n"
        "        with engine.connect() as conn:\n"
        "            row = conn.execute(\n"
        '                text("SELECT version_num FROM alembic_version")\n'
        "            ).fetchone()\n"
        "            return str(row[0]) if row else None\n"
        "    except DatabaseError:\n"
        "        # No alembic_version table yet: not migrated, not an error.\n"
        "        return None\n"
        "    finally:\n"
        "        engine.dispose()\n"
        "\n"
        "\n"
        "def head_revision() -> str | None:\n"
        "    from alembic.script import ScriptDirectory\n"
        "\n"
        "    return ScriptDirectory.from_config(alembic_config()).get_current_head()\n"
    )


def render_backup() -> str:
    return (
        '"""Backup, restore, and retention for platform.db.\n'
        "\n"
        "Uses SQLite's online backup API (not a file copy) so a live WAL\n"
        "writer cannot produce a torn snapshot. A restore that has not been\n"
        "drilled is not a restore — tests/test_data_lifecycle.py performs\n"
        "backup → wipe → restore → assert rows.\n"
        "\n"
        "Same-disk BACKUP_DIR (the default) protects against logical loss,\n"
        "not disk loss. The mounted Render disk is a SPOF.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "import sqlite3\n"
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "from typing import List\n"
        "\n"
        "from app.store import db_path\n"
        "\n"
        f"DEFAULT_KEEP = {BACKUP_KEEP}\n"
        "\n"
        "\n"
        "def _utcstamp() -> str:\n"
        '    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")\n'
        "\n"
        "\n"
        "def backup_root() -> Path:\n"
        '    override = os.getenv("BACKUP_DIR", "").strip()\n'
        "    if override:\n"
        "        return Path(override)\n"
        '    return Path(os.getenv("STORAGE_PATH", "./data")) / "backups"\n'
        "\n"
        "\n"
        "def _sidecar_paths(db: Path) -> List[Path]:\n"
        '    return [Path(str(db) + suffix) for suffix in ("-wal", "-shm")]\n'
        "\n"
        "\n"
        "def create_backup() -> Path:\n"
        "    src = db_path()\n"
        "    dest_dir = backup_root()\n"
        "    dest_dir.mkdir(parents=True, exist_ok=True)\n"
        '    dest = dest_dir / f"platform-{_utcstamp()}.db"\n'
        "    src_conn = sqlite3.connect(str(src))\n"
        "    dest_conn = sqlite3.connect(str(dest))\n"
        "    try:\n"
        "        src_conn.backup(dest_conn)\n"
        "    finally:\n"
        "        dest_conn.close()\n"
        "        src_conn.close()\n"
        "    prune_backups(keep=DEFAULT_KEEP)\n"
        "    return dest\n"
        "\n"
        "\n"
        "def restore_backup(archive: Path, dest: Path | None = None) -> Path:\n"
        "    dest = dest or db_path()\n"
        "    dest.parent.mkdir(parents=True, exist_ok=True)\n"
        "    if dest.exists():\n"
        "        dest.unlink()\n"
        "    for side in _sidecar_paths(dest):\n"
        "        if side.exists():\n"
        "            side.unlink()\n"
        "    src_conn = sqlite3.connect(str(archive))\n"
        "    dest_conn = sqlite3.connect(str(dest))\n"
        "    try:\n"
        "        src_conn.backup(dest_conn)\n"
        "    finally:\n"
        "        dest_conn.close()\n"
        "        src_conn.close()\n"
        "    return dest\n"
        "\n"
        "\n"
        "def wipe_database() -> None:\n"
        "    target = db_path()\n"
        "    if target.exists():\n"
        "        target.unlink()\n"
        "    for side in _sidecar_paths(target):\n"
        "        if side.exists():\n"
        "            side.unlink()\n"
        "\n"
        "\n"
        "def prune_backups(keep: int = DEFAULT_KEEP) -> List[Path]:\n"
        "    root = backup_root()\n"
        "    if not root.is_dir():\n"
        "        return []\n"
        '    archives = sorted(root.glob("platform-*.db"))\n'
        "    removed: List[Path] = []\n"
        "    for stale in archives[: max(0, len(archives) - keep)]:\n"
        "        stale.unlink()\n"
        "        removed.append(stale)\n"
        "    return removed\n"
        "\n"
        "\n"
        "def list_backups() -> List[Path]:\n"
        "    root = backup_root()\n"
        "    if not root.is_dir():\n"
        "        return []\n"
        '    return sorted(root.glob("platform-*.db"))\n'
    )


def render_alembic_ini() -> str:
    return (
        "[alembic]\n"
        "script_location = alembic\n"
        "prepend_sys_path = .\n"
        "path_separator = os\n"
        "\n"
        "[loggers]\n"
        "keys = root,sqlalchemy,alembic\n"
        "\n"
        "[handlers]\n"
        "keys = console\n"
        "\n"
        "[formatters]\n"
        "keys = generic\n"
        "\n"
        "[logger_root]\n"
        "level = WARN\n"
        "handlers = console\n"
        "\n"
        "[logger_sqlalchemy]\n"
        "level = WARN\n"
        "handlers =\n"
        "qualname = sqlalchemy.engine\n"
        "\n"
        "[logger_alembic]\n"
        "level = INFO\n"
        "handlers =\n"
        "qualname = alembic\n"
        "\n"
        "[handler_console]\n"
        "class = StreamHandler\n"
        "args = (sys.stderr,)\n"
        "level = NOTSET\n"
        "formatter = generic\n"
        "\n"
        "[formatter_generic]\n"
        "format = %(levelname)-5.5s [%(name)s] %(message)s\n"
    )


def render_alembic_env() -> str:
    return (
        '"""Alembic env for a generated platform. app.db decides the engine."""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import sys\n"
        "from logging.config import fileConfig\n"
        "from pathlib import Path\n"
        "\n"
        "from alembic import context\n"
        "from sqlalchemy import create_engine, pool\n"
        "\n"
        "# The product root, so `app` imports when alembic is run as a bare CLI\n"
        "# (scripts/entrypoint.sh) as well as through app/migrations.py.\n"
        "ROOT = Path(__file__).resolve().parents[1]\n"
        "if str(ROOT) not in sys.path:\n"
        "    sys.path.insert(0, str(ROOT))\n"
        "\n"
        "# One place decides the engine. This file used to keep its own copy --\n"
        '# "Must match app.store.db_path(). Duplicated so a migration can run\n'
        '# before app is imported." -- and the copy hardcoded sqlite, so a\n'
        "# platform with DATABASE_URL set migrated one database and served\n"
        "# another. That is the exact failure the acceptance floor's own text\n"
        "# forbids: a DATABASE_URL that is read and then ignored.\n"
        "from app.db import resolved_url as sqlalchemy_url\n"
        "\n"
        "config = context.config\n"
        "if config.config_file_name is not None:\n"
        "    fileConfig(config.config_file_name)\n"
        "\n"
        "\n"
        "def run_migrations_offline() -> None:\n"
        "    context.configure(\n"
        "        url=sqlalchemy_url(),\n"
        "        literal_binds=True,\n"
        '        dialect_opts={"paramstyle": "named"},\n'
        "    )\n"
        "    with context.begin_transaction():\n"
        "        context.run_migrations()\n"
        "\n"
        "\n"
        "def run_migrations_online() -> None:\n"
        "    connectable = create_engine(sqlalchemy_url(), poolclass=pool.NullPool)\n"
        "    with connectable.connect() as connection:\n"
        "        context.configure(connection=connection)\n"
        "        with context.begin_transaction():\n"
        "            context.run_migrations()\n"
        "\n"
        "\n"
        "if context.is_offline_mode():\n"
        "    run_migrations_offline()\n"
        "else:\n"
        "    run_migrations_online()\n"
    )


def render_script_mako() -> str:
    return (
        '"""${message}\n'
        "\n"
        "Revision ID: ${up_revision}\n"
        "Revises: ${down_revision | comma,n}\n"
        "Create Date: ${create_date}\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "${imports if imports else ''}\n"
        "\n"
        "revision = ${repr(up_revision)}\n"
        "down_revision = ${repr(down_revision)}\n"
        "branch_labels = ${repr(branch_labels)}\n"
        "depends_on = ${repr(depends_on)}\n"
        "\n"
        "\n"
        "def upgrade() -> None:\n"
        "    ${upgrades if upgrades else 'pass'}\n"
        "\n"
        "\n"
        "def downgrade() -> None:\n"
        "    ${downgrades if downgrades else 'pass'}\n"
    )


def render_revision_0001(specs: Dict[str, Dict[str, Any]]) -> str:
    tables = table_specs(specs)
    upgrade_lines: List[str] = []
    downgrade_lines: List[str] = []
    for spec in tables:
        cols = [
            '        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),',
            '        sa.Column("tenant_id", sa.Text(), nullable=False),',
        ]
        seen = {"id"}
        for field in spec["fields"]:
            name = str(field.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            sa_type = _field_sa_type(field)
            cols.append(f'        sa.Column("{name}", {sa_type}, nullable=True),')
        upgrade_lines.append(f'    op.create_table(\n        "{spec["entity"]}",')
        upgrade_lines.extend(cols)
        upgrade_lines.append("    )")
        downgrade_lines.append(f'    op.drop_table("{spec["entity"]}")')
    upgrade_lines.append(f'    op.create_table(\n        "{WORK_QUEUE_TABLE}",')
    upgrade_lines.append(
        '        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),'
    )
    upgrade_lines.append('        sa.Column("capability_id", sa.Text(), nullable=False),')
    upgrade_lines.append('        sa.Column("tenant_id", sa.Text(), nullable=False),')
    upgrade_lines.append('        sa.Column("payload", sa.Text(), nullable=False),')
    upgrade_lines.append('        sa.Column("status", sa.Text(), nullable=False),')
    upgrade_lines.append('        sa.Column("result", sa.Text(), nullable=True),')
    upgrade_lines.append('        sa.Column("idempotency_key", sa.Text(), nullable=True),')
    upgrade_lines.append("    )")
    upgrade_lines.append(f'    op.create_table(\n        "{IDEMPOTENCY_TABLE}",')
    upgrade_lines.append('        sa.Column("key", sa.Text(), primary_key=True),')
    upgrade_lines.append('        sa.Column("entity", sa.Text(), nullable=False),')
    upgrade_lines.append('        sa.Column("record_id", sa.Integer(), nullable=False),')
    upgrade_lines.append("    )")
    downgrade_lines.append(f'    op.drop_table("{WORK_QUEUE_TABLE}")')
    downgrade_lines.append(f'    op.drop_table("{IDEMPOTENCY_TABLE}")')
    downgrade_lines = list(reversed(downgrade_lines))
    return (
        '"""v1 domain tables from the capability specs.\n'
        "\n"
        f"Revision ID: {REVISION_0001}\n"
        "Revises:\n"
        "Create Date: 2026-08-23\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "\n"
        f'revision = "{REVISION_0001}"\n'
        "down_revision = None\n"
        "branch_labels = None\n"
        "depends_on = None\n"
        "\n"
        "\n"
        "def upgrade() -> None:\n"
        + "\n".join(upgrade_lines)
        + "\n"
        "\n"
        "\n"
        "def downgrade() -> None:\n"
        + "\n".join(downgrade_lines)
        + "\n"
    )


def render_revision_0002() -> str:
    return (
        '"""v2 schema change: lifecycle_audit table (up and down).\n'
        "\n"
        f"Revision ID: {REVISION_0002}\n"
        f"Revises: {REVISION_0001}\n"
        "Create Date: 2026-08-23\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from alembic import op\n"
        "import sqlalchemy as sa\n"
        "\n"
        f'revision = "{REVISION_0002}"\n'
        f'down_revision = "{REVISION_0001}"\n'
        "branch_labels = None\n"
        "depends_on = None\n"
        "\n"
        "\n"
        "def upgrade() -> None:\n"
        "    op.create_table(\n"
        f'        "{AUDIT_TABLE}",\n'
        '        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),\n'
        '        sa.Column("event", sa.Text(), nullable=False),\n'
        '        sa.Column("at", sa.Text(), nullable=False),\n'
        "    )\n"
        "\n"
        "\n"
        "def downgrade() -> None:\n"
        f'    op.drop_table("{AUDIT_TABLE}")\n'
    )


def render_entrypoint() -> str:
    return (
        "#!/bin/sh\n"
        "# Apply versioned migrations against the persistent disk, then serve.\n"
        "# Failure here refuses boot (fail-closed). Do not start uvicorn on a\n"
        "# schema that is behind head.\n"
        "set -eu\n"
        "cd /app\n"
        "python -m alembic upgrade head\n"
        "python -c \"import json, os; print(json.dumps({'event': 'entrypoint.start', 'revision': os.getenv('APP_REVISION', ''), 'storage': os.getenv('STORAGE_PATH', '')}))\"\n"
        'exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"\n'
    )


def lifecycle_declaration() -> Dict[str, Any]:
    return {
        "schema_version": "data_lifecycle.v1",
        "migrations": {
            "tool": "alembic",
            "source": (
                "RoleRunner emission, patterned on the Store estate kit's "
                "steward_runtime/migrations"
            ),
            "revisions": [REVISION_0001, REVISION_0002],
            "applied_at": "deploy entrypoint + FastAPI lifespan",
            "storage_path": "/app/data",
            "database": "platform.db",
        },
        "durability": {
            "journal_mode": SQLITE_JOURNAL_MODE,
            "synchronous": "NORMAL",
            "busy_timeout_ms": SQLITE_BUSY_TIMEOUT_MS,
            "connect_timeout_s": SQLITE_CONNECT_TIMEOUT_S,
            "fastapi_sync_threadpool": FASTAPI_SYNC_THREADPOOL,
            "writers": 1,
        },
        "backup": {
            "api": "sqlite3.Connection.backup",
            "default_dir": "$STORAGE_PATH/backups",
            "retention": BACKUP_KEEP,
            "restore_drill": "tests/test_data_lifecycle.py performs backup→wipe→restore",
        },
        "sqlite_on_mounted_disk": True,
        "spof": (
            "SPOF: Render persistent disk is single-instance and the live "
            "SQLite file lives on it. One writer. Losing the disk loses the "
            "live database. Same-disk backups do not survive disk loss; set "
            "BACKUP_DIR onto another volume if disk loss is in scope."
        ),
        "capacity": {
            "disk_gb": DISK_SIZE_GB,
            "practical_sqlite": (
                "Bound by the 1 GiB volume declared in deploy/contract.json, "
                "not SQLite's theoretical file limit."
            ),
            "ha": False,
            "replicas": 0,
        },
    }


def render_lifecycle_doc() -> str:
    return json.dumps(lifecycle_declaration(), indent=2, sort_keys=True) + "\n"


class EmittedSuiteContractError(ValueError):
    """The Factory rendered a lifecycle suite that expects to read back a key
    the product's store never writes as declared data. That is the Factory's
    defect, raised at render time so it ends the run as a Factory fault --
    it must never reach the writer as a rework it cannot fix."""


def check_sample_is_declared(entity: str, sample: Dict[str, Any], columns: List[str]) -> None:
    """Every key the suite will insert and read back is a declared column."""
    stray = sorted(k for k in sample if k not in columns)
    if stray:
        raise EmittedSuiteContractError(
            f"lifecycle sample for {entity!r} expects undeclared column(s) "
            f"{stray}; declared: {columns}"
        )


def render_product_tests(specs: Dict[str, Dict[str, Any]]) -> str:
    entity, sample = first_entity_sample(specs)
    if entity:
        check_sample_is_declared(entity, sample, columns_map(specs).get(entity, []))
        entity_spec = next((s for s in specs.values() if s.get("entity") == entity), None)
        check_sample_fits_declared_types(entity, sample, entity_spec)
    entities = [spec["entity"] for spec in table_specs(specs)]
    return f'''"""S10 data lifecycle — performed, not configured.

Schema up/down on a populated v1 DB, a restore drill (backup → wipe →
restore → assert rows), and parallel writes at the FastAPI sync threadpool
size. connect() must not CREATE TABLE.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from app import backup, store
from app.migrations import current_revision, downgrade, upgrade_head, upgrade_to

ENTITY = {entity!r}
SAMPLE = {sample!r}
#: Tenant the generated tests act as. Tenant-scoped store calls require
#: an explicit tenant — never a default (the tenancy module's rule).
TENANT = "test-tenant"
ENTITIES = {entities!r}
REV_V1 = {REVISION_0001!r}
REV_V2 = {REVISION_0002!r}
AUDIT = {AUDIT_TABLE!r}
#: The declared persistence contract (data_lifecycle.SQLITE_JOURNAL_MODE):
#: the journal mode the Factory's boot path sets. Asserted, never invented.
DECLARED_JOURNAL_MODE = {SQLITE_JOURNAL_MODE.lower()!r}


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "data"))
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    return tmp_path


def _tables() -> set[str]:
    conn = store.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {{r[0] for r in rows}}
    finally:
        conn.close()


def test_connect_is_wal_with_a_busy_timeout_and_creates_nothing(isolated_db):
    """Observed on the connection and the module's syntax tree, not searched
    for in store.py's text. WAL is a property of the file: set by connect(),
    by a declared enable_wal(), or -- the Factory's own design -- once at
    boot by app.migrations.upgrade_head(), which says store.py (writer-
    authored) need not expose a WAL helper. All three are accepted; none
    may be missing. A connection by itself must never create a table."""
    import ast
    import inspect

    conn = store.connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        made = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert int(timeout) > 0
    assert made == 0
    src = Path(inspect.getsourcefile(store)).read_text(encoding="utf-8")
    defs = {{
        n.name for n in ast.walk(ast.parse(src))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }}
    if str(mode).lower() == DECLARED_JOURNAL_MODE or "enable_wal" in defs:
        return
    # The boot path owns WAL: after it runs, a fresh connection is WAL.
    upgrade_head()
    conn = store.connect()
    try:
        booted = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert str(booted).lower() == DECLARED_JOURNAL_MODE, (
        "WAL is set by none of connect(), app.store.enable_wal() or the "
        "boot path app.migrations.upgrade_head()"
    )


def test_connect_does_not_create_domain_tables(isolated_db):
    conn = store.connect()
    try:
        names = {{
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }}
    finally:
        conn.close()
    for name in ENTITIES:
        assert name not in names, name


@pytest.mark.skipif(not ENTITY, reason="no domain entity to migrate")
def test_schema_change_applies_to_populated_v1_and_rolls_back(isolated_db):
    assert upgrade_to(REV_V1) == REV_V1
    saved = store.save(ENTITY, dict(SAMPLE), tenant_id=TENANT)
    assert saved["id"] is not None
    assert store.get(ENTITY, saved["id"], tenant_id=TENANT) is not None
    assert ENTITY in _tables()
    assert AUDIT not in _tables()

    # Upgrade to V2 explicitly, not to head: this test is about the
    # v1 -> v2 transition over populated data, and pinning the absolute
    # head would make the product unextendable -- any migration the
    # agent later authors for its own capability would fail this suite.
    assert upgrade_to(REV_V2) == REV_V2
    fetched = store.get(ENTITY, saved["id"], tenant_id=TENANT)
    assert fetched is not None, "v1 row did not survive upgrade to v2"
    for key, value in SAMPLE.items():
        assert fetched[key] == value
    assert AUDIT in _tables()
    assert current_revision() == REV_V2

    assert downgrade(REV_V1) == REV_V1
    rolled = store.get(ENTITY, saved["id"], tenant_id=TENANT)
    assert rolled is not None, "v1 row did not survive downgrade"
    for key, value in SAMPLE.items():
        assert rolled[key] == value
    assert AUDIT not in _tables()
    assert current_revision() == REV_V1


@pytest.mark.skipif(not ENTITY, reason="no domain entity to restore")
def test_restore_drill_backup_wipe_restore_rows(isolated_db):
    upgrade_head()
    original = [store.save(ENTITY, dict(SAMPLE), tenant_id=TENANT) for _ in range(3)]
    ids = [row["id"] for row in original]
    archive = backup.create_backup()
    assert archive.is_file() and archive.stat().st_size > 0

    backup.wipe_database()
    assert not store.db_path().exists()

    backup.restore_backup(archive)
    assert store.db_path().exists()
    restored_ids = [row["id"] for row in store.list_all(ENTITY, tenant_id=TENANT)]
    assert restored_ids == ids
    for row in original:
        fetched = store.get(ENTITY, row["id"], tenant_id=TENANT)
        assert fetched is not None
        for key, value in SAMPLE.items():
            assert fetched[key] == value


@pytest.mark.skipif(not ENTITY, reason="no domain entity to write")
def test_parallel_writes_match_fastapi_threadpool(isolated_db):
    upgrade_head()
    workers = store.FASTAPI_SYNC_THREADPOOL

    def _write(i: int):
        payload = dict(SAMPLE)
        if "reference" in payload:
            payload["reference"] = f"s10-{{i}}"
        return store.save(ENTITY, payload, tenant_id=TENANT)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_write, i) for i in range(workers)]
        results = [f.result() for f in as_completed(futures)]
    assert len(results) == workers
    assert all(r["id"] is not None for r in results)
    persisted = store.list_all(ENTITY, tenant_id=TENANT)
    assert len(persisted) == workers
    assert {{r["id"] for r in persisted}} == {{r["id"] for r in results}}


def test_wal_and_busy_timeout_after_migrate(isolated_db):
    upgrade_head()
    conn = store.connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == DECLARED_JOURNAL_MODE
    assert int(timeout) >= store.SQLITE_BUSY_TIMEOUT_MS


def test_retention_prunes_old_backups(isolated_db):
    upgrade_head()
    store.save(ENTITY, dict(SAMPLE), tenant_id=TENANT) if ENTITY else None
    root = backup.backup_root()
    root.mkdir(parents=True, exist_ok=True)
    for i in range(6):
        (root / f"platform-2026080{{i}}T000000Z.db").write_bytes(b"stub")
    removed = backup.prune_backups(keep=3)
    remaining = sorted(p.name for p in backup.list_backups() if p.stat().st_size == 4)
    assert len(removed) == 3
    assert remaining == [
        "platform-20260803T000000Z.db",
        "platform-20260804T000000Z.db",
        "platform-20260805T000000Z.db",
    ]
'''


def emit_writer_artifacts(workspace: Any, specs: Dict[str, Dict[str, Any]]) -> None:
    """Write persistence, Alembic, backup, entrypoint, and the SPOF doc.

    Floor WRITER passes :class:`~app.factory.build.workspace.RoleWorkspace`
    (``write_text(rel, content)``). Path roots and incomplete selfcheck
    handles fall back through :func:`write_workspace_text`
    (CEREBRUMDEV-BACKEND-V).
    """
    write_workspace_text(workspace, Path("app") / "store.py", render_store(specs))
    write_workspace_text(workspace, Path("app") / "migrations.py", render_migrations())
    write_workspace_text(workspace, Path("app") / "backup.py", render_backup())
    write_workspace_text(workspace, "alembic.ini", render_alembic_ini())
    write_workspace_text(workspace, Path("alembic") / "env.py", render_alembic_env())
    write_workspace_text(
        workspace, Path("alembic") / "script.py.mako", render_script_mako()
    )
    write_workspace_text(
        workspace,
        Path("alembic") / "versions" / "0001_baseline.py",
        render_revision_0001(specs),
    )
    write_workspace_text(
        workspace,
        Path("alembic") / "versions" / "0002_lifecycle_audit.py",
        render_revision_0002(),
    )
    write_workspace_text(
        workspace, Path("docs") / "data_lifecycle.json", render_lifecycle_doc()
    )
    write_workspace_text(
        workspace, Path("scripts") / "entrypoint.sh", render_entrypoint()
    )
    # The ops floor, on THIS path too. These were added to
    # platform_substrate() (the CodeWhale backfill) and not here, so a build
    # that took the in-process writer got tests/test_backup_restore.py from
    # TESTER -- which is emitted unconditionally -- with no scripts/backup.sh
    # for it to run. "scripts/backup.sh is missing" in CI, and it was right.
    write_workspace_text(workspace, Path("app") / "db.py", render_db_module())
    write_workspace_text(
        workspace, Path("app") / "observability.py", render_observability()
    )
    write_workspace_text(workspace, Path("scripts") / "backup.sh", render_backup_script())
    write_workspace_text(workspace, Path("scripts") / "bench.py", render_bench_script())


#: The spec-independent half of :func:`emit_writer_artifacts`.
#:
#: ``run_writer`` returns at its CodeWhale branch long before
#: ``emit_writer_artifacts`` is reached, and ``FACTORY_CODEWHALE_WRITER=1``
#: makes that the only path production takes -- so none of this substrate was
#: written, while ``run_tester`` still stamped ``tests/test_data_lifecycle.py``,
#: which opens ``from app import backup, store``. Live build
#: sess_b6d51f9089e14176 died on exactly that:
#: ``ImportError: cannot import name 'backup' from 'app'``.
#:
#: ``app/store.py`` and ``0001_baseline`` are deliberately absent: they carry
#: the entity schema, the agent authors them, and overwriting them would
#: destroy the capability work this backfill exists to protect.



def platform_substrate() -> List[Tuple[str, str]]:
    """(relpath, content) for every substrate file that needs no specs."""
    return [
        ("app/migrations.py", render_migrations()),
        ("app/backup.py", render_backup()),
        ("alembic.ini", render_alembic_ini()),
        ("alembic/env.py", render_alembic_env()),
        ("alembic/script.py.mako", render_script_mako()),
        (f"alembic/versions/{REVISION_0002}.py", render_revision_0002()),
        ("scripts/entrypoint.sh", render_entrypoint()),
        ("docs/data_lifecycle.json", render_lifecycle_doc()),
        # The ops floor. A pilot goes to a DevOps team who cannot answer
        # "is it up, is it slow, is it being hammered" from logs alone, and
        # a backup nobody has restored is a file. None of it is
        # domain-specific: it counts requests and seconds, and restores
        # whatever the platform stores on.
        ("app/db.py", render_db_module()),
        ("app/observability.py", render_observability()),
        ("scripts/backup.sh", render_backup_script()),
        ("scripts/bench.py", render_bench_script()),
    ]


def backfill_platform_substrate(workspace: Any) -> Dict[str, List[str]]:
    """Write substrate the agent was never asked for. Never overwrites.

    Anything already on disk is left exactly as the agent wrote it -- this
    fills gaps, it does not converge. Returns what was written and what was
    skipped so the pass is visible on the Floor rather than silent.
    """
    written: List[str] = []
    skipped: List[str] = []
    for rel, content in platform_substrate():
        if workspace.exists(rel):
            skipped.append(rel)
            continue
        # A module must not shadow a package the agent already wrote. The
        # writer prompt asks for ``app/migrations/``; this module is
        # ``app/migrations.py``. With both present the package wins and
        # ``from app.migrations import upgrade_head`` fails in a way that
        # reads as the agent's bug. Leave it alone and say so.
        if rel.endswith(".py") and workspace.exists(rel[:-3]):
            skipped.append(f"{rel} (a package of the same name exists)")
            continue
        write_workspace_text(workspace, Path(rel), content)
        written.append(rel)
    return {"written": written, "skipped": skipped}


def _sql_creates_storage(statement: str) -> bool:
    """True when SQLite compiles ``statement`` to a program that creates a
    table or index. Decided by the engine (EXPLAIN, nothing executed), never
    by searching the text; a string that is not SQL is simply not DDL."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    try:
        rows = conn.execute("EXPLAIN " + statement).fetchall()
    except (sqlite3.Error, ValueError):
        return False
    finally:
        conn.close()
    return any(len(r) > 1 and r[1] == "CreateBtree" for r in rows)


def connect_time_ddl(store_source: str) -> list:
    """String constants in a store module that SQLite compiles to DDL. An
    f-string's replacement fields stand in as a plain identifier."""
    import ast

    try:
        tree = ast.parse(store_source)
    except SyntaxError:
        return []
    inside = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
    found = []
    for node in ast.walk(tree):
        if id(node) in inside:
            continue
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                str(v.value) if isinstance(v, ast.Constant) else "t" for v in node.values
            )
        else:
            continue
        if _sql_creates_storage(text):
            found.append(text.strip()[:80])
    return found


def assert_no_connect_time_ddl(store_source: str) -> None:
    ddl = connect_time_ddl(store_source)
    if ddl:
        raise ValueError(
            "store.py still emits table DDL (schema belongs in Alembic): " + "; ".join(ddl)
        )


def migration_table_names(revision_0001_source: str) -> set[str]:
    created: set[str] = set()
    token = "op.create_table("
    idx = 0
    while True:
        found = revision_0001_source.find(token, idx)
        if found < 0:
            break
        after = revision_0001_source[found + len(token) :]
        quote = after.find('"')
        if quote < 0:
            break
        end = after.find('"', quote + 1)
        created.add(after[quote + 1 : end])
        idx = found + len(token)
    return created
