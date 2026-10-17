"""S11: deploy / observe / rollback is a machine-gated RoleRunner property.

Health is fail-closed. Rollback is performed (start N, persist, start N+1,
roll back to N, assert prior health and prior data). LotDesk-class
always-200 health is F1 and is rejected. Structured logs carry a request
id and do not put emoji on machine-parseable stdout.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.deploy import (
    MARK_BASELINE,
    MARK_CHANGED,
    REQUEST_ID_HEADER,
    REVISION_N,
    REVISION_N_PLUS_1,
    assert_fail_closed_health_source,
    assert_no_emoji_in_machine_logs,
    contains_emoji,
    health_is_always_200,
    reject_always_200_health,
    reject_lotdesk_always_200_health,
)
from app.factory.build.lotdesk_gate import reject_lotdesk_as_shipped
from app.factory.build.runner import RoleRunner
from app.factory.coder import CoderError
from tests.factory.coder_stub_bodies import invoking_handler_body

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


def _keep_fallback_spec(**kwargs):
    # 0.5: the S11 probes persist and assert the deterministic fallback
    # schema (reference/status/quantity); raising routes the writer to it
    # (recorded as a coder failure, not fatal).
    raise CoderError("stub: keep deterministic fallback model spec")


@contextlib.contextmanager
def _stub_coder_with_fallback_specs():
    """0.5: agent-written HANDLERS satisfy writer_no_output, while model

    specs stay on the deterministic fallback so quantity round-trips.
    The shared stub_coder_patches replaces specs with a reference-only
    schema, which would silently drop the quantity column these tests
    exercise -- so this fixture stubs only the handler/readme legs.
    """
    old_env = {
        k: os.environ.get(k)
        for k in ("FACTORY_CODER_ENABLED", "FACTORY_BRIEF_DISPATCH")
    }
    os.environ["FACTORY_CODER_ENABLED"] = "1"
    os.environ["FACTORY_BRIEF_DISPATCH"] = "0"
    patchers = [
        mock.patch(
            "app.factory.build.coder_session.cli_available",
            lambda command=None: False,
        ),
        mock.patch(
            "app.factory.coder.generate_platform_handler",
            lambda **kw: {
                "body": invoking_handler_body({"run": "stable"}),
                "model": "stub-coder",
            },
        ),
        mock.patch("app.factory.coder.generate_model_spec", _keep_fallback_spec),
        mock.patch("app.factory.coder.generate_from_compiled_brief", _keep_fallback_spec),
        mock.patch(
            "app.factory.coder._llm_code_call",
            lambda messages: ("# stub readme\n", "stub-model"),
        ),
    ]
    for patcher in patchers:
        patcher.start()
    try:
        yield
    finally:
        for patcher in patchers:
            patcher.stop()
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    # Module-scoped: autouse monkeypatch has not run yet.
    os.environ["FACTORY_CODER_ENABLED"] = "0"
    out = tmp_path_factory.mktemp("s11") / "build"
    # 0.5: without a coding agent the WRITER refuses (writer_no_output);
    # stub a deterministic agent so the S11 build still goes green.
    with _stub_coder_with_fallback_specs():
        outcome = RoleRunner(load_blueprint(SMOKE), out).run()
    assert outcome.ok, outcome.to_dict()
    return out


def _probe(built: Path, storage: Path, body: str) -> dict:
    script = (
        "import json, os, sys\n"
        f"os.environ['STORAGE_PATH'] = {str(storage)!r}\n"
        f"sys.path.insert(0, {str(built)!r})\n"
        + body
        + "\nprint('S11_PROBE=' + json.dumps(result))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=built,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    line = [ln for ln in proc.stdout.splitlines() if ln.startswith("S11_PROBE=")]
    assert line, proc.stdout + proc.stderr
    return json.loads(line[-1].split("=", 1)[1])


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _http_json(url: str, headers: dict | None = None, timeout: float = 2.0):
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8")), dict(exc.headers)


def _start_revision(
    built: Path,
    storage: Path,
    *,
    revision: str,
    mark: str | None,
    port: int,
) -> subprocess.Popen:
    env = os.environ.copy()
    env["STORAGE_PATH"] = str(storage)
    env["APP_REVISION"] = revision
    if mark is None:
        env.pop("APP_MARK", None)
    else:
        env["APP_MARK"] = mark
    env["PYTHONPATH"] = str(built)
    env["PYTHONUNBUFFERED"] = "1"
    env["PORT"] = str(port)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=built,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _wait_health(port: int, proc: subprocess.Popen | None = None, timeout: float = 25.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise AssertionError(
                f"uvicorn exited {proc.returncode} before health on {port}: {last}\n{out}"
            )
        try:
            return _http_json(f"http://127.0.0.1:{port}/health", timeout=1.0)
        except Exception as exc:  # noqa: BLE001 — wait loop
            last = exc
            time.sleep(0.1)
    raise AssertionError(f"health did not answer on {port}: {last}")


def _stop(proc: subprocess.Popen) -> str:
    if proc.poll() is None:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate(timeout=4)
    else:
        out, _ = proc.communicate(timeout=2)
    return out or ""


def test_role_runner_emits_fail_closed_health_rollback_and_logs(built):
    main = (built / "app" / "main.py").read_text(encoding="utf-8")
    assert_fail_closed_health_source(main)
    assert health_is_always_200(main) is False
    assert reject_always_200_health(main)["ok"] is True
    health = (built / "app" / "health.py").read_text(encoding="utf-8")
    assert "evaluate_health" in health
    assert "persistent_disk" in health
    assert "migrations" in health
    assert "JSONResponse" in health
    observe = (built / "app" / "observe.py").read_text(encoding="utf-8")
    assert REQUEST_ID_HEADER in observe
    assert "JsonFormatter" in observe
    assert "strip_emoji" in observe
    revision = (built / "app" / "revision.py").read_text(encoding="utf-8")
    assert REVISION_N in revision
    assert MARK_BASELINE in revision
    rollback = (built / "scripts" / "rollback.sh").read_text(encoding="utf-8")
    assert "STORAGE_PATH" in rollback
    assert "deploy_revision" in rollback
    entry = (built / "scripts" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "alembic upgrade head" in entry
    assert "uvicorn" in entry
    doc = json.loads((built / "docs" / "deploy.json").read_text(encoding="utf-8"))
    assert doc["health"]["fail_closed"] is True
    assert doc["health"]["unconditional_ok_is"] == "F1"
    assert doc["rollback"]["performed"] is True
    assert "SPOF" in doc["spof"]
    assert doc["capacity"]["ha"] is False
    assert doc["capacity"]["replicas"] == 0
    assert (built / "tests" / "test_deploy.py").is_file()
    assert "PILOT_READY" not in (built / "docs" / "deploy.json").read_text(
        encoding="utf-8"
    )


def test_always_200_health_is_f1_and_lotdesk_is_rejected(built):
    stub = (
        "from fastapi import FastAPI\n"
        "app = FastAPI()\n"
        '@app.get("/health")\n'
        "def health():\n"
        '    return {"status": "ok"}\n'
    )
    rejected = reject_always_200_health(stub)
    assert rejected["ok"] is False
    assert "F1" in rejected["codes"]
    lotdesk = reject_lotdesk_always_200_health()
    assert lotdesk["ok"] is False
    assert lotdesk["f1_present"] is True
    assert lotdesk["lotdesk"] == "fixture only; not patched"
    shipped = reject_lotdesk_as_shipped()
    assert "F1" in shipped["codes"]
    main = (built / "app" / "main.py").read_text(encoding="utf-8")
    assert reject_always_200_health(main)["ok"] is True


def test_health_fails_when_app_is_down():
    port = _free_port()
    with pytest.raises((urllib.error.URLError, ConnectionError, OSError, TimeoutError)):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)


def test_health_fails_when_disk_or_migrations_missing(built, tmp_path):
    missing = _probe(
        built,
        tmp_path / "no-such-disk",
        "\n".join(
            [
                "from app.health import evaluate_health",
                "code, body = evaluate_health()",
                "result = {'code': code, 'ok': body['ok'], 'checks': body['checks']}",
            ]
        ),
    )
    assert missing["code"] == 503
    assert missing["ok"] is False
    names = {item["name"]: item for item in missing["checks"]}
    assert names["persistent_disk"]["ok"] is False

    empty = tmp_path / "empty-disk"
    empty.mkdir()
    unmigrated = _probe(
        built,
        empty,
        "\n".join(
            [
                "from app.health import evaluate_health",
                "code, body = evaluate_health()",
                "result = {'code': code, 'ok': body['ok'], 'checks': body['checks']}",
            ]
        ),
    )
    assert unmigrated["code"] == 503
    assert unmigrated["ok"] is False
    names = {item["name"]: item for item in unmigrated["checks"]}
    assert names["database"]["ok"] is False or names["migrations"]["ok"] is False


def test_health_is_200_when_process_disk_db_and_head_are_present(built, tmp_path):
    storage = tmp_path / "ready"
    storage.mkdir()
    result = _probe(
        built,
        storage,
        "\n".join(
            [
                "from app.health import evaluate_health",
                "from app.migrations import upgrade_head",
                "upgrade_head()",
                "code, body = evaluate_health()",
                "result = {'code': code, 'ok': body['ok'], 'status': body['status'], "
                "'checks': body['checks'], 'revision': body['revision']}",
            ]
        ),
    )
    assert result["code"] == 200
    assert result["ok"] is True
    assert result["status"] == "ok"
    assert all(item["ok"] for item in result["checks"])


def test_rollback_drill_is_performed(built, tmp_path):
    """Start N, persist, start N+1, roll back to N, assert prior health+data."""
    storage = tmp_path / "disk"
    storage.mkdir()
    revision_py = built / "app" / "revision.py"
    original = revision_py.read_text(encoding="utf-8")
    row = None
    proc = None
    logs = ""
    try:
        port = _free_port()
        proc = _start_revision(
            built, storage, revision=REVISION_N, mark=None, port=port
        )
        status, body, _headers = _wait_health(port, proc)
        assert status == 200, body
        assert body["ok"] is True
        assert body["revision"] == REVISION_N
        assert body["mark"] == MARK_BASELINE
        row = _probe(
            built,
            storage,
            "\n".join(
                [
                    "from app import store",
                    "saved = store.save('analytics_surface', "
                    "{'reference': 's11-keep', 'status': 'open', 'quantity': 7}, tenant_id='local')",
                    "result = saved",
                ]
            ),
        )
        assert row["id"] is not None
        assert row["reference"] == "s11-keep"
        logs += _stop(proc)
        proc = None

        mutated = original.replace(
            f'MARK = "{MARK_BASELINE}"', f'MARK = "{MARK_CHANGED}"'
        )
        assert mutated != original
        revision_py.write_text(mutated, encoding="utf-8")

        port = _free_port()
        proc = _start_revision(
            built, storage, revision=REVISION_N_PLUS_1, mark=None, port=port
        )
        status, body, _headers = _wait_health(port, proc)
        assert status == 200, body
        assert body["ok"] is True
        assert body["revision"] == REVISION_N_PLUS_1
        assert body["mark"] == MARK_CHANGED
        still = _probe(
            built,
            storage,
            "\n".join(
                [
                    "from app import store",
                    f"fetched = store.get('analytics_surface', {row['id']!r}, tenant_id='local')",
                    "result = fetched",
                ]
            ),
        )
        assert still is not None
        assert still["reference"] == "s11-keep"
        logs += _stop(proc)
        proc = None

        revision_py.write_text(original, encoding="utf-8")
        rolled = subprocess.run(
            ["sh", str(built / "scripts" / "rollback.sh"), REVISION_N],
            cwd=built,
            env={**os.environ, "STORAGE_PATH": str(storage)},
            capture_output=True,
            text=True,
            check=False,
        )
        assert rolled.returncode == 0, rolled.stdout + rolled.stderr
        assert "rollback.performed" in rolled.stdout
        lock = (storage / "deploy_revision").read_text(encoding="utf-8").strip()
        assert lock == REVISION_N

        port = _free_port()
        proc = _start_revision(
            built, storage, revision=REVISION_N, mark=None, port=port
        )
        status, body, _headers = _wait_health(port, proc)
        assert status == 200, body
        assert body["ok"] is True
        assert body["revision"] == REVISION_N
        assert body["mark"] == MARK_BASELINE
        restored = _probe(
            built,
            storage,
            "\n".join(
                [
                    "from app import store",
                    f"fetched = store.get('analytics_surface', {row['id']!r}, tenant_id='local')",
                    "result = fetched",
                ]
            ),
        )
        assert restored is not None
        assert restored["id"] == row["id"]
        assert restored["reference"] == "s11-keep"
        assert restored["quantity"] == 7
        logs += _stop(proc)
        proc = None
        assert_no_emoji_in_machine_logs(logs.splitlines())
    finally:
        if proc is not None:
            _stop(proc)
        revision_py.write_text(original, encoding="utf-8")


def test_structured_logs_carry_request_id_and_reject_emoji(built, tmp_path):
    storage = tmp_path / "logs"
    storage.mkdir()
    port = _free_port()
    proc = _start_revision(
        built, storage, revision=REVISION_N, mark=None, port=port
    )
    try:
        status, _body, headers = _wait_health(port, proc)
        assert status == 200
        status, _body, headers = _http_json(
            f"http://127.0.0.1:{port}/health",
            headers={REQUEST_ID_HEADER: "s11-factory"},
        )
        assert status == 200
        assert headers.get("X-Request-Id") == "s11-factory" or headers.get(
            "x-request-id"
        ) == "s11-factory"
    finally:
        logs = _stop(proc)
    jsonl = storage / "request.jsonl"
    file_lines = jsonl.read_text(encoding="utf-8").splitlines() if jsonl.is_file() else []
    stdout_lines = [ln for ln in logs.splitlines() if ln.strip().startswith("{")]
    json_lines = file_lines + stdout_lines
    assert json_lines, "no structured request lines on disk or stdout"
    assert_no_emoji_in_machine_logs(json_lines)
    parsed = [json.loads(ln) for ln in json_lines]
    assert any(item.get("request_id") == "s11-factory" for item in parsed)
    observed = _probe(
        built,
        storage,
        "\n".join(
            [
                "import json, logging",
                "from app.observe import JsonFormatter, strip_emoji",
                "party = chr(0x1F389)",
                "record = logging.LogRecord(",
                "    'platform.request', logging.INFO, 'x', 0, 'boot ' + party, (), None)",
                "line = JsonFormatter().format(record)",
                "result = {",
                "    'line': line,",
                "    'has_emoji': party in line,",
                "    'stripped': strip_emoji('boot ' + party),",
                "    'parsed': json.loads(line),",
                "}",
            ]
        ),
    )
    assert observed["has_emoji"] is False
    assert observed["stripped"] == "boot "
    assert observed["parsed"]["msg"] == "boot "
    assert contains_emoji(chr(0x1F389)) is True
    assert contains_emoji(observed["line"]) is False
