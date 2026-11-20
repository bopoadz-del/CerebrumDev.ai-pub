"""F-2 deploy safety: whether builds can use the exam proofs directory is
shown where the rollout looks, without taking the API out of rotation.

The probe resolves the directory, runs the vendored-lock gate's placement
check against the gate's own root list (``runner.agent_rw_roots_for``),
creates it and writes then removes a probe file; any error is a plain reason.
Its result is cached briefly. /ready keeps its status code and reports
``details.builds`` (available, reason); boot logs one line; a build start is
refused at the door with the reason; the post-deploy smoke reads it."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import main
from app.factory.build import runner, store_override


@pytest.fixture(autouse=True)
def _fresh_probe_cache():
    store_override._probe_cache.clear()
    yield
    store_override._probe_cache.clear()


def _layout(tmp_path: Path, monkeypatch, proofs: Path) -> Path:
    builds = tmp_path / "storage" / "factory_outputs"
    builds.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(builds))
    monkeypatch.setenv(store_override.PROOFS_DIR_ENV, str(proofs))
    return builds


def _make_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a directory", encoding="utf-8")
    return path


# -- probe_proofs_dir --------------------------------------------------------


@pytest.mark.parametrize(
    "proofs_rel",
    ["storage/exam_proofs", "elsewhere/deep/not/yet/made/exam_proofs"],
)
def test_a_usable_directory_passes_and_keeps_no_probe_file(tmp_path, monkeypatch, proofs_rel):
    proofs = tmp_path / proofs_rel
    builds = _layout(tmp_path, monkeypatch, proofs)
    assert store_override.probe_proofs_dir(builds) is None
    assert proofs.is_dir()
    assert list(proofs.iterdir()) == []


@pytest.mark.parametrize(
    "proofs_rel, expected",
    [
        ("storage/factory_outputs/exam_proofs", "on the build volume"),
        ("storage/factory_outputs/sessions/s1/exam_proofs", "on the build volume"),
        ("storage", "where the coding agent may write"),
    ],
)
def test_a_directory_builds_can_reach_is_refused(tmp_path, monkeypatch, proofs_rel, expected):
    proofs = tmp_path / proofs_rel
    builds = _layout(tmp_path, monkeypatch, proofs)
    reason = store_override.probe_proofs_dir(builds)
    assert reason is not None and expected in reason
    assert str(tmp_path) not in reason


@pytest.mark.parametrize("below", [0, 2])
def test_an_unwritable_directory_is_refused_with_a_plain_reason(tmp_path, monkeypatch, below):
    blocker = _make_file(tmp_path / "blocker")
    proofs = blocker.joinpath(*(["x"] * below))
    builds = _layout(tmp_path, monkeypatch, proofs)
    reason = store_override.probe_proofs_dir(builds)
    assert reason is not None and reason.startswith("the exam proofs directory is not usable (")
    assert str(tmp_path) not in reason


@pytest.mark.parametrize("error", [RuntimeError, OSError])
def test_an_unresolvable_default_is_refused_not_raised(tmp_path, monkeypatch, error):
    builds = _layout(tmp_path, monkeypatch, tmp_path / "unused")
    monkeypatch.delenv(store_override.PROOFS_DIR_ENV)

    def _no_home():
        raise error("no home directory")

    monkeypatch.setattr(store_override.Path, "home", staticmethod(_no_home))
    assert store_override.probe_proofs_dir(builds) == (
        f"the exam proofs directory is not usable ({error.__name__})"
    )


@pytest.mark.parametrize("error", [ValueError, KeyError])
def test_any_exception_in_the_probe_is_not_usable_with_its_class_name(
    tmp_path, monkeypatch, error
):
    builds = _layout(tmp_path, monkeypatch, tmp_path / "factory_data" / "exam_proofs")

    def _raises(*_a, **_k):
        raise error("/a/secret/path")

    monkeypatch.setattr(store_override, "placement_refusal", _raises)
    reason = store_override.probe_proofs_dir(builds)
    assert reason == f"the exam proofs directory is not usable ({error.__name__})"


@pytest.mark.parametrize("extra_name", ["agent_extra", "granted/by/spawn"])
def test_the_probe_checks_the_gates_own_root_list(tmp_path, monkeypatch, extra_name):
    """The function the gate uses supplies the roots: a root only that
    function knows about refuses a directory under it."""
    extra = tmp_path / extra_name
    builds = _layout(tmp_path, monkeypatch, extra / "exam_proofs")
    seen = []
    real = runner.agent_rw_roots_for

    def _with_extra(workspace):
        seen.append(Path(workspace))
        return (*real(workspace), extra)

    monkeypatch.setattr(runner, "agent_rw_roots_for", _with_extra)
    reason = store_override.probe_proofs_dir(builds)
    assert reason == "exam proofs would sit where the coding agent may write"
    assert seen and seen[0].parent == builds


@pytest.mark.parametrize("name", ["prod", "nested/prod"])
def test_the_root_list_is_the_one_the_build_runner_holds(tmp_path, name):
    workspace = tmp_path / name
    roots = runner.agent_rw_roots_for(workspace)
    built = runner.RoleRunner.agent_rw_roots(SimpleNamespace(workspace=workspace))
    assert built == roots
    assert workspace in roots
    assert runner.role_staging_dir(workspace, runner.BuildRole.WRITER) in roots


# -- the cache ---------------------------------------------------------------


@pytest.mark.parametrize("answer", [None, "on the build volume"])
def test_the_probe_result_is_reused_for_a_short_time(tmp_path, monkeypatch, answer):
    calls = []

    def _probe(builds_root, environ=None):
        calls.append(builds_root)
        return answer

    monkeypatch.setattr(store_override, "probe_proofs_dir", _probe)
    env = {store_override.PROOFS_DIR_ENV: str(tmp_path / "p")}
    ttl = store_override.PROBE_CACHE_SECONDS
    assert store_override.builds_unavailable_reason(tmp_path, env, now=100.0) == answer
    assert store_override.builds_unavailable_reason(tmp_path, env, now=100.0 + ttl - 1) == answer
    assert len(calls) == 1
    assert store_override.builds_unavailable_reason(tmp_path, env, now=100.0 + ttl) == answer
    assert len(calls) == 2


@pytest.mark.parametrize("second", ["q", ""])
def test_a_changed_setting_is_probed_again(tmp_path, monkeypatch, second):
    calls = []

    def _probe(builds_root, environ=None):
        calls.append(builds_root)

    monkeypatch.setattr(store_override, "probe_proofs_dir", _probe)
    env = store_override.PROOFS_DIR_ENV
    store_override.builds_unavailable_reason(tmp_path, {env: "p"}, now=5.0)
    store_override.builds_unavailable_reason(tmp_path, {env: second}, now=5.0)
    assert len(calls) == 2


# -- /ready ------------------------------------------------------------------


def _ready(client, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    return client.get("/ready")


@pytest.mark.parametrize("proofs_rel", ["storage/exam_proofs", "factory_data/exam_proofs"])
def test_ready_reports_builds_available(client, tmp_path, monkeypatch, proofs_rel):
    _layout(tmp_path, monkeypatch, tmp_path / proofs_rel)
    res = _ready(client, tmp_path, monkeypatch)
    body = res.json()
    assert body["details"]["builds"] == {"available": True, "reason": ""}
    assert res.status_code == 200 and body["status"] == "ready"


@pytest.mark.parametrize(
    "case, expected",
    [
        ("unwritable", "the exam proofs directory is not usable ("),
        ("on_build_volume", "on the build volume"),
        ("above_agent_roots", "where the coding agent may write"),
    ],
)
def test_ready_stays_ready_when_builds_cannot_start_and_says_why(
    client, tmp_path, monkeypatch, case, expected
):
    proofs = {
        "unwritable": tmp_path / "blocker" / "exam_proofs",
        "on_build_volume": tmp_path / "storage" / "factory_outputs" / "exam_proofs",
        "above_agent_roots": tmp_path / "storage",
    }[case]
    if case == "unwritable":
        _make_file(tmp_path / "blocker")
    _layout(tmp_path, monkeypatch, proofs)
    res = _ready(client, tmp_path, monkeypatch)
    body = res.json()
    assert res.status_code == 200 and body["status"] == "ready"
    assert "exam_proofs_dir" not in body["checks"]
    assert body["details"]["builds"]["available"] is False
    assert expected in body["details"]["builds"]["reason"]
    assert str(tmp_path) not in str(body["details"]["builds"])


@pytest.mark.parametrize("env", ["production", "prod"])
def test_ready_is_not_ready_in_production_when_drive_tokens_are_unencrypted(
    tmp_path, monkeypatch, env
):
    """The builds detail is informational: /ready's own production rule (Drive
    configured, no encryption key) still answers 503 beside it."""
    _layout(tmp_path, monkeypatch, tmp_path / "storage" / "exam_proofs")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ENV", env)
    monkeypatch.setenv("CEREBRUM_DEV_API_KEY", "master-for-ready")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "drive-client")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "drive-secret")
    monkeypatch.delenv("DATA_ENCRYPTION_KEY", raising=False)
    res = asyncio.run(main.ready())
    body = json.loads(res.body)
    assert res.status_code == 503 and body["status"] == "not_ready"
    assert body["checks"]["data_encryption"] is False
    assert body["details"]["builds"] == {"available": True, "reason": ""}


# -- a build start -----------------------------------------------------------


def _no_thread(*_a, **_k):
    raise AssertionError("a refused build must not start a runner thread")


@pytest.mark.parametrize(
    "proofs_rel, expected",
    [
        ("storage/factory_outputs/exam_proofs", "on the build volume"),
        ("blocker/exam_proofs", "the exam proofs directory is not usable ("),
    ],
)
def test_a_build_start_is_refused_at_the_door_with_the_reason(
    tmp_path, monkeypatch, proofs_rel, expected
):
    from app.factory import build_jobs
    from app.factory.build.coder_session import BuildsUnavailable

    _make_file(tmp_path / "blocker")
    builds = _layout(tmp_path, monkeypatch, tmp_path / proofs_rel)
    out = builds / "prod"
    monkeypatch.setattr(runner, "frozen_blueprint", lambda bp: bp)
    monkeypatch.setattr(runner, "blueprint_hash", lambda bp: "h")
    monkeypatch.setattr(build_jobs.threading, "Thread", _no_thread)
    with pytest.raises(BuildsUnavailable) as exc:
        build_jobs.start_runner_build(SimpleNamespace(product_id="p"), out, brief='{"ask": "Count the items on hand.", "documents": {"file_types": [], "uploads": false}}')
    assert expected in str(exc.value)
    assert str(tmp_path) not in str(exc.value)
    assert not build_jobs._ledger_path(out).exists()


@pytest.mark.parametrize(
    "reason", ["on the build volume", "the exam proofs directory is not usable (OSError)"]
)
def test_the_floor_shows_the_refusal_reason(reason):
    from app.factory.build.coder_session import BuildsUnavailable
    from app.factory.platform_chat_flow import _cli_unavailable_reply

    pd = SimpleNamespace(blueprint_approved=True, last_error=None)
    reply = _cli_unavailable_reply(pd, BuildsUnavailable(reason))
    assert reply["ok"] is False
    assert reply["blocker"] == "FACTORY_BUILDS_UNAVAILABLE"
    assert reason in reply["summary"] and pd.last_error == reason


# -- boot --------------------------------------------------------------------


def _quiet_boot(monkeypatch) -> None:
    """Every other boot step becomes a no-op: this test reads one line."""
    from app.cerebrum_product_kernel.ingestion import engine_install
    from app.factory.build import agent_process, orphan_recovery

    monkeypatch.setattr(engine_install, "install_default_engine", lambda: None)
    monkeypatch.setattr(main.backup_scheduler, "start", lambda: None)
    monkeypatch.setattr(agent_process, "guard_exit_signals", lambda: None)
    monkeypatch.setattr(orphan_recovery, "recover_orphaned_model_calls", lambda: None)
    monkeypatch.setattr(orphan_recovery, "recover_stranded_n3_handoffs", lambda: None)


async def _boot_once() -> None:
    async with main._lifespan(main.app):
        pass


@pytest.mark.parametrize(
    "proofs_rel, logged",
    [
        ("storage/exam_proofs", None),
        ("factory_data/exam_proofs", None),
        ("storage/factory_outputs/exam_proofs", "on the build volume"),
        ("storage", "where the coding agent may write"),
    ],
)
def test_boot_logs_one_plain_line_only_when_builds_cannot_use_the_directory(
    tmp_path, monkeypatch, caplog, proofs_rel, logged
):
    _layout(tmp_path, monkeypatch, tmp_path / proofs_rel)
    _quiet_boot(monkeypatch)
    with caplog.at_level(logging.ERROR, logger="cerebrumdev.factory.exam_proofs"):
        asyncio.run(_boot_once())
    lines = [
        r.getMessage() for r in caplog.records if r.name == "cerebrumdev.factory.exam_proofs"
    ]
    if logged is None:
        assert lines == []
    else:
        assert len(lines) == 1
        assert lines[0].startswith("builds unavailable: ") and logged in lines[0]
        assert str(tmp_path) not in lines[0]
