"""M9: session product routes map blueprint/registry errors to 400, others to 500."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.factory.blueprint import BlueprintError, CapabilitySpec, ProductBlueprint
from app.factory.dual_registry import DualRegistryError
from app.main import app


def _bp() -> ProductBlueprint:
    return ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id="error-route-demo",
        product_name="Error Route",
        vertical="demo",
        summary="error mapping",
        capabilities=[
            CapabilitySpec(
                id="audit",
                description="audit",
                block_ids=["audit"],
                strategy_hint="REUSE",
            )
        ],
    )


@pytest.fixture()
def product_client(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    from app.core.session_store import create_session, get_session, update_session

    session_id = "sess_product_errors"
    create_session(session_id, "tester")
    state = get_session(session_id)
    assert state is not None
    state.product_design.blueprint = _bp().model_dump(mode="json")
    state.product_design.blueprint_approved = True
    state.product_design.build_level = "prototype"  # the user's typed choice
    state.product_design.plan = {
        "product_id": "error-route-demo",
        "capabilities": [],
    }
    update_session(session_id, state)
    return TestClient(app), session_id


def test_dual_registry_error_is_400(product_client, monkeypatch):
    client, session_id = product_client

    def _boom(*_a, **_k):
        raise DualRegistryError("block not on both shelves")

    monkeypatch.setattr(
        "app.routers.session_product.plan_blueprint", _boom
    )
    res = client.post(f"/v1/sessions/{session_id}/product/plan")
    assert res.status_code == 400, res.text
    assert "both shelves" in res.json()["detail"]


def test_blueprint_error_on_generate_is_400(product_client, monkeypatch):
    client, session_id = product_client

    def _boom(*_a, **_k):
        raise BlueprintError("capabilities must be non-empty")

    monkeypatch.setattr(
        "app.routers.session_product.generate_product", _boom
    )
    res = client.post(f"/v1/sessions/{session_id}/product/generate", json={})
    assert res.status_code == 400, res.text
    assert "capabilities" in res.json()["detail"]


def test_unexpected_generate_error_is_500(product_client, monkeypatch):
    client, session_id = product_client

    def _boom(*_a, **_k):
        raise RuntimeError("disk vanished")

    monkeypatch.setattr(
        "app.routers.session_product.generate_product", _boom
    )
    res = client.post(f"/v1/sessions/{session_id}/product/generate", json={})
    assert res.status_code == 500, res.text
    assert res.json()["detail"] == "internal_error"
    assert "disk vanished" not in res.text
