"""R2 (Advisor 12:13Z, spec section K, item K-3): the writer repairs a
misbehaving Store block UNDER AN EXAM generated from the block's own declared
contract plus the failing case.

K-2 (``store_repair``) settles a verdict made only of Store defects by calling
a ``repairer(block_id, defects) -> RepairResult``. This module builds that
repairer. Every rule is read from the block's ``block.json`` and from the
defect the grader recorded; nothing names a block, product or value.

* The exam (E1, generated first). SEALED cases from the declared contract:
  a call with every required input (and each declared option) must be
  accepted; a call missing one required input must be refused (never a silent
  guess); a value outside an input's declared options must be refused. Each
  Store defect adds its failing case: with a failing input, that input must
  now be accepted; with none (a finding in a file), the check that found it
  must no longer find it there.
* F-1 (grader 21:50Z): the exam requires the block to WORK, not just to
  validate. Every call the contract accepts must return each declared output,
  of its declared type and not empty (a stub that validates and returns
  nothing fails). The contract's acceptance list widens the refusal vocabulary
  (a declared acceptance status is never an acceptance), and an acceptance
  entry that declares an input is a sealed refusal case. A no-regression case
  per vendored file: the file and its exported (public top-level) symbols
  survive, so deleting the offending file fails. Each failing input is joined
  in the sealed half by variants the writer never sees (the failing input
  with each declared option, each optional input, and over the base call), so
  special-casing the failing input fails.
* The exam is tested (E3) before it is used: it must pass a reference built
  from the declared contract and fail every wrong variant the contract can
  tell apart (a silent default, an undeclared option accepted, a block that
  refuses everything). An exam that cannot is the Factory's defect: no writer
  round is run and the repair fails, naming it.
* Working and sealed are apart (E2/E4): the writer is handed the block's
  declared contract, the staged source and each defect's failing input and
  kind. It is never handed a case or an expected outcome; a failed case is
  reported by its input and the kind of failure only.
* The writer works on a staging copy ``repair/<id>/``, never on
  ``vendor/blocks/<id>/``; a repair that touched the vendored copy fails.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.factory.build.store_override import VENDOR_BLOCKS, StoreDefect, tree_hash
from app.factory.build.store_repair import RepairResult

#: Where the writer's repair of a block is staged inside the product.
REPAIR_DIR = "repair"
#: The block's declared contract, inside its vendored copy.
CONTRACT_FILE = "block.json"

ACCEPTED = "accepted"
REFUSED = "refused"
#: An outcome mapping whose status is one of these is a refusal.
REFUSAL_STATUSES = frozenset({"error", "refused", "failed"})

RUNTIME = "runtime"
STATIC = "static"
SURVIVE = "survive"

#: Envelope keys of a block result; what is left is the block's output.
ENVELOPE_KEYS = frozenset({"status", "ok"})

#: A typed sample per declared input type, used when nothing else is declared.
_TYPE_SAMPLES: Mapping[str, Any] = {
    "string": "sample",
    "number": 1,
    "float": 1.0,
    "integer": 1,
    "int": 1,
    "boolean": True,
    "bool": True,
    "json": {},
    "object": {},
    "dict": {},
    "array": [],
    "list": [],
}

#: A non-empty typed value per declared output type (the contract reference).
_OUTPUT_SAMPLES: Mapping[str, Any] = {
    "string": "sample",
    "number": 1,
    "float": 1.0,
    "integer": 1,
    "int": 1,
    "boolean": True,
    "bool": True,
    "array": [1],
    "list": [1],
}
#: Python types an output of a declared type must have (others: any value).
_OUTPUT_TYPES: Mapping[str, Tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "float": (int, float),
    "integer": (int,),
    "int": (int,),
    "boolean": (bool,),
    "bool": (bool,),
    "array": (list, tuple),
    "list": (list, tuple),
    "object": (dict,),
    "dict": (dict,),
}

Invoke = Callable[[Mapping[str, Any]], Any]
#: ``rescan(block_dir) -> findings`` with each finding's ``file`` relative to
#: ``block_dir`` -- the same check that found the defect, run on the repair.
Rescan = Callable[[Path], Iterable[Mapping[str, Any]]]
#: ``writer(block_id, staging_dir, handback) -> None``: the writer's repair.
Writer = Callable[[str, Path, Dict[str, Any]], Any]
#: ``{file relative to the block: its exported symbols}``.
Surface = Mapping[str, Sequence[str]]


@dataclass(frozen=True)
class ExamCase:
    """One sealed case. ``expect`` never leaves the exam."""

    case_id: str
    source: str
    check: str
    inputs: Any
    expect: str
    kind: str = ""


@dataclass(frozen=True)
class Exam:
    exam_id: str
    block_id: str
    working: Dict[str, Any]
    sealed: Tuple[ExamCase, ...]


@dataclass(frozen=True)
class SelfTest:
    ok: bool
    caught: Dict[str, bool] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True)
class CaseFailure:
    """What a failed case may tell the writer: its input and the failure kind."""

    case_id: str
    inputs: Any
    failure: str


@dataclass(frozen=True)
class ExamRun:
    passed: bool
    score: float
    failures: Tuple[CaseFailure, ...] = ()


def _declared_inputs(contract: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [i for i in (contract.get("inputs") or []) if isinstance(i, Mapping) and i.get("name")]


def _sample(spec: Mapping[str, Any]) -> Any:
    if spec.get("options"):
        return list(spec["options"])[0]
    if "default" in spec:
        return spec["default"]
    return _TYPE_SAMPLES.get(str(spec.get("type") or "").lower(), "sample")


def _undeclared(options: Sequence[Any]) -> str:
    """A value no declared option equals (longer than every one of them)."""
    return "undeclared:" + "|".join(str(o) for o in options)


def _base_call(inputs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    return {str(i["name"]): _sample(i) for i in inputs if i.get("required") is True}


def _contract_cases(contract: Mapping[str, Any]) -> List[ExamCase]:
    inputs = _declared_inputs(contract)
    base = _base_call(inputs)
    cases = [ExamCase("contract:base", "contract", RUNTIME, dict(base), ACCEPTED)]
    for spec in inputs:
        name = str(spec["name"])
        if spec.get("required") is True:
            call = {k: v for k, v in base.items() if k != name}
            cases.append(ExamCase(f"contract:missing:{name}", "contract", RUNTIME, call, REFUSED))
        options = list(spec.get("options") or [])
        for n, option in enumerate(options):
            call = {**base, name: option}
            cases.append(ExamCase(f"contract:option:{name}:{n}", "contract", RUNTIME, call, ACCEPTED))
        if options:
            call = {**base, name: _undeclared(options)}
            cases.append(ExamCase(f"contract:undeclared:{name}", "contract", RUNTIME, call, REFUSED))
    for n, entry in enumerate(_acceptance(contract)):
        if isinstance(entry.get("input"), Mapping):
            call = {**base, **entry["input"]}
            cases.append(ExamCase(f"acceptance:{n}", "acceptance", RUNTIME, call, REFUSED))
    return cases


def _acceptance(contract: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    return [a for a in (contract.get("acceptance") or []) if isinstance(a, Mapping)]


def refusal_statuses(contract: Mapping[str, Any]) -> frozenset:
    """The generic refusal statuses plus every status the contract's
    acceptance list declares (a declared acceptance outcome is not success)."""
    declared = {str(a.get("status")).lower() for a in _acceptance(contract) if a.get("status")}
    return REFUSAL_STATUSES | declared


def _variants(call: Mapping[str, Any], contract: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Sealed siblings of a failing input that the writer never sees: over the
    base call, with each declared option, with each optional input added."""
    inputs = _declared_inputs(contract)
    out: List[Dict[str, Any]] = [{**_base_call(inputs), **call}]
    for spec in inputs:
        name = str(spec["name"])
        for option in spec.get("options") or []:
            out.append({**call, name: option})
        if spec.get("required") is not True and name not in call:
            out.append({**call, name: _sample(spec)})
    seen: List[Dict[str, Any]] = [dict(call)]
    for variant in out:
        if variant not in seen:
            seen.append(variant)
    return seen[1:]


