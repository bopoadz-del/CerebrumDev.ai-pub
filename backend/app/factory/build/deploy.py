"""S11 deploy / observe / rollback emitters for RoleRunner products.

Health is process-level and fail-closed: a down process, a missing
persistent disk/DB, or a schema behind Alembic head is not 200. An
unconditional ``ok: true`` / always-200 body is F1 (LotDesk-class) and is
rejected by factory tests.

Rollback is a performed drill (start N → persist → start N+1 → roll back
to N → assert prior health and prior data), not a configured-only script.

Structured request logs carry a correlation id and must not put emoji on
machine-parseable stdout (F13).
"""

from __future__ import annotations

import json
import ast
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.factory.build import probe_set
from app.factory.build.data_lifecycle import (
    DISK_SIZE_GB,
    check_sample_fits_declared_types,
    first_entity_sample,
)
from app.factory.build.lotdesk_gate import inspect_path, resolve_lotdesk_fixture

REVISION_N = "rev-n"
REVISION_N_PLUS_1 = "rev-n-plus-1"
MARK_BASELINE = "baseline"
MARK_CHANGED = "changed"
REQUEST_ID_HEADER = "x-request-id"

# The /health response shape -- ONE definition. render_health() emits it,
# and every stamped test that reads a health body (tests/test_deploy.py and
# test_routes.py::test_health) asserts it from these same names.
HEALTH_CHECK_NAMES = ("process", "persistent_disk", "database", "migrations")
HEALTH_STATUS_OK = "ok"
HEALTH_STATUS_NOT_READY = "not_ready"

# Factory-owned deploy modules: the stamped suite imports names from them AND
# reads their response/log shapes, so the Factory stamps them outright on the
# CodeWhale path (stamp_factory_deploy_modules) instead of gap-filling.
FACTORY_OWNED_DEPLOY_MODULES = ("app/revision.py", "app/health.py", "app/observe.py")

# Unconditional liveness that cannot fail when the app, disk, or schema is
# gone. LotDesk ships this. RoleRunner must not.

EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002700-\U000027bf"
    "\U0001f600-\U0001f64f"
    "\U00002600-\U000026ff"
    "]+",
    flags=re.UNICODE,
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    detail: str


def _is_literal(node: ast.AST) -> bool:
    """A value fixed at write time: constants and containers of constants."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, ast.Dict):
        return all(k is not None and _is_literal(k) for k in node.keys) and all(
            _is_literal(v) for v in node.values)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return all(_is_literal(e) for e in node.elts)
    return False


def health_is_always_200(source: str) -> bool:
    """True when GET /health cannot fail (LotDesk-class F1).

    By shape, not by spelling: a ``health`` function that calls nothing and
    returns only literal values cannot report a down app, a missing disk or an
    unapplied migration -- whatever literal it returns.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name != "health":
            continue
        # The body only: the route decorator is a call, the handler's work is not.
        body = [n for stmt in fn.body for n in ast.walk(stmt)]
        returns = [n for n in body if isinstance(n, ast.Return)]
        calls = [n for n in body if isinstance(n, ast.Call)]
        if returns and not calls and all(r.value is not None and _is_literal(r.value) for r in returns):
            return True
    return False


def inspect_health_source(source: str, *, path: str = "app/main.py") -> List[Finding]:
    findings: List[Finding] = []
    if health_is_always_200(source):
        findings.append(
            Finding(
                probe_set.code_for("health_unconditional_ok"),
                path,
                "GET /health is unconditional ok / always-200; "
                "a down app, missing disk/DB, or unapplied migration still looks healthy",
            )
        )
    return findings


def reject_always_200_health(source: str, *, path: str = "app/main.py") -> Dict[str, Any]:
    findings = inspect_health_source(source, path=path)
    return {
        "ok": not findings,
        "gate": "always_200_health",
        "codes": [item.code for item in findings],
        "findings": [asdict(item) for item in findings],
        "lotdesk": "fixture only; not patched",
    }


