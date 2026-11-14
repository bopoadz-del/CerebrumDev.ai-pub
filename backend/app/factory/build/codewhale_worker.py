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
termination_reason, provider, model, tool outcomes, output. F-S1: the CLI
runs with its exec stream (``--output-format stream-json``); the receipt
also carries the Factory's own per-round timing of that stream and the
token counts the stream's ``turn_usage`` events report, when they report
any -- no count is invented.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, Iterator, List, Optional, Tuple

from app.factory.build.agent_process import (
    agent_popen_kwargs,
    kill_agent_tree,
    resume_agent_tree,
    suspend_agent_tree,
    track_agent,
)
from app.factory.build.coder_session_status import (
    CONTROL_PAUSE,
    CONTROL_STOP,
    OwnerStopped,
    check_owner,
    current_owner_control,
    owner_action,
)

logger = logging.getLogger("cerebrumdev.factory.codewhale_worker")

WORKER_CONCURRENCY_CAPPED = "worker_concurrency_capped"
WORKER_DISPATCH_AMBIGUOUS = "worker_dispatch_ambiguous"
WORKER_CLI_MISSING = "worker_cli_missing"
WORKER_EXEC_FAILED = "worker_exec_failed"
WORKER_TIMED_OUT = "worker_timed_out"
#: The progress watchdog's named stall. Distinct from WORKER_TIMED_OUT (the
#: wall): the wall bounds the whole pass; a stall is a pass that made NO
#: observable progress (no new step, no new file on disk, no new stdout line)
#: for the idle limit. The first stall retries; the second fails the phase.
WORKER_STALLED = "worker_stalled"

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
#: A specialist child slot asked for by a tenant that holds no writer slot
#: (F-S2c ruling (a): children exist only under a held writer slot).
SPECIALIST_WITHOUT_PARENT = "specialist_without_parent"

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
#: The coding-agent CLI's OWN per-turn wall-clock budget (codewhale 0.9.13:
#: env ``CODEWHALE_TURN_WALL_CLOCK_SECS`` / config ``[tui].turn_wall_clock_secs``,
#: default 3600s). The Factory never set it, so a turn the Factory had granted
#: 5400s stopped itself at the CLI default 3600s ("Per-turn wall-clock budget
#: exhausted after 3843s (limit: 3600s)", pilot b34dfdc). The env is read once
#: at CLI start, so the dispatch carries the largest wall the Factory may grant
#: this pass (ruling F-W2c) and the Factory's own wall stays the binding one.
CLI_TURN_WALL_ENV = "CODEWHALE_TURN_WALL_CLOCK_SECS"
#: Idle limit for the PROGRESS watchdog -- a separate clock from the wall. A
#: live-but-silent writer sits inside one model call that never returns for
#: the whole wall (09:38Z: one DeepSeek call, 30 min, written=0); the wall
#: cannot see that, only a progress clock can. No new step, no new file on
#: disk and no new stdout line for this long is a stall.
DEFAULT_IDLE_S = 480.0
IDLE_ENV = "FACTORY_CODEWHALE_IDLE_S"
#: One fresh-process retry after a stall, as ordered. The second stall fails.
WORKER_STALL_RETRIES = 1
#: Seconds to wait after a ``worker_stalled`` event before re-dispatching the
#: one fresh-process retry. A provider blip that caused the stall needs time
#: to recover; an instant re-dispatch burns the second pass on the same
#: failing upstream (live 2026-11-11: retry written=0, stub_rate=1.0).
STALL_RETRY_BACKOFF_S = 120.0
#: The writer CLI's own runtime home, pinned inside the checkout so the
#: tailer can read its agent-loop log. It is the agent's state, never its
#: output: a stalled writer that keeps churning its own logs/state must not
#: look like it is authoring files (live 2026-11-11 pilot 43013469394:
#: written=0, stub_rate=1.0 while ``count_authored_files`` rose 8 -> 136 on
#: the CLI's own home, resetting the idle clock so the 480s stall never
#: fired and only the 1800s wall ended the pass).
WORKER_CLI_HOME_DIR = ".codewhale-home"

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


class _WorkerStall(Exception):
    """A headless pass made no observable progress for the idle limit.

    Internal signal only. It carries the facts the ``worker_stalled`` ledger
    event reports: how long the pass was idle, and how many files/steps it had
    produced when it stalled. ``run_worker_job`` raises it; the retry wrapper
    turns the FINAL stall into a named ``worker_stalled`` failure -- never the
    wall's ``worker_timed_out``.
    """

    def __init__(self, idle_s: float, files: int, steps: int) -> None:
        super().__init__(
            f"{WORKER_STALLED}: idle for {idle_s:.0f}s "
            f"({files} files on disk, {steps} steps reported)"
        )
        self.idle_s = idle_s
        self.files = files
        self.steps = steps


