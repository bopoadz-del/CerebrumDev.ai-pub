"""Shared helpers for domain smoke contract tests.

A domain smoke contract proves the full factory flow without real LLM credentials:

  user need -> proposed chain -> validation -> approval -> platform package -> package inspection
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.session_store import create_session, get_session


def create_fake_engine(
    tmp_path: Path,
    monkeypatch,
    domain: str,
    block_id: str,
    block_filename: str | None = None,
) -> Path:
    """Create a minimal fake Cerebrum-Blocks engine checkout with a domain kit.

    The packager will consume this checkout to build a platform package.  The
    fixture sets ``CEREBRUM_BLOCKS_ROOT`` to the generated directory.
    """
    block_filename = block_filename or f"{block_id}.py"
    engine_root = tmp_path / "Cerebrum-Blocks"
    kit_root = engine_root / "block_store" / "kits" / domain
    bundle = kit_root / "bundle"

    # Kit artifacts
    (bundle / "app" / "blocks").mkdir(parents=True)
    (bundle / "app" / "blocks" / block_filename).write_text(
        f"class {block_id.title().replace('_', '')}Block:\n    name = '{block_id}'\n",
        encoding="utf-8",
    )
    (bundle / "app" / "containers").mkdir(parents=True)
    container_class = "".join(part.title() for part in domain.split("_")) + "Container"
    (bundle / "app" / "containers" / f"{domain}.py").write_text(
        f"class {container_class}:\n    name = '{domain}'\n",
        encoding="utf-8",
    )
    manifest = {
        "id": domain,
        "container": {"class": f"app.containers.{domain}.{container_class}"},
        "blocks": ["pdf", "ocr", "chat", "image", block_id],
        "artifacts": [
            {
                "src": f"app/containers/{domain}.py",
                "dest": f"app/containers/{domain}.py",
            },
            {
                "src": f"app/blocks/{block_filename}",
                "dest": f"app/blocks/{block_filename}",
            },
        ],
    }
    (kit_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    # Engine runtime pieces required by the packager
    cli_pkg = engine_root / "cli" / "cerebrum_cli"
    cli_pkg.mkdir(parents=True)
    (cli_pkg / "__init__.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")
    (engine_root / "app" / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (engine_root / "app" / "main.py").write_text("app\n", encoding="utf-8")
    (engine_root / "block_store" / "README.md").parent.mkdir(
        parents=True, exist_ok=True
    )
    (engine_root / "block_store" / "README.md").write_text("store\n", encoding="utf-8")

    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))
    return engine_root


async def _mock_generate_chain_suggestion(chain: Dict[str, Any], *args, **kwargs):
    return chain


def run_domain_smoke_contract(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
    domain: str,
    block_id: str,
    chain: Dict[str, Any],
    block_filename: str | None = None,
) -> None:
    """Execute the full domain smoke contract and assert package contents.

    This helper patches the LLM chain generator and block registry fetch so the
    test runs without real AI keys or a running Cerebrum-Blocks backend.
    """
    block_filename = block_filename or f"{block_id}.py"
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))

    # 1. Create a session for this domain
    session = create_session(session_id=f"sess_{domain}_smoke", user_id="test_user")
    session.config.domain = domain

    # 2. Submit a workflow prompt with patched chain generator/registry
    fake_registry = {b["id"]: {"name": b["id"]} for b in chain["chain"]["blocks"]}
    with patch(
        "app.routers.chat.generate_chain_suggestion",
        new=lambda *a, **k: _mock_generate_chain_suggestion(chain, *a, **k),
    ), patch(
        "app.routers.chat.fetch_block_registry",
        return_value=fake_registry,
    ):
        response = client.post(
            f"/v1/sessions/{session.session_id}/chat",
            json={
                "message": f"Build a {domain} analysis chain using document_engine, pdf, and chat blocks.",
                "action": "chain",
            },
        )
    assert response.status_code == 200

    # 3. Assert proposed chain was saved and contains required blocks
    state = get_session(session.session_id)
    assert state is not None
    assert state.proposed_chain is not None
    assert state.validation_passed is True

    block_ids = {b["id"] for b in state.proposed_chain["blocks"]}
    assert block_id in block_ids
    assert "chat" in block_ids
    assert block_ids & {"document_engine", "pdf", "vector_search", "ocr", "ocr_v2"}

    # 4. Chain preview endpoint returns the chain
    response = client.get(f"/v1/sessions/{session.session_id}/chain/preview")
    assert response.status_code == 200
    preview = response.json()
    assert preview["chain"] == state.proposed_chain

    # 5. Approve the chain
    response = client.post(
        f"/v1/sessions/{session.session_id}/chain/approve",
        json={"approve": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["chain_approved"] is True

    # 6. Generate platform package
    response = client.post(
        f"/v1/sessions/{session.session_id}/deploy?target=platform"
    )
    assert response.status_code == 200
    deploy_data = response.json()
    assert deploy_data["status"] == "packaged"
    assert "variant=platform" in deploy_data["download_url"]

    # 7. Download and inspect the zip
    response = client.get(
        f"/v1/sessions/{session.session_id}/deploy/package?variant=platform"
    )
    assert response.status_code == 200
    zip_bytes = response.content
    assert len(zip_bytes) > 0

    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
        names = set(zf.namelist())

        assert any(n.startswith("engine/app/") for n in names)
        assert any(n.startswith("engine/block_store/") for n in names)
        assert f"app/containers/{domain}.py" in names
        assert f"app/blocks/{block_filename}" in names
        assert "default_chain.json" in names
        assert "build_metadata.json" in names
        assert "Dockerfile" in names

        dockerfile = zf.read("Dockerfile").decode("utf-8")
        assert "COPY engine/ /app" in dockerfile
        assert "git clone" not in dockerfile.lower()
        assert "2>/dev/null" not in dockerfile
        assert "|| true" not in dockerfile

        build_metadata = json.loads(zf.read("build_metadata.json").decode("utf-8"))
        engine_meta = build_metadata["engine"]
        assert engine_meta["vendored"] is True
        assert engine_meta["vendored_path"] == "engine/"
        assert engine_meta["ref"] and engine_meta["ref"] != ""
        assert "commit_sha" in engine_meta
