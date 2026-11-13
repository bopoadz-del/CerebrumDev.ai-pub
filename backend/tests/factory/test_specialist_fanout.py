"""F-S2c: the Factory dispatches the writer's specialists as their own sessions.

Ruling (a), Advisor 18:20Z: specialist sessions are a CHILD slot class of the
held writer slot, counted against the PROCESS cap only and bounded by
``writer_specialist_cap()`` -- no new number, no ceiling change. Each
specialist sees the brief, a skeleton index and its own scope, runs in a
worktree of its own copied from the skeleton, and is merged back under the
factory_owned snapshot/restore with two-writer conflicts recorded by path and
never overwritten. DEVOPS then SECURITY run serially on the merged tree; the
writer prompt drops its SPECIALISTS block when the fan-out is on.
"""

from __future__ import annotations

import os
import stat
import sys
import threading
import time
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.factory.build import codewhale_worker
from app.factory.build import specialist_fanout as fan
from app.factory.build.codewhale_worker import (
    PROCESS_CAP_ENV,
    PROFILE_ENV,
    SLOT_WAIT_EXHAUSTED,
    SPECIALIST_WITHOUT_PARENT,
    TENANT_CAP_ENV,
    InProcessSlotCounter,
    WorkerError,
    rounds_summary_line,
    set_slot_counter,
    specialist_child_slot,
    tree_rss_mb,
    worker_job_slot,
    worker_slots_snapshot,
)
from app.factory.build.tenant_bind import TenantStoreBinding
from app.factory.build.writer_prompt import (
    SPECIALISTS_HEAD,
    WRITER_SPECIALIST,
    render_writer_prompt,
    specialist_scopes,
)

def _within(seconds: float = 3.0):
    """A bounded ceiling: a broken counter fails a test, never hangs it."""
    end = time.monotonic() + seconds
    return lambda: end - time.monotonic()


def _binding(digest: str) -> TenantStoreBinding:
    return TenantStoreBinding(
        tenant_key=f"key_{digest}", store_dir=Path(f"/tmp/tenant-{digest}"), bound_at=0.0
    )


@pytest.fixture(autouse=True)
def _fresh_slots(monkeypatch):
    for name in (PROCESS_CAP_ENV, TENANT_CAP_ENV, PROFILE_ENV, fan.FANOUT_ENV):
        monkeypatch.delenv(name, raising=False)
    previous = set_slot_counter(InProcessSlotCounter())
    try:
        yield
    finally:
        set_slot_counter(previous)


def _wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


# -- the child slot class ----------------------------------------------------


