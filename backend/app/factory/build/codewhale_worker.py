"""The CodeWhale writer worker (Phase 5.1/5.4).

Runs the CodeWhale (DeepSeek) coding agent headless via ``codewhale exec``
from a job queue, under the tenant-scoped store handle (Phase 1 isolation
applies to the builder too), behind an explicit concurrency cap.

CONCURRENCY IS TWO-DIMENSIONAL. CerebrumDev.ai is multi-tenant: every
account gets its own shell and the coder deploys its own agents inside
it. A single process-wide counter cannot express that, so slots are
accounted on two independent axes:

    process cap  -- HOST PROTECTION. Total jobs in flight on this
                    instance, across every tenant. Sized from the box's
                    memory budget (see WORKER_PROFILES).
    tenant cap   -- FAIRNESS. Jobs in flight for ONE bound tenant. Stops
                    one account consuming every slot on the box.

Both refusals are NAMED and HARD: the job is refused, never silently run
and never silently queued, and the two exhaustion modes carry different
reason tokens so an operator reading a build log can tell "this user is
at their limit" from "the box is full".

VERIFIED (Phase 5.1): ``codewhale exec --auto --json`` completes a trivial
file job non-interactively -- agent mode, machine-readable summary,
termination_reason "resolved", no approval-prompt hang. The local probe
result is recorded in the PR report; T5.1 re-runs it wherever the CLI
exists and SKIPS with a reason elsewhere (CI has no codewhale binary).

The receipt records what the CLI actually reports: status,
termination_reason, provider, model, tool outcomes, output. Token-level
COGS is a named gap -- the CLI summary does not emit usage, so no cost
number is invented.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from app.factory.build.agent_process import agent_popen_kwargs, kill_agent_tree, track_agent

logger = logging.getLogger("cerebrumdev.factory.codewhale_worker")

WORKER_CONCURRENCY_CAPPED = "worker_concurrency_capped"
WORKER_DISPATCH_AMBIGUOUS = "worker_dispatch_ambiguous"
WORKER_CLI_MISSING = "worker_cli_missing"
WORKER_EXEC_FAILED = "worker_exec_failed"
WORKER_TIMED_OUT = "worker_timed_out"

#: The close half of the model-call NOTE. build_status treats the "CLI
#: started" NOTE as an in-flight coder call until a NOTE whose detail
#: carries "FACTORY_CODE_CLI session finished" lands (_open_model_call_note);
#: budget_inspect closes on the same text. Only the C-BRIEF dispatch layer
#: ever emitted it, so on every codewhale build the call stayed open forever
#: and 1800s after the CLI STARTED the Floor declared the build stopped --
#: live 2026-09-28: "coder LLM timed out after 2595s (deadline 1800s)" over
#: a run whose writer had finished and whose tester was still working.
from app.factory.build.model_call import CLOSED, MODEL_CALL_STATE  # noqa: E402

MODEL_CALL_CLOSED_DETAIL = (
    "FACTORY_CODE_CLI session finished — codewhale writer CLI exited"
)

#: The two exhaustion modes behind WORKER_CONCURRENCY_CAPPED. The capped
#: token stays the LEADING token of both messages -- the documented
#: contract is preserved, the scope is ADDED. Grep one or the other to
#: separate "the box is full" (page someone) from "this user hit their
#: own limit" (the box may be idle; nobody else is affected).
PROCESS_SLOTS_EXHAUSTED = "process_slots_exhausted"
TENANT_SLOTS_EXHAUSTED = "tenant_slots_exhausted"

#: A full slot never fails a build (owner, 2026-10-06): a job that cannot get
#: a slot WAITS, first-in-first-out per tenant, its position narrated on the
#: Floor and in the ledger. The only bound is the run's own phase-wall
#: ceiling; when that is spent the job stops with this typed reason -- a
#: timeout, never "slots full". Live 2026-10-06: the post-deploy smoke died
#: "worker_concurrency_capped: tenant_slots_exhausted" because a second build
#: on the same account held the slot.
SLOT_WAIT_EXHAUSTED = "slot_wait_exhausted"
#: The typed ``slot_wait`` NOTE payload: a queued writer is OPEN until it
#: holds its slot (CLOSED). The budget ramp and the Floor read this field,
#: never the NOTE's prose.
SLOT_WAIT_OPEN = "open"
SLOT_WAIT_CLOSED = "closed"
#: How often a waiting job re-checks (the counter is also notified on every
#: release, so this is only the narration / deadline cadence).
SLOT_WAIT_POLL_S = 15.0


#: A handle that carries no server-derived tenant key cannot be accounted
#: for fairly, so it is refused rather than dropped into a shared bucket.
#: Fail-closed, same trust boundary as NO_AUTHENTICATED_TENANT.
UNKEYED_TENANT_HANDLE = "unkeyed_tenant_handle"

#: A cap the operator typed wrong is an UNKNOWN operating capacity. The
#: worker refuses by name rather than silently substituting a default --
#: an operator who typed "3O" must not be told the box is running at 3.
WORKER_CAP_MALFORMED = "worker_cap_malformed"
#: A profile naming a box that cannot host a writer child at all.
WORKER_PROFILE_UNSUPPORTED = "worker_profile_unsupported"
WORKER_PROFILE_UNKNOWN = "worker_profile_unknown"

#: Phase 1's named refusal, mirrored here so the builder's isolation
#: boundary uses the same reason string as the runtime seam.
NO_AUTHENTICATED_TENANT = "no_authenticated_tenant"

# ---------------------------------------------------------------------------
# THE MEMORY BUDGET -- recompute these three numbers before raising a cap.
# ---------------------------------------------------------------------------
# The live cerebrumdev-backend runs on Render plan 1c-2g (1 vCPU / 2 GB,
# numInstances=1, autoscaling=None -- read from the Render API 2026-09-16).
#
#   BASE_MB     = 500  uvicorn + FastAPI + chromadb + the app import graph,
#                      resident before any build starts (~400-500 MB
#                      measured; 500 is the pessimistic figure).
#   HEADROOM_MB = 150  Render overhead, page cache, and the per-build Python
#                      objects the build THREAD itself holds (workspace tree,
#                      blueprint, ledger buffers).
#   PER_JOB_MB  = 400  One `codewhale exec` Node child at peak RSS. THIS IS
#                      THE NUMBER THAT MOVES THE TABLE MOST and it is an
#                      ESTIMATE, not a measurement -- a Node agent CLI with
#                      loaded tool/context state typically lands near 300 MB.
#                      MEASURE IT before raising any cap.
#
#   process_cap = min( floor((RAM_MB - BASE_MB - HEADROOM_MB) / PER_JOB_MB),
#                      USER_BUILDS_PER_VCPU * vCPU + reserved_slots )
#
# The CPU bound is MEASURED, not assumed. It used to be 8 jobs/vCPU on the
# theory that the children only wait on DeepSeek HTTP; live 2026-10-08 (1c-2g,
# release cycle on 0e50fcc1) a smoke build plus two repro builds -- three jobs
# -- held the single vCPU at 85-99 % for 40 minutes and the writers slowed
# into their walls: the writer runs pytest, ruff and its own probe servers,
# not just HTTP. Two user builds per vCPU, plus the smoke's reserved slot, is
# what one vCPU carried at the edge of saturation (1 vCPU -> 2 + 1 = 3, the
# live number), so it is the rule for every row (derive_profile_caps).
BASE_MB = 500
HEADROOM_MB = 150
PER_JOB_MB = 400
USER_BUILDS_PER_VCPU = 2
#: Above this many process slots a tenant may hold more than one: no account
#: takes more than ~6% of a large box (per-tenant = process // 16, 1..4).
TENANT_SHARE_DIVISOR = 16
MAX_TENANT_CAP = 4

#: 2048 MB - 500 - 150 = 1398; 1398 / 400 = 3, and the CPU rule gives the
#: same 2 + 1. THREE is what a 1c-2g box can honestly carry, and it is the CODE
#: default because render.yaml is not an applied blueprint (see its header)
#: -- the default is what production actually receives on the next deploy,
#: with zero dashboard action.
DEFAULT_PROCESS_CAP = 3
#: Fairness floor. One tenant, one concurrent build on a 3-slot box: two
#: other accounts can always get in. Per-tenant caps grow far more slowly
#: than the process cap on purpose -- see WORKER_PROFILES.
DEFAULT_TENANT_CAP = 1
DEFAULT_WORKER_TIMEOUT_S = 1800.0

#: Kept for the existing operator contract: this env var has always named
#: the PROCESS cap and still does.
PROCESS_CAP_ENV = "FACTORY_CODEWHALE_WORKER_CAP"
TENANT_CAP_ENV = "FACTORY_CODEWHALE_TENANT_CAP"
#: THE SINGLE UPGRADE KNOB. Moving from a 3-slot box to a 50-tenant box is
#: this one env var plus a Render resize. No code edit.
PROFILE_ENV = "FACTORY_WORKER_PROFILE"

#: Plan -> (vCPU, RAM MB). DATA: the caps are DERIVED from it by one rule
#: (derive_profile_caps), so a new plan is one line here and no cap is typed.
#:
#:   plan      vCPU  RAM      memory bound      CPU bound (2/vCPU + 1)  PROCESS  TENANT
#:   ----------------------------------------------------------------------------------
#:   starter   0.5     512 MB  < 0               --                      UNSUPPORTED
#:   1c-2g     1      2048 MB  3                 3                       3        1  <- LIVE
#:   2c-4g     2      4096 MB  8                 5                       5        1
#:   4c-8g     4      8192 MB  18                9                       9        1
#:   4c-16g    4     16384 MB  39                9                       9        1
#:   8c-32g    8     32768 MB  80               17                      17        1
#:
#: CPU binds on every row but 1c-2g. 50 concurrent tenants on ONE instance
#: needs ~25 vCPU under the measured rule -- beyond any single Fargate task --
#: so 50 is reached by more instances (the MULTI-INSTANCE SEAM below), or by
#: an operator who sets the explicit caps after measuring a lighter writer.
#: The slot machinery itself still serves 50 distinct tenant keys (the stub
#: tests drive 50 bind_tenant_store digests through acquire/release).
#:
#: ``starter`` derives None deliberately: 512 MB does not fit BASE_MB, let
#: alone a Node child. It resolves to a NAMED REFUSAL rather than quietly
#: emitting a cap the box cannot serve.
WORKER_PLANS: Dict[str, Tuple[float, int]] = {
    "starter": (0.5, 512),
    "1c-2g": (1, 2048),
    "2c-4g": (2, 4096),
    "4c-8g": (4, 8192),
    "4c-16g": (4, 16384),
    "8c-32g": (8, 32768),
}


def derive_profile_caps(vcpu: float, ram_mb: int) -> Optional[Tuple[int, int]]:
    """(process cap, per-tenant cap) for a box, by the ONE rule; None when the
    box cannot host a single writer child."""
    memory_bound = (int(ram_mb) - BASE_MB - HEADROOM_MB) // PER_JOB_MB
    if memory_bound < 1 or vcpu < 1:
        return None
    user = int(USER_BUILDS_PER_VCPU * vcpu)
    cpu_bound = user + reserved_slots(user + 1)
    process = min(memory_bound, cpu_bound)
    tenant = max(1, min(MAX_TENANT_CAP, process // TENANT_SHARE_DIVISOR))
    return process, tenant


#: The profile the live box runs, and the value render.yaml declares.
DEPLOYED_PROFILE = "1c-2g"


class WorkerError(RuntimeError):
    """A named refusal/failure from the CodeWhale worker layer."""


def worker_cli_path() -> Optional[str]:
    return shutil.which("codewhale")


def worker_provider() -> str:
    """Model provider for the headless CLI. DeepSeek by default (the CLI's
    production provider); an operator override from the admin page wins over
    the env, and the env over the default."""
    try:
        from app.core.runtime_settings import CODER_PROVIDER, get as _rt_get

        override = _rt_get(CODER_PROVIDER)
        if override:
            return override
    except Exception:  # noqa: BLE001 — settings never break provider resolution
        pass
    return os.getenv("CODEWHALE_PROVIDER", "deepseek").strip() or "deepseek"


def worker_api_key() -> str:
    """The CLI's API key from the deployment env. Empty when the CLI's own
    stored config is the credential source (local dev)."""
    return (
        os.getenv("CODEWHALE_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
    )


def _cap_from_env(name: str) -> Optional[int]:
    """An explicit operator override, or None when the var is unset.

    A malformed value is a NAMED refusal, never a silent fallback. The old
    ``int(os.getenv(...) or 1)`` had three failure modes that become
    platform-wide outages once this gates 50 tenants: "0" slipped past the
    ``or`` guard (which only catches the EMPTY string) and refused every
    job including the first; a negative did the same; and a typo raised a
    bare ValueError from inside the slot lock -- an unnamed crash, not a
    named refusal. Silently substituting the default is no better: an
    operator who typed "3O" would be told the box runs at 3 when nobody
    chose 3. Unknown capacity is refused, loudly.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    text = raw.strip()
    try:
        value = int(text)
    except ValueError as exc:
        raise WorkerError(
            f"{WORKER_CAP_MALFORMED}: {name}={raw!r} is not an integer — "
            "refusing to guess a concurrency cap"
        ) from exc
    if value < 1:
        raise WorkerError(
            f"{WORKER_CAP_MALFORMED}: {name}={raw!r} must be >= 1 — a zero "
            "or negative cap is an outage, not a configuration"
        )
    return value


