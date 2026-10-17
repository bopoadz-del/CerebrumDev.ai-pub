"""Two-axis worker concurrency: the platform serves many tenants at once.

CerebrumDev.ai is a multi-tenant Factory: every account gets its own shell
and the coder deploys its own agents inside it. Concurrency accounting
therefore has TWO dimensions, and the tests below are the gate on both:

    process cap -- host protection. Total in flight on this instance.
    tenant cap  -- fairness. In flight for ONE bound tenant.

Before the fix these were the same number (a single module-level
``_active_jobs`` int), so ONE tenant holding the only slot refused every
other tenant on the platform. The tests named "does_not_refuse" and
"cannot_take_every" fail on that code.

STUB-DRIVEN: no ``codewhale`` subprocess, no network. Tenants are REAL
``bind_tenant_store`` bindings — the same server-derived sha256 digests
production uses — never a hand-rolled fake, so the key under test is the
key the worker actually receives.
"""

from __future__ import annotations

import threading
from contextlib import ExitStack
from pathlib import Path

import pytest
import yaml

from app.factory.build import codewhale_worker as worker_mod
from app.factory.build.codewhale_worker import (
    DEFAULT_PROCESS_CAP,
    DEFAULT_TENANT_CAP,
    DEPLOYED_PROFILE,
    NO_AUTHENTICATED_TENANT,
    PROCESS_CAP_ENV,
    PROCESS_SLOTS_EXHAUSTED,
    PROFILE_ENV,
    TENANT_CAP_ENV,
    TENANT_SLOTS_EXHAUSTED,
    UNKEYED_TENANT_HANDLE,
    WORKER_CAP_MALFORMED,
    WORKER_CONCURRENCY_CAPPED,
    WORKER_PROFILES,
    WORKER_PROFILE_UNSUPPORTED,
    InProcessSlotCounter,
    WorkerError,
    set_slot_counter,
    worker_job_slot,
    worker_process_cap,
    worker_slots_snapshot,
    worker_tenant_cap,
)
from app.factory.build.tenant_bind import bind_tenant_store

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean_slots(monkeypatch):
    """A fresh counter and a clean env for every test.

    Installed through the documented seam rather than by reaching into
    module globals — which is also what proves the seam is real.
    """
    for name in (PROCESS_CAP_ENV, TENANT_CAP_ENV, PROFILE_ENV):
        monkeypatch.delenv(name, raising=False)
    previous = set_slot_counter(InProcessSlotCounter())
    try:
        yield
    finally:
        set_slot_counter(previous)


def _tenant(n):
    """A REAL bound handle for a distinct account."""
    binding = bind_tenant_store(f"acct_{n}")
    assert binding is not None
    return binding


# ---------------------------------------------------------------------------
# THE BUG: one tenant must not starve the platform
# ---------------------------------------------------------------------------


def test_tenant_at_its_cap_does_not_refuse_another_tenant(monkeypatch):
    """THE production defect, with both knobs UNSET — the live Render config.

    FACTORY_CODEWHALE_WORKER_CAP is not among the live service's env vars,
    so the code default IS production. alpha holds its only slot; beta is a
    different account and the box has room. beta must be SERVED.
    """
    alpha, beta = _tenant("alpha"), _tenant("beta")
    with worker_job_slot(alpha):
        assert worker_slots_snapshot()["total"] == 1
        with worker_job_slot(beta):
            snap = worker_slots_snapshot()
            assert snap["total"] == 2
            assert snap["by_tenant"][alpha.tenant_key] == 1
            assert snap["by_tenant"][beta.tenant_key] == 1


def test_one_tenant_cannot_take_every_process_slot(monkeypatch):
    """The anti-cheat for the test above.

    Merely bumping the old single cap from 1 to 3 would satisfy
    "beta is served". It cannot satisfy this: the hog must be stopped at
    its OWN limit while the box still has two free slots, and a different
    account must still get in. That requires real per-tenant accounting.
    """
    monkeypatch.setenv(PROCESS_CAP_ENV, "3")
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    hog, other = _tenant("hog"), _tenant("other")

    with worker_job_slot(hog):
        with pytest.raises(WorkerError) as exc:
            with worker_job_slot(hog):
                pass
        assert TENANT_SLOTS_EXHAUSTED in str(exc.value)
        # The box is NOT full — two slots are free and another account
        # takes one of them.
        with worker_job_slot(other):
            assert worker_slots_snapshot()["total"] == 2