def _inside_block(defect: StoreDefect) -> str:
    """The defect's file path relative to its block's directory."""
    parts = defect.path.split("/")
    return "/".join(parts[len(VENDOR_BLOCKS) + 1 :])


def _defect_cases(defects: Sequence[StoreDefect], contract: Mapping[str, Any]) -> List[ExamCase]:
    cases: List[ExamCase] = []
    for n, defect in enumerate(defects):
        if defect.failing_input is None:
            cases.append(
                ExamCase(f"defect:{n}", "defect", STATIC, _inside_block(defect), ACCEPTED, defect.kind)
            )
            continue
        call = defect.failing_input if isinstance(defect.failing_input, Mapping) else {"input": defect.failing_input}
        cases.append(ExamCase(f"defect:{n}", "defect", RUNTIME, dict(call), ACCEPTED, defect.kind))
        for k, variant in enumerate(_variants(call, contract)):
            cases.append(ExamCase(f"defect:{n}:variant:{k}", "defect", RUNTIME, variant, ACCEPTED, defect.kind))
    return cases


def _survive_cases(surface: Surface, defects: Sequence[StoreDefect]) -> List[ExamCase]:
    """No regression: every vendored file (and every file a static defect
    names) survives with its exported symbols."""
    files: Dict[str, List[str]] = {str(f): sorted(set(s)) for f, s in surface.items()}
    for defect in defects:
        if defect.failing_input is None:
            files.setdefault(_inside_block(defect), [])
    return [
        ExamCase(f"survive:{f}", "regression", SURVIVE, {"file": f, "symbols": files[f]}, ACCEPTED)
        for f in sorted(files)
    ]