@pytest.mark.parametrize("child_cap,expected", [(1, 1), (3, 3)])
def test_child_cap_bounds_concurrent_children(child_cap, expected):
    """cap 1 vs 3 gives 1 vs 3 concurrent children of one held writer slot."""
    counter = InProcessSlotCounter()
    counter.acquire("A", process_cap=10, tenant_cap=1)
    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def child():
        counter.acquire_child("A", process_cap=10, child_cap=child_cap, time_left=_within(), poll_s=0.01)
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        time.sleep(0.15)
        with lock:
            live["now"] -= 1
        counter.release_child("A")

    threads = [threading.Thread(target=child, daemon=True) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert live["peak"] == expected
    assert counter.snapshot() == {"total": 1, "by_tenant": {"A": 1}}


@pytest.mark.parametrize("child_cap", [2, 3])
def test_releasing_one_child_frees_exactly_one(child_cap):
    counter = InProcessSlotCounter()
    counter.acquire("A", process_cap=10, tenant_cap=1)
    for _ in range(child_cap):
        counter.acquire_child("A", process_cap=10, child_cap=child_cap, time_left=_within())
    counter.release_child("A")
    assert counter._child_admissible("A", 10, child_cap) is True
    counter.acquire_child("A", process_cap=10, child_cap=child_cap, time_left=_within())
    assert counter._child_admissible("A", 10, child_cap) is False


@pytest.mark.parametrize("children", [1, 2])
def test_a_child_counts_against_the_process_cap_only(children):
    counter = InProcessSlotCounter()
    counter.acquire("A", process_cap=10, tenant_cap=1)
    for _ in range(children):
        counter.acquire_child("A", process_cap=10, child_cap=5, time_left=_within())
    snap = counter.snapshot()
    assert snap["total"] == 1 + children
    assert snap["by_tenant"] == {"A": 1}


@pytest.mark.parametrize("process_cap,prefill", [(3, 1), (4, 2)])
def test_a_second_tenants_job_is_never_starved_by_a_child(process_cap, prefill):
    """The box is full with A's writer and its children; B's writer queues.
    When one child ends, B's queued writer takes the slot -- a child of A
    asking at the same time waits behind it."""
    counter = InProcessSlotCounter()
    counter.acquire("A", process_cap=process_cap, tenant_cap=1)
    for _ in range(prefill):
        counter.acquire_child("A", process_cap=process_cap, child_cap=5, time_left=_within())
    got = []

    def b_writer():
        counter.acquire_waiting(
            "B", process_cap=process_cap, tenant_cap=1, time_left=_within(), poll_s=0.01
        )
        got.append("B")

    def a_child():
        counter.acquire_child(
            "A", process_cap=process_cap, child_cap=5, time_left=_within(), poll_s=0.01
        )
        got.append("A-child")

    tb = threading.Thread(target=b_writer, daemon=True)
    tb.start()
    assert _wait_until(lambda: len(counter._waiters) == 1)
    ta = threading.Thread(target=a_child, daemon=True)
    ta.start()
    time.sleep(0.1)
    assert got == []
    counter.release_child("A")
    assert _wait_until(lambda: "B" in got)
    time.sleep(0.1)
    assert got == ["B"]
    counter.release("B")
    assert _wait_until(lambda: "A-child" in got)


def test_a_child_with_no_held_writer_slot_is_refused_by_name():
    counter = InProcessSlotCounter()
    with pytest.raises(WorkerError) as exc:
        counter.acquire_child("A", process_cap=5, child_cap=3, time_left=_within())
    assert SPECIALIST_WITHOUT_PARENT in str(exc.value)
    assert counter.snapshot()["total"] == 0


def test_a_child_waiting_past_the_ceiling_stops_with_slot_wait_exhausted():
    counter = InProcessSlotCounter()
    counter.acquire("A", process_cap=5, tenant_cap=1)
    counter.acquire_child("A", process_cap=5, child_cap=1, time_left=_within())
    with pytest.raises(WorkerError) as exc:
        counter.acquire_child("A", process_cap=5, child_cap=1, time_left=lambda: 0.0)
    assert SLOT_WAIT_EXHAUSTED in str(exc.value)


@pytest.mark.parametrize("child_cap,expected_total", [(1, 2), (2, 3)])
def test_specialist_child_slot_holds_and_releases(monkeypatch, child_cap, expected_total):
    monkeypatch.setenv(PROCESS_CAP_ENV, "6")
    tenant = _binding("child")
    with worker_job_slot(tenant):
        with ExitStack() as stack:
            for _ in range(child_cap):
                stack.enter_context(
                    specialist_child_slot(tenant, wait_left=_within(), child_cap=child_cap)
                )
            assert worker_slots_snapshot()["total"] == expected_total
        assert worker_slots_snapshot()["total"] == 1
    assert worker_slots_snapshot()["total"] == 0


# -- per-child RSS: the measurement PER_JOB_MB is waiting for ----------------


def _proc(root: Path, pid: int, ppid: int, rss_kb: int) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "status").write_text(
        f"Name:\tx\nPPid:\t{ppid}\nVmRSS:\t{rss_kb} kB\n", encoding="utf-8"
    )


