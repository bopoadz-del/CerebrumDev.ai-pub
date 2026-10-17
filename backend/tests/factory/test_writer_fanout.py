"""Concurrency in the WRITER must not be visible in what the WRITER produces.

The per-capability coder shots now go out ``FACTORY_CODER_FANOUT`` at a time.
That is only worth having if a build at fanout 5 is indistinguishable from a
build at fanout 1 -- same bytes, same ledger, same order -- because a factory
whose output depends on which model answered first has traded reproducibility
for wall-clock, and reproducibility is the whole product.

So these tests are not "does it go faster". Speed gets one test. The rest are
about what must NOT move:

* every file in the delivered workspace, hashed;
* the ledger, event for event, in order;
* the budget decision, which must be all-agent or all-template for a whole
  loop -- never three agent-written handlers and two templates with nothing in
  the artifact explaining the seam.

These run on the per-capability path (``FACTORY_BRIEF_DISPATCH=0``). The keyed
default answers for every capability in one compiled-brief dispatch and has
nothing per-capability to overlap.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.factory.blueprint import load_blueprint
from app.factory.build.runner import BuildBudget, RoleRunner
from app.factory.build.writer_fanout import FanOut, coder_fanout, fan_out
from tests.factory.coder_stub_bodies import invoking_handler_body

ROOT = Path(__file__).resolve().parents[3]
SMOKE = ROOT / "blueprints/examples/runner_smoke.yaml"


@pytest.fixture()
def blueprint():
    return load_blueprint(SMOKE)


@pytest.fixture(autouse=True)
def _per_capability_coder(monkeypatch):
    """Keyed, stubbed, and on the per-capability path -- no network, no key.

    Every coder entry point is stubbed. Leaving any of them live means a
    machine with a key in backend/.env makes paid calls from the test suite,
    which has happened before.
    """
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "1")
    monkeypatch.setenv("FACTORY_BRIEF_DISPATCH", "0")
    monkeypatch.delenv("FACTORY_CODER_FANOUT", raising=False)
    monkeypatch.setattr(
        "app.factory.build.coder_session.cli_available",
        lambda command=None: False,
    )
    monkeypatch.setattr(
        "app.factory.coder.generate_route_body",
        lambda **kw: {"body": _STUB_ROUTE, "model": "stub-route"},
    )
    monkeypatch.setattr(
        "app.factory.coder._llm_code_call",
        lambda messages: ("# stub readme\n", "stub-model"),
    )
    monkeypatch.setattr(
        "app.factory.coder.review_capability_bindings",
        lambda **kw: {
            "reviews": [
                {
                    "capability_id": c.get("id"),
                    "block_ids": c.get("block_ids") or [],
                    "verdict": "endorse",
                    "reason": "stub",
                }
                for c in kw.get("capabilities") or []
            ],
            "model": "stub-collector",
        },
    )
    monkeypatch.setattr(
        "app.factory.coder.propose_domain_test_cases",
        lambda **kw: {"cases": [], "model": "stub-tester"},
    )


_STUB_ROUTE = (
    "    result = handle(payload)\n"
    '    return {"ok": True, "capability": CAPABILITY_ID, "result": result}'
)


def _recorded_coder(monkeypatch, *, delay: float = 0.0, watcher=None):
    """Per-capability stubs whose output depends only on the capability id.

    A stub that returned anything time- or order-dependent would make the
    hash comparison meaningless: it would pass because both runs were equally
    arbitrary, not because the fan-out preserved anything.
    """

    def _spec(**kw):
        cid = kw["capability_id"]
        if watcher:
            watcher.enter()
        if delay:
            time.sleep(delay)
        if watcher:
            watcher.leave()
        return {
            "entity": cid.replace("-", "_"),
            "fields": [{"name": "reference", "type": "str", "required": True}],
            "model": "stub-spec",
        }

    def _handler(**kw):
        cid = kw["capability_id"]
        if watcher:
            watcher.enter()
        if delay:
            time.sleep(delay)
        if watcher:
            watcher.leave()
        return {
            "body": invoking_handler_body({"cap": cid}),
            "model": "stub-handler",
        }

    monkeypatch.setattr("app.factory.coder.generate_model_spec", _spec)
    monkeypatch.setattr("app.factory.coder.generate_platform_handler", _handler)


@dataclass
class _DataclassContext:
    """The smallest thing ``fan_out`` needs.

    A dataclass because ``fan_out`` copies the context with
    ``dataclasses.replace``; declared here rather than reaching for a real
    ``RoleContext``, which needs a workspace on disk.
    """

    state: dict = field(default_factory=dict)
    progress: object = None

    def note(self, detail: str, **payload) -> None:
        if self.progress is not None:
            self.progress(detail, payload)

    def coder_time_left(self):
        return None


class _Concurrency:
    """Counts how many stub calls are in flight at once."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.live = 0
        self.peak = 0

    def enter(self) -> None:
        with self._lock:
            self.live += 1
            self.peak = max(self.peak, self.live)

    def leave(self) -> None:
        with self._lock:
            self.live -= 1


