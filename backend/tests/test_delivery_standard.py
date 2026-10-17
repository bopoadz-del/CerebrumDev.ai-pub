"""Delivery standard: immutable prompt, fail-closed renderer, API, agent wiring."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.factory import delivery_standard as ds

PLATFORM = {
    "product_repository": "bopoadz-del/Cerebrum-BuildOps",
    "default_branch": "main",
    "working_branch": "feat/buildops-platform-v1",
    "pull_request": "none",
    "head_sha": "abc123def456",
    "capability_repository": "bopoadz-del/Cerebrum-Blocks",
    "factory_repository": "bopoadz-del/CerebrumDev.ai",
    "reference_repositories": "bopoadz-del/Projecttwo",
    "deployment_target": "RENDER",
    "production_url": "not deployed",
    "test_result": "unknown",
    "deployment_state": "new platform",
}

DOMAIN_PACK = {
    "platform_name": "Cerebrum BuildOps",
    "domain": "Construction and project controls",
    "product_type": "Project controls platform",
    "target_users": "Project managers, controls engineers",
    "mission": "A construction project-controls platform with deterministic EV maths and approval gates.",
    "domain_purpose": "One system of record for programme and cost performance.",
    "primary_users": ["Project manager", "Controls engineer"],
    "required_roles": ["project_admin", "project_manager", "viewer"],
    "required_product_modules": ["Programme dashboard", "Cost controls", "Claims"],
    "core_business_workflows": "Baseline import → progress update → variance → forecast.",
    "authoritative_calculations": ["Schedule variance", "Cost variance", "Earned value"],
    "domain_rules": "Baselines are immutable once approved.",
    "high_impact_actions": ["Baseline approval", "Claim issue", "Contract notice"],
    "prohibited_autonomous_actions": "No contractual notice without human approval.",
    "data_sources": ["XER", "XLSX", "Site diaries"],
    "required_connectors": "File import (XER, XLSX, CSV, PDF).",
    "required_exports": ["EV report (XLSX)", "Change-order register (CSV)"],
    "security_regulatory_rules": "Contract documents are privileged; cross-project access fails closed.",
    "demo_data_requirements": "One mid-life infrastructure project, labeled demo accounts.",
    "domain_acceptance_conditions": "EV report reproduces a hand calculation; no claim without recorded approval.",
}


# --- the standard is immutable ------------------------------------------------


def test_standard_prompt_is_immutable():
    """'We keep that prompt unchanged' — enforced, not hoped for."""
    assert ds.standard_sha256() == ds.STANDARD_SHA256


# --- renderer -----------------------------------------------------------------


def test_render_fills_every_slot():
    brief = ds.render(PLATFORM, DOMAIN_PACK)
    assert "PLATFORM_NAME: Cerebrum BuildOps" in brief
    assert "DOMAIN: Construction and project controls" in brief
    assert "DEPLOYMENT_TARGET: RENDER" in brief
    assert "bopoadz-del/Cerebrum-BuildOps" in brief
    assert "abc123def456" in brief
    assert "- Programme dashboard\n- Cost controls\n- Claims" in brief  # list → bullets
    assert DOMAIN_PACK["mission"] in brief
    for marker in (
        "[INSERT",
        "[OWNER/",
        "[MAIN_OR_MASTER]",
        "[FEATURE_BRANCH]",
        "[PR_NUMBER",
        "[CURRENT_HEAD_SHA]",
        "[BLOCK_STORE",
        "[FACTORY_REPOSITORY",
        "[REFERENCE_REPOSITORIES",
        "[RENDER /",
        "[URL_IF",
        "[TEST_RESULT",
        "[STATE]",
    ):
        assert marker not in brief, f"unfilled slot leaked: {marker}"


def test_render_domain_fields_land_in_document_order():
    sentinels = {f: f"SENTINEL_{i:02d}" for i, f in enumerate(ds.DOMAIN_PACK_FIELDS)}
    brief = ds.render(PLATFORM, {**DOMAIN_PACK, **sentinels})
    positions = [brief.index(f"SENTINEL_{i:02d}") for i in range(len(ds.DOMAIN_PACK_FIELDS))]
    assert positions == sorted(positions)  # every [INSERT] filled under the right label


def test_render_missing_domain_key_fails_closed():
    bad = {k: v for k, v in DOMAIN_PACK.items() if k != "authoritative_calculations"}
    with pytest.raises(ValueError, match="authoritative_calculations"):
        ds.render(PLATFORM, bad)


def test_render_missing_platform_key_fails_closed():
    bad = {k: v for k, v in PLATFORM.items() if k != "head_sha"}
    with pytest.raises(ValueError, match="head_sha"):
        ds.render(bad, DOMAIN_PACK)


def test_render_blank_value_counts_as_missing():
    with pytest.raises(ValueError, match="domain_purpose"):
        ds.render(PLATFORM, {**DOMAIN_PACK, "domain_purpose": "   "})


def test_render_missing_mission_fails_closed():
    bad = {k: v for k, v in DOMAIN_PACK.items() if k != "mission"}
    with pytest.raises(ValueError, match="mission"):
        ds.render(PLATFORM, bad)


# --- API -----------------------------------------------------------------------


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    # No master key now means "refuse", not "let everyone in", so an
    # unauthenticated fixture has to ask for the dev principal.
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    from app.routers import delivery_standard as router_mod

    app = FastAPI()
    app.include_router(router_mod.router, prefix="/v1/factory/delivery-standard")
    return TestClient(app)


def test_api_standard_info(client):
    res = client.get("/v1/factory/delivery-standard")
    assert res.status_code == 200
    body = res.json()
    assert body["sha256"] == ds.STANDARD_SHA256
    assert body["coder"] == "kimi_code"
    assert "authoritative_calculations" in body["domain_pack_fields"]


def test_api_render(client):
    res = client.post(
        "/v1/factory/delivery-standard/render",
        json={"platform": PLATFORM, "domain_pack": DOMAIN_PACK},
    )
    assert res.status_code == 200
    assert "PLATFORM_NAME: Cerebrum BuildOps" in res.json()["brief"]


def test_api_render_incomplete_returns_400_naming_missing_keys(client):
    res = client.post(
        "/v1/factory/delivery-standard/render",
        json={"platform": PLATFORM, "domain_pack": {"platform_name": "x"}},
    )
    assert res.status_code == 400
    assert "domain_pack.authoritative_calculations" in res.json()["detail"]


# --- workbench wiring -----------------------------------------------------------


def test_agent_brief_uses_standard_when_pack_present():
    from app.workbench.agent import _standard_brief

    envelope = {
        "kind": "EXPANSION",
        "envelope_checksum": "abc123",
        "request": {"platform": PLATFORM, "domain_pack": DOMAIN_PACK},
    }
    brief = _standard_brief(envelope)
    assert brief is not None
    assert "PLATFORM_NAME: Cerebrum BuildOps" in brief
    assert "kind=EXPANSION" in brief
    assert "no Store writes" in brief


def test_agent_brief_is_none_without_pack():
    from app.workbench.agent import _standard_brief

    assert _standard_brief({"request": {}}) is None


def test_agent_brief_rejects_one_sided_pack():
    from app.workbench.agent import AgentError, _standard_brief

    with pytest.raises(AgentError, match="both 'platform' and 'domain_pack'"):
        _standard_brief({"request": {"platform": PLATFORM}})


def test_agent_brief_rejects_incomplete_pack():
    from app.workbench.agent import AgentError, _standard_brief

    with pytest.raises(AgentError, match="incomplete"):
        _standard_brief({"request": {"platform": PLATFORM, "domain_pack": {"platform_name": "x"}}})
