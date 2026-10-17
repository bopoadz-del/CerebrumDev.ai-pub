"""Builders for signed change-request documents (schema v1)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from app.change_requests.signing import (
    generate_keypair,
    public_key_id_for,
    register_public_key,
    sign_document,
)
from app.change_requests.validate import validate_or_raise
from app.resident_engineer.modes import AutonomyLevel

RequesterType = Literal["resident", "human"]
Autonomy = Union[AutonomyLevel, str]
SCHEMA_VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_repair_request(
    *,
    product_id: str,
    dna_version: str,
    requester_type: RequesterType,
    requester_id: str,
    target: str,
    symptom: str,
    evidence: Optional[Dict[str, Any]] = None,
    requested_autonomy_level: Autonomy = AutonomyLevel.L3.value,
    failed_action_id: Optional[str] = None,
    dna_entity_refs: Optional[List[str]] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    repair: Dict[str, Any] = {"target": target, "symptom": symptom}
    if failed_action_id:
        repair["failed_action_id"] = failed_action_id
    if dna_entity_refs is not None:
        repair["dna_entity_refs"] = list(dna_entity_refs)
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or str(uuid.uuid4()),
        "kind": "REPAIR",
        "product_id": product_id,
        "dna_version": dna_version,
        "requester": {"type": requester_type, "id": requester_id},
        "evidence": evidence or {},
        "requested_autonomy_level": requested_autonomy_level,
        "created_at": _now(),
        "repair": repair,
    }


def build_expansion_request(
    *,
    product_id: str,
    dna_version: str,
    requester_type: RequesterType,
    requester_id: str,
    capability: str,
    evidence: Optional[Dict[str, Any]] = None,
    requested_autonomy_level: Autonomy = AutonomyLevel.L4.value,
    block_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    hat_id: Optional[str] = None,
    rationale: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    expansion: Dict[str, Any] = {"capability": capability}
    if block_id:
        expansion["block_id"] = block_id
    if endpoint:
        expansion["endpoint"] = endpoint
    if hat_id:
        expansion["hat_id"] = hat_id
    if rationale:
        expansion["rationale"] = rationale
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or str(uuid.uuid4()),
        "kind": "EXPANSION",
        "product_id": product_id,
        "dna_version": dna_version,
        "requester": {"type": requester_type, "id": requester_id},
        "evidence": evidence or {},
        "requested_autonomy_level": requested_autonomy_level,
        "created_at": _now(),
        "expansion": expansion,
    }


def build_upgrade_request(
    *,
    product_id: str,
    dna_version: str,
    requester_type: RequesterType,
    requester_id: str,
    component: str,
    from_version: str,
    to_version: str,
    evidence: Optional[Dict[str, Any]] = None,
    requested_autonomy_level: Autonomy = AutonomyLevel.L3.value,
    component_kind: str = "block",
    changelog: Optional[str] = None,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    upgrade: Dict[str, Any] = {
        "component": component,
        "component_kind": component_kind,
        "from_version": from_version,
        "to_version": to_version,
    }
    if changelog:
        upgrade["changelog"] = changelog
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or str(uuid.uuid4()),
        "kind": "UPGRADE",
        "product_id": product_id,
        "dna_version": dna_version,
        "requester": {"type": requester_type, "id": requester_id},
        "evidence": evidence or {},
        "requested_autonomy_level": requested_autonomy_level,
        "created_at": _now(),
        "upgrade": upgrade,
    }


def sign_and_register(
    document: Dict[str, Any],
    *,
    private_key_b64: Optional[str] = None,
    public_key_b64: Optional[str] = None,
) -> Dict[str, Any]:
    """Sign document; register public key for product if newly generated."""
    product_id = str(document["product_id"])
    if private_key_b64 and public_key_b64:
        priv, pub = private_key_b64, public_key_b64
    else:
        priv, pub = generate_keypair()
    key_id = public_key_id_for(product_id, pub)
    register_public_key(product_id, pub, key_id)
    signed = sign_document(document, private_key_b64=priv, public_key_id=key_id)
    validate_or_raise(signed)
    return signed
