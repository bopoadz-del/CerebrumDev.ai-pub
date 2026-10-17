"""The role runner — drives a gated, lane-restricted, resumable build.

This is what turns the three kernels from inert checks into a manufacturing
run. It owns the phase order, hands each role a workspace it cannot write
outside of, runs the phase's gate afterwards, and drives the WRITER<->TESTER
rework loop until a gate passes or the budget is spent.

Three properties are load-bearing and each is enforced here rather than
trusted:

* **Gates are looked up by phase**, never supplied by the role. A role cannot
  weaken, mock or skip the check that judges it.
* **A spent budget is a failure.** A run that ends without its gates green
  terminates ``FAILED`` with the reason in the ledger. There is no code path
  that reports success for an ungated build -- that is the same "plausible
  green" hazard the gates were written against, and it must not be
  reintroduced one layer up.
* **Resume keys on the blueprint, not the output tree.** ``ProductGenerator``
  reports an ``inputs_hash`` that is really ``hash_tree`` of the generated
  output, so it changes whenever an LLM writes a handler. Keying resume on it
  would refuse every resume of an unchanged blueprint. :func:`blueprint_hash`
  hashes the inputs instead, which is stable by construction.

The role runner is the production default (``FACTORY_BUILD_ENGINE=runner``).
``ProductGenerator`` remains the template emitter and the source of the
14-class surfaces RoleRunner now converges onto. ``FACTORY_BUILD_ENGINE=template``
is the documented revert.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("cerebrumdev.factory.runner")

from app.factory.build.failure_kinds import failure_kind
from app.factory.build.authority import (
    BUILD_PHASES,
    SEALED_AFTER_CLONER,
    AuthorityError,
    BuildRole,
    assert_phase_order,
    authority_manifest,
)
from app.factory.build.gates import (
    FACTORY_SUITE_MARKER_EXPR,
    GateContext,
    GateResult,
    gate_for,
)
from app.factory.build.ledger import BuildLedger, EventKind
from app.factory.build.roles import (
    ROLE_IMPLEMENTATIONS,
    RoleContext,
    RoleError,
    RoleResult,
)
from app.factory.build.workspace import RoleWorkspace

RUNNER_FLAG_ENV = "FACTORY_RUNNER_ENABLED"
LEDGER_FILENAME = "build_ledger.jsonl"

#: Phases that participate in the rework loop. A failed TESTER gate sends the
#: WRITER back round with the findings as its work list -- and so does a
#: failed WRITER gate: its typed findings (F1, F11, schema, compile, UI) are
#: work only the writer can do. Both share the rework budget and the
#: same-failure-twice rule.
REWORK_SOURCE = BuildRole.TESTER
#: Phases whose in-runner gate failure can be reworked. The N3 Store gate's
#: product-owned verdict reaches the same rule through
#: ``reopen_after_store_gate``; the in-runner STORE_MANAGER gate (docker
#: unavailable, store contract) is never product work and stays terminal.
REWORK_SOURCES = frozenset({BuildRole.TESTER, BuildRole.WRITER})

#: Owner rule: two rework rounds PER GATE (WRITER gate, TESTER, Store gate);
#: one gate's rounds never reduce another's. A gate that has used its two and
#: fails again stops the run.
REWORK_BUDGET = 2
#: Hard ceiling on rework rounds for the whole build, every gate together.
REWORK_CEILING = 6

#: The four things the runner can do with a failed phase verdict. One rule
#: (``RoleRunner.decide``) picks one, from data, and records it.
from app.factory.build import findings as _findings  # noqa: E402
from app.factory.build.factory_owned import WRITER_AUTHORED as WRITER_AUTHORED_FACTORY_FILE  # noqa: E402
from app.factory.build.rule_decision import (  # noqa: E402
    ADVISORY as DECISION_ADVISORY,
    REGENERATE_TEST as DECISION_REGENERATE_TEST,
    REPROMPT as DECISION_REPROMPT,
    REWORK as DECISION_REWORK,
    STOP as DECISION_STOP,
)


@dataclass(frozen=True)
class GateDecision:
    """What ``RoleRunner.decide`` chose for one failed verdict."""

    kind: str
    work_list: Tuple[str, ...] = ()
    outcome: Optional["Outcome"] = None
    detail: str = ""
    findings: Tuple[str, ...] = ()
    record: Dict[str, Any] = field(default_factory=dict)


def _failure_keys(verdict: Any, exclude: Sequence[str] = ()) -> List[str]:
    """Same-failure-twice keys -- check id + finding SHAPE, never the gate.

    Failing test rows carry their own (``<nodeid> [failure|error]``). A gate
    verdict without rows gives one ``<check>:<shape>`` per brief check it
    failed; the shape is the typed ``finding_shape`` the gate declares, else
    the verdict's typed reason, else ``failed``. The gate is not in the key, so the same check failing the
    same way at two different gates is the same failure."""
    from app.factory.build import brief_gates, failure_owner

    payload = getattr(verdict, "payload", None) or {}
    keys = failure_owner.failure_names(verdict, exclude=exclude)
    gate = str(getattr(verdict, "gate", "") or "")
    bare = [f"{gate}:{getattr(verdict, 'reason', '') or 'failed'}"] if gate else []
    if keys and keys != bare:
        # Typed failing-test rows (or a caller's own keys): already the shape.
        return keys
    # A gate that declares no shape has typed its failure by its reason
    # (GateResult types each finding the same way). Keying it "failed" made
    # two different failures of one check "the same failure twice" -- live
    # cycle 4 smoke B: writer_authored_factory_file in round 1, then
    # locked_block_loaded_elsewhere stopped the build unheard.
    shape = str(payload.get("finding_shape") or getattr(verdict, "reason", "") or "failed")
    checks = sorted({check for check, _ in brief_gates.failure_checks(verdict) if check})
    return [f"{check}:{shape}" for check in checks] or keys
REWORK_TARGET = BuildRole.WRITER


def runner_enabled() -> bool:
    """The runner is opt-in. The template path stays the default until cutover."""
    return os.getenv(RUNNER_FLAG_ENV, "0").strip().lower() in {"1", "true", "yes", "on"}


#: Ledger NOTE ``stage`` for a capability that has landed on disk.
#: ``resume_point()`` still names the role; this is the intra-WRITER spine.
CHECKPOINT_STAGE = "checkpoint"

#: When an in-flight C-BRIEF CLI has this much (or less) of the phase
#: wall left, ``_extend_wall`` may lift the live deadline to the 7200s
#: ceiling. Not a bigger initial wall — ramp + salvage.
CLI_PHASE_RAMP_HEADROOM_S = 120.0


def _test_defect_items(defects: Sequence[Dict[str, Any]]) -> list:
    """One typed ``test_defect`` work-list item per writer test that demanded
    a live answer from a declared placeholder capability (failure_owner)."""
    from app.factory.build.placeholder_connectors import defect_work_item

    return [
        defect_work_item(str(d.get("nodeid") or ""), list(d.get("capabilities") or []))
        for d in defects
        if d.get("nodeid")
    ]


def _writer_gate_items(verdict: Any) -> tuple:
    """The WRITER's rework list after its OWN gate failed: one item per
    finding, named by the check it measures (brief_gates), then the command
    that runs the gate's probe so the writer can confirm the fix itself."""
    from app.factory.build import brief_gates
    from app.factory.build.writer_behaviour import SELF_CHECK_COMMAND

    items = [
        # The bracket names the check for the coder; the typed fields travel
        # with the item (capability_id drives the ratchet, never this text).
        finding.with_fields(detail=f"[{check}] {finding.detail}")
        if isinstance(finding, _findings.Finding)
        else f"[{check}] {finding}"
        for check, finding in brief_gates.failure_checks(verdict)
    ]
    items.append(
        f"[{brief_gates.WRITER_BEHAVIOUR_CHECK}] before declaring done, run "
        f"`{SELF_CHECK_COMMAND}` (the WRITER gate's own probe) and fix every "
        "record it prints"
    )
    return tuple(items)


def _tester_recheck_item(verdict: Any) -> str:
    """The TESTER rework round's re-check: the failing product-gate tests by
    node id, and the self-check that runs the same suites TESTER stamped.
    Every finding row already names the capability and field it refused."""
    from app.factory.build import failure_owner
    from app.factory.build.brief_gates import PRODUCT_GATE_CHECK
    from app.factory.build.product_suites import RECHECK_TAG
    from app.factory.build.writer_behaviour import SELF_CHECK_COMMAND

    nodes = [
        str(name).rsplit(" [", 1)[0]
        for name in failure_owner.failure_names(verdict)
        if "::" in str(name)
    ]
    rerun = (
        " and `python -m pytest -m \"pilot or not pilot\" "
        + " ".join(nodes)
        + "`"
        if nodes
        else ""
    )
    return (
        f"{RECHECK_TAG} [{PRODUCT_GATE_CHECK}] the suites that failed are on disk (Factory-"
        f"owned; TESTER re-stamps them from your app/models.py): run "
        f"`{SELF_CHECK_COMMAND}`{rerun} before declaring done. A payload "
        "\"built from its own schema\" uses the model's FIELDS -- declare in "
        "that capability's model every field its handler or route requires"
    )


def landed_capability_ids(ledger: Any, inputs_hash: str) -> list:
    """Capabilities already checkpointed for this ``blueprint_hash``.

    Resume keys on the blueprint, not the output tree. A NOTE whose
    ``inputs_hash`` does not match is a different run and is ignored.
    """
    digest = str(inputs_hash or "")
    out: list = []
    seen: set = set()
    events = ledger.events() if hasattr(ledger, "events") else ()
    for event in events:
        payload = getattr(event, "payload", None) or {}
        if str(payload.get("stage") or "") != CHECKPOINT_STAGE:
            continue
        if str(payload.get("inputs_hash") or "") != digest:
            continue
        cid = str(payload.get("capability") or "").strip()
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def pending_capability_ids(ctx: Any) -> list:
    """Plan order minus capabilities already on the resume spine."""
    plan = getattr(ctx, "plan", None)
    caps = [
        str(getattr(cap, "capability_id", "") or "")
        for cap in getattr(plan, "capabilities", ()) or ()
    ]
    landed = {
        str(item)
        for item in (getattr(ctx, "state", {}) or {}).get("landed_capabilities") or ()
        if item
    }
    return [cid for cid in caps if cid and cid not in landed]


def checkpoint_landed_capability(ctx: Any, capability_id: str) -> None:
    """Persist one landed capability into destination + ledger resume spine."""
    from app.factory.build.persist_accept import persist_handler_rel

    cid = str(capability_id or "").strip()
    if not cid:
        return
    state = getattr(ctx, "state", None)
    if not isinstance(state, dict):
        return
    rel = persist_handler_rel(cid)
    ws = getattr(ctx, "workspace", None)
    text = ""
    if ws is not None:
        try:
            if hasattr(ws, "exists") and ws.exists(rel):
                text = ws.read_text(rel)
        except (OSError, TypeError, ValueError):
            text = ""
        dest = getattr(ws, "destination", None)
        if dest is not None and text:
            path = Path(dest) / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    digest = str(state.get("inputs_hash") or "")
    if not digest and getattr(ctx, "blueprint", None) is not None:
        try:
            digest = blueprint_hash(ctx.blueprint)
            state["inputs_hash"] = digest
        except Exception:  # noqa: BLE001 — checkpoint must not fail WRITER
            digest = ""
    landed = [str(item) for item in (state.get("landed_capabilities") or []) if item]
    if cid not in landed:
        landed.append(cid)
        state["landed_capabilities"] = landed
    note = getattr(ctx, "note", None)
    if callable(note):
        note(
            f"landed capability {cid}",
            stage=CHECKPOINT_STAGE,
            capability=cid,
            inputs_hash=digest,
        )


def _specs_from_product_models(workspace: Path) -> Dict[str, Any]:
    """``model_specs`` read back off the product's ``app/models.py`` (one
    source: declared_specs, which TESTER also reads every pass)."""
    from app.factory.build.declared_specs import specs_from_product_models

    return specs_from_product_models(workspace)


def frozen_blueprint(blueprint: Any) -> Any:
    """The blueprint as approved: a deep copy no later caller can reach.

    The build is keyed to what was approved. The caller keeps its own object
    (the session, the chat flow, a later typed field) and anything it changes
    after approval changes ITS copy, never the build's -- so the hash taken
    from this snapshot stays the build's identity for its whole life.
    """
    copier = getattr(blueprint, "model_copy", None)
    if callable(copier):
        return copier(deep=True)
    import copy

    return copy.deepcopy(blueprint)


