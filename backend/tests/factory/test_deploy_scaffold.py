"""A delivered platform must be deployable, not merely runnable locally.

New-shape tests for the deploy scaffold. The runner produced a working
application with no way to ship it: an inventory against the template
generator showed Dockerfile, Procfile, render.yaml and .env.example among the
83 files only the old path emitted. Without them the artifact runs on a
developer's machine and nowhere else.

The scaffold is templated rather than coder-written on purpose. Container and
process config is mechanical — there is no domain judgement for an agent to
add, and a hallucinated base image or start command is a deployment failure
rather than a test failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import AuthorityError, BuildRole, assert_write_allowed
from app.factory.build.runner import RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture()
def built(tmp_path, stub_coder):
    # 0.5: without a coding agent the WRITER refuses (writer_no_output);
    # the fixture stubs a deterministic agent so the build goes green.
    out = tmp_path / "build"
    assert RoleRunner(load_blueprint(SMOKE), out).run().ok
    return out


def test_the_deploy_scaffold_is_present(built):
    for name in ("Dockerfile", ".dockerignore", "Procfile", ".env.example"):
        assert (built / name).is_file(), f"missing {name}"
    assert (built / "scripts" / "acceptance.py").is_file()
    assert (built / "deploy" / "contract.json").is_file()
    assert (built / ".github" / "workflows" / "ci.yml").is_file()
    assert (built / "docs" / "openapi.json").is_file()
    assert (built / "app" / "auth.py").is_file()
    assert (built / "app" / "static" / "index.html").is_file()


def test_the_dockerfile_starts_the_platform_and_provisions_storage(built):
    text = (built / "Dockerfile").read_text(encoding="utf-8")
    assert "requirements.txt" in text
    # sqlite needs its directory to exist; the container must create it.
    assert "STORAGE_PATH" in text
    assert "mkdir -p" in text
    # F19: a red suite must not produce a deployable image.
    assert "scripts/release_gate.py" in text
    assert "requirements-dev.txt" in text
    # S10: migrate against the mounted disk, then serve. uvicorn lives in
    # the entrypoint so a failed revision refuses boot.
    assert "scripts/entrypoint.sh" in text
    entry = (built / "scripts" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "alembic upgrade head" in entry
    assert "uvicorn" in entry and "app.main:app" in entry


def test_the_deploy_contract_declares_no_database(built):
    """Persistence is a sqlite file. A Postgres or key-value block here would
    provision paid infrastructure the platform never uses -- the exact class of
    charge that prompted rebuilding this deployment in the first place. The
    assertion moved from render.yaml to the contract when Render went away; the
    rule did not."""
    import json

    text = (built / "deploy" / "contract.json").read_text(encoding="utf-8")
    contract = json.loads(text)
    assert contract["datastores"] == []
    assert contract["health_path"] == "/health"
    assert contract["persistent_volume"]["mount_path"] == "/app/data"
    for forbidden in ("postgres", "keyvalue", "redis"):
        assert forbidden not in text.lower(), forbidden


def test_the_service_name_is_slugged_from_the_product_id(tmp_path, stub_coder):
    # 0.5: needs a green build; stub a deterministic coding agent.
    out = tmp_path / "b"
    assert RoleRunner(load_blueprint(SMOKE), out).run().ok
    import json

    contract = json.loads((out / "deploy" / "contract.json").read_text(encoding="utf-8"))
    task = json.loads(
        (out / "deploy" / "aws" / "task-definition.json").read_text(encoding="utf-8")
    )
    # product_id is "runner-smoke"; a raw value with spaces or capitals is not a
    # legal ECS family or EFS volume name either, so the slug still has to hold.
    assert contract["service"] == "runner-smoke"
    assert task["family"] == "runner-smoke"
    assert task["volumes"][0]["name"] == "runner-smoke-data"


def test_the_env_example_offers_no_store_wiring(built):
    """The template generator's .env.example documents a store URL because its
    handlers POST to one. This platform's handlers import the blocks vendored
    beside them, so offering a store variable would misdescribe how it runs."""
    text = (built / ".env.example").read_text(encoding="utf-8")
    assert "STORAGE_PATH" in text
    assert "P1" in text
    for token in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "/v1/execute"):
        assert f"{token}=" not in text, f"{token} offered in a P1 platform"


def test_the_dockerignore_excludes_build_and_runtime_artefacts(built):
    text = (built / ".dockerignore").read_text(encoding="utf-8")
    for entry in ("__pycache__/", "data/", ".env", "build_ledger.jsonl"):
        assert entry in text, entry


def test_the_writer_lane_admits_the_scaffold_but_stays_narrow(tmp_path):
    """Named files, not a root wildcard: the writer must not be able to drop
    arbitrary files at the workspace root."""
    ws = tmp_path / "w"
    ws.mkdir()
    for allowed in (
        "Dockerfile",
        "Procfile",
        ".env.example",
        ".dockerignore",
        "deploy/contract.json",
        "deploy/README.md",
        "deploy/aws/task-definition.json",
        "alembic.ini",
        "scripts/entrypoint.sh",
        "scripts/rollback.sh",
        "docs/data_lifecycle.json",
        "docs/deploy.json",
    ):
        assert assert_write_allowed(BuildRole.WRITER, ws / allowed, workspace=ws)
    assert assert_write_allowed(
        BuildRole.WRITER, ws / "alembic" / "versions" / "0001_baseline.py", workspace=ws
    )
    for allowed in (
        "product-dna/entity_model.json",
        "docs/blueprint/product_blueprint.json",
        "docs/provenance/provenance.json",
        "docs/certification/dual_certification.json",
        "docs/edge_profile.json",
        "docs/network_posture.json",
        "docs/sbom.cdx.json",
        "docs/permissions.json",
        "docs/domain_pack.json",
        "docs/coder_brief.md",
        "frontend/src/App.tsx",
        "scripts/acceptance.py",
        ".github/workflows/ci.yml",
        "docs/openapi.json",
    ):
        assert assert_write_allowed(BuildRole.WRITER, ws / allowed, workspace=ws)
    # deploy/ is three NAMED files, not a wildcard. A `deploy/**` entry would
    # pass every assertion above while letting the writer drop anything into a
    # directory the customer is told to read as generated.
    for denied in (
        "docker-compose.yml",
        "Makefile",
        "setup.py",
        "deploy/anything-else.yaml",
        "deploy/aws/extra.json",
    ):
        with pytest.raises(AuthorityError):
            assert_write_allowed(BuildRole.WRITER, ws / denied, workspace=ws)


def test_no_other_role_may_write_the_scaffold(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    for role in (BuildRole.CLONER, BuildRole.TESTER, BuildRole.COLLECTOR):
        with pytest.raises(AuthorityError):
            assert_write_allowed(role, ws / "Dockerfile", workspace=ws)


# --- the delivered platform must be deployable on the infrastructure that
# --- actually exists, and must state what it needs rather than inherit it ----

def test_the_platform_states_its_runtime_requirements_in_one_machine_readable_place(built):
    """Render injected five things no config file mentioned, and the hosting
    migration found each one by breaking: PORT, DATABASE_URL, the persistent
    disk, RENDER_GIT_COMMIT and the datastore links. A delivered platform must
    not inherit that trick — what it needs to run has to be written down where
    a deployer can read it, not implied by a blueprint for one vendor."""
    import json

    contract_path = built / "deploy" / "contract.json"
    assert contract_path.is_file(), (
        "the platform ships no deploy contract; its requirements live only in a "
        "vendor blueprint, which is how an unstated requirement becomes an outage"
    )
    contract = json.loads(contract_path.read_text(encoding="utf-8"))

    assert contract["port"] == 8000
    assert contract["health_path"] == "/health"
    # The one that wipes data when it is missed.
    volume = contract["persistent_volume"]
    assert volume["mount_path"] == "/app/data"
    assert volume["required"] is True
    assert "sqlite" in volume["reason"].lower()
    # Cost discipline, carried over from the Render blueprint's own note.
    assert contract["datastores"] == []


def test_the_contract_agrees_with_the_container_it_describes(built):
    """A contract that drifts from the Dockerfile is worse than none: it reads
    as authoritative and is wrong. Both are generated, so they must be
    generated from the same values."""
    import json

    contract = json.loads((built / "deploy" / "contract.json").read_text(encoding="utf-8"))
    dockerfile = (built / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (built / "scripts" / "entrypoint.sh").read_text(encoding="utf-8")

    assert f"EXPOSE {contract['port']}" in dockerfile
    assert f"${{PORT:-{contract['port']}}}" in entrypoint, (
        "the container must default to the contract's port when the platform "
        "injects none — ECS does not inject PORT the way Render did"
    )
    assert contract["persistent_volume"]["mount_path"] in dockerfile


def test_the_platform_ships_an_aws_deploy_path_that_mounts_the_volume(built):
    """ECS gives a task no disk. The Render blueprint declared one and that is
    the only reason sqlite survived a deploy; an ECS task definition without an
    EFS mount loses every row on the next roll, silently."""
    import json

    task_def_path = built / "deploy" / "aws" / "task-definition.json"
    assert task_def_path.is_file(), "no AWS deploy path; the platform is undeployable as delivered"
    task_def = json.loads(task_def_path.read_text(encoding="utf-8"))

    volumes = task_def.get("volumes") or []
    assert any(v.get("efsVolumeConfiguration") for v in volumes), (
        "the task definition declares no EFS volume, so the sqlite file sits on "
        "ephemeral storage and every deploy wipes the platform's data"
    )
    container = task_def["containerDefinitions"][0]
    mounts = {m["containerPath"] for m in container.get("mountPoints") or []}
    assert "/app/data" in mounts
    # PORT is not injected on ECS; the container must be told.
    env = {e["name"]: e["value"] for e in container.get("environment") or []}
    assert env.get("STORAGE_PATH") == "/app/data"


def test_no_render_blueprint_is_shipped(built):
    """Render is gone. A blueprint for a suspended account is not neutral: it
    is the most deploy-shaped file in the delivery, so it is what a customer
    reaches for first."""
    assert not (built / "render.yaml").exists(), (
        "the platform still ships a Render blueprint for infrastructure that "
        "no longer exists"
    )
