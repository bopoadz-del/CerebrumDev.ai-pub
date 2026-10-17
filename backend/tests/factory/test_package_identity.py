"""S8: two RoleRunner builds of the same blueprint share one content digest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.authority import AuthorityError, BuildRole, assert_write_allowed
from app.factory.build.package import (
    IDENTITY_REL,
    artifact_digest,
    residue_paths,
)
from app.factory.build.runner import RoleRunner
from app.factory.build.supply_chain import PYTHON_312_SLIM_FROM

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


def test_two_builds_share_a_digest(tmp_path, stub_coder):
    bp = load_blueprint(SMOKE)
    a = tmp_path / "a"
    b = tmp_path / "b"
    assert RoleRunner(bp, a).run().ok
    assert RoleRunner(bp, b).run().ok
    da = artifact_digest(a)
    db = artifact_digest(b)
    assert da == db
    ident_a = json.loads((a / IDENTITY_REL).read_text(encoding="utf-8"))
    ident_b = json.loads((b / IDENTITY_REL).read_text(encoding="utf-8"))
    assert ident_a["digest"] == da
    assert ident_b["digest"] == db
    assert ident_a == ident_b
    assert (a / "build_ledger.jsonl").is_file()
    assert "build_ledger.jsonl" in residue_paths(a)
    docker = (a / "Dockerfile").read_text(encoding="utf-8")
    assert PYTHON_312_SLIM_FROM in docker
    assert "@sha256:" in docker


def test_identity_lane_is_named(tmp_path):
    ws = tmp_path / "w"
    ws.mkdir()
    assert assert_write_allowed(
        BuildRole.WRITER, ws / "docs" / "package_identity.json", workspace=ws
    )
    with pytest.raises(AuthorityError):
        assert_write_allowed(
            BuildRole.TESTER, ws / "docs" / "package_identity.json", workspace=ws
        )
