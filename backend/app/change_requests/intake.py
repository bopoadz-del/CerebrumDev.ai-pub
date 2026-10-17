"""Factory intake pipeline: validate → sign-check → queue → dry-run → awaiting-approval."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from app.change_requests.dry_run import evaluate_dry_run
from app.change_requests.flags import change_request_intake_enabled
from app.change_requests.queue import ChangeRequestQueue, log_rejection
from app.change_requests.signing import SignatureError, load_public_key, verify_document
from app.change_requests.validate import validate_change_request
from app.change_requests.workspace import create_workspace_record

logger = logging.getLogger(__name__)


class IntakeRejected(Exception):
    def __init__(self, reason: str, status_code: int = 400):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def intake_change_request(
    document: Dict[str, Any],
    *,
    queue: Optional[ChangeRequestQueue] = None,
    dna_bundle: Optional[Dict[str, Any]] = None,
    product_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Receive a signed change-request. Dry-run only — no execution."""
    if not change_request_intake_enabled():
        log_rejection("intake_disabled", {"request_id": document.get("request_id")})
        raise IntakeRejected("CHANGE_REQUEST_INTAKE_ENABLED is false", status_code=503)

    q = queue or ChangeRequestQueue()

    # Schema first (unknown fields rejected)
    errors = validate_change_request(document)
    if errors:
        log_rejection("schema_invalid", {"errors": errors, "request_id": document.get("request_id")})
        raise IntakeRejected("schema validation failed: " + "; ".join(errors))

    # Signature
    try:
        product_id = str(document["product_id"])
        key_id = (document.get("signature") or {}).get("public_key_id")
        pub = load_public_key(product_id, key_id)
        verify_document(document, public_key_b64=pub)
    except SignatureError as exc:
        log_rejection(
            "signature_invalid",
            {"reason": str(exc), "request_id": document.get("request_id")},
        )
        raise IntakeRejected(f"signature rejected: {exc}", status_code=401) from exc

    item = q.put_received(document)
    # Idempotent re-delivery: if already past received, return as-is
    if item.get("state") != "received" and item.get("dry_run_report"):
        return item

    q.update_state(item["request_id"], "validated", validation_errors=[])

    if dna_bundle is None and product_root is not None:
        try:
            from app.resident_engineer.dna_loader import load_product_dna

            dna_bundle = load_product_dna(product_root, verify=False)
        except Exception as exc:  # noqa: BLE001
            logger.info("dna not loaded for dry-run: %s", exc)

    report = evaluate_dry_run(document, dna_bundle=dna_bundle)
    q.update_state(item["request_id"], "dry_run_evaluated", dry_run_report=report)

    workspace = create_workspace_record(
        str(document["request_id"]),
        str(document["product_id"]),
        product_root=str(product_root) if product_root else None,
    )
    item = q.update_state(
        item["request_id"],
        "awaiting_approval",
        workspace=workspace,
        dry_run_report=report,
    )
    return item
