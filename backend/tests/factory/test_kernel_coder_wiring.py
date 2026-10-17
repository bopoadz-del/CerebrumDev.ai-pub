"""COLLECTOR may consult the coding agent; TESTER does not.

The harness's acceptance IS the tester. These tests pin collector review
as report-only (gaps unchanged) and that TESTER no longer asks the LLM
for extra domain cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.roles import (
    RoleContext,
    _is_payload_mutation,
    _payload_constraint_violations,
    run_collector,
    run_tester,
)
from app.factory.build.workspace import RoleWorkspace


@pytest.fixture(autouse=True)
def _coder_on(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")


class _Cap:
    def __init__(self, cid, block_ids=()):
        self.capability_id = cid
        self.block_ids = tuple(block_ids)
        self.notes = cid


class _Plan:
    def __init__(self, *caps):
        self.capabilities = caps


class _Blueprint:
    product_name = "Kernel Probe"
    product_id = "kernel-probe"
    vertical = "testing"


def _ctx(tmp_path: Path, role: BuildRole, plan=None, state=None):
    ws = RoleWorkspace(role, tmp_path / "build")
    return RoleContext(
        role=role,
        workspace=ws,
        blueprint=_Blueprint(),
        plan=plan or _Plan(_Cap("retail_core", ("audit",)), _Cap("loyalty")),
        state=dict(state or {}),
    )


def test_collector_records_agent_reviews_without_inventing_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.factory.coder.review_capability_bindings",
        lambda **kw: {
            "reviews": [
                {
                    "capability_id": "retail_core",
                    "block_ids": ["audit"],
                    "verdict": "endorse",
                    "reason": "audit trail is a fit",
                },
                {
                    "capability_id": "loyalty",
                    "block_ids": [],
                    "verdict": "mismatch",
                    "reason": "no block bound",
                },
            ],
            "model": "stub-collector",
        },
    )
    ctx = _ctx(tmp_path, BuildRole.COLLECTOR)
    result = run_collector(ctx)
    assert result.ok
    assert result.gaps == ("loyalty",)
    reviews = result.notes["agent_binding_reviews"]
    assert {r["capability_id"]: r["verdict"] for r in reviews} == {
        "retail_core": "endorse",
        "loyalty": "mismatch",
    }
    assert "coding agent reviewed" in result.detail
    assert ctx.workspace.written == []  # COLLECTOR stays read-only
    intake = result.notes.get("intake_blueprint") or {}
    assert intake.get("schema_version") == "intake_blueprint.v1"
    assert {c["id"] for c in intake.get("capabilities") or []} >= {"retail_core", "loyalty"}


def test_collector_without_coder_stays_deterministic(tmp_path, monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    called = []
    monkeypatch.setattr(
        "app.factory.coder.review_capability_bindings",
        lambda **kw: called.append(kw) or {"reviews": [], "model": "nope"},
    )
    result = run_collector(_ctx(tmp_path, BuildRole.COLLECTOR))
    assert called == []
    assert result.notes["agent_binding_reviews"] == []
    assert result.gaps == ("loyalty",)


def test_payload_mutation_rejects_replacements_and_new_keys():
    sample = {"status": "open", "title": "x"}
    assert _is_payload_mutation(sample, {"status": "closed", "title": "x"})
    assert not _is_payload_mutation(sample, sample)
    assert not _is_payload_mutation(sample, {"status": "open", "title": "x", "extra": 1})
    assert not _is_payload_mutation(sample, {})


def test_payload_constraint_violations_match_the_spec():
    spec = {
        "fields": [
            {"name": "status", "type": "str", "allowed_values": ["open", "closed"]},
            {"name": "qty", "type": "int", "min": 0, "max": 10},
        ]
    }
    assert _payload_constraint_violations({"status": "open", "qty": 1}, spec) == []
    assert "status" in _payload_constraint_violations({"status": "nope", "qty": 1}, spec)
    assert "qty" in _payload_constraint_violations({"status": "open", "qty": -1}, spec)

def test_tester_does_not_consult_the_llm(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(
        "app.factory.coder.propose_domain_test_cases",
        lambda **kw: called.append(kw) or {"cases": [], "model": "nope"},
    )
    spec = {
        "retail_core": {
            "entity": "retail_core",
            "fields": [{"name": "reference", "type": "str", "required": True}],
        }
    }
    ctx = _ctx(
        tmp_path,
        BuildRole.TESTER,
        plan=_Plan(_Cap("retail_core", ("audit",))),
        state={"model_specs": spec, "vendored_blocks": ("audit",)},
    )
    result = run_tester(ctx)
    assert result.ok
    assert called == []
    assert result.notes["agent_domain_cases"] == []
    assert not (tmp_path / "build" / "tests" / "agent_domain_cases.py").exists()
    assert not (tmp_path / "build" / "tests" / "test_agent_domain.py").exists()
    assert (tmp_path / "build" / "tests" / "test_routes.py").is_file()
