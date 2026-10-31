"""F-8a -- Layer-1 function grader.

For a built product workspace and its blueprint:

1. derive the functions to grade FROM the product: every blueprint
   capability whose ``app/actions/<capability_id>.py`` exposes ``handle``;
2. generate a two-input test for each from the capability's own definition
   (two different inputs must give two different results);
3. execute the standard mutants against that test -- ``constant_return``
   (handle answers one fixed result) and ``drop_rule`` (handle ignores its
   input) -- each mutant must redden the test.

A function whose test does not pass, or whose mutant survives, makes the
platform NOT handover-ready and is named in the grade. Fewer than
``MIN_FUNCTIONS`` gradeable functions is itself a FAIL.

Each function runs in a fresh subprocess with the workspace on sys.path, so
a product's imports never leak into the Factory process.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import secrets
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from app.factory.build.product_grader import vault

#: ORDERS_1710Z-B: "graded on at least 4-5 of its functions".
MIN_FUNCTIONS = 4
MUTANTS: Sequence[str] = ("constant_return", "drop_rule")
RUN_TIMEOUT_S = 60


@dataclass(frozen=True)
class GradedFunction:
    capability_id: str
    module: str
    inputs: List[Dict[str, Any]]


@dataclass
class FunctionResult:
    capability_id: str
    two_input_passed: bool
    mutants_killed: List[str] = field(default_factory=list)
    mutants_survived: List[str] = field(default_factory=list)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.two_input_passed and not self.mutants_survived


@dataclass
class FunctionGrade:
    functions: List[FunctionResult]
    min_functions: int = MIN_FUNCTIONS
    errors: List[str] = field(default_factory=list)
    #: F-8d writer-blind vault for this run: {seed, entries, gaps}. None when no
    #: grader root was given (the vault is a Factory-owned, grader-root artifact).
    vault: "Dict[str, Any] | None" = None

    def __post_init__(self) -> None:
        # MIN_FUNCTIONS is a floor, not a default: a caller can ask for more
        # gradeable functions, never fewer (0, negatives, bools, non-ints).
        m = self.min_functions
        if isinstance(m, bool) or not isinstance(m, int):
            raise TypeError(f"min_functions must be an int, got {type(m).__name__}")
        self.min_functions = max(m, MIN_FUNCTIONS)

    @property
    def ok(self) -> bool:
        return (
            not self.errors
            and len(self.functions) >= self.min_functions
            and all(f.ok for f in self.functions)
        )

    @property
    def failing(self) -> List[str]:
        names = [f.capability_id for f in self.functions if not f.ok]
        if len(self.functions) < self.min_functions:
            names.append(
                f"too few gradeable functions: {len(self.functions)} < {self.min_functions}"
            )
        return names + list(self.errors)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "min_functions": self.min_functions,
            "graded": len(self.functions),
            "failing": self.failing,
            "functions": [dict(asdict(f), ok=f.ok) for f in self.functions],
            **({"vault": self.vault} if self.vault is not None else {}),
        }


def _words(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z][a-z0-9]+", text.lower()) if len(w) > 2]


def two_inputs(capability: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Two distinct payloads built from the capability's own definition.

    Keys are the capability's own description words; values differ between
    the two inputs in every key (text and number), so a function that reads
    its input at all must answer them differently."""
    cid = str(capability.get("id") or "")
    words = list(dict.fromkeys(_words(str(capability.get("description") or ""))))[:6]
    if not words:
        words = _words(cid.replace("_", " ")) or ["value"]
    seed = int(hashlib.sha256(cid.encode("utf-8")).hexdigest()[:6], 16)
    out: List[Dict[str, Any]] = []
    for n in (1, 2):
        payload: Dict[str, Any] = {}
        for i, w in enumerate(words):
            payload[w] = f"{w}-{cid}-{n}" if i % 2 == 0 else (seed % 97) + n * (i + 1)
        payload["quantity"] = n * 7 + seed % 5
        payload["text"] = " ".join(words[: n + 1])
        out.append(payload)
    return out