def worker_profile() -> str:
    """The single upgrade knob: a Render plan slug from WORKER_PROFILES."""
    return os.getenv(PROFILE_ENV, "").strip()


def _profile_caps() -> Optional[Tuple[int, int]]:
    """(process, tenant) for the named profile, or None when unset.

    An unknown or unsupported profile is a NAMED refusal: the operator
    named a box this code has no honest numbers for, and inventing one is
    exactly the "claim a capacity the box cannot serve" failure.
    """
    name = worker_profile()
    if not name:
        return None
    if name not in WORKER_PROFILES:
        raise WorkerError(
            f"{WORKER_PROFILE_UNKNOWN}: {PROFILE_ENV}={name!r} is not a known "
            f"plan — known: {', '.join(sorted(WORKER_PROFILES))}"
        )
    caps = WORKER_PROFILES[name]
    if caps is None:
        raise WorkerError(
            f"{WORKER_PROFILE_UNSUPPORTED}: {PROFILE_ENV}={name!r} is 0.5 vCPU "
            f"/ 512 MB — it does not fit the app's own {BASE_MB} MB resident "
            f"footprint, let alone a {PER_JOB_MB} MB writer child. Resize "
            "before pointing the profile here."
        )
    return caps


def worker_process_cap() -> int:
    """Total jobs in flight allowed on this instance, across all tenants.

    PRECEDENCE, highest first:
      1. FACTORY_CODEWHALE_WORKER_CAP  (explicit operator override)
      2. FACTORY_WORKER_PROFILE        (the upgrade knob)
      3. DEFAULT_PROCESS_CAP           (the honest 1c-2g default)
    """
    explicit = _cap_from_env(PROCESS_CAP_ENV)
    if explicit is not None:
        return explicit
    caps = _profile_caps()
    if caps is not None:
        return caps[0]
    return DEFAULT_PROCESS_CAP