def _exported(source: str) -> List[str]:
    """Public top-level names a Python file defines (none if unparseable)."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return sorted({n for n in names if not n.startswith("_")})


def block_surface(block_dir: Path) -> Dict[str, List[str]]:
    """``{file: exported symbols}`` of a block tree (bytecode caches skipped)."""
    out: Dict[str, List[str]] = {}
    for path in sorted(block_dir.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8", errors="replace") if path.suffix == ".py" else ""
        out[path.relative_to(block_dir).as_posix()] = _exported(source)
    return out


def working_contract(contract: Mapping[str, Any]) -> Dict[str, Any]:
    """The part of the contract the writer is given (the WORKING half)."""
    return {key: contract.get(key) for key in ("id", "inputs", "outputs", "acceptance") if key in contract}


def generate_exam(
    block_id: str,
    contract: Mapping[str, Any],
    defects: Sequence[StoreDefect],
    surface: Optional[Surface] = None,
) -> Exam:
    """E1: the block's exam, from its declared contract, the failing cases and
    the vendored block's surface (its files and exported symbols)."""
    sealed = tuple(
        _contract_cases(contract) + _defect_cases(defects, contract) + _survive_cases(surface or {}, defects)
    )
    body = json.dumps(
        [[c.case_id, c.check, c.inputs, c.expect, c.kind] for c in sealed], sort_keys=True, default=str
    )
    exam_id = "exam:" + block_id + ":" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return Exam(exam_id, block_id, working_contract(contract), sealed)


def call_block(
    invoke: Invoke, inputs: Mapping[str, Any], refusals: Iterable[str] = REFUSAL_STATUSES
) -> Tuple[str, Any]:
    """``(ACCEPTED or REFUSED, result)``: a raise, a refusal status or
    ``ok: false`` is a refusal."""
    try:
        result = invoke(dict(inputs))
    except Exception:  # noqa: BLE001 -- any raise is the block refusing the call
        return REFUSED, None
    if isinstance(result, Mapping):
        if str(result.get("status") or "").lower() in set(refusals) or result.get("ok") is False:
            return REFUSED, result
    return ACCEPTED, result


def outcome(invoke: Invoke, inputs: Mapping[str, Any], refusals: Iterable[str] = REFUSAL_STATUSES) -> str:
    """ACCEPTED or REFUSED (see :func:`call_block`)."""
    return call_block(invoke, inputs, refusals)[0]


