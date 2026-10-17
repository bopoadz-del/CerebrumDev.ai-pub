#!/usr/bin/env python3
"""Score a Store-gate run whose product image built but never served.

The image built; the container then died (or never answered /health) before
acceptance.py could run. Like a failed image build (score_failed_build.py),
that is a verdict on the product's image -- not "the gate's workflow failed
before scoring". This script writes it from the product's own harness
constants (read with ``ast``, never run):

* every check in ``IMAGE_CHECKS`` -> FAIL, structural detail;
* every other check -> NOT_RUN;
* ``ACCEPTANCE: 0/<REQUIRED>``.

The container log tail is EVIDENCE (``image_build_evidence.json``), never the
detail. When the log holds a Python traceback, the innermost frame inside the
product tree becomes a TYPED row in ``image_evidence_rows.json``:
``{"kind": "boot_crash", "file": <path relative to the product root>,
"line": <n>, "text": <the exception line>}``. The Factory reads that row
against the build's factory receipt to decide who owns the crash (a crash in
Factory-stamped code is the Factory's, never the writer's rework). Frames
outside the product tree (site-packages, the interpreter) are never the row.

Live 2026-10-07 (build/plt_0b355947d7a1494e): the product's stamped
app/observe.py raised PermissionError on its request log at boot; the run
read "workflow failed before scoring" and nobody was told which file.

Usage: boot_crash.py <harness.py> <container.log> [out_dir] [app_root]
(app_root: the product's path inside the image, default /app)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_failed_build import EVIDENCE_LINES, NO_IMAGE_CHECKS, _ANSI, harness_constants  # noqa: E402

ROW_BOOT_CRASH = "boot_crash"
#: Python's own traceback frame line -- the interpreter's format, not prose.
_FRAME = re.compile(r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)')
_EXC = re.compile(r"^(?P<exc>[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt)\b.*)$")


def crash_row(log_text: str, app_root: str = "/app") -> dict | None:
    """The innermost product-tree frame of the LAST traceback, as a typed row."""
    lines = _ANSI.sub("", log_text).splitlines()
    starts = [i for i, ln in enumerate(lines) if ln.startswith("Traceback (most recent call last)")]
    if not starts:
        return None
    block = lines[starts[-1]:]
    root = PurePosixPath(app_root)
    frame = None
    for ln in block:
        m = _FRAME.match(ln)
        if not m:
            continue
        path = PurePosixPath(m.group("file"))
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        if rel.parts and rel.parts[0] not in {"usr", "venv", ".venv"} and "site-packages" not in rel.parts:
            frame = (str(rel), int(m.group("line")))
    if frame is None:
        return None
    text = ""
    for ln in block:
        m = _EXC.match(ln.strip())
        if m:
            text = m.group("exc")
    return {"kind": ROW_BOOT_CRASH, "file": frame[0], "line": frame[1], "text": text}


def score(harness: Path, log_text: str, app_root: str = "/app") -> tuple | None:
    consts = harness_constants(harness)
    checks = [str(c) for c in consts.get("CHECKS") or []]
    image = [str(c) for c in consts.get("IMAGE_CHECKS") or [] if str(c) in checks]
    required = consts.get("REQUIRED")
    if not checks or not image or not isinstance(required, int):
        return None
    detail = "the image built but the container never answered /health (it died or hung at boot)"
    out = []
    for name in checks:
        if name in image:
            out.append(f"FAIL {name} — {detail}")
        else:
            out.append(f"NOT_RUN {name} — not measured: the container never served")
    out.append(f"ACCEPTANCE: 0/{required}")
    tail = "\n".join(_ANSI.sub("", log_text).strip().splitlines()[-EVIDENCE_LINES:])
    row = crash_row(log_text, app_root)
    rows = {name: [row] for name in image} if row else {}
    return "\n".join(out) + "\n", {name: tail for name in image}, rows


def main(argv: list) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    harness, log = Path(argv[0]), Path(argv[1])
    out_dir = Path(argv[2]) if len(argv) > 2 else Path(".")
    app_root = argv[3] if len(argv) > 3 else "/app"
    log_text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    if not harness.is_file():
        print(f"{harness} missing: the run stays unscored")
        return NO_IMAGE_CHECKS
    result = score(harness, log_text, app_root)
    if result is None:
        print("the harness declares no IMAGE_CHECKS: the run stays unscored")
        return NO_IMAGE_CHECKS
    text, evidence, rows = result
    (out_dir / "acceptance.out").write_text(text, encoding="utf-8")
    (out_dir / "image_build_evidence.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (out_dir / "image_evidence_rows.json").write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
