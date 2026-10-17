"""Gate replay: a gate change is judged against builds it already certified.

Owner rule (2026-10-08): every change to the gate is replayed, before merge,
against the newest N build branches the Store gate certified (N from
``.github/gate_replay.json``, default 3). A gate that fails a previously
certified export is a gate defect until proven a product defect.

The gate runs in cerebrum-builds; this module is the Factory's half of it:

* ``render`` re-renders every file the Factory writes into a product tree --
  every stamp in the registry, then the brief-aware refresh the runner does
  before each gate -- onto a certified checkout, with THIS Factory's code;
* ``verdict`` routes the replayed gate's ``store_gate.json`` through the live
  ingest rule (n3_store_gate) on that checkout: a certified build must still
  come out green, never factory-owed and never product rework;
* ``dispatch`` asks cerebrum-builds to run its gate-replay workflow for a
  Factory sha and waits for the answer -- the Factory PR's check.

Branches are chosen by the gate's own record (its newest successful runs with
a green ``store-gate`` status), in cerebrum-builds' ``replay_select.py`` --
never by name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

#: The workflow in cerebrum-builds that replays the gate on certified builds.
REPLAY_WORKFLOW = "gate-replay.yml"
#: This repo's replay config: how many certified builds a change is replayed on.
CONFIG_REL = Path(".github") / "gate_replay.json"
CONFIG_KEY = "certified_branches"
DEFAULT_CERTIFIED_BRANCHES = 3
#: The cerebrum-builds ref whose gate replays a Factory change.
BUILDS_REF_KEY = "builds_ref"
DEFAULT_BUILDS_REF = "main"

_SKIP_DIRS = {".git", "__pycache__", ".pytest_cache"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def replay_config(root: Optional[Path] = None) -> Dict[str, Any]:
    """The replay config, defaults filled in."""
    path = Path(root or repo_root()) / CONFIG_REL
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    n = raw.get(CONFIG_KEY, DEFAULT_CERTIFIED_BRANCHES)
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = DEFAULT_CERTIFIED_BRANCHES
    return {
        CONFIG_KEY: n if n > 0 else DEFAULT_CERTIFIED_BRANCHES,
        BUILDS_REF_KEY: str(raw.get(BUILDS_REF_KEY) or DEFAULT_BUILDS_REF),
    }


def _digests(root: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in _SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        out[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def load_tree_blueprint(root: Path) -> Any:
    """The brief a build tree was compiled from (its canonical copy), or None."""
    from app.factory.blueprint import ProductBlueprint
    from app.factory.build.branch_attach import _branch_blueprint

    raw = _branch_blueprint(Path(root))
    return ProductBlueprint.model_validate(raw) if raw else None


def render_onto(root: Path | str) -> List[str]:
    """Re-render every Factory-written file onto ``root``; the paths that changed.

    Every stamp in the registry runs (each touches only what it owns), then
    the brief-aware refresh the runner performs before every gate: the
    acceptance harness must follow THIS build's brief, not the strictest
    reading a brief-less stamp renders."""
    from app.factory.build.factory_refresh import product_display_name, refresh_factory_files
    from app.factory.build.stamp_registry import stamps

    root = Path(root)
    before = _digests(root)
    for stamp in stamps():
        if stamp.apply is not None:
            stamp.apply(root)
    blueprint = load_tree_blueprint(root)
    refresh_factory_files(root, product_display_name(blueprint), blueprint)
    after = _digests(root)
    return sorted(p for p, digest in after.items() if before.get(p) != digest)


def verdict(root: Path | str, payload: Mapping[str, Any], *, branch: str = "") -> Dict[str, Any]:
    """Route a replayed gate result through the live ingest rule on ``root``.

    The result is turned into the ``store-gate`` status the gate workflow
    would write (state + "acceptance.py in Docker k/N"), mapped by the same
    snapshot rule the live read uses, then judged by the same verdict rule
    the live ingest applies (n3_store_gate.judge_snapshot)."""
    from app.factory.build.n3_store_gate import (
        BuildsTarget,
        judge_snapshot,
        report_from_store_gate_payload,
        snapshot_from_status,
    )

    sha = str(payload.get("sha") or "")
    state = "success" if payload.get("ok") else "failure"
    description = f"acceptance.py in Docker {payload.get('score') or 'no score'}"
    snap = snapshot_from_status(
        BuildsTarget(owner="", repo="", sha=sha, branch=branch),
        state,
        description,
        lambda: list(report_from_store_gate_payload(payload).lines),
    )
    snap.via = "gate-replay"
    result = judge_snapshot(Path(root), snap)
    return {
        "branch": branch,
        "sha": sha,
        "ok": bool(result.ok),
        "honesty": result.honesty,
        "detail": result.detail,
    }


# -- dispatch: the Factory PR's check ----------------------------------------


def _runs_for(request: str, runs: Any) -> Optional[Mapping[str, Any]]:
    for run in (runs or {}).get("workflow_runs") or []:
        if isinstance(run, Mapping) and request in str(run.get("display_title") or run.get("name") or ""):
            return run
    return None


def dispatch_and_wait(
    factory_sha: str,
    *,
    env: Optional[Mapping[str, str]] = None,
    request_fn: Optional[Callable[..., Any]] = None,
    sleep: Callable[[float], None] = time.sleep,
    poll_s: float = 30.0,
    wall_s: float = 4200.0,
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Run cerebrum-builds' gate replay for ``factory_sha``; its conclusion."""
    from app.factory.build.builds_push import builds_token, github_request, parse_builds_repo

    blob = env if env is not None else os.environ
    req = request_fn or github_request
    token = builds_token(blob)
    if not token:
        return {"ok": False, "detail": "no cerebrum-builds token: the replay cannot be asked for"}
    owner, repo, _ = parse_builds_repo(blob)
    cfg = dict(config or replay_config())
    request = f"factory-{factory_sha[:12]}-{uuid.uuid4().hex[:8]}"
    status, body = req(
        "POST",
        f"/repos/{owner}/{repo}/actions/workflows/{REPLAY_WORKFLOW}/dispatches",
        token=token,
        body={
            "ref": cfg[BUILDS_REF_KEY],
            "inputs": {
                "factory_ref": factory_sha,
                "certified_branches": str(cfg[CONFIG_KEY]),
                "request": request,
            },
        },
    )
    if status >= 300:
        return {
            "ok": False,
            "detail": f"dispatching {owner}/{repo} {REPLAY_WORKFLOW}@{cfg[BUILDS_REF_KEY]} "
            f"answered HTTP {status}: {str(body)[:300]}",
        }
    deadline = time.monotonic() + wall_s
    run: Optional[Mapping[str, Any]] = None
    while time.monotonic() < deadline:
        sleep(poll_s)
        status, body = req(
            "GET",
            f"/repos/{owner}/{repo}/actions/workflows/{REPLAY_WORKFLOW}/runs"
            "?event=workflow_dispatch&per_page=30",
            token=token,
        )
        if status < 300:
            run = _runs_for(request, body) or run
        if run and run.get("status") == "completed":
            break
        if run:
            status, fresh = req("GET", f"/repos/{owner}/{repo}/actions/runs/{run['id']}", token=token)
            if status < 300 and isinstance(fresh, Mapping):
                run = fresh
                if run.get("status") == "completed":
                    break
    if not run:
        return {"ok": False, "detail": f"no replay run appeared for request {request}"}
    url = str(run.get("html_url") or "")
    if run.get("status") != "completed":
        return {"ok": False, "detail": f"replay did not finish within {int(wall_s)} s: {url}"}
    return {
        "ok": run.get("conclusion") == "success",
        "detail": f"gate replay {run.get('conclusion')}: {url}",
        "url": url,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="gate_replay")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_render = sub.add_parser("render")
    p_render.add_argument("root")
    p_render.add_argument("--changed", default="")
    p_verdict = sub.add_parser("verdict")
    p_verdict.add_argument("root")
    p_verdict.add_argument("store_gate_json")
    p_verdict.add_argument("--branch", default="")
    p_verdict.add_argument("--out", default="")
    p_dispatch = sub.add_parser("dispatch")
    p_dispatch.add_argument("factory_sha")
    args = parser.parse_args(argv)

    if args.cmd == "render":
        changed = render_onto(args.root)
        if args.changed:
            Path(args.changed).write_text("".join(p + "\n" for p in changed), encoding="utf-8")
        print(f"re-rendered {len(changed)} Factory-written file(s)")
        for p in changed:
            print(f"  {p}")
        return 0
    if args.cmd == "verdict":
        path = Path(args.store_gate_json)
        payload = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        result = verdict(args.root, payload, branch=args.branch)
        if args.out:
            Path(args.out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True))
        return 0
    result = dispatch_and_wait(args.factory_sha)
    print(result["detail"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
