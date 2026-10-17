"""Phase 2 §1 client ingestion: chunker, mount gating, router honesty.

The scaffold must behave exactly as the contract says before §2/§3 fill
in the Drive/formula handlers:

- ``chunk_document`` emits RetrievalEngine.ingest()-shaped dicts and
  refuses an empty tenant by name (tenant comes from the authenticated
  principal, never a client field).
- ``render_main(product_name, vertical)`` mounts the ingestion router
  only when the vertical is not in the inventory exclusion set.
- ``roles_handlers._render_main`` threads the blueprint vertical into
  ``render_main`` (the drop-the-vertical bug).
- The mounted routes resolve the tenant through ``app.tenancy`` and
  answer 401 by name for an unbound caller, 501 honestly until §2/§3.
"""

from __future__ import annotations

import sys
import types

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cerebrum_product_kernel.ingestion.chunker import chunk_document
from app.cerebrum_product_kernel.ingestion.router import router as ingestion_router
from app.factory.build.deploy import render_main
from app.factory.build.roles_handlers import _render_main


def test_chunk_document_contract_shape_and_windows():
    text = ("Paragraph one. " * 80) + "\n\n" + ("Paragraph two. " * 80)
    chunks = chunk_document(
        text, source_name="drive:notes", layer=2, tenant_id="tenant-a"
    )
    assert chunks, "non-empty text must chunk"
    for chunk in chunks:
        assert set(chunk) == {"id", "text", "layer", "tenant_id", "object_id"}
        assert len(chunk["text"]) <= 1200
        assert chunk["layer"] == 2
        assert chunk["tenant_id"] == "tenant-a"
    joined = " ".join(c["text"] for c in chunks)
    assert "Paragraph one" in joined and "Paragraph two" in joined
    ids = [c["id"] for c in chunks]
    assert ids == sorted(set(ids)), "chunk ids must be unique and ordered"


def test_chunk_document_refuses_empty_tenant_by_name():
    import pytest

    with pytest.raises(ValueError, match="authenticated principal"):
        chunk_document("text", source_name="x", layer=1, tenant_id="")
    with pytest.raises(ValueError, match="authenticated principal"):
        chunk_document("text", source_name="x", layer=1, tenant_id="   ")


def test_chunk_document_refuses_layer_out_of_range_and_empty_text():
    import pytest

    with pytest.raises(ValueError, match="layer must be one of"):
        chunk_document("text", source_name="x", layer=5, tenant_id="t")
    assert chunk_document("", source_name="x", layer=1, tenant_id="t") == []
    assert chunk_document("   \n\n  ", source_name="x", layer=1, tenant_id="t") == []


def test_render_main_mounts_ingestion_only_outside_exclusion_set():
    excluded = render_main("Warehouse Platform", vertical="medical")
    assert "product_ingestion_router" not in excluded
    assert "exclusion set" in excluded

    mounted = render_main("Warehouse Platform", vertical="retail")
    assert "product_ingestion_router" in mounted
    assert "app.include_router(product_ingestion_router)" in mounted
    # The guard keeps a generated product bootable when the kernel
    # ingestion package is not vendored.
    assert "except ImportError" in mounted


def test_render_main_threads_vertical_into_render_main():
    """The _render_main wrapper must not drop the blueprint vertical."""
    from app.factory.inventory import vertical_is_excluded

    retail = _render_main("Retail Platform", "retail")
    assert "product_ingestion_router" in retail

    medical = _render_main("Clinic Platform", "medical")
    assert "product_ingestion_router" not in medical
    assert vertical_is_excluded("medical")
    # Sanity: the exclusion set is exactly the content-less verticals.
    assert vertical_is_excluded("retail") is False


def _stub_tenancy(monkeypatch, token_to_tenant):
    """Stub the product's single tenancy path (app.tenancy) for the router."""
    mod = types.ModuleType("app.tenancy")

    class TenantRefused(Exception):
        pass

    class _Tenant:
        def __init__(self, tenant_id):
            self.tenant_id = tenant_id
            self.roles = []

    def resolve_tenant(headers):
        auth = (headers or {}).get("authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        tid = token_to_tenant.get(token)
        if tid is None:
            raise TenantRefused(token)
        return _Tenant(tid)

    mod.TenantRefused = TenantRefused
    mod.resolve_tenant = resolve_tenant
    monkeypatch.setitem(sys.modules, "app.tenancy", mod)
    return mod


def test_router_401s_unbound_caller_by_name(monkeypatch):
    _stub_tenancy(monkeypatch, {"good-token": "tenant-a"})
    app = FastAPI()
    app.include_router(ingestion_router)
    client = TestClient(app)

    resp = client.post(
        "/v1/product/ingestion/upload",
        headers={"Authorization": "Bearer unknown-token"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "authentication_required"

    no_header = client.post("/v1/product/ingestion/upload")
    assert no_header.status_code == 401
    assert no_header.json()["detail"] == "authentication_required"


def test_router_501s_honestly_until_drive_and_formulas_land(monkeypatch):
    _stub_tenancy(monkeypatch, {"good-token": "tenant-a"})
    app = FastAPI()
    app.include_router(ingestion_router)
    client = TestClient(app)
    headers = {"Authorization": "Bearer good-token"}

    for method, path in (
        ("post", "/v1/product/ingestion/upload"),
        ("post", "/v1/product/ingestion/formulas"),
        ("get", "/v1/product/ingestion/formulas"),
        ("get", "/v1/product/ingestion/drive/files"),
    ):
        resp = getattr(client, method)(path, headers=headers)
        assert resp.status_code == 501, (method, path, resp.status_code)
        assert resp.json()["detail"] == (
            "phase2_ingestion_scaffold_not_implemented"
        ), (method, path)
