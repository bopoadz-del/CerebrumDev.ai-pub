"""Production HTTP surface: no public OpenAPI, baseline security headers."""

from __future__ import annotations

import shutil

import pytest

import app.main as main


def test_openapi_docs_enabled_defaults_off_in_production(monkeypatch):
    monkeypatch.delenv("OPENAPI_DOCS_ENABLED", raising=False)
    monkeypatch.setenv("ENV", "production")
    assert main.openapi_docs_enabled() is False
    monkeypatch.setenv("ENV", "prod")
    assert main.openapi_docs_enabled() is False


def test_openapi_docs_enabled_in_dev_and_test(monkeypatch):
    monkeypatch.delenv("OPENAPI_DOCS_ENABLED", raising=False)
    monkeypatch.setenv("ENV", "test")
    assert main.openapi_docs_enabled() is True
    monkeypatch.setenv("ENV", "development")
    assert main.openapi_docs_enabled() is True


def test_openapi_docs_explicit_override(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", "1")
    assert main.openapi_docs_enabled() is True
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("OPENAPI_DOCS_ENABLED", "0")
    assert main.openapi_docs_enabled() is False


def test_production_env_404s_docs_redoc_and_openapi(client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("OPENAPI_DOCS_ENABLED", raising=False)
    for path in ("/docs", "/redoc", "/openapi.json"):
        res = client.get(path)
        assert res.status_code == 404, path
        assert res.json()["detail"] == "Not Found"


def test_health_carries_baseline_security_headers(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["Referrer-Policy"] == "no-referrer"
    assert "camera=()" in res.headers["Permissions-Policy"]


def test_production_env_sets_hsts(client, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    res = client.get("/health")
    assert res.headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )


def test_stateless_factory_product_surface_is_gone(client):
    """Floor is the only product HTTP surface. Unused /v1/factory/product/* 404s."""
    posts = (
        "/v1/factory/product/draft",
        "/v1/factory/product/plan",
        "/v1/factory/product/generate",
    )
    for path in posts:
        res = client.post(path, json={})
        assert res.status_code == 404, (path, res.status_code, res.text)
    res = client.get("/v1/factory/product/golden/steward")
    assert res.status_code == 404, res.text


def _frontend_security_headers():
    import json
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2] / "frontend" / "security-headers.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    return path, data


def test_render_yaml_starts_the_header_capable_frontend_server():
    """Blueprint must point at serve.mjs — a static headers: block never hit the wire."""
    import yaml

    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    render = yaml.safe_load((root / "render.yaml").read_text(encoding="utf-8"))
    frontend = [s for s in render["services"] if s.get("name") == "cerebrumdev-frontend"]
    assert frontend, "cerebrumdev-frontend missing from render.yaml"
    svc = frontend[0]
    assert svc.get("type") == "web", svc.get("type")
    assert svc.get("runtime") == "node", svc.get("runtime")
    assert svc.get("rootDir") == "frontend"
    assert svc.get("startCommand") == "node serve.mjs"
    assert not svc.get("headers"), (
        "web services emit headers from serve.mjs; a Blueprint headers: "
        "block is the static-site path that production never applied"
    )
    serve = (root / "frontend" / "serve.mjs").read_text(encoding="utf-8")
    assert "security-headers.json" in serve
    assert "0.0.0.0" in serve


def test_frontend_security_header_declarations_match_the_serving_path():
    """_headers / JSON / smoke names stay twins; serve.mjs is what is served."""
    from pathlib import Path

    _path, headers = _frontend_security_headers()
    required = {
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Content-Security-Policy",
        "Strict-Transport-Security",
    }
    assert required <= set(headers)
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "https://api.cerebrum-dev.com" in csp
    assert "https://*.ingest.sentry.io" in csp
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Strict-Transport-Security"] == (
        "max-age=31536000; includeSubDomains"
    )

    text = (
        Path(__file__).resolve().parents[2] / "frontend" / "public" / "_headers"
    ).read_text(encoding="utf-8")
    for name, value in headers.items():
        assert name in text
        assert value in text


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node is required to exercise frontend/serve.mjs",
)
def test_serve_mjs_emits_all_five_security_headers_on_the_wire(tmp_path):
    """Lock the process Render would start, not a file the static runtime ignored."""
    import os
    import socket
    import subprocess
    import time
    import urllib.error
    import urllib.request

    _path, expected = _frontend_security_headers()
    root = _path.parent
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>ok</title>", encoding="utf-8"
    )

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    env = {
        **os.environ,
        "HOST": "127.0.0.1",
        "PORT": str(port),
        "STATIC_ROOT": str(tmp_path),
    }
    proc = subprocess.Popen(
        ["node", str(root / "serve.mjs")],
        cwd=str(root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # Startup time is not what this test measures: a loaded CI runner
        # took >8 s to start node (run 37727662031). A dead server still fails
        # fast below via proc.poll().
        deadline = time.time() + 30
        last_exc = None
        while time.time() < deadline:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://127.0.0.1:{port}/", method="HEAD"
                    ),
                    timeout=1,
                )
                break
            except Exception as exc:  # noqa: BLE001 — wait for listen
                last_exc = exc
                if proc.poll() is not None:
                    out, err = proc.communicate(timeout=2)
                    raise AssertionError(
                        f"serve.mjs exited {proc.returncode}: "
                        f"{err.decode() or out.decode()}"
                    ) from exc
                time.sleep(0.05)
        else:
            raise AssertionError(f"serve.mjs did not listen: {last_exc}")

        for method in ("HEAD", "GET"):
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/", method=method
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                got = {k.lower(): v for k, v in resp.headers.items()}
                assert resp.status == 200
            for name, value in expected.items():
                assert got[name.lower()] == value, (method, name, got)

        spa = urllib.request.Request(
            f"http://127.0.0.1:{port}/floor/session", method="GET"
        )
        with urllib.request.urlopen(spa, timeout=5) as resp:
            spa_got = {k.lower(): v for k, v in resp.headers.items()}
            assert resp.status == 200
        assert "content-security-policy" in spa_got

        missing = urllib.request.Request(
            f"http://127.0.0.1:{port}/missing.js", method="GET"
        )
        try:
            urllib.request.urlopen(missing, timeout=5)
            raise AssertionError("missing.js should 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            err_got = {k.lower(): v for k, v in exc.headers.items()}
            for name, value in expected.items():
                assert err_got[name.lower()] == value, (name, err_got)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


# ── CSP on the API origin (2026-09-10) ────────────────────────────────────
#
# Measured against live api.cerebrum-dev.com: HSTS, X-Frame-Options,
# Referrer-Policy and nosniff were present; Content-Security-Policy was not.
# The frontend has carried a CSP in _headers/render.yaml for some time and
# this origin never did.


def test_api_responses_carry_an_enforcing_csp(client):
    res = client.get("/health")
    csp = res.headers["Content-Security-Policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "Content-Security-Policy-Report-Only" not in res.headers, (
        "an API has no sources to discover; report-only would be theatre"
    )


def test_error_responses_carry_the_csp_too(client):
    """A refusal is the response an unauthenticated prober sees most.

    The exact code is 401 or 403 depending on how the environment resolves
    a principal, and this test is not about which -- only that a refused
    request is still a hardened response.
    """
    res = client.get("/v1/billing/status")
    assert res.status_code in (401, 403), res.text
    assert "default-src 'none'" in res.headers["Content-Security-Policy"]


def test_docs_page_gets_a_policy_that_lets_swagger_load(client, monkeypatch):
    """default-src 'none' would leave /docs a blank page.

    The docs page pulls swagger-ui from a CDN. It is served only when
    openapi_docs_enabled(), i.e. never in a production-like ENV, but when it
    is served it has to work.
    """
    monkeypatch.setenv("ENV", "test")
    res = client.get("/docs")
    assert res.status_code == 200
    csp = res.headers["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" in csp
    assert "default-src 'none'" not in csp


def test_the_smoke_twin_reads_the_wire_not_a_file():
    """The frontend headers were declared in three files and served in none.

    Both existing twins assert file contents; this one asserts the live
    response, which is the only place the difference showed up.
    """
    import importlib.util
    from pathlib import Path

    # Repo-root scripts/, not backend/scripts/ (which is the importable one).
    script = Path(__file__).resolve().parents[2] / "scripts" / "post_deploy_smoke.py"
    assert script.is_file(), script
    spec = importlib.util.spec_from_file_location("_smoke_probe", script)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)

    assert "content-security-policy" in smoke.API_REQUIRED_HEADERS
    assert "strict-transport-security" in smoke.FRONTEND_REQUIRED_HEADERS
    assert callable(smoke.record_security_headers)