@pytest.mark.parametrize("pid,expected", [(100, 4.0), (101, 3.0), (999, None)])
def test_tree_rss_sums_the_process_and_its_descendants(tmp_path, pid, expected):
    _proc(tmp_path, 100, 1, 1024)
    _proc(tmp_path, 101, 100, 2048)
    _proc(tmp_path, 102, 101, 1024)
    _proc(tmp_path, 200, 1, 9999)
    assert tree_rss_mb(pid, proc_root=str(tmp_path)) == expected


def test_tree_rss_is_none_without_a_process_table(tmp_path):
    assert tree_rss_mb(1, proc_root=str(tmp_path / "absent")) is None


@pytest.mark.parametrize(
    "stats,shown", [({"rounds": 3, "peak_rss_mb": 512.0}, True), ({"rounds": 3}, False)]
)
def test_rounds_line_carries_the_measured_peak_only_when_read(stats, shown):
    assert ("peak_rss_mb=512.0" in rounds_summary_line(stats)) == shown


@pytest.mark.skipif(sys.platform == "win32", reason="the fake CLI is a POSIX shell script")
@pytest.mark.parametrize("every_s,expect_first_only", [(1000.0, True), (0.0, False)])
def test_worker_records_the_childs_peak_rss_in_its_receipt(
    tmp_path, monkeypatch, every_s, expect_first_only
):
    fake = tmp_path / "fake-codewhale"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then echo "codewhale 0.9.13-test"; exit 0; fi\n'
        "sleep 2\n"
        'echo \'{"status": "completed", "termination_reason": "done"}\'\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    samples = iter(range(100, 10000, 100))
    monkeypatch.setattr(codewhale_worker, "worker_cli_path", lambda: str(fake))
    monkeypatch.setattr(codewhale_worker, "_dns_probe", lambda cwd, env: {})
    monkeypatch.setattr(codewhale_worker, "RSS_SAMPLE_EVERY_S", every_s)
    monkeypatch.setattr(codewhale_worker, "tree_rss_mb", lambda pid: float(next(samples)))
    receipt = codewhale_worker.run_worker_job(
        "build it", tmp_path / "out", tenant_store=_binding("rss"), timeout_s=60.0
    )
    peak = receipt.rounds["peak_rss_mb"]
    assert (peak == 100.0) == expect_first_only


# -- the prompt: one SPECIALISTS text, cut when the Factory dispatches -------


@pytest.mark.parametrize(
    "name,fragment", [("INFRA", "Config and secrets"), ("SECURITY", "attack it")]
)
def test_each_hat_reads_its_scope_from_the_one_block(name, fragment):
    scopes = specialist_scopes()
    assert list(scopes) == ["INFRA", "BACKEND", "FRONTEND", "DEVOPS", "SECURITY"]
    assert fragment in scopes[name]


@pytest.mark.parametrize(
    "dispatched,block_shown", [((), True), (("INFRA", "FRONTEND", "DEVOPS", "SECURITY"), False)]
)
def test_writer_prompt_drops_the_specialists_block_when_dispatched(dispatched, block_shown):
    bp = SimpleNamespace(product_id="p", product_name="P", vertical="v", summary="s")
    text = render_writer_prompt(bp, dispatched_specialists=dispatched)
    assert (SPECIALISTS_HEAD in text) == block_shown
    assert (f"YOUR SCOPE: {WRITER_SPECIALIST}" in text) == (not block_shown)
    assert "PROGRESS LOG (the operator watches" in text


# -- the fan-out module --------------------------------------------------------


@pytest.mark.parametrize("value,on", [("1", True), ("on", True), ("", False), ("0", False)])
def test_fanout_switch(monkeypatch, value, on):
    monkeypatch.setenv(fan.FANOUT_ENV, value)
    assert fan.fanout_enabled() is on


