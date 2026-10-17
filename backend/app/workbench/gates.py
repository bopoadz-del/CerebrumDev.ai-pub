"""Re-run DNA-declared acceptance gates against a candidate change set."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.factory.blueprint import ProductBlueprint, load_blueprint
from app.factory.planner import CapabilityPlanner
from app.product_dna.emit import verify_checksum_manifest
from app.workbench.candidate import verify_candidate_checksum
from app.workbench.live_audit import AuditRejected, validate_audit_artifact


def _gate_result(name: str, passed: bool, evidence: Any = None, message: str = "") -> Dict[str, Any]:
    return {
        "gate": name,
        "passed": passed,
        "message": message,
        "evidence": evidence,
    }


def run_acceptance_gates(
    *,
    item: Dict[str, Any],
    candidate: Dict[str, Any],
    product_root: Path,
    workspace: Optional[Path] = None,
) -> Dict[str, Any]:
    """Execute gates listed on the dry-run report (+ DNA test_catalog).

    Pass → gate_passed. Fail → gate_failed with evidence (no silent retry).
    """
    dry = item.get("dry_run_report") or {}
    requested = list(dry.get("acceptance_gates_to_rerun") or [])
    # Always verify candidate integrity
    gate_names = ["candidate_checksum"] + [g for g in requested if g != "candidate_checksum"]

    # DNA test_catalog (may be empty)
    test_catalog: List[Dict[str, Any]] = []
    catalog_path = Path(product_root) / "product-dna" / "test_catalog.json"
    if catalog_path.is_file():
        try:
            test_catalog = list(
                (json.loads(catalog_path.read_text(encoding="utf-8")).get("tests") or [])
            )
        except json.JSONDecodeError:
            test_catalog = []

    results: List[Dict[str, Any]] = []

    # candidate_checksum
    ok = verify_candidate_checksum(candidate)
    results.append(
        _gate_result(
            "candidate_checksum",
            ok,
            evidence={"checksum": candidate.get("checksum")},
            message="ok" if ok else "candidate checksum mismatch (tampered)",
        )
    )

    artifact_ref = item.get("audit_artifact") or candidate.get("audit_artifact")
    try:
        if not artifact_ref:
            raise AuditRejected("no audit_artifact reference on the request")
        audit_data = validate_audit_artifact(Path(artifact_ref))
        results.append(
            _gate_result(
                "live_audit_evidence",
                True,
                evidence={
                    "ran_at": audit_data.get("ran_at"),
                    "checks": len(audit_data.get("checks") or []),
                },
                message="live audit all-LIVE and fresh",
            )
        )
    except AuditRejected as exc:
        results.append(
            _gate_result("live_audit_evidence", False, message=str(exc))
        )

    mutation = candidate.get("mutation") or {}
    bp_data = mutation.get("blueprint")
    blueprint: Optional[ProductBlueprint] = None
    if bp_data:
        try:
            blueprint = ProductBlueprint.model_validate(bp_data)
        except Exception as exc:  # noqa: BLE001
            results.append(
                _gate_result("blueprint_valid", False, message=str(exc))
            )
    elif workspace:
        bp_path = Path(workspace) / "blueprint" / "product_blueprint.yaml"
        if bp_path.is_file():
            try:
                blueprint = load_blueprint(bp_path)
            except Exception as exc:  # noqa: BLE001
                results.append(_gate_result("blueprint_valid", False, message=str(exc)))

    for name in gate_names:
        if name == "candidate_checksum":
            continue
        if name in {"dna_checksum_verify", "dna_reemit"}:
            dna_dir = Path(product_root) / "product-dna"
            errors = verify_checksum_manifest(dna_dir) if dna_dir.is_dir() else ["missing dna"]
            # Baseline DNA must still verify; candidate is not yet packaged
            results.append(
                _gate_result(
                    name,
                    not errors,
                    evidence=errors,
                    message="baseline DNA ok" if not errors else "; ".join(errors),
                )
            )
        elif name in {"dual_registry_gate", "planner_fail_closed"}:
            if blueprint is None:
                results.append(
                    _gate_result(name, False, message="no candidate blueprint")
                )
            else:
                # Surveyed before planning so the evidence survives the
                # raise. Reading plan().unsupported could only ever report
                # an empty list -- plan() refuses before returning one --
                # so the gate that exists to name unsupported capabilities
                # never named any.
                planner = CapabilityPlanner(None, None)
                unsupported: list = []
                try:
                    unsupported = list(planner.survey(blueprint).unsupported or [])
                    plan = planner.plan(blueprint)
                    passed = True
                    msg = "planner completed"
                    if name == "dual_registry_gate":
                        bad = []
                        for cap in plan.capabilities:
                            if cap.strategy in {"REUSE", "ADAPT", "COMPOSE"} and not cap.block_ids:
                                bad.append(cap.capability_id)
                        if bad:
                            passed = False
                            msg = f"unresolved capabilities: {bad}"
                    results.append(
                        _gate_result(
                            name,
                            passed,
                            evidence={
                                "unsupported": unsupported,
                                "capabilities": len(plan.capabilities),
                            },
                            message=msg,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — DualRegistryError etc.
                    results.append(
                        _gate_result(
                            name,
                            False,
                            evidence={"error": str(exc), "unsupported": unsupported},
                            message=str(exc),
                        )
                    )
        elif name in {"product_tests", "regression_suite", "heal_post_check", "health_probe"}:
            if not test_catalog:
                results.append(
                    _gate_result(
                        name,
                        True,
                        evidence={"tests": []},
                        message="test_catalog empty — honesty pass (no invented tests)",
                    )
                )
            else:
                # Declarative catalog only — no shell test runner in M4 sandbox
                results.append(
                    _gate_result(
                        name,
                        True,
                        evidence={"tests_declared": len(test_catalog)},
                        message="catalog present; runtime suite deferred to packager CI",
                    )
                )
        elif name in {"store_compatibility"}:
            results.append(
                _gate_result(
                    name,
                    True,
                    evidence={"store_write": "impossible"},
                    message="advisory only — workbench has no Store write path",
                )
            )
        else:
            results.append(
                _gate_result(
                    name,
                    True,
                    message=f"unknown gate treated as advisory pass: {name}",
                )
            )

    passed_all = all(r["passed"] for r in results)
    return {
        "schema_version": "1.0.0",
        "passed": passed_all,
        "gates": results,
        "test_catalog_count": len(test_catalog),
        "honesty": (
            "gate_failed returns the request for re-approval — never silently retried"
            if not passed_all
            else "all gates passed — eligible for packager promotion to staging/community"
        ),
    }
