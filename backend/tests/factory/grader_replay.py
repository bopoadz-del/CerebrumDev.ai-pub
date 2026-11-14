"""FG-3: the grader replay harness -- a grader change provable by pytest.

The tree the grader sees on a live build is composed here at test time by the
Factory's OWN code, never a committed copy and never a real product, in the
order a build writes it:

1. ``runner``   -- what ``RoleRunner.__init__`` writes into a workspace;
2. ``vendored`` -- the CLONER's vendoring of the plan's blocks from the
   pinned Store checkout (``run_cloner``, the build's own ``blocks.lock.json``);
3. ``stamped``  -- the Factory's pre-writer stamping (``factory_owned.prestamp``)
   and then every stamp in the registry that carries an ``apply``;
4. ``writer``   -- an INVENTED writer layer: one handler per planned
   capability that computes from its declared inputs, and the models that
   declare those inputs (``grader_contract``).

Each step's digest diff records which layer wrote each path -- the test's own
ground truth, independent of anything the grader computes.

The tree is graded through the runner's real call (``RoleRunner._writer_grader``)
with the grader switch ON and the state / prompt shape the runner itself
builds. Layer 2 replays from a :class:`Cassette` keyed on question id (a miss
is a question the grader did not answer -- never a silent pass); the UI layer
is fed a recorded report or the not-run shape. Nothing here drives a build.

Nothing in this file names a product, a capability, a block, a file the
Factory stamps or a literal the grader is expected to report: every one of
those is read from the Factory's own code at run time.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from tests.factory.grader_contract import COMPUTES, MODEL_CLASS

REPO = Path(__file__).resolve().parents[3]
#: Invented example blueprints that ship with the repo. The harness reads the
#: capability list, the blocks and the brief from the blueprint at run time.
BLUEPRINTS = tuple(sorted((REPO / "blueprints" / "examples").glob("*.yaml")))

RUNNER = "runner"
VENDORED = "vendored"
STAMPED = "stamped"
WRITER = "writer"
FACTORY_LAYERS = (RUNNER, VENDORED, STAMPED)


def _snapshot(root: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _record(layers: Dict[str, str], before: Mapping[str, str], after: Mapping[str, str], layer: str) -> None:
    for rel, digest in after.items():
        if before.get(rel) != digest:
            layers[rel] = layer
    for rel in before:
        if rel not in after:
            layers.pop(rel, None)


def blueprints_with_enough_capabilities(minimum: int) -> List[Path]:
    """The example blueprints with at least ``minimum`` capabilities -- the
    function grade's own floor, read from the grader at run time."""
    from app.factory.blueprint import load_blueprint

    return [p for p in BLUEPRINTS if len(load_blueprint(p).capabilities) >= minimum]


#: The two blueprint cases every harness test runs on: an example blueprint
#: that meets the function floor on its own, and one COMBINED at test time
#: from the example blueprints that do not (a different vertical, different
#: blocks). Both are read from the repo's invented examples at run time.
MEETS_FLOOR = "meets_floor"
COMBINED = "combined"
CASES = (MEETS_FLOOR, COMBINED)


def blueprint_for(case: str, scratch: Path) -> Path:
    """The blueprint file for a harness case (written under ``scratch`` when
    it is composed at test time)."""
    import yaml

    from app.factory.blueprint import load_blueprint

    floor = function_floor()
    if case == MEETS_FLOOR:
        return blueprints_with_enough_capabilities(floor)[0]
    below = [p for p in BLUEPRINTS if len(load_blueprint(p).capabilities) < floor]
    docs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in below]
    combined = dict(docs[0])
    seen: Dict[str, None] = {}
    caps: List[Any] = []
    for doc in docs:
        for cap in doc.get("capabilities") or []:
            if cap.get("id") not in seen:
                seen[cap.get("id")] = None
                caps.append(cap)
    combined["capabilities"] = caps
    assert len(caps) >= floor, "the combined example blueprints no longer reach the function floor"
    out = Path(scratch) / "combined_blueprint.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(yaml.safe_dump(combined, sort_keys=False), encoding="utf-8")
    return out


def compose_case(case: str, scratch: Path, store: Path) -> Composed:
    return compose(blueprint_for(case, scratch), Path(scratch) / "tree", store)


def function_floor() -> int:
    from app.factory.build.product_grader import function_grader

    return int(function_grader.MIN_FUNCTIONS)


@dataclass
class Composed:
    """A composed product tree and who wrote each path."""

    blueprint_path: Path
    root: Path
    store: Path
    layers: Dict[str, str]
    capability_ids: List[str]

    def layer_of(self, rel: str) -> Optional[str]:
        return self.layers.get(str(rel).replace("\\", "/"))

    def paths(self, layer: str) -> List[str]:
        return sorted(rel for rel, owner in self.layers.items() if owner == layer)

    def copy_to(self, dest: Path) -> "Composed":
        """An independent copy (a grade writes its record into the tree)."""
        shutil.copytree(self.root, dest)
        return Composed(self.blueprint_path, dest, self.store, dict(self.layers), list(self.capability_ids))

    def write_writer_file(self, rel: str, text: str) -> None:
        """Plant a file in the writer layer (a defect or a control)."""
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.layers[str(rel).replace("\\", "/")] = WRITER