def worker_tenant_cap() -> int:
    """Jobs in flight allowed for ONE bound tenant. Same precedence."""
    explicit = _cap_from_env(TENANT_CAP_ENV)
    if explicit is not None:
        return explicit
    caps = _profile_caps()
    if caps is not None:
        return caps[1]
    return DEFAULT_TENANT_CAP


def worker_cap() -> int:
    """Back-compat alias: this name has always meant the PROCESS cap."""
    return worker_process_cap()


#: How many specialist agents ONE writer may run at once. The writer delegates
#: infra / backend / frontend / devops / security, and how many of those can be
#: in flight together is a property of the BOX, not of the prompt -- so it is
#: read here and rendered into the prompt, never hardcoded in the template.
SPECIALIST_WORKERS_ENV = "FACTORY_WRITER_SPECIALIST_WORKERS"


def writer_specialist_cap() -> int:
    """Specialist agents one writer may run concurrently.

    PRECEDENCE, highest first:
      1. FACTORY_WRITER_SPECIALIST_WORKERS  (explicit operator override)
      2. the instance's own agent-child budget (worker_process_cap), which is
         what the box is sized for and moves with FACTORY_WORKER_PROFILE

    The process cap is the right default rather than 1: the per-TENANT cap is
    normally 1, so a tenant's build is usually the only one in flight and the
    instance's whole child budget is genuinely available to it. An operator who
    runs several tenants hot turns this down; an operator on a bigger plan gets
    more without touching this file.
    """
    explicit = _cap_from_env(SPECIALIST_WORKERS_ENV)
    if explicit is not None:
        return explicit
    return max(1, worker_process_cap())


def worker_timeout_s() -> float:
    return float(
        os.getenv("FACTORY_CODEWHALE_WORKER_TIMEOUT_S", str(DEFAULT_WORKER_TIMEOUT_S))
    )


@dataclass(frozen=True)
class WorkerReceipt:
    """What the CLI actually reported. No invented fields."""

    status: str
    termination_reason: Optional[str]
    provider: str = ""
    model: str = ""
    output: str = ""
    tools: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    error_category: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "termination_reason": self.termination_reason,
            "provider": self.provider,
            "model": self.model,
            "output": self.output,
            "tools": list(self.tools),
            "error": self.error,
            "error_category": self.error_category,
        }


# ---------------------------------------------------------------------------
# MULTI-INSTANCE SEAM.
#
# This counter is CORRECT only while the service runs exactly ONE instance.
# Verified against the live Render API 2026-09-16: cerebrumdev-backend
# numInstances=1, autoscaling=None. Builds are daemon THREADS in this one
# process (build_jobs.py, threading.Thread(target=_run)), so a
# threading.Lock around in-memory counters is the whole of the problem.
#
# THE CONDITION THAT FORCES THE SWAP: numInstances > 1, or Render
# autoscaling enabled. At that moment each instance keeps its own
# _process_active and independently admits up to the process cap, so the
# real ceiling silently becomes numInstances x process_cap and the memory
# budget above is wrong by that factor -- in the direction that OOMs the
# box. Per-tenant fairness breaks the same way: a tenant capped at 1 gets
# numInstances concurrent builds. Nothing raises an error; the caps just
# quietly stop meaning what they say.
#
# DO NOT implement shared state until that condition holds. A network
# round-trip per slot acquire is a new failure mode for zero benefit at one
# instance. When it does hold the substrate is already provisioned --
# cerebrumdev-redis in render.yaml, wired to REDIS_URL -- so the swap is
# implementation only: no new dependency, no new infrastructure.
#
# TO SWAP: implement acquire/release/snapshot with the same signatures
# (acquire takes an optional holder dict naming the job for refusals) and
# semantics (atomic check-and-increment; decrement-and-delete-at-zero;
# release must be exception-safe and must not leak a slot if the holder
# dies -- a TTL on the shared entry, which the in-process version does not
# need because the finally block cannot be skipped) and call
# set_slot_counter(). No caller changes.
#
# The seam is a PROTOCOL, not an abstract base class: nothing is imported
# to define it, so "no new dependency" holds literally.
# ---------------------------------------------------------------------------


