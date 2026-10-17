"""Tests for the Fork-class platform packager."""

from __future__ import annotations

from typing import Dict

import json
import os
import re
import shutil
import zipfile
from pathlib import Path

import pytest

from app.core.platform_packager import (
    MissingKitError,
    package_platform_session,
)
from app.models.session import SessionState

# Fake credentials for this test, generated per run (no value is stored in the repo).
_GENERATED_KEY_1 = "sk-" + __import__("secrets").token_hex(15)


@pytest.fixture
def session(tmp_path: Path) -> SessionState:
    # Point storage away from the project tree.
    os.environ["STORAGE_PATH"] = str(tmp_path / "storage")
    return SessionState(
        session_id="sess_test_platform",
        user_id="user_1",
        config={"domain": "medical"},
        proposed_chain={"blocks": [{"id": "chat"}], "connections": []},
        chain_approved=True,
    )


@pytest.fixture
def fake_medical_kit(tmp_path: Path) -> Path:
    """Create a fake domain kit with a manifest and one artifact."""
    engine_root = tmp_path / "Cerebrum-Blocks"
    kit_root = engine_root / "block_store" / "kits" / "medical"
    bundle = kit_root / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "app" / "containers").mkdir(parents=True)
    (bundle / "app" / "containers" / "medical.py").write_text(
        "class MedicalContainer:\n    name = 'medical'\n",
        encoding="utf-8",
    )
    manifest = {
        "id": "medical",
        "container": {"class": "app.containers.medical.MedicalContainer"},
        "blocks": ["chat"],
        "artifacts": [
            {"src": "app/containers/medical.py", "dest": "app/containers/medical.py"}
        ],
    }
    (kit_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    # Engine checkout must also contain the relocated CLI and top-level runtime dirs.
    cli_pkg = engine_root / "cli" / "cerebrum_cli"
    cli_pkg.mkdir(parents=True)
    (cli_pkg / "__init__.py").write_text("__version__ = '0.0.0'\n", encoding="utf-8")
    (cli_pkg / "marker_from_engine.txt").write_text("engine\n", encoding="utf-8")
    (engine_root / "app" / "main.py").parent.mkdir(parents=True, exist_ok=True)
    (engine_root / "app" / "main.py").write_text("app\n", encoding="utf-8")
    (engine_root / "block_store" / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (engine_root / "block_store" / "README.md").write_text("store\n", encoding="utf-8")
    # Excluded artifacts should not be copied into the vendored engine/ folder.
    (engine_root / ".git" / "config").parent.mkdir(parents=True, exist_ok=True)
    (engine_root / ".git" / "config").write_text("git\n", encoding="utf-8")
    (engine_root / "__pycache__" / "cache.pyc").parent.mkdir(parents=True, exist_ok=True)
    (engine_root / "__pycache__" / "cache.pyc").write_text("pyc\n", encoding="utf-8")
    (engine_root / ".env").write_text("SECRET=xyz\n", encoding="utf-8")
    return kit_root


def test_package_platform_session_neutral(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """A platform package is generated and contains Fork-class files."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_medical_kit.parent.parent.parent))

    info = package_platform_session(session)

    assert info["service_name"].startswith("cerebrum-platform-medical-")
    assert Path(info["zip_path"]).exists()

    with zipfile.ZipFile(info["zip_path"], "r") as zf:
        names = zf.namelist()
        assert "Dockerfile" in names
        assert "docker-compose.yml" in names
        assert "deploy/contract.json" in names
        assert "entrypoint.sh" in names
        assert ".env" in names
        assert "default_chain.json" in names
        assert "vectors.json" in names
        assert "bootstrap.py" in names
        assert "README.platform.md" in names
        assert "app/containers/medical.py" in names

        # Domain-neutral: no construction-specific defaults leaked.
        env = zf.read(".env").decode()
        assert "CEREBRUM_DOMAIN_KITS=medical" in env
        assert "dar_al_arkan" not in env.lower()
        assert "construction" not in env.lower()

        # Was render.yaml with a `databases:` block. The package still needs
        # Postgres -- entrypoint.sh migrates against DATABASE_URL -- so the
        # contract DECLARES it instead of provisioning it on a vendor.
        contract = zf.read("deploy/contract.json").decode()
        assert '"kind": "postgres"' in contract
        assert "cerebrum-platform-medical-" in contract


def test_package_uses_fallback_kit_resolution(
    session: SessionState, tmp_path: Path, monkeypatch
):
    """Missing domain kit raises MissingKitError (engine must already resolve)."""
    os.environ["STORAGE_PATH"] = str(tmp_path / "storage2")
    session.config.domain = "nonexistent_xyz"
    # Avoid network fetch of private Cerebrum-Blocks — this test is about kit lookup.
    monkeypatch.setattr(
        "app.core.platform_packager.resolve_engine_source",
        lambda: (tmp_path / "fake-engine", {"commit": "test", "source": "stub"}),
    )
    with pytest.raises(MissingKitError):
        package_platform_session(session)


def test_package_neutral_no_construction_strings(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """No construction-specific words leak into package text files."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_medical_kit.parent.parent.parent))

    info = package_platform_session(session)

    forbidden = ["the-fork", "dar_al_arkan", "projects_folder", "curated_kb"]
    with zipfile.ZipFile(info["zip_path"], "r") as zf:
        for name in zf.namelist():
            if not name.endswith((".py", ".yml", ".yaml", ".json", ".md", ".sh", ".txt", ".env")):
                continue
            text = zf.read(name).decode(errors="ignore").lower()
            for word in forbidden:
                assert word not in text, f"forbidden word {word!r} found in {name}"


def test_package_does_not_leak_factory_llm_key(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """Generated packages must not contain the factory's live LLM API key."""
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(fake_medical_kit.parent.parent.parent))
    monkeypatch.setenv("CEREBRUM_LLM_API_KEY", _GENERATED_KEY_1)
    monkeypatch.setenv("CEREBRUM_LLM_BASE_URL", "https://api.moonshot.ai/v1")
    monkeypatch.setenv("CEREBRUM_LLM_MODEL", "kimi-k2.7-code")
    monkeypatch.setenv("LLM_PROVIDER", "kimi")

    info = package_platform_session(session)

    with zipfile.ZipFile(info["zip_path"], "r") as zf:
        for name in zf.namelist():
            if not name.endswith((".py", ".yml", ".yaml", ".json", ".md", ".sh", ".txt", ".env")):
                continue
            text = zf.read(name).decode(errors="ignore")
            assert _GENERATED_KEY_1 not in text, (
                f"factory LLM key leaked in {name}"
            )
        env = zf.read(".env").decode()
        assert "CEREBRUM_LLM_API_KEY=<owner-supplied>" in env or "# CEREBRUM_LLM_API_KEY" in env
        assert "CEREBRUM_LLM_BASE_URL=https://api.moonshot.ai/v1" in env


def test_package_cli_sourced_from_engine(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """The packaged CLI is copied from the engine checkout."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))

    info = package_platform_session(session)

    with zipfile.ZipFile(info["zip_path"], "r") as zf:
        names = zf.namelist()
        assert "cli/cerebrum_cli/__init__.py" in names
        assert "cli/cerebrum_cli/marker_from_engine.txt" in names
        assert zf.read("cli/cerebrum_cli/marker_from_engine.txt").decode().strip() == "engine"
        assert "cli/install.sh" in names
        assert "cli/config.toml" in names


def test_package_cli_config_stamps_deployed_mode(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """The packaged CLI config.toml declares mode = 'deployed'."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))

    info = package_platform_session(session)

    config_path = Path(info["package_dir"]) / "cli" / "config.toml"
    assert config_path.exists()
    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with open(config_path, "rb") as f:
        cfg = tomllib.load(f)
    assert cfg.get("mode") == "deployed"


def test_vectors_json_includes_embedding_meta(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """Platform package vectors.json always contains an embedding stanza."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))
    session.embedding_meta = {
        "provider": "zvec",
        "backend": "model2vec",
        "dimensions": 256,
        "model": "minishlab/potion-base-8M",
    }
    session.chunks = ["chunk one"]
    session.embeddings = [[0.1] * 256]

    info = package_platform_session(session)
    vectors = json.loads((Path(info["package_dir"]) / "vectors.json").read_text(encoding="utf-8"))
    assert vectors["embedding"] == {
        "provider": "zvec",
        "model": "minishlab/potion-base-8M",
        "dim": 256,
    }


def test_package_cli_missing_engine_cli_raises(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """A missing engine cli/ directory raises the mandated RuntimeError."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))
    # Remove the relocated CLI from the fake engine checkout.
    shutil.rmtree(engine_root / "cli")

    expected = (
        f"Engine checkout at {engine_root} lacks cli/ — requires Cerebrum-Blocks "
        "with the relocated CLI (Spec 2, commit c8176867 or later)"
    )
    with pytest.raises(RuntimeError, match=re.escape(expected)):
        package_platform_session(session)


def test_find_engine_root_falls_back_to_sibling_checkout(monkeypatch, tmp_path: Path):
    """When CEREBRUM_BLOCKS_ROOT is unset, the sibling Cerebrum-Blocks checkout is discovered."""
    from app.core.engine_discovery import _find_engine_root

    monkeypatch.delenv("CEREBRUM_BLOCKS_ROOT", raising=False)
    project_root = tmp_path / "CerebrumDev.ai"
    sibling = tmp_path / "Cerebrum-Blocks"
    sibling.mkdir()
    anchor = project_root / "backend" / "app" / "core" / "engine_discovery.py"
    assert _find_engine_root(anchor) == sibling


def _load_dotenv(package_dir: Path) -> dict:
    """Parse a simple KEY=VALUE .env file."""
    env: Dict[str, str] = {}
    dotenv = package_dir / ".env"
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            env[key] = value
    return env


def _engine_auth_validate(key: str, env: Dict[str, str]) -> bool:
    """Mirror the production key-loading path in Cerebrum-Blocks app/core/auth.py.

    Returns True when *key* is one of the keys the engine would load from the
    packaged environment in production.
    """
    valid = {env.get("CEREBRUM_MASTER_KEY", ""), env.get("CB_DEV_KEY", "")}
    for k, v in env.items():
        if k.startswith("CEREBRUM_API_KEY_") and v:
            valid.add(v)
    return key in valid and key != ""


def test_platform_auth_single_source_of_truth(
    session: SessionState, fake_medical_kit: Path, monkeypatch
):
    """The minted key is wired consistently across .env, render.yaml, and CLI config."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))

    info = package_platform_session(session)
    deploy_key = info["api_key"]
    package_dir = Path(info["package_dir"])

    env = _load_dotenv(package_dir)
    assert env["CEREBRUM_MASTER_KEY"] == deploy_key
    assert env["CB_DEV_KEY"] == deploy_key
    assert env["CEREBRUM_API_KEY_PLATFORM"] == deploy_key

    contract = (package_dir / "deploy" / "contract.json").read_text(encoding="utf-8")
    assert "CEREBRUM_MASTER_KEY" in contract

    try:
        import tomllib
    except ImportError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    with open(package_dir / "cli" / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    assert cfg["api_key"] == deploy_key


def test_platform_auth_boots_with_its_own_key(
    session: SessionState, fake_medical_kit: Path, monkeypatch
):
    """The generated platform package authenticates with its own key and rejects others."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))

    info = package_platform_session(session)
    deploy_key = info["api_key"]
    env = _load_dotenv(Path(info["package_dir"]))

    assert _engine_auth_validate(deploy_key, env) is True
    assert _engine_auth_validate("cb_dev_key", env) is False
    assert _engine_auth_validate("wrong-key", env) is False
    assert _engine_auth_validate("", env) is False


def test_package_vendors_engine(session: SessionState, fake_medical_kit: Path, monkeypatch):
    """The platform package contains a vendored copy of the engine."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))

    info = package_platform_session(session)
    package_dir = Path(info["package_dir"])

    assert (package_dir / "engine" / "app" / "main.py").exists()
    assert (package_dir / "engine" / "block_store" / "README.md").exists()
    assert (package_dir / "engine" / "cli" / "cerebrum_cli" / "__init__.py").exists()

    # Excluded artifacts must not be vendored.
    assert not (package_dir / "engine" / ".git").exists()
    assert not (package_dir / "engine" / "__pycache__").exists()
    assert not (package_dir / "engine" / ".env").exists()

    with zipfile.ZipFile(info["zip_path"], "r") as zf:
        names = zf.namelist()
        assert "engine/app/main.py" in names
        assert "engine/block_store/README.md" in names
        assert "engine/.git/config" not in names
        assert "engine/__pycache__/cache.pyc" not in names
        assert "engine/.env" not in names


def test_package_dockerfile_uses_vendored_engine(
    session: SessionState, fake_medical_kit: Path, monkeypatch
):
    """The generated Dockerfile copies the vendored engine and does not clone."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))

    info = package_platform_session(session)
    package_dir = Path(info["package_dir"])
    dockerfile = (package_dir / "Dockerfile").read_text(encoding="utf-8")

    assert "git clone" not in dockerfile.lower()
    assert "COPY engine/ /app" in dockerfile
    assert "COPY . /app" in dockerfile


def test_package_dockerfile_frontend_copy_is_valid(
    session: SessionState, fake_medical_kit: Path, monkeypatch
):
    """The frontend stage always produces /frontend/dist and the runtime COPY is valid."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))

    info = package_platform_session(session)
    package_dir = Path(info["package_dir"])
    dockerfile = (package_dir / "Dockerfile").read_text(encoding="utf-8")

    assert "2>/dev/null" not in dockerfile
    assert "|| true" not in dockerfile
    assert "mkdir -p /frontend/dist" in dockerfile
    assert "COPY --from=frontend /frontend/dist /app/frontend/dist" in dockerfile


def test_package_build_metadata_records_vendored_engine(
    session: SessionState, fake_medical_kit: Path, monkeypatch
):
    """build_metadata.json records repo, ref, commit SHA, and vendored: true."""
    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))

    info = package_platform_session(session)
    metadata = info["build_metadata"]["engine"]

    assert metadata["source"] == "local"
    assert metadata["repo"] == "https://github.com/bopoadz-del/Cerebrum-Blocks.git"
    assert metadata["vendored"] is True
    assert metadata["vendored_path"] == "engine/"
    assert "commit_sha" in metadata

    written = json.loads(
        (Path(info["package_dir"]) / "build_metadata.json").read_text(encoding="utf-8")
    )
    assert written["engine"]["vendored"] is True
    assert written["engine"]["vendored_path"] == "engine/"


def test_the_platform_package_states_its_requirements_instead_of_a_render_blueprint(
    session: SessionState, fake_medical_kit: Path, monkeypatch
):
    """This package genuinely needs a database -- its entrypoint runs alembic
    when DATABASE_URL is a postgres URL, and its docker-compose ships one. So
    the fix here is not the build path's (sqlite on a volume, no datastore);
    it is to STATE the database rather than provision it on a vendor the
    company has left. A render.yaml for a suspended account is the most
    deploy-shaped file in the delivery, so it is the one a customer opens.
    """
    import json

    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))
    info = package_platform_session(session)
    package_dir = Path(info["package_dir"])

    assert not (package_dir / "render.yaml").exists(), (
        "the platform package still ships a Render blueprint"
    )

    contract = json.loads(
        (package_dir / "deploy" / "contract.json").read_text(encoding="utf-8")
    )
    assert contract["port"] == 8000
    assert contract["health_path"] == "/health"
    volume = contract["persistent_volume"]
    assert volume["required"] is True
    assert volume["mount_path"] == "/app/data"

    # The difference from the build path, stated rather than assumed.
    postgres = [d for d in contract["datastores"] if d["kind"] == "postgres"]
    assert postgres, "the package runs alembic against DATABASE_URL and says so nowhere"
    assert postgres[0]["env"] == "DATABASE_URL"
    assert postgres[0]["required"] is True

    # The compose file is the portable path and already carries a database;
    # it must survive, or removing the blueprint leaves no way to run this.
    assert (package_dir / "docker-compose.yml").is_file()


def test_the_minted_key_is_wired_into_the_contract_not_a_blueprint(
    session: SessionState, fake_medical_kit: Path, monkeypatch
):
    """The key-consistency check moved with the file, and kept the blueprint's
    discipline: render.yaml named CEREBRUM_MASTER_KEY with ``sync: false``, so
    the secret was NEVER in the delivered file. The contract names it as
    required and carries no value -- writing the minted key into a json file
    inside the package would be a regression the old blueprint did not have.
    """
    import json

    engine_root = fake_medical_kit.parent.parent.parent
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(engine_root))
    info = package_platform_session(session)
    package_dir = Path(info["package_dir"])

    env = _load_dotenv(package_dir)
    raw = (package_dir / "deploy" / "contract.json").read_text(encoding="utf-8")
    contract = json.loads(raw)

    assert "CEREBRUM_MASTER_KEY" in contract["secrets_required"]
    assert "CEREBRUM_MASTER_KEY" not in contract.get("environment", {})
    secret = env["CEREBRUM_MASTER_KEY"]
    assert secret and secret not in raw, (
        "the minted key was written into the delivered contract; the blueprint "
        "it replaces used sync: false precisely so it would not be"
    )
