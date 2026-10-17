"""Load + validate an approved change-request (schema shape, signature present).

Signature *verification* (ed25519) happens at Factory intake; the runtime only
accepts requests that arrived signed — matching the spec: "signature present".
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from . import stop

KINDS = {"REPAIR", "EXPANSION", "UPGRADE"}
REQUIRED = ("schema_version", "request_id", "kind", "product_id", "dna_version", "requester", "evidence", "requested_autonomy_level", "created_at", "signature")


@dataclass(frozen=True)
class ChangeRequest:
    raw: Dict[str, Any]
    request_id: str
    kind: str
    product_id: str
    dna_version: str


def load(cr_path: str) -> ChangeRequest:
    p = Path(cr_path)
    if not p.is_file():
        stop.halt("cr_missing", {"path": cr_path}, "Provide the approved change-request path.")
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        stop.halt("cr_unparsable", {"error": str(exc)}, "Fix the change-request JSON.")
    if not isinstance(doc, dict):
        stop.halt("cr_invalid", {"error": "root not an object"}, "Re-issue the request.")
    missing = [k for k in REQUIRED if k not in doc]
    if missing:
        stop.halt("cr_invalid", {"missing": missing}, "Re-issue with all required fields.")
    if doc["kind"] not in KINDS:
        stop.halt("cr_invalid", {"kind": doc["kind"]}, f"kind in {sorted(KINDS)}.")
    sig = doc.get("signature")
    if not isinstance(sig, dict) or not all(sig.get(k) for k in ("algorithm", "public_key_id", "value")):
        stop.halt("cr_unsigned", {}, "Only approved (signed) change-requests run — sign at intake.")
    return ChangeRequest(raw=doc, request_id=str(doc["request_id"]), kind=str(doc["kind"]), product_id=str(doc["product_id"]), dna_version=str(doc["dna_version"]))
