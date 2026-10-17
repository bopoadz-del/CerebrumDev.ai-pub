"""The backend task's size is declared in the repo and the deploy applies it.

Owner decision 2026-10-08: 2 vCPU / 4 GB. The size used to live only in the
live task definition (cerebrumdev-backend:4, 1024/2048) -- deploy-aws.yml
force-rolled whatever revision was live, so no repo change could resize it.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DECLARED = REPO_ROOT / "infra" / "backend_task.json"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-aws.yml"


def _mod():
    path = REPO_ROOT / "scripts" / "declared_task_def.py"
    spec = importlib.util.spec_from_file_location("declared_task_def", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live(cpu="1024", memory="2048", env=None):
    """A describe-task-definition answer shaped like the live revision."""
    return {
        "taskDefinition": {
            "taskDefinitionArn": "arn:aws:ecs:us-west-2:1:task-definition/backend:4",
            "family": "backend",
            "revision": 4,
            "status": "ACTIVE",
            "registeredAt": "2026-10-01T00:00:00Z",
            "registeredBy": "arn:aws:iam::1:user/x",
            "requiresAttributes": [{"name": "ecs.capability.secrets.asm.environment-variables"}],
            "compatibilities": ["EC2", "FARGATE"],
            "taskRoleArn": "arn:aws:iam::1:role/task",
            "executionRoleArn": "arn:aws:iam::1:role/exec",
            "networkMode": "awsvpc",
            "requiresCompatibilities": ["FARGATE"],
            "cpu": cpu,
            "memory": memory,
            "volumes": [{"name": "storage", "efsVolumeConfiguration": {"fileSystemId": "fs-1"}}],
            "containerDefinitions": [
                {
                    "name": "backend",
                    "image": "repo/backend:latest",
                    "portMappings": [{"containerPort": 8000, "protocol": "tcp"}],
                    "environment": env if env is not None else [{"name": "ENV", "value": "production"}],
                    "secrets": [{"name": "TOKEN", "valueFrom": "arn:aws:secretsmanager:x"}],
                    "logConfiguration": {"logDriver": "awslogs", "options": {"awslogs-group": "/ecs/b"}},
                    "mountPoints": [{"sourceVolume": "storage", "containerPath": "/data"}],
                }
            ],
        },
        "tags": [{"key": "app", "value": "factory"}],
    }


DECL = {"container": "backend", "cpu": "2048", "memory": "4096",
        "environment": {"FACTORY_WORKER_PROFILE": "2c-4g"}}


def test_a_differing_live_revision_gets_only_the_declared_fields_replaced():
    live = _live()
    before = copy.deepcopy(live)
    new = _mod().override(live, DECL)
    assert live == before, "the live input is never mutated"
    changed = new.pop("_changed")
    assert changed == ["cpu", "memory", "env:FACTORY_WORKER_PROFILE"]
    assert (new["cpu"], new["memory"]) == ("2048", "4096")
    td = live["taskDefinition"]
    # Read-only fields are dropped; every other field is byte-equal.
    for key in _mod().READ_ONLY_FIELDS:
        assert key not in new
    for key, value in td.items():
        if key in _mod().READ_ONLY_FIELDS or key in ("cpu", "memory", "containerDefinitions"):
            continue
        assert json.dumps(new[key], sort_keys=True) == json.dumps(value, sort_keys=True), key
    old_c, new_c = td["containerDefinitions"][0], new["containerDefinitions"][0]
    for key in old_c:
        if key != "environment":
            assert json.dumps(new_c[key], sort_keys=True) == json.dumps(old_c[key], sort_keys=True), key
    assert new_c["environment"] == old_c["environment"] + [
        {"name": "FACTORY_WORKER_PROFILE", "value": "2c-4g"}
    ]
    assert new["tags"] == live["tags"]


def test_an_existing_env_value_is_replaced_in_place():
    live = _live(env=[{"name": "FACTORY_WORKER_PROFILE", "value": "1c-2g"},
                      {"name": "ENV", "value": "production"}])
    new = _mod().override(live, DECL)
    assert new["containerDefinitions"][0]["environment"] == [
        {"name": "FACTORY_WORKER_PROFILE", "value": "2c-4g"},
        {"name": "ENV", "value": "production"},
    ]


def test_a_matching_live_revision_is_a_no_op():
    live = _live("2048", "4096", env=[{"name": "FACTORY_WORKER_PROFILE", "value": "2c-4g"}])
    assert _mod().override(live, DECL) is None


def test_an_unknown_container_is_refused_not_guessed():
    module = _mod()
    with pytest.raises(module.DeclarationError):
        module.override(_live(), {**DECL, "container": "nope"})


def test_the_cli_writes_nothing_when_unchanged(tmp_path, capsys):
    live = tmp_path / "live.json"
    live.write_text(json.dumps(_live("2048", "4096",
                                     env=[{"name": "FACTORY_WORKER_PROFILE", "value": "2c-4g"}])))
    decl = tmp_path / "d.json"
    decl.write_text(json.dumps(DECL))
    out = tmp_path / "new.json"
    assert _mod().main(["--live", str(live), "--declared", str(decl), "--out", str(out)]) == 0
    assert not out.exists()
    assert "unchanged" in capsys.readouterr().out


def test_the_repo_declares_two_vcpu_four_gb_and_the_2c4g_profile():
    declared = json.loads(DECLARED.read_text(encoding="utf-8"))
    assert (declared["cpu"], declared["memory"]) == ("2048", "4096")
    assert declared["environment"]["FACTORY_WORKER_PROFILE"] == "2c-4g"
    import sys

    sys.path.insert(0, str(REPO_ROOT / "backend"))
    from app.factory.build.codewhale_worker import WORKER_PLANS

    # The declared size and the declared profile describe the same box.
    vcpu, ram = WORKER_PLANS[declared["environment"]["FACTORY_WORKER_PROFILE"]]
    assert (int(vcpu * 1024), ram) == (int(declared["cpu"]), int(declared["memory"]))


def test_the_deploy_applies_the_declaration_before_rolling():
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["deploy"]["steps"]
    roll = next(s for s in steps if "update-service" in (s.get("run") or ""))
    run = roll["run"]
    order = [
        run.index("aws ecs describe-task-definition"),
        run.index("python3 scripts/declared_task_def.py"),
        run.index("aws ecs register-task-definition"),
        run.index("update-service"),
    ]
    assert order == sorted(order)
    assert "infra/backend_task.json" in run
    assert '--task-definition "$target"' in run
    # The checkout the script needs comes before the roll.
    assert any("actions/checkout" in (s.get("uses") or "") for s in steps[: steps.index(roll)])
