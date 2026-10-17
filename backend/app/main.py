import logging
import os
import sys
import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv


def _configure_logging() -> None:
    """Send this application's logs to stdout.

    Nothing configured logging, so uvicorn's own loggers printed access lines
    while every ``logging.getLogger("cerebrumdev...")`` call went nowhere --
    the root logger had no handler and defaulted to WARNING. A twenty-minute
    background build was therefore completely unobservable in production: no
    coder calls, no phase transitions, no failures, only access logs. Level
    is LOG_LEVEL (default INFO); uvicorn keeps its own handlers.
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    if not any(getattr(h, "_cerebrum_stdout", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter("%(levelname)s %(name)s: %(message)s")
        )
        handler._cerebrum_stdout = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(getattr(logging, level, logging.INFO))


_configure_logging()

# Load .env before any module reads environment variables
load_dotenv()

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

from .core.request_limits import BodySizeLimitMiddleware
from .core import backup_scheduler
from .core.auth import require_api_key, require_master_key, verify_production_auth
from .core.cors_policy import cors_allow_origins
from .core.llm_config import _truthy, env_key_present, llm_ready_details
from .core.metrics import HttpMetricsMiddleware, metrics_response
from .core.billing import require_entitled
from .routers import (
    accounts,
    accounts_restore,
    admin_ops,
    billing,
    sessions,
    config,
    domains,
    upload,
    chat,
    deploy,
    factory_drive,
    session_product,
    delivery_standard,
    registry_reuse,
    resend_verification,
)
from .resident_engineer.router import router as resident_engineer_router
from .change_requests.router import router as change_requests_router
from .workbench.router import router as workbench_router
from .workbench.flags import build_mode_enabled
from .resident_engineer.flags import resident_engineer_enabled

verify_production_auth()

_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off"}
_OPENAPI_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}


def openapi_docs_enabled() -> bool:
    """Swagger/ReDoc/OpenAPI are off in production unless explicitly re-enabled.

    Live currently serves ``/docs``, ``/redoc`` and a 100-path ``/openapi.json``
    to the public internet — including ``/v1/auth/smoke-login`` and admin
    retention. That is a map of the attack surface, not a product feature.
    ``OPENAPI_DOCS_ENABLED=1`` is the deliberate local/debug override.
    """
    explicit = os.getenv("OPENAPI_DOCS_ENABLED", "").strip().lower()
    if explicit in _TRUTHY:
        return True
    if explicit in _FALSY:
        return False
    env = os.getenv("ENV", "development").strip().lower()
    return env not in {"production", "prod"}


def llm_key_configured() -> bool:
    """True only when a real LLM credential is present.

    ``LLM_PROVIDER`` is a routing choice, not a key. render.yaml pins
    ``LLM_PROVIDER=cursor``, so treating it as configured made ``/ready``
    report ``llm_configured: true`` on a keyless box. ``KIMI_MOCK`` is a
    mock and is reported separately as ``llm_mock``.

    Cursor-family keys count whenever present (Factory coding / BA).
    ``CEREBRUM_CHAT_LLM_API_KEY`` and ``OPENROUTER_API_KEY`` count for
    HTTP Floor chat. Cursor keys are not chat-completions credentials.

    Per-key booleans live on ``/ready`` ``details.llm`` (see
    ``llm_ready_details``) so a dashboard-filled secret can be proven
    present or absent in this process without leaking the value.
    """
    return any(
        env_key_present(name)
        for name in (
            "KIMI_API_KEY",
            "CEREBRUM_LLM_API_KEY",
            "CEREBRUM_CHAT_LLM_API_KEY",
            "CEREBRUM_FACTORY_LLM_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENROUTER_API_KEY",
            "CURSOR_API_KEY",
            "CURSOR_AGENT_API_KEY",
            "FACTORY_CURSOR_API_KEY",
        )
    )


def _is_openapi_path(path: str) -> bool:
    return path in _OPENAPI_PATHS or path.startswith("/docs/")


#: CSP for the API origin. The frontend has carried one in
#: ``frontend/public/_headers`` and ``render.yaml`` for some time; this origin
#: never has. Measured against live api.cerebrum-dev.com on 2026-09-10: HSTS,
#: X-Frame-Options, Referrer-Policy and nosniff were present, CSP was not.
#:
#: ``default-src 'none'`` is the honest floor for an API: it serves JSON and
#: needs no scripts, styles, images or fonts of its own. ``frame-ancestors``
#: is the half a browser enforces regardless of X-Frame-Options, and the half
#: that matters if this origin ever returns HTML.
#:
#: Enforcing rather than report-only, deliberately: unlike an SPA there is no
#: set of sources here to discover first. The docs page is the one exception
#: and gets its own policy below rather than weakening this one for every
#: route.
_API_CSP = (
    "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)

#: Swagger UI loads its bundle and stylesheet from a CDN, so the docs page
#: needs its own policy. It is served only when ``openapi_docs_enabled()`` --
#: never in a production-like ENV, where those paths 404 above.
_DOCS_CSP = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "frame-ancestors 'none'; base-uri 'self'"
)


def _apply_security_headers(response: Response, path: str = "") -> None:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy",
        _DOCS_CSP if _is_openapi_path(path) else _API_CSP,
    )
    env = os.getenv("ENV", "").strip().lower()
    if env in {"production", "prod"}:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )


class ProductionHttpSurfaceMiddleware(BaseHTTPMiddleware):
    """Close OpenAPI in production-like ENV and stamp baseline security headers."""

    async def dispatch(self, request: StarletteRequest, call_next):
        if not openapi_docs_enabled() and _is_openapi_path(request.url.path):
            response = JSONResponse({"detail": "Not Found"}, status_code=404)
        else:
            response = await call_next(request)
        _apply_security_headers(response, request.url.path)
        return response


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Arm in-process nightly backup (+ stale factory_outputs sweep)."""
    # Nightly backups run in-process: Render cron jobs cannot mount persistent
    # disks and a disk belongs to exactly one service, so this process is the
    # only one that can read /app/storage at all. See core/backup_scheduler.py.
    app.state.backup_task = backup_scheduler.start()
    # uvicorn has installed its exit handlers by now (capture_signals wraps
    # the whole serve); while a coding agent runs, an exit signal is refused
    # (agent_process: the 2026-10-09 restart).
    try:
        from app.factory.build.agent_process import guard_exit_signals

        guard_exit_signals()
    except Exception:  # noqa: BLE001 -- boot must not die on the guard
        logging.getLogger("cerebrumdev.factory.agent_process").exception("exit-signal guard not installed")
    try:
        from app.factory.build.orphan_recovery import recover_orphaned_model_calls

        recover_orphaned_model_calls()
    except Exception:  # noqa: BLE001 — boot must not die on orphan scan
        logging.getLogger("cerebrumdev.factory.orphan_recovery").exception(
            "orphaned FACTORY_CODE_CLI recovery failed"
        )
    try:
        from app.factory.build.orphan_recovery import recover_stranded_n3_handoffs

        recover_stranded_n3_handoffs()
    except Exception:  # noqa: BLE001 — boot must not die on the handoff scan
        logging.getLogger("cerebrumdev.factory.orphan_recovery").exception(
            "stranded N3 handoff recovery failed"
        )
    try:
        yield
    finally:
        task = getattr(app.state, "backup_task", None)
        if task is not None:
            task.cancel()


