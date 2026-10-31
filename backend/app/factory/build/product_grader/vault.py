"""F-8d -- writer-blind grader input vault.

The writer must never see the inputs the grader tests a built product with.
Per graded function this module:

* generates a set of test inputs from the function's resolved DEFINITION (its
  declared ``inputs`` and ``expression``) under a per-run random seed;
* computes the EXPECTED result through the kernel evaluator
  (:func:`cerebrum_product_kernel.formulas.evaluator.safe_eval`) -- kernel
  first, then a Store definition, never a hand-typed oracle;
* stores the seed and the generated set under the grader's OWN root
  (:data:`VAULT_REL`), never the product tree and never the writer's workspace.

A function with no kernel/Store definition is a *kernel gap*: reported "not
gradable" and appended to ``new_domain_candidates.jsonl``; it is never passed
and never hand-oracled.

Resolution order (kernel -> Store -> nothing) is the caller's job
(``formula_resolution``); this module is handed the resolved definition, or
``None`` for a gap. Writer isolation (no read path to the vault directory) is
enforced by the build runner's Factory-owned-files root, the same mechanism
``domain_acceptance.product_kernel_files`` uses; a test proves a writer-side
``open()`` of a vault path fails.
"""

from __future__ import annotations

import json
import random
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Union

from app.cerebrum_product_kernel.formulas.evaluator import MissingInput, safe_eval

#: The grader's own vault directory, relative to the grader working root. Never
#: created under the product tree or on the writer's sys.path / working root.
VAULT_REL = ".grader_vault"

#: Reason recorded for a function the kernel and Store do not cover.
KERNEL_GAP = "not gradable -- kernel gap"


class VaultLeak(Exception):
    """The vault would be reachable from the writer's workspace -- refused."""


def _within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class VaultEntry:
    """The seeded inputs and kernel-computed expected result for one function."""

    capability_id: str
    definition_id: str
    definition_address: str
    seed: int
    inputs: Dict[str, float]
    expected: Any

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class KernelGap:
    """A function the kernel/Store do not define: never gradable, never passed."""

    capability_id: str
    reason: str = KERNEL_GAP

    def to_dict(self) -> Dict[str, Any]:
        return {"capability_id": self.capability_id, "reason": self.reason, "gradable": False}


Entry = Union[VaultEntry, KernelGap]


def new_seed() -> int:
    """A fresh per-run seed. ``secrets`` so it is unpredictable to the writer,
    who never sees it (only the grader's own grade.json records it afterwards)."""
    return secrets.randbits(63)


def _input_names(definition: Mapping[str, Any]) -> List[str]:
    names = [str(n) for n in (definition.get("inputs") or []) if str(n)]
    return list(dict.fromkeys(names))


def _draw(name: str, input_schema: Mapping[str, Any], rng: random.Random) -> float:
    """A seeded value for one input, inside the schema's declared range when it
    states one. Drawn from ``rng`` so two seeds give two different values -- the
    value is never a constant and never a field-name-specific literal."""
    lo, hi = 1.0, 1000.0
    spec = input_schema.get(name) if isinstance(input_schema, Mapping) else None
    if isinstance(spec, Mapping):
        if spec.get("minimum") is not None:
            lo = float(spec["minimum"])
        if spec.get("maximum") is not None:
            hi = float(spec["maximum"])
    if hi <= lo:
        hi = lo + 1.0
    return round(rng.uniform(lo, hi), 2)


def generate_inputs(
    definition: Mapping[str, Any],
    input_schema: Mapping[str, Any],
    seed: int,
) -> Dict[str, float]:
    """A seeded input set covering every name the definition's expression binds.
    The seed alone determines the draw; the writer cannot predict it."""
    rng = random.Random(seed)
    return {name: _draw(name, input_schema or {}, rng) for name in _input_names(definition)}


def expected_value(definition: Mapping[str, Any], inputs: Mapping[str, float]) -> Any:
    """The expected result, computed by the kernel evaluator over the generated
    inputs -- never read from a literal. Raises :class:`MissingInput` if the
    expression binds a name the input set does not cover (surfaced, not
    defaulted)."""
    expression = str(definition.get("expression") or "").strip()
    if not expression:
        raise MissingInput("expression")
    return safe_eval(expression, {k: float(v) for k, v in inputs.items()})


def build_entry(
    capability_id: str,
    definition: Optional[Mapping[str, Any]],
    input_schema: Mapping[str, Any],
    seed: int,
) -> Entry:
    """One vault entry for a graded function, or a :class:`KernelGap` when no
    kernel/Store definition resolved (never hand-oracled)."""
    if not definition:
        return KernelGap(capability_id)
    inputs = generate_inputs(definition, input_schema, seed)
    expected = expected_value(definition, inputs)
    return VaultEntry(
        capability_id=capability_id,
        definition_id=str(definition.get("id") or ""),
        definition_address=str(definition.get("key") or definition.get("address") or ""),
        seed=seed,
        inputs=inputs,
        expected=expected,
    )


def vault_dir(grader_root: Union[Path, str]) -> Path:
    """The grader-owned vault directory, created under the grader root -- never
    under the product tree or the writer's working root."""
    directory = Path(grader_root) / VAULT_REL
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_vault(
    entries: List[VaultEntry],
    grader_root: Union[Path, str],
    product_workspace: Optional[Union[Path, str]] = None,
) -> Path:
    """Persist the seed + generated set under the grader's own root. The returned
    path's contents are what grade.json carries for reproducibility.

    When ``product_workspace`` is given, writer isolation is ENFORCED (not
    trusted): the grader root must sit outside the writer's workspace -- the
    writer runs with that workspace as its cwd/PYTHONPATH (see
    ``function_grader.run_function``), so a vault under it would be readable.
    A build that tried to put the vault in the product tree raises
    :class:`VaultLeak` here, before any input is generated."""
    if product_workspace is not None and _within(Path(grader_root), Path(product_workspace)):
        raise VaultLeak(
            f"vault root {Path(grader_root)} is inside the writer workspace "
            f"{Path(product_workspace)}"
        )
    out = vault_dir(grader_root) / "vault.json"
    out.write_text(
        json.dumps([e.to_dict() for e in entries], indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return out


def record_kernel_gaps(gaps: List[KernelGap], candidates_path: Union[Path, str]) -> None:
    """Append each kernel-gap function as a ``new_domain_candidates`` row -- the
    kernel-coverage backlog, never a pass."""
    if not gaps:
        return
    path = Path(candidates_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for gap in gaps:
            fh.write(json.dumps(gap.to_dict(), sort_keys=True) + "\n")
