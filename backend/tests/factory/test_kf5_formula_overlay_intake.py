"""K-F5: client overlay sets are validated by the kernel, stored per tenant,
served over POST/GET /overlays (422 with the PrecedenceError reason on
refusal), and passed into answer_question for that tenant."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.cerebrum_product_kernel.formulas import PrecedenceError, load_base_definitions
from app.cerebrum_product_kernel.ingestion import formula_intake as fi
from app.cerebrum_product_kernel.ingestion import router as ingestion_router_mod

from tests.factory.test_phase2_client_ingestion import _stub_tenancy


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))


def _base_def():
    base = load_base_definitions()
    d = base["definitions"][0]
    return d, f"{base['set_id']}:{d['id']}_v{d['definition_version']}"


def _extend(set_id, ident):
    return {"set_id": set_id, "definitions": [
        {"id": ident, "definition_version": 1, "expression": "a / b", "inputs": ["a", "b"],
         "provenance": {"kind": "client_submitted", "reference": f"{set_id} rulebook"}}]}


def _override(set_id):
    d, addr = _base_def()
    row = dict(d, overrides=addr, reason="tenant rule differs",
               provenance={"kind": "client_submitted", "reference": f"{set_id} policy"})
    return {"set_id": set_id, "definitions": [row]}, d["id"], addr


def _shadow(set_id):
    d, _ = _base_def()
    return {"set_id": set_id, "definitions": [dict(d)]}


# ---- module ----
def test_two_tenants_store_two_different_sets(store):
    a = fi.submit_overlay("tenant-a", _extend("set-a", "metric_alpha"))
    b = fi.submit_overlay("tenant-b", _extend("set-b", "metric_beta"))
    assert a["definitions"] == ["metric_alpha"] and b["definitions"] == ["metric_beta"]
    assert [s["set_id"] for s in fi.tenant_overlays("tenant-a")] == ["set-a"]
    assert [s["set_id"] for s in fi.tenant_overlays("tenant-b")] == ["set-b"]


def test_override_with_reason_is_accepted_and_reports_the_base_address(store):
    ov, ident, addr = _override("set-o")
    out = fi.submit_overlay("tenant-a", ov)
    assert out["overrides"] == {ident: addr}
    assert fi.submit_overlay("tenant-b", _extend("set-e", "metric_gamma"))["overrides"] == {}


def test_undeclared_shadow_is_refused_and_not_stored(store):
    fi.submit_overlay("tenant-a", _extend("set-a", "metric_alpha"))
    with pytest.raises(PrecedenceError, match="overrides="):
        fi.submit_overlay("tenant-a", _shadow("set-bad"))
    assert [s["set_id"] for s in fi.tenant_overlays("tenant-a")] == ["set-a"]


def test_two_sets_claiming_one_id_conflict(store):
    fi.submit_overlay("tenant-a", _extend("set-1", "metric_shared"))
    with pytest.raises(PrecedenceError, match="defined by both"):
        fi.submit_overlay("tenant-a", _extend("set-2", "metric_shared"))
    # the same set re-submitted replaces itself instead of conflicting
    fi.submit_overlay("tenant-a", _extend("set-1", "metric_shared"))
    assert fi.list_overlays("tenant-a")["count"] == 1


@pytest.mark.parametrize("bad", [None, {}, {"set_id": " ", "definitions": [{}]},
                                 {"set_id": "s", "definitions": []}, {"set_id": "s", "definitions": ["x"]}])
def test_shape_errors(store, bad):
    with pytest.raises(fi.OverlayShapeError):
        fi.submit_overlay("tenant-a", bad)
    assert fi.tenant_overlays("tenant-a") == []


# ---- routes ----
def _client(monkeypatch):
    _stub_tenancy(monkeypatch, {"tok-a": "tenant-a", "tok-b": "tenant-b"})
    app = FastAPI()
    app.include_router(ingestion_router_mod.product_router)
    return TestClient(app)


def test_routes_post_get_and_422(store, monkeypatch):
    c = _client(monkeypatch)
    ok = c.post("/v1/product/ingestion/overlays", json=_extend("set-a", "metric_alpha"),
                headers={"Authorization": "Bearer tok-a"})
    assert ok.status_code == 200 and ok.json()["definitions"] == ["metric_alpha"]
    bad = c.post("/v1/product/ingestion/overlays", json=_shadow("set-x"),
                 headers={"Authorization": "Bearer tok-a"})
    assert bad.status_code == 422 and "overrides=" in bad.json()["detail"]
    shape = c.post("/v1/product/ingestion/overlays", json={"set_id": "s"},
                   headers={"Authorization": "Bearer tok-a"})
    assert shape.status_code == 422
    la = c.get("/v1/product/ingestion/overlays", headers={"Authorization": "Bearer tok-a"}).json()
    lb = c.get("/v1/product/ingestion/overlays", headers={"Authorization": "Bearer tok-b"}).json()
    assert (la["count"], lb["count"]) == (1, 0)
    assert c.get("/v1/product/ingestion/overlays").status_code == 401


@pytest.mark.parametrize("token,count", [("tok-a", 1), ("tok-b", 0)])
def test_get_overlays_is_never_cached(store, monkeypatch, token, count):
    """A tenant's overlay listing must not be stored by any cache: two
    tenants, two listings, both answered with Cache-Control: no-store."""
    c = _client(monkeypatch)
    c.post("/v1/product/ingestion/overlays", json=_extend("set-a", "metric_alpha"),
           headers={"Authorization": "Bearer tok-a"})
    got = c.get("/v1/product/ingestion/overlays", headers={"Authorization": f"Bearer {token}"})
    assert got.status_code == 200 and got.json()["count"] == count
    assert got.headers.get("cache-control") == "no-store"


def test_answer_question_passes_the_tenants_overlays(store, monkeypatch):
    seen = {}

    class _A:
        def to_dict(self):
            return {}

    def fake_answer_query(tenant_id, query, answer_text, *, overlays=(), **kw):
        seen[tenant_id] = [s["set_id"] for s in overlays]
        return _A()

    import app.cerebrum_product_kernel.answer as ans
    monkeypatch.setattr(ans, "answer_query", fake_answer_query)
    fi.submit_overlay("tenant-a", _extend("set-a", "metric_alpha"))
    body = ingestion_router_mod.AnswerRequest(query="q", answer_text="t")
    ingestion_router_mod.answer_question("tenant-a", body)
    ingestion_router_mod.answer_question("tenant-b", body)
    assert seen == {"tenant-a": ["set-a"], "tenant-b": []}


# ---- repair 1 (G2 M5 / M7) ----
@pytest.mark.parametrize("ids", [("s1", "s2"), ("alpha_set", "beta_set", "gamma_set")])
def test_one_tenant_keeps_every_distinct_set(store, ids):
    outs = [fi.submit_overlay("tenant-a", _extend(sid, f"metric_{sid}")) for sid in ids]
    assert [o["sets_stored"] for o in outs] == list(range(1, len(ids) + 1))
    listed = fi.list_overlays("tenant-a")
    assert [s["set_id"] for s in listed["sets"]] == list(ids)
    assert listed["count"] == len(ids)
    # resubmitting one set replaces it, the count does not grow
    assert fi.submit_overlay("tenant-a", _extend(ids[0], f"metric_{ids[0]}"))["sets_stored"] == len(ids)
