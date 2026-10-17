"""A generated platform's writes and work queue respect the caller's tenant.

Found auditing build sess_065fc3eac75c4f62 (FinOps), which went 13/13 in
Docker. Two defects in what every generated platform ships:

1. Update and delete never worked. Tenancy gives a caller roles
   (``("admin",)``); product_context passed them straight through as kernel
   permissions, and the update/delete specs require ``product.write``. Every
   PUT and DELETE answered "missing permissions: product.write". No Docker
   check performs a successful write-after-create, so it shipped.

2. The work queue ignored tenants. Its routes authenticated the caller and
   threw the resolved tenant away: the list returned every tenant's items,
   and process claimed any id and ran it as its owner on a stranger's
   request. cross_tenant_404 only probed capability routes.

Fixing (1) makes update/delete reachable for the first time, so this test
also attacks them across tenants -- a permission fix must not open a
cross-tenant write.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.runner import RoleRunner
from tests.factory.conftest import stub_coder_patches

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

PROBE = r'''
import json, os, sys
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient
from app.main import app
import app.domain_ops as domain

A = {"Authorization": "Bearer token-a"}
B = {"Authorization": "Bearer token-b"}
out = {}
with TestClient(app) as http:
    items = http.get("/v1/capabilities", headers=A).json().get("items") or []
    first = items[0]
    cap = first if isinstance(first, str) else (first.get("id") or first.get("capability_id"))
    sample = domain.sample_payload(cap)

    # -- CRUD -------------------------------------------------------------
    created = http.post(f"/v1/{cap}", json=sample, headers=A).json()
    rid = (created.get("stored") or {}).get("id")
    out["create_ok"] = bool(created.get("ok")) and rid is not None
    b_upd = http.put(f"/v1/{cap}/{rid}", json=dict(sample), headers=B).json()
    b_del = http.delete(f"/v1/{cap}/{rid}", headers=B).json()
    out["b_update_ok"] = bool(b_upd.get("ok"))
    out["b_delete_ok"] = bool(b_del.get("ok"))
    out["a_still_has_it"] = http.get(f"/v1/{cap}/{rid}", headers=A).status_code == 200
    a_upd = http.put(f"/v1/{cap}/{rid}", json=dict(sample), headers=A).json()
    out["a_update_ok"] = bool(a_upd.get("ok")); out["a_update_err"] = str(a_upd.get("error"))[:120]
    a_del = http.delete(f"/v1/{cap}/{rid}", headers=A).json()
    out["a_delete_ok"] = bool(a_del.get("ok")); out["a_delete_err"] = str(a_del.get("error"))[:120]

    # -- work queue -------------------------------------------------------
    made = http.post("/v1/work_queue", json={"capability_id": cap}, headers=A).json()
    item = (made.get("result") or {}).get("output", {}).get("item") or {}
    iid = item.get("id")
    out["enqueued"] = bool(made.get("ok")) and iid is not None
    out["enqueue_err"] = str(made.get("error"))[:120]
    out["item_tenant"] = item.get("tenant_id")
    out["a_sees"] = [i["id"] for i in http.get("/v1/work_queue", headers=A).json()["items"]]
    out["b_sees"] = [i["id"] for i in http.get("/v1/work_queue", headers=B).json()["items"]]
    stolen = http.post(f"/v1/work_queue/{iid}/process", headers=B).json()
    out["b_process_ok"] = bool(stolen.get("ok"))
    out["b_process_error"] = (stolen.get("result") or {}).get("error_code")
    after = {i["id"]: i for i in http.get("/v1/work_queue", headers=A).json()["items"]}
    out["status_after_b"] = (after.get(iid) or {}).get("status")
    own = http.post(f"/v1/work_queue/{iid}/process", headers=A).json()
    out["a_process_ok"] = bool(own.get("ok"))
    out["iid"] = iid
print("TENANT_PROBE=" + json.dumps(out))
'''


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    os.environ["FACTORY_CODER_ENABLED"] = "0"
    out = tmp_path_factory.mktemp("tenants") / "build"
    with stub_coder_patches():
        outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()
    storage = tmp_path_factory.mktemp("tenants-storage")
    env = {
        **os.environ,
        "TENANT_TOKENS": "token-a:tenant-a,token-b:tenant-b",
        "PLATFORM_TOKEN": "token-a",
        "STORAGE_PATH": str(storage / "data"),
        "BACKUP_DIR": str(storage / "backups"),
        "PYTHONIOENCODING": "utf-8",
    }
    proc = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=out, env=env, capture_output=True, text=True
    )
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("TENANT_PROBE=")]
    assert line, proc.stderr[-2500:]
    return json.loads(line[-1].split("=", 1)[1])


# -- CRUD ---------------------------------------------------------------------

def test_the_owner_can_update_and_delete(probe):
    assert probe["create_ok"], probe
    assert probe["a_update_ok"], probe["a_update_err"]
    assert probe["a_delete_ok"], probe["a_delete_err"]


def test_another_tenant_cannot_update_or_delete_it(probe):
    assert probe["b_update_ok"] is False, "tenant B updated tenant A's record"
    assert probe["b_delete_ok"] is False, "tenant B deleted tenant A's record"
    assert probe["a_still_has_it"], "B's attempt removed A's record"


# -- work queue ---------------------------------------------------------------

def test_an_item_is_enqueued_under_the_callers_tenant(probe):
    assert probe["enqueued"], probe["enqueue_err"]
    assert probe["item_tenant"] == "tenant-a", probe


def test_another_tenant_cannot_list_it(probe):
    assert probe["iid"] in probe["a_sees"], probe
    assert probe["iid"] not in probe["b_sees"], "tenant B listed tenant A's queue item"


def test_another_tenant_cannot_process_it(probe):
    assert probe["b_process_ok"] is False, "tenant B processed tenant A's queue item"
    assert probe["b_process_error"] == "not_found", probe
    assert probe["status_after_b"] == "pending", "B's attempt changed A's item"


def test_the_owner_can_process_it(probe):
    assert probe["a_process_ok"] is True, probe
