"""Candidate change-set artifact — diff + summary + transcript. Never applied."""

from __future__ import annotations

import hashlib
import json
import difflib
from pathlib import Path
from typing import Any, Dict, List

from app.workbench.sandbox import WorkbenchSandbox


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_diff(label: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
        )
    )


def build_candidate_artifact(
    sandbox: WorkbenchSandbox,
    *,
    agent_result: Dict[str, Any],
    product_root: Path,
) -> Dict[str, Any]:
    """Build candidate change set from sandbox outputs (diff + summary + transcript)."""
    mutation_raw = ""
    mutation_path = sandbox.workspace / "candidate" / "mutation.json"
    if mutation_path.is_file():
        mutation_raw = mutation_path.read_text(encoding="utf-8")
    mutation = json.loads(mutation_raw) if mutation_raw else {}

    bp_rel = "blueprint/product_blueprint.yaml"
    bp_path = sandbox.workspace / bp_rel
    after_bp = bp_path.read_text(encoding="utf-8") if bp_path.is_file() else ""

    before_bp = ""
    for candidate in (
        product_root / "product-dna" / "product_blueprint.yaml",
        product_root / "blueprint.yaml",
        product_root / "product_blueprint.yaml",
    ):
        if candidate.is_file():
            before_bp = candidate.read_text(encoding="utf-8")
            break

    diffs: List[str] = []
    if before_bp or after_bp:
        diffs.append(_file_diff("product_blueprint.yaml", before_bp, after_bp))
    if mutation_raw:
        diffs.append(_file_diff("candidate/mutation.json", "", mutation_raw))

    unified = "\n".join(d for d in diffs if d)
    summary = str(agent_result.get("summary") or mutation.get("summary") or "candidate ready")
    transcript = list(sandbox.transcript)

    payload = {
        "schema_version": "1.0.0",
        "kind": "candidate_change_set",
        "summary": summary,
        "diff": unified,
        "transcript": transcript,
        "agent": {
            "backend": agent_result.get("backend"),
            "honesty": agent_result.get("honesty"),
            "kimi_fallback": agent_result.get("kimi_fallback"),
            "kimi_error": agent_result.get("kimi_error"),
            "cli_returncode": agent_result.get("cli_returncode"),
            "brief_mode": agent_result.get("brief_mode"),
        },
        "mutation": mutation,
        "product_dna_version": agent_result.get("product_dna_version")
        or mutation.get("product_dna_version"),
        "pin_versions": agent_result.get("pin_versions") or mutation.get("pin_versions") or {},
        "paths_touched": [
            "blueprint/product_blueprint.yaml",
            "candidate/mutation.json",
            "transcript/session.jsonl",
        ],
        "applied": False,
        "honesty": "candidate only — never an applied change; packager owns promotion",
    }
    canonical = json.dumps(
        {k: v for k, v in payload.items() if k != "checksum"},
        sort_keys=True,
        separators=(",", ":"),
    )
    payload["checksum"] = _sha256_text(canonical)

    # Persist artifact inside sandbox
    sandbox.write_text(
        "candidate/artifact.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )
    sandbox.write_text("candidate/diff.patch", unified or "# empty diff\n")
    return payload


def verify_candidate_checksum(candidate: Dict[str, Any]) -> bool:
    """Return True if candidate checksum matches payload (detects tampering)."""
    expected = candidate.get("checksum")
    if not expected:
        return False
    body = {k: v for k, v in candidate.items() if k != "checksum"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return _sha256_text(canonical) == expected
