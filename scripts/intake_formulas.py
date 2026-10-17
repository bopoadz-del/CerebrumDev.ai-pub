#!/usr/bin/env python3
"""Load and validate formula definitions against the overlay precedence contract.

This is the intake CLI the base set points at. It does not invent a second
merge rule: it loads the kernel resolver and lets :class:`PrecedenceError`
refuse silent shadowing, stale-version overrides, and overlays that lack
provenance or a reason.

Usage:
  python scripts/intake_formulas.py
  python scripts/intake_formulas.py --overlay path/to/domain.json
  python scripts/intake_formulas.py --base path/to/base.json --overlay a.json

Exit codes:
  0  the set resolved
  1  precedence / JSON error
  2  usage or missing file
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.cerebrum_product_kernel.formulas import (  # noqa: E402
    PrecedenceError,
    definition_index,
    load_base_definitions,
    resolve_definitions,
)

DEFAULT_BASE = (
    BACKEND
    / "app"
    / "cerebrum_product_kernel"
    / "formulas"
    / "universal_definitions.json"
)


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid JSON in %s: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("%s must be a JSON object" % path)
    return data


def intake(
    base_path: Path = DEFAULT_BASE,
    overlay_paths: Sequence[Path] = (),
) -> List[Dict[str, Any]]:
    """Resolve base + overlays. Raises PrecedenceError on contract failure."""
    base = load_base_definitions(str(base_path))
    overlays = [_load_json(Path(path)) for path in overlay_paths]
    resolved = resolve_definitions(base=base, overlays=overlays)
    return definition_index(resolved)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate formula overlays against the kernel precedence contract."
    )
    parser.add_argument(
        "--base",
        type=Path,
        default=DEFAULT_BASE,
        help="Path to the base definition JSON (default: kernel universal set).",
    )
    parser.add_argument(
        "--overlay",
        action="append",
        default=[],
        type=Path,
        help="Domain overlay JSON. Repeatable. extend vs override is declared in the file.",
    )
    parser.add_argument(
        "--print-index",
        action="store_true",
        help="Print the resolved definition index as JSON.",
    )
    parser.add_argument(
        "--answer",
        metavar="ID",
        help="Look up one definition on the product Q&A path and print it.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.base.is_file():
        print("base definition file not found: %s" % args.base, file=sys.stderr)
        return 2
    for path in args.overlay:
        if not path.is_file():
            print("overlay file not found: %s" % path, file=sys.stderr)
            return 2

    try:
        index = intake(args.base, args.overlay)
    except PrecedenceError as exc:
        print("precedence refused: %s" % exc, file=sys.stderr)
        return 1
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.answer:
        row = next((item for item in index if item["id"] == args.answer), None)
        if row is None:
            print("unknown definition: %s" % args.answer, file=sys.stderr)
            return 1
        json.dump(row, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
        return 0

    if args.print_index:
        json.dump(index, sys.stdout, indent=2, sort_keys=True)
        sys.stdout.write("\n")
    else:
        print("resolved %d definitions" % len(index))
    return 0


if __name__ == "__main__":
    sys.exit(main())
