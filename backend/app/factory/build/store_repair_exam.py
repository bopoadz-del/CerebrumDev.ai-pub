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

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.factory.build.store_override import (
    VENDOR_BLOCKS,
    ExamProof,
    StoreDefect,
    is_inside,
    tree_hash,
    write_proof,
)
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

Invoke = Callable[[Mapping[str, Any]], Any]
#: ``rescan(block_dir) -> findings`` with each finding's ``file`` relative to
#: ``block_dir`` -- the same check that found the defect, run on the repair.
Rescan = Callable[[Path], Iterable[Mapping[str, Any]]]
#: ``writer(block_id, staging_dir, handback) -> None``: the writer's repair.
Writer = Callable[[str, Path, Dict[str, Any]], Any]


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
    return cases


def _inside_block(defect: StoreDefect) -> str:
    """The defect's file path relative to its block's directory."""
    parts = defect.path.split("/")
    return "/".join(parts[len(VENDOR_BLOCKS) + 1 :])


def _defect_cases(defects: Sequence[StoreDefect]) -> List[ExamCase]:
    cases: List[ExamCase] = []
    for n, defect in enumerate(defects):
        if defect.failing_input is None:
            cases.append(
                ExamCase(f"defect:{n}", "defect", STATIC, _inside_block(defect), ACCEPTED, defect.kind)
            )
            continue
        call = defect.failing_input if isinstance(defect.failing_input, Mapping) else {"input": defect.failing_input}
        cases.append(ExamCase(f"defect:{n}", "defect", RUNTIME, dict(call), ACCEPTED, defect.kind))
    return cases


def working_contract(contract: Mapping[str, Any]) -> Dict[str, Any]:
    """The part of the contract the writer is given (the WORKING half)."""
    return {key: contract.get(key) for key in ("id", "inputs", "outputs", "acceptance") if key in contract}


def generate_exam(block_id: str, contract: Mapping[str, Any], defects: Sequence[StoreDefect]) -> Exam:
    """E1: the block's exam, from its declared contract plus the failing cases."""
    sealed = tuple(_contract_cases(contract) + _defect_cases(defects))
    body = json.dumps(
        [[c.case_id, c.check, c.inputs, c.expect, c.kind] for c in sealed], sort_keys=True, default=str
    )
    exam_id = "exam:" + block_id + ":" + hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    return Exam(exam_id, block_id, working_contract(contract), sealed)


def outcome(invoke: Invoke, inputs: Mapping[str, Any]) -> str:
    """ACCEPTED or REFUSED: a raise, an error status or ``ok: false`` is a refusal."""
    try:
        result = invoke(dict(inputs))
    except Exception:  # noqa: BLE001 -- any raise is the block refusing the call
        return REFUSED
    if isinstance(result, Mapping):
        if str(result.get("status") or "").lower() in REFUSAL_STATUSES or result.get("ok") is False:
            return REFUSED
    return ACCEPTED


def contract_reference(contract: Mapping[str, Any], *, mutant: str = "") -> Invoke:
    """A block that does exactly what the contract declares, or one wrong
    variant of it (``silent_default``, ``accept_undeclared``, ``refuse_all``)."""
    inputs = _declared_inputs(contract)

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
        return {"status": "success"}

    return invoke


def mutants_of(contract: Mapping[str, Any]) -> Tuple[str, ...]:
    """The wrong variants this contract can tell apart from the reference."""
    inputs = _declared_inputs(contract)
    out = ["refuse_all"]
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
        if outcome(reference, case.inputs) != case.expect:
            return SelfTest(False, reason=f"the contract reference fails {case.case_id}")
    caught: Dict[str, bool] = {}
    for mutant in mutants_of(contract):
        wrong = contract_reference(contract, mutant=mutant)
        caught[mutant] = any(outcome(wrong, c.inputs) != c.expect for c in cases)
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


def _runtime_failure(case: ExamCase, invoke: Invoke) -> Optional[str]:
    got = outcome(invoke, case.inputs)
    if got == case.expect:
        return None
    if case.source == "defect":
        return f"still fails: {case.kind}"
    return "refused a call the contract accepts" if got == REFUSED else "accepted a call the contract refuses"


def run_exam(exam: Exam, invoke: Invoke, rescan: Callable[[], Iterable[Mapping[str, Any]]]) -> ExamRun:
    """Run every sealed case; the result names failures by input and kind only."""
    findings = [f for f in (rescan() or []) if isinstance(f, Mapping)]
    failures: List[CaseFailure] = []
    for case in exam.sealed:
        if case.check == STATIC:
            why = _static_failure(case, findings)
        else:
            why = _runtime_failure(case, invoke)
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
    proofs_dir: Path | str | None = None,
) -> Callable[[str, Tuple[StoreDefect, ...]], RepairResult]:
    """R2: the ``repairer`` K-2's :func:`store_repair.settle` calls.

    F-2: with ``proofs_dir`` (a Factory-held directory outside the product),
    every exam run leaves its proof there: the exam id, the block, the hash of
    the tree it examined and the verdict. Acceptance trusts that proof, not the
    returned result. Without ``proofs_dir`` no proof is written, so no repair
    from this repairer can be accepted."""
    root = Path(workspace)
    if proofs_dir is not None and is_inside(proofs_dir, root):
        raise ValueError("exam proofs must be held outside the product tree")

    def repair(block_id: str, defects: Tuple[StoreDefect, ...]) -> RepairResult:
        block_dir = root.joinpath(*VENDOR_BLOCKS, block_id)
        contract_path = block_dir / CONTRACT_FILE
        if not contract_path.is_file():
            return RepairResult(block_id, False, detail="no declared contract: the exam cannot be generated")
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
        exam = generate_exam(block_id, contract, defects)
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
        run = run_exam(exam, invoker(staging), lambda: rescan(staging))
        if proofs_dir is not None:
            proof = ExamProof(exam.exam_id, block_id, tree_hash(staging), run.passed, run.score)
            write_proof(proofs_dir, proof)
        detail = "; ".join(f"{f.case_id}: {f.failure}" for f in run.failures)
        return RepairResult(block_id, run.passed, exam.exam_id, run.score, detail)

    return repair