def _defines_handle(text: str) -> bool:
    """A module-level ``def handle``. Unparseable source still counts: it is
    graded (and fails on import), never silently dropped."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return True
    return any(isinstance(n, ast.FunctionDef) and n.name == "handle" for n in tree.body)


def derive_functions(blueprint: Mapping[str, Any], workspace: Path) -> List[GradedFunction]:
    found: List[GradedFunction] = []
    for cap in blueprint.get("capabilities") or []:
        cid = str(cap.get("id") or "")
        if not (cid.isascii() and cid.isidentifier()):
            continue
        src = Path(workspace) / "app" / "actions" / f"{cid}.py"
        if not src.is_file() or not _defines_handle(src.read_text("utf-8", "replace")):
            continue
        found.append(GradedFunction(cid, f"app.actions.{cid}", two_inputs(cap)))
    return found


_RUNNER = r"""
import importlib, json, sys
spec = json.loads(sys.stdin.read())
def emit(out):
    # The result goes to a file named by the parent with a per-run nonce --
    # never stdout, which the product's own code can print to.
    out["nonce"] = spec["nonce"]
    with open(spec["out"], "w", encoding="utf-8") as fh:
        json.dump(out, fh)
def why(exc):
    kind = "exited" if isinstance(exc, SystemExit) else "raised"
    return f"handle {kind}: {type(exc).__name__}: {exc}"[:300]
try:
    original = importlib.import_module(spec["module"]).handle
except BaseException as exc:
    emit({"two_input": False, "mutants": {m: "not_run" for m in spec["mutants"]},
          "error": "import failed: " + why(exc)})
    sys.exit(0)
def canon(r):
    return json.dumps(r, sort_keys=True, default=str)
def two_input(handle):
    a, b = (canon(handle(dict(p))) for p in spec["inputs"])
    return a != b
out = {"two_input": None, "mutants": {}}
try:
    out["two_input"] = two_input(original)
except BaseException as exc:
    # A handle that raises or exits answered nothing: never ok, mutants prove nothing.
    out["two_input"] = False
    out["error"] = why(exc)
fixed = None
if not out.get("error"):
    fixed = original(dict(spec["inputs"][0]))
mutants = {
    "constant_return": lambda payload: fixed,
    "drop_rule": lambda payload: original({}),
}
for name in spec["mutants"]:
    if out.get("error"):
        out["mutants"][name] = "not_run"
        continue
    try:
        survived = two_input(mutants[name])
    except BaseException:
        survived = False  # the test reddened by raising
    out["mutants"][name] = "survived" if survived else "killed"
