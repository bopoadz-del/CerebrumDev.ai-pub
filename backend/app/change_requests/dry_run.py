"""Dry-run evaluator — report what WOULD change; never mutates the product."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.resident_engineer.injection_guard import strip_instruction_patterns
from app.resident_engineer.modes import AutonomyLevel


def evaluate_dry_run(
    request: Dict[str, Any],
    *,
    dna_bundle: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Produce an evaluation report attached to the queue item.

    No filesystem mutation of the product. Diffs are declarative against DNA.
    """
    kind = request.get("kind")
    files = (dna_bundle or {}).get("files") or {}
    dna_version = request.get("dna_version")
    autonomy = request.get("requested_autonomy_level")
    would_change: List[Dict[str, Any]] = []
    risk_flags: List[Dict[str, Any]] = []
    gates: List[str] = []

    if kind == "REPAIR":
        repair = request.get("repair") or {}
        target = strip_instruction_patterns(str(repair.get("target") or ""))
        would_change.append(
            {
                "surface": "config_or_runtime",
                "target": target,
                "action": "restore_declared_state",
                "against_dna": "security_policy.json / architecture.json",
            }
        )
        if autonomy in {AutonomyLevel.L4.value, AutonomyLevel.L5.value}:
            risk_flags.append(
                {
                    "code": "high_autonomy_repair",
                    "severity": "high",
                    "message": f"REPAIR requested at {autonomy}; human approval required",
                }
            )
        gates.extend(["dna_checksum_verify", "health_probe", "heal_post_check"])
    elif kind == "EXPANSION":
        expansion = request.get("expansion") or {}
        would_change.append(
            {
                "surface": "capability",
                "capability": expansion.get("capability"),
                "block_id": expansion.get("block_id"),
                "action": "add_capability_via_factory_regenerate",
                "against_dna": "action_catalog.json / capability_resolution.json",
            }
        )
        risk_flags.append(
            {
                "code": "expansion_requires_regenerate",
                "severity": "medium",
                "message": "EXPANSION must go through Factory packager (M4); never hand-edit",
            }
        )
        gates.extend(["dual_registry_gate", "planner_fail_closed", "dna_reemit", "product_tests"])
    elif kind == "UPGRADE":
        upgrade = request.get("upgrade") or {}
        would_change.append(
            {
                "surface": upgrade.get("component_kind") or "block",
                "component": upgrade.get("component"),
                "from_version": upgrade.get("from_version"),
                "to_version": upgrade.get("to_version"),
                "action": "version_bump_via_factory",
                "against_dna": "block_lockfile.json",
            }
        )
        gates.extend(["store_compatibility", "dna_reemit", "regression_suite"])
    else:
        risk_flags.append({"code": "unknown_kind", "severity": "high", "message": str(kind)})

    # Honest: if DNA missing catalogs, say so
    if dna_bundle is None:
        risk_flags.append(
            {
                "code": "dna_not_loaded",
                "severity": "info",
                "message": "Dry-run without product DNA context; diffs are declarative only",
            }
        )
    else:
        lock = files.get("block_lockfile.json") or {}
        would_change.append(
            {
                "surface": "reference",
                "block_pins": len(lock.get("pins") or []),
                "dna_version_observed": dna_version,
            }
        )

    return {
        "schema_version": "1.0.0",
        "kind": kind,
        "request_id": request.get("request_id"),
        "product_id": request.get("product_id"),
        "would_change": would_change,
        "required_autonomy_level": autonomy,
        "risk_flags": risk_flags,
        "acceptance_gates_to_rerun": gates,
        "mutates_product": False,
        "honesty": "dry-run only — no files/blocks/config were modified",
    }
