#!/usr/bin/env python3
"""Score a Store-gate run whose product image did not build.

``docker build`` of the PRODUCT's Dockerfile is a product step. When it fails
nothing can run inside the image, but that is not "the gate's workflow failed
before scoring": the product failed the check that judges its image. This
script writes the verdict the harness would have, from the product's own
harness constants -- read with ``ast``, never imported or run:

* every check in ``IMAGE_CHECKS`` (the floor's ``stage: image``) -> FAIL, with
  a structural detail (which step, which file, the exit code);
* every other check -> NOT_RUN (it was never measured; neither satisfied nor
  a failure anyone is handed);
* ``ACCEPTANCE: 0/<REQUIRED>``.

The build log's tail goes to ``image_build_evidence.json`` as EVIDENCE, kept
out of ``detail``: the Factory derives a failure's owner from the check's
subject and its detail, and a log naming a Factory script that the product's
Dockerfile chose to run must not move the failure onto the Factory.

A harness without ``IMAGE_CHECKS`` (stamped by an older Factory) gets no score
at all -- exit 3, nothing written -- exactly as before this script existed.

Usage: score_failed_build.py <harness.py> <build.log> <exit_code> [out_dir]
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

#: The input of the image build step, which is what the failure is about.
DOCKERFILE = "Dockerfile"
EVIDENCE_LINES = 60
NO_IMAGE_CHECKS = 3
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def harness_constants(harness: Path) -> dict:
    """The literal module-level CHECKS / REQUIRED / IMAGE_CHECKS assignments."""
    tree = ast.parse(harness.read_text(encoding="utf-8", errors="replace"))
    found: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name) and target.id in {"CHECKS", "REQUIRED", "IMAGE_CHECKS"}:
            try:
                found[target.id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return found


def score(harness: Path, log_text: str, exit_code: int) -> tuple:
    """(acceptance.out text, {check: evidence}) or None when unscorable."""
    consts = harness_constants(harness)
    checks = [str(c) for c in consts.get("CHECKS") or []]
    image = [str(c) for c in consts.get("IMAGE_CHECKS") or [] if str(c) in checks]
    required = consts.get("REQUIRED")
    if not checks or not image or not isinstance(required, int):
        return None
    detail = (
        f"the image did not build from {DOCKERFILE} "
        f"(docker build exit {exit_code}); nothing could run inside it"
    )
    tail = "\n".join(_ANSI.sub("", log_text).strip().splitlines()[-EVIDENCE_LINES:])
    out = []
    for name in checks:
        if name in image:
            out.append(f"FAIL {name} — {detail}")
        else:
            out.append(f"NOT_RUN {name} — not measured: the image did not build")
    out.append(f"ACCEPTANCE: 0/{required}")
    return "\n".join(out) + "\n", {name: tail for name in image}


def main(argv: list) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    harness, log, code = Path(argv[0]), Path(argv[1]), int(argv[2])
    out_dir = Path(argv[3]) if len(argv) > 3 else Path(".")
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    if not harness.is_file():
        print(f"{harness} missing: the run stays unscored")
        return NO_IMAGE_CHECKS
    result = score(harness, log_text, code)
    if result is None:
        print("the harness declares no IMAGE_CHECKS: the run stays unscored")
        return NO_IMAGE_CHECKS
    text, evidence = result
    (out_dir / "acceptance.out").write_text(text, encoding="utf-8")
    (out_dir / "image_build_evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
