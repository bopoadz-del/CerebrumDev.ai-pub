#!/usr/bin/env python3
"""Steward Oracle V2 runner.

Static Factory-kit checks run without a live URL. Live suites require --base-url.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from urllib.error import URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
#: The blueprint this oracle certifies; its lines are the general product gate's.
BLUEPRINT = ROOT / "blueprints" / "steward" / "steward.v1.yaml"

SUITES: List[Dict[str, str]] = [
    {"suite": "A", "name": "health_and_version", "mandatory": "true"},
    {"suite": "B", "name": "readiness_fail_closed", "mandatory": "true"},
    {"suite": "C", "name": "authentication_anonymous_denied", "mandatory": "true"},
    {"suite": "D", "name": "authorization_cross_estate_denied", "mandatory": "true"},
    {"suite": "E", "name": "canonical_rag_query", "mandatory": "true"},
    {"suite": "F", "name": "legacy_rag_disabled", "mandatory": "true"},
    {"suite": "G", "name": "ingest_authz", "mandatory": "true"},
    {"suite": "H", "name": "admin_routes_disabled", "mandatory": "true"},
    {"suite": "I", "name": "insufficiency_policy", "mandatory": "true"},
    {"suite": "J", "name": "workflow_oracles", "mandatory": "true"},
    {"suite": "K", "name": "agent_runtime", "mandatory": "true"},
    {"suite": "L", "name": "resident_engineer_security", "mandatory": "true"},
    {"suite": "M", "name": "financial_integrity", "mandatory": "true"},
    {"suite": "N", "name": "store_execution", "mandatory": "true"},
    {"suite": "O", "name": "provenance_and_version_pin", "mandatory": "true"},
    {"suite": "P", "name": "determinism", "mandatory": "true"},
    {"suite": "Q", "name": "documentation_honesty", "mandatory": "true"},
    {"suite": "R", "name": "deployment_readiness_live", "mandatory": "true"},
]


def _kit_path(*parts: str) -> Path:
    # The kit is the Store's (the Factory holds no kits): resolve it by
    # registry id under the Store checkout the Factory builds against.
    store = Path(os.environ.get("CEREBRUM_BLOCKS_ROOT") or ROOT.parent / "Cerebrum-Blocks")
    return store / "block_store" / "kits" / "private_estate_operations" / Path(*parts)


def _repo_path(*parts: str) -> Path:
    return ROOT.joinpath(*parts)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _evaluate_static_suite(suite_id: str) -> Dict[str, Any]:
    api_py = _kit_path("steward_runtime", "api.py")
    models_py = _kit_path("steward_runtime", "models.py")
    money_py = _kit_path("steward_runtime", "money.py")
    audit_models = _kit_path("steward_runtime", "audit_models.py")
    estate_router = _kit_path("estate_kit_router.py")
    heal_catalog = _repo_path("backend", "app", "resident_engineer", "heal", "catalog.py")
    heal_executor = _repo_path("backend", "app", "resident_engineer", "heal", "executor.py")
    entity_model = _kit_path("entity_model.json")
    determinism = ROOT / "artifacts" / "steward_factory_determinism.json"
    audit_doc = ROOT / "docs" / "audits" / "STEWARD_V2_AGENT_AUDIT.md"

    if suite_id == "F":
        ok = "_legacy_rag_guard" in _read(estate_router)
        return {"status": "PASS" if ok else "FAIL", "detail": "legacy RAG guard in estate kit router"}

    if suite_id == "H":
        ok = "admin_route_disabled" in _read(api_py)
        return {"status": "PASS" if ok else "FAIL", "detail": "admin routes fail-closed in steward API kit"}

    if suite_id == "L":
        text = _read(heal_catalog) + _read(heal_executor) + _read(audit_models)
        ok = (
            "HealApproval" in text
            and '"executed": False' in _read(heal_catalog)
            and '"simulation": True' in _read(heal_catalog)
            and '"ok": False' in _read(heal_executor)
        )
        return {
            "status": "PASS" if ok else "FAIL",
            "detail": "simulated heals honest; HealApproval model present",
        }

    if suite_id == "M":
        ok = money_py.is_file() and "Numeric(20, 4)" in _read(models_py)
        if ok:
            from decimal import Decimal

            from importlib.util import spec_from_loader, module_from_spec
            import importlib.util

            spec = importlib.util.spec_from_file_location("steward_money", money_py)
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            exact = mod.money_add(Decimal("0.1"), Decimal("0.2")) == Decimal("0.3000")
            ok = exact
        return {
            "status": "PASS" if ok else "FAIL",
            "detail": "Decimal money scaffolding exact-add verified" if ok else "money scaffolding incomplete",
        }

    # Suites that measure a mechanism run on the GENERATED product through the
    # general product gate -- the same lines run on every blueprint.
    measured = _product_lines()
    by_suite = {"N": "store_runtime", "P": "determinism", "J": "workflow_approvals",
                "K": "agent_runtime", "Q": "doc_honesty"}
    if suite_id in by_suite:
        line = measured[by_suite[suite_id]]
        return {"status": line["status"], "detail": line["detail"]}

    # Everything else needs a DEPLOYED instance: declared, not failed.
    return _product_gate().live(None, suite_id)


def _http_get(base_url: str, path: str, headers: Dict[str, str] | None = None) -> Dict[str, Any]:
    url = base_url.rstrip("/") + path
    req = Request(url, headers=headers or {}, method="GET")
    try:
        with urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            return {
                "ok": True,
                "status": resp.status,
                "body": json.loads(body) if body else {},
            }
    except URLError as exc:
        return {"ok": False, "status": None, "error": str(exc.reason)}


_PRODUCT_LINES: Dict[str, Any] = {}


def _product_gate():
    sys.path.insert(0, str(ROOT / "scripts"))
    import product_gate

    return product_gate


def _product_lines() -> Dict[str, Any]:
    """The general product-gate lines for this oracle's blueprint, once."""
    if not _PRODUCT_LINES:
        import tempfile

        pg = _product_gate()
        _PRODUCT_LINES["determinism"] = pg.determinism(BLUEPRINT)
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "product"
            pg.generate(BLUEPRINT, tree)
            _PRODUCT_LINES["store_runtime"] = pg.store_runtime(tree)
            _PRODUCT_LINES["agent_runtime"] = pg.agent_scope(tree)
            _PRODUCT_LINES["workflow_approvals"] = pg.workflow_approvals(tree)
            _PRODUCT_LINES["doc_honesty"] = pg.doc_honesty(tree)
    return _PRODUCT_LINES