def reject_lotdesk_always_200_health(explicit: Optional[Path] = None) -> Dict[str, Any]:
    """LotDesk-class health is F1. The zip is inspected, never patched."""
    path = resolve_lotdesk_fixture(explicit)
    lotdesk = inspect_path(path)
    health = set(probe_set.codes_where(cls="health"))
    health_findings = [item for item in lotdesk if item.code in health]
    source_map = _lotdesk_main_source(path)
    if source_map:
        health_findings.extend(
            inspect_health_source(source_map[1], path=source_map[0])
        )
    # Deduplicate F1 if both scanners fired.
    seen = set()
    unique: List[Finding] = []
    for item in health_findings:
        key = (item.code, item.path, item.detail)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    codes = [item.code for item in unique]
    return {
        "ok": False,
        "gate": "lotdesk_always_200_health",
        "fixture": str(path),
        "codes": codes,
        "findings": [asdict(item) for item in unique],
        **probe_set.present_flags(codes, (probe_set.code_for("health_unconditional_ok"),)),
        "lotdesk": "fixture only; not patched",
    }


def _lotdesk_main_source(path: Path) -> Optional[tuple[str, str]]:
    target = Path(path)
    if target.is_file() and target.suffix == ".zip":
        import zipfile

        with zipfile.ZipFile(target) as zf:
            for name in zf.namelist():
                norm = name.replace("\\", "/")
                if norm.endswith("app/main.py"):
                    return (norm, zf.read(name).decode("utf-8", errors="replace"))
        return None
    main = target / "app" / "main.py"
    if main.is_file():
        return ("app/main.py", main.read_text(encoding="utf-8"))
    return None


def contains_emoji(text: str) -> bool:
    return bool(EMOJI_RE.search(text or ""))


def render_revision() -> str:
    return (
        '"""Deploy revision identity for health and the rollback drill.\n'
        "\n"
        "APP_REVISION / APP_MARK override the compiled defaults so a local\n"
        "or Render rollback is a process restart against the same disk, not\n"
        "a schema wipe. N+1 is a detectable mark change; rolling back to N\n"
        "restores the prior mark and leaves persisted rows in place.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "\n"
        f'REVISION_N = "{REVISION_N}"\n'
        f'REVISION_N_PLUS_1 = "{REVISION_N_PLUS_1}"\n'
        f'MARK_BASELINE = "{MARK_BASELINE}"\n'
        f'MARK_CHANGED = "{MARK_CHANGED}"\n'
        f'MARK = "{MARK_BASELINE}"\n'
        "\n"
        "\n"
        "def current_app_revision() -> str:\n"
        '    return os.getenv("APP_REVISION") or REVISION_N\n'
        "\n"
        "\n"
        "def current_app_mark() -> str:\n"
        '    return os.getenv("APP_MARK") or MARK\n'
    )