class InProcessSlotCounter:
    """Two-axis concurrency accounting for ONE worker process.

    process axis -- host protection: total in flight on this instance.
    tenant axis  -- fairness: in flight for ONE bound tenant.

    ONE lock guards both. EVERY mutation happens inside it, cleanup
    included, and the per-tenant entry is POPPED at zero in the same
    critical section as the decrement -- never read-then-delete across two
    acquisitions, which at 50 tenants would race a concurrent acquire into
    resurrecting a half-deleted entry. The dict is therefore bounded by the
    number of tenants CURRENTLY holding slots, never by the number of
    tenants ever seen.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Every release notifies waiters; a waiting job re-checks its turn.
        self._turn = threading.Condition(self._lock)
        # The FIFO of jobs waiting for a slot, in arrival order. Reserved
        # (smoke-principal) waiters are served ahead of user waiters.
        self._waiters: list = []
        self._process_active = 0
        # Slots held by reserved (smoke-principal) jobs. A user job may hold
        # at most process_cap - RESERVED_SLOTS of the instance, so the smoke
        # always has a slot a user build cannot take.
        self._reserved_active = 0
        # A plain dict, deliberately not a defaultdict: a defaultdict
        # materialises an entry on every READ, including the read that is
        # about to refuse, so a refusal storm would grow the map as fast as
        # the successes.
        self._tenant_active: Dict[str, int] = {}
        # WHO holds each slot (live 2026-09-30: "tenant f714a7c0 holds 1/1"
        # told the owner nothing about WHICH of their builds held it).
        # Parallel to _tenant_active, same lock, popped at zero with it.
        self._tenant_holders: Dict[str, list] = {}

    def acquire(
        self,
        key: str,
        *,
        process_cap: int,
        tenant_cap: int,
        holder: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Take one slot for ``key`` or raise a named WorkerError.

        CHECK ORDER is process first, then tenant, and that is deliberate:
        the process cap is the host-protection invariant. If the instance
        is full the job is refused regardless of whose it is, so reporting
        "this user is at their limit" when the truth is "the box is full"
        would send an operator to the wrong place. Process exhaustion is
        the page-worthy condition and wins the message.
        """
        short = key[:8]
        with self._lock:
            active = self._process_active
            held = self._tenant_active.get(key, 0)
            if active >= process_cap:
                # No mutation on the refusal path.
                raise WorkerError(
                    f"{WORKER_CONCURRENCY_CAPPED}: {PROCESS_SLOTS_EXHAUSTED} "
                    f"scope=process — {active}/{process_cap} job slots in "
                    f"flight on this instance across all tenants (tenant "
                    f"{short} holds {held}/{tenant_cap}) — job refused, never "
                    f"silently run and never silently queued. Raise "
                    f"{PROCESS_CAP_ENV} only with the memory budget "
                    f"recomputed, or move {PROFILE_ENV} to a larger plan."
                )
            if held >= tenant_cap:
                holders = ""
                named = [
                    f"session {h.get('session_id')} building {h.get('product_id')}"
                    for h in self._tenant_holders.get(key, ())
                    if h.get("session_id") or h.get("product_id")
                ]
                if named:
                    holders = " Held by your own " + "; ".join(named) + "."
                raise WorkerError(
                    f"{WORKER_CONCURRENCY_CAPPED}: {TENANT_SLOTS_EXHAUSTED} "
                    f"scope=tenant — tenant {short} holds {held}/{tenant_cap} "
                    f"of its own concurrent build slots; this instance has "
                    f"{active}/{process_cap} in flight, so other tenants are "
                    f"unaffected — job refused, never silently run and never "
                    f"silently queued.{holders}"
                )
            # Both mutations together, LAST. The increment is the final
            # statement of the critical section and `try:` opens immediately
            # after it in worker_job_slot, so no path can decrement a slot it
            # never incremented.
            self._take(key, holder, reserved=False)

    # -- the queue -------------------------------------------------------

    def _take(self, key: str, holder: Optional[Dict[str, Any]], *, reserved: bool) -> None:
        """Both increments together. Caller holds the lock."""
        self._process_active += 1
        if reserved:
            self._reserved_active += 1
        self._tenant_active[key] = self._tenant_active.get(key, 0) + 1
        entry = dict(holder or {})
        entry["_reserved"] = bool(reserved)
        self._tenant_holders.setdefault(key, []).append(entry)

    def _admissible(self, waiter: Dict[str, Any], process_cap: int, tenant_cap: int) -> bool:
        """Whether a slot is free for this waiter right now. Caller holds the lock."""
        if self._tenant_active.get(waiter["key"], 0) >= tenant_cap:
            return False
        if waiter["reserved"]:
            return self._process_active < process_cap
        user_cap = process_cap - reserved_slots(process_cap)
        user_active = self._process_active - self._reserved_active
        return user_active < user_cap and self._process_active < process_cap

    def _serving_order(self) -> list:
        """Reserved waiters first, then arrival order. Caller holds the lock."""
        return sorted(self._waiters, key=lambda w: (not w["reserved"], w["seq"]))

    def _turn_for(self, waiter: Dict[str, Any], process_cap: int, tenant_cap: int) -> bool:
        """FIFO: this waiter may take a slot only if it is the first waiter in
        serving order that can use one, and no earlier waiter of its OWN tenant
        is still waiting (a tenant's builds start in the order they asked).
        Caller holds the lock."""
        for other in self._serving_order():
            if other is waiter:
                return self._admissible(waiter, process_cap, tenant_cap)
            if other["key"] == waiter["key"]:
                return False
            if self._admissible(other, process_cap, tenant_cap):
                return False
        return False

    def _position(self, waiter: Dict[str, Any]) -> Dict[str, int]:
        """1-based place in the serving order, and how many are ahead."""
        order = self._serving_order()
        ahead = order.index(waiter) if waiter in order else 0
        same_tenant = sum(1 for w in order[:ahead] if w["key"] == waiter["key"])
        return {"position": ahead + 1, "ahead": ahead, "ahead_same_tenant": same_tenant}

    def acquire_waiting(
        self,
        key: str,
        *,
        process_cap: int,
        tenant_cap: int,
        holder: Optional[Dict[str, Any]] = None,
        reserved: bool = False,
        time_left: Callable[[], Optional[float]],
        on_wait: Optional[Callable[[Dict[str, Any]], None]] = None,
        poll_s: float = SLOT_WAIT_POLL_S,
    ) -> Dict[str, Any]:
        """Take a slot, WAITING in FIFO order when none is free.

        Never refuses for lack of a slot. ``time_left`` is the run's own
        remaining phase-wall ceiling, read live; when it reaches zero the job
        stops with SLOT_WAIT_EXHAUSTED. ``on_wait`` is told the job's queue
        place each time it re-checks (the caller narrates it). Returns the
        wait record: ``{"waited_s", "position"}`` (position 0 = no wait).
        """
        import time as _time

        started = _time.monotonic()
        with self._turn:
            self._seq = getattr(self, "_seq", 0) + 1
            waiter = {"key": key, "reserved": bool(reserved), "seq": self._seq}
            self._waiters.append(waiter)
            first_place = 0
            try:
                while not self._turn_for(waiter, process_cap, tenant_cap):
                    place = self._position(waiter)
                    if not first_place:
                        first_place = place["position"]
                    left = time_left()
                    if left is not None and left <= 0:
                        raise WorkerError(
                            f"{SLOT_WAIT_EXHAUSTED}: waited "
                            f"{_time.monotonic() - started:.0f}s for a build slot "
                            f"(position {place['position']}, {place['ahead']} ahead) "
                            "and the run's phase-wall ceiling is spent"
                        )
                    if on_wait is not None:
                        info = dict(place)
                        info["since_s"] = round(_time.monotonic() - started, 1)
                        # Narrate OUTSIDE the lock: the callback writes the
                        # ledger and must never block a release.
                        self._turn.release()
                        try:
                            on_wait(info)
                        finally:
                            self._turn.acquire()
                        if self._turn_for(waiter, process_cap, tenant_cap):
                            break
                    wait_for = poll_s if left is None else max(0.01, min(poll_s, left))
                    self._turn.wait(timeout=wait_for)
                self._take(key, holder, reserved=reserved)
            finally:
                self._waiters.remove(waiter)
                # A departing waiter may unblock the one behind it.
                self._turn.notify_all()
        return {"waited_s": round(_time.monotonic() - started, 1), "position": first_place}

    def release(self, key: str) -> None:
        """Give the slot back. Mirrors acquire; runs from a finally block."""
        with self._lock:
            self._process_active -= 1
            holders_now = self._tenant_holders.get(key) or []
            if holders_now and holders_now[-1].get("_reserved"):
                self._reserved_active = max(0, self._reserved_active - 1)
            remaining = self._tenant_active.get(key, 0) - 1
            if remaining > 0:
                self._tenant_active[key] = remaining
                holders = self._tenant_holders.get(key)
                if holders:
                    holders.pop()
            else:
                # CLEANUP INSIDE THE LOCK. This pop is what keeps 50
                # churning tenants from leaving 50 zero-valued entries
                # behind forever.
                self._tenant_active.pop(key, None)
                self._tenant_holders.pop(key, None)
            self._turn.notify_all()

    def snapshot(self) -> Dict[str, Any]:
        """Observability + the leak assertion. A copy, never the live map."""
        with self._lock:
            return {
                "total": self._process_active,
                "by_tenant": dict(self._tenant_active),
            }

    def queue_snapshot(self) -> Dict[str, Any]:
        """The queue beside the slots: jobs waiting, reserved slots held."""
        with self._lock:
            return {
                "waiting": len(self._waiters),
                "reserved": self._reserved_active,
            }


