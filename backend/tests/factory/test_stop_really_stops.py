"""F-U1a: the owner's Stop really stops the build in flight.

14 Nov: the console's Stop wrote ``docs/coder_control.json`` and nothing
running read it -- the only reader was the retired coder-session
dispatcher. The writer CLI's wait loop, the HTTP model-call watchdog and the
phase loop never looked, so a stopped build kept its CLI (and its model
calls) alive and kept its slot.

Each test runs the stopped case and the not-stopped case. (Pause / Resume
and the pilot runner's own stop are F-U1b.)
"""

from __future__ import annotations

import os
import threading
import time
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from app.factory import llm_watchdog
from app.factory.blueprint import CapabilitySpec, ProductBlueprint, load_blueprint
from app.factory.build import codewhale_worker
from app.factory.build import specialist_fanout as fan
from app.factory.build.codewhale_worker import (
    PROCESS_CAP_ENV,
    InProcessSlotCounter,
    set_slot_counter,
)
from app.factory.build.coder_session_status import (
    OwnerStopped,
    check_owner,
    current_owner_control,
    owner_control_scope,
    read_control,
    read_control_record,
    write_control,
)
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import ROLE_IMPLEMENTATIONS
from app.factory.build.rule_decision import DECISION_KEY
from app.factory.build.runner import BUILD_PHASES, Outcome, RoleRunner
from app.factory.build.tenant_bind import TenantStoreBinding, bind_tenant_store

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"
POSIX_ONLY = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")


@pytest.fixture(autouse=True)
def _fresh_slots():
    previous = set_slot_counter(InProcessSlotCounter())
    try:
        yield
    finally:
        set_slot_counter(previous)


