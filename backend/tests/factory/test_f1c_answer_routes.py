"""F-1c BAR 2: answer_query has a REAL product caller on BOTH surfaces.

The answer verb is wired symmetrically with the upload verb:

* GENERATED PRODUCT: POST /v1/product/ingestion/answer on the kernel ingestion
  router (mounted into every product).
* FACTORY FLOOR:      POST /v1/sessions/{id}/product/ingestion/answer.

Both go through ONE shared helper (``answer_question`` -> ``answer_query``). A
kernel-covered question returns the kernel's COMPUTED number (never the prose
"0.99"). MUTATION: removing either route's wiring reddens that route's test
(the POST 404s / stops returning the kernel result).
"""

from __future__ import annotations

from typing import List

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cerebrum_product_kernel.ingestion import object_store, parse_job, retrieval_index
from app.cerebrum_product_kernel.ingestion.parsers import parse_object
from app.cerebrum_product_kernel.ingestion.router import router as ingestion_router
from app.cerebrum_product_kernel.retrieval_engine import InMemoryVectorStore, RetrievalEngine

_FINANCE = {"gross", "margin", "ratio", "revenue", "cogs", "profit"}


class _FinanceEmbedder:
    semantic = True

    def __call__(self, text: str) -> List[float]:
        vec = [0.0, 0.0]
        for word in text.lower().replace(".", " ").split():
            if any(k in word for k in _FINANCE):
                vec[0] += 1.0
            else:
                vec[1] += 0.0
        norm = sum(x * x for x in vec) ** 0.5
        return [x / norm for x in vec] if norm else vec


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    engine = RetrievalEngine(embedder=_FinanceEmbedder(), store=InMemoryVectorStore())
    retrieval_index.set_engine_provider(lambda identity: engine)
    yield engine
    retrieval_index.set_engine_provider(None)


def _attach(identity):
    data = b"Gross margin inputs.\n\nRevenue is 100. COGS is 60.\n"
    stored = object_store.save_object(identity, "finance.md", data)
    job = parse_job.create_job(
        identity, object_id=stored.object_id, source_name=stored.source_name
    )
    final = parse_object(identity, stored.object_id, job_id=job.job_id)
    assert final.status is parse_job.ParseStatus.PARSED


def _stub_tenancy(monkeypatch, token_to_tenant):
    import sys
    import types

    mod = types.ModuleType("app.tenancy")

    class TenantRefused(Exception):
        pass

    class _Tenant:
        def __init__(self, tid):
            self.tenant_id = tid
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


def test_generated_product_answer_route_reaches_answer_query(eng, monkeypatch):
    """The kernel ingestion router's /answer runs answer_query for a product."""
    _stub_tenancy(monkeypatch, {"good-token": "tenant-a"})
    _attach("tenant-a")
    app = FastAPI()
    app.include_router(ingestion_router)
    client = TestClient(app)

    resp = client.post(
        "/v1/product/ingestion/answer",
        headers={"Authorization": "Bearer good-token"},
        json={
            "query": "what is the gross margin ratio",
            "answer_text": "The gross margin ratio is 0.99.",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "kernel"
    assert body["kernel_value"] == pytest.approx(0.40)
    assert "0.99" not in body["text"]

    # Unbound caller is refused by name (same guard as /upload).
    unbound = client.post(
        "/v1/product/ingestion/answer",
        headers={"Authorization": "Bearer nope"},
        json={"query": "x"},
    )
    assert unbound.status_code == 401


def test_floor_answer_route_reaches_answer_query(eng, monkeypatch):
    """The Floor's /product/ingestion/answer runs answer_query via the shared helper."""
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)

    from app.core.auth import DEV_LOCAL_TENANT
    from app.core.session_store import create_session
    from app.main import app

    _attach(DEV_LOCAL_TENANT)
    create_session("sess_answer", DEV_LOCAL_TENANT)
    client = TestClient(app)  # bare: lifespan not run, fixture provider holds

    resp = client.post(
        "/v1/sessions/sess_answer/product/ingestion/answer",
        json={
            "query": "what is the gross margin ratio",
            "answer_text": "The gross margin ratio is 0.99.",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["kind"] == "kernel"
    assert body["kernel_value"] == pytest.approx(0.40)
    assert "0.99" not in body["text"]


def test_floor_answer_route_surfaces_no_engine(tmp_path, monkeypatch):
    """With no retrieval backend, the route surfaces 503 'retrieval backend not
    configured' rather than a silent empty."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    retrieval_index.set_engine_provider(None)

    from app.core.auth import DEV_LOCAL_TENANT
    from app.core.session_store import create_session
    from app.main import app

    create_session("sess_noeng", DEV_LOCAL_TENANT)
    client = TestClient(app)
    resp = client.post(
        "/v1/sessions/sess_noeng/product/ingestion/answer",
        json={"query": "what is the gross margin ratio", "answer_text": "0.99"},
    )
    assert resp.status_code == 503, resp.text
    assert retrieval_index.RETRIEVAL_BACKEND_NOT_CONFIGURED in resp.json()["detail"]