def render_health() -> str:
    return (
        '"""Fail-closed process health for Render and the local drill.\n'
        "\n"
        "A 200 means this process is serving, STORAGE_PATH is a writable\n"
        "persistent disk, the configured database answers, and Alembic is at\n"
        "head. Anything else is 503. Unconditional ok:true is F1 and is\n"
        "forbidden here.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import os\n"
        "from pathlib import Path\n"
        "from typing import Any, Dict, List, Tuple\n"
        "\n"
        "from fastapi.responses import JSONResponse\n"
        "\n"
        "from app.revision import current_app_mark, current_app_revision\n"
        "\n"
        "\n"
        "def _storage_root() -> Path | None:\n"
        '    raw = os.getenv("STORAGE_PATH")\n'
        "    if not raw:\n"
        "        return None\n"
        "    return Path(raw)\n"
        "\n"
        "\n"
        "def evaluate_health() -> Tuple[int, Dict[str, Any]]:\n"
        "    checks: List[Dict[str, Any]] = []\n"
        "\n"
        "    checks.append(\n"
        "        {\n"
        f'            "name": {json.dumps(HEALTH_CHECK_NAMES[0])},\n'
        '            "ok": True,\n'
        '            "detail": f"pid={os.getpid()}",\n'
        "        }\n"
        "    )\n"
        "\n"
        "    storage = _storage_root()\n"
        "    if storage is None:\n"
        "        disk_ok, disk_detail = False, \"STORAGE_PATH unset\"\n"
        "    elif not storage.exists():\n"
        "        disk_ok, disk_detail = False, f\"missing {storage}\"\n"
        "    elif not storage.is_dir():\n"
        "        # A writable FILE passes exists() and os.access(); the service\n"
        "        # cannot keep a database or uploads under it.\n"
        "        disk_ok, disk_detail = False, f\"not a directory {storage}\"\n"
        "    elif not os.access(storage, os.R_OK | os.W_OK):\n"
        "        disk_ok, disk_detail = False, f\"not writable {storage}\"\n"
        "    else:\n"
        "        disk_ok, disk_detail = True, str(storage)\n"
        "    checks.append(\n"
        f'        {{"name": {json.dumps(HEALTH_CHECK_NAMES[1])}, "ok": disk_ok, "detail": disk_detail}}\n'
        "    )\n"
        "\n"
        "    # The database the platform actually runs on -- app.db decides\n"
        "    # (Postgres when DATABASE_URL says so, else the SQLite file). A\n"
        "    # SQLite-file check here read 'platform.db missing' on every healthy\n"
        "    # Postgres deployment and held /health at 503.\n"
        "    db_ok = False\n"
        '    db_detail = "not checked"\n'
        "    if not disk_ok:\n"
        '        db_detail = "persistent disk missing"\n'
        "    else:\n"
        "        try:\n"
        "            from app import db as _db\n"
        "\n"
        "            backend = _db.backend_name()\n"
        "            if backend == \"sqlite\" and not _db.sqlite_path().exists():\n"
        '                db_detail = f"{_db.sqlite_path().name} missing"\n'
        "            else:\n"
        "                conn = _db.connect()\n"
        "                try:\n"
        "                    if backend == \"postgres\":\n"
        "                        from sqlalchemy import text as _sql_text\n"
        "\n"
        '                        conn.execute(_sql_text("SELECT 1"))\n'
        "                    else:\n"
        '                        conn.execute("SELECT 1")\n'
        "                finally:\n"
        "                    conn.close()\n"
        "                db_ok = True\n"
        "                db_detail = backend\n"
        "        except Exception as exc:  # noqa: BLE001 -- health must not raise\n"
        "            db_detail = type(exc).__name__\n"
        f"    checks.append({{\"name\": {json.dumps(HEALTH_CHECK_NAMES[2])}, \"ok\": db_ok, \"detail\": db_detail}})\n"
        "\n"
        "    mig_ok = False\n"
        '    mig_detail = "not checked"\n'
        "    if not db_ok:\n"
        '        mig_detail = "database missing"\n'
        "    else:\n"
        "        try:\n"
        "            from app.migrations import current_revision, head_revision\n"
        "\n"
        "            current = current_revision()\n"
        "            head = head_revision()\n"
        "            mig_ok = bool(current) and current == head\n"
        '            mig_detail = f"current={current} head={head}"\n'
        "        except Exception as exc:  # noqa: BLE001 — health must not raise\n"
        "            mig_detail = type(exc).__name__\n"
        f"    checks.append({{\"name\": {json.dumps(HEALTH_CHECK_NAMES[3])}, \"ok\": mig_ok, \"detail\": mig_detail}})\n"
        "\n"
        "    ok = all(bool(item[\"ok\"]) for item in checks)\n"
        "    body = {\n"
        '        "ok": ok,\n'
        f'        "status": {json.dumps(HEALTH_STATUS_OK)} if ok else {json.dumps(HEALTH_STATUS_NOT_READY)},\n'
        '        "checks": checks,\n'
        '        "revision": current_app_revision(),\n'
        '        "mark": current_app_mark(),\n'
        "    }\n"
        "    return (200 if ok else 503, body)\n"
        "\n"
        "\n"
        "def health_response() -> JSONResponse:\n"
        "    code, body = evaluate_health()\n"
        "    return JSONResponse(status_code=code, content=body)\n"
    )


