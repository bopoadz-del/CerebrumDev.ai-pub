"""Structured failure reports referencing DNA entities."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.resident_engineer.dna_loader import dna_entity_refs, load_product_dna
from app.resident_engineer.modes import AutonomyLevel
from app.resident_engineer.injection_guard import strip_instruction_patterns


def build_failure_report(
    *,
    product_root=None,
    symptom: str,
    failed_checks: Optional[List[Dict[str, Any]]] = None,
    related_action_ids: Optional[List[str]] = None,
    related_capability_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Produce a diagnosis document tied to DNA catalogs (untrusted text sanitized)."""
    bundle = load_product_dna(product_root)
    refs = dna_entity_refs(bundle)
    return {
        "schema_version": "1.0.0",
        "kind": "failure_report",
        "product_id": refs.get("product_id"),
        "symptom": strip_instruction_patterns(symptom or ""),
        "failed_checks": failed_checks or [],
        "dna_refs": {
            "actions": related_action_ids
            or [a for a in (refs.get("action_ids") or []) if a][:20],
            "capabilities": related_capability_ids
            or [c for c in (refs.get("capability_ids") or []) if c][:20],
            "agents": [a for a in (refs.get("agent_ids") or []) if a][:20],
            "workflows": [w for w in (refs.get("workflow_ids") or []) if w][:20],
        },
        "recommended_level": (AutonomyLevel.L2 if related_action_ids else AutonomyLevel.L1).value,
    }