emit(out)
"""


def run_function(fn: GradedFunction, workspace: Path, *, python: str = sys.executable) -> FunctionResult:
    nonce = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="f8a-") as tmp:
        out_path = Path(tmp) / "grade.json"
        spec = json.dumps({"module": fn.module, "inputs": fn.inputs, "mutants": list(MUTANTS),
                           "nonce": nonce, "out": str(out_path)})
        try:
            proc = subprocess.run(
                [python, "-c", _RUNNER],
                input=spec,
                capture_output=True,
                text=True,
                cwd=str(workspace),
                timeout=RUN_TIMEOUT_S,
                env={"PYTHONPATH": str(workspace), "PATH": "/usr/bin:/bin", "ENV": "test"},
            )
        except subprocess.TimeoutExpired:
            return FunctionResult(fn.capability_id, False, [], list(MUTANTS), "timeout")
        data = _read_result(out_path, nonce)
    if data is None or proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no result"]
        return FunctionResult(fn.capability_id, False, [], list(MUTANTS),
                              f"handle exited the grader (rc={proc.returncode}): {tail[0]}"[:300])
    killed = [m for m, v in data["mutants"].items() if v == "killed"]
    survived = [m for m, v in data["mutants"].items() if v != "killed"]
    detail = data.get("error") or ("two inputs gave one result" if not data["two_input"] else "")
    return FunctionResult(fn.capability_id, data["two_input"] is True, killed, survived, detail)


def _read_result(path: Path, nonce: str) -> Dict[str, Any] | None:
    """The runner's result, or None when it never wrote one (the process was
    exited from under it) or the nonce does not match (not the runner's)."""
    if not path.is_file():
        return None
    data = json.loads(path.read_text("utf-8"))
    return data if isinstance(data, dict) and data.get("nonce") == nonce else None


def _input_schemas(blueprint: Mapping[str, Any]) -> Dict[str, Any]:
    """Each capability's declared ActionSpec ``input_schema`` (for the vault's
    per-input ranges), keyed by capability id."""
    out: Dict[str, Any] = {}
    for cap in blueprint.get("capabilities") or []:
        cid = str(cap.get("id") or "")
        if cid:
            out[cid] = cap.get("input_schema") or {}
    return out


def _store_formula(cid: str, store_index: Sequence[Mapping[str, Any]]) -> Dict[str, Any] | None:
    """The Store pack formula for ``cid`` with ``expression`` + ``inputs``, read
    from the pack it was indexed from; None when the pack row carries no
    computable expression (then it is a kernel gap, never hand-oracled)."""
    for row in store_index:
        if str(row.get("id")) != str(cid):
            continue
        src = row.get("source")
        if not src:
            return None
        try:
            data = json.loads(Path(src).read_text("utf-8"))
        except (OSError, ValueError):
            return None
        for spec in data.get("formulas") or []:
            if str((spec or {}).get("formula_id")) == str(cid) and (spec or {}).get("expression"):
                return {
                    "id": cid,
                    "key": f"store:{cid}",
                    "expression": spec["expression"],
                    "inputs": list(spec.get("inputs") or []),
                }
    return None


def _definition_resolver(vertical: str, store_root: Any):
    """A kernel -> Store -> nothing lookup: capability id -> a definition with
    ``expression`` + ``inputs``, or None. No hard-wired ids; the layer is decided
    by ``formula_resolution.resolve_layer`` over the live kernel/Store indexes."""
    from app.cerebrum_product_kernel.formulas import definition_index, resolve_definitions
    from app.factory.build.formula_resolution import (
        LAYER_KERNEL,
        LAYER_STORE,
        resolve_layer,
        store_coverage_index,
    )

    resolved = resolve_definitions()
    kernel_index = definition_index(resolved)
    store_index = store_coverage_index(vertical, store_root)

    def resolve(cid: str) -> Dict[str, Any] | None:
        layer = resolve_layer(cid, kernel_index, store_index)["layer"]
        if layer == LAYER_KERNEL:
            return resolved.get(cid)
        if layer == LAYER_STORE:
            return _store_formula(cid, store_index)
        return None

    return resolve


def _build_vault(
    blueprint: Mapping[str, Any], workspace: Path, grader_root: Path | str, store_root: Any
) -> Dict[str, Any]:
    """F-8d: the writer-blind vault for this run -- seeded inputs + kernel/Store
    computed expected per gradeable capability -- stored under the grader root
    (isolated from the writer), with kernel gaps recorded as candidate rows."""
    cids = [str(c.get("id") or "") for c in (blueprint.get("capabilities") or []) if c.get("id")]
    resolve = _definition_resolver(str(blueprint.get("vertical") or ""), store_root)
    seed = vault.new_seed()
    entries, gaps = vault.build_run(cids, resolve, _input_schemas(blueprint), seed)
    vault.write_vault(entries, grader_root, product_workspace=workspace)
    if gaps:
        vault.record_kernel_gaps(
            gaps, Path(grader_root) / vault.VAULT_REL / "new_domain_candidates.jsonl"
        )
    return {
        "seed": seed,
        "entries": [e.to_dict() for e in entries],
        "gaps": [g.to_dict() for g in gaps],
    }


def grade_functions(
    blueprint: Mapping[str, Any],
    workspace: Path | str,
    *,
    python: str = sys.executable,
    min_functions: int = MIN_FUNCTIONS,
    grader_root: Path | str | None = None,
    store_root: Any = None,
) -> FunctionGrade:
    ws = Path(workspace)
    fns = derive_functions(blueprint, ws)
    results = [run_function(f, ws, python=python) for f in fns]
    grade = FunctionGrade(results, min_functions=min_functions)
    # F-8d: when a grader root is given, also build the writer-blind vault
    # (seeded inputs + kernel-computed expected) and record it on the grade so
    # grade_record carries the seed + set into grade.json under layer1.functions.
    if grader_root is not None:
        grade.vault = _build_vault(blueprint, ws, grader_root, store_root)
    return grade