def _alive(pid: int) -> bool:
    """A process that exists and is not a zombie."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


def _out(tmp_path: Path) -> Path:
    return tmp_path / "checkout" / "out"


def _fake_agent(tmp_path: Path, ticks: int) -> Path:
    """A CLI that starts a child, then 'calls the model' once per tick.

    Its evidence lives under the checkout: the CLI is confined (Landlock) to
    write only beneath its working directory.
    """
    script = tmp_path / "fake_agent.sh"
    out = _out(tmp_path)
    out.mkdir(parents=True, exist_ok=True)
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo 1.0; exit 0; fi\n'
        f"echo started > {out}/started\n"
        f"sleep 300 >/dev/null 2>&1 &\necho $! > {out}/child.pid\n"
        f"i=0\nwhile [ $i -lt {ticks} ]; do echo call >> {out}/calls; i=$((i+1)); sleep 0.2; done\n"
        f"echo done > {out}/done\n"
        "echo '{\"status\": \"completed\"}'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _calls(tmp_path: Path) -> int:
    try:
        return len((_out(tmp_path) / "calls").read_text().splitlines())
    except OSError:
        return 0


def _run_job(tmp_path: Path, agent: Path, control) -> dict:
    """One writer job on this thread, under the owner control ``control``."""
    result: dict = {}
    with mock.patch.object(codewhale_worker, "worker_cli_path", return_value=str(agent)), \
         mock.patch.object(codewhale_worker, "worker_api_key", return_value="sk-test"), \
         mock.patch.object(codewhale_worker, "worker_provider", return_value="deepseek"):
        started = time.monotonic()
        try:
            with owner_control_scope(control):
                result["receipt"] = codewhale_worker.run_worker_job(
                    "write the platform",
                    tmp_path / "checkout",
                    tenant_store=bind_tenant_store("acct-stop"),
                    progress=lambda line, info: None,
                    timeout_s=60,
                )
        except OwnerStopped as exc:
            result["stopped"] = exc
        except Exception as exc:  # noqa: BLE001 -- recorded for the assertion
            result["error"] = exc
        result["wall"] = time.monotonic() - started
    return result


# --- the reader itself --------------------------------------------------------


@pytest.mark.parametrize("pressed, stopped", [("stop", True), ("run", False)])
def test_check_owner_raises_only_on_stop(pressed, stopped):
    raised = False
    try:
        check_owner(lambda: pressed)
    except OwnerStopped:
        raised = True
    assert raised == stopped
    assert issubclass(OwnerStopped, BaseException)
    assert not issubclass(OwnerStopped, Exception), "no except Exception may eat a stop"


def _broken() -> str:
    raise OSError("control unreadable")


@pytest.mark.parametrize("reader", [None, _broken])
def test_no_control_or_a_broken_one_never_stops_a_build(reader):
    with owner_control_scope(reader):
        assert current_owner_control() is reader
        check_owner()  # returns: run on
    assert current_owner_control() is None


# --- (a) + (b): Stop kills the CLI and its children; the slot is free -------


@POSIX_ONLY
@pytest.mark.parametrize("pressed, stopped", [("stop", True), ("run", False)])
def test_stop_kills_the_writer_tree_and_frees_the_slot(tmp_path, pressed, stopped):
    agent = _fake_agent(tmp_path, ticks=10)
    child_pid_file = _out(tmp_path) / "child.pid"

    def control() -> str:
        # The owner presses once the CLI is working (its first model call).
        return pressed if _calls(tmp_path) >= 1 else "run"

    result = _run_job(tmp_path, agent, control)
    assert ("stopped" in result) == stopped, result
    assert (_out(tmp_path) / "done").exists() == (not stopped)
    child = int(child_pid_file.read_text().strip())
    if stopped:
        assert result["wall"] < 10, "a stop ends the job within seconds"
        calls_at_stop = _calls(tmp_path)
        time.sleep(1.0)
        assert _calls(tmp_path) == calls_at_stop, "no model call after the stop"
        deadline = time.monotonic() + 5
        while _alive(child) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _alive(child), "the CLI's child outlived the stop"
    else:
        os.kill(child, 9)
    snap = codewhale_worker.slot_counter().snapshot()
    assert snap["total"] == 0 and snap["by_tenant"] == {}, snap


@POSIX_ONLY
@pytest.mark.parametrize("pressed, stopped", [("stop", True), ("run", False)])
def test_no_cli_starts_after_a_stop(tmp_path, pressed, stopped):
    agent = _fake_agent(tmp_path, ticks=1)
    result = _run_job(tmp_path, agent, lambda: pressed)
    assert ("stopped" in result) == stopped, result
    assert (_out(tmp_path) / "started").exists() == (not stopped)
    if not stopped:
        os.kill(int((_out(tmp_path) / "child.pid").read_text().strip()), 9)
    assert codewhale_worker.slot_counter().snapshot()["total"] == 0


# --- (c): Stop mid model call (the HTTP stages) -------------------------------


@pytest.mark.parametrize("pressed, stopped", [("stop", True), ("run", False)])
def test_stop_cancels_a_model_call_in_flight(pressed, stopped):
    release = threading.Event()
    answer = object()

    def slow_post(url, **_kw):
        release.wait(2.0)
        return answer

    pressed_at = time.monotonic() + 0.3

    def control() -> str:
        return pressed if time.monotonic() >= pressed_at else "run"

    started = time.monotonic()
    try:
        with owner_control_scope(control):
            got = llm_watchdog.post_with_deadline(
                "http://model.invalid/v1", json={}, post=slow_post, timeout=30
            )
        outcome = "answered" if got is answer else "other"
    except OwnerStopped:
        outcome = "stopped"
    finally:
        release.set()
    assert outcome == ("stopped" if stopped else "answered"), outcome
    if stopped:
        assert time.monotonic() - started < 1.5, "the call is cancelled, not waited for"


@pytest.mark.parametrize("pressed, sent", [("stop", False), ("run", True)])
def test_no_model_call_starts_after_a_stop(pressed, sent):
    posted: list = []

    def post(url, **_kw):
        posted.append(url)
        return "ok"

    try:
        with owner_control_scope(lambda: pressed):
            llm_watchdog.post_with_deadline("http://model.invalid/v1", json={}, post=post, timeout=5)
    except OwnerStopped:
        pass
    assert bool(posted) == sent


# --- (b) + (c) + (f): every stage ends as one named final state, in-process --


@pytest.fixture()
def _every_phase_runs(monkeypatch):
    """No paid calls, and every phase runs (collect-all: a failed gate is a
    finding, not a halt), so a stop can be pressed at any stage. A stop is
    not a finding: collect-all never sees it."""
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")
    monkeypatch.setenv("FACTORY_GATE_COLLECT_ALL", "1")


def _phases_started(runner) -> list:
    return [e.role for e in runner.ledger.events() if e.kind is EventKind.PHASE_STARTED]


@pytest.mark.parametrize("stage", [*BUILD_PHASES, None])
def test_stop_at_any_stage_ends_the_build_as_stopped_by_owner(
    tmp_path, monkeypatch, stub_coder, _every_phase_runs, stage
):
    out = tmp_path / "build"
    roles = dict(ROLE_IMPLEMENTATIONS)
    narrated: list = []
    monkeypatch.setattr(
        RoleRunner, "_attach_failure_narrative", lambda self, phase: narrated.append(phase)
    )

    def pressing_stop(inner):
        def role(ctx):
            write_control(out, "stop", by="owner-account")
            # The stage's next model call is where the stop lands.
            llm_watchdog.post_with_deadline(
                "http://model.invalid/v1", json={}, post=lambda *a, **k: None, timeout=5
            )
            return inner(ctx)

        return role

    if stage is not None:
        roles[stage] = pressing_stop(roles[stage])
    runner = RoleRunner(load_blueprint(SMOKE), out, roles=roles)
    outcome = runner.run()

    assert (outcome.outcome is Outcome.STOPPED_BY_OWNER) == (stage is not None)
    if stage is None:
        assert _phases_started(runner) == list(BUILD_PHASES), "an unstopped build runs on"
        return
    assert _phases_started(runner)[-1] is stage, "nothing ran after the stop"
    assert outcome.failed_phase is stage
    aborted = [e for e in runner.ledger.events() if e.kind is EventKind.PHASE_ABORTED]
    assert aborted and aborted[-1].role is stage
    decision = runner.ledger.terminal_event().payload[DECISION_KEY]
    assert decision["reason"] == "stopped_by_owner"
    assert decision["by"] == "owner-account"
    assert decision["at"] == read_control_record(out)["updated_at"]
    assert narrated == [], "a stopped build makes no model call to narrate itself"


@pytest.mark.parametrize("pressed, stopped", [("stop", True), ("run", False)])
def test_a_stop_between_stages_starts_no_further_phase(
    tmp_path, stub_coder, _every_phase_runs, pressed, stopped
):
    out = tmp_path / "build"
    roles = dict(ROLE_IMPLEMENTATIONS)
    first = BUILD_PHASES[0]
    inner = roles[first]

    def pressing(ctx):
        # Pressed inside the first stage, with no model call after it in
        # that stage: the next phase boundary is where it lands.
        result = inner(ctx)
        write_control(out, pressed)
        return result

    roles[first] = pressing
    runner = RoleRunner(load_blueprint(SMOKE), out, roles=roles)
    outcome = runner.run()
    assert (outcome.outcome is Outcome.STOPPED_BY_OWNER) == stopped
    assert _phases_started(runner) == ([first] if stopped else list(BUILD_PHASES))


# --- the specialists read the writer's control --------------------------------


def _within(seconds: float = 3.0):
    end = time.monotonic() + seconds
    return lambda: end - time.monotonic()


def _start_specialists(tmp_path, monkeypatch, fake_run):
    monkeypatch.setenv(PROCESS_CAP_ENV, "6")
    monkeypatch.setattr(codewhale_worker, "run_worker_job_with_stall_retry", fake_run)
    dest = tmp_path / "dest"
    (dest / "app").mkdir(parents=True)
    (dest / "app" / "main.py").write_text("M", encoding="utf-8")
    tenant = TenantStoreBinding(
        tenant_key="key_spec", store_dir=Path("/tmp/tenant-spec"), bound_at=0.0
    )
    with ExitStack() as stack:
        fo, _held = fan.start_for_writer(
            stack,
            dest,
            brief="B",
            note=lambda *a, **k: None,
            tenant_store=tenant,
            session_id="s",
            product_id="p",
            timeout_s=60.0,
            live_time_left=None,
            slot_wait_left=_within(),
            on_slot_wait=None,
            turn_wall_s=None,
        )
        fo.finish()
        fo.cleanup()
    return fo


@pytest.mark.parametrize("installed", [True, False])
def test_specialists_read_the_writer_control(tmp_path, monkeypatch, installed):
    seen: list = []

    def fake_run(prompt, checkout, **kw):
        seen.append(current_owner_control())
        return SimpleNamespace(status="completed", rounds={})

    def control() -> str:
        return "run"

    if installed:
        with owner_control_scope(control):
            _start_specialists(tmp_path, monkeypatch, fake_run)
    else:
        _start_specialists(tmp_path, monkeypatch, fake_run)
    assert seen, "the specialists ran"
    assert all(read is (control if installed else None) for read in seen)


@pytest.mark.parametrize("stopped", [True, False])
def test_a_stopped_specialist_is_recorded_as_stopped(tmp_path, monkeypatch, stopped):
    def fake_run(prompt, checkout, **kw):
        if stopped:
            raise OwnerStopped("owner stopped the build")
        return SimpleNamespace(status="completed", rounds={})

    fo = _start_specialists(tmp_path, monkeypatch, fake_run)
    statuses = {r.get("status") for r in fo.results.values()}
    assert statuses and (statuses == {"stopped"}) == stopped, fo.results


# --- (e): the one authenticated route records who pressed it -----------------


@pytest.mark.parametrize("by", ["acct-owner", None])
def test_the_stop_record_says_who_pressed_it(tmp_path, by):
    write_control(tmp_path, "stop", by=by)
    record = read_control_record(tmp_path)
    assert record["action"] == "stop"
    assert record.get("by") == by
    assert read_control(tmp_path) == "stop"


@pytest.mark.parametrize("action, stored", [("stop", "stop"), ("resume", "run")])
def test_the_route_writes_the_control_with_the_principal(tmp_path, monkeypatch, action, stored):
    from fastapi.testclient import TestClient

    from app.core.auth import DEV_LOCAL_TENANT
    from app.core.session_store import create_session, get_session, update_session
    from app.main import app

    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "factory_outputs"))
    monkeypatch.setenv("BILLING_ENFORCEMENT", "0")
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    out = tmp_path / "workspace"
    out.mkdir()
    session_id = "sess_owner_stop"
    create_session(session_id, DEV_LOCAL_TENANT)
    state = get_session(session_id)
    state.product_design.generation = {"output_dir": str(out)}
    update_session(session_id, state)

    res = TestClient(app).post(
        f"/v1/sessions/{session_id}/product/coder-control", json={"action": action}
    )
    assert res.status_code == 200, res.text
    assert res.json()["control"]["action"] == stored
    record = read_control_record(out)
    assert record["action"] == stored
    assert record.get("by"), "the record names who pressed it"


# --- a new build starts with the control reset --------------------------------


def _bp() -> ProductBlueprint:
    return ProductBlueprint(
        schema_version="product_blueprint.v1",
        product_id="owner-stop-demo",
        product_name="Owner Stop",
        vertical="demo",
        summary="owner stop",
        capabilities=[
            CapabilitySpec(id="audit", description="audit", block_ids=["audit"], strategy_hint="REUSE")
        ],
    )


@pytest.mark.parametrize("running, expected", [(False, "run"), (True, "stop")])
def test_a_new_build_resets_the_control_and_a_refused_one_does_not(
    tmp_path, monkeypatch, running, expected
):
    from app.factory.build_jobs import start_runner_build

    monkeypatch.setenv("FACTORY_BUILD_ENGINE", "runner")
    monkeypatch.setattr("threading.Thread.start", lambda self: None)
    out = tmp_path / "product"
    out.mkdir()
    if running:
        # A build of this workspace is in flight: its Stop is its own.
        ledger = BuildLedger(out / "build_ledger.jsonl")
        ledger.start_run(product_id="owner-stop-demo", inputs_hash="abc123")
        ledger.append(EventKind.PHASE_STARTED, role="COLLECTOR", detail="in flight")
    write_control(out, "stop", by="owner-account")
    start_runner_build(_bp(), out)
    assert read_control(out) == expected
