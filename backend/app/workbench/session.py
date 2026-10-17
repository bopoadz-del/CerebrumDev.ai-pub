"""Workbench session orchestration: approved CR → sandbox → candidate → gates → promote."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from app.change_requests.queue import ChangeRequestQueue
from app.workbench.agent import AgentError, KimiCliError, run_agent
from app.workbench.audit import audit_event
from app.workbench.candidate import build_candidate_artifact
from app.workbench.envelope import EnvelopeError, build_task_envelope, load_verified_dna
from app.workbench.flags import build_mode_enabled
from app.workbench.gates import run_acceptance_gates
from app.workbench.promote import PromotionRejected, promote_via_packager
from app.workbench.sandbox import SandboxTimeout, SandboxViolation, WorkbenchSandbox


class WorkbenchRejected(Exception):
    def __init__(self, reason: str, status_code: int = 400):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


def _resolve_product_root(item: Dict[str, Any], product_root: Optional[Path]) -> Path:
    from app.factory.paths import UnsafeProductRoot, safe_workbench_product_root

    raw: Optional[Path | str] = None
    if product_root:
        raw = product_root
    else:
        evidence = (item.get("request") or {}).get("evidence") or {}
        for key in ("scaffold", "product_root", "path"):
            if evidence.get(key):
                raw = str(evidence[key])
                break
        if raw is None:
            ws = item.get("workspace") or {}
            if ws.get("product_root"):
                raw = str(ws["product_root"])
    if raw is None:
        raise WorkbenchRejected(
            "product_root required (pass explicitly or in request.evidence.scaffold)"
        )
    try:
        return safe_workbench_product_root(raw)
    except UnsafeProductRoot as exc:
        raise WorkbenchRejected(str(exc), status_code=400) from exc


def run_workbench_session(
    request_id: str,
    *,
    product_root: Optional[Path] = None,
    run_gates: bool = True,
    queue: Optional[ChangeRequestQueue] = None,
) -> Dict[str, Any]:
    """Spin sandbox for one approved change-request → candidate (+ optional gates)."""
    if not build_mode_enabled():
        raise WorkbenchRejected("BUILD_MODE_ENABLED is false", status_code=503)

    q = queue or ChangeRequestQueue()
    item = q.get(request_id)
    if not item:
        raise WorkbenchRejected("not found", status_code=404)
    if item.get("state") != "approved":
        if item.get("state") == "awaiting_approval":
            raise WorkbenchRejected("request awaits approval — approve before workbench")
        if item.get("state") == "gate_failed":
            raise WorkbenchRejected(
                "gate_failed — re-approve the request before another workbench run"
            )
        raise WorkbenchRejected(
            f"workbench requires approved request, got state={item.get('state')}"
        )

    root = _resolve_product_root(item, product_root)
    if not root.is_dir():
        raise WorkbenchRejected(f"product_root not found: {root}")

    try:
        dna_bundle = load_verified_dna(root)
    except EnvelopeError as exc:
        raise WorkbenchRejected(str(exc)) from exc

    session_id = str(uuid.uuid4())
    sandbox = WorkbenchSandbox(session_id)
    sandbox.persist_meta()

    # Seed DNA + baseline (readonly) and request
    sandbox.seed_readonly(root / "product-dna", "product-dna")
    sandbox.seed_readonly(root, "baseline")
    request = item["request"]
    sandbox.write_text(
        # request.json is readonly in envelope — seed via direct workspace write for setup
        # Use candidate path then copy? Envelope marks request.json readonly.
        # Seed by writing through workspace path before agent runs:
        "candidate/_setup_request.json",
        json.dumps(request, indent=2, sort_keys=True) + "\n",
    )
    # Place request.json at workspace root (readonly for agent after seed)
    req_path = sandbox.workspace / "request.json"
    req_path.write_text(json.dumps(request, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    envelope = build_task_envelope(
        request=request,
        dry_run=item.get("dry_run_report"),
        dna_bundle=dna_bundle,
        workspace_root=sandbox.workspace,
        product_root=root,
    )
    sandbox.write_text(
        "task_envelope.json",
        json.dumps(envelope, indent=2, sort_keys=True) + "\n",
    )

    q.update_state(
        request_id,
        "workbench_running",
        workbench_session={
            "session_id": session_id,
            "workspace": str(sandbox.workspace),
            "product_root": str(root),
            "status": "running",
        },
        workspace={
            **(item.get("workspace") or {}),
            "path": str(sandbox.workspace),
            "worktree_created": True,
            "agent_started": True,
            "product_root": str(root),
            "session_id": session_id,
        },
        audit_append=audit_event(
            "workbench_started",
            summary=f"session {session_id} started",
            details={"session_id": session_id, "product_root": str(root)},
        ),
    )

    try:
        agent_result = run_agent(sandbox, envelope=envelope, product_root=root)
        candidate = build_candidate_artifact(
            sandbox, agent_result=agent_result, product_root=root
        )
    except (SandboxViolation, SandboxTimeout, EnvelopeError, AgentError) as exc:
        gate_report: Dict[str, Any] = {
            "passed": False,
            "error": str(exc),
            "honesty": "session died loudly",
        }
        if isinstance(exc, KimiCliError):
            gate_report["kimi_error"] = exc.kimi_error
            gate_report["cli_returncode"] = exc.cli_returncode
        q.update_state(
            request_id,
            "gate_failed",
            workbench_session={
                "session_id": session_id,
                "workspace": str(sandbox.workspace),
                "status": "failed",
                "error": str(exc),
            },
            gate_report=gate_report,
            audit_append=audit_event(
                "workbench_failed",
                summary=str(exc),
                details={"session_id": session_id, "transcript_tail": sandbox.transcript[-5:]},
            ),
        )
        status = 502 if isinstance(exc, KimiCliError) else 400
        raise WorkbenchRejected(str(exc), status_code=status) from exc

    item = q.update_state(
        request_id,
        "candidate_ready",
        candidate=candidate,
        workbench_session={
            "session_id": session_id,
            "workspace": str(sandbox.workspace),
            "product_root": str(root),
            "status": "candidate_ready",
            "agent": agent_result,
        },
        audit_append=audit_event(
            "candidate_ready",
            summary=candidate.get("summary") or "candidate ready",
            details={
                "checksum": candidate.get("checksum"),
                "diff_bytes": len(candidate.get("diff") or ""),
                "session_id": session_id,
            },
        ),
    )

    if run_gates:
        item = evaluate_gates(request_id, queue=q)

    return item


def evaluate_gates(
    request_id: str,
    *,
    queue: Optional[ChangeRequestQueue] = None,
) -> Dict[str, Any]:
    if not build_mode_enabled():
        raise WorkbenchRejected("BUILD_MODE_ENABLED is false", status_code=503)
    q = queue or ChangeRequestQueue()
    item = q.get(request_id)
    if not item:
        raise WorkbenchRejected("not found", status_code=404)
    if item.get("state") not in {"candidate_ready", "gate_failed", "gate_passed"}:
        raise WorkbenchRejected(f"gates require candidate_ready, got {item.get('state')}")
    candidate = item.get("candidate")
    if not candidate:
        raise WorkbenchRejected("missing candidate artifact")

    ws = (item.get("workbench_session") or {}).get("workspace")
    product_root = Path(
        (item.get("workbench_session") or {}).get("product_root")
        or (item.get("workspace") or {}).get("product_root")
        or ""
    )
    if not product_root.is_dir():
        raise WorkbenchRejected("product_root missing on session")

    report = run_acceptance_gates(
        item=item,
        candidate=candidate,
        product_root=product_root,
        workspace=Path(ws) if ws else None,
    )

    if report["passed"]:
        return q.update_state(
            request_id,
            "gate_passed",
            gate_report=report,
            audit_append=audit_event(
                "gate_passed",
                summary="acceptance gates passed",
                details={"gates": [g["gate"] for g in report["gates"]]},
            ),
        )

    # Fail → evidence attached; return for re-approval (never silent retry)
    return q.update_state(
        request_id,
        "gate_failed",
        gate_report=report,
        decision={
            **(item.get("decision") or {}),
            "superseded_by_gate_failure": True,
            "note": "gate_failed — re-approval required before another workbench run",
        },
        audit_append=audit_event(
            "gate_failed",
            summary="acceptance gates failed — returned for re-approval",
            details={"gates": report["gates"]},
        ),
    )


def promote_request(
    request_id: str,
    *,
    actor: str = "owner",
    queue: Optional[ChangeRequestQueue] = None,
) -> Dict[str, Any]:
    if not build_mode_enabled():
        raise WorkbenchRejected("BUILD_MODE_ENABLED is false", status_code=503)
    q = queue or ChangeRequestQueue()
    item = q.get(request_id)
    if not item:
        raise WorkbenchRejected("not found", status_code=404)
    if item.get("state") != "gate_passed":
        raise WorkbenchRejected(f"promote requires gate_passed, got {item.get('state')}")

    product_root = Path(
        (item.get("workbench_session") or {}).get("product_root")
        or (item.get("workspace") or {}).get("product_root")
        or ""
    )
    try:
        promotion = promote_via_packager(
            item=item,
            candidate=item["candidate"],
            product_root=product_root,
            actor=actor,
        )
    except PromotionRejected as exc:
        raise WorkbenchRejected(str(exc)) from exc

    return q.update_state(
        request_id,
        "staged",
        promotion=promotion,
        audit_append=audit_event(
            "staged",
            summary=f"packaged to staging/community at {promotion.get('output_dir')}",
            details=promotion,
        ),
    )