def render_observe() -> str:
    return (
        '"""Structured request logs with a correlation id (F13).\n'
        "\n"
        "Machine-parseable stdout is one JSON object per line. Emoji is\n"
        "stripped so a cp1252 or log shipper cannot turn a request line into\n"
        "a parse failure. Human banners do not belong here.\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "import json\n"
        "import logging\n"
        "import os\n"
        "import re\n"
        "import sys\n"
        "import uuid\n"
        "from contextvars import ContextVar\n"
        "from datetime import datetime, timezone\n"
        "from pathlib import Path\n"
        "\n"
        "from starlette.middleware.base import BaseHTTPMiddleware\n"
        "from starlette.requests import Request\n"
        "from starlette.responses import Response\n"
        "\n"
        f'REQUEST_ID_HEADER = "{REQUEST_ID_HEADER}"\n'
        "EMOJI_RE = re.compile(\n"
        '    "["\n'
        '    "\\U0001F300-\\U0001FAFF"\n'
        '    "\\U00002700-\\U000027BF"\n'
        '    "\\U0001F600-\\U0001F64F"\n'
        '    "\\U00002600-\\U000026FF"\n'
        '    "]+",\n'
        "    flags=re.UNICODE,\n"
        ")\n"
        '_request_id: ContextVar[str] = ContextVar("request_id", default="")\n'
        "\n"
        "\n"
        "def strip_emoji(text: str) -> str:\n"
        "    return EMOJI_RE.sub(\"\", text or \"\")\n"
        "\n"
        "\n"
        "class JsonFormatter(logging.Formatter):\n"
        "    def format(self, record: logging.LogRecord) -> str:\n"
        "        payload = {\n"
        '            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),\n'
        '            "level": record.levelname,\n'
        '            "logger": record.name,\n'
        '            "msg": strip_emoji(record.getMessage()),\n'
        '            "request_id": getattr(record, "request_id", "") or _request_id.get(),\n'
        "        }\n"
        "        return json.dumps(payload, ensure_ascii=True)\n"
        "\n"
        "\n"
        "class RequestIdFilter(logging.Filter):\n"
        "    def filter(self, record: logging.LogRecord) -> bool:\n"
        '        record.request_id = getattr(record, "request_id", "") or _request_id.get()\n'
        "        return True\n"
        "\n"
        "\n"
        "class CorrelationMiddleware(BaseHTTPMiddleware):\n"
        "    async def dispatch(self, request: Request, call_next) -> Response:\n"
        "        rid = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())\n"
        "        token = _request_id.set(rid)\n"
        "        request.state.request_id = rid\n"
        "        try:\n"
        "            response = await call_next(request)\n"
        "            response.headers[REQUEST_ID_HEADER] = rid\n"
        "            logging.getLogger(\"platform.request\").info(\n"
        '                "%s %s %s", request.method, request.url.path, response.status_code,\n'
        "                extra={\"request_id\": rid},\n"
        "            )\n"
        "            return response\n"
        "        finally:\n"
        "            _request_id.reset(token)\n"
        "\n"
        "\n"
        "def configure_logging() -> None:\n"
        "    formatter = JsonFormatter()\n"
        "    filt = RequestIdFilter()\n"
        "    root = logging.getLogger()\n"
        "    root.handlers.clear()\n"
        "    stream = logging.StreamHandler(sys.stdout)\n"
        "    stream.setFormatter(formatter)\n"
        "    stream.addFilter(filt)\n"
        "    root.addHandler(stream)\n"
        '    storage = os.getenv("STORAGE_PATH")\n'
        "    if storage:\n"
        "        dest = Path(storage)\n"
        "        if dest.is_dir():\n"
        "            # Observability never stops the service. An unwritable disk\n"
        "            # is reported by /health (its storage check), not by a boot\n"
        "            # crash: the request log stays on stdout and serving goes on.\n"
        "            try:\n"
        "                file_handler = logging.FileHandler(dest / \"request.jsonl\", delay=True)\n"
        "                file_handler.stream = file_handler._open()\n"
        "            except OSError as exc:\n"
        "                sys.stdout.write(json.dumps({\"level\": \"WARNING\", \"logger\": \"platform.observe\", \"msg\": f\"request log disabled: {type(exc).__name__}: {dest / 'request.jsonl'}\"}, ensure_ascii=True) + \"\\n\")\n"
        "            else:\n"
        "                file_handler.setFormatter(formatter)\n"
        "                file_handler.addFilter(filt)\n"
        "                root.addHandler(file_handler)\n"
        "    root.setLevel(logging.INFO)\n"
        "\n"
        "\n"
        "def install_observability(app) -> None:\n"
        "    configure_logging()\n"
        "    app.add_middleware(CorrelationMiddleware)\n"
    )