_docs = openapi_docs_enabled()
app = FastAPI(
    title="CerebrumDev.ai API",
    version="0.1.0",
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
    lifespan=_lifespan,
)

_cors_origins = cors_allow_origins()

# Body-size ceiling. Added BEFORE the CORS block on purpose: Starlette runs the
# last-added middleware outermost, so registering this first leaves CORS on the
# outside and the 413 comes back with the CORS headers attached. Reversed, the
# browser reports an opaque CORS failure instead of "file too large".
app.add_middleware(BodySizeLimitMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Outermost: 404s /docs in production (per-request, so tests can flip ENV)
# and stamps security headers on every response, including CORS 403s.
app.add_middleware(ProductionHttpSurfaceMiddleware)
# Outermost of all: HTTP request count/latency/in-flight for Prometheus scrapes.
app.add_middleware(HttpMetricsMiddleware)

# Public account endpoints (register/login/verify carry no credential; me/keys
# enforce their own account principal).
app.include_router(accounts.router, prefix="/v1/auth", tags=["auth"])
app.include_router(resend_verification.router, prefix="/v1/auth", tags=["auth"])
# Master-key one-shot SQLite → Postgres accounts restore. Not user-callable.
app.include_router(accounts_restore.router)
# Admin ops (master-key only): switch the coder model, reboot the service.
app.include_router(admin_ops.router, prefix="/v1/admin", tags=["admin"])
# Billing status self-enforces an account credential; checkout/webhook join here.
app.include_router(billing.router, prefix="/v1/billing", tags=["billing"])
app.include_router(sessions.router, prefix="/v1/sessions", tags=["sessions"], dependencies=[Depends(require_api_key)])
app.include_router(config.router, prefix="/v1/sessions", tags=["config"], dependencies=[Depends(require_api_key)])
app.include_router(domains.router, prefix="/v1/domains", tags=["domains"], dependencies=[Depends(require_api_key)])
app.include_router(upload.router, prefix="/v1/sessions", tags=["upload"], dependencies=[Depends(require_api_key)])
app.include_router(chat.router, prefix="/v1/sessions", tags=["chat"], dependencies=[Depends(require_api_key)])
app.include_router(deploy.router, prefix="/v1/sessions", tags=["deploy"], dependencies=[Depends(require_api_key)])
app.include_router(factory_drive.router, prefix="/v1/sessions", tags=["factory-drive"], dependencies=[Depends(require_api_key)])
app.include_router(factory_drive.callback_router)
# The live product surface is Floor session chat + ``/v1/sessions/{id}/product/*``.
# Unused ``/v1/factory/product/*`` was a second HTTP door (frontend, Playwright,
# and CI never called it) and is not mounted.
# The coder's canonical brief: immutable standard + per-product domain pack.
app.include_router(
    delivery_standard.router,
    prefix="/v1/factory/delivery-standard",
    tags=["delivery-standard"],
    dependencies=[Depends(require_entitled)],
)
# Blocks #106 contract stub — always 200, exact-id REUSE. Live Blocks
# (CEREBRUM_API_URL) wins when that surface exists; this is the in-repo
# feature-detect target so C-BRIEF does not wait on the Blocks merge.
app.include_router(
    registry_reuse.router,
    prefix="/v1/registry",
    tags=["registry"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    session_product.router,
    prefix="/v1/sessions",
    tags=["session-product"],
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    resident_engineer_router,
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    change_requests_router,
    dependencies=[Depends(require_api_key)],
)
app.include_router(
    workbench_router,
    dependencies=[Depends(require_api_key)],
)


def _probe_sentry() -> dict:
    """Honest config probe — never claims live ingest without a DSN."""
    return {"configured": bool(os.getenv("SENTRY_DSN", "").strip())}


def _git_sha() -> str:
    for key in ("RENDER_GIT_COMMIT", "GIT_COMMIT", "SOURCE_VERSION"):
        val = os.getenv(key, "").strip()
        if val:
            return val
    return ""


def _backup_details() -> dict:
    """Surface the last backup honestly. Never invent ok: true."""
    from .core import backup as backup_mod

    last = backup_scheduler.last_status_for_ready()
    pg_url = bool(os.getenv("ACCOUNTS_DATABASE_URL", "").strip())
    dump = backup_mod.pg_dump_probe()
    probe = {
        "pg_dump_needed": pg_url,
        "pg_dump_available": bool(dump.get("available")),
        "pg_dump_version": dump.get("version"),
    }
    live_host = backup_mod.accounts_host_fingerprint()
    if last is None:
        return {
            "ok": False,
            "at": None,
            "error": "no backup recorded",
            "accounts_host": None,
            "live_accounts_host": backup_mod.public_accounts_host_label(live_host),
            "matches_live_engine": False if live_host else None,
            **probe,
        }
    recorded_host = last.get("accounts_host")
    details = {
        "ok": bool(last.get("ok")),
        "at": last.get("at"),
        "error": last.get("error"),
        "engine": last.get("engine"),
        "dump_method": last.get("dump_method"),
        "accounts_host": backup_mod.public_accounts_host_label(recorded_host),
        "live_accounts_host": backup_mod.public_accounts_host_label(live_host),
        "matches_live_engine": (
            recorded_host == live_host if live_host else None
        ),
        **probe,
    }
    if last.get("reconciled_from"):
        details["reconciled_from"] = last.get("reconciled_from")
        details["prior_error"] = last.get("prior_error")
        details["archive"] = last.get("archive")
    return details


def _probe_redis() -> dict:
    """Optional Redis/Key Value probe — unset REDIS_URL is fine."""
    url = os.getenv("REDIS_URL", "").strip()
    if not url:
        return {"configured": False}
    try:
        import redis  # type: ignore

        client = redis.from_url(url, socket_connect_timeout=1)
        client.ping()
        return {"configured": True, "ok": True}
    except ImportError:
        return {"configured": True, "ok": False, "error": "redis package not installed"}
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "ok": False, "error": str(exc)}


def _probe_drive_encryption() -> dict:
    """Drive tokens must be encrypted at rest in production."""
    from .core.file_crypto import encryption_enabled
    from .core.google_drive_connector import configured as drive_configured

    drive_on = drive_configured()
    enc = encryption_enabled()
    return {
        "drive_configured": drive_on,
        "encryption_enabled": enc,
        "ok": (not drive_on) or enc,
    }


def _env_is_production() -> bool:
    return os.getenv("ENV", "").strip().lower() in {"production", "prod"}


def _probe_storage() -> dict:
    storage = os.getenv("STORAGE_PATH", "./storage")
    try:
        os.makedirs(storage, exist_ok=True)
        probe = os.path.join(storage, ".healthcheck")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return {"ok": True, "path": storage}
    except OSError as exc:
        return {"ok": False, "path": storage, "error": str(exc)}


def _probe_blocks() -> dict:
    url = os.getenv("CEREBRUM_API_URL")
    if not url:
        return {"ok": False, "error": "CEREBRUM_API_URL unset"}
    try:
        import urllib.request

        req = urllib.request.Request(f"{url.rstrip('/')}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return {"ok": 200 <= resp.status < 300, "status": resp.status}
    except Exception as exc:  # noqa: BLE001 - health probes must not raise
        return {"ok": False, "error": str(exc)}


@app.get("/health")
async def health():
    storage = _probe_storage()
    redis = _probe_redis()
    from app.core.mailer import frontend_url_is_public

    return {
        "status": "ok" if storage.get("ok") else "degraded",
        "storage": storage,
        "redis": redis,
        "resident_engineer_enabled": resident_engineer_enabled(),
        "build_mode_enabled": build_mode_enabled(),
        # Evaluated capability, not configuration: enabled only when the
        # flag is on AND the CLI actually answers.
        "sentry": _probe_sentry(),
        # Boolean only — never echo FRONTEND_URL. False means reset/verify
        # mail still falls back to localhost.
        "frontend_url_public": frontend_url_is_public(),
    }


@app.get("/ready")
async def ready():
    storage = _probe_storage()
    blocks = _probe_blocks()
    # KIMI_MOCK is a mock, not a configured LLM — reported separately.
    # LLM_PROVIDER is a routing choice, not evidence a key exists.
    llm_configured = llm_key_configured()
    checks = {
        "storage": bool(storage.get("ok")),
        "cerebrum_blocks": bool(blocks.get("ok")),
        "llm_configured": llm_configured,
        # Same helper as llm_config: only 1/true/yes/on. bool(os.getenv)
        # treated "0" and "false" as mock.
        "llm_mock": _truthy("KIMI_MOCK"),
        "api_key_configured": bool(os.getenv("CEREBRUM_DEV_API_KEY"))
        or os.getenv("ENV", "development") != "production",
    }
    # Storage + API key are hard requirements; blocks/LLM may be degraded in local dev.
    ready_ok = checks["storage"] and checks["api_key_configured"]
    # Informational, not gating: a failed backup must page someone, not take
    # the API out of rotation.
    last_backup = _backup_details()
    encryption = _probe_drive_encryption()
    checks["data_encryption"] = bool(encryption.get("ok"))
    # Production + Drive configured + no DATA_ENCRYPTION_KEY is not ready:
    # OAuth refresh tokens would sit plaintext on the Render disk.
    if _env_is_production() and not encryption.get("ok"):
        ready_ok = False
    body = {
        "status": "ready" if ready_ok else "not_ready",
        "checks": checks,
        "details": {
            "storage": storage,
            "cerebrum_blocks": blocks,
            "last_backup": last_backup,
            "sentry": _probe_sentry(),
            "data_encryption": encryption,
            # Booleans + hostname + error only. Never echo key values.
            "llm": llm_ready_details(),
        },
    }
    # Answer with a status code the platform can act on. This endpoint is the
    # Render health check: returning 200 while reporting "not_ready" means an
    # unwritable disk reads as healthy and no restart or alert ever happens.
    return JSONResponse(status_code=200 if ready_ok else 503, content=body)


@app.head("/ready")
async def ready_head():
    """Render and uptime probes sometimes HEAD the health path."""
    resp = await ready()
    return Response(status_code=resp.status_code)


@app.get("/metrics")
async def metrics(_admin=Depends(require_master_key)):
    """Prometheus scrape. Master key only — same gate as ``POST /v1/ops/backup``."""
    return metrics_response()


@app.get("/version")
async def version():
    """Fail-closed ops pin: git SHA when Render (or the image) provides it.

    Missing SHA is reported as null rather than invented. Production smoke
    treats an empty SHA as DEAD.
    """
    sha = _git_sha()
    return {
        "service": "cerebrumdev-factory",
        "git_sha": sha or None,
        "git_sha_short": sha[:7] if sha else None,
        "env": os.getenv("ENV"),
        "sentry_configured": _probe_sentry()["configured"],
    }


@app.post("/v1/ops/backup")
async def trigger_backup(_admin=Depends(require_master_key)):
    """Run one backup now and overwrite last_backup.json.

    Nightly 03:00 UTC still runs in-process. This exists so a stale pg_dump
    fail does not sit on /ready until tomorrow after a dump-path fix ships.
    Master API key only — not a user login token.
    """
    report = await asyncio.to_thread(backup_scheduler.run_backup_once)
    return report
