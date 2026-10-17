"""Fan out the WRITER's per-capability coder calls without moving the output.

The WRITER asks the coding agent once per capability, in ordered loops --
models first, then handlers, because handlers are written against the models.
Those barriers stay. What does not need to be serial is the *waiting*: each
capability's call is an independent blocking ``httpx.post``, and running them
one at a time makes the phase wall the sum of N network latencies.

The rule this module exists to keep is that concurrency must not be visible in
the output. A build at ``FACTORY_CODER_FANOUT=5`` must emit the same bytes and
the same ledger as one at ``1``, or the fan-out has bought speed with
reproducibility, which is not a trade this factory makes. Two things enforce
that:

* **Workers never touch shared state.** No ``ctx.note``, no list append, no
  workspace write. A worker gets a context whose progress sink is a buffer and
  whose ``coder_failures`` is a private dict, and hands both back with its
  value. The caller replays them while walking capabilities in plan order, at
  the exact point the serial code would have emitted them -- so the ledger is
  what it is today, at any fan-out.
* **Failures surface in capability order.** Every worker is drained before any
  exception is raised, and the exception is re-raised from the ordered walk.
  Without that, which ``RoleError`` ends the phase depends on which thread lost
  the race, and the same broken blueprint blames a different capability each
  run.

The budget decision moves up here with the calls. ``_budget_too_low`` asks
"does one more call fit?" before each call; asked N times down a loop that is
running out of wall it answers yes, yes, no, no -- and the phase ships three
agent-written handlers and two templates, with nothing in the artifact saying
why capability four differs from capability three.
:func:`loop_budget_too_low` asks once, before the loop, and its answer governs
every capability in it: all-agent or all-template, with the reason in the
ledger either way. It is deliberately sized independently of the fan-out --
see there for why a wave-scaled reservation would put the knob in the
artifact.

Default is ``1``: serial, in the calling thread, no pool -- today's behaviour.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Sequence, Tuple

#: Workers per WRITER loop.
FANOUT_ENV = "FACTORY_CODER_FANOUT"

#: Ceiling on what the env may ask for. Not a throughput knob: it is how many
#: simultaneous requests the factory is willing to put on a paid provider, and
#: a 429 on a paid leg is deliberately not retried (coder.py). Asking for 50
#: workers buys 50 rejected calls and a templated platform.
MAX_FANOUT = 8


def coder_fanout() -> int:
    """How many of a loop's coder calls may be in flight at once."""
    raw = (os.getenv(FANOUT_ENV) or "").strip()
    if not raw:
        return 1
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(1, min(value, MAX_FANOUT))


class NoteBuffer:
    """Collects a worker's ``ctx.note`` calls for replay by the caller.

    ``RoleContext.note`` writes a ledger event. Called from a worker it would
    interleave by completion order, which is the one thing about a build that
    must not depend on which model answered first.
    """

    def __init__(self) -> None:
        self.notes: List[Tuple[str, Dict[str, Any]]] = []

    def __call__(self, detail: str, payload: Dict[str, Any]) -> None:
        self.notes.append((detail, dict(payload)))


@dataclass
class FanOut:
    """What the workers produced, keyed by capability id."""

    values: Dict[str, Any] = field(default_factory=dict)
    notes: Dict[str, List[Tuple[str, Dict[str, Any]]]] = field(default_factory=dict)
    failures: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    errors: Dict[str, BaseException] = field(default_factory=dict)
    #: Capabilities whose notes and state delta have been handed to the
    #: caller. Tracked so a shot that was paid for cannot go unrecorded.
    replayed: set = field(default_factory=set)

    def unreplayed(self) -> List[str]:
        """Shots the walk never collected. Should always be empty.

        A capability that reaches the coder but not the replay has had a call
        made and paid for, and its "coder LLM (model)" note dropped -- the
        artifact would credit a template for work the agent did, or the other
        way round. The predicate choosing which ids to shoot and the branch
        that replays them have to stay in step, and this is what says so when
        someone adds a ``continue`` between them.
        """
        return sorted(set(self.notes) - self.replayed)

    def replay(self, ctx: Any, cid: str) -> None:
        """Emit one capability's notes and merge its state delta, in order.

        Called from the ordered walk at the point the serial code would have
        made the call, so the ledger reads identically at any fan-out. Raises
        that capability's exception if it had one, so a failure still ends the
        phase where it ended before.
        """
        self.replayed.add(cid)
        for detail, payload in self.notes.get(cid, ()):
            ctx.note(detail, **payload)
        delta = self.failures.get(cid)
        if delta:
            ctx.state.setdefault("coder_failures", {}).update(delta)
        exc = self.errors.get(cid)
        if exc is not None:
            raise exc


