#!/usr/bin/env python3
"""Product gate lines that measure a GENERATED PRODUCT, whatever it is.

Each line here measures the generated tree (or the generator itself) for one
mechanism. Nothing names a product, a capability or a vertical; the same line
runs on every blueprint:

  determinism       generate the blueprint twice; every file is identical
                    (JSON values shaped like a timestamp are the only thing
                    allowed to differ -- by value shape, not by key name)
  store_runtime     every block a REUSE/COMPOSE capability binds imports and
                    loads IN-PROCESS from the product's vendor/ tree, and every
                    such handler executes through app/block_runtime -- no
                    remote Store
  agent_scope       an agent (X-Agent-Id) is refused (403) an action outside
                    its hat's allowed_actions, and an unknown agent is refused
  workflow_approvals  approvals persist for every workflow step that declares
                    ``required``, survive a restart, and are refused for steps
                    that require none
  doc_honesty       the product's own README/docs carry no percentage figure
                    (an unmeasured readiness claim)
  live              suites that need a DEPLOYED instance: WITHHELD unless a
                    base URL is given, with the operator settings they need
                    declared in artifacts/blockers.json

Run:  python scripts/product_gate.py --blueprint blueprints/<x>.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
BLOCKERS = ROOT / "artifacts" / "blockers.json"

PASS, FAIL, WITHHELD = "PASS", "FAIL", "WITHHELD"

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:?\d{2}|Z)?$")
_PERCENT = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%")


def _line(status: str, detail: Any) -> Dict[str, Any]:
    return {"status": status, "detail": detail}


def store_root() -> Optional[str]:
    return os.environ.get("CEREBRUM_BLOCKS_ROOT") or None


def generate(blueprint: Path, out: Path, blocks_root: Optional[str] = None) -> None:
    """The standard generator (app.factory.cli generate), coder off."""
    env = {**os.environ, "PYTHONPATH": str(BACKEND), "ENV": os.environ.get("ENV", "test"),
           "FACTORY_CODER_ENABLED": "0", "PYTHONIOENCODING": "utf-8"}
    cmd = [sys.executable, "-m", "app.factory.cli", "generate",
           "--blueprint", str(blueprint), "--out", str(out)]
    root = blocks_root or store_root()
    if root:
        cmd += ["--blocks-root", root]
    proc = subprocess.run(cmd, cwd=str(BACKEND), env=env, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not (out / "app" / "main.py").is_file():
        raise RuntimeError(f"generation failed: {proc.stderr[-800:]}")


# --- determinism -------------------------------------------------------------

def _scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, str) and _TIMESTAMP.match(value):
        return "<timestamp>"
    return value


def _tree(root: Path) -> Dict[str, Path]:
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts}


def determinism(blueprint: Path, blocks_root: Optional[str] = None) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        a, b = Path(tmp) / "a", Path(tmp) / "b"
        generate(blueprint, a, blocks_root)
        generate(blueprint, b, blocks_root)
        ta, tb = _tree(a), _tree(b)
        diffs: List[str] = sorted(set(ta) ^ set(tb))
        for rel in sorted(set(ta) & set(tb)):
            x, y = ta[rel].read_bytes(), tb[rel].read_bytes()
            if x == y:
                continue
            if rel.endswith(".json"):
                try:
                    if _scrub(json.loads(x)) == _scrub(json.loads(y)):
                        continue
                except ValueError:
                    pass
            diffs.append(rel)
        if diffs:
            return _line(FAIL, {"differing_files": diffs[:20], "count": len(diffs)})
        return _line(PASS, {"files_compared": len(ta)})


# --- in-product probes (run inside the product, isolated) --------------------

def _probe(product: Path, code: str, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-c", code], cwd=str(product), capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(product), "PYTHONIOENCODING": "utf-8", **(env or {})},
        check=False, timeout=600,
    )
    last = (proc.stdout.strip().splitlines() or [""])[-1]
    try:
        return json.loads(last)
    except ValueError:
        return {"error": (proc.stderr or proc.stdout)[-800:]}


_STORE_RUNTIME_PROBE = r'''
import ast, json, pathlib
from app.block_runtime import load_block
plan = json.loads(pathlib.Path("factory_plan.json").read_text(encoding="utf-8"))
bound, handlers, broken, remote = set(), [], [], []
for cap in plan["capabilities"]:
    if not cap.get("block_ids"):
        continue
    bound.update(cap["block_ids"])
    mod = pathlib.Path("app/actions") / (cap["capability_id"].replace("-", "_") + ".py")
    tree = ast.parse(mod.read_text(encoding="utf-8"))
    via = any(isinstance(n, ast.ImportFrom) and n.module == "app.block_runtime" for n in ast.walk(tree))
    (handlers if via else remote).append(cap["capability_id"])
for bid in sorted(bound):
    try:
        load_block(bid)
    except Exception as exc:
        broken.append(bid + ": " + type(exc).__name__ + ": " + str(exc)[:160])
print(json.dumps({"bound_blocks": len(bound), "in_process_handlers": len(handlers),
                  "not_in_process": remote, "unloadable": broken}))
'''


def store_runtime(product: Path) -> Dict[str, Any]:
    if not (product / "app" / "block_runtime.py").is_file():
        return _line(FAIL, "product carries no in-process block runtime (app/block_runtime.py)")
    r = _probe(product, _STORE_RUNTIME_PROBE)
    if "error" in r:
        return _line(FAIL, r["error"])
    ok = not r["not_in_process"] and not r["unloadable"]
    return _line(PASS if ok else FAIL, r)


_AGENT_PROBE = r'''
import json, pathlib
from fastapi.testclient import TestClient
from app.main import app
from app.actions import ACTION_CATALOG
hats = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(pathlib.Path("app/agents/manifests").glob("*.json"))]
hats = [h for h in hats if h.get("kind") == "hat"]
client = TestClient(app)
pair = next(((h, s) for h in hats for s in ACTION_CATALOG if s["action_id"] not in set(h.get("allowed_actions") or [])), None)
out = {"hats": len(hats)}
if pair is None:
    out["measurable"] = False
else:
    h, s = pair
    r = client.post("/v1/actions/" + s["capability_id"], json={}, headers={"X-Agent-Id": h["agent_id"], "X-Tenant-Id": "gate"})
    out["outside_scope"] = r.status_code
    own = next((s2 for s2 in ACTION_CATALOG if s2["action_id"] in set(h.get("allowed_actions") or [])), None)
    if own is not None:
        r2 = client.post("/v1/actions/" + own["capability_id"], json={}, headers={"X-Agent-Id": h["agent_id"], "X-Tenant-Id": "gate"})
        out["inside_scope"] = r2.status_code
    r3 = client.post("/v1/actions/" + s["capability_id"], json={}, headers={"X-Agent-Id": "no-such-agent", "X-Tenant-Id": "gate"})
    out["unknown_agent"] = r3.status_code
print(json.dumps(out))
'''


def agent_scope(product: Path) -> Dict[str, Any]:
    r = _probe(product, _AGENT_PROBE)
    if "error" in r:
        return _line(FAIL, r["error"])
    if not r.get("hats") or r.get("measurable") is False:
        return _line(FAIL, {"reason": "no hat with a bounded scope to enforce", **r})
    ok = (r.get("outside_scope") == 403 and r.get("unknown_agent") == 403
          and r.get("inside_scope", 0) != 403)
    return _line(PASS if ok else FAIL, r)


_APPROVAL_WRITE = r'''
import json
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
wfs = c.get("/v1/workflows").json()
posted, refused, wrong = 0, 0, []
for wf in wfs:
    for i, step in enumerate(wf.get("steps") or []):
        r = c.post("/v1/workflows/" + wf["workflow_id"] + "/approvals",
                   json={"step": i, "approver": "gate", "decision": "approved"})
        if step.get("required") is True:
            posted += r.status_code == 201
            if r.status_code != 201:
                wrong.append((wf["workflow_id"], i, r.status_code))
        else:
            refused += r.status_code == 422
            if r.status_code != 422:
                wrong.append((wf["workflow_id"], i, r.status_code))
print(json.dumps({"workflows": [w["workflow_id"] for w in wfs], "posted": posted, "refused": refused, "wrong": wrong}))
'''

_APPROVAL_READ = r'''
import json, sys
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
n = sum(len(c.get("/v1/workflows/" + w + "/approvals").json()["approvals"]) for w in json.loads(sys.argv[1]))
print(json.dumps({"persisted": n}))
'''


def workflow_approvals(product: Path) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory() as data:
        env = {"PRODUCT_DATA_DIR": data}
        w = _probe(product, _APPROVAL_WRITE, env)
        if "error" in w:
            return _line(FAIL, w["error"])
        proc = subprocess.run(
            [sys.executable, "-c", _APPROVAL_READ, json.dumps(w["workflows"])],
            cwd=str(product), capture_output=True, text=True, check=False, timeout=600,
            env={**os.environ, "PYTHONPATH": str(product), "PYTHONIOENCODING": "utf-8", **env})
        try:
            r = json.loads((proc.stdout.strip().splitlines() or ["{}"])[-1])
        except ValueError:
            return _line(FAIL, proc.stderr[-800:])
    detail = {**w, **r}
    ok = not w["wrong"] and r.get("persisted") == w["posted"]
    return _line(PASS if ok else FAIL, detail)


def doc_honesty(product: Path) -> Dict[str, Any]:
    docs = [product / "README.md", *sorted((product / "docs").rglob("*.md"))]
    claims = []
    for path in docs:
        if path.is_file():
            for m in _PERCENT.finditer(path.read_text(encoding="utf-8", errors="replace")):
                claims.append(f"{path.relative_to(product).as_posix()}: {m.group(0)}")
    return _line(FAIL if claims else PASS, claims[:10] or f"{len(docs)} product doc(s), no percentage claim")


def live(base_url: Optional[str], suite: str) -> Dict[str, Any]:
    """A suite that needs a deployed instance. WITHHELD (declared) without one."""
    if base_url:
        return _line(PASS, "run against " + base_url)
    blockers = json.loads(BLOCKERS.read_text(encoding="utf-8")) if BLOCKERS.is_file() else {}
    entry = (blockers.get("live_suites") or {})
    return _line(WITHHELD, {"suite": suite, "needs": entry.get("needs"), "declared_in": "artifacts/blockers.json"})


def run(blueprint: Path, product: Optional[Path] = None, blocks_root: Optional[str] = None,
        base_url: Optional[str] = None) -> Dict[str, Any]:
    lines: Dict[str, Any] = {"determinism": determinism(blueprint, blocks_root)}
    with tempfile.TemporaryDirectory() as tmp:
        tree = product or Path(tmp) / "product"
        if product is None:
            generate(blueprint, tree, blocks_root)
        lines["store_runtime"] = store_runtime(tree)
        lines["agent_scope"] = agent_scope(tree)
        lines["workflow_approvals"] = workflow_approvals(tree)
        lines["doc_honesty"] = doc_honesty(tree)
    lines["live"] = live(base_url, "deployed-instance suites")
    return {"blueprint": str(blueprint), "lines": lines}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--blueprint", type=Path, required=True)
    ap.add_argument("--product", type=Path, default=None, help="an already generated tree")
    ap.add_argument("--blocks-root", default=None)
    ap.add_argument("--base-url", default=None)
    args = ap.parse_args(argv)
    report = run(args.blueprint.resolve(), args.product, args.blocks_root, args.base_url)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if any(v["status"] == FAIL for v in report["lines"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
