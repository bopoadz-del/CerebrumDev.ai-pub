"""Shadow proof for F0: template brief vs architect NARRATIVE brief.

Reads finished build workspaces (their ``build_ledger.jsonl`` and tree) --
never runs a build -- and prints, per build and per arm, the four measures the
owner set:

* handler_bodies_distinct -- distinct ``handle()`` bodies / handlers in app/actions
* authorship              -- agent-written artifacts (docs/build_provenance.json)
* rounds_to_green         -- REWORK rounds recorded before the run ended
* k/N                     -- the last acceptance score the ledger recorded

The arm of a build is read from its own ledger: a build whose architect NOTE
was used (mode ``on``, not a fallback) is ``narrative``; everything else is
``template``. Flip ``FACTORY_BRIEF_NARRATIVE=on`` only when the narrative arm
is >= the template arm on every measure (fewer or equal rounds).

Usage:
    python scripts/compare_brief_narrative.py <workspace> [<workspace> ...]
    python scripts/compare_brief_narrative.py --json <workspace> ...
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
from pathlib import Path
from statistics import mean
from typing import Any

SCORE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


def _events(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "build_ledger.jsonl"
    out: list[dict[str, Any]] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    p = row.get("payload")
    return p if isinstance(p, dict) else {}


def arm_of(events: list[dict[str, Any]]) -> str:
    for row in events:
        rec = _payload(row).get("architect")
        if (
            isinstance(rec, dict)
            and rec.get("entry") == "narrative"
            and rec.get("mode") == "on"
            and (rec.get("lint") or {}).get("ok")
            and not rec.get("fallback")
        ):
            return "narrative"
    return "template"


def rounds_to_green(events: list[dict[str, Any]]) -> int:
    return sum(1 for row in events if row.get("kind") == "REWORK")


def outcome(events: list[dict[str, Any]]) -> str:
    for row in reversed(events):
        if row.get("kind") in ("RUN_SUCCEEDED", "RUN_FAILED"):
            return str(row.get("kind"))
    return "UNFINISHED"


def acceptance_score(events: list[dict[str, Any]]) -> str | None:
    for row in reversed(events):
        score = str(_payload(row).get("score") or "")
        if SCORE_RE.match(score):
            return score.replace(" ", "")
    return None


def handler_bodies(workspace: Path) -> dict[str, int] | None:
    actions = workspace / "app" / "actions"
    if not actions.is_dir():
        return None
    digests: list[str] = []
    for path in sorted(actions.glob("*.py")):
        if path.name.startswith("_"):
            continue
        src = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "handle":
                chunk = ast.get_source_segment(src, node) or ast.dump(node)
                digests.append(hashlib.sha256(chunk.encode("utf-8")).hexdigest())
                break
    return {"handlers": len(digests), "distinct": len(set(digests))}


def authorship(workspace: Path) -> int | None:
    path = workspace / "docs" / "build_provenance.json"
    if not path.is_file():
        return None
    try:
        prov = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.factory.build.authorship import writer_authorship_counts

    return writer_authorship_counts(prov.get("artifact_sources") or {})["agent_written"]


def measure(workspace: Path) -> dict[str, Any]:
    events = _events(workspace)
    bodies = handler_bodies(workspace)
    score = acceptance_score(events)
    k = SCORE_RE.match(score) if score else None
    return {
        "workspace": str(workspace),
        "arm": arm_of(events),
        "outcome": outcome(events),
        "rounds_to_green": rounds_to_green(events),
        "acceptance": score,
        "acceptance_k": int(k.group(1)) if k else None,
        "handlers_distinct": (bodies or {}).get("distinct"),
        "handlers": (bodies or {}).get("handlers"),
        "agent_written": authorship(workspace),
    }


def _avg(rows: list[dict[str, Any]], key: str) -> float | None:
    vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
    return round(mean(vals), 2) if vals else None


def verdict(rows: list[dict[str, Any]]) -> dict[str, Any]:
    arms = {a: [r for r in rows if r["arm"] == a] for a in ("template", "narrative")}
    summary = {
        a: {
            "builds": len(rs),
            "rounds_to_green": _avg(rs, "rounds_to_green"),
            "acceptance_k": _avg(rs, "acceptance_k"),
            "handlers_distinct": _avg(rs, "handlers_distinct"),
            "agent_written": _avg(rs, "agent_written"),
        }
        for a, rs in arms.items()
    }
    t, n = summary["template"], summary["narrative"]
    measures = ("rounds_to_green", "acceptance_k", "handlers_distinct", "agent_written")
    unmeasured = [m for m in measures if t[m] is None or n[m] is None]
    ok = not unmeasured and t["builds"] > 0 and n["builds"] > 0 and all(
        (n[m] <= t[m]) if m == "rounds_to_green" else (n[m] >= t[m]) for m in measures
    )
    return {"summary": summary, "flip": ok, "unmeasured": unmeasured}


def main(argv: list[str]) -> int:
    as_json = "--json" in argv
    paths = [Path(a) for a in argv if a != "--json"]
    if not paths:
        print(__doc__)
        return 2
    rows = [measure(p) for p in paths]
    result = {"builds": rows, **verdict(rows)}
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    for r in rows:
        print(
            f"{r['arm']:<10} {r['outcome']:<13} rounds={r['rounds_to_green']} "
            f"k/N={r['acceptance']} distinct={r['handlers_distinct']}/{r['handlers']} "
            f"authored={r['agent_written']}  {r['workspace']}"
        )
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    print("FLIP to on:" if result["flip"] else "do not flip:", result["unmeasured"] or "every measure compared")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