def _worker_context(ctx: Any) -> Tuple[Any, NoteBuffer, Dict[str, Any]]:
    """A context a worker may write to without anyone else seeing it."""
    buffer = NoteBuffer()
    failures: Dict[str, Any] = {}
    state = dict(ctx.state or {})
    state["coder_failures"] = failures
    return replace(ctx, state=state, progress=buffer), buffer, failures


def fan_out(
    ctx: Any,
    capability_ids: Sequence[str],
    call: Callable[[Any, str], Any],
    *,
    workers: int,
) -> FanOut:
    """Run ``call(worker_ctx, capability_id)`` for each id, keyed by id.

    ``workers <= 1`` runs in the calling thread, so the default build creates
    no pool at all. Above that, one ``ThreadPoolExecutor`` bounds in-flight
    calls: ``max_workers`` is the semaphore, and a second one would only be a
    second place to get the number wrong.
    """
    result = FanOut()
    ids = list(capability_ids)
    if not ids:
        return result

    def _one(cid: str) -> None:
        worker_ctx, buffer, failures = _worker_context(ctx)
        try:
            result.values[cid] = call(worker_ctx, cid)
        except BaseException as exc:  # noqa: BLE001 -- re-raised in plan order
            result.errors[cid] = exc
        finally:
            result.notes[cid] = buffer.notes
            if failures:
                result.failures[cid] = dict(failures)

    if workers <= 1 or len(ids) == 1:
        for cid in ids:
            _one(cid)
        return result

    with ThreadPoolExecutor(
        max_workers=min(workers, len(ids)), thread_name_prefix="writer-coder"
    ) as pool:
        # Drain every worker before letting anything out. _one swallows into
        # result.errors, so this cannot raise; a wave that stopped early would
        # leave later capabilities' notes unrecorded and the ledger would show
        # a phase that halted for no stated reason.
        list(pool.map(_one, ids))
    return result


def loop_budget_too_low(ctx: Any, what: str, *, count: int, workers: int) -> bool:
    """Decide once, before the loop, whether the agent is affordable at all.

    Sized at **one** call, and deliberately not at ``ceil(count / workers)``
    waves, for two reasons that both came out of the numbers:

    * ``_call_timeout_s()`` is 1200s against a 1500s code-phase wall. It is a
      worst-case ceiling, not an expected cost, and summing it across a loop
      is not a budget test -- it says no to every loop of two or more
      capabilities, which would disable the coder on this path entirely.
    * A reservation scaled by ``workers`` makes the *decision* depend on the
      fan-out, so ``FACTORY_CODER_FANOUT=5`` would produce agent-written
      handlers where ``1`` produced templates. That is the fan-out becoming
      visible in the artifact, which is the one thing this module exists to
      prevent. ``workers`` is recorded in the ledger and changes nothing.

    What moving the question up here does buy is that the answer governs the
    whole loop: a budget already spent when the loop starts templates every
    capability in it, rather than funding the first and refusing the rest with
    nothing but a per-call note to explain the seam. ``_budget_too_low``
    remains live per call as the wall a long loop can still hit.

    Records the refusal under ``coder_failures[what]`` so the provenance says
    the agent was not used and why.
    """
    if count <= 0:
        return False
    left = ctx.coder_time_left()
    if left is None:
        return False
    from app.factory.coder import _call_timeout_s

    needed = _call_timeout_s() + 30
    if left > needed:
        return False
    ctx.state.setdefault("coder_failures", {})[what] = (
        f"skipped: {int(max(left, 0))}s of build budget left, one {what} call "
        f"needs up to {int(needed)}s; all {count} are templated, not some"
    )
    ctx.note(
        f"coder skipped for every {what} — build budget nearly spent",
        stage="budget",
        count=count,
        fanout=workers,
    )
    return True
