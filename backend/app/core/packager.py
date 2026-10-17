"""Package a CerebrumDev.ai session into a deployable Cerebrum-Blocks instance.

Two paths:
- deploy contract: deploy/contract.json + Dockerfile that clones the engine at
  build time. Was a Render blueprint until 2026-09-27; that platform is gone.
- zip: full package with vectors.json, docs, chain, Dockerfile, .env
"""

import json
import logging
import os
import zipfile
from pathlib import Path
from typing import Dict, Any

from ..models.session import SessionState
from . import client_data
from .chroma_store import load_chunks
from .llm_config import get_llm_config

logger = logging.getLogger(__name__)

STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")


def _package_dir(session_id: str) -> Path:
    path = Path(STORAGE_PATH) / "sessions" / session_id / "deploy"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_dockerfile(package_root: Path, engine_repo: str, engine_ref: str) -> None:
    dockerfile = package_root / "Dockerfile"
    dockerfile.write_text(
        f'''FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \\
    build-essential gcc g++ cmake git \\
    && rm -rf /var/lib/apt/lists/*

# Clone the Cerebrum-Blocks engine
RUN git clone --depth 1 --branch {engine_ref} {engine_repo} /tmp/engine && \\
    cp -r /tmp/engine/* /app/ && rm -rf /tmp/engine

RUN pip install --no-cache-dir -r requirements.txt

# Copy session-specific files
COPY . /app

ENV PORT=8000
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${{PORT}}
''',
        encoding="utf-8",
    )


#: Names whose value is a credential, never written into a delivered file.
_SECRET_ENV_KEYS = frozenset(
    {"SECRET_KEY", "DATA_ENCRYPTION_KEY", "CEREBRUM_MASTER_KEY"}
)