@pytest.mark.parametrize("index,name", [(0, "INFRA"), (3, "SECURITY")])
def test_dispatched_order_is_parallel_then_serial(index, name):
    assert fan.dispatched_specialists()[index] == name


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("limit,tail", [(10, None), (2, "- ... and 1 more")])
def test_skeleton_index_lists_paths_and_counts_the_rest(tmp_path, limit, tail):
    for rel in ("app/a.py", "app/b.py", "README.md"):
        _write(tmp_path, rel, rel)
    lines = fan.skeleton_index(fan.tree_manifest(tmp_path), limit=limit).splitlines()
    assert lines[0] == "- README.md"
    assert (lines[-1] == tail) if tail else len(lines) == 3


@pytest.mark.parametrize("after_merge,phrase", [(False, "BESIDE"), (True, "AFTER")])
def test_specialist_prompt_has_brief_index_and_own_scope(after_merge, phrase):
    text = fan.render_specialist_prompt(
        "DEVOPS", brief="BRIEF-TEXT", index="- app/x.py", after_merge=after_merge
    )
    assert phrase in text
    assert "BRIEF-TEXT" in text and "- app/x.py" in text
    assert specialist_scopes()["DEVOPS"] in text
    assert specialist_scopes()["SECURITY"] not in text
    assert "STEP <n> [DEVOPS]" in text


@pytest.mark.parametrize("name", ["INFRA", "FRONTEND"])
def test_worktree_is_a_copy_of_the_skeleton_and_is_removed(tmp_path, name):
    skel = tmp_path / "skel"
    _write(skel, "app/a.py", "A")
    _write(skel, ".git/HEAD", "ref")
    _write(skel, "locked.txt", "L")
    os.chmod(skel / "locked.txt", stat.S_IREAD)
    tree = fan.make_worktree(skel, tmp_path / "wt", name)
    assert tree.name == name.lower()
    assert (tree / "app/a.py").read_text(encoding="utf-8") == "A"
    assert not (tree / ".git").exists()
    fan._rmtree(tmp_path / "wt")
    assert not (tmp_path / "wt").exists()
    os.chmod(skel / "locked.txt", stat.S_IWRITE | stat.S_IREAD)


@pytest.mark.parametrize("edit,expected", [("same", {}), ("changed", {"app/a.py": "digest"})])
def test_changed_paths_reports_only_differences(tmp_path, edit, expected):
    _write(tmp_path, "app/a.py", "A")
    _write(tmp_path, "app/b.py", "B")
    base = fan.tree_manifest(tmp_path)
    if edit == "changed":
        _write(tmp_path, "app/a.py", "A2")
    out = fan.changed_paths(base, tmp_path)
    assert sorted(out) == sorted(expected)


def _merge_case(tmp_path, case):
    dest, wt = tmp_path / "dest", tmp_path / "wt"
    for root in (dest, wt):
        _write(root, "app/shared.py", "BASE")
        _write(root, "app/gone.py", "G")
        _write(root, "docs/owned.json", "{}")
    base = fan.tree_manifest(dest)
    if case == "new":
        _write(wt, "infra/settings.py", "S")
    elif case == "conflict":
        _write(dest, "app/shared.py", "WRITER")
        _write(wt, "app/shared.py", "SPECIALIST")
    elif case == "same":
        _write(dest, "app/shared.py", "BOTH")
        _write(wt, "app/shared.py", "BOTH")
    elif case == "owned":
        _write(wt, "docs/owned.json", '{"x": 1}')
    elif case == "deleted":
        (wt / "app/gone.py").unlink()
    merged = fan.merge_back(dest, base, {"INFRA": wt}, owned=["docs/owned.json"], writer="BACKEND")
    return dest, merged


@pytest.mark.parametrize(
    "case,applied,conflicts,owned",
    [
        ("new", ["infra/settings.py"], 0, 0),
        ("conflict", [], 1, 0),
        ("same", [], 0, 0),
        ("owned", [], 0, 1),
        ("deleted", ["app/gone.py"], 0, 0),
    ],
)
def test_merge_back(tmp_path, case, applied, conflicts, owned):
    dest, merged = _merge_case(tmp_path, case)
    assert merged["applied"]["INFRA"] == applied
    assert len(merged["conflicts"]) == conflicts
    assert len(merged["owned"]) == owned