def reserved_slots(process_cap: int) -> int:
    """Slots on this instance only a reserved (smoke-principal) job may hold.

    One, whenever the instance can run more than one job; on a one-slot box
    a reservation would starve every user, so there the smoke is only served
    first in the queue (never behind a user waiter), not given a held slot.
    """
    return 1 if int(process_cap) >= 2 else 0


#: Plan -> (process cap, per-tenant cap), derived from WORKER_PLANS by the one
#: rule. Selected at run time by FACTORY_WORKER_PROFILE.
WORKER_PROFILES: Dict[str, Optional[Tuple[int, int]]] = {
    name: derive_profile_caps(vcpu, ram_mb) for name, (vcpu, ram_mb) in WORKER_PLANS.items()
}


_COUNTER: Any = InProcessSlotCounter()


def set_slot_counter(counter: Any) -> Any:
    """Install a slot-counter backend; returns the one replaced.

    The multi-instance swap point (see the seam note above), and the way a
    test installs a clean counter instead of reaching into module globals.
    """
    global _COUNTER
    previous = _COUNTER
    _COUNTER = counter
    return previous


def slot_counter() -> Any:
    return _COUNTER


def worker_slots_snapshot() -> Dict[str, Any]:
    """``{"total": int, "by_tenant": {key: int}}`` for the live counter."""
    return _COUNTER.snapshot()


_TENANT_KEY_ATTR = "tenant_key"


def _tenant_slot_key(tenant_store: Any) -> str:
    """The slot bucket for a BOUND handle. Never a client-supplied name.

    Production always takes the first branch: the handle is a
    TenantStoreBinding (tenant_bind.TenantStoreBinding) whose ``tenant_key``
    is a server-side sha256 digest of the AUTHENTICATED identity
    (tenant_bind.bind_tenant_store). The caller cannot choose another
    tenant's bucket for the same reason it cannot choose another tenant's
    store dir: it never supplies the string. Same handle, same boundary,
    same trust decision as _require_bound_tenant -- no second decision is
    introduced here.

    Deliberately NOT read: ``tenant_id``, ``digest``, ``name`` or any other
    attribute. Those are attacker-shaped names on an arbitrary object.

    A handle with no tenant_key is REFUSED BY NAME rather than dropped into
    a shared or anonymous bucket. Fail-closed: an unkeyed handle cannot be
    accounted for fairly, and a shared fallback bucket would silently
    collapse every unkeyed caller onto one key -- which would make a
    50-tenant test pass while proving nothing. The only handles production
    ever passes are TenantStoreBinding or None (build_jobs binds it,
    runner seeds ctx.state, roles_handlers hands it here), so this refusal
    is unreachable from the live path by construction.
    """
    key = getattr(tenant_store, _TENANT_KEY_ATTR, None)
    if isinstance(key, str) and key.strip():
        return key.strip()
    raise WorkerError(
        f"{UNKEYED_TENANT_HANDLE}: the bound handle carries no server-derived "
        f"{_TENANT_KEY_ATTR} — refusing to account a build against a shared "
        "slot bucket"
    )


def tenant_reserved(tenant_store: Any) -> bool:
    """Whether this bound handle may use the reserved slot.

    Read from the BOUND handle only (``reserved``, set server-side by
    tenant_bind.bind_tenant_store from the authenticated account), the same
    trust boundary as the tenant key -- the caller never supplies it.
    """
    return getattr(tenant_store, "reserved", False) is True