def run_oracle(base_url: str | None) -> Dict[str, Any]:
    suites: List[Dict[str, Any]] = []
    for spec in SUITES:
        suite_id = spec["suite"]
        entry: Dict[str, Any] = dict(spec)
        if base_url and suite_id == "A":
            health = _http_get(base_url, "/health")
            version = _http_get(base_url, "/version")
            ready = _http_get(base_url, "/ready")
            passed = (
                health.get("status") == 200
                and version.get("status") == 200
                and ready.get("status") in {200, 503}
            )
            entry["status"] = "PASS" if passed else "FAIL"
            entry["detail"] = {
                "health": health.get("status"),
                "version": version.get("status"),
                "ready": ready.get("status"),
            }
        elif base_url and suite_id == "R":
            ready = _http_get(base_url, "/ready")
            entry["status"] = "PASS" if ready.get("status") in {200, 503} else "FAIL"
            entry["detail"] = {"ready": ready.get("status")}
        else:
            static = _evaluate_static_suite(suite_id)
            entry.update(static)
        suites.append(entry)

    mandatory_fail = any(s["mandatory"] == "true" and s["status"] == "FAIL" for s in suites)
    mandatory_nv = any(s["mandatory"] == "true" and s["status"] == "NOT VERIFIED" for s in suites)
    withheld = any(s["status"] == "WITHHELD" for s in suites)
    verdict = "NO-GO" if mandatory_fail or mandatory_nv else ("WITHHELD" if withheld else "GO")

    return {
        "schema_version": "steward_oracle_v2",
        "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "verdict": verdict,
        "suites": suites,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Steward Oracle V2 runner")
    parser.add_argument("--base-url", default=None, help="Live Steward base URL (optional)")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "artifacts" / "steward_oracle_v2.json",
        help="Write JSON report to this path",
    )
    parser.add_argument("--print", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args(argv)

    report = run_oracle(args.base_url)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    if args.print:
        print(payload, end="")
    print(f"Oracle report written to {args.out}", file=sys.stderr)
    print(f"Verdict: {report['verdict']}", file=sys.stderr)
    return 0 if report["verdict"] == "GO" else 1


if __name__ == "__main__":
    raise SystemExit(main())
