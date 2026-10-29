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

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

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


def derive_functions(blueprint: Mapping[str, Any], workspace: Path) -> List[GradedFunction]:
    found: List[GradedFunction] = []
    for cap in blueprint.get("capabilities") or []:
        cid = str(cap.get("id") or "")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", cid):
            continue
        src = Path(workspace) / "app" / "actions" / f"{cid}.py"
        if not src.is_file() or not re.search(r"^def handle\(", src.read_text("utf-8", "replace"), re.M):
            continue
        found.append(GradedFunction(cid, f"app.actions.{cid}", two_inputs(cap)))
    return found


_RUNNER = r"""
import importlib, json, sys
spec = json.loads(sys.stdin.read())
mod = importlib.import_module(spec["module"])
original = mod.handle
def canon(r):
    return json.dumps(r, sort_keys=True, default=str)
def two_input(handle):
    a, b = (canon(handle(dict(p))) for p in spec["inputs"])
    return a != b
out = {"two_input": None, "mutants": {}}
try:
    out["two_input"] = two_input(original)
except Exception as exc:
    out["two_input"] = False
    out["error"] = f"{type(exc).__name__}: {exc}"[:300]
fixed = None
try:
    fixed = original(dict(spec["inputs"][0]))
except Exception:
    fixed = None
mutants = {
    "constant_return": lambda payload: fixed,
    "drop_rule": lambda payload: original({}),
}
for name in spec["mutants"]:
    try:
        survived = two_input(mutants[name])
    except Exception:
        survived = False  # the test reddened by raising
    out["mutants"][name] = "survived" if survived else "killed"
print("@@GRADE@@" + json.dumps(out))
"""


def run_function(fn: GradedFunction, workspace: Path, *, python: str = sys.executable) -> FunctionResult:
    spec = json.dumps({"module": fn.module, "inputs": fn.inputs, "mutants": list(MUTANTS)})
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
    line = next((l for l in proc.stdout.splitlines() if l.startswith("@@GRADE@@")), None)
    if line is None:
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        return FunctionResult(fn.capability_id, False, [], list(MUTANTS), tail[0][:300])
    data = json.loads(line[len("@@GRADE@@"):])
    killed = [m for m, v in data["mutants"].items() if v == "killed"]
    survived = [m for m, v in data["mutants"].items() if v != "killed"]
    detail = data.get("error") or ("two inputs gave one result" if not data["two_input"] else "")
    return FunctionResult(fn.capability_id, bool(data["two_input"]), killed, survived, detail)


def grade_functions(
    blueprint: Mapping[str, Any],
    workspace: Path | str,
    *,
    python: str = sys.executable,
    min_functions: int = MIN_FUNCTIONS,
) -> FunctionGrade:
    ws = Path(workspace)
    fns = derive_functions(blueprint, ws)
    results = [run_function(f, ws, python=python) for f in fns]
    return FunctionGrade(results, min_functions=min_functions)