def _empty(value: Any) -> bool:
    return value is None or (isinstance(value, (str, bytes, list, tuple, dict, set)) and not value)


def output_failure(outputs: Sequence[Mapping[str, Any]], result: Any) -> Optional[str]:
    """Why an accepted call does not deliver the declared outputs, or None.
    The output is the result minus its envelope keys; a lone declared output
    may be the whole output."""
    payload = {k: v for k, v in result.items() if k not in ENVELOPE_KEYS} if isinstance(result, Mapping) else result
    if _empty(payload):
        return "accepted with an empty result"
    declared = [o for o in outputs if isinstance(o, Mapping) and o.get("name")]
    for spec in declared:
        name = str(spec["name"])
        if isinstance(payload, Mapping) and name in payload:
            value = payload[name]
        elif len(declared) == 1:
            value = payload
        else:
            return f"missing output: {name}"
        if _empty(value):
            return f"empty output: {name}"
        kind = str(spec.get("type") or "").lower()
        want = _OUTPUT_TYPES.get(kind)
        if want is not None and (not isinstance(value, want) or (isinstance(value, bool) and bool not in want)):
            return f"output {name} is not {kind}"
    return None


def _output_sample(spec: Mapping[str, Any]) -> Any:
    return _OUTPUT_SAMPLES.get(str(spec.get("type") or "").lower(), {"sample": 1})


def contract_reference(contract: Mapping[str, Any], *, mutant: str = "") -> Invoke:
    """A block that does exactly what the contract declares, or one wrong
    variant of it (``silent_default``, ``accept_undeclared``, ``refuse_all``,
    ``empty_result``: validates every input and returns nothing)."""
    inputs = _declared_inputs(contract)
    outputs = [o for o in (contract.get("outputs") or []) if isinstance(o, Mapping) and o.get("name")]
    produced = {str(o["name"]): _output_sample(o) for o in outputs} or {"result": {"sample": 1}}

    def invoke(call: Mapping[str, Any]) -> Dict[str, Any]:
        if mutant == "refuse_all":
            return {"status": "error"}
        for spec in inputs:
            name = str(spec["name"])
            if spec.get("required") is True and name not in call and mutant != "silent_default":
                return {"status": "error"}
            options = list(spec.get("options") or [])
            if options and name in call and call[name] not in options and mutant != "accept_undeclared":
                return {"status": "error"}
        if mutant == "empty_result":
            return {"status": "success"}
        return {"status": "success", **produced}

    return invoke


def mutants_of(contract: Mapping[str, Any]) -> Tuple[str, ...]:
    """The wrong variants this contract can tell apart from the reference."""
    inputs = _declared_inputs(contract)
    out = ["refuse_all", "empty_result"]
    if any(i.get("required") is True for i in inputs):
        out.append("silent_default")
    if any(i.get("options") for i in inputs):
        out.append("accept_undeclared")
    return tuple(out)


def _runtime_contract(exam: Exam) -> List[ExamCase]:
    return [c for c in exam.sealed if c.source == "contract" and c.check == RUNTIME]


def self_test(exam: Exam, contract: Mapping[str, Any]) -> SelfTest:
    """E3: the exam passes the contract reference and fails every mutant."""
    cases = _runtime_contract(exam)
    reference = contract_reference(contract)
    for case in cases:
        if _runtime_failure(case, reference, exam) is not None:
            return SelfTest(False, reason=f"the contract reference fails {case.case_id}")
    caught: Dict[str, bool] = {}
    for mutant in mutants_of(contract):
        wrong = contract_reference(contract, mutant=mutant)
        caught[mutant] = any(_runtime_failure(c, wrong, exam) is not None for c in cases)
    missed = sorted(m for m, hit in caught.items() if not hit)
    if missed:
        return SelfTest(False, caught, "the exam cannot tell apart: " + ", ".join(missed))
    return SelfTest(True, caught)


def _static_failure(case: ExamCase, findings: Iterable[Mapping[str, Any]]) -> Optional[str]:
    for finding in findings:
        if str(finding.get("file") or "").replace("\\", "/") != case.inputs:
            continue
        if str(finding.get("kind") or "unknown") == case.kind:
            return f"still found: {case.kind}"
    return None