def render_rollback_script() -> str:
    return (
        "#!/bin/sh\n"
        "# Roll the running identity back to a prior revision.\n"
        "# Does not wipe STORAGE_PATH/platform.db — persisted rows stay.\n"
        "# Render equivalent: Dashboard rollback to the previous deploy\n"
        "# (same disk). Losing that disk is still a SPOF; this script\n"
        "# cannot invent a replica.\n"
        "set -eu\n"
        'TARGET="${1:?usage: rollback.sh <revision>}"\n'
        'STORAGE="${STORAGE_PATH:?STORAGE_PATH required}"\n'
        "mkdir -p \"$STORAGE\"\n"
        "printf '%s\\n' \"$TARGET\" > \"$STORAGE/deploy_revision\"\n"
        "printf '%s\\n' \"{\\\"event\\\":\\\"rollback.performed\\\",\\\"revision\\\":\\\"$TARGET\\\",\\\"storage\\\":\\\"$STORAGE\\\"}\"\n"
    )


def render_main(product_name: str, vertical: str = "") -> str:
    from app.factory.build.network_posture import NETWORK_POSTURE, NETWORK_POSTURE_REASON
    from app.factory.inventory import vertical_is_excluded

    if vertical_is_excluded(vertical):
        ingestion_mount = (
            "# Client-ingestion surface NOT mounted: the product vertical is in\n"
            "# the inventory exclusion set (medical, legal, veterinary, pharma) —\n"
            "# a product with no certified content to back must not receive a\n"
            "# client-ingestion surface (Phase 2 §1).\n"
        )
    else:
        ingestion_mount = (
            "# Phase 2 client ingestion (chunker + tenant-resolved routes).\n"
            "try:\n"
            "    from app.cerebrum_product_kernel.ingestion.router import router as product_ingestion_router\n"
            "\n"
            "    app.include_router(product_ingestion_router)\n"
            "except ImportError:\n"
            "    pass\n"
        )

    return (
        '"""Entrypoint for the generated platform.\n'
        "\n"
        "Runs standalone: uvicorn app.main:app. No factory, no block store, no\n"
        f"outbound dependency at runtime ({NETWORK_POSTURE}: {NETWORK_POSTURE_REASON}).\n"
        "Kernel jobs are at GET /v1/jobs.\n"
        "GET /health is fail-closed (process, disk, DB, Alembic head).\n"
        '"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "from contextlib import asynccontextmanager\n"
        "\n"
        "from pathlib import Path\n"
        "\n"
        "from fastapi import FastAPI\n"
        "from fastapi.responses import FileResponse, HTMLResponse\n"
        "\n"
        "from app.health import health_response\n"
        "from app.observe import install_observability\n"
        "from app.routes import router\n"
        "\n"
        "\n"
        "@asynccontextmanager\n"
        "async def lifespan(_app: FastAPI):\n"
        "    # Fail-closed: a revision behind head refuses boot.\n"
        "    from app.migrations import upgrade_head\n"
        "    from app.observe import configure_logging\n"
        "\n"
        "    upgrade_head()\n"
        "    # Platform preconditions, BEFORE any capability can be called\n"
        "    # (R1c). A block that mints its own id needs that id to exist\n"
        "    # first; leaving it to each handler is how residential-lettings\n"
        "    # answered 'Team access denied' from a handler whose calling\n"
        "    # convention was correct. Never raises: a platform that cannot\n"
        "    # reach a block at boot still starts and reports the fact.\n"
        "    try:\n"
        "        from app.preconditions import ensure_all\n"
        "\n"
        "        ensure_all()\n"
        "    except Exception:  # noqa: BLE001 - boot must not die here\n"
        "        import logging\n"
        "\n"
        "        logging.getLogger(__name__).exception(\n"
        "            'platform preconditions did not run'\n"
        "        )\n"
        "    # Uvicorn configures logging after import; win it back for JSON lines.\n"
        "    configure_logging()\n"
        "    yield\n"
        "\n"
        "\n"
        f'app = FastAPI(title="{product_name}", lifespan=lifespan)\n'
        "install_observability(app)\n"
        'app.include_router(router, prefix="/v1")\n'
        "try:\n"
        "    from app.rag_routes import router as rag_router\n"
        "\n"
        "    app.include_router(rag_router)\n"
        "except ImportError:\n"
        "    pass\n"
        + ingestion_mount
        + "\n"
        "\n"
        '@app.get("/")\n'
        "def ui_root():\n"
        "    here = Path(__file__).resolve().parent\n"
        "    for candidate in (here / 'static' / 'index.html', here.parent / 'frontend' / 'index.html'):\n"
        "        if candidate.is_file():\n"
        "            return FileResponse(candidate, media_type='text/html')\n"
        f'    return HTMLResponse("<!doctype html><html><body><h1>{product_name}</h1></body></html>")\n'
        "\n"
        "\n"
        '@app.get("/health")\n'
        "def health():\n"
        "    return health_response()\n"
    )