@pytest.mark.parametrize("case,kept", [("conflict", "WRITER"), ("same", "BOTH")])
def test_a_conflict_is_recorded_by_path_and_never_overwritten(tmp_path, case, kept):
    dest, merged = _merge_case(tmp_path, case)
    assert (dest / "app/shared.py").read_text(encoding="utf-8") == kept
    for row in merged["conflicts"]:
        assert row == {
            "path": "app/shared.py",
            "kept": "BACKEND",
            "not_applied": "INFRA",
            "change": fan.CONFLICT,
        }


@pytest.mark.parametrize("case,on_disk", [("owned", "{}"), ("new", "{}")])
def test_an_owned_path_is_never_merged(tmp_path, case, on_disk):
    dest, _ = _merge_case(tmp_path, case)
    assert (dest / "docs/owned.json").read_text(encoding="utf-8") == on_disk


def _fake_launch(calls, *, fail=None, sleep_s=0.0):
    def launch(name, prompt, checkout, progress):
        calls.append((name, Path(checkout), "AFTER" in prompt, sorted(fan.tree_manifest(checkout))))
        if sleep_s:
            time.sleep(sleep_s)
        if name == fail:
            raise WorkerError("boom")
        _write(Path(checkout), f"{name.lower()}/done.txt", name)
        return SimpleNamespace(status="completed", rounds={"rounds": 2, "peak_rss_mb": 300.0})

    return launch


@pytest.mark.parametrize("fail,merged_total", [(None, 2), ("FRONTEND", 1)])
def test_fanout_runs_parallel_then_merges_then_serial(tmp_path, fail, merged_total):
    dest = tmp_path / "dest"
    _write(dest, "app/main.py", "M")
    calls, notes = [], []
    fo = fan.SpecialistFanout(
        dest,
        brief="B",
        launch=_fake_launch(calls, fail=fail),
        note=lambda text, **kw: notes.append((text, kw)),
        owned=(),
        workroot=tmp_path,
    )
    fo.start()
    _write(dest, "app/backend.py", "writer session")
    record = fo.finish()
    fo.cleanup()
    by_name = {c[0]: c for c in calls}
    assert sorted(by_name) == ["DEVOPS", "FRONTEND", "INFRA", "SECURITY"]
    assert [c[0] for c in calls[-2:]] == ["DEVOPS", "SECURITY"]
    # The parallel hats ran in their own worktrees, not the checkout.
    assert by_name["INFRA"][1] != dest and by_name["INFRA"][2] is False
    # The serial hats ran on the MERGED checkout.
    assert by_name["DEVOPS"][1] == dest and by_name["DEVOPS"][2] is True
    assert "infra/done.txt" in by_name["DEVOPS"][3]
    assert sum(record["merged"].values()) == merged_total
    assert record["sessions"]["INFRA"]["peak_rss_mb"] == 300.0
    assert (record["sessions"]["FRONTEND"]["status"] == "error") == (fail == "FRONTEND")
    assert any("specialist sessions:" in text for text, _ in notes)
    assert not any(p.name.startswith("specialists-") for p in tmp_path.iterdir())


@pytest.mark.parametrize("heartbeat_s,expect_beats", [(0.0, True), (1000.0, False)])
def test_join_narrates_while_specialists_run(tmp_path, heartbeat_s, expect_beats):
    dest = tmp_path / "dest"
    _write(dest, "app/main.py", "M")
    beats = []
    fo = fan.SpecialistFanout(
        dest,
        brief="B",
        launch=_fake_launch([], sleep_s=1.2),
        note=lambda *a, **k: None,
        owned=(),
        workroot=tmp_path,
        serial=(),
        join_heartbeat_s=heartbeat_s,
    )
    fo.start()
    fo.finish(lambda line, info: beats.append(line))
    fo.cleanup()
    assert bool([b for b in beats if b.startswith("specialists still working")]) == expect_beats