# ---------------------------------------------------------------------------
# The process cap still protects the host
# ---------------------------------------------------------------------------


def test_process_cap_refuses_when_the_instance_is_full(monkeypatch):
    monkeypatch.setenv(PROCESS_CAP_ENV, "2")
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    a, b, c = _tenant("a"), _tenant("b"), _tenant("c")

    with worker_job_slot(a), worker_job_slot(b):
        with pytest.raises(WorkerError) as exc:
            with worker_job_slot(c):
                pass
        message = str(exc.value)
        assert WORKER_CONCURRENCY_CAPPED in message
        assert PROCESS_SLOTS_EXHAUSTED in message
        assert "scope=process" in message
        assert "2/2" in message


def test_a_released_slot_is_reusable(monkeypatch):
    monkeypatch.setenv(PROCESS_CAP_ENV, "1")
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    a, b = _tenant("a"), _tenant("b")

    release_holder = threading.Event()
    holding = threading.Event()
    failed: list = []

    def hold():
        try:
            with worker_job_slot(a):
                holding.set()
                # Released by an explicit signal, never by a fixed sleep:
                # a 50-tenant suite cannot afford blind waits.
                assert release_holder.wait(timeout=30), "holder was never released"
        except Exception as exc:  # pragma: no cover - surfaced by the assert
            failed.append(exc)
            holding.set()

    holder = threading.Thread(target=hold, name="slot-holder")
    holder.start()
    assert holding.wait(timeout=30)
    assert not failed, failed

    with pytest.raises(WorkerError) as exc:
        with worker_job_slot(b):
            pass
    assert PROCESS_SLOTS_EXHAUSTED in str(exc.value)

    release_holder.set()
    holder.join(timeout=30)
    assert not holder.is_alive()
    assert not failed, failed
    assert worker_slots_snapshot() == {"total": 0, "by_tenant": {}}

    with worker_job_slot(b):
        assert worker_slots_snapshot()["total"] == 1


# ---------------------------------------------------------------------------
# The two exhaustion modes are distinguishable from one log line
# ---------------------------------------------------------------------------


def test_tenant_and_process_exhaustion_are_distinguishable(monkeypatch):
    """An operator greps one line and knows whose problem it is."""
    monkeypatch.setenv(PROCESS_CAP_ENV, "4")
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    solo = _tenant("solo")

    with worker_job_slot(solo):
        with pytest.raises(WorkerError) as exc:
            with worker_job_slot(solo):
                pass
    tenant_message = str(exc.value)

    # Same contract token leads BOTH messages — the documented refusal is
    # preserved, the scope is added alongside it.
    assert tenant_message.startswith(WORKER_CONCURRENCY_CAPPED)
    assert TENANT_SLOTS_EXHAUSTED in tenant_message
    assert "scope=tenant" in tenant_message
    # The box is nowhere near full (1 of 4), so this must NOT read as
    # process exhaustion.
    assert PROCESS_SLOTS_EXHAUSTED not in tenant_message
    assert "scope=process" not in tenant_message
    # Both counters are reported, so one line answers "my problem or
    # theirs".
    assert "1/1" in tenant_message and "1/4" in tenant_message


def test_process_exhaustion_does_not_read_as_a_tenant_limit(monkeypatch):
    monkeypatch.setenv(PROCESS_CAP_ENV, "1")
    monkeypatch.setenv(TENANT_CAP_ENV, "4")
    a, b = _tenant("a"), _tenant("b")

    with worker_job_slot(a):
        with pytest.raises(WorkerError) as exc:
            with worker_job_slot(b):
                pass
    message = str(exc.value)
    assert message.startswith(WORKER_CONCURRENCY_CAPPED)
    assert PROCESS_SLOTS_EXHAUSTED in message
    assert TENANT_SLOTS_EXHAUSTED not in message
    assert "scope=tenant" not in message


# ---------------------------------------------------------------------------
# Correctness at 50 tenants
# ---------------------------------------------------------------------------