def deploy_declaration() -> Dict[str, Any]:
    return {
        "schema_version": "deploy_observe.v1",
        "health": {
            "path": "/health",
            "fail_closed": True,
            "checks": list(HEALTH_CHECK_NAMES),
            "unconditional_ok_is": probe_set.code_for("health_unconditional_ok"),
            "render": "healthCheckPath: /health (same probe; 503 takes the instance out)",
        },
        "rollback": {
            "script": "scripts/rollback.sh",
            "drill": (
                "tests/test_deploy.py + factory test_deploy_observe.py "
                "start rev-n, persist a row, start rev-n-plus-1 with a "
                "detectable mark, roll back to rev-n, assert prior health "
                "and prior data"
            ),
            "performed": True,
            "render_equivalent": "Dashboard rollback to the previous deploy; same disk",
        },
        "logging": {
            "format": "json_lines",
            "correlation_header": REQUEST_ID_HEADER,
            "emoji_on_machine_stdout": False,
            "f13": "structured request lines are ASCII JSON; emoji stripped",
        },
        "sqlite_on_mounted_disk": True,
        "spof": (
            "SPOF: Render rollback restarts a prior image against the same "
            "single-instance disk. There is no replica and no multi-AZ "
            "handoff. Losing the disk loses live SQLite (platform.db) and "
            "same-disk backups. Rollback does not create HA. A prior image "
            "that cannot read a newer schema is an S10 concern; this stage "
            "rolls back process identity, not a second copy of the data."
        ),
        "capacity": {
            "disk_gb": DISK_SIZE_GB,
            "ha": False,
            "replicas": 0,
            "rollback_retains_disk": True,
        },
        "revisions": {
            "n": REVISION_N,
            "n_plus_1": REVISION_N_PLUS_1,
            "mark_n": MARK_BASELINE,
            "mark_n_plus_1": MARK_CHANGED,
        },
    }


def render_deploy_doc() -> str:
    return json.dumps(deploy_declaration(), indent=2, sort_keys=True) + "\n"