@pytest.mark.parametrize("turn_wall_s", [None, 900.0])
def test_start_for_writer_holds_the_writer_slot_and_runs_children_under_it(
    tmp_path, monkeypatch, turn_wall_s
):
    monkeypatch.setenv(PROCESS_CAP_ENV, "6")
    seen = []

    def fake_run(prompt, checkout, **kw):
        with kw["slot_scope"]():
            seen.append((dict(worker_slots_snapshot()), "turn_wall_s" in kw))
            _write(Path(checkout), "x/done.txt", "x")
        return SimpleNamespace(status="completed", rounds={})

    monkeypatch.setattr(codewhale_worker, "run_worker_job_with_stall_retry", fake_run)
    dest = tmp_path / "dest"
    _write(dest, "app/main.py", "M")
    tenant = _binding("held")
    with ExitStack() as stack:
        fo, held_scope = fan.start_for_writer(
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
            turn_wall_s=turn_wall_s,
        )
        with held_scope():
            assert worker_slots_snapshot()["by_tenant"] == {tenant.tenant_key: 1}
        fo.finish()
        fo.cleanup()
    assert worker_slots_snapshot()["total"] == 0
    assert len(seen) == 4
    for snap, passed in seen:
        assert snap["by_tenant"] == {tenant.tenant_key: 1}
        assert snap["total"] >= 2
        assert passed == (turn_wall_s is not None)


# -- the writer handler ------------------------------------------------------


def _handler_ctx(tmp_path):
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.workspace import RoleWorkspace

    ws = RoleWorkspace(BuildRole.WRITER, tmp_path / "build")
    return RoleContext(
        role=BuildRole.WRITER,
        workspace=ws,
        blueprint=SimpleNamespace(product_id="p", product_name="n", vertical="v", summary="s"),
        plan=None,
        state={"tenant_store": _binding("handler"), "session_id": "s"},
    )


@pytest.mark.parametrize("fanout,sessions", [("1", 5), ("", 1)])
def test_writer_handler_dispatches_the_specialists_only_when_on(
    tmp_path, monkeypatch, fanout, sessions
):
    from app.factory.build.roles_handlers import _run_writer_via_codewhale_worker

    monkeypatch.setenv(fan.FANOUT_ENV, fanout)
    monkeypatch.setenv(PROCESS_CAP_ENV, "6")
    prompts = []

    def fake_run(prompt, checkout, **kw):
        scope = kw.get("slot_scope")
        with scope() if scope is not None else nullcontext():
            prompts.append(prompt)
            _write(Path(checkout), f"app/actions/a{len(prompts)}.py", 'AUTHORED_BY = "codewhale exec"\n')
        return SimpleNamespace(
            status="completed",
            termination_reason="done",
            error_category=None,
            rounds={},
            tools=[],
            provider="deepseek",
            model="m",
            output="",
            error=None,
            to_dict=lambda: {"status": "completed"},
        )

    monkeypatch.setattr(codewhale_worker, "run_worker_job", fake_run)
    monkeypatch.setattr(
        "app.factory.build.roles_handlers._writer_slot_wait_left", lambda ctx: _within()
    )
    ctx = _handler_ctx(tmp_path)
    ctx.note = lambda *a, **k: None
    _run_writer_via_codewhale_worker(ctx)
    assert len(prompts) == sessions
    writer_prompt = next(p for p in prompts if "WRITER role" in p)
    assert (SPECIALISTS_HEAD in writer_prompt) == (fanout == "")
    assert ("writer_specialists" in ctx.state) == (fanout == "1")
    assert worker_slots_snapshot()["total"] == 0
