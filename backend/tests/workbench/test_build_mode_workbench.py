"""Build Mode workbench (M4) tests — steward golden round-trip + boundary guards."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.change_requests.builders import build_expansion_request, sign_and_register
from app.change_requests.intake import intake_change_request
from app.change_requests.queue import ChangeRequestQueue
from app.factory.blueprint import load_blueprint
from app.factory.generator import ProductGenerator
from app.product_dna.emit import verify_checksum_manifest
from app.workbench.agent import run_kimi_cli_agent
from app.workbench.candidate import build_candidate_artifact, verify_candidate_checksum
from app.workbench.envelope import build_task_envelope, load_verified_dna, path_allowed
from app.workbench.promote import PromotionRejected, promote_via_packager
from app.workbench.sandbox import SandboxViolation, WorkbenchSandbox
from app.workbench.session import WorkbenchRejected, promote_request, run_workbench_session

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _flags(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    # Existing fixtures generate Steward under tmp_path/. Confine workbench
    # to that tree so M3 does not break local packager/CLI tests.
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("CHANGE_REQUEST_INTAKE_ENABLED", "true")
    monkeypatch.setenv("BUILD_MODE_ENABLED", "true")
    monkeypatch.setenv("KIMI_WORKBENCH_ENABLED", "false")
    monkeypatch.setenv("WORKBENCH_SESSION_TIMEOUT_SECONDS", "120")


def _write_fake_kimi(tmp_path: Path) -> Path:
    """A stand-in Kimi CLI: `--version` succeeds, any real invocation fails.

    Written per-platform rather than as a `#!/bin/sh` script, because Windows
    cannot exec a shebang file and raised WinError 193 -- which made this
    guard unverifiable on the machine the factory is developed on. A `.cmd`
    launched by absolute path runs without shell=True and gives the same
    exit codes as the POSIX version.
    """
    if os.name == "nt":
        cli = tmp_path / "fake-kimi.cmd"
        cli.write_text(
            "@echo off\r\n"
            'if "%1"=="--version" (echo kimi 9.9.9 & exit /b 0)\r\n'
            "echo boom 1>&2\r\n"
            "exit /b 3\r\n",
            encoding="utf-8",
        )
        return cli
    cli = tmp_path / "fake-kimi"
    cli.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo kimi 9.9.9; exit 0; fi\n'
        "echo boom >&2; exit 3\n",
        encoding="utf-8",
    )
    cli.chmod(0o755)
    return cli


def _steward_product(tmp_path: Path) -> Path:
    bp = load_blueprint(ROOT / "blueprints/steward/steward.v1.yaml")
    out = tmp_path / "Projectsix"
    ProductGenerator(bp, blocks_root=None).generate(out)
    return out


@pytest.fixture
def fresh_audit_artifact(tmp_path: Path) -> Path:
    path = tmp_path / "live_audit.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "live_audit.v1",
                "ran_at": datetime.now(timezone.utc).isoformat(),
                "base_url": "http://test",
                "all_live": True,
                "checks": [{"name": "health", "status": "LIVE"}],
            }
        ),
        encoding="utf-8",
    )
    return path


def _attach_audit_artifact(request_id: str, path: Path) -> dict:
    q = ChangeRequestQueue()
    item = q.get(request_id)
    assert item is not None
    item["audit_artifact"] = str(path)
    q._atomic_write(q._item_path(request_id), item)
    return item


def _approved_expansion(product_id: str, product_root: Path) -> dict:
    doc = build_expansion_request(
        product_id=product_id,
        dna_version="1.0.0",
        requester_type="human",
        requester_id="owner",
        capability="ops_audit_trail",
        block_id="audit",
        rationale="M4 steward golden: add audit block",
        evidence={"scaffold": str(product_root)},
    )
    signed = sign_and_register(doc)
    bundle = load_verified_dna(product_root)
    item = intake_change_request(signed, dna_bundle=bundle, product_root=product_root)
    assert item["state"] == "awaiting_approval"
    decided = ChangeRequestQueue().record_decision(
        item["request_id"], approved=True, actor="owner", note="M4 round-trip"
    )
    assert decided["state"] == "approved"
    assert decided["decision"]["executes"] is False
    return decided


def test_build_mode_flag_off(monkeypatch, tmp_path):
    monkeypatch.setenv("BUILD_MODE_ENABLED", "false")
    with pytest.raises(WorkbenchRejected) as exc:
        run_workbench_session("missing")
    assert exc.value.status_code == 503


def test_sandbox_escape_refused_and_logged(tmp_path):
    sandbox = WorkbenchSandbox("escape-test")
    with pytest.raises(SandboxViolation):
        sandbox.resolve_in_workspace("../outside_secret", write=True)
    assert any(e.get("event") == "boundary_refused" for e in sandbox.transcript)


def test_store_write_impossible(tmp_path, monkeypatch):
    # Seed the forbidden keys so their absence is proven scrubbing, not
    # a vacuously-true assertion about an env that never had them.
    monkeypatch.setenv("RENDER_API_KEY", "seeded")
    monkeypatch.setenv("GITHUB_TOKEN", "seeded")
    monkeypatch.setenv("STORE_WRITE_TOKEN", "seeded")
    sandbox = WorkbenchSandbox("store-test")
    env = sandbox.scrubbed_env()
    assert "RENDER_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "STORE_WRITE_TOKEN" not in env
    assert env.get("WORKBENCH_STORE_WRITE") == "impossible"
    with pytest.raises(SandboxViolation):
        sandbox.attempt_store_write()


def test_path_allowed_envelope_rules(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "candidate").mkdir()
    assert path_allowed(ws, ws / "candidate" / "x.json", write=True)
    assert not path_allowed(ws, ws / "product-dna" / "x.json", write=True)
    assert path_allowed(ws, ws / "product-dna" / "x.json", write=False)
    assert not path_allowed(ws, tmp_path / "other" / "x", write=True)


def test_tampered_candidate_fails_at_packager(tmp_path, fresh_audit_artifact):
    product = _steward_product(tmp_path)
    item = _approved_expansion("projectsix", product)
    _attach_audit_artifact(item["request_id"], fresh_audit_artifact)
    result = run_workbench_session(item["request_id"], product_root=product, run_gates=True)
    assert result["state"] == "gate_passed"
    result["audit_artifact"] = str(fresh_audit_artifact)
    candidate = dict(result["candidate"])
    candidate["summary"] = "tampered summary"
    assert verify_candidate_checksum(candidate) is False
    with pytest.raises(PromotionRejected):
        promote_via_packager(
            item=result,
            candidate=candidate,
            product_root=product,
            actor="owner",
        )


def test_steward_expansion_round_trip_to_staging(tmp_path, fresh_audit_artifact):
    """Signed EXPANSION → approved → workbench → gates → packager staging."""
    product = _steward_product(tmp_path)
    assert (product / "product-dna" / "checksum_manifest.json").is_file()

    item = _approved_expansion("projectsix", product)
    _attach_audit_artifact(item["request_id"], fresh_audit_artifact)
    result = run_workbench_session(item["request_id"], product_root=product, run_gates=True)
    assert result["state"] == "gate_passed"
    assert result["candidate"]["applied"] is False
    assert result["candidate"]["checksum"]
    assert result["gate_report"]["passed"] is True
    assert any(a["kind"] == "candidate_ready" for a in result.get("audit_trail") or [])

    staged = promote_request(item["request_id"], actor="owner")
    assert staged["state"] == "staged"
    promotion = staged["promotion"]
    assert promotion["trust_tier"] == "staging/community"
    assert promotion["certified"] is False
    assert promotion["deployed"] is False
    assert promotion["store_published"] is False
    assert promotion["deploy_credentials"] is False

    out = Path(promotion["output_dir"])
    assert out.is_dir()
    dna = out / "product-dna"
    assert verify_checksum_manifest(dna) == []
    manifest = json.loads((dna / "generation_manifest.json").read_text(encoding="utf-8"))
    assert manifest["product_dna_version"] == promotion["product_dna_version"]
    assert manifest["product_dna_version"] != "1.0.0"
    lock = json.loads((dna / "block_lockfile.json").read_text(encoding="utf-8"))
    pin_ids = {p["block_id"] for p in lock.get("pins") or []}
    assert "audit" in pin_ids

    # Full audit trail readable end-to-end
    kinds = [a["kind"] for a in staged.get("audit_trail") or []]
    assert "workbench_started" in kinds
    assert "candidate_ready" in kinds
    assert "gate_passed" in kinds
    assert "staged" in kinds

    receipt = out / "STAGING_PROMOTION.json"
    assert receipt.is_file()


def test_envelope_checksum_stable(tmp_path):
    product = _steward_product(tmp_path)
    dna = load_verified_dna(product)
    req = {
        "request_id": "r1",
        "kind": "EXPANSION",
        "product_id": "projectsix",
        "dna_version": "1.0.0",
        "expansion": {"capability": "x", "block_id": "audit"},
    }
    env = build_task_envelope(
        request=req,
        dry_run={"acceptance_gates_to_rerun": ["dna_checksum_verify"], "would_change": []},
        dna_bundle=dna,
        workspace_root=tmp_path / "ws",
        product_root=product,
    )
    assert env["envelope_checksum"]
    assert "production_deploy" in env["mutable"]["forbidden"]


def _assert_legal_kimi_argv(argv):
    """Reject argv combinations the real Kimi CLI refuses at startup.

    ``--prompt`` implies auto permission mode; ``--yolo`` and ``--auto`` are
    mutually exclusive with it (and with each other).
    """
    flags = set(argv)
    if "--prompt" in flags and ({"--yolo", "--auto"} & flags):
        raise AssertionError(f"illegal Kimi argv: --prompt with --yolo/--auto: {argv}")
    if "--yolo" in flags and "--auto" in flags:
        raise AssertionError(f"illegal Kimi argv: --yolo with --auto: {argv}")


def test_kimi_cli_invoked_with_prompt_and_workspace(monkeypatch, tmp_path):
    """When KIMI_WORKBENCH_ENABLED is true, the agent must pass the brief to the
    Kimi CLI inside the sandbox workspace — not only run --version and fall back —
    and the CLI's edits must survive into the candidate diff.
    """
    monkeypatch.setenv("KIMI_WORKBENCH_ENABLED", "true")
    monkeypatch.setenv("KIMI_CODE_CLI", "kimi")

    product = _steward_product(tmp_path)
    dna = load_verified_dna(product)
    req = {
        "request_id": "r1",
        "kind": "EXPANSION",
        "product_id": "projectsix",
        "dna_version": "1.0.0",
        "expansion": {"capability": "x", "block_id": "audit"},
    }
    envelope = build_task_envelope(
        request=req,
        dry_run={"acceptance_gates_to_rerun": ["dna_checksum_verify"], "would_change": []},
        dna_bundle=dna,
        workspace_root=tmp_path / "ws",
        product_root=product,
    )
    sandbox = WorkbenchSandbox("kimi-cli-test")
    calls = []

    def fake_run_command(argv, *, cwd_relative=".", timeout=None):
        _assert_legal_kimi_argv(argv)
        calls.append({"argv": list(argv), "cwd": cwd_relative})
        if argv[1:] == ["--version"]:
            return {"returncode": 0, "stdout_tail": "kimi 1.0.0", "stderr_tail": ""}
        # Simulate Kimi producing the candidate blueprint in the workspace.
        baseline = (product / "product-dna" / "product_blueprint.yaml").read_text(
            encoding="utf-8"
        )
        bp_path = sandbox.workspace / "blueprint" / "product_blueprint.yaml"
        bp_path.parent.mkdir(parents=True, exist_ok=True)
        bp_path.write_text(baseline + "\n# edited by kimi\n", encoding="utf-8")
        return {"returncode": 0, "stdout_tail": "done", "stderr_tail": ""}

    monkeypatch.setattr(sandbox, "run_command", fake_run_command)

    result = run_kimi_cli_agent(sandbox, envelope=envelope, product_root=product)

    # Must invoke the CLI with the prompt file and the workspace, not only --version.
    non_version = [c for c in calls if c["argv"][1:] != ["--version"]]
    assert len(non_version) >= 1, f"expected a real CLI invocation, got {calls}"
    real = non_version[0]
    assert "--prompt" in real["argv"], f"missing --prompt in {real['argv']}"
    prompt_index = real["argv"].index("--prompt")
    prompt_arg = real["argv"][prompt_index + 1]
    assert "kimi_prompt" in prompt_arg, f"expected prompt file arg, got {prompt_arg}"
    assert "--add-dir" in real["argv"], f"missing --add-dir in {real['argv']}"
    assert result.get("backend") != "factory_regenerator_stub"

    # The sentinel must survive into the candidate diff — behaviour, not shape.
    candidate = build_candidate_artifact(
        sandbox, agent_result=result, product_root=product
    )
    assert "# edited by kimi" in candidate["diff"], (
        "CLI edit did not survive into candidate diff"
    )


def test_workbench_fails_loudly_when_cli_missing(monkeypatch, tmp_path, fresh_audit_artifact):
    """KIMI_WORKBENCH_ENABLED=true with no CLI must NOT succeed via silent stub."""
    monkeypatch.setenv("KIMI_WORKBENCH_ENABLED", "true")
    monkeypatch.setenv("KIMI_CODE_CLI", str(tmp_path / "definitely-missing-kimi"))

    product = _steward_product(tmp_path)
    item = _approved_expansion("projectsix", product)
    _attach_audit_artifact(item["request_id"], fresh_audit_artifact)

    with pytest.raises(WorkbenchRejected) as exc:
        run_workbench_session(item["request_id"], product_root=product, run_gates=True)
    assert "cli_not_found" in exc.value.reason
    assert exc.value.status_code == 502

    after = ChangeRequestQueue().get(item["request_id"])
    assert after["state"] != "gate_passed"
    assert "cli_not_found" in json.dumps(after.get("gate_report") or {})


def test_workbench_fails_loudly_when_cli_session_fails(
    monkeypatch, tmp_path, fresh_audit_artifact
):
    """A CLI that exists but whose coding session fails must not become ok:True."""
    fake_cli = _write_fake_kimi(tmp_path)
    monkeypatch.setenv("KIMI_WORKBENCH_ENABLED", "true")
    monkeypatch.setenv("KIMI_CODE_CLI", str(fake_cli))

    product = _steward_product(tmp_path)
    item = _approved_expansion("projectsix", product)
    _attach_audit_artifact(item["request_id"], fresh_audit_artifact)

    with pytest.raises(WorkbenchRejected) as exc:
        run_workbench_session(item["request_id"], product_root=product, run_gates=True)
    assert "cli_session_failed" in exc.value.reason
    assert "returncode=3" in exc.value.reason

    after = ChangeRequestQueue().get(item["request_id"])
    assert after["state"] != "gate_passed"


def test_candidate_artifact_propagates_cli_error_fields(monkeypatch, tmp_path):
    """candidate.agent must carry kimi_error and cli_returncode, not drop them."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    product = _steward_product(tmp_path)
    sandbox = WorkbenchSandbox("candidate-propagation-test")
    agent_result = {
        "backend": "factory_regenerator_stub",
        "honesty": "STUB",
        "kimi_fallback": True,
        "kimi_error": "cli_session_failed",
        "cli_returncode": 3,
        "summary": "fell back",
    }
    candidate = build_candidate_artifact(
        sandbox, agent_result=agent_result, product_root=product
    )
    assert candidate["agent"]["kimi_error"] == "cli_session_failed"
    assert candidate["agent"]["cli_returncode"] == 3