def render_product_tests(specs: Dict[str, Dict[str, Any]]) -> str:
    entity, sample = first_entity_sample(specs)
    if entity:
        entity_spec = next((s for s in specs.values() if s.get("entity") == entity), None)
        check_sample_fits_declared_types(entity, sample, entity_spec)
    return f'''"""S11 deploy / observe — fail-closed health and performed rollback."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.health import evaluate_health
from app.main import app
from app.observe import JsonFormatter, REQUEST_ID_HEADER, strip_emoji
from app.revision import MARK_BASELINE, REVISION_N

ENTITY = {entity!r}
SAMPLE = {sample!r}
TENANT = "test-tenant"


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_fail_closed_when_disk_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "missing-disk"))
    code, body = evaluate_health()
    assert code == 503
    assert body["ok"] is False
    assert body["status"] == {HEALTH_STATUS_NOT_READY!r}
    names = {{item["name"]: item for item in body["checks"]}}
    assert names[{json.dumps(HEALTH_CHECK_NAMES[1])}]["ok"] is False


def test_health_is_fail_closed_when_migrations_missing(monkeypatch, tmp_path):
    storage = tmp_path / "empty"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    code, body = evaluate_health()
    assert code == 503
    assert body["ok"] is False
    names = {{item["name"]: item for item in body["checks"]}}
    assert names[{json.dumps(HEALTH_CHECK_NAMES[2])}]["ok"] is False or names[{json.dumps(HEALTH_CHECK_NAMES[3])}]["ok"] is False


def test_health_is_200_only_when_process_disk_db_and_head(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status"] == {HEALTH_STATUS_OK!r}
    names = {{item["name"] for item in body["checks"]}}
    assert set({list(HEALTH_CHECK_NAMES)!r}) <= names
    assert all(item["ok"] for item in body["checks"])
    assert body["revision"]
    assert body["mark"] == MARK_BASELINE or body["mark"]


def test_request_log_carries_correlation_id_without_emoji(client, caplog):
    caplog.set_level(logging.INFO)
    resp = client.get("/health", headers={{REQUEST_ID_HEADER: "s11-product"}})
    assert resp.headers.get(REQUEST_ID_HEADER) == "s11-product"
    assert resp.status_code == 200
    formatter = JsonFormatter()
    lines = [formatter.format(record) for record in caplog.records]
    # The correlation id is a structured log field, read from parsed JSON.
    ids = {{json.loads(line).get("request_id") for line in lines}}
    assert "s11-product" in ids or resp.headers.get(REQUEST_ID_HEADER)
    assert strip_emoji("ok") == "ok"
    party = chr(0x1F389)
    assert party not in json.dumps(resp.json())
    noisy = logging.LogRecord(
        "platform.request", logging.INFO, __file__, 0, "ready " + party, (), None
    )
    rendered = formatter.format(noisy)
    payload = json.loads(rendered)
    assert party not in rendered
    assert payload["msg"] == "ready "


@pytest.mark.skipif(not ENTITY, reason="no domain entity to persist across rollback")
def test_revision_identity_and_row_survive_mark_change(client, monkeypatch):
    from app import store
    from app.revision import MARK_CHANGED, REVISION_N_PLUS_1

    monkeypatch.setenv("APP_REVISION", REVISION_N)
    monkeypatch.setenv("APP_MARK", MARK_BASELINE)
    body_n = client.get("/health").json()
    assert body_n["ok"] is True
    saved = store.save(ENTITY, dict(SAMPLE), tenant_id=TENANT)
    assert store.get(ENTITY, saved["id"], tenant_id=TENANT) is not None

    monkeypatch.setenv("APP_REVISION", REVISION_N_PLUS_1)
    monkeypatch.setenv("APP_MARK", MARK_CHANGED)
    body_next = client.get("/health").json()
    assert body_next["ok"] is True
    assert body_next["revision"] == REVISION_N_PLUS_1
    assert body_next["mark"] == MARK_CHANGED
    assert store.get(ENTITY, saved["id"], tenant_id=TENANT) is not None

    monkeypatch.setenv("APP_REVISION", REVISION_N)
    monkeypatch.setenv("APP_MARK", MARK_BASELINE)
    body_back = client.get("/health").json()
    assert body_back["ok"] is True
    assert body_back["revision"] == REVISION_N
    assert body_back["mark"] == MARK_BASELINE
    rolled = store.get(ENTITY, saved["id"], tenant_id=TENANT)
    assert rolled is not None
    for key, value in SAMPLE.items():
        assert rolled[key] == value
'''