def _write_deploy_contract(
    package_root: Path,
    service_name: str,
    env_vars: Dict[str, str],
) -> None:
    """State what this package needs. Replaces the Render blueprint.

    Render is gone and that account is suspended, so a blueprint is a file
    that cannot be applied -- and it is the most deploy-shaped thing in the
    delivery, so it is what gets opened first.

    The blueprint it replaces wrote EVERY variable with its value, including
    the minted platform key, into a file handed to the customer. Secrets are
    now named and never valued, and the redaction matches on value as well as
    name so an alias of the same credential cannot slip through under a
    different key.
    """
    import json as _json

    secret_values = {
        str(v)
        for k, v in env_vars.items()
        if k in _SECRET_ENV_KEYS and str(v).strip()
    }
    redacted = {
        k
        for k, v in env_vars.items()
        if k in _SECRET_ENV_KEYS or str(v) in secret_values
    }
    contract = {
        "schema": "cerebrum.deploy.v1",
        "service": service_name,
        "port": 8000,
        "health_path": "/health",
        "environment": {k: v for k, v in env_vars.items() if k not in redacted},
        "secrets_required": sorted(redacted),
        "datastores": [],
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


def _write_probe_script(package_root: Path) -> None:
    """Write a smoke probe script for deployed instances."""
    probe = package_root / "probe.py"
    probe.write_text(
        '''"""Smoke probe for a deployed Cerebrum-Blocks instance."""
import json
import os
import sys
import urllib.request

BASE = os.getenv("PROBE_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("PROBE_API_KEY", "")


def _req(path: str) -> dict:
    req = urllib.request.Request(BASE + path)
    if API_KEY:
        req.add_header("Authorization", f"Bearer {API_KEY}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def main() -> int:
    health = _req("/health")
    print(json.dumps({"health": health}, indent=2))
    return 0 if health.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
''',
        encoding="utf-8",
    )


def _safe_name(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in value.lower())[:48]


def package_session(state: SessionState, api_key: str = None) -> Dict[str, Any]:
    """Create a deployable package for *state* and return metadata."""
    session_id = state.session_id
    domain = state.config.domain
    package_root = _package_dir(session_id) / "package"
    if package_root.exists():
        import shutil

        shutil.rmtree(package_root)
    package_root.mkdir(parents=True, exist_ok=True)

    # 1. Chain
    chain_path = package_root / "default_chain.json"
    chain_path.write_text(
        json.dumps(state.proposed_chain or {"blocks": [], "connections": []}, indent=2),
        encoding="utf-8",
    )

    # 2. Vectors
    vectors = {
        "session_id": session_id,
        "domain": domain,
        "chunks": [],
        "embeddings": [],
        "metadatas": [],
    }
    try:
        chunks, embeddings, metadatas = load_chunks(session_id)
        vectors.update({"chunks": chunks, "embeddings": embeddings, "metadatas": metadatas})
    except Exception as exc:
        logger.warning("Could not load Chroma vectors for %s: %s", session_id, exc)
        if state.chunks:
            vectors["chunks"] = state.chunks
            vectors["embeddings"] = state.embeddings or []
            vectors["metadatas"] = [{"session_id": session_id, "index": i} for i in range(len(state.chunks))]
    (package_root / "vectors.json").write_text(json.dumps(vectors, indent=2), encoding="utf-8")

    # 3. Uploaded docs
    docs_dir = package_root / "data" / "docs"
    client_data.declare(package_root, package_root / "vectors.json", docs_dir)
    session_files = Path(STORAGE_PATH) / "sessions" / session_id / "files"
    if session_files.exists():
        docs_dir.mkdir(parents=True, exist_ok=True)
        for f in session_files.iterdir():
            if f.is_file():
                import shutil

                shutil.copy2(f, docs_dir / f.name)

    # 4. Dockerfile + deploy contract + bootstrap + probe
    engine_repo = os.getenv(
        "CEREBRUM_BLOCKS_REPO", "https://github.com/bopoadz-del/Cerebrum-Blocks.git"
    )
    # The Store commit this Factory is pinned to (store.pin), never a
    # floating "main": a packaged product builds from the Store it was
    # verified against.
    from app.core.engine_discovery import _effective_ref

    engine_ref = _effective_ref()
    _write_dockerfile(package_root, engine_repo, engine_ref)
    _write_bootstrap(package_root)
    _write_probe_script(package_root)

    service_name = f"cerebrum-{_safe_name(domain)}-{_safe_name(session_id)[:16]}"
    deploy_api_key = api_key or f"cd-{os.urandom(16).hex()}"
    llm_cfg = get_llm_config()
    env_vars = {
        "ENV": "production",
        "PORT": "8000",
        "CEREBRUM_DOMAIN_KITS": domain,
        "CEREBRUM_API_KEY": deploy_api_key,
        "CB_DEV_KEY": deploy_api_key,
        # Hardened default: only the deployed service origin + local dev.
        # Buyers override CORS_ORIGINS for custom domains.
        "CORS_ORIGINS": f"https://{service_name}.onrender.com,http://localhost:5173",
        "CHROMA_PERSIST_DIR": "/app/chroma",
        "LLM_PROVIDER": llm_cfg["provider"],
        "CEREBRUM_LLM_BASE_URL": llm_cfg["base_url"],
        "CEREBRUM_LLM_MODEL": llm_cfg["model"],
    }
    # Non-secret LLM configuration is fine to ship; the owner's API key is NOT.

    _write_deploy_contract(package_root, service_name, env_vars)

    dotenv = package_root / ".env"
    dotenv_lines = [f"{key}={value}" for key, value in env_vars.items()]
    dotenv_lines.append("# CEREBRUM_LLM_API_KEY=<owner-supplied>")
    dotenv.write_text("\n".join(dotenv_lines), encoding="utf-8")
    client_data.declare(package_root, dotenv)

    readme = package_root / "README.deploy.md"
    llm_key_note = "Add your own `CEREBRUM_LLM_API_KEY` to the environment."
    readme.write_text(
        f"""# Deploy package for session {session_id}

Domain: {domain}
Service name: {service_name}

## Required owner-supplied credentials
- {llm_key_note}
- No factory credentials are bundled in this package.

## Option A: any container runtime
1. Read `deploy/contract.json` — port, health path and the secrets you must
   supply (named there, never valued).
2. Build the Dockerfile and run it, passing those values as environment.

## Option B: Manual Docker
```bash
docker build -t {service_name} .
docker run -p 8000:8000 {service_name}
```

## Bootstrap vectors
Run `python bootstrap.py` on first start (or bake it into the entrypoint).
""",
        encoding="utf-8",
    )

    # 5. Zip
    zip_path = _package_dir(session_id) / "package.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in package_root.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(package_root))

    logger.info("Packaged session %s into %s (%d bytes)", session_id, zip_path, zip_path.stat().st_size)
    return {
        "package_dir": str(package_root),
        "zip_path": str(zip_path),
        "service_name": service_name,
        "api_key": deploy_api_key,
        "env_vars": env_vars,
    }