def test_fifty_distinct_tenants_are_all_served(monkeypatch):
    """The owner's requirement, exercised: 50 concurrent tenants.

    With 64 process slots every one of 50 distinct accounts holds a slot at
    the same time. The 51st job is the FIRST tenant's SECOND job, which is
    refused for FAIRNESS while process slots remain free — the machinery is
    serving 50 tenants, not 50 jobs for whoever asks first. (No single plan
    carries 64 under the measured CPU rule; the explicit cap is the operator
    knob that sizes the slot counter.)
    """
    monkeypatch.setenv(PROCESS_CAP_ENV, "64")
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    assert worker_process_cap() >= 50

    tenants = [_tenant(i) for i in range(50)]
    assert len({t.tenant_key for t in tenants}) == 50

    with ExitStack() as stack:
        for t in tenants:
            stack.enter_context(worker_job_slot(t))
        snap = worker_slots_snapshot()
        assert snap["total"] == 50
        assert len(snap["by_tenant"]) == 50
        assert set(snap["by_tenant"].values()) == {1}

        # Process slots remain (64 > 50) yet tenant 0 is still held to its
        # own limit: fairness is enforced independently of host capacity.
        with pytest.raises(WorkerError) as exc:
            with worker_job_slot(tenants[0]):
                pass
        assert TENANT_SLOTS_EXHAUSTED in str(exc.value)

    assert worker_slots_snapshot() == {"total": 0, "by_tenant": {}}


def test_counters_return_to_empty_after_fifty_tenants_churn(monkeypatch):
    """No leak: the per-tenant map is bounded by tenants CURRENTLY holding
    slots, never by the number of tenants ever seen. An entry that is
    decremented to zero but not popped is unbounded dict growth keyed by
    tenant digest."""
    monkeypatch.setenv(PROCESS_CAP_ENV, "50")
    monkeypatch.setenv(TENANT_CAP_ENV, "1")

    for _round in range(3):
        for i in range(50):
            with worker_job_slot(_tenant(i)):
                pass
        assert worker_slots_snapshot() == {"total": 0, "by_tenant": {}}

    # And once more with all 50 concurrent, to prove the pop happens on the
    # concurrent path too, not only on the trivially-sequential one.
    tenants = [_tenant(i) for i in range(50)]
    with ExitStack() as stack:
        for t in tenants:
            stack.enter_context(worker_job_slot(t))
        assert len(worker_slots_snapshot()["by_tenant"]) == 50
    assert worker_slots_snapshot() == {"total": 0, "by_tenant": {}}


