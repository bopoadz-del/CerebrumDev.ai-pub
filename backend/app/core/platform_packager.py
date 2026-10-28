"""Package a CerebrumDev.ai session into a production-hardened, Fork-class platform.

Unlike the lightweight packager, this produces a multi-stage Docker build with
Postgres/pgvector support, alembic migrations, and a hardened entrypoint. The
runtime is still Cerebrum-Blocks, but the packaging follows the reference implementation's
production patterns and is fully domain-neutral.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..models.session import SessionState
from . import client_data
from .chroma_store import load_chunks, collection_exists
from .engine_discovery import EngineDiscoveryError, resolve_engine_source
from .packager import _safe_name
from .llm_config import get_llm_config

logger = logging.getLogger(__name__)

STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")

# Files/directories excluded when vendoring the engine into a platform package.
_ENGINE_COPY_EXCLUDES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    ".env",
    "*.pyc",
}

# Name-based skip list for hidden/cache artifacts.
_ENGINE_COPY_SKIP_SUFFIXES = (".pyc",)
_ENGINE_COPY_SKIP_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".venv",
    "venv",
    ".env",
}


class PlatformPackagerError(Exception):
    """Raised when a platform package cannot be generated."""


class MissingKitError(PlatformPackagerError):
    """Raised when the requested domain kit cannot be found."""


class MissingForkTemplateError(PlatformPackagerError):
    """Raised when the reference template files are not available."""


def _package_dir(session_id: str) -> Path:
    path = Path(STORAGE_PATH) / "sessions" / session_id / "deploy" / "platform"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _export_vectors(session_id: str, state: SessionState) -> Dict[str, Any]:
    """Export the session's vector index to a JSON-serializable structure."""
    chunks: List[str] = []
    embeddings: List[List[float]] = []
    metadatas: List[Dict[str, Any]] = []

    if collection_exists(session_id):
        try:
            chunks, embeddings, metadatas = load_chunks(session_id)
            logger.info("Exported %d vectors from ChromaDB for %s", len(chunks), session_id)
        except Exception as exc:
            logger.warning("Could not load Chroma vectors for %s: %s", session_id, exc)

    if not chunks and state.chunks:
        chunks = state.chunks
        embeddings = state.embeddings or []
        metadatas = [{"session_id": session_id, "index": i} for i in range(len(chunks))]

    vectors = {
        "session_id": session_id,
        "domain": state.config.domain,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "chunks": chunks,
        "embeddings": embeddings,
        "metadatas": metadatas,
    }
    if state.embedding_meta:
        vectors["embedding"] = {
            "provider": state.embedding_meta.get("provider", "zvec"),
            "model": state.embedding_meta.get("model"),
            "dim": state.embedding_meta.get("dimensions"),
        }
    elif embeddings:
        vectors["embedding"] = {
            "provider": "unknown",
            "model": "unknown",
            "dim": len(embeddings[0]),
        }
    return vectors


def _find_kit_root(domain: str, engine_root: Path) -> Path:
    """Locate the domain kit inside the resolved engine checkout."""
    kit_root = engine_root / "block_store" / "kits" / domain
    if kit_root.is_dir() and (kit_root / "manifest.json").exists():
        return kit_root
    raise MissingKitError(f"Domain kit '{domain}' not found in: {kit_root}")


def _copy_kit_artifacts(kit_root: Path, package_root: Path) -> None:
    """Copy kit bundle artifacts into the package tree."""
    manifest_path = kit_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_dir = kit_root / "bundle"

    artifacts = manifest.get("artifacts", [])
    if not artifacts and bundle_dir.exists():
        # Fallback: copy the whole bundle tree preserving structure.
        for src in bundle_dir.rglob("*"):
            if src.is_file():
                rel = src.relative_to(bundle_dir)
                dest = package_root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
        return

    for artifact in artifacts:
        src = bundle_dir / artifact["src"]
        dest = package_root / artifact["dest"]
        if not src.exists():
            logger.warning("Kit artifact missing: %s", src)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _copy_engine(engine_root: Path, package_root: Path) -> None:
    """Copy the resolved engine checkout into the package under ``engine/``.

    Excludes VCS metadata, Python caches, dependency trees, local secrets, and
    other large or machine-specific artifacts so the vendored snapshot stays
    reproducible and lean.
    """
    engine_dest = package_root / "engine"
    if engine_dest.exists():
        shutil.rmtree(engine_dest)
    engine_dest.mkdir(parents=True, exist_ok=True)

    for src in engine_root.rglob("*"):
        rel = src.relative_to(engine_root)

        # Skip top-level and nested excluded directories by name.
        if any(part in _ENGINE_COPY_SKIP_NAMES for part in rel.parts):
            continue

        # Skip dotenv files anywhere in the tree.
        if src.name.startswith(".env"):
            continue

        # Skip compiled Python artifacts.
        if src.name.endswith(_ENGINE_COPY_SKIP_SUFFIXES):
            continue

        if src.is_dir():
            (engine_dest / rel).mkdir(parents=True, exist_ok=True)
        elif src.is_file():
            dest = engine_dest / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)

    logger.info("Vendored engine from %s into %s", engine_root, engine_dest)


