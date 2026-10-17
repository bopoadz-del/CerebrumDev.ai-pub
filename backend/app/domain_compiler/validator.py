"""Validate a candidate pack against the kernel's REAL schemas.

The compiler does not re-declare the Domain Pack standard — it imports the
schemas the Reasoning Kernel enforces, so a pack that compiles here is a
pack the kernel will load. If the store checkout is unavailable, validation
refuses loudly rather than silently passing.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

DEFAULT_BLOCKS_PATH = r"C:\Users\user\Cerebrum-Blocks"


class SchemaUnavailableError(RuntimeError):
    """The kernel schemas could not be loaded — validation cannot run."""


def _load_domain_pack_schema():
    """Load the DomainPack schema: live store checkout first, pinned copy last.

    Order: CEREBRUM_BLOCKS_PATH env -> sibling Cerebrum-Blocks checkout ->
    the pinned vendored snapshot (kernel_schemas_pinned.py). Validation must
    never silently pass without a schema.
    """
    candidates: List[Path] = []
    env_path = os.environ.get("CEREBRUM_BLOCKS_PATH")
    if env_path:
        candidates.append(Path(env_path) / "app" / "reasoning_kernel" / "schemas.py")
    sibling = Path(__file__).resolve().parents[3] / "Cerebrum-Blocks"
    candidates.append(sibling / "app" / "reasoning_kernel" / "schemas.py")
    candidates.append(Path(DEFAULT_BLOCKS_PATH) / "app" / "reasoning_kernel" / "schemas.py")

    for schemas_py in candidates:
        if schemas_py.is_file():
            spec = importlib.util.spec_from_file_location(
                "cerebrum_blocks_kernel_schemas_live", str(schemas_py)
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            return module.DomainPack

    from . import kernel_schemas_pinned

    return kernel_schemas_pinned.DomainPack


def validate_pack(pack: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Validate a pack dict against the kernel DomainPack schema.

    Returns (ok, reasons). A validation failure is a refusal with reasons —
    never a silent default.
    """
    try:
        DomainPack = _load_domain_pack_schema()
    except SchemaUnavailableError as exc:
        return False, [str(exc)]
    reasons: List[str] = []
    try:
        DomainPack.model_validate(pack)
    except Exception as exc:  # pydantic ValidationError
        reasons.append(f"schema validation failed: {exc}")
    # Consistency checks the schema cannot express.
    if pack.get("manifest", {}).get("certification") != "candidate":
        reasons.append(
            "compiler output must be 'candidate' — certification is the "
            "store gate's decision, never the compiler's"
        )
    for artifact in (
        list(pack.get("formulas", []))
        + list(pack.get("rules", []))
        + list(pack.get("workflows", []))
        + list(pack.get("approvals", []))
    ):
        if artifact.get("certification") != "candidate":
            reasons.append(
                f"{artifact.get('formula_id') or artifact.get('rule_id') or artifact.get('workflow_id') or artifact.get('action')} "
                "carries a non-candidate certification — compiler output must "
                "not self-certify"
            )
        prov = artifact.get("provenance", {})
        for required in ("donor_repo", "donor_commit", "donor_path", "donor_symbol"):
            if not prov.get(required):
                reasons.append(
                    f"{artifact} missing provenance field '{required}'"
                )
    return (not reasons, reasons)
