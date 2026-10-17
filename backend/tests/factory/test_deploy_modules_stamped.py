"""Factory-owned deploy modules are stamped, and the stamped tests read their
shape from the same definition.

Siblings of the kernel-roster fault (#678): on the CodeWhale path
``backfill_deploy_substrate`` keeps any app/health.py / app/observe.py /
app/revision.py that provides the imported NAMES. A copy with those names and
another response shape passed that check, and the Factory's own stamped tests
-- test_routes.py::test_health and tests/test_deploy.py -- then died on a
KeyError the writer cannot fix (Factory data).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from app.factory.build.deploy import (
    FACTORY_OWNED_DEPLOY_MODULES,
    HEALTH_CHECK_NAMES,
    HEALTH_STATUS_NOT_READY,
    HEALTH_STATUS_OK,
    backfill_deploy_substrate,
    deploy_substrate,
    render_health,
    render_health_route_test,
    render_product_tests,
    stamp_factory_deploy_modules,
)

# The CodeWhale-path shape the name check let through: every name the stamped
# suite imports, a different body.
AGENT_HEALTH = '''from fastapi.responses import JSONResponse


def evaluate_health():
    return 200, {"healthy": True, "components": {"db": "up"}}


def health_response():
    code, body = evaluate_health()
    return JSONResponse(status_code=code, content=body)
'''


class _Workspace:
    def __init__(self, root: Path):
        self.workspace = root

    def exists(self, rel) -> bool:
        return (self.workspace / rel).exists()

    def read_text(self, rel) -> str:
        return (self.workspace / rel).read_text(encoding="utf-8")

    def write_text(self, rel, text):
        path = self.workspace / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


class _Resp:
    def __init__(self, code, body):
        self.status_code = code
        self._body = body

    def json(self):
        return self._body


def _run_route_test(evaluate) -> None:
    """Run the emitted test_routes.py::test_health against a /health that
    returns what ``evaluate()`` returns (as app/main.py's route does)."""
    code, body = evaluate()
    client = SimpleNamespace(get=lambda path, **_: _Resp(code, body))
    scope = {"client": client}
    exec(compile("\n".join(render_health_route_test()), "test_routes.py", "exec"), scope)
    scope["test_health"]()


def _healthy_body() -> tuple:
    return 200, {
        "ok": True,
        "status": HEALTH_STATUS_OK,
        "checks": [{"name": n, "ok": True, "detail": ""} for n in HEALTH_CHECK_NAMES],
        "revision": "rev-n",
        "mark": "baseline",
    }


# --- sibling 1: test_routes.py::test_health ---------------------------------


def test_the_live_name_only_health_fails_the_emitted_route_test():
    ns = {}
    exec(compile(AGENT_HEALTH, "health.py", "exec"), ns)
    try:
        _run_route_test(ns["evaluate_health"])
    except (KeyError, AssertionError):
        return
    raise AssertionError("a name-only health copy must fail the emitted route test")


def test_the_factory_shape_passes_the_emitted_route_test():
    _run_route_test(_healthy_body)


def test_a_health_missing_a_declared_check_still_fails():
    code, body = _healthy_body()
    body["checks"] = [c for c in body["checks"] if c["name"] != HEALTH_CHECK_NAMES[2]]
    try:
        _run_route_test(lambda: (code, body))
    except AssertionError:
        return
    raise AssertionError("a body missing a declared check must fail")


def test_the_shape_has_one_definition():
    # render_health emits every declared check name and both statuses ...
    src = render_health()
    for name in HEALTH_CHECK_NAMES:
        assert json.dumps(name) in src
    assert json.dumps(HEALTH_STATUS_OK) in src and json.dumps(HEALTH_STATUS_NOT_READY) in src
    # ... and both stamped suites assert them.
    route = "\n".join(render_health_route_test())
    deploy_tests = render_product_tests({})
    for text in (route, deploy_tests):
        assert repr(list(HEALTH_CHECK_NAMES)) in text
        assert repr(HEALTH_STATUS_OK) in text


# --- sibling 2: tests/test_deploy.py (health/observe/revision) --------------


def test_backfill_alone_keeps_the_name_only_copy(tmp_path):
    # Control: the pre-fix CodeWhale path keeps the live shape.
    ws = _Workspace(tmp_path)
    ws.write_text("app/health.py", AGENT_HEALTH)
    backfill_deploy_substrate(ws)
    assert ws.read_text("app/health.py") == AGENT_HEALTH


def test_the_codewhale_stamp_replaces_every_factory_owned_module(tmp_path):
    ws = _Workspace(tmp_path)
    ws.write_text("app/health.py", AGENT_HEALTH)
    ws.write_text("app/observe.py", "def JsonFormatter():\n    pass\nREQUEST_ID_HEADER = 'x'\ndef strip_emoji(t):\n    return t\n")
    changed = stamp_factory_deploy_modules(ws)
    canonical = dict(deploy_substrate())
    assert set(changed) == set(FACTORY_OWNED_DEPLOY_MODULES)
    for rel in FACTORY_OWNED_DEPLOY_MODULES:
        assert ws.read_text(rel) == canonical[rel]
    # Idempotent: a canonical tree is left alone.
    assert stamp_factory_deploy_modules(ws) == []


def test_the_stamp_leaves_non_owned_substrate_to_the_backfill(tmp_path):
    ws = _Workspace(tmp_path)
    ws.write_text("scripts/rollback.sh", "#!/bin/sh\necho mine\n")
    stamp_factory_deploy_modules(ws)
    assert ws.read_text("scripts/rollback.sh") == "#!/bin/sh\necho mine\n"


def test_stamped_health_module_satisfies_the_deploy_suite_shape(tmp_path, monkeypatch):
    # The canonical module, executed: a missing disk is 503 / not_ready with
    # the declared persistent-disk check false -- what test_deploy.py asserts.
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "missing-disk"))
    import sys

    monkeypatch.setitem(
        sys.modules,
        "app.revision",
        SimpleNamespace(current_app_mark=lambda: "baseline", current_app_revision=lambda: "rev-n"),
    )
    ns: dict = {}
    exec(compile(render_health(), "health.py", "exec"), ns)
    code, body = ns["evaluate_health"]()
    assert code == 503
    assert body["ok"] is False and body["status"] == HEALTH_STATUS_NOT_READY
    names = {item["name"]: item for item in body["checks"]}
    assert set(names) == set(HEALTH_CHECK_NAMES)
    assert names[HEALTH_CHECK_NAMES[1]]["ok"] is False