def _write_dockerfile(package_root: Path) -> None:
    """Write a multi-stage, production-hardened Dockerfile based on the reference implementation."""
    dockerfile = package_root / "Dockerfile"
    dockerfile.write_text(
        '''# Production-hardened Cerebrum-Blocks platform (Fork-class).
# Multi-stage build: compile deps, build frontend (if present), then runtime.
FROM python:3.11-slim AS builder
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \\
    build-essential gcc g++ gfortran pkg-config git \\
    && rm -rf /var/lib/apt/lists/*

# The Cerebrum-Blocks runtime is vendored into the package at engine/.
# No runtime clone happens at Docker build time.
COPY engine/ /app
COPY . /app

RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \\
    pip install --no-cache-dir -r requirements.txt && \\
    pip install --no-cache-dir "fastembed>=0.6.0"

# ---------------------------------------------------------------------------
# Frontend build stage (optional — only if the runtime has a frontend folder).
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend
WORKDIR /frontend
COPY --from=builder /app/frontend/package*.json ./
RUN if [ -f package.json ]; then npm ci; fi
COPY --from=builder /app/frontend ./
ENV VITE_API_BASE=""
RUN if [ -f package.json ]; then npm run build; else mkdir -p /frontend/dist; fi

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM python:3.11-slim
WORKDIR /app

# System libraries needed by PDF/OCR/image blocks and healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \\
    libgl1 libglib2.0-0 curl ffmpeg tesseract-ocr tesseract-ocr-eng \\
    libxext6 libsm6 libxrender1 libice6 libxi6 libxcomposite1 libxcursor1 \\
    libxdamage1 libxfixes3 libxrandr2 libxtst6 libnss3 \\
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Bring in the runtime plus session-specific injections.
COPY --from=builder /app /app
COPY . /app

# Replace the (gitignored) frontend/dist with the freshly built one.
COPY --from=frontend /frontend/dist /app/frontend/dist

# Create an unprivileged user. /app/data is the persistent volume mount.
RUN useradd --system --uid 1000 --shell /usr/sbin/nologin cerebrum \\
    && mkdir -p /app/data && chown -R cerebrum:cerebrum /app
USER cerebrum

ENV PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \\
    CMD curl -f "http://localhost:${PORT}/health" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
''',
        encoding="utf-8",
    )


def _write_entrypoint(package_root: Path) -> None:
    """Write the production entrypoint with alembic migrations."""
    entrypoint = package_root / "entrypoint.sh"
    entrypoint.write_text(
        '''#!/bin/bash
set -e

echo "🚀 Cerebrum Blocks platform starting on $(uname -m)"

if command -v nvidia-smi &> /dev/null; then
    export HARDWARE="gpu"
elif [ -d "/proc/device-tree" ] && grep -q "nvidia" /proc/device-tree/model 2>/dev/null; then
    export HARDWARE="jetson"
else
    export HARDWARE="cpu"
fi
echo "ℹ️  Hardware profile: ${HARDWARE}"

PORT=${PORT:-8000}
echo "📡 Listening on 0.0.0.0:${PORT}"

# Run alembic migrations when a Postgres DATABASE_URL is configured.
if [ -n "${DATABASE_URL:-}" ] && [[ "${DATABASE_URL}" == postgresql* ]]; then
    echo "🗄️  DATABASE_URL set — running alembic upgrade head"
    if ! python -m alembic upgrade head; then
        echo "❌ alembic upgrade failed"
        exit 1
    fi
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --no-access-log
''',
        encoding="utf-8",
    )


