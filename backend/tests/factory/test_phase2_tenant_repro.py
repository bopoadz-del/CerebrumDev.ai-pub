"""Phase 2 §0.2 — tenant isolation, fixed and locked.

Builds a product with the fixed emitter (stubbed coder) and asserts:

1. Tenancy is emitter-guaranteed (app/tenancy.py exists).
2. The kernel bridge no longer hardcodes anonymous/local.
3. LIVE exchange in a fresh interpreter with the workspace as cwd:
   tenant A writes, tenant B reads → HTTP 404, never tenant A's row
   (404-not-403: cross-tenant access never leaks existence).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.runner import BuildBudget, RoleRunner

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"

_EXCHANGE = (
    "import json, os, sys\n"
    "from fastapi.testclient import TestClient\n"
    "from app.main import app as product_app\n"
    "from app.models import MODELS\n"
    "cap = sorted(MODELS)[0]\n"
    "cls = MODELS[cap]\n"
    "constraints = getattr(cls, 'CONSTRAINTS', {}) or {}\n"
    "payload = {}\n"
    "for field in getattr(cls, 'FIELDS', []) or []:\n"
    "    rules = constraints.get(field) or {}\n"
    "    if rules.get('allowed_values'):\n"
    "        payload[field] = rules['allowed_values'][0]\n"
    "    elif rules.get('required'):\n"
    "        payload[field] = 'sample'\n"
    "client = TestClient(product_app)\n"
    "from app import store\n"
    "with client:\n"
    "    # Persist as tenant A through the store layer (the route's own\n"
    "    # write path is the writer-authored handler; the stub writer does\n"
    "    # not persist). The doctrine under test is the READ surface.\n"
    "    saved = store.save(cap, payload, tenant_id='tenant-a')\n"
    "    row_id = saved['id']\n"
    "    read = client.get('/v1/%s/%s' % (cap, row_id),\n"
    "                      headers={'Authorization': 'Bearer token-b'})\n"
    "    own = client.get('/v1/%s/%s' % (cap, row_id),\n"
    "                     headers={'Authorization': 'Bearer token-a'})\n"
    "print(json.dumps({'created': 200,\n"
    "                  'row_id': row_id,\n"
    "                  'read': read.status_code,\n"
    "                  'own': own.status_code,\n"
    "                  'read_body': read.text[:200]}))\n"
)


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


def test_emitted_product_is_tenant_scoped(blueprint, tmp_path, stub_coder, monkeypatch):
    runner = RoleRunner(
        blueprint, tmp_path / "build", budget=BuildBudget(max_rework=2)
    )
    outcome = runner.run()
    assert outcome.ok, outcome.to_dict()

    ws = tmp_path / "build"
    tenancy_py = ws / "app" / "tenancy.py"
    auth_py = (ws / "app" / "auth.py").read_text(encoding="utf-8")
    bridge_py = (ws / "app" / "kernel_bridge.py").read_text(encoding="utf-8")

    # 1. Tenancy is emitter-guaranteed.
    assert tenancy_py.is_file(), "app/tenancy.py must be emitted deterministically"
    assert "def resolve_tenant" in tenancy_py.read_text(encoding="utf-8")

    # 2. Auth resolves the tenant through the single resolution path.
    assert "tenancy.resolve_tenant" in auth_py

    # 3. The kernel bridge no longer hardcodes anonymous/local.
    assert 'tenant_id="local"' not in bridge_py
    assert 'user_id="anonymous"' not in bridge_py

    # 4. LIVE cross-tenant exchange in a fresh interpreter (cwd=workspace,
    #    so `app` is the PRODUCT's package, not the factory's).
    env = dict(os.environ)
    env.update(
        {
            "PLATFORM_TOKEN": "platform-token",
            "TENANT_TOKENS": "token-a:tenant-a,token-b:tenant-b",
            "STORAGE_PATH": str(tmp_path / "product-storage"),
        }
    )
    proc = subprocess.run(
        [sys.executable, "-c", _EXCHANGE],
        cwd=str(ws),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result["row_id"] is not None, result
    assert result["read"] == 404, (
        f"tenant B must not see tenant A's record "
        f"(got {result['read']}): {result['read_body']}"
    )
    assert result["own"] == 200, result
