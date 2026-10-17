"""The HTTP surface answers, and what it answers has the right shape."""

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["ok"] is True
    names = {item["name"] for item in body["checks"]}
    assert {"process", "persistent_disk", "database", "migrations"} <= names
    assert all(item["ok"] for item in body["checks"])


def test_kernel_jobs_roster():
    """GET /v1/jobs publishes every kernel JD; distinctive routes answer."""
    resp = client.get("/v1/jobs")
    assert resp.status_code == 200
    jobs = resp.json()["jobs"]
    by_kernel = {j["kernel"]: j for j in jobs}
    assert set(by_kernel) == {
        "COLLECTOR", "CLONER", "WRITER", "TESTER", "STORE_MANAGER"
    }
    assert by_kernel["COLLECTOR"]["title"] == "Binding surveyor"
    assert by_kernel["CLONER"]["title"] == "Block stocker"
    assert by_kernel["WRITER"]["title"] == "Platform manufacturer"
    assert by_kernel["TESTER"]["title"] == "Acceptance inspector"
    assert by_kernel["STORE_MANAGER"]["title"] == "Store registrar"
    for job in jobs:
        assert job["mandate"] and job["http_routes"] and job["agent"]
    catalog = client.get("/v1/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["kernel"] == "COLLECTOR"
    inventory = client.get("/v1/inventory")
    assert inventory.status_code == 200
    assert inventory.json()["kernel"] == "CLONER"
    assert "lock" in inventory.json()
    caps_resp = client.get("/v1/capabilities")
    assert caps_resp.status_code == 200
    assert isinstance(caps_resp.json()["items"], list)
    gates = client.get("/v1/gates")
    assert gates.status_code == 200
    assert gates.json()["kernel"] == "TESTER"
    assert gates.json()["runs_over_http"] is False
    prov = client.get("/v1/provenance")
    assert prov.status_code == 200
    assert prov.json()["kernel"] == "STORE_MANAGER"


def test_every_capability_route_answers():
    """Code-phase: each capability POST answers HTTP 200 JSON.
    Store ok: False is allowed here — acceptance is the pilot test."""
    failures = []
    payload = {'reference': 'sample', 'status': 'open', 'quantity': 0}
    resp = client.post("/v1/analytics_surface", json=payload)
    if resp.status_code != 200:
        failures.append('analytics_surface: HTTP ' + str(resp.status_code) + ': ' + resp.text[:200])
    else:
        try:
            body = resp.json()
        except Exception:
            failures.append('analytics_surface: response is not JSON')
        else:
            if not isinstance(body, dict):
                failures.append('analytics_surface: JSON body is not a dict')
            listed = client.get("/v1/analytics_surface")
            if listed.status_code != 200:
                failures.append('analytics_surface list: HTTP ' + str(listed.status_code))

    payload = {'reference': 'sample', 'status': 'open', 'quantity': 0}
    resp = client.post("/v1/dashboard_surface", json=payload)
    if resp.status_code != 200:
        failures.append('dashboard_surface: HTTP ' + str(resp.status_code) + ': ' + resp.text[:200])
    else:
        try:
            body = resp.json()
        except Exception:
            failures.append('dashboard_surface: response is not JSON')
        else:
            if not isinstance(body, dict):
                failures.append('dashboard_surface: JSON body is not a dict')
            listed = client.get("/v1/dashboard_surface")
            if listed.status_code != 200:
                failures.append('dashboard_surface list: HTTP ' + str(listed.status_code))

    assert not failures, "; ".join(failures)


@pytest.mark.pilot
def test_every_capability_route_accepts_payload():
    """Pilot: spec payload is accepted (ok is not False) and persisted.
    Not the factory code-phase gate."""
    failures = []
    payload = {'reference': 'sample', 'status': 'open', 'quantity': 0}
    resp = client.post("/v1/analytics_surface", json=payload)
    if resp.status_code != 200:
        failures.append('analytics_surface: HTTP ' + str(resp.status_code) + ': ' + resp.text[:200])
    elif resp.json().get("ok") is False:
        failures.append('analytics_surface rejected a payload built from its own schema: ' + str(resp.json().get('error')))
    else:
        listed = client.get("/v1/analytics_surface")
        if listed.status_code != 200:
            failures.append('analytics_surface list: HTTP ' + str(listed.status_code))
        elif not listed.json()["items"]:
            failures.append('analytics_surface accepted a record but persisted nothing')
        else:
            item_id = listed.json()["items"][0]["id"]
            got = client.get(f"/v1/analytics_surface/{item_id}")
            if got.status_code != 200:
                failures.append('analytics_surface get: HTTP ' + str(got.status_code))
            missing = client.get("/v1/analytics_surface/999999")
            if missing.status_code != 404:
                failures.append('analytics_surface missing id: HTTP ' + str(missing.status_code) + ' (expected 404)')

    payload = {'reference': 'sample', 'status': 'open', 'quantity': 0}
    resp = client.post("/v1/dashboard_surface", json=payload)
    if resp.status_code != 200:
        failures.append('dashboard_surface: HTTP ' + str(resp.status_code) + ': ' + resp.text[:200])
    elif resp.json().get("ok") is False:
        failures.append('dashboard_surface rejected a payload built from its own schema: ' + str(resp.json().get('error')))
    else:
        listed = client.get("/v1/dashboard_surface")
        if listed.status_code != 200:
            failures.append('dashboard_surface list: HTTP ' + str(listed.status_code))
        elif not listed.json()["items"]:
            failures.append('dashboard_surface accepted a record but persisted nothing')
        else:
            item_id = listed.json()["items"][0]["id"]
            got = client.get(f"/v1/dashboard_surface/{item_id}")
            if got.status_code != 200:
                failures.append('dashboard_surface get: HTTP ' + str(got.status_code))
            missing = client.get("/v1/dashboard_surface/999999")
            if missing.status_code != 404:
                failures.append('dashboard_surface missing id: HTTP ' + str(missing.status_code) + ' (expected 404)')

    assert not failures, "; ".join(failures)