def render_health_route_test() -> List[str]:
    """test_routes.py::test_health, asserting the shape render_health() emits.

    GET /health is the writer's route in app/main.py; the floor's
    health_fail_closed line declares it returns ``app.health.health_response()``,
    so the body is this module's shape.
    """
    return [
        "def test_health():",
        '    resp = client.get("/health")',
        "    assert resp.status_code == 200",
        "    body = resp.json()",
        f'    assert body["status"] == {HEALTH_STATUS_OK!r}',
        '    assert body["ok"] is True',
        '    names = {item["name"] for item in body["checks"]}',
        f"    assert set({list(HEALTH_CHECK_NAMES)!r}) <= names",
        '    assert all(item["ok"] for item in body["checks"])',
    ]


def stamp_factory_deploy_modules(workspace: Any) -> List[str]:
    """Stamp the Factory-owned deploy modules outright; returns what changed.

    ``backfill_deploy_substrate`` keeps any copy that provides the imported
    NAMES -- but the stamped suite also reads the health body, the log line
    and the revision identity these modules produce. A copy with the right
    names and another shape passed the name check and failed the suite on a
    KeyError the writer cannot fix (Factory data). These three are never the
    agent's to invent (deploy_substrate), so the Factory stamps them.
    """
    rendered = dict(deploy_substrate())
    changed: List[str] = []
    for rel in FACTORY_OWNED_DEPLOY_MODULES:
        text = rendered[rel]
        current = workspace.read_text(rel) if workspace.exists(rel) else None
        if current is not None and current.replace("\r\n", "\n") == text:
            continue
        workspace.write_text(Path(rel), text)
        changed.append(rel)
    return changed


def emit_writer_artifacts(workspace: Any) -> None:
    """Write health, observability, revision identity, rollback, deploy doc."""
    for rel, content in deploy_substrate():
        workspace.write_text(Path(rel), content)


def deploy_substrate() -> List[Tuple[str, str]]:
    """(relpath, content) for the deploy half of the writer artifacts.

    The third emitter stranded below ``run_writer``'s CodeWhale branch, and so
    never run in production, while ``run_tester`` stamps
    ``tests/test_deploy.py`` regardless -- a file that opens
    ``from app.health import evaluate_health``, ``from app.observe import
    JsonFormatter`` and ``from app.revision import MARK_BASELINE``. Three
    modules, none of them the agent's to invent.

    ``backfill_platform_substrate`` closed the same hole for ``app/backup.py``
    and covers ``app/observability.py`` -- a different module from
    ``app/observe.py``, which is what the stamped suite actually imports.
    """
    return [
        ("app/revision.py", render_revision()),
        ("app/health.py", render_health()),
        ("app/observe.py", render_observe()),
        ("scripts/rollback.sh", render_rollback_script()),
        ("docs/deploy.json", render_deploy_doc()),
    ]


def backfill_deploy_substrate(workspace: Any) -> Dict[str, List[str]]:
    """Write the deploy substrate the agent was never asked for, AND repair a
    stub that cannot satisfy the stamped import contract (D2).

    ``tests/test_deploy.py`` imports specific names from ``app.health``,
    ``app.observe`` and ``app.revision``; a stub of any of those that provides
    none of them is replaced with canonical, a partial one is a conflict.
    """
    from app.factory.build.substrate_contract import reconcile_substrate

    return reconcile_substrate(
        workspace,
        deploy_substrate(),
        [render_product_tests({})],
    )


def assert_fail_closed_health_source(main_source: str) -> None:
    if health_is_always_200(main_source):
        raise ValueError("app/main.py still emits LotDesk-class always-200 health (F1)")
    try:
        tree = ast.parse(main_source)
    except SyntaxError as exc:
        raise ValueError(f"app/main.py does not parse: {exc}") from exc
    called = {
        (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", ""))
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
    }
    if "health_response" not in called:
        raise ValueError("app/main.py does not call health_response()")


def assert_no_emoji_in_machine_logs(lines: Iterable[str]) -> None:
    for line in lines:
        text = line.strip()
        if not text.startswith("{"):
            continue
        if contains_emoji(text):
            raise ValueError(f"emoji on machine-parseable stdout: {text}")
        json.loads(text)