def _survive_failure(case: ExamCase, staged: Surface) -> Optional[str]:
    path = case.inputs["file"]
    if path not in staged:
        return f"file removed: {path}"
    lost = sorted(set(case.inputs["symbols"]) - set(staged[path]))
    return f"exported symbols removed from {path}: {', '.join(lost)}" if lost else None


def _runtime_failure(case: ExamCase, invoke: Invoke, exam: Exam) -> Optional[str]:
    got, result = call_block(invoke, case.inputs, refusal_statuses(exam.working))
    if got == case.expect:
        return output_failure(exam.working.get("outputs") or [], result) if got == ACCEPTED else None
    if case.source == "defect":
        return f"still fails: {case.kind}"
    return "refused a call the contract accepts" if got == REFUSED else "accepted a call the contract refuses"


def run_exam(
    exam: Exam,
    invoke: Invoke,
    rescan: Callable[[], Iterable[Mapping[str, Any]]],
    surface: Optional[Callable[[], Surface]] = None,
) -> ExamRun:
    """Run every sealed case; the result names failures by input and kind
    only. ``surface()`` is the repaired tree's surface (none given: no file
    survives)."""
    findings = [f for f in (rescan() or []) if isinstance(f, Mapping)]
    staged = surface() if surface is not None else {}
    failures: List[CaseFailure] = []
    for case in exam.sealed:
        if case.check == STATIC:
            why = _static_failure(case, findings)
        elif case.check == SURVIVE:
            why = _survive_failure(case, staged)
        else:
            why = _runtime_failure(case, invoke, exam)
        if why is not None:
            failures.append(CaseFailure(case.case_id, case.inputs, why))
    total = len(exam.sealed)
    score = (total - len(failures)) / total if total else 0.0
    return ExamRun(bool(total) and not failures, score, tuple(failures))


def handback(exam: Exam, defects: Sequence[StoreDefect], staging: Path) -> Dict[str, Any]:
    """What the writer is handed (E2/E4): contract, source, failing input and
    kind. No sealed case and no expected outcome."""
    return {
        "block_id": exam.block_id,
        "exam_id": exam.exam_id,
        "contract": exam.working,
        "source_dir": str(staging),
        "defects": [{"path": d.path, "kind": d.kind, "failing_input": d.failing_input} for d in defects],
    }


def make_repairer(
    workspace: Path | str,
    *,
    writer: Writer,
    invoker: Callable[[Path], Invoke],
    rescan: Rescan,
) -> Callable[[str, Tuple[StoreDefect, ...]], RepairResult]:
    """R2: the ``repairer`` K-2's :func:`store_repair.settle` calls."""
    root = Path(workspace)

    def repair(block_id: str, defects: Tuple[StoreDefect, ...]) -> RepairResult:
        block_dir = root.joinpath(*VENDOR_BLOCKS, block_id)
        contract_path = block_dir / CONTRACT_FILE
        if not contract_path.is_file():
            return RepairResult(block_id, False, detail="no declared contract: the exam cannot be generated")
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        exam = generate_exam(block_id, contract, defects, block_surface(block_dir))
        checked = self_test(exam, contract)
        if not checked.ok:
            return RepairResult(block_id, False, exam.exam_id, detail=f"exam defect (Factory): {checked.reason}")
        staging = root / REPAIR_DIR / block_id
        if staging.exists():
            shutil.rmtree(staging)
        shutil.copytree(block_dir, staging, ignore=shutil.ignore_patterns("__pycache__"))
        vendored = tree_hash(block_dir)
        writer(block_id, staging, handback(exam, defects, staging))
        if tree_hash(block_dir) != vendored:
            return RepairResult(block_id, False, exam.exam_id, detail="the repair changed the vendored copy")
        run = run_exam(exam, invoker(staging), lambda: rescan(staging), lambda: block_surface(staging))
        detail = "; ".join(f"{f.case_id}: {f.failure}" for f in run.failures)
        return RepairResult(block_id, run.passed, exam.exam_id, run.score, detail)

    return repair
