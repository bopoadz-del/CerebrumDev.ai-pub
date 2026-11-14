"""F-8c-3 -- GRADE.md: the human-readable render of a grade record.

Pure function of the grade dict (product_grade.v1). It reports; it never
decides -- certification is grade_record.certification_refusal's job.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Mapping

GRADE_MD = "GRADE.md"


def _names(xs: Any) -> str:
    return ", ".join(str(x) for x in xs or []) or "none"


def _shown_verdict(v: Mapping[str, Any]) -> str:
    """PASS only for exactly ``PASS`` with non-blank string evidence (the gate's
    rule); anything else shows as FAIL, naming what was recorded."""
    verdict, ev = v.get("verdict"), v.get("evidence")
    if verdict == "PASS" and isinstance(ev, str) and ev.strip():
        return "PASS"
    return "FAIL" if verdict == "FAIL" else f"FAIL (recorded {verdict!r})"


def _probe_lines(f: Mapping[str, Any]) -> List[str]:
    """The handback for a failing handle: the exact inputs the grader sent,
    the answers it observed and the declared fields the answer moved with.
    Empty when the record carries no probe (a passing handle)."""
    probe = f.get("probe") or {}
    if not isinstance(probe, Mapping) or not probe:
        return []
    lines = [f"    - sent: {json.dumps(probe.get('inputs'), sort_keys=True, default=str)}"]
    lines += [f"    - observed: {a}" for a in probe.get("answers") or []]
    lines.append(f"    - answer moved with: {_names(f.get('depends_on'))}")
    return lines


def render_md(grade: Mapping[str, Any]) -> str:
    l1, l2 = grade["layer1"], grade["layer2"]
    fn, ui = l1.get("functions") or {}, l1.get("ui") or {}
    steps = ui.get("steps") or []
    lines: List[str] = [
        f"# GRADE -- {grade['product_id']}",
        "",
        f"**Certified:** {'YES' if grade.get('certified') is True else 'NO'} (Layer 1 decides; Layer 2 can only block)",
        "",
        f"## Layer 1 (deterministic): {'GREEN' if l1.get('ok') is True else 'RED'}",
        f"- Functions graded: {fn.get('graded', 0)} (min {fn.get('min_functions', '?')})",
    ]
    for f in fn.get("functions") or []:
        lines.append(
            f"  - `{f['capability_id']}`: {'PASS' if f.get('ok') is True else 'FAIL'}; "
            f"mutants killed {_names(f.get('mutants_killed'))}; survived {_names(f.get('mutants_survived'))}"
        )
        if f.get("ok") is not True:
            lines += _probe_lines(f)
    for u in fn.get("ungraded") or []:
        lines.append(f"  - `{u.get('capability_id')}`: UNGRADED; {u.get('detail')}")
    lines.append(f"- UI steps passed: {sum(1 for s in steps if s.get('passed') is True)}/{len(steps)}")
    lines += [f"  - FAIL: {name}" for name in ui.get("failing") or []]
    lines += ["", f"## Layer 2 (separate grader context): {'ran' if l2.get('ran') is True else 'did not run'}"]
    for v in l2.get("verdicts") or []:
        lines.append(f"- {_shown_verdict(v)}: {v.get('question')} -- evidence: \"{v.get('evidence') or ''}\"")
    lines += [f"- OPEN: {q}" for q in l2.get("open_fails") or []]
    if grade.get("next_tasks"):
        lines += ["", "## Next tasks"] + [f"- {t}" for t in grade["next_tasks"]]
    return "\n".join(lines) + "\n"


def write_grade_md(root: Path | str, grade: Mapping[str, Any]) -> Path:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / GRADE_MD
    path.write_text(render_md(grade), encoding="utf-8")
    return path
