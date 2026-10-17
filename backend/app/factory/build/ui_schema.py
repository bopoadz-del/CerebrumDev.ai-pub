"""F16: one canonical UI contract, and a named check for real divergence.

A block publishes its UI contract twice, in two different shapes:

* ``block.json`` carries ``ui_schema`` as a **flat list** of
  ``{name, label, widget, options}``;
* the Store class carries ``ui_schema`` as a **dict** of
  ``input`` / ``output`` / ``params`` / ``quick_actions``.

F16 asked for "an adapter that round-trips both". Measured against the
Store, that framing does not survive contact with the data. Across the 85
blocks that publish both shapes:

    block.json is a strict superset   63
    the two agree exactly             21
    the class carries fields
    block.json does not                1   (sandbox)

A round-trip is only meaningful between two encodings of the same content.
These are not: one side is richer in 63 cases and the other in one. So the
resolution is a canonical shape plus a check, not an adapter.

**``block.json`` is canonical.** It is the published contract — the artifact
the Store ships to consumers and the one the CLONER vendors — and it is the
richer of the two almost everywhere. A generator reads it and nothing else.

The check runs in the other direction, which is the direction that loses
information: a field the class declares and ``block.json`` omits would never
reach a generated UI. ``sandbox`` is the one live instance, where
``block.json`` describes the operation (action, code, language, policy) and
the class describes the sandbox's config (memory, cpu time, network). Those
are different facts about the same block, and the class's are invisible to
anyone reading the published contract.

Divergence is reported per block rather than failing the factory outright:
it is an upstream Store condition, and a product that never binds the block
is not affected by it. :func:`assert_no_divergence` exists for callers that
do bind one and want it to be fatal.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

CANONICAL = "block.json"


class UiSchemaDivergence(RuntimeError):
    """A bound block declares UI fields its published contract omits."""


def canonical_fields(block_dir: Path) -> Set[str]:
    """Field names from the published ``block.json`` ui_schema."""
    manifest = block_dir / "block.json"
    if not manifest.is_file():
        return set()
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    schema = data.get("ui_schema")
    if not isinstance(schema, list):
        return set()
    return {
        str(item["name"])
        for item in schema
        if isinstance(item, dict) and item.get("name")
    }


def _class_ui_schema(module_path: Path) -> Optional[Dict[str, Any]]:
    """The class-shaped ui_schema literal, without importing the module.

    Parsed rather than imported: a Store block pulls in its own dependencies
    at import time, and the factory must be able to read this without them.
    """
    try:
        tree = ast.parse(module_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "ui_schema":
                try:
                    value = ast.literal_eval(node.value)
                except (ValueError, SyntaxError):
                    return None
                return value if isinstance(value, dict) else None
    return None


def class_fields(module_path: Path) -> Set[str]:
    """Field names the Store class declares: ``input`` plus every param."""
    schema = _class_ui_schema(module_path)
    if not schema:
        return set()
    names: Set[str] = set()
    if isinstance(schema.get("input"), dict):
        names.add("input")
    for param in schema.get("params") or []:
        if isinstance(param, dict) and param.get("name"):
            names.add(str(param["name"]))
    return names


def inspect_block(block_dir: Path, module_path: Optional[Path] = None) -> Dict[str, Any]:
    """Compare a block's two UI shapes. Reports; never raises."""
    out: Dict[str, Any] = {
        "block_id": block_dir.name,
        "canonical": CANONICAL,
        "canonical_fields": 0,
        "class_fields": 0,
        "class_only": [],
        "has_canonical": False,
        "has_class": False,
    }
    canonical = canonical_fields(block_dir)
    out["has_canonical"] = bool(canonical)
    out["canonical_fields"] = len(canonical)

    if module_path is None or not module_path.is_file():
        return out
    declared = class_fields(module_path)
    out["has_class"] = bool(declared)
    out["class_fields"] = len(declared)
    # Only this direction loses information: a field the class declares and
    # the published contract omits never reaches a generated UI.
    out["class_only"] = sorted(declared - canonical)
    return out


def assert_no_divergence(block_dir: Path, module_path: Optional[Path] = None) -> None:
    """Raise when a block's class declares UI fields block.json omits."""
    report = inspect_block(block_dir, module_path)
    if report["class_only"]:
        raise UiSchemaDivergence(
            f"{report['block_id']}: the block class declares UI field(s) its "
            f"published block.json omits: {', '.join(report['class_only'])}. "
            "block.json is canonical, so those fields would never reach a "
            "generated UI."
        )


def survey(registry_root: Path, blocks_root: Optional[Path] = None) -> Dict[str, Any]:
    """Compare both shapes across a Store checkout. Used by S2 evidence."""
    reports: List[Dict[str, Any]] = []
    for block_dir in sorted(p for p in registry_root.iterdir() if p.is_dir()):
        module = None
        if blocks_root is not None:
            candidate = blocks_root / f"{block_dir.name}.py"
            module = candidate if candidate.is_file() else None
        reports.append(inspect_block(block_dir, module))
    diverging = [r for r in reports if r["class_only"]]
    return {
        "canonical": CANONICAL,
        "blocks": len(reports),
        "with_canonical": sum(1 for r in reports if r["has_canonical"]),
        "with_class": sum(1 for r in reports if r["has_class"]),
        "diverging": [
            {"block_id": r["block_id"], "class_only": r["class_only"]}
            for r in diverging
        ],
    }