def handler_rel(capability_id: str) -> str:
    """Where the function grade imports a capability's handler from."""
    return f"app/actions/{capability_id.replace('-', '_')}.py"


def _runner(blueprint_path: Path, root: Path, store: Path) -> Any:
    from app.factory.blueprint import load_blueprint
    from app.factory.build.runner import RoleRunner

    return RoleRunner(load_blueprint(blueprint_path), root, blocks_root=store, store_root=store, roles={})


def _role_ctx(runner: Any, role: Any) -> Any:
    from app.factory.build.roles import RoleContext
    from app.factory.build.workspace import RoleWorkspace

    return RoleContext(
        role=role,
        workspace=RoleWorkspace(role, runner.workspace),
        blueprint=runner.blueprint,
        plan=runner.plan,
        blocks_root=runner.blocks_root,
        blocks_lock=getattr(runner, "blocks_lock", None),
        state=runner.state,
    )


def compose(blueprint_path: Path, root: Path, store: Path) -> Composed:
    """Compose the three-layer tree a live build hands the grader."""
    from app.factory.build import factory_owned
    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import run_cloner
    from app.factory.build.stamp_registry import stamps

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    layers: Dict[str, str] = {}

    before = _snapshot(root)
    runner = _runner(blueprint_path, root, store)
    after = _snapshot(root)
    _record(layers, before, after, RUNNER)

    # The CLONER vendors exactly the blocks the COLLECTOR resolves: every
    # planned capability's block ids, first seen first.
    resolved: Dict[str, None] = {}
    for cap in runner.plan.capabilities:
        for bid in cap.block_ids:
            resolved.setdefault(bid, None)
    runner.state["resolved_blocks"] = tuple(resolved)
    before = after
    result = run_cloner(_role_ctx(runner, BuildRole.CLONER))
    assert result.ok, f"CLONER refused the composed tree: {result.detail}"
    after = _snapshot(root)
    _record(layers, before, after, VENDORED)

    before = after
    stamp_ctx = _role_ctx(runner, BuildRole.WRITER)
    factory_owned.prestamp(root, runner.blueprint, stamp_ctx)
    for stamp in stamps():
        if stamp.apply is not None:
            stamp.apply(root)
    after = _snapshot(root)
    _record(layers, before, after, STAMPED)

    capability_ids = [c.capability_id for c in runner.plan.capabilities]
    before = after
    for package in ("app/__init__.py", "app/actions/__init__.py"):
        if not (root / package).is_file():
            (root / package).parent.mkdir(parents=True, exist_ok=True)
            (root / package).write_text("", encoding="utf-8")
    for cid in capability_ids:
        path = root / handler_rel(cid)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(COMPUTES, encoding="utf-8")
    (root / "app" / "models.py").write_text(writer_models_source(capability_ids), encoding="utf-8")
    _write_writer_persistence(root)
    after = _snapshot(root)
    _record(layers, before, after, WRITER)
    return Composed(Path(blueprint_path), root, Path(store), layers, capability_ids)


def writer_models_source(capability_ids: Sequence[str]) -> str:
    """``app/models.py`` for the invented writer layer: one declared entity
    per capability (the grader_contract's typed fields and bound), each in
    its own table, keyed by the capability's module name."""
    parts: List[str] = []
    registry: List[str] = []
    for n, cid in enumerate(capability_ids):
        name = cid.replace("-", "_")
        cls = f"Entry{n}"
        parts.append(
            MODEL_CLASS.replace("class Entry:", f"class {cls}:").replace(
                "ENTITY = 'entry'", f"ENTITY = {name + '_entry'!r}"
            )
        )
        registry.append(f"{name!r}: {cls}")
    return "\n".join(parts) + "\nMODELS = {" + ", ".join(registry) + "}\n"


def _write_writer_persistence(root: Path) -> None:
    """The two persistence files the Factory's substrate leaves for the writer
    to author (they carry the entity schema): rendered here from the writer
    layer's OWN declared models, as a writer that follows the contract would."""
    from app.factory.build.data_lifecycle import REVISION_0001, render_revision_0001, render_store
    from app.factory.build.declared_specs import specs_from_product_models

    specs = specs_from_product_models(root)
    (root / "app" / "store.py").write_text(render_store(specs), encoding="utf-8")
    revision = root / "alembic" / "versions" / f"{REVISION_0001}.py"
    revision.parent.mkdir(parents=True, exist_ok=True)
    revision.write_text(render_revision_0001(specs), encoding="utf-8")


# --- Layer 2 cassette ---------------------------------------------------------