def _write_docker_compose(package_root: Path) -> None:
    """Write a docker-compose stack with Postgres/pgvector."""
    compose = package_root / "docker-compose.yml"
    compose.write_text(
        '''version: "3.8"

services:
  postgres:
    image: pgvector/pgvector:pg16
    container_name: cerebrum-platform-postgres
    environment:
      POSTGRES_USER: cerebrum
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: cerebrum
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U cerebrum -d cerebrum"]
      interval: 5s
      timeout: 5s
      retries: 5

  cerebrum:
    build: .
    container_name: cerebrum-platform
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./.env:/app/.env:ro
    environment:
      - PORT=8000
      - HOST=0.0.0.0
      - DATA_DIR=/app/data
      - DATABASE_URL=postgresql+psycopg://cerebrum:${POSTGRES_PASSWORD}@postgres:5432/cerebrum
      - PYTHONUNBUFFERED=1
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s

volumes:
  postgres_data:
''',
        encoding="utf-8",
    )


def _write_deploy_contract(
    package_root: Path,
    service_name: str,
    env_vars: Dict[str, str],
) -> None:
    """State what this platform needs; do not provision it on a vendor.

    Replaces the Render blueprint. Render is gone and that account is
    suspended, and a blueprint is the most deploy-shaped file in a delivery --
    the one a customer opens first.

    This package is NOT the build path's shape: its entrypoint runs
    ``alembic upgrade head`` when DATABASE_URL is a postgres URL and its
    docker-compose ships a Postgres service, so the database is real and is
    declared as required. The build path's platform is sqlite on a volume and
    declares no datastore; both are honest about what they actually open.

    Secrets are NAMED and never valued, exactly as the blueprint's
    ``sync: false`` did. A minted key written into a json file inside the
    package would be a regression the file this replaces did not have.
    """
    import json as _json

    secret_keys = {"SECRET_KEY", "DATA_ENCRYPTION_KEY", "CEREBRUM_MASTER_KEY"}
    # Redact by VALUE as well as by name. The minted deploy key is written to
    # three variables -- CEREBRUM_MASTER_KEY, CB_DEV_KEY and
    # CEREBRUM_API_KEY_PLATFORM (see test_platform_auth_single_source_of_truth)
    # -- and the name list covered only the first, so render.yaml shipped the
    # platform's live API key in plaintext under the other two. Matching on the
    # value catches every alias, including ones added later.
    secret_values = {
        str(v) for k, v in env_vars.items() if k in secret_keys and str(v).strip()
    }
    redacted = {
        k for k, v in env_vars.items() if k in secret_keys or str(v) in secret_values
    }
    environment = {k: v for k, v in env_vars.items() if k not in redacted}
    contract = {
        "schema": "cerebrum.deploy.v1",
        "service": service_name,
        "port": 8000,
        "health_path": "/health",
        "persistent_volume": {
            "required": True,
            "mount_path": "/app/data",
            "size_gb": 1,
            "reason": (
                "session state and uploaded artifacts live here. On a runtime "
                "with ephemeral storage every deploy discards them silently."
            ),
        },
        "environment": environment,
        "secrets_required": sorted(redacted),
        "datastores": [
            {
                "kind": "postgres",
                "required": True,
                "env": "DATABASE_URL",
                "reason": (
                    "entrypoint.sh runs alembic upgrade head when DATABASE_URL "
                    "is a postgresql URL; docker-compose.yml provisions one for "
                    "local use. Supply a managed instance in production."
                ),
            }
        ],
    }
    deploy = package_root / "deploy"
    deploy.mkdir(parents=True, exist_ok=True)
    (deploy / "contract.json").write_text(
        _json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_bootstrap(package_root: Path) -> None:
    """Write a bootstrap script that restores the vector index."""
    bootstrap = package_root / "bootstrap.py"
    bootstrap.write_text(
        '''"""Restore the session vector index into ChromaDB on first boot."""
import json
import os
from pathlib import Path

CHROMA_PERSIST_DIR = os.getenv("CHROMA_PERSIST_DIR", "/app/chroma")
vectors_path = Path(__file__).parent / "vectors.json"
if not vectors_path.exists():
    print("No vectors.json found; skipping bootstrap.")
    raise SystemExit(0)

with open(vectors_path, encoding="utf-8") as f:
    data = json.load(f)

chunks = data.get("chunks", [])
embeddings = data.get("embeddings", [])
metadatas = data.get("metadatas", []) or [{"index": i} for i in range(len(chunks))]

if not chunks:
    print("No chunks to index.")
    raise SystemExit(0)

os.makedirs(CHROMA_PERSIST_DIR, exist_ok=True)
try:
    import chromadb
    client = chromadb.PersistentClient(path=CHROMA_PERSIST_DIR)
    collection = client.get_or_create_collection(
        name="session_default",
        metadata={"domain": data.get("domain", "general"), "source": "platform_bootstrap"},
    )
    ids = [f"chunk_{i}" for i in range(len(chunks))]
    collection.upsert(
        ids=ids,
        documents=chunks,
        embeddings=embeddings if embeddings and len(embeddings) == len(chunks) else None,
        metadatas=metadatas,
    )
    print(f"Bootstrapped {len(chunks)} chunks into ChromaDB at {CHROMA_PERSIST_DIR}")
except Exception as exc:
    print(f"Bootstrap failed (non-fatal): {exc}")
''',
        encoding="utf-8",
    )


def _write_readme(package_root: Path, service_name: str) -> None:
    readme = package_root / "README.platform.md"
    readme.write_text(
        f'''# {service_name}

Production-hardened Cerebrum Blocks platform generated by CerebrumDev.ai.

## Local development

```bash
docker compose up --build
```

The first startup runs `alembic upgrade head` automatically when `DATABASE_URL`
is set.

## Deploy

`deploy/contract.json` states what this platform needs: port, health path,
the persistent volume, and the Postgres it migrates against. Read it first —
every requirement below comes from there.

1. Push this folder to a Git repository.
2. Build the Dockerfile and run it on any container runtime. `docker compose
   up` works as-is and brings its own Postgres for local use; in production
   supply a managed instance via `DATABASE_URL`.
3. Supply the secret keys out of band (they are named in the contract's
   `secrets_required` and deliberately carry no values):
   - `SECRET_KEY`
   - `DATA_ENCRYPTION_KEY`
   - `CEREBRUM_MASTER_KEY`
4. Apply the blueprint.

## Domain kit

The selected domain kit is installed at build time via `CEREBRUM_DOMAIN_KITS`.
Session documents are in `data/docs/` and the approved chain is in
`default_chain.json`.
''',
        encoding="utf-8",
    )


def _drop_cli_artifacts(
    package_root: Path,
    service_name: str,
    env_vars: Dict[str, str],
    engine_root: Path,
) -> None:
    """Drop a cerebrum CLI folder into the platform package."""
    cli_dir = package_root / "cli"
    cli_dir.mkdir(parents=True, exist_ok=True)

    install_sh = cli_dir / "install.sh"
    install_sh.write_text(
        "#!/bin/sh\n"
        "# Install the cerebrum CLI bundled with this instance.\n"
        "set -e\n"
        "SCRIPT_DIR=\"$(cd \"$(dirname \"$0\")\" && pwd)\"\n"
        "pip install \"$SCRIPT_DIR/cerebrum_cli\"\n"
        "echo \"cerebrum CLI installed. Run: cerebrum --help\"\n",
        encoding="utf-8",
    )
    install_sh.chmod(0o755)

    config_toml = cli_dir / "config.toml"
    base_url = f"https://{service_name}.onrender.com"
    config_toml.write_text(
        f"# Cerebrum CLI configuration for this deployed instance.\n"
        f'mode = "deployed"\n'
        f'base_url = "{base_url}"\n'
        f'api_key = "{env_vars.get("CEREBRUM_MASTER_KEY", "")}"\n'
        f'domain = "{env_vars.get("CEREBRUM_DOMAIN_KITS", "construction")}"\n'
        f'instance_name = "{service_name}"\n'
        f'session_id = ""\n',
        encoding="utf-8",
    )

    engine_cli_dir = engine_root / "cli"
    if not engine_cli_dir.is_dir():
        raise RuntimeError(
            f"Engine checkout at {engine_root} lacks cli/ — requires Cerebrum-Blocks "
            "with the relocated CLI (Spec 2, commit c8176867 or later)"
        )
    shutil.copytree(engine_cli_dir, cli_dir, dirs_exist_ok=True)
    logger.info("Copied CLI from engine checkout %s into platform package", engine_cli_dir)


def package_platform_session(state: SessionState, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Create a Fork-class platform package for *state* and return metadata."""
    session_id = state.session_id
    domain = state.config.domain
    package_root = _package_dir(session_id) / "platform"
    if package_root.exists():
        shutil.rmtree(package_root)
    package_root.mkdir(parents=True, exist_ok=True)

    # Resolve the engine checkout up front so the fetch path fails loud and early.
    try:
        engine_root, engine_metadata = resolve_engine_source()
    except EngineDiscoveryError as exc:
        raise PlatformPackagerError(f"Engine resolution failed: {exc}") from exc

    # 1. Domain kit
    kit_root = _find_kit_root(domain, engine_root)
    _copy_kit_artifacts(kit_root, package_root)

    # 2. Session assets
    chain_path = package_root / "default_chain.json"
    chain_path.write_text(
        json.dumps(state.proposed_chain or {"blocks": [], "connections": []}, indent=2),
        encoding="utf-8",
    )

    vectors = _export_vectors(session_id, state)
    (package_root / "vectors.json").write_text(
        json.dumps(vectors, indent=2),
        encoding="utf-8",
    )

    docs_dir = package_root / "data" / "docs"
    client_data.declare(package_root, package_root / "vectors.json", docs_dir)
    session_files = Path(STORAGE_PATH) / "sessions" / session_id / "files"
    if session_files.exists():
        docs_dir.mkdir(parents=True, exist_ok=True)
        for f in session_files.iterdir():
            if f.is_file():
                shutil.copy2(f, docs_dir / f.name)

    # 3. Vendor the engine snapshot and write runtime / deployment files
    _copy_engine(engine_root, package_root)
    _write_dockerfile(package_root)
    _write_entrypoint(package_root)
    _write_docker_compose(package_root)
    _write_bootstrap(package_root)

    # 4. Env + Render blueprint
    service_name = f"cerebrum-platform-{_safe_name(domain)}-{_safe_name(session_id)[:16]}"
    deploy_api_key = api_key or f"cd-platform-{os.urandom(16).hex()}"
    # The bundled Postgres password, generated per package (never a fixed value
    # in the code): written to .env, read by docker-compose and DATABASE_URL.
    db_password = os.urandom(16).hex()
    llm_cfg = get_llm_config()
    env_vars: Dict[str, str] = {
        "ENV": "production",
        "PORT": "8000",
        "HOST": "0.0.0.0",
        "DATA_DIR": "/app/data",
        "POSTGRES_PASSWORD": db_password,
        "DATABASE_URL": f"postgresql+psycopg://cerebrum:{db_password}@postgres:5432/cerebrum",
        "CEREBRUM_DOMAIN_KITS": domain,
        # Single source of truth for platform authentication. The engine's
        # APIKeyAuth reads CEREBRUM_MASTER_KEY; CB_DEV_KEY covers legacy
        # container paths that still consult it in non-production modes.
        "CEREBRUM_MASTER_KEY": deploy_api_key,
        "CB_DEV_KEY": deploy_api_key,
        "CEREBRUM_API_KEY_PLATFORM": deploy_api_key,
        # Hardened default: only the deployed service origin + local dev.
        # Buyers override CORS_ORIGINS for custom domains.
        "CORS_ORIGINS": f"https://{service_name}.onrender.com,http://localhost:5173",
        "CHROMA_PERSIST_DIR": "/app/chroma",
        "SECRET_KEY": deploy_api_key,
        "DATA_ENCRYPTION_KEY": deploy_api_key,
        "LLM_PROVIDER": llm_cfg["provider"],
        "CEREBRUM_LLM_BASE_URL": llm_cfg["base_url"],
        "CEREBRUM_LLM_MODEL": llm_cfg["model"],
    }
    # Non-secret LLM configuration is fine to ship; owner API keys are NOT.

    dotenv_lines = [f'{key}={value}' for key, value in env_vars.items()]
    dotenv_lines.append("# CEREBRUM_LLM_API_KEY=<owner-supplied>")

    dotenv = package_root / ".env"
    dotenv.write_text("\n".join(dotenv_lines), encoding="utf-8")
    client_data.declare(package_root, dotenv)
    _write_deploy_contract(package_root, service_name, env_vars)
    _write_readme(package_root, service_name)
    _drop_cli_artifacts(package_root, service_name, env_vars, engine_root)

    # 5. Build metadata
    engine_meta = dict(engine_metadata)
    engine_meta.update(
        {
            "vendored": True,
            "vendored_path": "engine/",
        }
    )
    build_metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "cerebrumdev.platform_packager",
        "session_id": session_id,
        "domain": domain,
        "service_name": service_name,
        "engine": engine_meta,
    }
    (package_root / "build_metadata.json").write_text(
        json.dumps(build_metadata, indent=2),
        encoding="utf-8",
    )

    # 6. Zip package
    zip_path = _package_dir(session_id) / "platform.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in package_root.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(package_root))

    logger.info(
        "Packaged platform session %s into %s (%d bytes)",
        session_id,
        zip_path,
        zip_path.stat().st_size,
    )
    return {
        "package_dir": str(package_root),
        "zip_path": str(zip_path),
        "service_name": service_name,
        "api_key": deploy_api_key,
        "env_vars": env_vars,
        "build_metadata": build_metadata,
    }
