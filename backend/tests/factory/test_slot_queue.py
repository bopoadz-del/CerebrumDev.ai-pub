"""A full build slot QUEUES, never fails (owner, 2026-10-06).

Live 2026-10-06: the post-deploy smoke died "worker_concurrency_capped:
tenant_slots_exhausted" because a second build on the same account held the
one tenant slot. The rule: a job that finds no slot waits first-in-first-out
per tenant, its place narrated; the smoke principal has a reserved slot a
user build can never take; the only bound is the run's phase-wall ceiling.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from app.factory.build.codewhale_worker import (
    SLOT_WAIT_EXHAUSTED,
    SLOT_WAIT_OPEN,
    InProcessSlotCounter,
    WorkerError,
    reserved_slots,
)

FOREVER = lambda: None  # noqa: E731 -- an unbounded ceiling


def _holder(counter, key, *, process_cap, tenant_cap, reserved=False, log, release_evt, places=None):
    """One job: take a slot (waiting), record when it got it, hold until told."""

    def run():
        counter.acquire_waiting(
            key,
            process_cap=process_cap,
            tenant_cap=tenant_cap,
            reserved=reserved,
            time_left=FOREVER,
            on_wait=(lambda info: places.append((key, info["position"]))) if places is not None else None,
            poll_s=0.02,
        )
        log.append(key)
        release_evt.wait(5)
        counter.release(key)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def _wait_until(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_a_second_build_on_the_same_tenant_queues_then_runs():
    counter = InProcessSlotCounter()
    log, places = [], []
    first_done, second_done = threading.Event(), threading.Event()
    a = _holder(counter, "tenant", process_cap=4, tenant_cap=1, log=log, release_evt=first_done)
    assert _wait_until(lambda: log == ["tenant"])
    b = _holder(counter, "tenant", process_cap=4, tenant_cap=1, log=log, release_evt=second_done, places=places)
    assert _wait_until(lambda: places), "the second build must be told its place"
    assert places[0] == ("tenant", 1), places
    assert log == ["tenant"], "never two in the tenant's one slot"
    assert counter.queue_snapshot()["waiting"] == 1
    first_done.set()
    assert _wait_until(lambda: log == ["tenant", "tenant"]), "the queued build must start when the slot frees"
    second_done.set()
    a.join(2)
    b.join(2)
    assert counter.snapshot() == {"total": 0, "by_tenant": {}}
    assert counter.queue_snapshot() == {"waiting": 0, "reserved": 0}


def test_three_builds_start_in_the_order_they_asked():
    counter = InProcessSlotCounter()
    order = []
    events = [threading.Event() for _ in range(3)]
    threads = []
    for i, evt in enumerate(events):
        key = "tenant"

        def run(i=i, evt=evt):
            counter.acquire_waiting(
                key, process_cap=4, tenant_cap=1, time_left=FOREVER, poll_s=0.02
            )
            order.append(i)
            evt.wait(5)
            counter.release(key)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        threads.append(t)
        # Build i has joined: the first holds the slot, the rest are queued.
        assert _wait_until(lambda i=i: order == [0] and counter.queue_snapshot()["waiting"] == i)
    for i, evt in enumerate(events):
        assert _wait_until(lambda i=i: len(order) == i + 1)
        evt.set()
    for t in threads:
        t.join(2)
    assert order == [0, 1, 2]


def test_the_smoke_principal_starts_while_user_builds_fill_the_box():
    """process_cap 2 -> one slot is reserved. Users may hold one; a second
    user waits; the smoke principal takes the reserved slot at once."""
    assert reserved_slots(2) == 1
    counter = InProcessSlotCounter()
    log, places = [], []
    hold = threading.Event()
    _holder(counter, "user-a", process_cap=2, tenant_cap=1, log=log, release_evt=hold)
    assert _wait_until(lambda: log == ["user-a"])
    _holder(counter, "user-b", process_cap=2, tenant_cap=1, log=log, release_evt=hold, places=places)
    assert _wait_until(lambda: places), "a second user build waits: the last slot is the smoke's"
    _holder(counter, "smoke", process_cap=2, tenant_cap=1, reserved=True, log=log, release_evt=hold)
    assert _wait_until(lambda: "smoke" in log), "the smoke principal must start at once"
    assert "user-b" not in log
    hold.set()
    assert _wait_until(lambda: "user-b" in log)


def test_on_a_one_slot_box_the_smoke_is_served_before_waiting_users():
    assert reserved_slots(1) == 0
    counter = InProcessSlotCounter()
    log = []
    first = threading.Event()
    later = threading.Event()
    _holder(counter, "user-a", process_cap=1, tenant_cap=1, log=log, release_evt=first)
    assert _wait_until(lambda: log == ["user-a"])
    _holder(counter, "user-b", process_cap=1, tenant_cap=1, log=log, release_evt=later)
    assert _wait_until(lambda: counter.queue_snapshot()["waiting"] == 1)
    _holder(counter, "smoke", process_cap=1, tenant_cap=1, reserved=True, log=log, release_evt=later)
    assert _wait_until(lambda: counter.queue_snapshot()["waiting"] == 2)
    first.set()
    assert _wait_until(lambda: len(log) == 2)
    assert log[1] == "smoke", log
    later.set()


def test_a_wait_past_the_ceiling_stops_with_a_typed_timeout():
    counter = InProcessSlotCounter()
    counter.acquire_waiting("tenant", process_cap=4, tenant_cap=1, time_left=FOREVER)
    with pytest.raises(WorkerError) as exc:
        counter.acquire_waiting(
            "tenant", process_cap=4, tenant_cap=1, time_left=lambda: 0.0, poll_s=0.01
        )
    assert str(exc.value).startswith(SLOT_WAIT_EXHAUSTED)
    assert "slots_exhausted" not in str(exc.value)
    assert counter.queue_snapshot()["waiting"] == 0, "a departed waiter leaves the queue"
    counter.release("tenant")


def test_the_queue_place_reaches_the_build_status():
    from app.factory.build_jobs import queued_for_slot

    notes = [
        SimpleNamespace(payload={"slot_wait": SLOT_WAIT_OPEN, "slot_queue": {"position": 2, "ahead": 1, "since_s": 5}}, ts="t1"),
    ]
    assert queued_for_slot(notes) == {"position": 2, "ahead": 1, "since": "t1", "waited_s": 5}
    closed = notes + [SimpleNamespace(payload={"slot_wait": "closed"}, ts="t2")]
    assert queued_for_slot(closed) is None
    running = notes + [SimpleNamespace(payload={"model_call": True}, ts="t3")]
    assert queued_for_slot(running) is None


def test_a_queued_writer_counts_as_live_work_for_the_budget():
    """The phase box must not kill a queued writer: its wait is bounded by
    the ceiling only, so the ramp and the stage inspect see it as live."""
    from app.factory.build.budget_inspect import _cli_flight

    queued = [SimpleNamespace(payload={"slot_wait": SLOT_WAIT_OPEN, "source": "codewhale_worker"})]
    assert _cli_flight(queued, {})["slot_waiting"] is True
    acquired = queued + [SimpleNamespace(payload={"slot_wait": "closed", "source": "codewhale_worker"})]
    assert _cli_flight(acquired, {})["slot_waiting"] is False
    # A slot_wait field from a non-agent source is not the writer's wait.
    foreign = [SimpleNamespace(payload={"slot_wait": SLOT_WAIT_OPEN, "source": "factory"})]
    assert _cli_flight(foreign, {})["slot_waiting"] is False


def test_the_reserved_slot_follows_the_authenticated_account(monkeypatch):
    """The binding marks the smoke gate's own principals reserved, derived
    server-side from the stored account -- never from a caller field."""
    from app.core import accounts_store
    from app.core.trial_limits import SMOKE_PRINCIPAL_A
    from app.factory.build import tenant_bind
    from app.factory.build.codewhale_worker import tenant_reserved

    emails = {"acct-smoke": SMOKE_PRINCIPAL_A, "acct-user": "someone@example.test"}
    monkeypatch.setattr(
        accounts_store, "subscription_fields", lambda aid: {"email": emails.get(aid)}
    )
    smoke = tenant_bind.bind_tenant_store("acct-smoke")
    user = tenant_bind.bind_tenant_store("acct-user")
    assert smoke.reserved is True and tenant_reserved(smoke) is True
    assert user.reserved is False and tenant_reserved(user) is False
    # A look-alike handle that merely claims reservation is not trusted unless
    # the attribute is exactly True on the bound object.
    assert tenant_reserved(SimpleNamespace(reserved="yes")) is False