def _preflight_probe(
    cli: str,
    runner: Callable[..., Any] = subprocess.run,
    clock: Callable[[], float] = time.perf_counter,
    timeout: float = 10.0,
) -> Tuple[bool, str]:
    """One ``cli --version`` probe with a single cold-start retry.

    Returns (ok, detail). ``ok`` is False when both attempts failed or the
    retry exited non-zero; ``detail`` carries the measured timings and stderr
    so a failed probe reports its cause, never a bare 'timeout' (0930Z BLOCKER).
    """

    def _once() -> Tuple[Optional[Any], float, str]:
        started = clock()
        try:
            proc = runner(
                [cli, "--version"],
                capture_output=True,
                text=True,
                timeout=timeout,
                stdin=subprocess.DEVNULL,
            )
            return proc, clock() - started, (proc.stderr or "")[-300:]
        except subprocess.TimeoutExpired:
            return None, clock() - started, f"timeout after {timeout}s"
        except OSError as exc:
            return None, clock() - started, f"{type(exc).__name__}: {exc}"

    proc, first_s, first_err = _once()
    if proc is not None and proc.returncode == 0:
        return True, f"ok ({first_s:.1f}s)"
    # Cold start: the first invocation warms the page cache; retry once so a
    # healthy-but-cold binary is not rejected.
    proc, second_s, second_err = _once()
    if proc is not None and proc.returncode == 0:
        return True, f"ok after retry (first {first_s:.1f}s, retry {second_s:.1f}s)"
    rc = getattr(proc, "returncode", "timeout")
    return False, f"first {first_s:.1f}s ({first_err}); retry {second_s:.1f}s ({second_err}); rc={rc}"


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
    stored config is the credential source (local dev).

    The writer is the SAME LLM as Floor chat, so it shares the platform's
    authoritative key -- ``CEREBRUM_CHAT_LLM_API_KEY`` -- rather than a separate
    export. ``DEEPSEEK_API_KEY`` is only a legacy last resort: a stale one left
    in the deploy env (a rotated key) must not shadow the working platform key,
    which is what stalled the live Fleet build at WRITER. An explicit
    ``CODEWHALE_API_KEY`` still overrides both when a deployment wants the
    writer on a distinct credential.
    """
    return (
        os.getenv("CODEWHALE_API_KEY", "").strip()
        or os.getenv("CEREBRUM_CHAT_LLM_API_KEY", "").strip()
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


def worker_idle_s() -> float:
    """Seconds of observable silence before a pass is declared stalled.

    Distinct from ``worker_timeout_s`` (the wall): this bounds silence, not
    elapsed time. A pass that emits no new step, no new file on disk and no
    new stdout line for this long is killed and retried once.
    """
    return float(os.getenv(IDLE_ENV, str(DEFAULT_IDLE_S)))


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
    #: F-S1: per-round timing summary measured by the Factory from the CLI's
    #: exec stream (``writer_round_stats``). Empty when the CLI printed only
    #: a summary (no stream to measure).
    rounds: Dict[str, Any] = field(default_factory=dict)

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
            "rounds": dict(self.rounds),
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
        # Specialist children in flight per parent writer key (F-S2c). Same
        # lock, popped at zero, counted in _process_active only.
        self._children: Dict[str, int] = {}

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
            waiter = {
                "key": key,
                "reserved": bool(reserved),
                "seq": self._seq,
                # A specialist child yields to this waiter (F-S2c), and must
                # judge it against the waiter's own tenant cap.
                "tenant_cap": tenant_cap,
            }
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

    # -- specialist children (F-S2c ruling (a)) ----------------------------

    def _child_admissible(self, key: str, process_cap: int, child_cap: int) -> bool:
        """Whether one more specialist child of ``key``'s held writer slot may
        start now. Caller holds the lock.

        A child is counted against the PROCESS cap only (never the tenant
        cap: it is part of the writer job that already holds the tenant's
        slot), bounded per parent by ``child_cap``, and it never takes the
        reserved slot. It also yields to every queued JOB that could start:
        a child never starves another build's queued writer."""
        if self._children.get(key, 0) >= child_cap:
            return False
        user_cap = process_cap - reserved_slots(process_cap)
        if self._process_active - self._reserved_active >= user_cap:
            return False
        return not any(
            self._admissible(waiter, process_cap, waiter["tenant_cap"])
            for waiter in self._waiters
        )

    def acquire_child(
        self,
        key: str,
        *,
        process_cap: int,
        child_cap: int,
        time_left: Callable[[], Optional[float]],
        poll_s: float = SLOT_WAIT_POLL_S,
    ) -> Dict[str, Any]:
        """Take one specialist child slot under ``key``'s HELD writer slot,
        waiting while none is free; returns ``{"waited_s", "children"}``.

        Refused by name when ``key`` holds no writer slot: a child has no
        slot class of its own outside the writer job it belongs to."""
        import time as _time

        started = _time.monotonic()
        with self._turn:
            if self._tenant_active.get(key, 0) <= 0:
                raise WorkerError(
                    f"{SPECIALIST_WITHOUT_PARENT}: tenant {key[:8]} holds no "
                    "writer slot -- a specialist child runs only under its "
                    "writer's held slot"
                )
            while not self._child_admissible(key, process_cap, child_cap):
                left = time_left()
                if left is not None and left <= 0:
                    raise WorkerError(
                        f"{SLOT_WAIT_EXHAUSTED}: a specialist child waited "
                        f"{_time.monotonic() - started:.0f}s for a process slot "
                        "and the run's phase-wall ceiling is spent"
                    )
                wait_for = poll_s if left is None else max(0.01, min(poll_s, left))
                self._turn.wait(timeout=wait_for)
            self._process_active += 1
            self._children[key] = self._children.get(key, 0) + 1
            held = self._children[key]
        return {"waited_s": round(_time.monotonic() - started, 1), "children": held}

    def release_child(self, key: str) -> None:
        """Give a child slot back. Mirrors acquire_child; runs from a finally."""
        with self._lock:
            self._process_active -= 1
            remaining = self._children.get(key, 0) - 1
            if remaining > 0:
                self._children[key] = remaining
            else:
                self._children.pop(key, None)
            self._turn.notify_all()

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


@contextmanager
def specialist_child_slot(
    tenant_store: Any,
    *,
    wait_left: Callable[[], Optional[float]],
    child_cap: Optional[int] = None,
) -> Iterator[Dict[str, Any]]:
    """One specialist session's slot: a CHILD of the tenant's held writer
    slot (F-S2c ruling (a)).

    Counted against the PROCESS cap only and bounded per writer by
    ``writer_specialist_cap()`` -- no new number, no ceiling change. It waits
    (never refuses) while the box is full or another build's writer is
    queued, bounded by ``wait_left``; a tenant holding no writer slot is
    refused by name. Yields ``{"waited_s", "children"}``.
    """
    _require_bound_tenant(tenant_store)
    key = _tenant_slot_key(tenant_store)
    cap = writer_specialist_cap() if child_cap is None else int(child_cap)
    record = _COUNTER.acquire_child(
        key,
        process_cap=worker_process_cap(),
        child_cap=max(1, cap),
        time_left=wait_left,
    )
    try:
        yield record
    finally:
        _COUNTER.release_child(key)


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
    from app.factory.build.agent_confine import scoped_child_env

    # The CLI reads its provider key from the environment, and prefers it
    # over any flag. A stale provider key left in os.environ (a rotated key)
    # must not shadow the key worker_api_key() resolved -- that shadow stalled
    # a live build at WRITER on an invalid-key auth error. Stamp the resolved
    # key onto the vars the CLI consults. The rest of the server environment
    # is not copied.
    extras: Dict[str, str] = {}
    provider_key = None
    resolved_key = worker_api_key()
    if resolved_key:
        extras["CODEWHALE_API_KEY"] = resolved_key
        provider_var = f"{worker_provider().upper()}_API_KEY"
        if provider_var.replace("_", "").isalnum():
            extras[provider_var] = resolved_key
            provider_key = provider_var
    sid = str(session_id or "").strip()
    if sid:
        extras["FACTORY_SESSION_ID"] = sid
        extras["FACTORY_CLI_PIVOT_SESSION_ID"] = sid
    if cli_home:
        extras["CODWHALE_HOME"] = cli_home
    return scoped_child_env(
        os.environ,
        extras,
        home=cli_home,
        tmp=cli_home,
        provider_key=provider_key,
    )


