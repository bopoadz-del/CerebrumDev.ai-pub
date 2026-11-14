"""FG-2(b): one boot definition for grading.

Replay-harness tests: each boots a small invented product through the
grader's real ``boot_product`` (a real interpreter, a real server, a real
/health), two inputs each. Nothing here names a probe, a product or a
capability; the products are written by the test and say only what the
boot must observe.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

from app.factory.build import data_lifecycle
from app.factory.build import grader
from app.factory.build.image_sufficiency import GATE_RUN_ENV
from app.factory.build.product_grader.function_grader import harness_process_env

#: A minimal ASGI product. ``HEALTH`` decides what /health answers; any
#: other path echoes the environment the product was booted with.
_ASGI = '''
import json, os, sys
{prelude}
HEALTH = {health}


async def app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.startup":
                await send({{"type": "lifespan.startup.complete"}})
            else:
                if globals().get("HANG_ON_SHUTDOWN"):
                    import asyncio
                    await asyncio.sleep(3600)
                await send({{"type": "lifespan.shutdown.complete"}})
                return
    if scope["path"] == "/health":
        status, body = HEALTH
    else:
        status, body = 200, dict(os.environ)
    data = json.dumps(body).encode()
    await send({{"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json")]}})
    await send({{"type": "http.response.body", "body": data}})
'''


def _product(root: Path, *, health=(200, {"status": "ok"}), prelude: str = "", entrypoint: bool = False) -> Path:
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "main.py").write_text(
        _ASGI.format(prelude=prelude, health=repr(health)), encoding="utf-8"
    )
    if entrypoint:
        (root / data_lifecycle.ENTRYPOINT_PATH).parent.mkdir(parents=True)
        (root / data_lifecycle.ENTRYPOINT_PATH).write_text(data_lifecycle.render_entrypoint(), encoding="utf-8")
    return root


def _env_of(base_url: str) -> dict:
    with urllib.request.urlopen(base_url + "/env", timeout=5) as resp:
        return json.loads(resp.read())


@pytest.mark.parametrize("declared", [True, False])
def test_boot_plan_runs_the_declared_entrypoint_steps_then_serves(tmp_path, declared):
    root = _product(tmp_path / "p", entrypoint=declared)
    plan = grader.boot_plan(root, 4321)
    expected_pre = [list(s) for s in data_lifecycle.PRE_SERVE_STEPS] if declared else []
    assert plan["pre"] == expected_pre
    assert plan["serve"][0] == data_lifecycle.SERVE_TARGET
    assert plan["serve"][-2:] == ["--port", "4321"]


@pytest.mark.parametrize(
    "factory_var,factory_value",
    [("FACTORY_ONLY_SETTING_A", "secret-a"), ("DATABASE_URL", "postgresql://factory/db")],
)
def test_boot_env_is_an_allowlist_never_the_factorys_environment(tmp_path, factory_var, factory_value):
    source = {factory_var: factory_value, "PATH": "/somewhere/else"}
    storage = tmp_path / "store"
    env = grader.boot_env(tmp_path / "p", storage, 5555, source)
    assert factory_var not in env
    # the function harness's test context, then the gate token, store, port
    for key, value in {**harness_process_env(tmp_path / "p"), **GATE_RUN_ENV}.items():
        assert env[key] == value
    assert env["STORAGE_PATH"] == str(storage)
    assert env["PORT"] == "5555"


@pytest.mark.parametrize("factory_var", ["FACTORY_ONLY_SETTING_A", "FACTORY_ONLY_SETTING_B"])
def test_a_variable_set_in_the_factory_is_absent_from_the_booted_product(tmp_path, monkeypatch, factory_var):
    monkeypatch.setenv(factory_var, "must-not-reach-the-product")
    root = _product(tmp_path / "p")
    boot, url = grader.boot_product(root, ready_timeout_s=30)
    assert boot is not None, url
    try:
        seen = _env_of(url)
    finally:
        boot.stop()
    assert factory_var not in seen
    assert "DATABASE_URL" not in seen
    for key, value in GATE_RUN_ENV.items():
        assert seen[key] == value


@pytest.mark.parametrize("required", ["OPERATOR_SUPPLIED_A", "OPERATOR_SUPPLIED_B"])
def test_a_deploy_time_setting_is_stood_in_as_the_function_harness_does(tmp_path, required):
    prelude = f'_REQUIRED = os.environ["{required}"]'
    root = _product(tmp_path / "p", prelude=prelude)
    boot, url = grader.boot_product(root, ready_timeout_s=30)
    assert boot is not None, url
    try:
        seen = _env_of(url)
    finally:
        boot.stop()
    assert required in seen


@pytest.mark.parametrize("boots", [2, 3])
def test_every_boot_gets_a_fresh_scratch_that_is_removed_on_stop(tmp_path, boots):
    root = _product(tmp_path / "p")
    stores = []
    scratches = []
    for _ in range(boots):
        boot, url = grader.boot_product(root, ready_timeout_s=30)
        assert boot is not None, url
        try:
            stores.append(_env_of(url)["STORAGE_PATH"])
            scratches.append(boot.scratch)
        finally:
            boot.stop()
        assert boot.poll() is not None
        assert not boot.scratch.exists()
    assert len(set(stores)) == boots
    assert all(not str(Path(s)).startswith(str(root)) for s in stores)


@pytest.mark.parametrize("check,detail", [("schema_at_head", "behind by 1"), ("store_writable", "read-only")])
def test_a_not_ready_health_is_quoted_with_its_status_and_failing_check(tmp_path, check, detail):
    health = (503, {"status": "not_ready", "checks": [{"name": "always_fine", "ok": True},
                                                      {"name": check, "ok": False, "detail": detail}]})
    root = _product(tmp_path / "p", health=health)
    boot, reason = grader.boot_product(root, ready_timeout_s=3)
    assert boot is None
    assert "last /health: 503" in reason
    assert f"{check} ({detail})" in reason
    assert "always_fine" not in reason


@pytest.mark.parametrize("code", [3, 7])
def test_a_product_that_exits_names_its_code_and_output_tail(tmp_path, code):
    marker = f"invented-startup-failure-{code}"
    prelude = f"sys.stderr.write({marker!r} + chr(10)); sys.exit({code})"
    root = _product(tmp_path / "p", prelude=prelude)
    boot, reason = grader.boot_product(root, ready_timeout_s=30)
    assert boot is None
    assert f"product boot exited with code {code}" in reason
    assert marker in reason.split("output tail:", 1)[1]


@pytest.mark.parametrize("timeout_s", [1.0, 2.0])
def test_a_product_that_never_listens_reads_as_not_booted_and_is_stopped(tmp_path, monkeypatch, timeout_s):
    root = _product(tmp_path / "p", prelude="import time; time.sleep(120)")
    stopped = []
    real_stop = grader.GradingBoot.stop

    def _recording_stop(self):
        stopped.append(self)
        real_stop(self)

    monkeypatch.setattr(grader.GradingBoot, "stop", _recording_stop)
    boot, reason = grader.boot_product(root, ready_timeout_s=timeout_s)
    assert boot is None
    assert f"within {timeout_s:g}s" in reason
    assert "last /health: no answer" in reason
    assert len(stopped) == 1
    assert stopped[0].poll() is not None
    assert not stopped[0].scratch.exists()


@pytest.mark.parametrize("declared", [True, False])
def test_the_declared_entrypoint_step_runs_before_the_server(tmp_path, declared):
    """A product declaring the deploy entrypoint has its pre-serve step run
    first; here the step cannot succeed (no migrations in the tree), so the
    boot fails at that step exactly as the deploy would -- and the same
    product without the declaration serves."""
    root = _product(tmp_path / "p", entrypoint=declared)
    boot, outcome = grader.boot_product(root, ready_timeout_s=30)
    if declared:
        assert boot is None
        assert "product boot exited with code" in outcome
        assert "boot step" in outcome
    else:
        assert boot is not None, outcome
        boot.stop()


@pytest.mark.parametrize(
    "body,expected",
    [
        (b'{"checks": [{"name": "a", "ok": false}, {"name": "b", "ok": true}]}', ["a"]),
        (b'{"checks": [{"name": "c", "ok": "yes", "detail": "d"}]}', ["c (d)"]),
    ],
)
def test_failing_health_checks_reads_the_declared_shape(body, expected):
    assert grader.failing_health_checks(body) == expected


@pytest.mark.parametrize("body", [b"not json", b'{"checks": "nope"}'])
def test_failing_health_checks_is_empty_for_an_unreadable_body(body):
    assert grader.failing_health_checks(body) == []


@pytest.mark.parametrize("raised", [RuntimeError, KeyError])
def test_grade_ui_stops_the_boot_on_every_exit_path(monkeypatch, tmp_path, raised):
    import app.factory.build.product_grader as pg

    stops = []

    class _Boot:
        def stop(self):
            stops.append(True)

    monkeypatch.setattr(grader, "boot_product", lambda root: (_Boot(), "http://127.0.0.1:9"))

    class _UiGrader:
        @staticmethod
        def grade_ui(blueprint, workdir, *, base_url):
            raise raised("the UI walk broke")

    monkeypatch.setattr(pg, "ui_grader", _UiGrader, raising=False)
    with pytest.raises(raised):
        grader._grade_ui({}, tmp_path, tmp_path / "g", None)
    assert stops == [True]


@pytest.mark.parametrize("reason", ["product boot exited with code 4; output tail: x", "did not answer"])
def test_grade_ui_quotes_the_boot_reason_as_not_run(monkeypatch, tmp_path, reason):
    monkeypatch.setattr(grader, "boot_product", lambda root: (None, reason))
    ui, ran, not_run = grader._grade_ui({}, tmp_path, tmp_path / "g", None)
    assert ran is False
    assert not_run == f"UI run did not happen: {reason}"
    assert ui.ok is False


@pytest.mark.parametrize("grace", [0.5, 1.0])
def test_stop_kills_a_boot_that_does_not_exit_on_terminate(tmp_path, monkeypatch, grace):
    prelude = "HANG_ON_SHUTDOWN = True"
    root = _product(tmp_path / "p", prelude=prelude)
    monkeypatch.setattr(grader, "_BOOT_STOP_GRACE_S", grace)
    boot, url = grader.boot_product(root, ready_timeout_s=30)
    assert boot is not None, url
    boot.stop()
    assert boot.poll() is not None
    assert not boot.scratch.exists()
