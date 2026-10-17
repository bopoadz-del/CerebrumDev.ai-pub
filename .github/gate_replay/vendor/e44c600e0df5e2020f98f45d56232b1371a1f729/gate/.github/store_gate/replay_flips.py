"""Judge a gate replay: did the gate under change fail a build it certified?

Owner rule (2026-10-08): a gate that fails a previously certified export is a
gate defect until proven a product defect. Certified -> red is a FLIP and
fails the replay; a build that was already red staying red is not one.

    python3 replay_flips.py selection.json results/ [--routing routing/]

A row marked ``expect: fail`` is a KNOWN-BAD fixture (known_bad_fixtures.json):
it must still fail, and on its ``expected_check``. Passing, failing only on
other checks, or being routed green by the Factory under replay is a MISS --
the gate under change no longer catches what the fixture proves.

``results/gate-replay-<label>/store_gate.json`` is each branch's replayed
gate result (artifact ``gate-replay-<label>``); with ``--routing``, ``routing/<label>.json``
is the Factory under replay's verdict on that result (gate_replay.py verdict),
which must route a certified build green too.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

#: store-gate.yml uploads a replay's result as ``gate-replay-<label>``.
ARTIFACT_PREFIX = "gate-replay-"


def _no_check(result: Mapping[str, Any]) -> str:
    """What a result that failed no check says instead: the gate's own
    ``detail`` when it wrote one (an image it could not pull, stale gate
    code), never a bare "no check failed" that names nothing."""
    detail = str(result.get("detail") or "").strip()
    if detail:
        return f"{detail}; via {result.get('via') or '?'}"
    return f"no check failed; via {result.get('via') or '?'}"


def failed_checks(result: Mapping[str, Any] | None) -> list[str]:
    return sorted(
        str(line.get("name"))
        for line in (result or {}).get("lines") or []
        if str(line.get("status") or "").upper() == "FAIL"
    )


def flip(
    baseline_ok: bool,
    result: Mapping[str, Any] | None,
    routing: Mapping[str, Any] | None = None,
) -> str | None:
    """Why this replay flipped a certified build, or None.

    ``baseline_ok`` is the gate's recorded verdict on the branch. Only a
    certified (ok) baseline can flip: fail -> fail is not a flip."""
    if not baseline_ok:
        return None
    if result is None:
        return "no result: the replayed gate wrote no store_gate.json"
    if not result.get("ok"):
        names = failed_checks(result)
        what = ", ".join(names) if names else _no_check(result)
        return f"certified -> {result.get('score') or 'no score'}: {what}"
    if routing is not None and not routing.get("ok"):
        return (
            f"the gate scored {result.get('score')} but the Factory under replay routes it "
            f"{routing.get('honesty') or 'not green'}: {routing.get('detail') or ''}".rstrip()
        )
    return None


def missed(
    expected_check: str,
    result: Mapping[str, Any] | None,
    routing: Mapping[str, Any] | None = None,
) -> str | None:
    """Why the gate under change no longer catches this known-bad build, or None."""
    if result is None:
        return "no result: the replayed gate wrote no store_gate.json"
    names = failed_checks(result)
    if result.get("ok") and not names:
        return f"passed {result.get('score') or 'with no score'}: the known-bad build was certified"
    if expected_check not in names:
        what = ", ".join(names) if names else _no_check(result)
        return f"failed {what} but not {expected_check}: not caught by the check it proves"
    if routing is not None and routing.get("ok"):
        return "the gate failed it but the Factory under replay routes it green"
    return None


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("selection")
    parser.add_argument("results")
    parser.add_argument("--routing", default="")
    args = parser.parse_args(argv)
    selection = json.loads(Path(args.selection).read_text(encoding="utf-8"))
    rows: list[str] = []
    flips = misses = certified = known = 0
    for item in selection:
        result = _read(Path(args.results) / f"{ARTIFACT_PREFIX}{item['label']}" / "store_gate.json")
        routing = _read(Path(args.routing) / f"{item['label']}.json") if args.routing else None
        score = (result or {}).get("score") or "no result"
        if item.get("expect") == "fail":
            known += 1
            why = missed(str(item.get("expected_check") or ""), result, routing)
            misses += why is not None
            verdict_cell = f"MISSED: {why}" if why else f"known-bad, still fails {item.get('expected_check')}"
        else:
            certified += 1
            if args.routing and routing is None:
                routing = {"ok": False, "detail": "no routing verdict was written"}
            why = flip(True, result, routing)
            flips += why is not None
            verdict_cell = f"FLIP: {why}" if why else "still certified"
        rows.append(f"| `{item['branch']}` | `{item['sha'][:12]}` | {score} | {verdict_cell} |")
    table = "\n".join(
        ["| branch | sha | replayed | verdict |", "|---|---|---|---|", *rows]
    )
    parts = []
    if flips:
        parts.append(f"**{flips} certified build(s) flipped** -- a gate defect until proven a product defect.")
    if misses:
        parts.append(f"**{misses} known-bad build(s) no longer caught** -- the gate under change lost a check.")
    if not parts:
        parts.append(
            f"All {certified} certified build(s) still pass and all {known} known-bad build(s) "
            "still fail on their expected check under the gate under change."
        )
    if not certified:
        parts.append("No certified build was replayed: the change is unproven.")
    verdict = " ".join(parts)
    print(table)
    print(verdict)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write("## Gate replay\n\n" + table + "\n\n" + verdict + "\n")
    return 1 if flips or misses or not certified else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