def _parse_writer_receipt(
    stdout_lines: List[str], returncode: int
) -> WorkerReceipt:
    """Build the writer receipt from the CLI's captured stdout.

    The CLI prints its receipt JSON to stdout on every exit path, including a
    non-zero one. A DNS/model failure exits 75 while still printing a receipt
    whose ``error_category=network`` / ``termination_reason=model_error`` -- the
    caller retries those, so the receipt must be parsed and RETURNED before the
    non-zero exit is raised (Advisor 13:12Z, pilot-live 42633433251).

    Two stdout shapes are read. The exec stream (``--output-format
    stream-json``, one ``codewhale.exec-stream`` event per line) carries the
    receipt in its terminal ``metadata`` event -- the same status /
    termination_reason / provider / model / error fields the ``--json``
    summary printed -- and the tool rounds as ``tool_use`` events. A stdout
    with no stream events is read as the legacy single summary object.
    """
    events = exec_stream_events(stdout_lines)
    if events:
        return _receipt_from_stream(events, stdout_lines, returncode)
    payload = None
    candidates = [stdout_lines[-1]] if stdout_lines else []
    candidates.append("\n".join(stdout_lines))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            payload = parsed
            break
    if payload is None:
        if returncode != 0:
            tail = "\n".join(stdout_lines)[-400:]
            raise WorkerError(
                f"{WORKER_EXEC_FAILED}: exit {returncode}: {tail}"
            )
        raise WorkerError(
            f"{WORKER_EXEC_FAILED}: non-JSON summary from the CLI"
        )
    return WorkerReceipt(
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


#: The schema tag every exec-stream event carries (codewhale 0.9.13,
#: ``exec --output-format stream-json``; observed as ``"schema":
#: "codewhale.exec-stream", "schema_version": 1`` on every line).
EXEC_STREAM_SCHEMA = "codewhale.exec-stream"
EXEC_STREAM_FLAGS = ("--output-format", "stream-json")
#: Event fields that name a tool, a token count or a sub-agent. Read in this
#: order; the first one present wins. Absent fields are never invented.
_TOOL_NAME_KEYS = ("name", "tool_name", "tool")
_PROMPT_TOKEN_KEYS = ("input_tokens", "prompt_tokens")
_COMPLETION_TOKEN_KEYS = ("output_tokens", "completion_tokens")
_AGENT_LABEL_KEYS = ("label", "name", "role", "agent_type", "subagent_type", "agent")
_AGENT_ID_KEYS = ("run_id", "agent_id", "id", "tool_id")
_STREAM_HOUSEKEEPING_KEYS = ("schema", "schema_version", "input")


def exec_stream_event(line: str) -> Optional[Dict[str, Any]]:
    """One stdout line as an exec-stream event, or None when it is not one."""
    text = (line or "").strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or parsed.get("schema") != EXEC_STREAM_SCHEMA:
        return None
    return parsed


def exec_stream_events(stdout_lines: List[str]) -> List[Dict[str, Any]]:
    """Every exec-stream event in the captured stdout, in arrival order."""
    out = []
    for line in stdout_lines:
        event = exec_stream_event(line)
        if event is not None:
            out.append(event)
    return out


def _first_present(source: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return None


def _input_bytes(event: Dict[str, Any]) -> int:
    value = event.get("input")
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (TypeError, ValueError):
        return 0


def _tool_record(event: Dict[str, Any]) -> Dict[str, Any]:
    """A tool_use event as a receipt tool entry: its own fields, minus the
    stream framing and the (possibly whole-file) input, plus its size."""
    record = {
        k: v for k, v in event.items() if k not in _STREAM_HOUSEKEEPING_KEYS
    }
    record["input_bytes"] = _input_bytes(event)
    return record


def _usage_tokens(event: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    """(prompt, completion) tokens a usage event reports, None when absent."""
    sources = [event]
    for nested in ("usage", "meta"):
        if isinstance(event.get(nested), dict):
            sources.insert(0, event[nested])
    prompt = completion = None
    for source in sources:
        if prompt is None:
            value = _first_present(source, _PROMPT_TOKEN_KEYS)
            prompt = value if isinstance(value, int) else None
        if completion is None:
            value = _first_present(source, _COMPLETION_TOKEN_KEYS)
            completion = value if isinstance(value, int) else None
    return prompt, completion


def _receipt_from_stream(
    events: List[Dict[str, Any]], stdout_lines: List[str], returncode: int
) -> WorkerReceipt:
    metas = [
        e.get("meta")
        for e in events
        if e.get("type") == "metadata" and isinstance(e.get("meta"), dict)
    ]
    if not metas:
        errors = [str(e.get("error")) for e in events if e.get("type") == "error"]
        tail = (errors[-1] if errors else "\n".join(stdout_lines))[-400:]
        if returncode != 0:
            raise WorkerError(f"{WORKER_EXEC_FAILED}: exit {returncode}: {tail}")
        raise WorkerError(
            f"{WORKER_EXEC_FAILED}: exec stream ended without a metadata receipt"
        )
    meta = metas[-1]
    listed = meta.get("tools")
    if isinstance(listed, list):
        tools = [dict(t) for t in listed if isinstance(t, dict)]
    else:
        tools = [_tool_record(e) for e in events if e.get("type") == "tool_use"]
    output = meta.get("output")
    if output is None:
        output = meta.get("visible_final_answer_excerpt")
    return WorkerReceipt(
        status=str(meta.get("status") or ""),
        termination_reason=meta.get("termination_reason"),
        provider=str(meta.get("provider") or ""),
        model=str(meta.get("model") or ""),
        output=str(output or ""),
        tools=tools,
        error=meta.get("error"),
        error_category=meta.get("error_category"),
    )


def _percentile(values: List[float], pct: float) -> float:
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-pct * len(ordered) // 100))))
    return ordered[rank - 1]


def writer_round_stats(
    timed_events: List[Tuple[float, Dict[str, Any]]],
    end_t: Optional[float] = None,
) -> Dict[str, Any]:
    """Per-round timing of one writer session, measured by the Factory.

    ``timed_events`` are exec-stream events with the monotonic second each
    line ARRIVED on the worker's stdout -- the Factory's clock, so the
    numbers never depend on a timestamp format the CLI may change. A round
    is one ``tool_use`` event; its wall is the time to the next round (the
    last round runs to ``end_t``, the moment the session ended), so it
    covers the model's think time plus the tool's own run.

    Token counts are the last ``turn_usage`` the stream reported; absent
    usage fields leave the keys out rather than invent a zero. Specialists
    are ``agent_spawned`` events; a specialist's wall runs from its spawn to
    the last later event that names the same id, or to ``end_t`` when the
    stream never names it again (counted in ``specialists_unended``).
    """
    ordered = sorted(
        ((float(t), e) for t, e in timed_events if isinstance(e, dict)),
        key=lambda pair: pair[0],
    )
    round_times = [t for t, e in ordered if e.get("type") == "tool_use"]
    walls = [b - a for a, b in zip(round_times, round_times[1:])]
    if round_times and end_t is not None and end_t >= round_times[-1]:
        walls.append(end_t - round_times[-1])
    stats: Dict[str, Any] = {"rounds": len(round_times)}
    if walls:
        stats["round_p50_s"] = round(_percentile(walls, 50), 1)
        stats["round_p95_s"] = round(_percentile(walls, 95), 1)
        stats["round_max_s"] = round(max(walls), 1)
    usage_turns = 0
    for _, event in ordered:
        if event.get("type") != "turn_usage":
            continue
        prompt, completion = _usage_tokens(event)
        if prompt is None and completion is None:
            continue
        usage_turns += 1
        if prompt is not None:
            stats["prompt_tokens_last"] = prompt
        if completion is not None:
            stats["completion_tokens_last"] = completion
    if usage_turns:
        stats["usage_turns"] = usage_turns

    specialists: List[Dict[str, Any]] = []
    for index, (spawn_t, event) in enumerate(ordered):
        if event.get("type") != "agent_spawned":
            continue
        label = _first_present(event, _AGENT_LABEL_KEYS)
        ident_key = next(
            (k for k in _AGENT_ID_KEYS if event.get(k) not in (None, "")), None
        )
        end_seen: Optional[float] = None
        if ident_key is not None:
            ident = event[ident_key]
            for later_t, later in ordered[index + 1 :]:
                if later.get("type") == "agent_spawned":
                    continue
                if any(later.get(k) == ident for k in _AGENT_ID_KEYS):
                    end_seen = later_t
        stop = end_seen if end_seen is not None else end_t
        specialists.append(
            {
                "label": str(label) if label is not None else "",
                "start": spawn_t,
                "stop": stop,
                "ended": end_seen is not None,
            }
        )
    stats["specialists"] = len(specialists)
    if specialists:
        points = []
        for s in specialists:
            points.append((s["start"], 1))
            if s["stop"] is not None:
                points.append((s["stop"], -1))
        # An end at the same instant as another start is not an overlap.
        points.sort(key=lambda p: (p[0], p[1]))
        live = peak = 0
        for _, delta in points:
            live += delta
            peak = max(peak, live)
        stats["max_concurrent"] = peak
        stats["specialists_unended"] = sum(1 for s in specialists if not s["ended"])
        stats["specialist_walls"] = [
            {
                "label": s["label"],
                "wall_s": (
                    round(s["stop"] - s["start"], 1)
                    if s["stop"] is not None
                    else None
                ),
                "ended": s["ended"],
            }
            for s in specialists
        ]
    return stats


def stream_progress_record(
    event: Dict[str, Any], next_round: int
) -> Optional[Dict[str, Any]]:
    """The docs/writer_progress.jsonl record for one exec-stream event.

    A ``tool_use`` is round ``next_round`` (tool name, bytes of input); a
    ``turn_usage`` records the token counts it reports (none when it reports
    none); ``agent_spawned`` records the specialist's label. Other events
    write nothing here (the receipt keeps them for timing).
    """
    kind = event.get("type")
    if kind == "tool_use":
        name = _first_present(event, _TOOL_NAME_KEYS)
        return {
            "round": next_round,
            "tool": str(name) if name is not None else "",
            "bytes": _input_bytes(event),
        }
    if kind == "turn_usage":
        prompt, completion = _usage_tokens(event)
        record: Dict[str, Any] = {"turn_usage": True}
        if prompt is not None:
            record["prompt_tokens"] = prompt
        if completion is not None:
            record["completion_tokens"] = completion
        return record
    if kind == "agent_spawned":
        label = _first_present(event, _AGENT_LABEL_KEYS)
        return {"specialist_spawned": str(label) if label is not None else ""}
    return None


#: Keys a shell tool's input may carry its command line under.
_SHELL_INPUT_KEYS = ("command", "cmd", "script", "args", "argv")
#: Tokens that end one shell command and start the next.
_SHELL_SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})
#: pytest options whose value is the NEXT token (never a test path).
_PYTEST_VALUE_OPTS = frozenset(
    {
        "-m", "-k", "-p", "-c", "-o", "-W", "-n", "--rootdir", "--deselect",
        "--ignore", "--ignore-glob", "--maxfail", "--tb", "--confcutdir",
        "--basetemp", "--junitxml", "--durations", "--timeout", "--import-mode",
    }
)


def _command_text(event: Dict[str, Any]) -> str:
    """The command line a tool_use event runs, '' when it carries none."""
    value = event.get("input")
    if isinstance(value, dict):
        value = _first_present(value, _SHELL_INPUT_KEYS)
    if isinstance(value, (list, tuple)):
        return " ".join(str(v) for v in value)
    return value if isinstance(value, str) else ""


def pytest_selections(command: str) -> List[List[str]]:
    """Every pytest invocation in a shell command line, as the positional
    arguments (test paths / node ids) it selects. [] when none runs pytest.

    Recognised by shape only: a ``pytest`` / ``py.test`` executable, or any
    command whose arguments run ``-m pytest`` (an interpreter, whatever its
    name).
    """
    import shlex

    try:
        tokens = shlex.split(command or "", posix=True)
    except ValueError:
        tokens = (command or "").split()
    out: List[List[str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        base = tok.replace("\\", "/").rsplit("/", 1)[-1].lower()
        start: Optional[int] = None
        if base in ("pytest", "py.test", "pytest.exe"):
            start = i + 1
        elif (
            tok not in _SHELL_SEPARATORS
            and tokens[i + 1 : i + 3] == ["-m", "pytest"]
        ):
            start = i + 3
        if start is None:
            i += 1
            continue
        selected: List[str] = []
        j = start
        while j < len(tokens) and tokens[j] not in _SHELL_SEPARATORS:
            arg = tokens[j]
            if arg in _PYTEST_VALUE_OPTS:
                j += 2
                continue
            if not arg.startswith("-"):
                selected.append(arg)
            j += 1
        out.append(selected)
        i = j
    return out


def _norm_test_path(arg: str) -> str:
    import posixpath

    path = arg.split("::", 1)[0].replace("\\", "/")
    return posixpath.normpath(path)


def is_suite_run(selection: List[str], targeted: Optional[List[str]]) -> bool:
    """True when a pytest invocation is NOT confined to the targeted set.

    No path selects the whole collection; a path that is not a test FILE (a
    directory) selects everything under it; a file outside ``targeted`` (the
    self-check's selection at that moment) is not targeted. ``targeted`` None
    means the set could not be computed: only the shape is judged.
    """
    if not selection:
        return True
    allowed = (
        None if targeted is None else {_norm_test_path(t) for t in targeted}
    )
    for arg in selection:
        path = _norm_test_path(arg)
        if not path.endswith(".py"):
            return True
        if allowed is not None and path not in allowed:
            return True
    return False


def targeted_tests_now(root: Path) -> Optional[List[str]]:
    """The targeted set the stamped self-check would run from ``root`` now.

    Executes the Factory's OWN rendered self-check source (never the
    workspace copy the writer can edit) with its ``__file__`` at the
    workspace stamp, so "changed this pass" is read off the same marker the
    self-check uses. None when it cannot be computed.
    """
    try:
        from app.factory.build.writer_behaviour import (
            SELF_CHECK_REL,
            render_self_check,
        )

        namespace: Dict[str, Any] = {
            "__name__": "factory_checks_targeted",
            "__file__": str(Path(root) / SELF_CHECK_REL),
        }
        exec(compile(render_self_check(), SELF_CHECK_REL, "exec"), namespace)  # noqa: S102 -- Factory-rendered source
        changed = namespace["_changed_sources"](str(root))
        return list(
            namespace["select_targeted_tests"](
                changed, namespace["_test_files"](str(root))
            )
        )
    except Exception:  # noqa: BLE001 -- an uncomputable set is reported as None, never as empty
        return None


def suite_runs_in(
    event: Dict[str, Any], targeted_for: Any
) -> int:
    """How many pytest invocations in one tool_use are not the targeted set.

    ``targeted_for`` is called (once, only when the event runs pytest) for
    the targeted set at that moment.
    """
    if event.get("type") != "tool_use":
        return 0
    selections = pytest_selections(_command_text(event))
    if not selections:
        return 0
    targeted = targeted_for()
    return sum(1 for s in selections if is_suite_run(s, targeted))


def suite_denied_count(denials: Optional[List[Dict[str, Any]]]) -> Optional[int]:
    """Refused pytest runs this pass; None (printed ``-``) when the policy's
    denial log could not be read -- unreadable never reads as 0."""
    return None if denials is None else len(denials)


#: Seconds between two RSS samples of a running writer child's process tree.
RSS_SAMPLE_EVERY_S = 5.0


def tree_rss_mb(pid: Optional[int], proc_root: str = "/proc") -> Optional[float]:
    """Resident memory of ``pid`` and every descendant, in MB, read from
    ``proc_root``; None when the process table cannot be read (not Linux,
    or the process is gone) or there is no process id to read it for.

    F-S2c: PER_JOB_MB is an ESTIMATE; this is the measurement. One child's
    peak rides its receipt (``peak_rss_mb``) into the ledger, and that number
    decides whether the specialist cap holds."""
    if isinstance(pid, bool) or not isinstance(pid, int):
        return None
    root = Path(proc_root)
    parents: Dict[int, int] = {}
    rss_kb: Dict[int, int] = {}
    try:
        entries = [e for e in root.iterdir() if e.name.isdigit()]
    except OSError:
        return None
    for entry in entries:
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fields = dict(
            line.split(":", 1) for line in status.splitlines() if ":" in line
        )
        try:
            parents[int(entry.name)] = int(fields.get("PPid", "0").strip() or 0)
            rss_kb[int(entry.name)] = int((fields.get("VmRSS", "0 kB").split() or ["0"])[0])
        except ValueError:
            continue
    if pid not in parents:
        return None
    tree = {pid}
    grew = True
    while grew:
        grew = False
        for child, parent in parents.items():
            if parent in tree and child not in tree:
                tree.add(child)
                grew = True
    return round(sum(rss_kb.get(p, 0) for p in tree) / 1024.0, 1)


def rounds_summary_line(stats: Dict[str, Any]) -> str:
    """The receipt's round summary as ``key=value`` pairs; ``-`` = not reported."""
    keys = (
        "rounds",
        "round_p50_s",
        "round_p95_s",
        "prompt_tokens_last",
        "specialists",
        "max_concurrent",
        "suite_runs",
        "suite_denied",
    )
    parts = [f"{k}={stats[k] if stats.get(k) is not None else '-'}" for k in keys]
    # F-S2c: the child's measured peak, only when one was read.
    if stats.get("peak_rss_mb") is not None:
        parts.append(f"peak_rss_mb={stats['peak_rss_mb']}")
    walls = stats.get("specialist_walls") or []
    if walls:
        parts.append(
            "specialist_walls="
            + ",".join(
                f"{w.get('label') or '?'}:"
                f"{w['wall_s'] if w.get('wall_s') is not None else '-'}s"
                for w in walls
            )
        )
    return " ".join(parts)


#: Hosts the writer must resolve; probed from inside the confinement before the
#: writer CLI starts so a deterministic DNS failure is attributed, not assumed.
_DNS_PROBE_HOSTS = ("api.deepseek.com", "api.github.com")

_DNS_PROBE_CODE = (
    "import json, socket, sys, time\n"
    "out = {}\n"
    "for host in sys.argv[1:]:\n"
    "    t0 = time.monotonic()\n"
    "    try:\n"
    "        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)\n"
    "        out[host] = {'resolved': sorted({a[4][0] for a in infos}),\n"
    "                     'elapsed_ms': round((time.monotonic() - t0) * 1000, 1)}\n"
    "    except Exception as exc:\n"
    "        out[host] = {'error': f'{type(exc).__name__}: {exc}',\n"
    "                     'elapsed_ms': round((time.monotonic() - t0) * 1000, 1)}\n"
    "try:\n"
    "    rc = open('/etc/resolv.conf', 'rb').read().decode('utf-8', 'replace')\n"
    "    out['_resolv_conf'] = {'ok': True, 'text': rc.strip()[:240], 'bytes': len(rc)}\n"
    "except Exception as exc:\n"
    "    out['_resolv_conf'] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}\n"
    "try:\n"
    "    open('/etc/nsswitch.conf', 'rb').read()\n"
    "    out['_nsswitch_conf'] = {'ok': True}\n"
    "except Exception as exc:\n"
    "    out['_nsswitch_conf'] = {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}\n"
    "print(json.dumps(out, sort_keys=True))\n"
)


def _dns_probe(
    cwd: Path,
    env: Dict[str, str],
    *,
    hosts=_DNS_PROBE_HOSTS,
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    """Resolve the writer's hosts INSIDE the confinement.

    Runs ``getaddrinfo`` in a child that carries the same scoped environment
    and the same Landlock ruleset the writer CLI runs under, so a deterministic
    resolver failure is measured, not assumed (Advisor 14:42Z). Returns
    ``{host: {resolved|error, elapsed_ms}}``, or ``{"_probe_error": ...}``.
    """
    from app.factory.build.agent_confine import (
        ConfinementUnavailable,
        confinement_popen_kwargs,
    )

    try:
        kwargs = confinement_popen_kwargs(cwd, sys.executable)
    except ConfinementUnavailable as exc:
        return {"_probe_error": f"confinement unavailable: {exc}"}
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _DNS_PROBE_CODE, *hosts],
            cwd=str(cwd),
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_s,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        return {"_probe_error": "timeout"}
    except OSError as exc:
        return {"_probe_error": f"spawn failed: {exc}"}
    except Exception as exc:  # noqa: BLE001 -- telemetry must never fail the writer
        # A mocked/partial Popen in tests, a Landlock surprise, etc. The probe
        # is diagnostic: any failure is recorded, never raised.
        return {"_probe_error": f"{type(exc).__name__}: {exc}"}
    if proc.returncode != 0:
        return {"_probe_error": f"exit {proc.returncode}: {proc.stderr.strip()[:200]}"}
    try:
        return json.loads(proc.stdout)
    except ValueError:
        return {"_probe_error": f"non-JSON stdout: {proc.stdout[:200]}"}


def _fmt_dns_probe(v: Any) -> str:
    """One probe result as a short human string for the ledger NOTE."""
    if not isinstance(v, dict):
        return str(v)
    if "resolved" in v:
        addrs = ",".join(v["resolved"][:3])
        return f"resolved {addrs} ({v['elapsed_ms']}ms)"
    return f"error {v.get('error', '?')} ({v.get('elapsed_ms', '?')}ms)"


def cli_turn_wall_secs(
    timeout_s: float, turn_wall_s: Optional[float] = None
) -> int:
    """The CLI per-turn budget for one dispatch: never below the Factory's
    dispatch wall, raised to ``turn_wall_s`` (the largest wall the runner may
    still grant this pass) when that is larger. Whole seconds, rounded up, so
    the CLI never ends a turn before the Factory's own wall would."""
    import math

    bound = float(timeout_s or 0.0)
    if turn_wall_s is not None:
        try:
            bound = max(bound, float(turn_wall_s))
        except (TypeError, ValueError):
            pass
    return max(1, int(math.ceil(bound)))


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
    turn_wall_s: Optional[float] = None,
    slot_scope: Optional[Callable[[], Any]] = None,
    control: Optional[Callable[[], str]] = None,
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

    ``turn_wall_s`` (ruling F-W2c): the largest wall the runner may grant
    this pass (phase-bounded, ceiling-capped). The CLI's own per-turn budget
    is stamped from it at dispatch (``CLI_TURN_WALL_ENV``, never below the
    dispatch wall) because the CLI cannot take a raise once running; the two
    budgets then never disagree and the Factory's wall is the one that binds.

    ``control`` (F-U1) is the owner's Pause / Stop, read on every wait tick;
    without one, the calling thread's installed control is used. Stop kills
    the CLI and everything it started and raises ``OwnerStopped``; Pause
    freezes the tree and neither the wall nor the idle watchdog counts the
    paused time; Resume lets it run on from where it was frozen.
    """
    control_read = control if control is not None else current_owner_control()
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
    # F-S2c: a session that runs under a slot its caller already holds (the
    # writer's own held slot, or a specialist child of it) is handed that
    # scope instead of taking a second job slot of its own.
    with slot_scope() if slot_scope is not None else worker_job_slot(
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
        # The provider is a flag; the KEY is NEVER passed on argv -- a value on
        # the command line shows in the process cmdline. It is stamped into the
        # child env (CODEWHALE_API_KEY / <PROVIDER>_API_KEY) by _child_env() and
        # the CLI reads it from there. Local dev with no env key falls back to
        # the CLI's own stored config.
        api_key = worker_api_key()
        if api_key:
            argv += ["--provider", worker_provider()]
        # F-S1: the exec STREAM, not the one-line ``--json`` summary (the two
        # flags are mutually exclusive in 0.9.13). The stream's terminal
        # ``metadata`` event carries the same receipt fields; its tool_use /
        # turn_usage / agent_spawned events are what the round timing reads.
        argv += ["exec", "--auto", *EXEC_STREAM_FLAGS]

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
            "codewhale writer dispatch: cli=%s provider=%s session=%s timeout=%ss "
            "cli_turn_wall=%ss",
            cli,
            worker_provider(),
            str(session_id or "")[:12] or "-",
            timeout,
            cli_turn_wall_secs(timeout, turn_wall_s),
        )
        # E3 part 0: pre-flight probe — a broken binary or a config prompt
        # must fail in seconds, not sit silent for the full worker wall.
        # Skipped when the cli path is a test double that does not exist.
        # One cold-start retry + measured cause in the failure detail.
        if Path(cli).exists():
            from app.factory.build.agent_confine import (
                ConfinementUnavailable,
                confinement_popen_kwargs,
            )

            try:
                probe_kw = confinement_popen_kwargs(cwd, cli)
            except ConfinementUnavailable as exc:
                raise WorkerError(f"{WORKER_EXEC_FAILED}: {exc}") from exc
            ok, detail = _preflight_probe(
                cli,
                runner=lambda argv, **kw: subprocess.run(
                    argv,
                    cwd=str(cwd),
                    env=_child_env(session_id, str(cwd / ".codewhale-home")),
                    **probe_kw,
                    **agent_popen_kwargs(),
                    **kw,
                ),
            )
            if not ok:
                raise WorkerError(
                    f"{WORKER_EXEC_FAILED}: pre-flight probe failed for {cli}: {detail}"
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
        # Monotonic arrival second of each stdout line (appended BEFORE the
        # line, so it is never shorter than stdout_lines): the round clock.
        stdout_times: List[float] = []
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
                        stdout_times.append(time.monotonic())
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

        from app.factory.build.agent_confine import (
            ConfinementUnavailable,
            confinement_popen_kwargs,
        )

        try:
            confine_kw = confinement_popen_kwargs(cwd, cli)
        except ConfinementUnavailable as exc:
            raise WorkerError(f"{WORKER_EXEC_FAILED}: {exc}") from exc
        child_env = _child_env(session_id, cli_home)
        turn_secs = cli_turn_wall_secs(timeout, turn_wall_s)
        child_env[CLI_TURN_WALL_ENV] = str(turn_secs)
        # F-S2a: the pytest tool policy rides the WRITER's env only (the
        # Factory's gate and TESTER runs never carry it). Stamped per pass;
        # an unwritable policy dir fails the dispatch like an unwritable
        # brief -- a writer without the policy is not the writer ordered.
        from app.factory.build.pytest_policy import read_denials, stamp_policy

        try:
            child_env.update(stamp_policy(cli_home, cwd))
        except OSError as exc:
            raise WorkerError(
                f"worker_policy_unwritable: could not stamp the pytest policy: {exc}"
            ) from exc
        # Advisor 14:42Z: measure DNS from INSIDE the confinement before the
        # writer CLI starts, so a deterministic resolver failure is attributed
        # (a typed ledger NOTE), not assumed. Telemetry only -- it never
        # blocks or fails the writer.
        probe = _dns_probe(cwd, child_env)

        probe_text = "; ".join(
            f"{h}: {_fmt_dns_probe(v)}" for h, v in sorted(probe.items())
        )
        if progress is not None:
            try:
                # A dedicated typed NOTE: the ``dns_probe`` field (in
                # WRITER_PROGRESS_FIELDS) carries the structured results; the
                # line text carries the human summary. No ``tool`` key, so the
                # probe note never masquerades as a CLI progress line.
                progress(
                    f"dns probe: {probe_text}"[:400],
                    {DNS_PROBE: probe},
                )
            except Exception:  # noqa: BLE001 -- telemetry never fails the build
                pass
        # No CLI (and so no model call) starts while the owner has paused
        # or stopped the build.
        check_owner(control_read)
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=child_env,
                # The agent's shell gets its own process group: a group-wide
                # signal it sends can never reach the Factory server.
                **confine_kw,
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
        # The last count a walk actually completed: an unreadable sample
        # repeats it (marked) instead of reporting an empty tree.
        last_files = {"n": 0}
        started_at = time.monotonic()
        # The progress watchdog's own clock -- separate from the wall. It
        # remembers when observable progress last landed and the snapshots it
        # compares against each tick: files on disk, steps reported, stdout
        # lines. ``quiet_clock`` covers the relayed lines (stderr, CLI log,
        # prompt steps); stdout and disk are tracked by count here because
        # the stdout reader runs on its own thread and never touches a clock.
        idle_limit = worker_idle_s()
        progress_state = {"t": started_at, "files": 0, "steps": 0, "stdout": 0}

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
            walked = authored_files_or_none(cwd)
            unreadable = walked is None
            if walked is not None:
                last_files["n"] = max(0, walked - files_at_start)
            files = last_files["n"]
            fields: Dict[str, Any] = {
                FILES_ON_DISK: files,
                STEPS_REPORTED: narration["steps"],
            }
            if unreadable:
                fields[FILES_UNREADABLE] = True
            _relay(
                heartbeat_line(
                    now - started_at, files, narration["steps"], unreadable=unreadable
                ),
                # The same facts as data, for the budget ramp: the line is for
                # people, these fields are what the inspector counts.
                fields,
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

        # F-S1: each exec-stream event is logged as it arrives (on THIS
        # thread, so docs/writer_progress.jsonl stays single-writer) and kept
        # with its arrival second for the receipt's round summary.
        stream_state: Dict[str, Any] = {
            "cursor": 0,
            "rounds": 0,
            "timed": [],
            "suite_runs": 0,
        }

        def _log_stream_events() -> None:
            upto = min(len(stdout_lines), len(stdout_times))
            while stream_state["cursor"] < upto:
                index = stream_state["cursor"]
                stream_state["cursor"] += 1
                event = exec_stream_event(stdout_lines[index])
                if event is None:
                    continue
                arrived = stdout_times[index]
                stream_state["timed"].append((arrived, event))
                record = stream_progress_record(event, stream_state["rounds"] + 1)
                if record is None:
                    continue
                if "round" in record:
                    stream_state["rounds"] += 1
                    # F-S1: a pytest run that is not the targeted set, read
                    # off the tool stream with the set computed NOW.
                    suites = suite_runs_in(event, lambda: targeted_tests_now(cwd))
                    if suites:
                        stream_state["suite_runs"] += suites
                        record = {**record, "suite_runs": suites}
                if progress_handle is not None:
                    try:
                        progress_handle.write(
                            json.dumps(
                                {
                                    "ts": datetime.now(timezone.utc).isoformat(),
                                    "elapsed_s": round(arrived - started_at, 1),
                                    **record,
                                }
                            )
                            + "\n"
                        )
                        progress_handle.flush()
                    except OSError:
                        pass

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

        # F-S2c: this child's measured peak RSS (its whole process tree).
        rss_state: Dict[str, Any] = {"peak": None, "next": 0.0}

        def _sample_rss() -> None:
            now = time.monotonic()
            if now < rss_state["next"]:
                return
            rss_state["next"] = now + RSS_SAMPLE_EVERY_S
            # A process handle with no pid is unmeasured, never a crash.
            sample = tree_rss_mb(getattr(proc, "pid", None))
            if sample is not None and (rss_state["peak"] is None or sample > rss_state["peak"]):
                rss_state["peak"] = sample

        _note_cli_started()
        wait_started = time.monotonic()
        deadline = wait_started + timeout
        pause: Dict[str, Any] = {"at": None, "frozen": False}
        try:
            while True:
                try:
                    proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    # F-U1: the owner's control comes before every clock.
                    action = owner_action(control_read)
                    if action == CONTROL_STOP:
                        kill_agent_tree(proc)
                        proc.wait()
                        raise OwnerStopped(
                            "owner stopped the build: the writer CLI and "
                            "everything it started were killed"
                        )
                    if action == CONTROL_PAUSE:
                        if pause["at"] is None:
                            pause["at"] = time.monotonic()
                            pause["frozen"] = suspend_agent_tree(proc)
                            _relay(
                                "owner paused the build: writer CLI "
                                + ("frozen" if pause["frozen"] else "could not be frozen"),
                                {"owner_paused": True, "frozen": pause["frozen"]},
                            )
                        continue
                    if pause["at"] is not None:
                        held = time.monotonic() - pause["at"]
                        if pause["frozen"]:
                            resume_agent_tree(proc)
                        pause.update({"at": None, "frozen": False})
                        # Paused time is not the writer's time: neither the
                        # wall nor the idle watchdog counts it.
                        deadline += held
                        progress_state["t"] += held
                        _relay(
                            f"owner resumed the build after {held:.0f}s paused",
                            {"owner_paused": False, "paused_s": round(held, 1)},
                        )
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
                    _log_stream_events()
                    _heartbeat()
                    _sample_rss()
                    # Progress watchdog -- a separate clock from the wall.
                    # Cheap progress (a new step, stdout line or relayed line)
                    # resets the clock without touching the disk; only when
                    # every cheap signal is quiet do we walk the tree for a
                    # new file, and only then does the idle counter run.
                    now = time.monotonic()
                    if (
                        narration["steps"] != progress_state["steps"]
                        or len(stdout_lines) != progress_state["stdout"]
                        or quiet_clock["last"] > progress_state["t"]
                    ):
                        progress_state.update(
                            {
                                "t": now,
                                "steps": narration["steps"],
                                "stdout": len(stdout_lines),
                            }
                        )
                    else:
                        walked = authored_files_or_none(cwd)
                        # An unreadable walk is no sample: it neither counts
                        # as new files nor as an empty tree.
                        files_now = (
                            progress_state["files"]
                            if walked is None
                            else max(0, walked - files_at_start)
                        )
                        if walked is not None:
                            last_files["n"] = files_now
                        if files_now != progress_state["files"]:
                            progress_state.update({"t": now, "files": files_now})
                        elif now - progress_state["t"] >= idle_limit:
                            kill_agent_tree(proc)
                            proc.wait()
                            # A stall is a named, recoverable event, never a wall
                            # kill: the wrapper retries once, then fails as
                            # ``worker_stalled`` -- never ``worker_timed_out``.
                            raise _WorkerStall(
                                now - progress_state["t"],
                                files_now,
                                narration["steps"],
                            )
        except BaseException:
            # Whatever ends this wait early (a stop raised from a relay, an
            # interrupt), the CLI tree never outlives it.
            if proc.poll() is None:
                kill_agent_tree(proc)
                proc.wait()
            raise
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
            _log_stream_events()
            session_end_t = time.monotonic()
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
        # Parse the receipt BEFORE the exit-code check: a DNS/model failure
        # makes the CLI exit non-zero (75) while STILL printing its receipt
        # JSON to stdout. Returning the receipt (rather than raising the exit
        # code) lets the caller retry error_category=network before the
        # writer_no_output abort (Advisor 13:12Z, pilot-live 42633433251).
        receipt = _parse_writer_receipt(stdout_lines, proc.returncode)
        if stream_state["timed"]:
            receipt = replace(
                receipt,
                rounds={
                    **writer_round_stats(stream_state["timed"], session_end_t),
                    "suite_runs": stream_state["suite_runs"],
                    "suite_denied": suite_denied_count(read_denials(cli_home)),
                },
            )
        if rss_state["peak"] is not None:
            receipt = replace(
                receipt, rounds={**receipt.rounds, "peak_rss_mb": rss_state["peak"]}
            )
        # E3 part 2: the verdict line.
        logger.info(
            "codewhale writer receipt: status=%s reason=%s provider=%s "
            "model=%s tools=%d error=%s %s",
            receipt.status,
            receipt.termination_reason or "-",
            receipt.provider,
            receipt.model,
            len(receipt.tools),
            receipt.error_category or "-",
            rounds_summary_line(receipt.rounds),
        )
        return receipt


def _emit_worker_stalled(
    progress: Optional[Callable[[str, Dict[str, Any]], None]],
    stall: _WorkerStall,
    attempt: int,
) -> None:
    """Emit the named ``worker_stalled`` ledger event (telemetry, never fatal)."""
    if progress is not None:
        try:
            progress(
                f"{WORKER_STALLED}: no new step, file or stdout line for "
                f"{stall.idle_s:.0f}s ({stall.files} files on disk, "
                f"{stall.steps} steps reported) -- pass {attempt} of "
                f"{WORKER_STALL_RETRIES + 1}",
                {
                    STALL_MARKER: WORKER_STALLED,
                    STALL_IDLE_S: round(stall.idle_s, 1),
                    STALL_FILES: stall.files,
                    STALL_STEPS: stall.steps,
                },
            )
        except Exception:  # noqa: BLE001 -- telemetry never fails the build
            pass


def _stall_retry_backoff(seconds: float) -> None:
    """Wait ``seconds`` before re-dispatching the retry pass.

    Split out from the wrapper so tests can record the call without sleeping.
    ``time.sleep(0)`` is a no-op, so a zeroed STALL_RETRY_BACKOFF_S keeps the
    unit suite fast.
    """
    time.sleep(seconds)


def run_worker_job_with_stall_retry(
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
    turn_wall_s: Optional[float] = None,
    slot_scope: Optional[Callable[[], Any]] = None,
    control: Optional[Callable[[], str]] = None,
) -> WorkerReceipt:
    """``run_worker_job`` with the progress watchdog's one-retry loop.

    A pass that goes idle for ``worker_idle_s`` seconds raises ``_WorkerStall``
    from inside ``run_worker_job``. This wrapper emits the ``worker_stalled``
    ledger event, kills the agent tree and retries ONCE with a fresh process on
    the same staged workspace. The second stall fails the phase as
    ``worker_stalled`` -- never the wall's ``worker_timed_out`` (the wall never
    fired).
    """
    for attempt in range(1, WORKER_STALL_RETRIES + 2):
        try:
            # The turn bound rides only when the runner set one, so a caller
            # with no bound dispatches exactly as before.
            extra: Dict[str, Any] = {}
            if turn_wall_s is not None:
                extra["turn_wall_s"] = turn_wall_s
            # F-S2c: a held (or child) slot rides the same way.
            if slot_scope is not None:
                extra["slot_scope"] = slot_scope
            if control is not None:
                extra["control"] = control
            return run_worker_job(
                prompt,
                checkout_dir,
                tenant_store=tenant_store,
                timeout_s=timeout_s,
                session_id=session_id,
                product_id=product_id,
                progress=progress,
                live_time_left=live_time_left,
                slot_wait_left=slot_wait_left,
                on_slot_wait=on_slot_wait,
                **extra,
            )
        except _WorkerStall as stall:
            _emit_worker_stalled(progress, stall, attempt)
            if attempt >= WORKER_STALL_RETRIES + 1:
                raise WorkerError(
                    f"{WORKER_STALLED}: headless job stalled "
                    f"{WORKER_STALL_RETRIES + 1} times; the last pass was idle "
                    f"for {stall.idle_s:.0f}s with no new step, file or stdout "
                    f"line ({stall.files} files on disk, "
                    f"{stall.steps} steps reported)"
                ) from stall
            _stall_retry_backoff(STALL_RETRY_BACKOFF_S)


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
DNS_PROBE = "dns_probe"
#: Set on a heartbeat whose tree walk raised: FILES_ON_DISK then repeats the
#: last count a walk completed, never a zero the tree did not have.
FILES_UNREADABLE = "files_unreadable"
WRITER_PROGRESS_FIELDS = (STEPS_REPORTED, FILES_ON_DISK, DNS_PROBE, FILES_UNREADABLE)
#: The ``worker_stalled`` ledger event: a named marker plus the facts the
#: order demands -- idle seconds, files at stall, steps at stall. Forwarded
#: by the writer relay exactly like WRITER_PROGRESS_FIELDS.
STALL_MARKER = "stall"
STALL_IDLE_S = "stall_idle_s"
STALL_FILES = "stall_files"
STALL_STEPS = "stall_steps"
WRITER_STALL_FIELDS = (STALL_MARKER, STALL_IDLE_S, STALL_FILES, STALL_STEPS)


def is_narration_line(line: str) -> bool:
    """Lines that ARE the narration: never throttled away by the relay."""
    text = str(line or "")
    return text.startswith(_STEP_PREFIX) or text.startswith(HEARTBEAT_PREFIX)


def count_authored_files(root: Path) -> int:
    """Files the pass has put on disk so far, the factory's own view.

    Vendored blocks, VCS internals, the writer CLI's own runtime home and
    the progress logs themselves are not the agent's output. Best-effort:
    an unreadable tree counts as zero.
    """
    skip = {"vendor", ".git", "node_modules", "__pycache__", WORKER_CLI_HOME_DIR}
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


def authored_files_or_none(root: Path) -> Optional[int]:
    """``count_authored_files``, but None when the walk raised part-way.

    A tree being rewritten under the walk (a test run creating and removing
    temporary paths) can raise mid-walk; the partial count it would return
    is indistinguishable from a tree that shrank. The heartbeat and the
    stall watchdog read None as "no sample", never as an empty tree.
    """
    skip = {"vendor", ".git", "node_modules", "__pycache__", WORKER_CLI_HOME_DIR}
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
        return None
    return total


#: Files the WORKER itself writes at dispatch (the brief pointer target and
#: the argv audit). Never evidence that the agent produced anything.
DISPATCH_AUDIT_FILES = frozenset({"docs/writer_prompt.txt", "docs/writer_argv.json"})


def agent_file_paths(root: Path) -> FrozenSet[str]:
    """Relative paths of the files ``count_authored_files`` counts, minus the
    worker's own dispatch audit files. A pass's new files are the difference
    of two snapshots taken around it."""
    skip = {"vendor", ".git", "node_modules", "__pycache__", WORKER_CLI_HOME_DIR}
    found = set()
    try:
        for path in Path(root).rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if set(rel.parts) & skip or path.name.startswith("writer_progress"):
                continue
            posix = rel.as_posix()
            if posix in DISPATCH_AUDIT_FILES:
                continue
            found.add(posix)
    except OSError:
        pass
    return frozenset(found)


def heartbeat_line(
    elapsed_s: float, files: int, steps_seen: int, *, unreadable: bool = False
) -> str:
    """What the factory can honestly say about a quiet agent.

    Observed facts only -- elapsed time, files on disk, steps reported. It
    never claims a step the agent did not report. ``unreadable``: this
    sample's walk failed, so ``files`` is the last count a walk completed.
    """
    minutes, seconds = divmod(int(elapsed_s), 60)
    clock = f"{minutes}m{seconds:02d}s"
    if unreadable:
        return (
            f"{HEARTBEAT_PREFIX} — {clock} in, tree unreadable this sample "
            f"(last good count {files} file(s)), {steps_seen} step(s) "
            "reported so far."
        )
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