def test_fifty_tenants_acquiring_concurrently_do_not_corrupt_the_counters(
    monkeypatch,
):
    """The lock is the whole mechanism — exercise it from 50 threads."""
    monkeypatch.setenv(PROCESS_CAP_ENV, "50")
    monkeypatch.setenv(TENANT_CAP_ENV, "1")

    all_in = threading.Barrier(51, timeout=60)
    release = threading.Event()
    errors: list = []

    def run(i):
        try:
            with worker_job_slot(_tenant(i)):
                all_in.wait()
                assert release.wait(timeout=60)
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
            try:
                all_in.wait()
            except threading.BrokenBarrierError:
                pass

    threads = [threading.Thread(target=run, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    all_in.wait()
    assert not errors, errors
    snap = worker_slots_snapshot()
    assert snap["total"] == 50
    assert len(snap["by_tenant"]) == 50

    release.set()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive()
    assert not errors, errors
    assert worker_slots_snapshot() == {"total": 0, "by_tenant": {}}


# ---------------------------------------------------------------------------
# Release discipline
# ---------------------------------------------------------------------------


def test_an_exception_inside_the_slot_still_releases_it(monkeypatch):
    monkeypatch.setenv(PROCESS_CAP_ENV, "1")
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    solo = _tenant("solo")

    with pytest.raises(ValueError):
        with worker_job_slot(solo):
            raise ValueError("the writer blew up mid-job")

    assert worker_slots_snapshot() == {"total": 0, "by_tenant": {}}
    # The same tenant can acquire again — the slot was not leaked.
    with worker_job_slot(solo):
        assert worker_slots_snapshot()["total"] == 1


def test_a_refused_job_never_consumes_a_slot(monkeypatch):
    """The refusal path must not mutate the counters."""
    monkeypatch.setenv(PROCESS_CAP_ENV, "1")
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    a, b = _tenant("a"), _tenant("b")

    with worker_job_slot(a):
        for _ in range(5):
            with pytest.raises(WorkerError):
                with worker_job_slot(b):
                    pass
        snap = worker_slots_snapshot()
        assert snap["total"] == 1
        assert snap["by_tenant"] == {a.tenant_key: 1}


# ---------------------------------------------------------------------------
# The tenant boundary
# ---------------------------------------------------------------------------


def test_an_unbound_tenant_is_still_refused():
    """A preserved contract, not a bug-prover: this passes before and after
    the concurrency fix. Its RED-ability comes from the P5 mutation probe,
    which deletes _require_bound_tenant."""
    with pytest.raises(WorkerError) as exc:
        with worker_job_slot(None):
            pass
    assert NO_AUTHENTICATED_TENANT in str(exc.value)
    assert worker_slots_snapshot() == {"total": 0, "by_tenant": {}}


def test_a_handle_with_no_tenant_key_is_a_named_refusal():
    """LOAD-BEARING anti-vacuity guard.

    If key derivation fell back to a shared bucket (``getattr(h,
    "tenant_key", "")``) every unkeyed handle would collapse onto ONE key
    and the 50-tenant tests above would pass while proving nothing. The key
    comes off the bound handle or the job is refused by name.
    """

    class _Unkeyed:
        tenant_id = "tenant_a"
        digest = "d1"
        name = "whatever-the-caller-said"

    for handle in (object(), _Unkeyed(), type("E", (), {"tenant_key": ""})()):
        with pytest.raises(WorkerError) as exc:
            with worker_job_slot(handle):
                pass
        assert UNKEYED_TENANT_HANDLE in str(exc.value)
    assert worker_slots_snapshot() == {"total": 0, "by_tenant": {}}


def test_the_slot_key_is_the_bound_digest_and_nothing_else():
    """Two handles for the SAME account share a bucket; two accounts never
    do. The bucket is the server-derived digest, never a supplied name."""
    first = bind_tenant_store("acct_same")
    again = bind_tenant_store("acct_same")
    other = bind_tenant_store("acct_other")
    assert first is not None and again is not None and other is not None

    with worker_job_slot(first), worker_job_slot(other):
        snap = worker_slots_snapshot()
        assert snap["by_tenant"] == {first.tenant_key: 1, other.tenant_key: 1}
        assert again.tenant_key == first.tenant_key
        assert other.tenant_key != first.tenant_key


# ---------------------------------------------------------------------------
# The operating profile is honest, and the upgrade is config-only
# ---------------------------------------------------------------------------


def test_live_1c2g_profile_caps_at_three_process_one_per_tenant():
    """With BOTH knobs unset — the live Render config — the code default is
    what production receives. 3 is the 2 GB memory budget, not a guess."""
    assert worker_process_cap() == 3
    assert worker_tenant_cap() == 1
    assert DEFAULT_PROCESS_CAP == 3
    assert DEFAULT_TENANT_CAP == 1
    assert WORKER_PROFILES[DEPLOYED_PROFILE] == (3, 1)


def test_the_upgrade_is_config_only(monkeypatch):
    """ONE env var moves the box to a bigger plan's slots. No code edit."""
    assert worker_process_cap() == DEFAULT_PROCESS_CAP

    monkeypatch.setenv(PROFILE_ENV, "2c-4g")
    assert worker_process_cap() > DEFAULT_PROCESS_CAP
    assert worker_tenant_cap() >= 1


def test_every_plan_cap_is_derived_by_the_one_rule():
    """No cap is typed: each plan's numbers come from its vCPU and RAM."""
    for plan, (vcpu, ram_mb) in worker_mod.WORKER_PLANS.items():
        assert WORKER_PROFILES[plan] == worker_mod.derive_profile_caps(vcpu, ram_mb), plan


def test_two_vcpu_gives_four_user_builds_plus_the_reserved_smoke():
    process, tenant = WORKER_PROFILES["2c-4g"]
    assert process == 5
    assert process - worker_mod.reserved_slots(process) == 4
    assert tenant == 1


def test_one_vcpu_keeps_todays_numbers():
    process, tenant = WORKER_PROFILES[DEPLOYED_PROFILE]
    assert (process, tenant) == (DEFAULT_PROCESS_CAP, DEFAULT_TENANT_CAP) == (3, 1)
    assert process - worker_mod.reserved_slots(process) == 2


def test_cpu_binds_once_memory_outgrows_it():
    # Live 2026-10-08: three jobs held one vCPU at 85-99 %. A plan with RAM
    # for many writers but few cores must not claim the RAM's number.
    process, _ = WORKER_PROFILES["4c-16g"]
    assert process == worker_mod.USER_BUILDS_PER_VCPU * 4 + 1
    assert process < (16384 - worker_mod.BASE_MB - worker_mod.HEADROOM_MB) // worker_mod.PER_JOB_MB


def test_every_documented_profile_is_selectable_by_env_alone(monkeypatch):
    for plan, caps in WORKER_PROFILES.items():
        monkeypatch.setenv(PROFILE_ENV, plan)
        if caps is None:
            with pytest.raises(WorkerError) as exc:
                worker_process_cap()
            assert WORKER_PROFILE_UNSUPPORTED in str(exc.value)
            continue
        process, tenant = caps
        assert worker_process_cap() == process
        assert worker_tenant_cap() == tenant
        assert tenant >= 1
        assert tenant <= process


def test_an_explicit_cap_overrides_the_profile(monkeypatch):
    monkeypatch.setenv(PROFILE_ENV, "8c-32g")
    monkeypatch.setenv(PROCESS_CAP_ENV, "5")
    assert worker_process_cap() == 5
    # The unset knob still follows the profile.
    assert worker_tenant_cap() == WORKER_PROFILES["8c-32g"][1]


def test_the_starter_plan_is_refused_not_silently_capped(monkeypatch):
    """512 MB cannot host a writer child at all. Emitting "cap 1" for it
    would claim a capacity the box cannot serve."""
    monkeypatch.setenv(PROFILE_ENV, "starter")
    with pytest.raises(WorkerError) as exc:
        worker_process_cap()
    assert WORKER_PROFILE_UNSUPPORTED in str(exc.value)


@pytest.mark.parametrize("bad", ["0", "-1", "three", "3O", "1.5", " "])
def test_a_malformed_cap_is_a_named_refusal_not_a_valueerror(monkeypatch, bad):
    """One typo in a Render env var must not take the Factory down with a
    log line that says ValueError. A zero cap refuses every job including
    the first, so it must not even be expressible."""
    monkeypatch.setenv(PROCESS_CAP_ENV, bad)
    if not bad.strip():
        # An empty value is "unset", not malformed.
        assert worker_process_cap() == DEFAULT_PROCESS_CAP
        return
    with pytest.raises(WorkerError) as exc:
        worker_process_cap()
    assert WORKER_CAP_MALFORMED in str(exc.value)


# ---------------------------------------------------------------------------
# The multi-instance seam
# ---------------------------------------------------------------------------


def test_the_slot_backend_is_swappable(monkeypatch):
    """numInstances is 1 today so an in-process counter is CORRECT and no
    Redis belongs here. When numInstances > 1 each instance independently
    admits its own cap and the counter must become shared state — so the
    seam must actually ROUTE, not merely exist."""
    calls: list = []

    class _RecordingCounter:
        def __init__(self):
            self.inner = InProcessSlotCounter()

        def acquire(self, key, *, process_cap, tenant_cap, holder=None):
            calls.append(("acquire", key, process_cap, tenant_cap))
            self.inner.acquire(
                key, process_cap=process_cap, tenant_cap=tenant_cap, holder=holder
            )

        def release(self, key):
            calls.append(("release", key))
            self.inner.release(key)

        def snapshot(self):
            return self.inner.snapshot()

    monkeypatch.setenv(PROCESS_CAP_ENV, "7")
    monkeypatch.setenv(TENANT_CAP_ENV, "2")
    a, b = _tenant("a"), _tenant("b")
    set_slot_counter(_RecordingCounter())

    with worker_job_slot(a):
        with worker_job_slot(b):
            pass

    assert [c[0] for c in calls] == ["acquire", "acquire", "release", "release"]
    assert calls[0][1] == a.tenant_key
    assert calls[1][1] == b.tenant_key
    # The caps are resolved by the worker and HANDED to the backend, so a
    # shared-state implementation inherits the same precedence rules.
    assert calls[0][2:] == (7, 2)
    assert worker_slots_snapshot() == {"total": 0, "by_tenant": {}}


def test_the_seam_comment_names_the_condition_that_forces_the_swap():
    """A seam with no stated trigger is a seam nobody uses in time."""
    import inspect

    source = inspect.getsource(worker_mod)
    assert "numInstances > 1" in source
    assert "MULTI-INSTANCE SEAM" in source


# ---------------------------------------------------------------------------
# The declared production configuration
# ---------------------------------------------------------------------------


def _backend_service():
    data = yaml.safe_load((REPO_ROOT / "render.yaml").read_text(encoding="utf-8"))
    return next(s for s in data["services"] if s.get("name") == "cerebrumdev-backend")


def test_render_yaml_declares_the_operating_profile_as_a_literal():
    """render.yaml is not an applied blueprint, so this declaration is
    DOCUMENTATION and the code default is the remediation. It still has to
    be right: a ``sync: false`` knob would leave the intended operating
    capacity un-greppable, which is how production came to inherit a cap of
    1 that nobody chose.

    Only the PROFILE is declared. The two cap vars override it, so
    declaring them here too would make the profile inert and let the two
    drift apart — an operator upgrading by changing only the profile would
    watch nothing happen.
    """
    by_key = {v["key"]: v for v in _backend_service()["envVars"]}

    assert by_key[PROFILE_ENV].get("value") == DEPLOYED_PROFILE
    assert "sync" not in by_key[PROFILE_ENV]
    assert PROCESS_CAP_ENV not in by_key
    assert TENANT_CAP_ENV not in by_key


def test_the_declared_profile_resolves_to_the_caps_the_file_claims(monkeypatch):
    """The file and the table can never drift: whatever profile render.yaml
    declares must resolve to the code defaults this box was sized for."""
    by_key = {v["key"]: v for v in _backend_service()["envVars"]}
    monkeypatch.setenv(PROFILE_ENV, by_key[PROFILE_ENV]["value"])
    assert worker_process_cap() == DEFAULT_PROCESS_CAP
    assert worker_tenant_cap() == DEFAULT_TENANT_CAP


def test_render_yaml_does_not_declare_a_plan_that_cannot_host_a_writer():
    """The file declared ``plan: starter`` (0.5 vCPU / 512 MB) while the
    live service runs 1c-2g. Syncing this blueprint to pick up the new cap
    vars would have silently DOWNGRADED production to a box with no room
    for a single Node writer child."""
    assert WORKER_PROFILES.get("starter") is None
    assert _backend_service().get("plan") != "starter"


# ---------------------------------------------------------------------------
# The refusal names the holder (live 2026-09-30, automotive re-run):
# "tenant f714a7c0 holds 1/1" told the owner nothing about WHICH of their
# builds held the slot -- a dead-end message on a phone screen.
# ---------------------------------------------------------------------------


def test_a_tenant_refusal_names_the_holding_build(monkeypatch):
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    solo = _tenant("solo")
    with worker_job_slot(
        solo, holder={"session_id": "sess_a1b2", "product_id": "automotive-aiops"}
    ):
        with pytest.raises(WorkerError) as exc:
            with worker_job_slot(
                solo, holder={"session_id": "sess_new", "product_id": "retry"}
            ):
                raise AssertionError("second slot must refuse")
        msg = str(exc.value)
        assert TENANT_SLOTS_EXHAUSTED in msg
        assert "sess_a1b2" in msg
        assert "automotive-aiops" in msg


def test_a_released_holder_no_longer_blocks_or_appears(monkeypatch):
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    solo = _tenant("solo2")
    with worker_job_slot(solo, holder={"session_id": "sess_gone", "product_id": "p1"}):
        pass
    # Slot free again; the old holder must not leak into a later refusal.
    with worker_job_slot(solo, holder={"session_id": "sess_live", "product_id": "p2"}):
        with pytest.raises(WorkerError) as exc:
            with worker_job_slot(solo):
                raise AssertionError("second slot must refuse")
        assert "sess_gone" not in str(exc.value)
        assert "sess_live" in str(exc.value)


def test_a_holderless_acquire_still_refuses_cleanly(monkeypatch):
    """Backends and callers that pass no holder keep the old message shape."""
    monkeypatch.setenv(TENANT_CAP_ENV, "1")
    solo = _tenant("solo3")
    with worker_job_slot(solo):
        with pytest.raises(WorkerError) as exc:
            with worker_job_slot(solo):
                raise AssertionError("second slot must refuse")
        assert TENANT_SLOTS_EXHAUSTED in str(exc.value)
