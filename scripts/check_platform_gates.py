"""Check a product repo carries the platform-build gate artifacts.

Forcing function for GATES.md (Factory). A platform build that does not
carry these artifacts has not passed its gates and must not ship.

Usage:
    python scripts/check_platform_gates.py --repo <path-to-product-repo>

Exit 1 on the first missing gate artifact.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REQUIRED = {
    "GATES.md (or ACCEPTANCE.md referencing GATES.md)": (
        lambda root: (root / "GATES.md").is_file()
        or (
            (root / "ACCEPTANCE.md").is_file()
            and "GATES.md" in (root / "ACCEPTANCE.md").read_text(encoding="utf-8")
        )
    ),
    "VENDOR.lock": lambda root: (root / "VENDOR.lock").is_file(),
    "KNOWNS / honesty doc": lambda root: (
        (root / "KNOWN_LIMITATIONS.md").is_file() or (root / "KNOWN_INCOMPLETE.md").is_file()
    ),
    "mutation probes": lambda root: any(
        root.rglob("mutation_probes.py")
    ),
    "CI workflow": lambda root: any(
        root.joinpath(".github", "workflows").rglob("*.yml")
    ),
    "no secrets in repo": lambda root: not any(
        root.rglob(".env")
    ) or "example" in str(next(root.rglob(".env"))),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    root = args.repo.resolve()
    if not root.is_dir():
        print(f"not a directory: {root}")
        return 1
    failures = []
    for name, check in REQUIRED.items():
        try:
            ok = check(root)
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"  check error for {name}: {exc}")
        if not ok:
            failures.append(name)
        else:
            print(f"OK   {name}")
    if failures:
        print("PLATFORM GATES MISSING:")
        for f in failures:
            print(f"  - {f}")
        print("Read GATES.md in the Factory before building a platform.")
        return 1
    print("PLATFORM GATES OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
