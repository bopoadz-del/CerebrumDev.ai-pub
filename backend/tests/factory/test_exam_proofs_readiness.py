"""F-2 deploy safety: the exam proofs directory is checked where the rollout
stops (boot and /ready), not in a customer's build.

The probe resolves the directory, runs the same placement check the build
runs (off the build volume, neither under nor above an agent root), creates
it and writes then removes a probe file. A failure is one plain line at boot
and /ready answers not ready with the reason (no path)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from app import main
from app.factory.build import store_override


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


# -- /ready ------------------------------------------------------------------


def _ready(client, tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    return client.get("/ready")


@pytest.mark.parametrize("proofs_rel", ["storage/exam_proofs", "factory_data/exam_proofs"])
def test_ready_with_a_usable_proofs_directory(client, tmp_path, monkeypatch, proofs_rel):
    _layout(tmp_path, monkeypatch, tmp_path / proofs_rel)
    res = _ready(client, tmp_path, monkeypatch)
    body = res.json()
    assert body["checks"]["exam_proofs_dir"] is True
    assert body["details"]["exam_proofs_dir"] == {"ok": True, "error": ""}
    assert res.status_code == 200 and body["status"] == "ready"


@pytest.mark.parametrize(
    "case, expected",
    [
        ("unwritable", "the exam proofs directory is not usable ("),
        ("on_build_volume", "on the build volume"),
        ("above_agent_roots", "where the coding agent may write"),
    ],
)
def test_ready_is_not_ready_when_builds_cannot_use_the_proofs_directory(
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
    assert res.status_code == 503 and body["status"] == "not_ready"
    assert body["checks"]["exam_proofs_dir"] is False
    assert expected in body["details"]["exam_proofs_dir"]["error"]
    assert str(tmp_path) not in str(body["details"]["exam_proofs_dir"])


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
    lines = [r.getMessage() for r in caplog.records if r.name == "cerebrumdev.factory.exam_proofs"]
    if logged is None:
        assert lines == []
    else:
        assert len(lines) == 1
        assert lines[0].startswith("factory not ready: ") and logged in lines[0]
        assert str(tmp_path) not in lines[0]