def _tree_digest(root: Path) -> dict:
    """sha256 of every file in the delivered workspace, by relative path."""
    out = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if "__pycache__" in rel or rel.endswith((".pyc", ".pyo")):
            continue
        if rel.endswith("build_ledger.jsonl"):
            continue  # timestamps; compared separately, event by event
        out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


#: ``2.45s`` inside a gate detail is how long pytest took, not a decision.
#: Narrow on purpose: it normalises a measured duration and nothing else, so a
#: detail that really did change still fails the comparison.
_DURATION = re.compile(r"\b\d+\.\d+s\b")


def _ledger_events(root: Path) -> list:
    """(kind, role, detail) in order. Timestamps are wall-clock, so excluded."""
    path = root / "build_ledger.jsonl"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [
        (r.get("kind"), r.get("role"), _DURATION.sub("<s>", r.get("detail") or ""))
        for r in rows
    ]


def _build(blueprint, out: Path, monkeypatch, fanout: str, **kw):
    monkeypatch.setenv("FACTORY_CODER_FANOUT", fanout)
    outcome = RoleRunner(blueprint, out, **kw).run()
    return outcome


# -- what must not move ---------------------------------------------------


def test_fanout_five_emits_the_same_bytes_as_fanout_one(
    blueprint, tmp_path, monkeypatch
):
    _recorded_coder(monkeypatch)

    serial_out = tmp_path / "serial"
    assert _build(blueprint, serial_out, monkeypatch, "1").ok
    serial = _tree_digest(serial_out)

    parallel_out = tmp_path / "parallel"
    assert _build(blueprint, parallel_out, monkeypatch, "5").ok
    parallel = _tree_digest(parallel_out)

    assert serial, "the build produced no files to compare"
    assert sorted(serial) == sorted(parallel), (
        "fan-out changed which files exist: "
        f"only serial={sorted(set(serial) - set(parallel))} "
        f"only parallel={sorted(set(parallel) - set(serial))}"
    )
    differing = [rel for rel, digest in serial.items() if parallel[rel] != digest]
    assert not differing, f"fan-out changed the bytes of: {differing}"


def test_fanout_five_writes_the_same_ledger_in_the_same_order(
    blueprint, tmp_path, monkeypatch
):
    """A NOTE emitted from a worker would land in completion order.

    That is the failure this is here to catch: the build still succeeds and
    the files still match, but the record of how it was made reshuffles run to
    run, and nobody can diff two builds again.
    """
    _recorded_coder(monkeypatch)

    serial_out = tmp_path / "serial"
    assert _build(blueprint, serial_out, monkeypatch, "1").ok
    parallel_out = tmp_path / "parallel"
    assert _build(blueprint, parallel_out, monkeypatch, "5").ok

    serial = _ledger_events(serial_out)
    parallel = _ledger_events(parallel_out)
    assert serial, "the build wrote no ledger"
    first_diff = next(
        (i for i, (a, b) in enumerate(zip(serial, parallel)) if a != b),
        min(len(serial), len(parallel)),
    )
    assert serial == parallel, (
        f"ledger diverged at event {first_diff}:\n"
        f"  fanout 1: {serial[first_diff:first_diff + 2]}\n"
        f"  fanout 5: {parallel[first_diff:first_diff + 2]}"
    )


