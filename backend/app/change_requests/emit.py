"""Resident / product-side emission of signed change-requests (flag-gated)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.change_requests.builders import build_repair_request, sign_and_register
from app.change_requests.flags import resident_emit_change_requests_enabled
from app.change_requests.intake import IntakeRejected, intake_change_request
from app.change_requests.signing import signing_key_from_env
from app.resident_engineer.modes import AutonomyLevel
from app.resident_engineer.injection_guard import strip_instruction_patterns


class EmitRejected(Exception):
    pass


def emit_repair_from_escalation(
    *,
    product_id: str,
    dna_version: str,
    symptom: str,
    target: str = "runtime",
    failed_action_id: Optional[str] = None,
    evidence: Optional[Dict[str, Any]] = None,
    requester_id: str = "resident_engineer",
    private_key_b64: Optional[str] = None,
    public_key_b64: Optional[str] = None,
    submit_to_intake: bool = True,
) -> Dict[str, Any]:
    """Turn an L2 dead-end into a signed REPAIR request.

    Off by default (``RESIDENT_EMIT_CHANGE_REQUESTS``). Optionally submits to
    Factory intake when ``CHANGE_REQUEST_INTAKE_ENABLED`` is also true.
    """
    if not resident_emit_change_requests_enabled():
        raise EmitRejected("RESIDENT_EMIT_CHANGE_REQUESTS is false")

    doc = build_repair_request(
        product_id=product_id,
        dna_version=dna_version,
        requester_type="resident",
        requester_id=requester_id,
        target=target,
        symptom=strip_instruction_patterns(symptom),
        evidence=evidence or {},
        requested_autonomy_level=AutonomyLevel.L3.value,
        failed_action_id=failed_action_id,
    )

    # Prefer explicit keys; else env private key requires matching registered pub
    # (tests pass both). If only env private key, generate pair path is used.
    env_priv = private_key_b64 or signing_key_from_env()
    if env_priv and public_key_b64:
        signed = sign_and_register(doc, private_key_b64=env_priv, public_key_b64=public_key_b64)
    else:
        signed = sign_and_register(doc)

    result: Dict[str, Any] = {"signed_request": signed, "submitted": False, "queue_item": None}
    if submit_to_intake:
        try:
            item = intake_change_request(signed)
            result["submitted"] = True
            result["queue_item"] = item
        except IntakeRejected as exc:
            result["intake_error"] = str(exc.reason)
    return result