def blueprint_hash(blueprint: Any) -> str:
    """Stable hash of the build's *inputs*.

    Canonical JSON with sorted keys, so it does not move with dict ordering,
    and it never touches the generated tree -- an output hash cannot be a
    resume key because it is unknown until the build it is meant to authorise
    has already run.

    Fields still at their schema default are left out. A default is not an
    input anyone gave -- it is the code version's filler -- so hashing it made
    the identity of an unchanged blueprint depend on which Factory release
    read it: a field added with a default (live 2026-10-05, #645
    ``CapabilitySpec.connectors``) moved the hash of every in-flight build,
    and the orphan resume after that deploy was refused as "inputs changed"
    on a blueprint nobody had touched. Any value that differs from its
    default -- an input someone actually gave -- still moves the hash, so a
    genuinely different blueprint is still refused.
    """
    payload = json.dumps(
        blueprint.model_dump(mode="json", exclude_defaults=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    #: Instrumented collect-all run: every phase executed, every gate
    #: finding recorded, no halt. NOT a success — ``BuildOutcome.ok``
    #: stays False and no package identity is sealed. The build is an
    #: instrument report (board P7: one run logging ALL gate findings).
    COLLECT_ALL_REPORT = "COLLECT_ALL_REPORT"
    #: CLI-pivot receipt accepted; N3 store-gate is next. Not product green.
    #: Emitted only after TESTER (and STORE_MANAGER) have run — never
    #: immediately after WRITER in a way that skips the acceptance inspector.
    HANDOFF_TO_N3 = "HANDOFF_TO_N3"
    FAILED_GATE = "FAILED_GATE"
    FAILED_BUDGET_SPENT = "FAILED_BUDGET_SPENT"
    FAILED_ROLE_ERROR = "FAILED_ROLE_ERROR"
    FAILED_AUTHORITY = "FAILED_AUTHORITY"


def _collect_all_enabled() -> bool:
    """FACTORY_GATE_COLLECT_ALL read live (tests / operators flip it
    without re-import). When truthy the run loop records every failed
    gate and RoleError as findings and keeps going instead of halting,
    so ONE run surfaces the complete list (board P7 collect-all). Lane
    violations (AuthorityError) stay terminal — that is a security
    boundary, not a build finding."""
    raw = os.getenv("FACTORY_GATE_COLLECT_ALL", "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class BuildBudget:
    """Bounds on a run. Exhausting a bound ends the build as a failure.

    ``wall_clock_s`` is the whole build. Default is stage 1 (~30 min);
    inspect-and-ramp may extend to 45 min. A leftover 2h wall is honoured
    but is not the default. ``phase_wall_clock_s`` caps *each* role.
    ``0`` disables that bound.
    """

    #: Per gate (owner rule): the same REWORK_BUDGET the rule enforces.
    max_rework: int = REWORK_BUDGET
    wall_clock_s: float = 1800.0
    phase_wall_clock_s: float = 1500.0
    #: The wall may be RAMPED at an inspect, never past this. It is the same
    #: number ``budget_inspect.CEILING_S`` proposes up to; it lives here as
    #: well because ``_extend_wall`` is the only writer of the deadline and
    #: two of its callers do not come from the inspector at all -- they pass
    #: ``elapsed + PILOT_SUITE_TAIL_S``, which grows with the run and was
    #: never compared to a ceiling. A bound enforced in the module that
    #: PROPOSES values, and not in the one that APPLIES them, is a
    #: convention. ``0`` disables it, matching ``wall_clock_s``.
    hard_ceiling_s: float = 7200.0

    def deadline_from(self, started: float) -> Optional[float]:
        return (started + self.wall_clock_s) if self.wall_clock_s > 0 else None

    def capped(self, wall: float) -> float:
        """The largest wall this budget will honour."""
        if self.hard_ceiling_s and self.hard_ceiling_s > 0:
            return min(float(wall), float(self.hard_ceiling_s))
        return float(wall)


@dataclass
class BuildOutcome:
    outcome: Outcome
    detail: str = ""
    failed_phase: Optional[BuildRole] = None
    rework_used: int = 0
    completed: tuple = ()
    findings: Sequence[str] = ()
    ledger_path: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.SUCCESS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "ok": self.ok,
            "detail": self.detail,
            "failed_phase": self.failed_phase.value if self.failed_phase else None,
            "rework_used": self.rework_used,
            "completed": [p.value for p in self.completed],
            "findings": list(self.findings),
            "ledger": self.ledger_path,
        }


class RoleRunner:
    """Drives one build of *blueprint* into *workspace*."""

    def __init__(
        self,
        blueprint: Any,
        workspace: Path | str,
        *,
        plan: Any = None,
        blocks_root: Optional[Path | str] = None,
        store_root: Optional[Path | str] = None,
        ledger: Optional[BuildLedger] = None,
        budget: Optional[BuildBudget] = None,
        roles: Optional[Mapping[BuildRole, Callable[[RoleContext], RoleResult]]] = None,
        gate_timeout_s: Optional[float] = None,
        subprocess_runner: Optional[Callable[..., Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        cycle: str = "code",
        auto_pilot: bool = False,
        blocks_lock: Optional[Dict[str, Any]] = None,
        tenant_store: Any = None,
        brief: str = "",
        session_id: str = "",
        inputs_hash: Optional[str] = None,
    ) -> None:
        from app.factory.planner import CapabilityPlanner, assert_generatable

        self.blueprint = blueprint
        #: The build's identity, taken ONCE: handed in by start_runner_build,
        #: which hashed the frozen snapshot at approval, or -- for a direct
        #: caller -- taken here before any role runs. run() never re-derives
        #: it, so nothing that touches self.blueprint later can move it.
        self.inputs_hash = str(inputs_hash or "") or blueprint_hash(blueprint)
        self.workspace = Path(workspace).resolve()
        # The Factory holds no blocks -- the Store does. A runner handed no
        # explicit root resolves the Store exactly as production does
        # (CEREBRUM_BLOCKS_ROOT, then the pinned Store clone). It used to stay
        # None, which meant "vendor mirror only": every test that built a
        # product ran off Factory-local copies and never touched the Store.
        if not blocks_root:
            from app.factory.blocks_source import resolve_blocks_root

            blocks_root = resolve_blocks_root()
        self.blocks_root = Path(blocks_root) if blocks_root else None
        self.store_root = Path(store_root) if store_root else None
        self.plan = (
            assert_generatable(plan)
            if plan
            else CapabilityPlanner(self.blocks_root).plan(blueprint)
        )
        self.budget = budget or BuildBudget()
        # Roles are injectable so a test can drive a misbehaving role; gates
        # are NOT -- see the module docstring.
        self.roles = dict(roles or ROLE_IMPLEMENTATIONS)
        self.gate_timeout_s = gate_timeout_s
        self.subprocess_runner = subprocess_runner
        self.clock = clock
        self.ledger = ledger or BuildLedger(self.workspace / LEDGER_FILENAME)
        self.state: Dict[str, Any] = {}
        # Phase 1: the tenant store handle bound from the authenticated
        # principal at build start. The WRITER role hands it to the
        # headless worker, whose isolation gate refuses an unbound job
        # (no_authenticated_tenant) before the CLI starts.
        if tenant_store is not None:
            self.state["tenant_store"] = tenant_store
        # THIS build's session id, resolved by the caller into a local and
        # threaded here rather than parked in os.environ. Under concurrency a
        # process-global session id is a cross-tenant identifier bleed: the
        # writer subprocess and the cli-pivot seam both resolve `build/
        # <session>-*` from it, so two tenants would resolve the same output
        # path. Same path the tenant handle takes, for the same reason.
        if str(session_id or "").strip():
            self.state["session_id"] = str(session_id).strip()
        # The user's own words from the Floor chat — the WRITER's BRIEF
        # section. Threaded from the session at generate/resume time.
        if str(brief or "").strip():
            self.state["brief"] = str(brief).strip()
        from app.factory.build.authorship import n_required_capabilities_from

        n_required = n_required_capabilities_from(
            plan=self.plan, blueprint=self.blueprint
        )
        if n_required is not None:
            self.state["n_required"] = n_required
            self.state["n_required_capabilities"] = n_required
        from app.factory.build.authorship import persist_required_capability_inputs

        persist_required_capability_inputs(
            self.workspace,
            blueprint=self.blueprint,
            plan=self.plan,
            n_required=n_required,
        )
        self.manifest = authority_manifest()
        #: Set by run(); roles read it to stop starting coder calls
        #: that cannot finish inside the build's wall clock.
        self._deadline: Optional[float] = None
        self._deadline_box: Dict[str, Any] = {"at": None}
        self._run_started: Optional[float] = None
        self._inspects_done: set = set()
        self._stage_halt: Optional[Dict[str, Any]] = None
        #: Reopen WRITER once when DeepSeek CLI was ready but unused.
        self._cli_writer_reopened: bool = False
        resolved = (cycle or "code").strip().lower()
        self.cycle = "pilot" if resolved == "pilot" else "code"
        #: Floor ``_run`` passes True when the run is to climb past code
        #: SUCCESS. Direct RoleRunner callers (tests, CLI helpers) stay
        #: code-only unless they opt in — a keyed CI stub must not open
        #: Store-green.
        self.auto_pilot = bool(auto_pilot)
        from app.factory.build.build_level import bar_for

        #: The user's BUILD LEVEL. When the blueprint declares one it is the
        #: single input for where this run stops and how strict its top rung
        #: is; None (a direct, non-Floor caller) keeps ``auto_pilot`` and
        #: every gate at full strength.
        self.level_bar = bar_for(blueprint)
        from app.factory.blocks_lock import resolve_lock

        self.blocks_lock = resolve_lock(blocks_lock)

    # -- gate plumbing ---------------------------------------------------

    def _gate_context(self, role: BuildRole) -> GateContext:
        kwargs: Dict[str, Any] = {
            "workspace": self.workspace,
            "role": role,
            "gaps": tuple(self.state.get("gaps", ())),
            "vendored_blocks": tuple(self.state.get("vendored_blocks", ())),
        }
        if self.gate_timeout_s is not None:
            kwargs["timeout_s"] = self.gate_timeout_s
        if self.subprocess_runner is not None:
            kwargs["runner"] = self.subprocess_runner
        kwargs["suite_marker"] = (
            "pilot" if self.state.get("build_cycle") == "pilot" else FACTORY_SUITE_MARKER_EXPR
        )
        kwargs["cycle"] = str(self.state.get("build_cycle") or self.cycle or "code")
        kwargs["store_ops"] = tuple(self.state.get("store_ops") or ())
        kwargs["store_unwired"] = bool(self.state.get("store_unwired"))
        kwargs["brief"] = str(self.state.get("brief") or "")
        return GateContext(**kwargs)

    def _absorb(self, result: RoleResult) -> None:
        if result.gaps:
            self.state["gaps"] = tuple(result.gaps)
        if result.vendored_blocks:
            self.state["vendored_blocks"] = tuple(result.vendored_blocks)
        kept_n = self.state.get("n_required")
        kept_n_caps = self.state.get("n_required_capabilities")
        for key, value in (result.notes or {}).items():
            self.state[key] = value
        # Roles may echo an inspect snapshot that omits n_required. Do not
        # let that clobber the floor computed from the blueprint at start.
        if kept_n not in (None, 0, "") and not self.state.get("n_required"):
            self.state["n_required"] = kept_n
        if kept_n_caps not in (None, 0, "") and not self.state.get(
            "n_required_capabilities"
        ):
            self.state["n_required_capabilities"] = kept_n_caps

    def _restore_workspace_state(self) -> None:
        """Rehydrate CLONER notes after a worker restart / pilot reopen.

        The runner's in-memory state dies with the process. A resume that
        skips CLONER would otherwise hand TESTER an empty vendored_blocks
        list and rewrite the suite against nothing.
        """
        vendor = self.workspace / "vendor" / "blocks"
        if vendor.is_dir():
            blocks = tuple(
                sorted(
                    p.name
                    for p in vendor.iterdir()
                    if (p / "block.py").is_file()
                )
            )
            if blocks:
                self.state.setdefault("vendored_blocks", blocks)
        # An attached cerebrum-builds branch travels in the ledger's RESUMED
        # note; every checkpoint and the Docker gate go back to that branch.
        if self.ledger.exists() and "attached_branch" not in self.state:
            for event in reversed(list(self.ledger.events())):
                branch = (event.payload or {}).get("attached_branch")
                if branch:
                    self.state["attached_branch"] = str(branch)
                    break
        # The platform's branch of record (build/<platform_id>), recorded once
        # by start_runner_build. Every passed phase is pushed to it when
        # cerebrum-builds is armed.
        if self.ledger.exists() and "platform_id" not in self.state:
            from app.factory.build.ledger import PLATFORM_ID_KEY

            for event in reversed(list(self.ledger.events())):
                pid = (event.payload or {}).get(PLATFORM_ID_KEY)
                if pid:
                    self.state["platform_id"] = str(pid)
                    break
        # Specs are in-memory state and die with the process. A re-entered run
        # reads them back off the product's own models, so TESTER samples the
        # contract WRITER actually shipped rather than an empty spec.
        if not self.state.get("model_specs") and (self.workspace / "app" / "models.py").is_file():
            specs = _specs_from_product_models(self.workspace)
            if specs:
                self.state["model_specs"] = specs
        lock_path = self.workspace / "blocks.lock.json"
        if lock_path.is_file() and "lock" not in self.state:
            try:
                self.state["lock"] = json.loads(
                    lock_path.read_text(encoding="utf-8")
                )
            except (OSError, ValueError):
                pass
        digest = str(self.state.get("inputs_hash") or "") or blueprint_hash(
            self.blueprint
        )
        self.state["inputs_hash"] = digest
        landed = landed_capability_ids(self.ledger, digest)
        if landed:
            prior = [
                str(item)
                for item in (self.state.get("landed_capabilities") or [])
                if item
            ]
            self.state["landed_capabilities"] = list(dict.fromkeys([*prior, *landed]))
        from app.factory.build.writer_phases import landed_phase_ids

        phases = landed_phase_ids(self.ledger, digest)
        if phases:
            prior_phases = [
                str(item)
                for item in (self.state.get("landed_writer_phases") or [])
                if item
            ]
            self.state["landed_writer_phases"] = list(
                dict.fromkeys([*prior_phases, *phases])
            )

    # -- one phase -------------------------------------------------------

    def _writer_can_resume(self, staging: Path) -> bool:
        """An interrupted CodeWhale pass that left a progress log to read."""
        try:
            from app.factory.build.roles_handlers import writer_uses_codewhale

            if not writer_uses_codewhale(os.environ):
                return False
            log = Path(staging) / "docs" / "writer_progress.log"
            return log.is_file() and log.stat().st_size > 0
        except Exception:  # noqa: BLE001 -- when unsure, the old safe wipe
            return False

    def _cycle_events(self) -> list:
        """Ledger events since the current cycle opened."""
        events = list(self.ledger.events()) if self.ledger.exists() else []
        for index in range(len(events) - 1, -1, -1):
            if events[index].kind is EventKind.PILOT_OPENED:
                return events[index + 1 :]
        return events

    def _branch_of_record(self) -> str:
        """The branch this run writes to: the platform's build/<id> when
        cerebrum-builds is armed, else an attached legacy branch, else empty."""
        from app.factory.build.builds_push import builds_token
        from app.factory.build.platform_identity import branch_of_record, is_platform_id

        pid = str(self.state.get("platform_id") or "")
        if is_platform_id(pid) and builds_token(os.environ):
            return branch_of_record(pid)
        return str(self.state.get("attached_branch") or "")

    def _push_branch_of_record(self, message: str) -> str:
        from app.factory.build.platform_identity import branch_of_record, is_platform_id

        pid = str(self.state.get("platform_id") or "")
        branch = self._branch_of_record()
        if is_platform_id(pid) and branch == branch_of_record(pid):
            from app.factory.build.platform_branch import push_to_branch_of_record

            return push_to_branch_of_record(self.workspace, pid, message)
        from app.factory.build.branch_attach import checkpoint

        return checkpoint(self.workspace, branch, message)

    def _rework_rounds_this_cycle(self) -> int:
        return sum(1 for e in self._cycle_events() if e.kind is EventKind.REWORK)

    def _last_rework_failures(self) -> list:
        """Failure names that triggered the most recent rework round."""
        for event in reversed(self._reworks()):
            return list((event.payload or {}).get("failure_names") or [])
        return []

    def _brief_defined_checks(self) -> Optional[FrozenSet[str]]:
        """The checks THIS build's brief turns on (brief_gates): the WRITER's
        compiled brief when this process compiled one, else the same brief
        compiled here from the blueprint. ``None`` -- recorded -- when no
        brief can be compiled; the round's failures then route as reported."""
        from app.factory.build import brief_gates

        recorded = (self.state.get("compiled_brief") or {}).get("acceptance_checks")
        try:
            accept = (
                tuple(recorded)
                if recorded
                else brief_gates.compiled_acceptance_checks(
                    self.blueprint, self.plan, blocks_root=self.blocks_root
                )
            )
            return brief_gates.brief_defined_checks(self.blueprint, accept)
        except Exception as exc:  # noqa: BLE001 -- named in the ledger
            self.ledger.append(
                EventKind.NOTE,
                detail=(
                    "brief checks unavailable "
                    f"({type(exc).__name__}: {exc}); failures route as reported"
                ),
                payload={"brief_checks_unavailable": f"{type(exc).__name__}: {exc}"},
            )
            return None

    def _split_by_brief(self, verdict: GateResult) -> Any:
        from app.factory.build import brief_gates

        defined = self._brief_defined_checks()
        if defined is None:
            return None
        return brief_gates.split_failures(verdict, defined)

    def _record_advisory(self, role: BuildRole, verdict: GateResult, split: Any) -> None:
        """One ledger event per round: which checks went advisory, and why."""
        from app.factory.build import brief_gates

        writer = bool(split.defined)
        self.ledger.append(
            EventKind.NOTE,
            role=role,
            detail=(
                f"GATE ADVISORY: {', '.join(split.invented_checks)} -- "
                f"{brief_gates.REASON_NOT_DEFINED}; "
                + (
                    "only the brief-defined failures go to the writer"
                    if writer
                    else "no writer round"
                )
            ),
            payload={
                "gate_advisory": [
                    {
                        "check": check,
                        "reason": brief_gates.REASON_NOT_DEFINED,
                        "findings": [f for c, f in split.invented if c == check],
                    }
                    for check in split.invented_checks
                ],
                "gate": verdict.gate,
                "brief_defined_failures": split.defined_checks,
                "writer_dispatched": writer,
            },
        )

    def _refresh_factory_files(self) -> None:
        from app.factory.build.factory_refresh import product_display_name, refresh_factory_files

        name = product_display_name(self.blueprint)
        try:
            # A build in progress: every Factory-owned file is rendered, a
            # missing one included (the writer-owned-files guard may have
            # removed the writer's copy).
            changed = refresh_factory_files(
                self.workspace, name, self.blueprint, render_absent=True
            )
            # The domain acceptance driver follows the specs TESTER is about to
            # stamp its suite from (cycle 8 vineyard: rendered with the
            # WRITER's empty specs, it named no capability the suite asks for).
            from app.factory.build.domain_acceptance import domain_specs, stamp_domain_substrate
            from app.factory.build.stamp_registry import _as_workspace

            specs = domain_specs(dict(self.state.get("model_specs") or {}), self.workspace)
            changed = list(changed) + [
                rel
                for rel in stamp_domain_substrate(_as_workspace(self.workspace), specs)
                if rel not in changed
            ]
        except Exception as exc:  # noqa: BLE001 -- recorded; the gates still judge
            self.ledger.append(
                EventKind.NOTE,
                detail=f"factory file refresh failed: {type(exc).__name__}: {exc}",
                payload={"refresh_failed": True},
            )
            return
        if changed:
            self.ledger.append(
                EventKind.NOTE,
                detail="REFRESHED factory files from current templates: " + ", ".join(changed),
                payload={"refreshed": changed},
            )

    def _factory_test_files(self) -> list:
        """Test files TESTER itself wrote -- the tests the WRITER may not edit.

        From this run's state, else the ledger (a resume, or a pilot cycle
        that kept the suite), so ownership survives a process restart.
        """
        files = self.state.get("factory_test_files")
        if files:
            return list(files)
        events = list(self.ledger.events()) if self.ledger.exists() else []
        for event in reversed(events):
            recorded = (event.payload or {}).get("factory_test_files")
            if recorded:
                return list(recorded)
        return []

    def _behavior_test_files(self) -> list:
        """Tests run_tester's own emitters stamped -- assertions in these
        judge the PRODUCT by construction. From state, else the ledger,
        exactly like _factory_test_files, so ownership survives a resume."""
        files = self.state.get("behavior_test_files")
        if files:
            return list(files)
        events = list(self.ledger.events()) if self.ledger.exists() else []
        for event in reversed(events):
            payload = event.payload or {}
            if payload.get("behavior_test_files"):
                return list(payload["behavior_test_files"])
        return []

    def _run_phase(self, role: BuildRole, work_list: Sequence[str]) -> GateResult:
        """Run the role then its gate. Raises RoleError / AuthorityError up."""
        self.ledger.append(EventKind.PHASE_STARTED, role=role, detail=role.value)
        logger.info("phase start: role=%s", role.value)
        # The WRITER is staged: it rewrites app/ wholesale on every rework
        # round, and the agent picks different entity names each call, so a
        # pass killed part-way through would leave models.py from one attempt
        # beside routes.py from another. Observed live as
        # "no such table: field_defect". Other roles append rather than
        # replace, so a partial pass is recoverable by re-running them.
        staging = (
            self.workspace.parent / f".{self.workspace.name}.staging-{role.value.lower()}"
            if role is BuildRole.WRITER
            else None
        )
        if staging is not None and staging.exists():
            # A staging tree at phase start means the previous WRITER pass
            # died mid-way (a committed pass removes its own staging). It
            # used to be wiped unconditionally -- right for the in-process
            # coder, which rewrites app/ wholesale and picks new entity names
            # each call. The CodeWhale agent is different: it narrates every
            # step to docs/writer_progress.log, so it can read where it got
            # to and carry on. Wiping threw away a finished 17-step pass
            # because the server restarted 25 seconds after it (live).
            if self._writer_can_resume(staging):
                self.state["writer_resumed"] = True
                self.ledger.append(
                    EventKind.NOTE,
                    role=role,
                    detail=(
                        "resuming the interrupted writer pass -- its files and "
                        "progress log are kept; the agent continues, it does "
                        "not start over"
                    ),
                    payload={"writer_resumed": True},
                )
            else:
                shutil.rmtree(staging, ignore_errors=True)
        sealed = (
            SEALED_AFTER_CLONER
            if role is not BuildRole.CLONER
            and BuildRole.CLONER in self.ledger.completed_roles()
            else ()
        )
        ws = RoleWorkspace(
            role,
            self.workspace,
            store_root=self.store_root,
            staging=staging,
            sealed=sealed,
        )
        def _progress(detail: str, payload: Dict[str, Any]) -> None:
            """Record intra-phase progress as a ledger NOTE.

            NOTE deliberately: it is not a verdict, so completed_roles(),
            resume_point() and the terminal-event readers are untouched -- a
            progress line can never be mistaken for a gate result.
            """
            self._maybe_stage_inspect()
            self.ledger.append(EventKind.NOTE, role=role, detail=detail, payload=payload)

        deadline = self._deadline
        phase_cap = self.budget.phase_wall_clock_s
        if phase_cap and phase_cap > 0:
            phase_deadline = self.clock() + float(phase_cap)
            deadline = (
                phase_deadline
                if deadline is None
                else min(deadline, phase_deadline)
            )
        self._deadline_box["at"] = deadline
        self._deadline_box["clock"] = self.clock
        self._deadline_box["inspect"] = self._maybe_stage_inspect
        ctx = RoleContext(
            role=role,
            workspace=ws,
            blueprint=self.blueprint,
            plan=self.plan,
            blocks_root=self.blocks_root,
            blocks_lock=self.blocks_lock,
            work_list=tuple(work_list),
            state=self.state,
            progress=_progress,
            deadline=deadline,
            deadline_box=self._deadline_box,
        )
        # Protect the destination ledger while the role (and FACTORY_CODE_CLI)
        # can see the workspace. Notes still go through append(); raw JSONL
        # scribbles get PermissionError (CEREBRUMDEV-BACKEND-B).
        with self.ledger.protect():
            result = self.roles[role](ctx)
        if not result.ok:
            raise RoleError(
                result.detail or f"{role.value} reported failure",
                reason=result.reason or f"{role.value.lower()}_role_failed",
                location=result.location or role.value,
            )
        # Only now does the staged pass become visible. Everything before this
        # line could be interrupted without the destination ever changing.
        committed = ws.commit()
        if staging is not None:
            # Commit-time visibility: the log must say what the staged pass
            # actually carried — and scream when files on disk in staging
            # were NOT carried. The writer_no_output live incident
            # (sess_620b8581fb224bea runs 1-2) read 'authored=8' at the
            # role and '0' at the gate for three runs because the worker's
            # subprocess files were stranded in staging and nothing logged
            # the drop. A missing-file warning here exposes that on run 1.
            try:
                staged_files = sorted(
                    p.relative_to(staging).as_posix()
                    for p in Path(staging).rglob("*")
                    if p.is_file()
                )
            except OSError:
                staged_files = []
            missing = [
                rel for rel in staged_files if rel not in set(committed)
            ]
            logger.info(
                "staged commit: role=%s carried=%d staged_on_disk=%d",
                role.value,
                len(set(committed)),
                len(staged_files),
            )
            if missing:
                logger.warning(
                    "staged commit DROPPED %d file(s): %s",
                    len(missing),
                    ", ".join(missing[:10]),
                )
            shutil.rmtree(staging, ignore_errors=True)
        self._absorb(result)

        if role is BuildRole.TESTER:
            # G1: remember which test files the FACTORY wrote, so a failure in
            # one of them is routed to the Factory and not to a writer rework.
            written = sorted(
                str(rel).replace("\\", "/")
                for rel in (getattr(ws, "written", None) or [])
                if str(rel).replace("\\", "/").startswith("tests/")
            )
            if written:
                self.state["factory_test_files"] = written
                self.ledger.append(
                    EventKind.NOTE,
                    role=role,
                    detail=f"TESTER wrote {len(written)} test file(s)",
                    payload={
                        "factory_test_files": written,
                        # Strictly what run_tester's own emitters stamped
                        # (its snapshot delta) -- a file injected during the
                        # phase is in factory_test_files but NOT here, so an
                        # assertion in it stays the factory's own fault.
                        "behavior_test_files": list(
                            self.state.get("behavior_test_files") or []
                        ),
                    },
                )

        if role is BuildRole.CLONER:
            for bid in result.vendored_blocks:
                lock = (self.state.get("lock") or {}).get("blocks", {}).get(bid, {})
                self.ledger.record_clone(
                    block_id=bid,
                    source_commit=str(lock.get("commit", "unpinned")),
                    store_repo=str(lock.get("source", "unknown")),
                    vendored_path=str(lock.get("path", f"vendor/blocks/{bid}")),
                )

        gate = gate_for(role)
        verdict = gate(self._gate_context(role))
        kind = EventKind.GATE_PASSED if verdict.ok else EventKind.GATE_FAILED
        logger.info(
            "gate verdict: role=%s gate=%s ok=%s reason=%s",
            role.value,
            verdict.gate,
            verdict.ok,
            verdict.reason or "-",
        )
        self.ledger.append(
            kind,
            role=role,
            detail=verdict.detail,
            payload={
                "gate": verdict.gate,
                # F1: every gate failure carries a named reason token —
                # explicit when the gate supplies one, derived otherwise so
                # no failure can ever reach the ledger reasonless.
                "reason": verdict.reason or f"{verdict.gate}_failed",
                "location": role.value,
                "findings": list(verdict.findings),
                _findings.TYPED_KEY: _findings.typed(verdict.findings),
                "role_detail": result.detail,
                "wrote": list(ws.written),
            },
        )
        return verdict

    # -- terminal bookkeeping --------------------------------------------

    def _n3_handoff_armed(self) -> bool:
        """True when the cerebrum-builds handoff can actually push."""
        try:
            from app.factory.build.builds_push import builds_token

            return bool(builds_token(os.environ))
        except Exception:  # noqa: BLE001
            return False

    def _thin_cli_success_blocker(self) -> Optional[str]:
        """Refuse Store-green SUCCESS from pure templates when CLI is ready."""
        from app.factory.build.budget_inspect import inspect_build
        from app.factory.build.coder_session import thin_stub_success_blocked

        elapsed = 0.0
        if self._run_started is not None:
            elapsed = self.clock() - self._run_started
        snap = inspect_build(self.ledger, self.workspace, self.state)
        return thin_stub_success_blocked(
            snapshot=snap,
            elapsed_s=elapsed,
            state=self.state,
            ledger=self.ledger,
            workspace=self.workspace,
            plan=self.plan,
            blueprint=self.blueprint,
        )

    def _should_reopen_writer_for_cli(self) -> bool:
        """Code-cycle skipped C-BRIEF on a DeepSeek-ready store-complete plan."""
        from app.factory.build.coder_session import (
            cli_dispatch_attempted,
            deepseek_cli_ready,
        )

        if self._cli_writer_reopened:
            return False
        if not deepseek_cli_ready():
            return False
        if cli_dispatch_attempted(self.state, self.ledger):
            return False
        return True

    def _reworks(self) -> list:
        """Rework rounds since the last budget reset (rule_decision)."""
        from app.factory.build.rule_decision import BUDGET_RESET_KEY

        events = list(self.ledger.events()) if self.ledger.exists() else []
        for index in range(len(events) - 1, -1, -1):
            if (events[index].payload or {}).get(BUDGET_RESET_KEY):
                events = events[index + 1 :]
                break
        return [e for e in events if e.kind is EventKind.REWORK]

    def _rework_rounds_this_build(self) -> int:
        """Rework rounds spent in this build, every gate together (ceiling)."""
        return len(self._reworks())

    def _rework_rounds_at(self, role: BuildRole) -> int:
        """Rework rounds THIS gate has spent (its own budget)."""
        return sum(
            1 for e in self._reworks() if (e.payload or {}).get("source") == role.value
        )

    def _all_rework_failures(self) -> set:
        """Every failure key any earlier rework round was opened for."""
        keys: set = set()
        for event in self._reworks():
            keys.update((event.payload or {}).get("failure_names") or [])
        return keys

    def _decision_record(
        self,
        kind: str,
        *,
        role: BuildRole,
        verdict: Any,
        gate_round: int,
        build_round: int,
        reason: str = "",
    ) -> Dict[str, Any]:
        """One decision as the ledger and the Floor carry it: gate, class,
        round (this gate's n/2 and the build's n/6), check, finding."""
        from app.factory.build import brief_gates
        from app.factory.build import rule_decision as rd

        pairs = brief_gates.failure_checks(verdict)
        check, finding = pairs[0] if pairs else (str(getattr(verdict, "gate", "")), "")
        return {
            rd.GATE: role.value,
            rd.CHECK: check,
            rd.FINDING: str(finding)[:300],
            rd.CLASS: kind,
            rd.ROUND_GATE: gate_round,
            rd.ROUND_BUILD: build_round,
            "gate_budget": int(self.budget.max_rework),
            "build_ceiling": REWORK_CEILING,
            "gate_name": str(getattr(verdict, "gate", "") or ""),
            "reason": reason,
        }

    def _stop(self, outcome: "Outcome", reason: str, verdict: Any, role: BuildRole) -> GateDecision:
        rec = self._decision_record(
            DECISION_STOP,
            role=role,
            verdict=verdict,
            gate_round=self._rework_rounds_at(role),
            build_round=self._rework_rounds_this_build(),
            reason=reason,
        )
        from app.factory.build.rule_decision import stop_status

        detail = f"{stop_status(rec)}: {reason}"
        return GateDecision(
            DECISION_STOP,
            outcome=outcome,
            detail=detail,
            findings=tuple(getattr(verdict, "findings", None) or ()),
            record=rec,
        )

    def decide(
        self,
        role: BuildRole,
        verdict: Any,
        *,
        rework_used: int,
        test_defect_rounds: int = 0,
        reopen: bool = False,
        factory_file_reprompts: int = 0,
        handed: Sequence[str] = (),
    ) -> GateDecision:
        """THE runner rule for a failed phase verdict -- every gate, one place.

        1. Classify from data: a check the brief never defined is ADVISORY
           (logged, no rework, no budget); a writer test that demanded the
           impossible of a declared contract is REGENERATE_TEST (no budget);
           a writer pass that touched a Factory-owned file is REPROMPT (no
           budget, its own bound; past it a FACTORY stop);
           a brief-defined failure on product code is REWORK.
        2. REWORK spends the build's shared budget (``budget.max_rework``,
           REWORK_BUDGET from the Floor): the writer gets the typed findings
           and the command that re-checks them.
        3. The same check failing the same way twice, or a failure after the
           budget is spent, STOPs: ``FAILED(<check>, <finding>)``.
        4. Every decision is written to the ledger with its class and round.
        ``reopen``: the verdict arrived after the run ended (the N3 Store
        gate) -- the REWORK re-opens WRITER, TESTER and STORE_MANAGER.
        ``handed``: the work list the failed pass was given. A REPROMPT keeps
        what it still owes (the pass was rejected before anything else was
        judged).
        """
        from app.factory.build import brief_gates, failure_owner

        split = self._split_by_brief(verdict)
        if split is not None and split.invented:
            self._record_advisory(role, verdict, split)
            if not split.defined:
                rec = self._decision_record(
                    DECISION_ADVISORY,
                    role=role,
                    verdict=verdict,
                    gate_round=self._rework_rounds_at(role),
                    build_round=self._rework_rounds_this_build(),
                    reason=brief_gates.REASON_NOT_DEFINED,
                )
                self.ledger.append(
                    EventKind.GATE_PASSED,
                    role=role,
                    detail=(
                        f"advisory: {', '.join(split.invented_checks)} "
                        f"{brief_gates.REASON_NOT_DEFINED}"
                    ),
                    payload={
                        "gate": verdict.gate,
                        "advisory": True,
                        "advisory_checks": split.invented_checks,
                        "reason": brief_gates.REASON_NOT_DEFINED,
                        "location": role.value,
                        "decision": rec,
                    },
                )
                return GateDecision(DECISION_ADVISORY, record=rec)
            verdict = brief_gates.narrowed(verdict, split)

        if role in (BuildRole.WRITER, BuildRole.STORE_MANAGER):
            # The WRITER gate judges the writer's own tree; a Store-gate
            # verdict reaching here carries only the lines the floor assigns
            # to the PRODUCT (Factory-owned lines never fail the product).
            owned = {
                "owner": failure_owner.PRODUCT,
                "tests": [],
                "generator": "",
                "test_defects": [],
                "factory_owned": [],
            }
        else:
            owned = failure_owner.classify(
                verdict,
                self._factory_test_files(),
                behavior_test_files=self._behavior_test_files(),
            )
        defects = list(owned.get("test_defects") or [])
        if owned["owner"] == failure_owner.TEST_DEFECT:
            if test_defect_rounds >= self.budget.max_rework:
                return self._stop(
                    Outcome.FAILED_GATE,
                    "TEST_DEFECT unresolved after "
                    f"{test_defect_rounds} regeneration(s): " + ", ".join(owned["tests"]),
                    verdict,
                    role,
                )
            rec = self._decision_record(
                DECISION_REGENERATE_TEST,
                role=role,
                verdict=verdict,
                gate_round=self._rework_rounds_at(role),
                build_round=self._rework_rounds_this_build(),
                reason=f"test regeneration {test_defect_rounds + 1} (no rework budget)",
            )
            self.ledger.append(
                EventKind.NOTE,
                role=role,
                detail="TEST_DEFECT (writer regenerates; not a product "
                "failure): " + ", ".join(owned["tests"]),
                payload={
                    "owner": owned["owner"],
                    "test_defects": defects,
                    "round": test_defect_rounds + 1,
                    "writer_dispatched": True,
                    "product_failure": False,
                    "decision": rec,
                },
            )
            return GateDecision(
                DECISION_REGENERATE_TEST,
                work_list=tuple(_test_defect_items(defects)),
                record=rec,
            )

        if owned["owner"] != failure_owner.PRODUCT:
            label = (
                "FACTORY_FAULT"
                if owned["owner"] == failure_owner.FACTORY
                else "ENVIRONMENT_FAULT"
            )
            names = ", ".join(owned["tests"]) or verdict.detail
            where = f" (generator {owned['generator']})" if owned["generator"] else ""
            self.ledger.append(
                EventKind.NOTE,
                role=role,
                detail=f"{label}: {names}{where}",
                payload={
                    "owner": owned["owner"],
                    "tests": owned["tests"],
                    "generator": owned["generator"],
                    "rework": rework_used,
                    "writer_dispatched": False,
                },
            )
            return self._stop(Outcome.FAILED_GATE, f"{label}: {names}{where}", verdict, role)

        factory_rows = owned.get("factory_owned") or []
        if factory_rows:
            self.ledger.append(
                EventKind.NOTE,
                role=role,
                detail="FACTORY_ALSO_OWNS (not sent to the writer): "
                + ", ".join(str(t) for t in factory_rows),
                payload={"factory_owned": factory_rows, "routed": "PRODUCT"},
            )

        if role is BuildRole.WRITER and getattr(verdict, "reason", "") == WRITER_AUTHORED_FACTORY_FILE:
            return self._reprompt_factory_files(role, verdict, factory_file_reprompts, handed)

        current = _failure_keys(verdict, [d["nodeid"] for d in defects])
        again = failure_owner.repeated(self._all_rework_failures(), current)
        if again:
            return self._stop(
                Outcome.FAILED_GATE,
                "SAME_FAILURE_TWICE: " + ", ".join(again),
                verdict,
                role,
            )
        gate_rounds = self._rework_rounds_at(role)
        build_rounds = self._rework_rounds_this_build()
        if gate_rounds >= self.budget.max_rework:
            return self._stop(
                Outcome.FAILED_BUDGET_SPENT,
                f"{role.value} gate rework budget of {self.budget.max_rework} "
                f"spent; still failing: {verdict.detail}",
                verdict,
                role,
            )
        if build_rounds >= REWORK_CEILING:
            return self._stop(
                Outcome.FAILED_BUDGET_SPENT,
                f"build rework ceiling of {REWORK_CEILING} reached; "
                f"{role.value} gate still failing: {verdict.detail}",
                verdict,
                role,
            )

        if role is BuildRole.WRITER:
            work = tuple(_writer_gate_items(verdict))
        elif role is BuildRole.STORE_MANAGER:
            work = tuple(verdict.findings)
        else:
            work = (
                tuple(verdict.findings)
                + tuple(_test_defect_items(defects))
                + (_tester_recheck_item(verdict),)
            )
        # The foreman (F2): every hard stop above has already allowed this
        # round, so it can never extend a budget -- it can only rewrite the
        # round's instructions, or end the build earlier (confidence-gated).
        foreman = self._foreman_review(role, verdict, work)
        if foreman.stop:
            return self._stop(
                Outcome.FAILED_GATE, f"foreman:{foreman.stop_reason}", verdict, role
            )
        if foreman.used:
            raw = set(verdict.findings)
            work = tuple(foreman.work) + tuple(i for i in work if i not in raw)
        rec = self._decision_record(
            DECISION_REWORK,
            role=role,
            verdict=verdict,
            gate_round=gate_rounds + 1,
            build_round=build_rounds + 1,
        )
        payload: Dict[str, Any] = {
            "findings": list(verdict.findings),
            _findings.TYPED_KEY: _findings.typed(verdict.findings),
            "work_list": list(work),
            "work_list_typed": _findings.typed(work),
            "capability_ids": sorted(_findings.capability_ids(work)),
            "failure_names": current,
            "source": role.value,
            "gate": verdict.gate,
            "decision": rec,
        }
        if foreman.mode != "off":
            payload["foreman"] = {
                "mode": foreman.mode,
                "used": foreman.used,
                "fallback": foreman.fallback,
                "attempts": foreman.attempts,
                "instruction": foreman.instruction.to_json() if foreman.instruction else None,
                "refusals": list(foreman.errors),
            }
        if reopen:
            payload["reopen"] = [BuildRole.WRITER.value, BuildRole.TESTER.value, role.value]
        # The grant is recorded BEFORE the REWORK: a reopened run resumes from
        # the REWORK, and ledger.reopening_rework() reads it only while it is
        # the last event -- a NOTE after it would hide the reopen.
        self._grant_rework_wall(role, reopen=reopen, build_round=build_rounds + 1)
        self.ledger.append(
            EventKind.REWORK,
            role=REWORK_TARGET,
            detail=(
                f"{role.value} round {gate_rounds + 1}/{self.budget.max_rework} "
                f"(build {build_rounds + 1}/{REWORK_CEILING}, gate '{verdict.gate}'): "
                f"{verdict.detail}"
            ),
            payload=payload,
        )
        return GateDecision(DECISION_REWORK, work_list=work, record=rec)

    def _reprompt_factory_files(
        self, role: BuildRole, verdict: Any, reprompts: int, handed: Sequence[str] = ()
    ) -> GateDecision:
        """A writer pass touched Factory-owned files: re-prompt, never rework.

        The pass is rejected on the FIRST such write (the WRITER gate checks
        it before anything else; the Factory's versions are already back) and
        the writer is sent straight back with the paths. It is not the
        product's failure, so it spends none of the gate's rework budget and
        never counts toward the same-failure-twice history (owner, cycle 8:
        the fintech writer created TESTER's suites in rounds 1 and 2, then
        stopped on its first real finding with "WRITER gate rework budget of
        2 spent"). Bounded by its own count: a writer that keeps touching them
        after being told is a FACTORY stop, never charged to the product."""
        from app.factory.build import factory_owned

        touched = list((getattr(verdict, "payload", None) or {}).get("touched") or [])
        paths = ", ".join(str(row.get("path")) for row in touched) or str(verdict.detail)
        gate_rounds = self._rework_rounds_at(role)
        build_rounds = self._rework_rounds_this_build()
        if reprompts >= self.budget.max_rework:
            return self._stop(
                Outcome.FAILED_GATE,
                f"FACTORY_FAULT: the writer touched Factory-owned files after {reprompts} "
                f"re-prompt(s) naming them: {paths}",
                verdict,
                role,
            )
        rec = self._decision_record(
            DECISION_REPROMPT,
            role=role,
            verdict=verdict,
            gate_round=gate_rounds,
            build_round=build_rounds,
            reason=f"re-prompt {reprompts + 1} (no rework budget): Factory-owned files touched",
        )
        own = tuple(getattr(verdict, "findings", None) or ()) + (
            "[factory_owned] those files were put back as the Factory renders them; this pass is "
            "re-prompted at no rework cost. Leave every path under FACTORY-OWNED FILES in the brief "
            "alone -- create, edit or delete none of them -- and finish the product.",
        )
        # What the rejected pass was handed and still owes: the gate refused it
        # on the Factory-owned files before judging anything else, so a rework
        # finding it was sent to fix is still open. Live cycle 9 co-op
        # (sess_e41f2375a98342d1): the re-prompt named only the touched file,
        # the next pass never heard the rework finding, and the build stopped
        # on it as SAME_FAILURE_TWICE. Lines an earlier re-prompt wrote are
        # not carried: they named files this pass may not have touched.
        earlier = getattr(self, "_reprompt_lines", set())
        carried = tuple(
            item for item in dict.fromkeys(str(i) for i in handed or ())
            if item not in earlier and item not in own
        )
        self._reprompt_lines = earlier | set(own)
        work = own + carried
        self._grant_rework_wall(role, build_round=build_rounds)
        self.ledger.append(
            EventKind.NOTE,
            role=REWORK_TARGET,
            detail=f"REPROMPT {reprompts + 1}/{self.budget.max_rework} (no rework budget): {paths}",
            payload={
                "reprompt": reprompts + 1,
                "touched": touched,
                "finding_shape": factory_owned.finding_shape(touched) if touched else "",
                "work_list": list(work),
                "writer_dispatched": True,
                "product_failure": False,
                "decision": rec,
            },
        )
        return GateDecision(DECISION_REPROMPT, work_list=work, record=rec)

    def _grant_rework_wall(
        self, role: BuildRole, *, reopen: bool = False, build_round: int = 0
    ) -> None:
        """A granted REWORK round brings the wall time its re-run is entitled to.

        The owner's rule makes rework rounds the loop (2 per gate, 6 per
        build) and the wall a ceiling. A fixed whole-build wall cannot hold
        a run that is inside those budgets: live 2026-10-06 (01a1eed7) a
        production build reworked its WRITER once and was stopped at the
        next phase with "wall-clock budget of 2700s spent before TESTER
        completed". Each granted round therefore extends the wall so the
        phases it re-runs (REWORK_TARGET through the failed gate) each get
        their phase allowance from now -- through ``_extend_wall``, which
        never shrinks the wall and never passes ``hard_ceiling_s``. When the
        ceiling is reached the existing typed stop still ends the run.
        """
        allowance_each = float(self.budget.phase_wall_clock_s or 0.0)
        if allowance_each <= 0 or not self.budget.wall_clock_s:
            return
        if self._run_started is None:
            return
        if reopen:
            rerun = [BuildRole.WRITER, BuildRole.TESTER, role]
        else:
            start = BUILD_PHASES.index(REWORK_TARGET)
            stop = BUILD_PHASES.index(role) if role in BUILD_PHASES else start
            rerun = list(BUILD_PHASES[start : max(start, stop) + 1])
        rerun = list(dict.fromkeys(rerun))
        elapsed = max(0.0, float(self.clock()) - float(self._run_started))
        requested = elapsed + allowance_each * len(rerun)
        before = float(self.budget.wall_clock_s or 0.0)
        if requested > before or (
            self._deadline is not None
            and float(self._run_started) + requested > float(self._deadline)
        ):
            self._extend_wall(requested)
        granted = float(self.budget.wall_clock_s or 0.0)
        ceiling = float(self.budget.hard_ceiling_s or 0.0)
        self.ledger.append(
            EventKind.NOTE,
            role=role,
            detail=(
                f"rework round {build_round}: wall {before:g}s -> {granted:g}s "
                f"for {len(rerun)} phase(s) to re-run "
                f"({', '.join(r.value for r in rerun)})"
                + (f"; capped at the {ceiling:g}s ceiling" if ceiling and requested > ceiling else "")
            ),
            payload={
                "wall_grant": {
                    "round_build": build_round,
                    "phases": [r.value for r in rerun],
                    "allowance_each_s": allowance_each,
                    "requested_s": round(requested, 1),
                    "wall_before_s": before,
                    "wall_s": granted,
                    "ceiling_s": ceiling,
                    "capped": bool(ceiling and requested > ceiling),
                }
            },
        )

    def _foreman_context(self, findings: Sequence[Any] = ()) -> Any:
        """What the foreman's validation knows about this build (all data)."""
        from app.factory.build import foreman as fm

        caps = [c.capability_id for c in getattr(self.plan, "capabilities", None) or []]
        resolved = {
            str(b)
            for c in getattr(self.plan, "capabilities", None) or []
            for b in (c.block_ids or [])
        }
        store = resolved | {str(b) for b in getattr(self.plan, "dual_registered_blocks", None) or []}
        own = {
            str(v)
            for v in (
                getattr(self.blueprint, "product_name", ""),
                getattr(self.blueprint, "product_id", ""),
            )
            if v
        } | set(caps)
        return fm.ForemanContext(
            capabilities=frozenset(caps),
            finding_capabilities=frozenset(_findings.capability_ids(findings) & set(caps)),
            resolved_blocks=frozenset(resolved),
            store_blocks=frozenset(store),
            own_names=frozenset(own),
        )

    def _foreman_audit(self) -> Any:
        """F4: once per build, after TESTER passes, the foreman reads the
        workspace and PROPOSES checks for a human. Nothing here touches the
        verdict, the work list or the outcome, and any failure of the audit
        itself is a NOTE -- the build carries on exactly as it would have."""
        from app.factory.build import foreman as fm

        mode = fm.audit_mode()
        if mode == "off" or self.state.get("foreman_audit_done"):
            return fm.AuditResult(mode=mode)
        self.state["foreman_audit_done"] = True
        try:
            brief_path = self.workspace / "docs" / "coder_brief.md"
            brief_text = brief_path.read_text(encoding="utf-8") if brief_path.is_file() else ""

            def note(detail: str, **payload: Any) -> None:
                self.ledger.append(EventKind.NOTE, role=BuildRole.TESTER, detail=detail, payload=payload)

            return fm.audit_workspace(
                self.workspace,
                ctx=self._foreman_context(),
                brief_text=brief_text,
                history=fm.findings_history(self.ledger),
                note=note,
                mode=mode,
                session_id=str(self.state.get("session_id") or ""),
            )
        except Exception as exc:  # noqa: BLE001 -- the audit never breaks a build
            self.ledger.append(
                EventKind.NOTE,
                role=BuildRole.TESTER,
                detail=f"foreman audit unavailable ({type(exc).__name__}); no suggested checks",
                payload={"foreman_audit_error": type(exc).__name__},
            )
            return fm.AuditResult(mode=mode, fallback=True)

    def _foreman_review(self, role: BuildRole, verdict: Any, work: Sequence[Any]) -> Any:
        """One foreman call for a REWORK round (no call when FACTORY_FOREMAN=off).
        Any failure of the foreman itself leaves the round exactly as it was."""
        from app.factory.build import brief_gates
        from app.factory.build import foreman as fm

        mode = fm.foreman_mode()
        if mode == "off":
            return fm.ForemanResult(mode=mode)
        try:
            ctx = self._foreman_context(verdict.findings)
            pairs = brief_gates.failure_checks(verdict)
            check = pairs[0][0] if pairs else str(getattr(verdict, "gate", ""))
            brief_path = self.workspace / "docs" / "coder_brief.md"
            brief_text = brief_path.read_text(encoding="utf-8") if brief_path.is_file() else ""

            def note(detail: str, **payload: Any) -> None:
                self.ledger.append(EventKind.NOTE, role=role, detail=detail, payload=payload)

            return fm.review(
                list(verdict.findings),
                ctx=ctx,
                gate=role.value,
                check=check,
                brief_text=brief_text,
                changed=fm.changed_files(self.workspace, fm._last_green_at(self.ledger)),
                tail=fm.ledger_tail(self.ledger),
                note=note,
                mode=mode,
                session_id=str(self.state.get("session_id") or ""),
            )
        except Exception as exc:  # noqa: BLE001 -- the foreman never breaks a build
            self.ledger.append(
                EventKind.NOTE,
                role=role,
                detail=f"foreman unavailable ({type(exc).__name__}); the round uses the typed findings",
                payload={"foreman_error": type(exc).__name__},
            )
            return fm.ForemanResult(mode=mode, fallback=True)

    def reopen_after_store_gate(self, verdict: Any) -> GateDecision:
        """The N3 Store gate answers after the runner returned: run its verdict
        through the same rule. A STOP is the run's verdict, written as such."""
        decision = self.decide(
            BuildRole.STORE_MANAGER,
            verdict,
            rework_used=self._rework_rounds_this_build(),
            reopen=True,
        )
        if decision.kind == DECISION_STOP:
            self.ledger.append(
                EventKind.RUN_FAILED,
                role=BuildRole.STORE_MANAGER,
                detail=decision.detail,
                payload={
                    "outcome": decision.outcome.value if decision.outcome else "FAILED_GATE",
                    "decision": decision.record,
                    "findings": list(decision.findings),
                    _findings.TYPED_KEY: _findings.typed(decision.findings),
                },
            )
        return decision

    def _finish(
        self,
        outcome: Outcome,
        detail: str,
        *,
        phase: Optional[BuildRole] = None,
        rework: int = 0,
        findings: Sequence[str] = (),
        decision: Optional[Dict[str, Any]] = None,
    ) -> BuildOutcome:
        bar = getattr(self, "level_bar", None)
        if outcome is Outcome.SUCCESS:
            if self._climbs_past_code() and getattr(self, "cycle", "code") != "pilot":
                lie = (
                    "auto-pilot run reached code-cycle SUCCESS without a "
                    "Store-green pilot cycle — refuse SUCCESS+non-pilot lie"
                )
                logger.error("factory refuse code-cycle SUCCESS on auto-pilot: %s", lie)
                outcome = Outcome.FAILED_ROLE_ERROR
                detail = lie
                findings = list(findings) + [lie]
                phase = phase or BuildRole.TESTER
            # "Thin SUCCESS is a failure" is the pilot-and-above bar; a
            # prototype or light build is not held to the authorship floor.
            thin_applies = bar is None or bar.thin_success_is_failure
            blocked = self._thin_cli_success_blocker() if thin_applies else None
            if blocked:
                logger.error("factory refuse thin SUCCESS: %s", blocked)
                outcome = Outcome.FAILED_ROLE_ERROR
                detail = blocked
                findings = list(findings) + [blocked]
                phase = phase or BuildRole.WRITER
        kind = (
            EventKind.RUN_SUCCEEDED
            if outcome is Outcome.SUCCESS
            else EventKind.RUN_FAILED
        )
        if outcome is Outcome.SUCCESS:
            from app.factory.build.package import write_identity

            sealed = (
                SEALED_AFTER_CLONER
                if BuildRole.CLONER in self.ledger.completed_roles()
                else ()
            )
            ws = RoleWorkspace(
                BuildRole.WRITER,
                self.workspace,
                store_root=self.store_root,
                sealed=sealed,
            )
            write_identity(ws, extra={"engine": "role_runner"})
            # Delivery is the client's choice made at request time. A green
            # build with delivery_format=github_repo pushes now; a push
            # failure turns the run into a NAMED failure — a client who
            # asked for a repo must not be handed a silent zip instead.
            delivery_format = str(
                getattr(self.blueprint, "delivery_format", "zip") or "zip"
            )
            if delivery_format == "github_repo":
                try:
                    from app.factory.build.github_delivery import (
                        push_workspace_repo,
                    )

                    repo_url = push_workspace_repo(
                        self.workspace,
                        product_id=str(
                            getattr(self.blueprint, "product_id", "platform")
                        ),
                        session_id=str(self.state.get("session_id") or ""),
                    )
                    self.state["repo_url"] = repo_url
                    logger.info("github delivery: repo=%s", repo_url)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("github delivery failed")
                    outcome = Outcome.FAILED_ROLE_ERROR
                    detail = f"github_delivery_failed: {exc}"
                    findings = list(findings) + [detail]
                    phase = phase or BuildRole.STORE_MANAGER
                    self.state["repo_url"] = detail
        payload: Dict[str, Any] = {
            "outcome": outcome.value,
            "rework_used": rework,
            "findings": list(findings),
            _findings.TYPED_KEY: _findings.typed(findings),
            "cycle": getattr(self, "cycle", "code"),
            "pilot_ready": getattr(self, "cycle", "code") == "pilot"
            and outcome is Outcome.SUCCESS,
            "delivery_format": str(
                getattr(self.blueprint, "delivery_format", "zip") or "zip"
            ),
            "repo_url": self.state.get("repo_url") or "",
        }
        if bar is not None:
            # Each run states its level, where that level stops, and where
            # this run actually stopped.
            payload.update(bar.to_json())
            payload["stopped_at"] = self._stopped_at(outcome, phase)
        if decision is not None:
            # The rule's STOP is this ONE terminal event (rule_decision).
            from app.factory.build.rule_decision import DECISION_KEY

            payload[DECISION_KEY] = dict(decision)
        self.ledger.append(
            kind,
            role=phase,
            detail=detail,
            payload=payload,
        )
        if kind is EventKind.RUN_FAILED:
            self._attach_failure_narrative(phase)
        if (
            outcome is Outcome.SUCCESS
            and getattr(self, "cycle", "code") == "pilot"
        ):
            try:
                self._stage_inspect(reason="pilot_closed", stage="pilot_close")
            except Exception:  # noqa: BLE001 — close inspect must not fail SUCCESS
                logger.exception("factory closing inspect after Store-green SUCCESS failed")
        return BuildOutcome(
            outcome=outcome,
            detail=detail,
            failed_phase=phase,
            rework_used=rework,
            completed=tuple(p for p in BUILD_PHASES if p in self.ledger.completed_roles()),
            findings=list(findings),
            ledger_path=str(self.ledger.path),
        )

    def _attach_failure_narrative(self, phase: Optional[BuildRole]) -> None:
        """F5: a FAILED run explains itself from its own ledger (what was
        tried, where it stopped, why). The narrative never changes the
        verdict, and its own failure never breaks the run."""
        from app.factory.build import failure_narrative as fn

        def note(detail: str, **payload: Any) -> None:
            self.ledger.append(EventKind.NOTE, role=phase, detail=detail, payload=payload)

        try:
            narrative = fn.compose_failure_narrative(
                list(self.ledger.events()),
                note=note,
                llm=getattr(self, "narrative_llm", None),
            )
            fn.attach(self.ledger, narrative, role=phase)
        except Exception as exc:  # noqa: BLE001 -- telemetry for the owner, never a verdict
            logger.warning("failure narrative unavailable: %s", type(exc).__name__)

    def _climbs_past_code(self) -> bool:
        """True when this run continues past code SUCCESS: the declared
        level reaches the Store gate, or -- with no level -- auto_pilot."""
        bar = getattr(self, "level_bar", None)
        if bar is not None:
            return bar.reaches_pilot
        return bool(self.auto_pilot)

    def _should_auto_open_pilot(self) -> bool:
        """True when code-phase 5/5 must continue into a Store-green cycle."""
        return self.cycle != "pilot" and self._climbs_past_code()

    def _stopped_at(self, outcome: "Outcome", phase: Optional[BuildRole]) -> str:
        """The rung this run ended on: the last gate passed on SUCCESS, the
        gate that failed otherwise (located exactly as three_gate_verdict
        locates it)."""
        pilot = getattr(self, "cycle", "code") == "pilot"
        if outcome is Outcome.SUCCESS:
            return "STORE" if pilot else "CODE"
        if phase is BuildRole.STORE_MANAGER:
            return "STORE"
        if phase is BuildRole.TESTER and pilot:
            return "PRODUCT"
        return "CODE"

    def _note_build_level(self) -> None:
        """State the run's level in its own ledger before any phase runs."""
        bar = getattr(self, "level_bar", None)
        if bar is None:
            return
        self.ledger.append(
            EventKind.NOTE,
            detail=(
                f"BUILD_LEVEL {bar.level.value}: the run stops at the "
                f"{bar.stop_gate} gate"
            ),
            payload=bar.to_json(),
        )

    def _emit_inspect(self, snapshot: Dict[str, Any], *, reason: str) -> None:
        """Append an inspect snapshot as a NOTE — never a verdict."""
        detail = str(snapshot.get("reason") or f"budget inspect ({reason})")
        payload = dict(snapshot)
        payload["budget_inspect"] = True
        payload["inspect_reason"] = reason
        self.ledger.append(EventKind.NOTE, detail=detail, payload=payload)
        logger.info("factory budget inspect (%s): %s", reason, detail)

    def _stage_inspect(
        self, *, reason: str, stage: str, lead_s: float = 0.0
    ) -> Dict[str, Any]:
        from app.factory.build.budget_inspect import inspect_build, inspect_decision

        elapsed = 0.0
        if self._run_started is not None:
            elapsed = self.clock() - self._run_started
        snap = inspect_build(self.ledger, self.workspace, self.state)
        decided = inspect_decision(
            elapsed_s=elapsed,
            current_wall_s=float(self.budget.wall_clock_s or 0.0),
            snapshot=snap,
            stage=stage,
            state=self.state,
            workspace=self.workspace,
            lead_s=lead_s,
        )
        self._emit_inspect(decided, reason=reason)
        return decided

    def _extend_wall(self, new_wall: float) -> None:
        """Lengthen the build wall and the live role deadline. Never shrink.

        Clamped to ``budget.hard_ceiling_s`` HERE rather than at each caller:
        this is the only writer of the deadline, and two of its callers pass
        ``elapsed + PILOT_SUITE_TAIL_S`` without consulting the inspector, so
        a cap applied only where values are proposed is not a cap.

        The CLI default ``wall_clock_s`` is already the 7200s ceiling, so a
        grow-only writer would no-op while the 1500s *phase* box still
        kills C-BRIEF. Lifting the live deadline to ``run_started +
        capped(new_wall)`` is what actually uses the ceiling.
        """
        old_wall = float(self.budget.wall_clock_s or 0.0)
        requested = float(new_wall)
        new_wall = self.budget.capped(requested)
        target = None
        if self._run_started is not None and new_wall > 0:
            target = float(self._run_started) + float(new_wall)

        lifted = False
        if target is not None:
            if self._deadline is None or float(self._deadline) + 1e-9 < target:
                self._deadline = target
                lifted = True
            boxed = self._deadline_box.get("at")
            if boxed is None or float(boxed) + 1e-9 < target:
                self._deadline_box["at"] = target
                lifted = True

        grew = new_wall > old_wall
        if grew:
            if target is None:
                add = new_wall - old_wall
                if self._deadline is not None:
                    self._deadline += add
                elif self._run_started is not None:
                    self._deadline = self._run_started + new_wall
                boxed = self._deadline_box.get("at")
                if boxed is not None:
                    self._deadline_box["at"] = boxed + add
                elif self._deadline is not None:
                    self._deadline_box["at"] = self._deadline
            self.budget = BuildBudget(
                max_rework=self.budget.max_rework,
                wall_clock_s=new_wall,
                phase_wall_clock_s=self.budget.phase_wall_clock_s,
                hard_ceiling_s=self.budget.hard_ceiling_s,
            )
        elif not lifted:
            if requested > old_wall:
                logger.info(
                    "factory budget ramp REFUSED: %.0fs requested, wall already "
                    "at the %.0fs ceiling",
                    requested,
                    float(self.budget.hard_ceiling_s),
                )
            return

        extra = ""
        if requested > new_wall:
            extra = f" [clamped from {requested:.0f}s]"
        elif lifted and not grew:
            extra = " [phase box lifted to ceiling]"
        logger.info(
            "factory budget ramp: wall %.0fs → %.0fs (deadline live)%s",
            old_wall,
            new_wall,
            extra,
        )
        self._record_lifted_model_call_deadline()

    def _record_lifted_model_call_deadline(self) -> None:
        """Write the lifted deadline onto the open coding-agent model call.

        ONE source of truth for "this model call's deadline": the ledger's
        latest open model-call NOTE. The worker writes it once at dispatch
        (deadline_s = the dispatch wall); build_jobs._model_call_overdue
        judges the latest open NOTE by its age and deadline_s. A lift that
        writes nothing leaves the reader timing the call out at the dispatch
        wall while the runner and the worker have moved on (live 2026-10-06:
        "coder LLM timed out after 1803s (deadline 1800s)").

        The NOTE carries the box's remaining time -- the live deadline the
        worker now waits on, already clamped to hard_ceiling_s by
        _extend_wall -- and the open call's own source, so it is the same
        call, not a new one. Nothing is written when no coding-agent call is
        in flight.

        Boundary: the ramp fires once the box has <= CLI_PHASE_RAMP_HEADROOM_S
        left, its pulse arrives at least every worker heartbeat, and the
        dispatch NOTE's deadline is never earlier than box end minus the
        writer's grace -- so the lift is recorded before the reader may time
        the dispatch NOTE out (pinned by a test on those three constants).
        """
        from app.factory.build.authorship import is_coding_agent_source
        from app.factory.build.model_call import closes_model_call

        boxed = self._deadline_box.get("at")
        if boxed is None:
            return
        source = None
        for event in reversed(list(self.ledger.events())):
            payload = getattr(event, "payload", None) or {}
            if closes_model_call(payload):
                return
            if payload.get("model_call"):
                if is_coding_agent_source(payload.get("source")):
                    source = payload.get("source")
                break
        if source is None:
            return
        remaining = max(0.0, float(boxed) - float(self.clock()))
        self.ledger.append(
            EventKind.NOTE,
            detail=(
                "coder CLI still in flight — live deadline lifted to "
                f"{remaining:.0f}s from now"
            ),
            payload={
                "model_call": True,
                "source": source,
                "deadline_s": round(remaining, 1),
                "cli_wall_extended": True,
            },
        )

    def _cli_in_flight(self) -> bool:
        from app.factory.build.budget_inspect import _cli_flight

        flight = _cli_flight(list(self.ledger.events()), self.state)
        return bool(flight.get("cli_in_flight"))

    def _slot_waiting(self) -> bool:
        """A coding agent queued for a build slot (typed ``slot_wait`` NOTE).
        Its wait is bounded by the phase-wall ceiling, so the phase box is
        lifted for it exactly as for a live call."""
        from app.factory.build.budget_inspect import _cli_flight

        flight = _cli_flight(list(self.ledger.events()), self.state)
        return bool(flight.get("slot_waiting"))

    def _cli_approaching_phase_wall(self) -> bool:
        phase_cap = float(self.budget.phase_wall_clock_s or 0.0)
        if phase_cap <= 0:
            return False
        boxed = self._deadline_box.get("at")
        now = float(self.clock())
        if boxed is not None:
            leftover = float(boxed) - now
        elif self._run_started is not None:
            leftover = phase_cap - (now - float(self._run_started))
        else:
            return False
        headroom = max(
            min(phase_cap * 0.2, CLI_PHASE_RAMP_HEADROOM_S),
            0.05,
        )
        return leftover <= headroom

    def _maybe_cli_phase_ramp(self) -> None:
        """Lift the phase-capped box to the wall already granted.

        CLI production ``wall_clock_s`` is already the 7200s ceiling;
        the 1500s phase box is what kills C-BRIEF. Do not jump a staged
        1800s wall to 7200 here — stage inspect still owns 30→45.
        """
        if self._run_started is None:
            return
        if not (self._cli_in_flight() or self._slot_waiting()):
            return
        if not self._cli_approaching_phase_wall():
            return
        granted = float(self.budget.wall_clock_s or 0.0)
        if granted <= 0:
            return
        self._extend_wall(granted)

    def _maybe_stage_inspect(self) -> Optional[Dict[str, Any]]:
        """Hard-stop inspect at ~30 min and ~45 min. No silent 2h bump."""
        from app.factory.build.budget_inspect import STAGE_1_S, STAGE_2_S

        if self._run_started is None:
            return None
        self._maybe_cli_phase_ramp()
        elapsed = self.clock() - self._run_started
        # While a coding-agent call (or a slot wait) is live, inspect a
        # headroom BEFORE the mark. On a staged run the box ends AT the mark
        # and the phase ramp cannot lift it (it extends to the staged wall it
        # already has), so an inspect that waits for the mark races the
        # writer's own wall -- dispatch + 1800, only the COLLECTOR/CLONER
        # seconds later -- with pulses a heartbeat apart (live 2026-10-07:
        # the 1800 -> 2700 lift landed the same second the worker was
        # killed). Pulses arrive every heartbeat < headroom, so one lands in
        # the window. With nothing live the inspect keeps its mark.
        lead = (
            CLI_PHASE_RAMP_HEADROOM_S
            if (self._cli_in_flight() or self._slot_waiting())
            else 0.0
        )
        stage = None
        mark = None
        if elapsed + lead + 0.01 >= STAGE_1_S and "stage_1" not in self._inspects_done:
            stage, mark = "stage_1", STAGE_1_S
        elif elapsed + lead + 0.01 >= STAGE_2_S and "stage_2" not in self._inspects_done:
            stage, mark = "stage_2", STAGE_2_S
        if stage is None:
            return None
        self._inspects_done.add(stage)
        decided = self._stage_inspect(
            reason=f"{stage}_{int(mark)}s", stage=stage, lead_s=lead
        )
        new_wall = decided.get("next_wall_s")
        if new_wall:
            self._extend_wall(float(new_wall))
            if decided.get("cli_in_flight"):
                self._emit_cli_watchdog_extend(decided)
        elif decided.get("decision") == "hard_stop":
            self._stage_halt = decided
            self.state["stage_halt"] = decided
        return decided

    def _emit_cli_watchdog_extend(self, decided: Mapping[str, Any]) -> None:
        """Keep Floor Last:/watchdog aligned with the bumped CLI wall."""
        from app.factory.build.coder_session import cli_watchdog_remaining_s

        boxed = self._deadline_box.get("at")
        if boxed is None:
            boxed = self._deadline
        left = None
        if boxed is not None:
            left = float(boxed) - float(self.clock())
        elapsed = 0.0
        if self._run_started is not None:
            elapsed = float(self.clock()) - float(self._run_started)
        deadline_s = cli_watchdog_remaining_s(
            leftover_s=left, elapsed_s=elapsed
        )
        payload: Dict[str, Any] = {
            "stage": "dispatch",
            "source": "coder CLI",
            "model_call": True,
            "cli_wall_extended": True,
        }
        if deadline_s is not None:
            payload["deadline_s"] = round(deadline_s, 1)
        self.ledger.append(
            EventKind.NOTE,
            detail=(
                "FACTORY_CODE_CLI still in-flight — staged wall extended "
                f"to {decided.get('next_wall_s'):g}s; model call still "
                "inside watchdog"
            ),
            payload=payload,
        )

    def _grant_pilot_budget(self) -> None:
        """Keep rework room for the pilot cycle. Do not jump the wall to 2h.

        Extra time is inspect-and-ramp only. A leftover high wall is left
        alone (never slashed).
        """
        # The rework budget is the owner's rule (REWORK_BUDGET per gate),
        # the same at every cycle: opening the pilot cycle does not raise it.
        self.budget = BuildBudget(
            max_rework=int(self.budget.max_rework),
            wall_clock_s=self.budget.wall_clock_s,
            phase_wall_clock_s=self.budget.phase_wall_clock_s,
        )
        snap = self._stage_inspect(reason="pilot_opened", stage="pilot_open")
        new_wall = snap.get("next_wall_s")
        if new_wall:
            self._extend_wall(float(new_wall))
        elif (
            int(snap.get("agent_written") or 0) > 0
            and not snap.get("contract_misses")
        ):
            from app.factory.build.auto_pilot import PILOT_SUITE_TAIL_S

            elapsed = 0.0
            if self._run_started is not None:
                elapsed = float(self.clock()) - float(self._run_started)
            remaining = float(self.budget.wall_clock_s or 0.0) - elapsed
            # Only when the staged wall is essentially spent (long C-BRIEF).
            # Do not nudge a healthy STAGE_2 leftover by 1s.
            if remaining < 120.0:
                needed = elapsed + float(PILOT_SUITE_TAIL_S)
                if needed > float(self.budget.wall_clock_s or 0.0):
                    self._extend_wall(needed)
        # Pilot-open inspect is informational when nothing was written.
        # Stage-1/2 hard-stops own ramps for thin/stub runs.
        elif snap.get("decision") == "hard_stop" and not snap.get("progressing"):
            logger.info("pilot open inspect: no extra wall — %s", snap.get("reason"))

    def _acceptance_regrade_needed(self) -> bool:
        """Ledger-pilot SUCCESS is not Store-green until acceptance is k/k."""
        from app.factory.build.store_acceptance import workspace_acceptance_is_kk

        return not workspace_acceptance_is_kk(self.workspace)

    def _acceptance_writer_needed(self) -> bool:
        from app.factory.build.store_acceptance import acceptance_surface_incomplete

        return acceptance_surface_incomplete(self.workspace)

    def _open_pilot_for_acceptance(
        self,
        *,
        reason: str,
        auto_pilot: bool = False,
    ) -> None:
        """Reopen STORE measurement (and WRITER when the harness is missing)."""
        reopen_writer = self._acceptance_writer_needed()
        payload: Dict[str, Any] = {
            "cycle": "pilot",
            "reopen_writer": reopen_writer,
        }
        if auto_pilot:
            payload["auto_pilot"] = True
        self.ledger.append(
            EventKind.NOTE,
            detail=reason,
            payload=payload,
        )
        self.ledger.open_pilot_cycle(reason=reason, reopen_writer=reopen_writer)
        self.cycle = "pilot"
        self.state["build_cycle"] = "pilot"
        if auto_pilot:
            self._grant_pilot_budget()

    def _open_auto_pilot(self) -> None:
        """Reopen TESTER + STORE_MANAGER without writing a code SUCCESS."""
        bar = getattr(self, "level_bar", None)
        why = (
            f"build level {bar.level.value} stops at {bar.stop_gate}"
            if bar is not None
            else "factory LLM configured"
        )
        self._open_pilot_for_acceptance(
            reason=f"code-phase SUCCESS; auto-opening Store-green cycle ({why})",
            auto_pilot=True,
        )

    # -- the run ---------------------------------------------------------

    def run(self) -> BuildOutcome:
        started = self.clock()
        deadline = self.budget.deadline_from(started)
        self._run_started = started
        self._deadline = deadline
        self._deadline_box["at"] = deadline
        # The run's phase-wall CEILING: the latest any wait in this run may
        # last (a writer queued for a build slot waits up to here and no
        # further -- owner, 2026-10-06: "timeout at the phase-wall ceiling
        # only"). None when the run has no ceiling.
        ceiling = float(self.budget.hard_ceiling_s or 0.0)
        self._deadline_box["ceiling_at"] = (started + ceiling) if ceiling > 0 else None
        inputs_hash = self.inputs_hash
        self.state["inputs_hash"] = inputs_hash

        # Refuse to continue a run whose blueprint changed underneath it.
        self.ledger.assert_resumable(inputs_hash=inputs_hash)
        if not self.ledger.exists():
            self.workspace.mkdir(parents=True, exist_ok=True)
            self.ledger.start_run(
                product_id=getattr(self.blueprint, "product_id", "unknown"),
                inputs_hash=inputs_hash,
            )
        self._note_build_level()

        from app.factory.build.preflight import evaluate_preflight

        preflight = evaluate_preflight()
        self.state["preflight"] = {
            "verdict": preflight["verdict"],
            "git_sha": preflight["git_sha"],
            "emitter": preflight["emitter_identity"]["id"],
            "kernel_ownership_ok": preflight["kernel_ownership"]["ok"],
        }
        if not preflight["ok"]:
            return self._finish(
                Outcome.FAILED_GATE,
                f"S0 preflight failed: {preflight.get('first_failing_criterion')}",
            )

        self._restore_workspace_state()
        if self.cycle == "pilot":
            self.state["build_cycle"] = "pilot"
            if (
                self.ledger.exists()
                and self.ledger.code_phase_succeeded()
                and not self.ledger.pilot_cycle_open()
                and (
                    not self.ledger.pilot_ready()
                    or self._acceptance_regrade_needed()
                )
            ):
                self._open_pilot_for_acceptance(
                    reason=(
                        "acceptance not k/k; re-opening STORE measurement"
                        if self.ledger.pilot_ready()
                        else "code-phase SUCCESS; opening Store-green cycle"
                    )
                )

        done = self.ledger.completed_roles()
        # G2: a resume does NOT reset the rework counter. Rounds already spent
        # in this cycle stay spent, so a re-entered run cannot buy a fresh
        # budget to repeat what already failed.
        rework_used = self._rework_rounds_this_build()
        # Writer-test regenerations for TEST_DEFECTs: their own count, never
        # the product rework budget.
        test_defect_rounds = 0
        # WRITER passes re-prompted for touching Factory-owned files: their own
        # count, never the rework budget (_reprompt_factory_files).
        factory_file_reprompts = 0
        work_list: Sequence[str] = ()
        collected: list[str] = []

        # A re-entered run that FAILED at the phase it is about to re-run must
        # be handed what failed. Live 2026-09-30 (automotive-aiops): re-entry
        # at WRITER worked, but the resumed writer got an EMPTY work_list and
        # failed the identical ui_not_wired_end_to_end gate a second time --
        # the prior verdict sat on the ledger's RUN_FAILED event and was never
        # read back, so the compiled brief named nothing to fix. Seed the
        # first re-run phase from that event: its structured findings, or its
        # detail when the gate surfaced as a RoleError (which carries none).
        # Cleared to () the moment a phase passes (below), so it only reaches
        # the resumed phase, and a fresh first run has no terminal event.
        terminal = self.ledger.terminal_event()
        if (
            terminal is not None
            and terminal.kind is EventKind.RUN_FAILED
            and terminal.role is not None
            and terminal.role == self.ledger.resume_point()
        ):
            # Typed when the ledger recorded them (capability ids survive a
            # resume); the plain strings of an older ledger otherwise.
            seeded = [f for f in _findings.from_payload(terminal.payload) if str(f).strip()]
            if not seeded and terminal.detail.strip():
                seeded = [terminal.detail.strip()]
            work_list = tuple(seeded)
        # A finished run re-opened by a REWORK (a product-owned Store-gate
        # failure): hand the reopened phase the typed items that REWORK
        # carries -- the same seed, from the event that re-opened the run.
        reopened = self.ledger.reopening_rework()
        if (
            not work_list
            and reopened is not None
            and reopened.role == self.ledger.resume_point()
        ):
            typed_work = [
                _findings.Finding.from_json(r)
                for r in (reopened.payload.get("work_list_typed") or [])
                if isinstance(r, dict)
            ]
            plain = [
                str(f)
                for f in (
                    reopened.payload.get("work_list")
                    or reopened.payload.get("findings")
                    or []
                )
                if str(f).strip()
            ]
            # The typed items replace their own text; untyped items (the
            # re-check command, test-defect items) keep their text.
            typed_text = {str(f) for f in typed_work}
            work_list = tuple(typed_work + [f for f in plain if f not in typed_text])

        # A re-entered run (resume, or a pasted build link) carries the
        # Factory files of the Factory that first built it. Re-render them
        # from the current templates before TESTER judges the build, so a
        # fixed Factory rule reaches every old branch. Coder files untouched.
        if BuildRole.WRITER in done and BuildRole.TESTER not in done:
            self._refresh_factory_files()

        index = 0
        while True:
            while index < len(BUILD_PHASES):
                role = BUILD_PHASES[index]
                if role in done:
                    index += 1
                    continue

                self._maybe_stage_inspect()
                deadline = self._deadline
                if self._stage_halt:
                    halt = self._stage_halt
                    return self._finish(
                        Outcome.FAILED_BUDGET_SPENT,
                        (
                            f"wall-clock budget of {self.budget.wall_clock_s:g}s spent "
                            f"before {role.value} completed; "
                            + str(halt.get("reason") or "stage stop after inspect")
                        ),
                        phase=role,
                        rework=rework_used,
                        findings=list(halt.get("pilot_ready_blockers") or [])
                        + list(halt.get("contract_misses") or [])[:8],
                    )
                if deadline is not None and self.clock() >= deadline:
                    snap = self._stage_inspect(
                        reason="wall_reached", stage="wall"
                    )
                    new_wall = snap.get("next_wall_s")
                    if new_wall:
                        self._extend_wall(float(new_wall))
                        if snap.get("cli_in_flight"):
                            self._emit_cli_watchdog_extend(snap)
                        deadline = self._deadline
                    elif (
                        snap.get("decision")
                        in {
                            "inspect_only_high_wall_honored",
                            "continue_pilot",
                            "continue_ceiling",
                        }
                        and int(snap.get("agent_written") or 0) > 0
                        and not snap.get("contract_misses")
                    ):
                        from app.factory.build.auto_pilot import PILOT_SUITE_TAIL_S

                        elapsed_now = 0.0
                        if self._run_started is not None:
                            elapsed_now = float(self.clock()) - float(
                                self._run_started
                            )
                        self._extend_wall(elapsed_now + float(PILOT_SUITE_TAIL_S))
                        deadline = self._deadline
                    else:
                        return self._finish(
                            Outcome.FAILED_BUDGET_SPENT,
                            (
                                f"wall-clock budget of {self.budget.wall_clock_s:g}s "
                                f"spent before {role.value} completed"
                                + (
                                    f"; {snap.get('reason')}"
                                    if snap.get("reason")
                                    else ""
                                )
                            ),
                            phase=role,
                            rework=rework_used,
                            findings=list(snap.get("pilot_ready_blockers") or []),
                        )

                # Factory-owned files (the acceptance harness, release gate,
                # CI) are the Factory's, never the writer's. The writer edits
                # the whole workspace and can reach older workspaces, so what
                # it leaves there is not trusted: re-render them from the
                # current templates every time TESTER is about to judge --
                # a fresh run as well as a re-entered one. Live 2026-10-04: a
                # fresh build shipped an older Factory's 21-check harness and
                # the Store gate refused its 21/21 for not being 22/22.
                if role is BuildRole.TESTER:
                    self._refresh_factory_files()
                try:
                    assert_phase_order(role, done)
                    verdict = self._run_phase(role, work_list)
                except AuthorityError as exc:
                    self.ledger.append(
                        EventKind.PHASE_ABORTED,
                        role=role,
                        detail=f"lane violation: {exc}",
                    )
                    return self._finish(
                        Outcome.FAILED_AUTHORITY,
                        f"{role.value} wrote outside its lane: {exc}",
                        phase=role,
                        rework=rework_used,
                    )
                except RoleError as exc:
                    if _collect_all_enabled():
                        finding = f"{role.value}: role error: {exc}"
                        collected.append(finding)
                        self.ledger.append(
                            EventKind.NOTE,
                            role=role,
                            detail=f"COLLECT-ALL: halt suppressed — {finding}",
                            payload={"collect_all": True, "finding": finding},
                        )
                        done.add(role)
                        work_list = ()
                        index += 1
                        continue
                    reason = getattr(exc, "reason", "") or ""
                    location = getattr(exc, "location", "") or role.value
                    self.ledger.append(
                        EventKind.PHASE_ABORTED,
                        role=role,
                        detail=str(exc),
                        payload={
                            "reason": reason,
                            "location": location,
                            "failure_kind": failure_kind(exc),
                        },
                    )
                    logger.error(
                        "phase aborted: role=%s reason=%s location=%s",
                        role.value,
                        reason or "-",
                        location,
                    )
                    return self._finish(
                        Outcome.FAILED_ROLE_ERROR,
                        f"{role.value} failed: {exc}",
                        phase=role,
                        rework=rework_used,
                    )

                if self._stage_halt:
                    halt = self._stage_halt
                    return self._finish(
                        Outcome.FAILED_BUDGET_SPENT,
                        (
                            f"wall-clock budget of {self.budget.wall_clock_s:g}s spent "
                            f"before {role.value} completed; "
                            + str(halt.get("reason") or "stage stop after inspect")
                        ),
                        phase=role,
                        rework=rework_used,
                        findings=list(halt.get("pilot_ready_blockers") or [])
                        + list(halt.get("contract_misses") or [])[:8],
                    )

                if verdict.ok:
                    # R3: the branch of record is checkpointed after every
                    # passed phase -- phase-forward commits only -- so the
                    # branch never lags the run it belongs to. A platform's
                    # build/<platform_id> when cerebrum-builds is armed; else
                    # an attached legacy branch.
                    attached = self._branch_of_record()
                    if attached:
                        from app.factory.build.branch_attach import checkpoint_message

                        try:
                            sha = self._push_branch_of_record(checkpoint_message(role.value))
                        except Exception as exc:  # noqa: BLE001 -- named, never silent
                            return self._finish(
                                Outcome.FAILED_GATE,
                                f"CHECKPOINT_FAILED after {role.value}: {exc}",
                                phase=role,
                                rework=rework_used,
                            )
                        self.ledger.append(
                            EventKind.NOTE,
                            role=role,
                            detail=f"CHECKPOINT {role.value} -> {attached}@{sha[:7]}",
                            payload={"checkpoint": sha, "attached_branch": attached},
                        )
                    if role is BuildRole.TESTER:
                        # F4: proposals only, after green; never a verdict.
                        self._foreman_audit()
                    done.add(role)
                    work_list = ()
                    index += 1
                    continue

                # Gate failed. In collect-all mode every failed gate is a
                # recorded finding, never a halt: the phase is marked done so
                # later phases still run and ONE instrumented run surfaces the
                # complete list. No rework rounds either — a single linear pass.
                if _collect_all_enabled():
                    header = (
                        f"{role.value} gate '{verdict.gate}' failed: {verdict.detail}"
                    )
                    collected.append(header)
                    collected.extend(f"{role.value}: {f}" for f in verdict.findings)
                    self.ledger.append(
                        EventKind.NOTE,
                        role=role,
                        detail=f"COLLECT-ALL: halt suppressed — {header}",
                        payload={
                            "collect_all": True,
                            "gate": verdict.gate,
                            "findings": list(verdict.findings),
                            _findings.TYPED_KEY: _findings.typed(verdict.findings),
                        },
                    )
                    done.add(role)
                    work_list = ()
                    index += 1
                    continue

                # Only the TESTER sends work back to the WRITER;
                # every other failed gate is terminal, because there is no role
                # positioned to act on its findings.
                #
                # EXCEPT the docker-shaped STORE refusal: when the host has no
                # docker and cerebrum-builds is armed (CEREBRUM_BUILDS_GITHUB_TOKEN),
                # the workspace is handed off to the N3 store-gate instead of
                # failing — Store-green is measured there, 12/12, not faked
                # on the host. A failed handoff is a named failure, never a
                # silent docker-skip pass.
                if (
                    role is BuildRole.STORE_MANAGER
                    and verdict.reason == "docker_unavailable"
                    and self._n3_handoff_armed()
                ):
                    try:
                        from app.factory.build.builds_push import push_workspace
                        from app.factory.build.n3_store_gate import (
                            HANDOFF_TO_N3,
                            dispatch_store_gate,
                        )

                        # The Factory's receipt -- what it stamped, vendored
                        # and declared -- taken after the last restamp, so the
                        # gate's audit findings are owned by provenance.
                        from app.factory.build.factory_receipt import record_receipt

                        record_receipt(self.workspace)
                        attached = self._branch_of_record()
                        gate_sha = ""
                        if attached:
                            # R3: a platform's Docker gate runs on its OWN
                            # branch of record, never a new sibling.
                            pushed = self._push_branch_of_record("factory: hand off to store-gate")
                            gate_sha = pushed.strip() if isinstance(pushed, str) else ""
                            gate_branch = attached
                        else:
                            gate_branch = push_workspace(
                                self.workspace,
                                env=os.environ,
                                session_id=str(self.state.get("session_id") or ""),
                            ).branch
                        # App-token pushes do not trigger workflow runs;
                        # dispatch the store-gate explicitly or the handoff
                        # waits forever. The dispatch carries main's whole
                        # gate onto the branch first, so the commit it
                        # gates may be newer than the push: N3 reads THAT.
                        gated = dispatch_store_gate(gate_branch, env=os.environ)
                        if isinstance(gated, str) and gated.strip():
                            gate_sha = gated.strip()
                        self.ledger.append(
                            EventKind.NOTE,
                            role=role,
                            detail=(
                                "docker unavailable — workspace handed off to "
                                "cerebrum-builds; N3 store-gate is next"
                            ),
                            # The gate's branch -- and the commit pushed to
                            # it, when the push named one -- recorded where
                            # every reader looks first
                            # (builds_fields_from_ledger): N3 reads THIS
                            # round's gate run, never an earlier round's.
                            payload={
                                "handoff": HANDOFF_TO_N3,
                                "builds_branch": gate_branch,
                                **({"builds_sha": gate_sha} if gate_sha else {}),
                            },
                        )
                        delivery_format = str(
                            getattr(self.blueprint, "delivery_format", "zip")
                            or "zip"
                        )
                        if delivery_format == "github_repo":
                            from app.factory.build.github_delivery import (
                                push_workspace_repo,
                            )

                            self.state["repo_url"] = push_workspace_repo(
                                self.workspace,
                                product_id=str(
                                    getattr(self.blueprint, "product_id", "platform")
                                ),
                                session_id=str(self.state.get("session_id") or ""),
                            )
                        return self._finish(
                            Outcome.HANDOFF_TO_N3,
                            "docker unavailable; workspace handed off to "
                            "cerebrum-builds — N3 store-gate is next",
                            phase=role,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("N3 handoff failed")
                        return self._finish(
                            Outcome.FAILED_GATE,
                            f"{role.value} gate '{verdict.gate}' failed and N3 "
                            f"handoff failed: {exc}",
                            phase=role,
                            findings=list(verdict.findings)
                            + [f"n3_handoff_failed: {exc}"],
                        )

                if role not in REWORK_SOURCES:
                    return self._finish(
                        Outcome.FAILED_GATE,
                        f"{role.value} gate '{verdict.gate}' failed: {verdict.detail}",
                        phase=role,
                        rework=rework_used,
                        findings=verdict.findings,
                    )

                decision = self.decide(
                    role,
                    verdict,
                    rework_used=rework_used,
                    test_defect_rounds=test_defect_rounds,
                    factory_file_reprompts=factory_file_reprompts,
                    handed=work_list,
                )
                if decision.kind == DECISION_ADVISORY:
                    done.add(role)
                    work_list = ()
                    index += 1
                    continue
                if decision.kind == DECISION_STOP:
                    return self._finish(
                        decision.outcome or Outcome.FAILED_GATE,
                        decision.detail,
                        phase=role,
                        rework=rework_used,
                        findings=decision.findings,
                        decision=decision.record,
                    )
                if decision.kind == DECISION_REGENERATE_TEST:
                    test_defect_rounds += 1
                elif decision.kind == DECISION_REPROMPT:
                    factory_file_reprompts += 1
                else:
                    rework_used += 1
                work_list = decision.work_list
                # Send the WRITER back round. Its earlier pass no longer counts.
                done.discard(REWORK_TARGET)
                index = BUILD_PHASES.index(REWORK_TARGET)

            if collected:
                return self._finish(
                    Outcome.COLLECT_ALL_REPORT,
                    f"collect-all: all phases ran; {len(collected)} finding(s) "
                    "recorded — instrument report, not a clean pass",
                    rework=rework_used,
                    findings=collected,
                )

            # Code-phase 5/5 is not Store-green. When a factory coder key is
            # configured, open a pilot cycle on the same workspace instead of
            # writing SUCCESS and parking the Floor on a thin prototype.
            if self.cycle != "pilot" and self._should_auto_open_pilot():
                self._open_auto_pilot()
                deadline = self._deadline
                # The rework budget is per BUILD: a pilot cycle does not refill it.
                rework_used = self._rework_rounds_this_build()
                work_list = ()
                done = self.ledger.completed_roles()
                if self._should_reopen_writer_for_cli() or BuildRole.WRITER not in done:
                    if self._should_reopen_writer_for_cli():
                        self._cli_writer_reopened = True
                        done.discard(BuildRole.WRITER)
                        self.ledger.append(
                            EventKind.NOTE,
                            role=BuildRole.WRITER,
                            detail=(
                                "reopening WRITER — DeepSeek FACTORY_CODE_CLI is "
                                "ready but C-BRIEF was unused on a store-complete "
                                "REUSE/COMPOSE inventory"
                            ),
                            payload={"cli_reopen": True, "stage": "dispatch"},
                        )
                    index = BUILD_PHASES.index(BuildRole.WRITER)
                else:
                    index = BUILD_PHASES.index(BuildRole.TESTER)
                continue
            break

        # THE VERDICT LINE (owner's ruling 1, 2026-09-01).
        #
        # "all phase gates passed" was a claim nobody could check. The
        # code-phase suite runs ``pytest -m "not pilot"`` and
        # ``@pytest.mark.pilot`` is the marker on the only tests that
        # exercise a business action -- so the sentence meant "everything
        # except the tests that check the product works passed", and
        # residential-lettings shipped a booting 216-file zip that could not
        # persist one record while every gate was green.
        #
        # Three gates now, each named WITH ITS SCOPE, and a gate that did not
        # run says so rather than being folded into a pass.
        # The gates themselves ran as phases; this sentence only has to stop
        # overclaiming what they covered. PRODUCT is the pilot cycle's TESTER
        # gate (see gates.gate_tester_contract), so reaching here on the
        # pilot cycle means it passed -- and reaching here on the code cycle
        # means it never ran, which must be said rather than implied.
        from app.factory.build.product_gate import GATE_SCOPES

        code_line = "CODE PASS — %s" % GATE_SCOPES["CODE"]
        if self.cycle != "pilot":
            return self._finish(
                Outcome.SUCCESS,
                "; ".join((
                    code_line,
                    "PRODUCT NOT RUN — %s" % GATE_SCOPES["PRODUCT"],
                    "STORE NOT RUN — %s" % GATE_SCOPES["STORE"],
                )),
                rework=rework_used,
            )
        return self._finish(
            Outcome.SUCCESS,
            "; ".join((
                code_line,
                "PRODUCT PASS — %s" % GATE_SCOPES["PRODUCT"],
                "STORE PASS — %s" % GATE_SCOPES["STORE"],
            )),
            rework=rework_used,
        )