def test_the_writer_really_runs_the_shots_concurrently(
    blueprint, tmp_path, monkeypatch
):
    """Wired into the WRITER, not merely available as a helper.

    The hash tests above pass just as well if the fan-out never engages, so
    something has to observe that calls actually overlap inside a real build.
    """
    watcher = _Concurrency()
    _recorded_coder(monkeypatch, delay=0.25, watcher=watcher)
    assert _build(blueprint, tmp_path / "parallel", monkeypatch, "5").ok
    assert watcher.peak > 1, (
        "no two coder shots were ever in flight together; the WRITER is still "
        "serial at fanout 5"
    )

    serial_watcher = _Concurrency()
    _recorded_coder(monkeypatch, delay=0.25, watcher=serial_watcher)
    assert _build(blueprint, tmp_path / "serial", monkeypatch, "1").ok
    assert serial_watcher.peak == 1, (
        "fanout 1 must run in the calling thread; peak concurrency was "
        f"{serial_watcher.peak}"
    )


# -- the speed, measured on the helper rather than through a build --------


def test_a_sleeping_call_finishes_at_least_twice_as_fast_at_fanout_five():
    """Eight 0.2s calls: ~1.6s serial, ~0.4s at five at a time.

    Measured on ``fan_out`` directly. Through a whole build the signal is
    buried under compileall and three pytest subprocesses, and the test
    becomes a flaky stopwatch on CI's scheduler rather than a statement about
    the fan-out.
    """
    ctx = _DataclassContext()
    ids = [f"cap_{i}" for i in range(8)]

    def _sleeper(_wctx, cid):
        time.sleep(0.2)
        return cid

    started = time.monotonic()
    serial = fan_out(ctx, ids, _sleeper, workers=1)
    serial_s = time.monotonic() - started

    started = time.monotonic()
    parallel = fan_out(ctx, ids, _sleeper, workers=5)
    parallel_s = time.monotonic() - started

    assert serial.values == parallel.values == {cid: cid for cid in ids}
    assert parallel_s * 2 <= serial_s, (
        f"fanout 5 took {parallel_s:.2f}s against {serial_s:.2f}s serial; "
        "expected at least a 2x drop"
    )


# -- the budget decision --------------------------------------------------


def test_an_exhausted_budget_templates_the_whole_loop_never_part_of_it(
    blueprint, tmp_path, monkeypatch
):
    """Three agent handlers and two templates is the shape being refused.

    ``_budget_too_low`` asked per call, so a loop running out of wall answered
    yes, yes, no -- and the delivered platform carried two authorship stories
    with nothing saying why. The decision is now taken once per loop.
    """
    seen = []

    def _spec(**kw):
        seen.append(("spec", kw["capability_id"]))
        return {
            "entity": kw["capability_id"].replace("-", "_"),
            "fields": [{"name": "reference", "type": "str", "required": True}],
            "model": "stub-spec",
        }

    def _handler(**kw):
        seen.append(("handler", kw["capability_id"]))
        return {"body": invoking_handler_body({"cap": kw["capability_id"]}), "model": "m"}

    monkeypatch.setattr("app.factory.coder.generate_model_spec", _spec)
    monkeypatch.setattr("app.factory.coder.generate_platform_handler", _handler)

    out = tmp_path / "starved"
    # A wall too short to fund even one call: every loop must template.
    outcome = _build(
        blueprint,
        out,
        monkeypatch,
        "5",
        budget=BuildBudget(wall_clock_s=0.001, phase_wall_clock_s=0.001),
    )

    assert seen == [], (
        "the coder was called despite a spent budget; a starved loop must "
        f"template every capability, not some: {seen}"
    )
    # However the run ends, no handler may claim an author it did not have.
    actions = out / "app" / "actions"
    if actions.is_dir():
        credited = [
            p.name
            for p in actions.glob("*.py")
            if "coder LLM" in p.read_text(encoding="utf-8")
        ]
        assert not credited, f"templated handlers credited to the coder: {credited}"
    assert outcome is not None


