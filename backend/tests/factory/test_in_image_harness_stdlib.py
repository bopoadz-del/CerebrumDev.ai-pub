"""The acceptance harness runs INSIDE the image: standard library only.

Live 2026-10-07 (e9efdf77, build/plt_573da9231dfc4c3e): the Store gate's step
"Run scripts/acceptance.py inside the image" died before its first check with
``RuntimeError: The starlette.testclient module requires the httpx2 package``.
The harness built an in-process test client, and a test client needs an HTTP
client package the image never installs (the floor's Dockerfile installs the
runtime requirements only). The harness now speaks urllib to a running
service -- the one ACCEPTANCE_BASE_URL names, or one it starts itself under
the product's own declared server.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

from app.factory.build.store_acceptance import render_acceptance_script

#: The product's own package: the one non-stdlib root the harness may import.
PRODUCT_PACKAGE = "app"


def _import_roots(source: str):
    roots = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_in_image_harness_imports_only_the_standard_library_and_the_product():
    roots = _import_roots(render_acceptance_script())
    foreign = sorted(r for r in roots if r not in sys.stdlib_module_names and r != PRODUCT_PACKAGE)
    assert foreign == [], "scripts/acceptance.py runs in the image; it may not import %s" % foreign


# A product with no web framework at all: a raw ASGI app under uvicorn. It
# answers /health, echoes what it received, and reports the tenants bound in
# its own environment -- the harness's service is a separate process.
_APP = textwrap.dedent(
    '''
    import json
    import os


    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        status = 404 if scope["path"] == "/missing" else 200
        payload = json.dumps({
            "method": scope["method"],
            "path": scope["path"],
            "body": json.loads(body or b"null"),
            "auth": headers.get("authorization", ""),
            "tenants": os.environ.get("TENANT_TOKENS", ""),
        }).encode()
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": payload})
    '''
)

# Runs the rendered harness's own client in a process where every HTTP-client
# and test-client package is unimportable.
_DRIVER = textwrap.dedent(
    '''
    import importlib.abc
    import json
    import sys

    BLOCKED = ("httpx", "httpx2", "requests", "starlette.testclient", "fastapi.testclient")


    class _Block(importlib.abc.MetaPathFinder):
        def find_spec(self, name, path=None, target=None):
            if any(name == b or name.startswith(b + ".") for b in BLOCKED):
                raise ModuleNotFoundError("blocked for this test: " + name)
            return None


    sys.meta_path.insert(0, _Block())
    script = sys.argv[1]
    ns = {"__file__": script, "__name__": "acceptance_under_test"}
    exec(compile(open(script, encoding="utf-8").read(), script, "exec"), ns)
    http, served = ns["_client"]()
    out = {}
    try:
        try:
            health = http.request("get", "/health")
            echo = http.request("post", "/v1/thing", json={"a": 1}, headers={"Authorization": "Bearer t"})
            missing = http.request("get", "/missing")
            out = {
                "health": health.status_code,
                "echo": echo.json(),
                "missing": missing.status_code,
                "harness_tenants": ns["CHECK_TENANT_TOKENS"],
            }
        except Exception as exc:
            out = {"error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        if served is not None:
            served.__exit__(None, None, None)
    print(json.dumps(out))
    '''
)


def _product(tmp_path: Path, app_source: str) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "app" / "main.py").write_text(app_source, encoding="utf-8")
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts" / "acceptance.py"
    script.write_text(render_acceptance_script(), encoding="utf-8")
    (tmp_path / "driver.py").write_text(_DRIVER, encoding="utf-8")
    return script


def _drive(tmp_path: Path, script: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("ACCEPTANCE_BASE_URL", "TENANT_TOKENS")}
    proc = subprocess.run(
        [sys.executable, str(tmp_path / "driver.py"), str(script)],
        cwd=str(tmp_path), capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_harness_reaches_a_live_service_with_no_http_client_package(tmp_path):
    out = _drive(tmp_path, _product(tmp_path, _APP))
    assert "error" not in out, out
    assert out["health"] == 200
    assert out["missing"] == 404
    assert out["echo"]["method"] == "POST"
    assert out["echo"]["body"] == {"a": 1}
    assert out["echo"]["auth"] == "Bearer t"


def test_the_started_service_has_the_isolation_checks_tenants_bound(tmp_path):
    # The isolation check writes as one tenant and reads as another. In a
    # separate process those tenants exist only if the service starts with
    # them -- the harness's constant is the one both sides read.
    script = _product(tmp_path, _APP)
    out = _drive(tmp_path, script)
    ns: dict = {"__file__": str(script), "__name__": "acceptance_constants"}
    exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), ns)
    # The tokens are generated per run (never a fixed value in the rendered
    # file): the service carries exactly the constant of the harness that
    # started it, and each load binds the same tenants under fresh tokens.
    assert out["echo"]["tenants"] == out["harness_tenants"]
    tenants = lambda s: [pair.partition(":")[2] for pair in s.split(",")]
    assert tenants(out["harness_tenants"]) == tenants(ns["CHECK_TENANT_TOKENS"])
    assert out["harness_tenants"] != ns["CHECK_TENANT_TOKENS"]


def test_a_service_that_never_answers_fails_the_http_checks_not_the_harness(tmp_path):
    out = _drive(tmp_path, _product(tmp_path, "raise SystemExit('boot refused')\n"))
    assert "the service exited" in out.get("error", ""), out
    assert "boot refused" in out["error"]