def test_workbench_product_root_must_stay_inside_factory_outputs(tmp_path, monkeypatch):
    from app.factory.paths import factory_outputs_root
    from app.workbench.session import _resolve_product_root

    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    (factory_outputs_root() / "ok").mkdir(parents=True)
    with pytest.raises(WorkbenchRejected, match="factory_outputs"):
        _resolve_product_root({}, Path("/etc"))
    with pytest.raises(WorkbenchRejected, match="factory_outputs"):
        _resolve_product_root({}, tmp_path / "outside")
    ok = _resolve_product_root({}, factory_outputs_root() / "ok")
    assert ok == (factory_outputs_root() / "ok").resolve()


def test_load_verified_dna_rejects_schema_invalid_doc(tmp_path):
    """A DNA doc that passes checksums but violates its schema must be refused."""
    import hashlib

    from app.workbench.envelope import EnvelopeError

    product = _steward_product(tmp_path)
    dna = product / "product-dna"
    bad_path = dna / "security_policy.json"
    # Valid checksum, invalid document: required fields removed.
    bad_path.write_text(json.dumps({"nonsense": True}), encoding="utf-8")
    manifest_path = dna / "checksum_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["security_policy.json"] = hashlib.sha256(
        bad_path.read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    with pytest.raises(EnvelopeError) as exc:
        load_verified_dna(product)
    assert "security_policy" in str(exc.value)