@contextmanager
def worker_job_slot(
    tenant_store: Any,
    holder: Optional[Dict[str, Any]] = None,
    *,
    wait_left: Optional[Callable[[], Optional[float]]] = None,
    on_wait: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Iterator[Dict[str, Any]]:
    """Acquire one concurrency slot for this tenant's job.

    Phase 1 applies to the builder: the store must already be BOUND for
    this job (resolved from the authenticated principal). An unbound job
    refuses with ``no_authenticated_tenant`` — P5's mutation runs the
    worker unbound and the suite goes RED.

    Two caps gate the job. The PROCESS cap protects the host; the TENANT
    cap keeps one account from taking the box. With ``wait_left`` (the run's
    remaining phase-wall ceiling, read live) a job that finds no slot QUEUES,
    FIFO per tenant, and is never failed for lack of a slot; it stops only
    when ``wait_left`` reaches zero (SLOT_WAIT_EXHAUSTED). Without it (direct
    callers, the operator probe) the old immediate, named refusal stands.
    Yields the wait record ``{"waited_s", "position"}``.
    """
    # The tenant boundary comes FIRST and outside the lock: it raises and
    # touches no counter state, so there is nothing to unwind, and an
    # unbound handle never reaches the counter at all.
    _require_bound_tenant(tenant_store)
    key = _tenant_slot_key(tenant_store)
    # Read each cap ONCE, outside the lock, into a local. os.environ is
    # mutated at runtime elsewhere in this process, so two reads are not
    # guaranteed to agree -- and a refusal message that reports a cap which
    # never gated anything sends an operator chasing a number that does not
    # exist. These same locals make the decision AND the message.
    process_cap = worker_process_cap()
    tenant_cap = worker_tenant_cap()
    record: Dict[str, Any] = {"waited_s": 0.0, "position": 0}
    if wait_left is not None and hasattr(_COUNTER, "acquire_waiting"):
        record = _COUNTER.acquire_waiting(
            key,
            process_cap=process_cap,
            tenant_cap=tenant_cap,
            holder=holder,
            reserved=tenant_reserved(tenant_store),
            time_left=wait_left,
            on_wait=on_wait,
        )
    else:
        _COUNTER.acquire(
            key, process_cap=process_cap, tenant_cap=tenant_cap, holder=holder
        )
    try:
        yield record
    finally:
        # An exception inside the yield still releases the slot.
        _COUNTER.release(key)


def _require_bound_tenant(tenant_store: Any) -> None:
    """The worker never runs without a bound tenant store handle.

    The handle IS the Phase 1 boundary: it was resolved from the
    authenticated principal upstream; the worker accepts only that handle,
    never a client-supplied name. None = unauthenticated = refusal.
    """
    if tenant_store is None:
        raise WorkerError(
            f"{NO_AUTHENTICATED_TENANT}: the worker job has no bound tenant "
            "store — refusing to run the builder without isolation"
        )


def _child_env(session_id: Optional[str], cli_home: Optional[str] = None) -> Dict[str, str]:
    """The environment ONE writer child runs under.

    Every child used to inherit the live process environment by reference
    at fork, with no ``env=`` argument. That is harmless at one concurrent
    build and a cross-tenant bleed at three: build-scoped keys are written
    into ``os.environ`` from inside per-build threads, so tenant B's writer
    could be spawned carrying tenant A's session id. Raising the process
    cap is what activates that, so the snapshot ships WITH the cap raise.

    The snapshot is taken ONCE, here, at job start, and the build-scoped
    keys are stamped from THIS job's own identity rather than inherited
    from whatever the process global happens to hold at fork time.

    ``cli_home`` pins CODWHALE_HOME so the CLI writes its agent-loop log
    where the worker's tailer reads it — Render's HOME is not where the
    CLI writes, and an invisible log makes a live writer look dead.
    """
    env = dict(os.environ)
    sid = str(session_id or "").strip()
    if sid:
        env["FACTORY_SESSION_ID"] = sid
        env["FACTORY_CLI_PIVOT_SESSION_ID"] = sid
    if cli_home:
        env["CODWHALE_HOME"] = cli_home
    return env


def run_worker_job(
    prompt: str,
    checkout_dir: Path | str,
    *,
    tenant_store: Any = None,
    timeout_s: Optional[float] = None,
    session_id: Optional[str] = None,
    product_id: str = "",
    progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    live_time_left: Optional[Callable[[], Optional[float]]] = None,
    slot_wait_left: Optional[Callable[[], Optional[float]]] = None,
    on_slot_wait: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> WorkerReceipt:
    """Run one headless CodeWhale exec — non-interactive, JSON summary.

    ``live_time_left`` is the run's own remaining budget, read on every wait
    tick. The wall is the later of the dispatch wall and ``now + live`` --
    it extends when the runner's budget ramp lifts the run's deadline (the
    ramp's pulse fires on the NOTEs this loop relays) and never shrinks
    below the dispatch wall. Live 2026-10-06: the wall was fixed once at
    dispatch, the ramp lifted a deadline nobody waited on, and a writer that
    was still producing work was killed at exactly 1800s.

    T5.1: no approval prompt may hang the worker; the CLI's --auto mode is
    the documented non-interactive automation path. The job runs INSIDE a
    worker slot; beyond either cap it refuses, never queues-and-forgets.

    ``session_id`` is this build's own identity, threaded from the runner
    state. It stamps the child environment so a concurrent build cannot
    hand its session id to this one.
    """
    cli = worker_cli_path()
    # The tenant boundary precedes everything: an unbound job is refused
    # before the binary is even looked up — isolation is the first gate,
    # and P5 must hold on runners with no codewhale installed.
    _require_bound_tenant(tenant_store)
    if cli is None:
        raise WorkerError(
            f"{WORKER_CLI_MISSING}: codewhale executable not found — the "
            "worker cannot run headless"
        )
    with worker_job_slot(
        tenant_store,
        # Name THIS job so a capped sibling's refusal can say which of the
        # owner's builds holds the slot (live 2026-09-30: "holds 1/1" with
        # no name was a dead-end on the Floor).
        holder={
            "session_id": str(session_id or "") or None,
            "product_id": str(product_id or "") or Path(checkout_dir).name,
        },
        # A job that finds no free slot waits its turn (FIFO per tenant,
        # smoke principal served first), bounded only by the run's ceiling.
        wait_left=slot_wait_left,
        on_wait=on_slot_wait,
    ):
        cwd = Path(checkout_dir)
        cwd.mkdir(parents=True, exist_ok=True)
        timeout = timeout_s if timeout_s is not None else worker_timeout_s()
        # codewhale 0.9.13 parses --provider/--api-key as GLOBAL flags:
        # they must precede `exec`. After the subcommand they are refused
        # ("--provider must be placed before `exec`") and the job dies in
        # under a second with zero authored artifacts.
        argv = [cli]
        # Headless deployments pass credentials explicitly; local dev lets
        # the CLI read its own stored config when the env key is absent.
        api_key = worker_api_key()
        if api_key:
            argv += ["--provider", worker_provider(), "--api-key", api_key]
        argv += ["exec", "--auto", "--json"]

        # The brief does NOT ride in argv. Linux caps one exec argument at
        # 128 KiB (MAX_ARG_STRLEN); a full brief -- template + floor + the
        # capability specs -- sits near that cliff, and a live build died
        # before the process even started: "worker_exec_failed: could not
        # start /usr/local/bin/codewhale: [Errno 7] Argument list too long".
        # codewhale 0.9.13 has no stdin or file flag (verified against the
        # pinned npm package: a missing positional is refused, and `-` is
        # read as a literal prompt), so the brief goes to the SAME file E4
        # already persisted for audit -- what the coder was told and what
        # the coder reads are now one artifact -- and argv carries only a
        # fixed-size pointer. Writing it is therefore part of the dispatch,
        # not best-effort audit: a pointer to a missing file would spend a
        # full worker wall on a prompt of nothing.
        try:
            (cwd / "docs").mkdir(parents=True, exist_ok=True)
            (cwd / "docs" / "writer_prompt.txt").write_text(
                prompt, encoding="utf-8"
            )
        except OSError as exc:
            raise WorkerError(
                "worker_brief_unwritable: could not write "
                f"docs/writer_prompt.txt for the dispatch: {exc}"
            ) from exc
        argv.append(
            "Your complete brief is in docs/writer_prompt.txt (UTF-8, in the "
            "current working directory). Read that file FIRST and follow it "
            "exactly as if its contents were this message. It is the entire "
            "task; nothing else will be sent."
        )

        # E4: persist how the coder was invoked, so "what did the writer
        # receive" never needs a source read or an SSH session again. The
        # api key is scrubbed from the persisted argv.
        sanitized = [
            "[redacted]" if (a == api_key and api_key) else a for a in argv
        ]
        try:
            (cwd / "docs" / "writer_argv.json").write_text(
                json.dumps({"argv": sanitized}, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError:  # never fail the build on audit persistence
            logger.exception("writer argv persistence failed")

        # E3 part 1: the dispatch line an operator can tail on Render.
        logger.info(
            "codewhale writer dispatch: cli=%s provider=%s session=%s timeout=%ss",
            cli,
            worker_provider(),
            str(session_id or "")[:12] or "-",
            timeout,
        )
        # E3 part 0: pre-flight probe — a broken binary or a config prompt
        # must fail in seconds, not sit silent for the full worker wall.
        # Skipped when the cli path is a test double that does not exist.
        if Path(cli).exists():
            try:
                probe = subprocess.run(
                    [cli, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                    stdin=subprocess.DEVNULL,
                    **agent_popen_kwargs(),
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise WorkerError(
                    f"{WORKER_EXEC_FAILED}: pre-flight probe failed for {cli}: {exc}"
                ) from exc
            if probe.returncode != 0:
                raise WorkerError(
                    f"{WORKER_EXEC_FAILED}: pre-flight probe exited "
                    f"{probe.returncode}: {(probe.stderr or '')[-300:]}"
                )
        from app.factory.build.sanitize import sanitize_for_status

        # The writer pass narrates itself: the CLI's agent-loop progress
        # (its log file) and its stderr are streamed line by line into the
        # progress callback — the role relays throttled NOTEs to the ledger
        # so the Floor shows what the writer is doing instead of "quiet
        # for N min". Persisted to docs/writer_progress.jsonl too.
        # stdout stays RAW and separate: it carries the final JSON summary
        # (merging stderr in broke the parse — live run4
        # sess_620b8581fb224bea).
        cli_home = os.getenv("CODWHALE_HOME") or str(cwd / ".codewhale-home")
        tailer = _cli_log_tailer(home=Path(cli_home))
        progress_path = cwd / "docs" / "writer_progress.jsonl"
        try:
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_handle = progress_path.open("a", encoding="utf-8")
        except OSError:
            progress_handle = None
        stdout_lines: List[str] = []
        relay_queue: "queue.Queue[str]" = queue.Queue()
        quiet_clock = {"last": time.monotonic()}

        def _relay(raw: str, fields: Optional[Dict[str, Any]] = None) -> None:
            line = sanitize_for_status(raw)[:400]
            if not line.strip():
                return
            if not line.startswith(HEARTBEAT_PREFIX):
                quiet_clock["last"] = time.monotonic()
            if progress_handle is not None:
                try:
                    progress_handle.write(
                        json.dumps(
                            {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "line": line,
                            }
                        )
                        + "\n"
                    )
                    progress_handle.flush()
                except OSError:
                    pass
            if progress is not None:
                try:
                    progress(line, {"tool": _tool_hint(line), **(fields or {})})
                except Exception:  # noqa: BLE001 — telemetry never fails the build
                    pass

        def _stdout_reader() -> None:
            try:
                assert proc.stdout is not None
                for raw in iter(proc.stdout.readline, ""):
                    if raw.strip():
                        stdout_lines.append(raw.rstrip("\r\n"))
            except (OSError, ValueError):  # closed pipe on kill
                pass

        def _stderr_reader() -> None:
            try:
                assert proc.stderr is not None
                for raw in iter(proc.stderr.readline, ""):
                    if raw.strip():
                        relay_queue.put(raw.rstrip("\r\n"))
            except (OSError, ValueError):  # closed pipe on kill
                pass

        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=_child_env(session_id, cli_home),
                # The agent's shell gets its own process group: a group-wide
                # signal it sends can never reach the Factory server.
                **agent_popen_kwargs(),
            )
        except OSError as exc:
            raise WorkerError(
                f"{WORKER_EXEC_FAILED}: could not start {cli}: {exc}"
            ) from exc
        track_agent(proc)

        stdout_reader = threading.Thread(target=_stdout_reader, daemon=True)
        stderr_reader = threading.Thread(target=_stderr_reader, daemon=True)
        # The prompt instructs the agent to append STEP lines to
        # docs/writer_progress.log; pump that file's growth into the relay
        # so the Floor narrates the pass even when the CLI's own logs are
        # not visible from the container.
        prompt_log_path = cwd / "docs" / "writer_progress.log"
        # On a resumed pass the log already holds the interrupted pass's
        # steps; stream only what is written from now on, not the history.
        try:
            prompt_log_offset = prompt_log_path.stat().st_size
        except OSError:
            prompt_log_offset = 0

        narration = {"steps": 0, "last_said": time.monotonic()}
        # The factory writes its own audit files (prompt, argv) into the
        # checkout before the CLI starts; only growth past this baseline is
        # the agent's output.
        files_at_start = count_authored_files(cwd)
        started_at = time.monotonic()

        def _heartbeat() -> None:
            """Speak for the agent when it has said nothing for a while.

            Runs inside the wait loop, so a heartbeat is itself proof the
            CLI process is alive and inside its deadline.
            """
            now = time.monotonic()
            since = now - max(narration["last_said"], quiet_clock["last"])
            if since < HEARTBEAT_EVERY_S:
                return
            narration["last_said"] = now
            files = max(0, count_authored_files(cwd) - files_at_start)
            _relay(
                heartbeat_line(now - started_at, files, narration["steps"]),
                # The same facts as data, for the budget ramp: the line is for
                # people, these fields are what the inspector counts.
                {FILES_ON_DISK: files, STEPS_REPORTED: narration["steps"]},
            )

        def _pump_prompt_progress() -> None:
            nonlocal prompt_log_offset
            try:
                size = prompt_log_path.stat().st_size
            except OSError:
                return
            if size < prompt_log_offset:
                prompt_log_offset = 0
            if size == prompt_log_offset:
                return
            try:
                with prompt_log_path.open(
                    "r", encoding="utf-8", errors="replace"
                ) as fh:
                    fh.seek(prompt_log_offset)
                    chunk = fh.read()
                    prompt_log_offset = fh.tell()
            except OSError:
                return
            for raw in chunk.splitlines():
                if raw.strip():
                    narration["steps"] += 1
                    _relay("writer: " + raw, {STEPS_REPORTED: narration["steps"]})

        stdout_reader.start()
        stderr_reader.start()

        # E3 part 1b: the CLI path never emitted a model_call NOTE (that
        # NOTE belonged to the factory-coder route), so a live-but-silent
        # CLI writer looked identical to a dead one on the Floor. Open the
        # call here with the worker wall as its deadline; _note_cli_finished
        # below closes it on EVERY exit of the wait loop -- success, bad
        # exit code, or wall kill.
        def _note_cli_started() -> None:
            if progress_handle is not None:
                try:
                    progress_handle.write(
                        json.dumps(
                            {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "line": "codewhale writer CLI started",
                                "model_call": True,
                            }
                        )
                        + "\n"
                    )
                    progress_handle.flush()
                except OSError:
                    pass
            if progress is not None:
                try:
                    progress(
                        "codewhale writer CLI started — model call in flight",
                        {
                            "model_call": True,
                            "deadline_s": timeout,
                            "provider": worker_provider(),
                        },
                    )
                except Exception:  # noqa: BLE001 — telemetry never fails the build
                    pass

        def _note_cli_finished() -> None:
            """Close the model-call NOTE the moment the session is over.

            Without this, _model_call_overdue keeps aging the STARTED
            NOTE and fails the build's status ``deadline_s`` after the
            CLI began, however alive the run is by then. model_call=False
            rides the payload so the relay never throttles the close.
            """
            if progress_handle is not None:
                try:
                    progress_handle.write(
                        json.dumps(
                            {
                                "ts": datetime.now(timezone.utc).isoformat(),
                                "line": MODEL_CALL_CLOSED_DETAIL,
                                "model_call": False,
                                MODEL_CALL_STATE: CLOSED,
                            }
                        )
                        + "\n"
                    )
                    progress_handle.flush()
                except OSError:
                    pass
            if progress is not None:
                try:
                    progress(
                        MODEL_CALL_CLOSED_DETAIL,
                        {
                            "model_call": False,
                            MODEL_CALL_STATE: CLOSED,
                            "provider": worker_provider(),
                        },
                    )
                except Exception:  # noqa: BLE001 — telemetry never fails the build
                    pass

        _note_cli_started()
        wait_started = time.monotonic()
        deadline = wait_started + timeout
        try:
            while True:
                try:
                    proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if live_time_left is not None:
                        try:
                            left = live_time_left()
                        except Exception:  # noqa: BLE001 -- a broken probe keeps the dispatch wall
                            left = None
                        if left is not None:
                            deadline = max(deadline, time.monotonic() + float(left))
                    if time.monotonic() > deadline:
                        kill_agent_tree(proc)
                        proc.wait()
                        # Name the wall that actually fired: the ramp may
                        # have lifted it past the dispatch value (live
                        # 2026-10-08 read "exceeded 1800.0s" at ~2640s).
                        raise WorkerError(
                            f"{WORKER_TIMED_OUT}: headless job exceeded "
                            f"its {deadline - wait_started:.0f}s wall "
                            f"(dispatched with {timeout}s)"
                        )
                    # Drain relays on THIS thread: ledger notes stay
                    # single-writer (the build thread is blocked here).
                    while True:
                        try:
                            _relay(relay_queue.get_nowait())
                        except queue.Empty:
                            break
                    tailer.pump(_relay)
                    _pump_prompt_progress()
                    _heartbeat()
        finally:
            # Drain the remainder so no authored evidence is lost.
            stdout_reader.join(timeout=5)
            stderr_reader.join(timeout=5)
            while True:
                try:
                    _relay(relay_queue.get_nowait())
                except queue.Empty:
                    break
            tailer.pump(_relay)
            _pump_prompt_progress()
            # The subprocess is over on every path through here (returned,
            # bad exit, or killed at the wall): close the model call NOW,
            # before the post-loop raises, so the Floor never ages a
            # finished session into "coder LLM timed out".
            _note_cli_finished()
            if progress_handle is not None:
                try:
                    progress_handle.close()
                except OSError:
                    pass
        returncode = proc.returncode
        if returncode != 0:
            tail = "\n".join(stdout_lines)[-400:]
            raise WorkerError(
                f"{WORKER_EXEC_FAILED}: exit {returncode}: {tail}"
            )
        # The summary is the CLI's final JSON on stdout. Parse exactly as
        # before the streaming change: the last line, then the whole raw
        # buffer (never the sanitised relay copy).
        payload = None
        candidates = [stdout_lines[-1]] if stdout_lines else []
        candidates.append("\n".join(stdout_lines))
        for candidate in candidates:
            try:
                payload = json.loads(candidate)
                break
            except ValueError:
                continue
        if payload is None:
            raise WorkerError(
                f"{WORKER_EXEC_FAILED}: non-JSON summary from the CLI"
            )
        receipt = WorkerReceipt(
            status=str(payload.get("status") or ""),
            termination_reason=payload.get("termination_reason"),
            provider=str(payload.get("provider") or ""),
            model=str(payload.get("model") or ""),
            output=str(payload.get("output") or ""),
            tools=[
                dict(t) for t in (payload.get("tools") or []) if isinstance(t, dict)
            ],
            error=payload.get("error"),
            error_category=payload.get("error_category"),
        )
        # E3 part 2: the verdict line.
        logger.info(
            "codewhale writer receipt: status=%s reason=%s provider=%s "
            "model=%s tools=%d error=%s",
            receipt.status,
            receipt.termination_reason or "-",
            receipt.provider,
            receipt.model,
            len(receipt.tools),
            receipt.error_category or "-",
        )
        return receipt


def _cli_log_tailer(home: Optional[Path] = None) -> Any:
    """A fresh tailer for one worker pass."""
    return _CliLogTailer(home=home)


def _tool_hint(line: str) -> str:
    """The step a CLI progress line belongs to, read from the line's own
    structure: a tracing record ``<timestamp> <LEVEL> <target>: <message>``
    names its step in ``<target>`` (``engine.turn``). A line without that
    structure names no step -- its words are never guessed at."""
    parts = (line or "").split(None, 3)
    if len(parts) < 3:
        return ""
    stamp, level, target = parts[0], parts[1], parts[2]
    try:
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if not (level.isalpha() and level.isupper() and target.endswith(":")):
        return ""
    return target[:-1]


#: Seconds of silence before the factory speaks for a quiet agent.
HEARTBEAT_EVERY_S = 60.0
HEARTBEAT_PREFIX = "writer working"
_STEP_PREFIX = "writer: STEP"
#: Progress-record fields (typed, never parsed from the narration text): the
#: steps the agent has reported in this pass, and the files it has put on
#: disk. The budget ramp reads them to tell a working writer from a quiet one.
STEPS_REPORTED = "steps_reported"
FILES_ON_DISK = "files_on_disk"
WRITER_PROGRESS_FIELDS = (STEPS_REPORTED, FILES_ON_DISK)


def is_narration_line(line: str) -> bool:
    """Lines that ARE the narration: never throttled away by the relay."""
    text = str(line or "")
    return text.startswith(_STEP_PREFIX) or text.startswith(HEARTBEAT_PREFIX)


def count_authored_files(root: Path) -> int:
    """Files the pass has put on disk so far, the factory's own view.

    Vendored blocks, VCS internals and the progress logs themselves are not
    the agent's output. Best-effort: an unreadable tree counts as zero.
    """
    skip = {"vendor", ".git", "node_modules", "__pycache__"}
    total = 0
    try:
        for path in Path(root).rglob("*"):
            if not path.is_file():
                continue
            parts = set(path.relative_to(root).parts)
            if parts & skip or path.name.startswith("writer_progress"):
                continue
            total += 1
    except OSError:
        return total
    return total


def heartbeat_line(elapsed_s: float, files: int, steps_seen: int) -> str:
    """What the factory can honestly say about a quiet agent.

    Observed facts only -- elapsed time, files on disk, steps reported. It
    never claims a step the agent did not report.
    """
    minutes, seconds = divmod(int(elapsed_s), 60)
    clock = f"{minutes}m{seconds:02d}s"
    if steps_seen == 0 and files == 0:
        return (
            f"{HEARTBEAT_PREFIX} \u2014 {clock} in, still on its first pass: reading "
            "the brief and the cloned blocks. No files yet; the first steps "
            "usually land around the 10 minute mark."
        )
    return (
        f"{HEARTBEAT_PREFIX} \u2014 {clock} in, {files} file(s) on disk, "
        f"{steps_seen} step(s) reported so far."
    )


class _CliLogTailer:
    """Polls the CLI's own log file for new lines during a headless pass.

    The codewhale CLI writes its agent-loop progress (e.g. ``engine turn
    completion settled status=...``) to ``~/.codewhale/logs/*.log`` rather
    than stdout. Tailing it turns the 30-minute silent pass into a stream
    of named steps. Best-effort: no log dir is never an error.
    """

    def __init__(self, home: Optional[Path] = None) -> None:
        self._dir: Optional[Path] = None
        self._file: Optional[Path] = None
        self._offset = 0
        self._last: Optional[float] = None
        candidates = []
        if home:
            candidates.append(home / "logs")
        candidates.extend(
            [
                Path.home() / ".codewhale" / "logs",
                Path(os.getenv("CODWHALE_HOME", "") or "") / "logs",
            ]
        )
        for candidate in candidates:
            try:
                if candidate.is_dir():
                    self._dir = candidate
                    break
            except OSError:
                continue

    def _pick(self) -> None:
        if self._dir is None:
            return
        try:
            candidates = sorted(
                (p for p in self._dir.iterdir() if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            candidates = []
        if not candidates:
            return
        newest = candidates[0]
        if newest != self._file:
            self._file = newest
            try:
                # The CLI rotates per run; only new lines from this file.\n"
                self._offset = newest.stat().st_size
            except OSError:
                self._offset = 0

    def pump(self, relay: Any) -> None:
        """Relay lines appended since the last pump. No-op when no logs."""
        if self._dir is None:
            return
        if self._file is None:
            self._pick()
            return
        try:
            stat = self._file.stat()
        except OSError:
            return
        if stat.st_size < self._offset:
            # Truncated/rotated: start from the current end.\n"
            self._offset = stat.st_size
            return
        if stat.st_size == self._offset:
            return
        try:
            with self._file.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
                self._offset = fh.tell()
        except OSError:
            return
        for raw in chunk.splitlines():
            if raw.strip():
                relay(_cli_log_line(raw))
        # Rotation check: the CLI may have opened a new file.\n"
        try:
            newest = max(
                (p for p in self._dir.iterdir() if p.is_file()),
                key=lambda p: p.stat().st_mtime,
                default=None,
            )
        except OSError:
            newest = None
        if newest is not None and newest != self._file:
            self._file = None
            self._offset = 0


def _cli_log_line(raw: str) -> str:
    """Strip the CLI log's timestamp/level prefix for Floor readability."""
    text = raw.strip()
    parts = text.split(" ", 2)
    if len(parts) == 3 and parts[0].startswith("20") and "T" in parts[0]:
        text = parts[2]
    return "codewhale log: " + text
