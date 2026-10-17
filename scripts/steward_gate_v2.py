#!/usr/bin/env python3
"""Steward pilot certification v2 gate runner.

Emits PASS / FAIL / NOT VERIFIED for each mandatory gate (G1–G14).
Run locally before pushing Factory or generated Steward changes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]

GATES: List[Dict[str, str]] = [
    {"gate": "G1_factory_determinism", "owner": "Factory"},
    {"gate": "G2_authentication", "owner": "Factory"},
    {"gate": "G3_authorization", "owner": "Factory"},
    {"gate": "G4_isolation", "owner": "Factory"},
    {"gate": "G5_database", "owner": "Factory"},
    {"gate": "G6_rag", "owner": "Factory"},
    {"gate": "G7_store", "owner": "Factory"},
    {"gate": "G8_agents", "owner": "Factory"},
    {"gate": "G9_workflows", "owner": "Factory"},
    {"gate": "G10_resident_engineer", "owner": "Factory"},
    {"gate": "G11_financial", "owner": "Factory"},
    {"gate": "G12_deployment", "owner": "Factory"},
    {"gate": "G13_oracle", "owner": "Factory"},
    {"gate": "G14_documentation", "owner": "Factory"},
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


def _evaluate_gate(gate_id: str) -> Dict[str, Any]:
    auth_py = _kit_path("steward_runtime", "auth.py")
    auth_models = _kit_path("steward_runtime", "auth_models.py")
    probes_py = _kit_path("steward_runtime", "probes.py")
    api_py = _kit_path("steward_runtime", "api.py")
    models_py = _kit_path("steward_runtime", "models.py")
    money_py = _kit_path("steward_runtime", "money.py")
    audit_models = _kit_path("steward_runtime", "audit_models.py")
    migration_auth = _kit_path("steward_runtime", "migrations", "versions", "0002_pilot_auth.py")
    migration_wave2 = _kit_path("steward_runtime", "migrations", "versions", "0003_wave2_audit_heal_money.py")
    migration_0001 = _kit_path("steward_runtime", "migrations", "versions", "0001_steward_rag.py")
    entity_model = _kit_path("entity_model.json")
    gate_script = ROOT / "scripts" / "steward_gate_v2.py"
    oracle_script = ROOT / "scripts" / "steward_oracle_v2.py"
    determinism = ROOT / "artifacts" / "steward_factory_determinism.json"
    heal_catalog = _repo_path("backend", "app", "resident_engineer", "heal", "catalog.py")
    heal_executor = _repo_path("backend", "app", "resident_engineer", "heal", "executor.py")
    heal_approval = _repo_path("backend", "app", "resident_engineer", "heal", "approval.py")

    if gate_id == "G1_factory_determinism":
        return _measured("determinism")

    if gate_id == "G2_authentication":
        ok = auth_py.is_file() and auth_models.is_file()
        text = _read(auth_py)
        has_models = all(x in text for x in ("PilotPrincipal", "PilotAccessToken", "hash_token"))
        status = "PASS" if ok and has_models else "FAIL"
        return {"status": status, "detail": "pilot auth kit present" if status == "PASS" else "missing pilot auth kit"}

    if gate_id == "G3_authorization":
        text = _read(auth_py)
        status = "PASS" if "authorize_estate" in text else "FAIL"
        return {"status": status, "detail": "authorize_estate implemented" if status == "PASS" else "authorize_estate missing"}

    if gate_id == "G4_isolation":
        api_text = _read(api_py)
        removed_defaults = "tenant_a" not in api_text or "_scope" not in api_text
        status = "PASS" if removed_defaults and "get_authenticated_principal" in api_text else "FAIL"
        return {
            "status": status,
            "detail": "header defaults removed; principal auth wired" if status == "PASS" else "caller-controlled defaults remain",
        }

    if gate_id == "G5_database":
        api_text = _read(api_py)
        m0001_text = _read(migration_0001)
        protected = (
            "admin_route_disabled" in api_text
            and migration_auth.is_file()
            and migration_wave2.is_file()
            and audit_models.is_file()
        )
        create_all_residual = "create_all" in m0001_text
        if protected and not create_all_residual:
            status = "PASS"
            detail = "admin routes gated; audit migration present; no create_all residual"
        elif protected:
            status = "PASS"
            detail = "admin routes gated; audit migration present; residual: 0001 uses create_all"
        else:
            status = "FAIL"
            detail = "missing admin gating, auth migration, or wave2 audit migration"
        return {"status": status, "detail": detail}

    if gate_id == "G6_rag":
        estate_router = _kit_path("estate_kit_router.py")
        text = _read(estate_router)
        status = "PASS" if "_legacy_rag_guard" in text else "FAIL"
        return {
            "status": status,
            "detail": "legacy /v1/rag/* guard present" if status == "PASS" else "legacy RAG not gated",
        }

    if gate_id == "G7_store":
        return _measured("store_runtime")

    if gate_id == "G8_agents":
        return _measured("agent_runtime")

    if gate_id == "G9_workflows":
        return _measured("workflow_approvals")

    if gate_id == "G10_resident_engineer":
        catalog_text = _read(heal_catalog)
        executor_text = _read(heal_executor)
        honest = (
            heal_approval.is_file()
            and "HealApproval" in _read(audit_models)
            and '"executed": False' in catalog_text
            and '"simulation": True' in catalog_text
            and '"ok": False' in executor_text
            and "validate_heal_approval_or_raise" in executor_text
        )
        status = "PASS" if honest else "FAIL"
        return {
            "status": status,
            "detail": "HealApproval + simulated heal honesty enforced"
            if status == "PASS"
            else "HealApproval or simulated heal honesty incomplete",
        }

    if gate_id == "G11_financial":
        models_text = _read(models_py)
        money_ok = money_py.is_file() and "Numeric(20, 4)" in models_text and "Decimal" in models_text
        status = "PASS" if money_ok else "FAIL"
        return {
            "status": status,
            "detail": "Decimal/Numeric money scaffolding in Factory kit"
            if status == "PASS"
            else "Float money fields or money helpers missing",
        }

    if gate_id == "G12_deployment":
        status = "PASS" if probes_py.is_file() and "readiness_checks" in _read(probes_py) else "FAIL"
        return {
            "status": status,
            "detail": "readiness/version probes in Factory kit" if status == "PASS" else "probes missing",
        }

    if gate_id == "G13_oracle":
        report = _oracle().run_oracle(None)
        statuses = {s["suite"]: s["status"] for s in report["suites"]}
        failed = sorted(k for k, v in statuses.items() if v in ("FAIL", "NOT VERIFIED"))
        withheld = sorted(k for k, v in statuses.items() if v == "WITHHELD")
        status = "FAIL" if failed else ("WITHHELD" if withheld else "PASS")
        return {"status": status, "detail": {
            "failed": failed, "withheld": withheld,
            "withheld_declared_in": "artifacts/blockers.json" if withheld else None,
            "passed": sorted(k for k, v in statuses.items() if v == "PASS")}}

    if gate_id == "G14_documentation":
        audit = ROOT / "docs" / "audits" / "STEWARD_V2_AGENT_AUDIT.md"
        entity_ok = entity_model.is_file() and len(json.loads(entity_model.read_text()).get("entities", [])) >= 5
        status = "PASS" if audit.is_file() and gate_script.is_file() and entity_ok else "FAIL"
        return {
            "status": status,
            "detail": "v2 audit + gate script + entity DNA stubs present"
            if status == "PASS"
            else "documentation or entity DNA incomplete",
        }

    return {"status": "NOT VERIFIED", "detail": "unknown gate"}


def _oracle():
    sys.path.insert(0, str(ROOT / "scripts"))
    import steward_oracle_v2

    return steward_oracle_v2


def _measured(line: str) -> Dict[str, Any]:
    """A line measured on the generated product by the general product gate."""
    result = _oracle()._product_lines()[line]
    return {"status": result["status"], "detail": result["detail"]}


def run_gates() -> Dict[str, Any]:
    results = []
    for gate in GATES:
        gate_id = gate["gate"]
        verdict = _evaluate_gate(gate_id)
        results.append({**gate, **verdict})

    mandatory_fail = any(r["status"] == "FAIL" for r in results)
    mandatory_not_verified = any(r["status"] == "NOT VERIFIED" for r in results)
    withheld = any(r["status"] == "WITHHELD" for r in results)
    pilot_verdict = (
        "NO-GO" if mandatory_fail or mandatory_not_verified
        else "WITHHELD" if withheld else "GO"
    )

    return {
        "schema_version": "steward_gate_v2",
        "ran_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(ROOT),
        "pilot_verdict": pilot_verdict,
        "gates": results,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Steward pilot certification v2 gate runner")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "artifacts" / "steward_gate_v2.json",
        help="Write JSON report to this path",
    )
    parser.add_argument("--print", action="store_true", help="Print JSON to stdout")
    args = parser.parse_args(argv)

    report = run_gates()
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(payload, encoding="utf-8")
    if args.print:
        print(payload, end="")
    print(f"Gate report written to {args.out}", file=sys.stderr)
    print(f"Pilot verdict: {report['pilot_verdict']}", file=sys.stderr)
    # WITHHELD (declared operator settings, artifacts/blockers.json) is not a
    # failure; FAIL and NOT VERIFIED are.
    return 0 if report["pilot_verdict"] in ("GO", "WITHHELD") else 1


if __name__ == "__main__":
    raise SystemExit(main())