def test_the_budget_decision_does_not_move_with_the_fanout(monkeypatch):
    """The knob must not decide who gets an agent-written handler.

    A reservation scaled by ``ceil(count / workers)`` is the obvious sizing and
    it is wrong here: it would fund at fanout 5 what it refuses at fanout 1,
    so the same blueprint would deliver agent-written handlers or templates
    depending on an env var. That is the fan-out leaking into the artifact.
    """
    from app.factory.build.writer_fanout import loop_budget_too_low

    monkeypatch.setattr("app.factory.coder._call_timeout_s", lambda: 100.0)

    class _Ctx:
        def __init__(self, left):
            self.state: dict = {}
            self.notes: list = []
            self._left = left

        def coder_time_left(self):
            return self._left

        def note(self, detail, **payload):
            self.notes.append((detail, payload))

    # One call needs 100 + 30 = 130s, at any fan-out.
    for workers in (1, 2, 5, 8):
        starved = _Ctx(120.0)
        assert loop_budget_too_low(starved, "handler", count=4, workers=workers) is True
        assert "coder_failures" in starved.state, workers

        funded = _Ctx(400.0)
        assert loop_budget_too_low(funded, "handler", count=4, workers=workers) is False
        assert funded.state == {}, workers

    # The refusal names the whole loop, not one call: that is the record a
    # reviewer reads to know no capability here was agent-written.
    starved = _Ctx(120.0)
    loop_budget_too_low(starved, "handler", count=4, workers=1)
    assert "all 4 are templated, not some" in starved.state["coder_failures"]["handler"]

    # Nothing to call is never too expensive.
    empty = _Ctx(0.0)
    assert loop_budget_too_low(empty, "handler", count=0, workers=1) is False
    assert empty.state == {}


# -- the knob -------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, 1),
        ("", 1),
        ("1", 1),
        ("5", 5),
        ("0", 1),
        ("-4", 1),
        ("99", 8),
        ("banana", 1),
    ],
)
def test_the_fanout_knob_never_returns_something_unusable(
    monkeypatch, raw, expected
):
    """Default 1, clamped to MAX_FANOUT, and never zero or negative.

    A 0 would open a pool with no workers and hang the WRITER; the ceiling is
    there because a 429 on a paid leg is deliberately not retried, so a wide
    burst buys rejected calls and a templated platform.
    """
    if raw is None:
        monkeypatch.delenv("FACTORY_CODER_FANOUT", raising=False)
    else:
        monkeypatch.setenv("FACTORY_CODER_FANOUT", raw)
    assert coder_fanout() == expected


def test_a_worker_failure_is_raised_in_capability_order(monkeypatch):
    """Which capability is blamed must not depend on who lost the race."""
    ctx = _DataclassContext()
    ids = ["a", "b", "c"]

    def _call(_wctx, cid):
        if cid in ("b", "c"):
            raise RuntimeError(f"boom {cid}")
        return cid

    result = fan_out(ctx, ids, _call, workers=3)
    assert result.values == {"a": "a"}
    assert set(result.errors) == {"b", "c"}
    # replay() is what the ordered walk calls; "b" comes before "c".
    result.replay(ctx, "a")
    with pytest.raises(RuntimeError, match="boom b"):
        result.replay(ctx, "b")


def test_a_worker_never_touches_the_shared_context(monkeypatch):
    """No note, no state write, escapes a worker until the walk replays it."""
    ctx = _DataclassContext(state={"vendored_blocks": ("analytics",)})
    emitted = []
    ctx.progress = lambda detail, payload: emitted.append(detail)

    def _call(wctx, cid):
        wctx.note(f"calling for {cid}", stage="coder")
        wctx.state.setdefault("coder_failures", {})[cid] = "stub failure"
        return cid

    result = fan_out(ctx, ["b", "a"], _call, workers=2)

    assert emitted == [], "a worker wrote to the shared progress sink"
    assert "coder_failures" not in ctx.state, "a worker wrote to shared state"

    result.replay(ctx, "a")
    result.replay(ctx, "b")
    assert emitted == ["calling for a", "calling for b"], (
        "notes must replay in the order the caller walks, not completion order"
    )
    assert ctx.state["coder_failures"] == {"a": "stub failure", "b": "stub failure"}


def test_an_empty_fanout_is_a_no_op():
    assert fan_out(_DataclassContext(), [], lambda *a: None, workers=5) == FanOut()


def test_a_shot_the_walk_never_collects_is_reported(monkeypatch):
    """A paid call whose provenance never reached the artifact.

    The predicate that decides which capabilities to shoot and the branch that
    replays them have to stay in step. If a ``continue`` is ever added between
    them, the coder is called, billed, and its authorship note dropped -- the
    handler would ship credited to a template. ``unreplayed`` is what notices.
    """
    ctx = _DataclassContext()
    result = fan_out(ctx, ["a", "b"], lambda _c, cid: cid, workers=2)

    assert result.unreplayed() == ["a", "b"]
    result.replay(ctx, "a")
    assert result.unreplayed() == ["b"]
    result.replay(ctx, "b")
    assert result.unreplayed() == []