@dataclass
class Cassette:
    """Layer 2 replayed by question id.

    ``answers`` maps a question id to the recorded answer (``verdict`` and
    ``evidence``). A question whose id is not in the cassette gets no answer
    at all -- the grader records it as a question it was not answered, never
    as a pass. Every request is kept with its hash so a test can say what the
    grader asked and what it was shown."""

    answers: Mapping[str, Mapping[str, Any]]
    requests: List[Dict[str, Any]] = field(default_factory=list)
    misses: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "Cassette":
        return cls(json.loads(Path(path).read_text(encoding="utf-8"))["answers"])

    def complete(self, messages: Sequence[Mapping[str, str]]) -> str:
        body = json.loads(messages[-1]["content"])
        digest = hashlib.sha256(json.dumps(list(messages), sort_keys=True).encode("utf-8")).hexdigest()
        self.requests.append({"hash": digest, "body": body})
        out = []
        for q in body.get("questions") or []:
            qid = str(q.get("id") or "")
            recorded = self.answers.get(qid)
            if recorded is None:
                self.misses.append(qid)
                continue
            out.append({"id": qid, "verdict": recorded.get("verdict"), "evidence": recorded.get("evidence")})
        return json.dumps(out)


CASSETTE = Path(__file__).with_name("grader_replay_cassette.json")


# --- the UI layer ---------------------------------------------------------------


class RecordedUi:
    """A recorded UI report replayed into the grade (no browser, no boot)."""

    def __init__(self, report: Mapping[str, Any]):
        self._report = dict(report)

    @property
    def ran(self) -> bool:
        return bool(self._report.get("ran"))

    @property
    def failing(self) -> List[str]:
        return list(self._report.get("failing") or [])

    @property
    def errors(self) -> List[str]:
        return list(self._report.get("errors") or [])

    @property
    def ok(self) -> bool:
        return self.ran and not self.failing

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "ran": self.ran, "failing": self.failing, "steps": list(self._report.get("steps") or [])}


#: A UI walk that ran and found nothing failing.
UI_GREEN = {"ran": True, "failing": [], "steps": []}


def ui_from_report(report: Optional[Mapping[str, Any]], not_run_reason: str = "") -> Callable[..., Any]:
    """A stand-in for ``grader._grade_ui``: the recorded report, or -- when
    ``report`` is None -- the grader's own not-run shape with the reason."""
    from app.factory.build import grader

    def grade_ui(blueprint, root, gradroot, base_url):
        if report is None:
            reason = not_run_reason or "UI run did not happen"
            return grader._UiNotRun(reason), False, reason
        ui = RecordedUi(report)
        return ui, ui.ran, "" if ui.ran else (not_run_reason or "UI run did not happen")

    return grade_ui


# --- grading through the runner's own call ----------------------------------------


@dataclass
class Replay:
    composed: Composed
    verdict: Any  # None when the runner's call returns no verdict (CERTIFIED)
    grade: Mapping[str, Any]
    notes: List[Mapping[str, Any]]
    cassette: Cassette

    @property
    def certified(self) -> bool:
        return self.verdict is None

    def finding_files(self) -> List[str]:
        """Every file a hardwiring finding names, from the grade record."""
        hard = self.grade.get("hardwiring") or {}
        return sorted(
            {str(f.get("file") or "") for f in hard.get("findings") or [] if isinstance(f, Mapping) and f.get("file")}
        )

    def items(self) -> List[str]:
        return list(getattr(self.verdict, "items", ()) or ())


class _Ledger:
    """Records the NOTEs the runner's grader call appends."""

    def __init__(self) -> None:
        self.notes: List[Mapping[str, Any]] = []

    def append(self, kind, role=None, detail="", payload=None):
        self.notes.append({"kind": kind, "detail": detail, "payload": dict(payload or {})})


def grade(
    composed: Composed,
    monkeypatch: Any,
    *,
    cassette: Optional[Cassette] = None,
    ui_report: Optional[Mapping[str, Any]] = UI_GREEN,
    ui_not_run_reason: str = "",
) -> Replay:
    """Grade the composed tree through ``RoleRunner._writer_grader``.

    The grader switch is ON (the suite turns it off session-wide), Layer 2
    runs under an independent identity against the cassette, and the UI layer
    is the recorded report (or the not-run shape when ``ui_report`` is None).
    """
    from app.factory.build import grader
    from app.factory.build.product_grader import grade_writer

    tape = cassette if cassette is not None else Cassette.load(CASSETTE)
    monkeypatch.setenv(grader.DISABLE_ENV, "1")
    # An independent grader identity (provider and model differ from the
    # writer's); the cassette stands in for its network call.
    monkeypatch.setenv(grader.GRADER_LLM_KEY_ENV, "replay-key")
    monkeypatch.setenv(grader.GRADER_LLM_BASE_URL_ENV, "https://grader.replay.invalid/v1")
    monkeypatch.setenv(grader.GRADER_LLM_MODEL_ENV, "replay-grader-model")
    monkeypatch.setattr(grader, "_grader_complete", tape.complete)
    monkeypatch.setattr(grader, "_grade_ui", ui_from_report(ui_report, ui_not_run_reason))

    runner = _runner(composed.blueprint_path, composed.root, composed.store)
    ledger = _Ledger()
    runner.ledger = ledger
    verdict = runner._writer_grader()
    record = composed.root / grade_writer.GRADE_JSON
    graded = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {}
    return Replay(composed, verdict, graded, ledger.notes, tape)
