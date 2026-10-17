"""The honest zip manifest (Phase 6).

Every exported platform carries ``MANIFEST.json`` declaring what it really
is — engine version, retrieval mode, embedder + vector store, populated
layers with object counts, tenant id, tenancy mode, prompt version, the CI
run id that greened it, and whether the kit engine is included. The UI's
export label is DERIVED from the manifest: it is impossible to render
"RAG included" unless the manifest says ``vector_rag`` AND the engine
files are present, and impossible to render multi-tenant unless the mode
says so.

Tenancy mode strings reflect the real stack (option B): the factory runs
no Postgres RLS, so the honest values are ``single_tenant_only`` and
``multi_tenant_partition`` — ``multi_tenant_rls`` from prompt v1.1 is
deliberately NOT a value here because no such mode exists in this
codebase. This deviation is named, not hidden.

The zip itself is refused (``zip_requires_green_ci``) unless the CI run
that greened the build is recorded AND the Phase 0.5 artifact gate holds
(agent-authored artifacts > 0). A manifest claim that the zip's contents
do not back is ``manifest_mismatch`` — verified, not trusted.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

MANIFEST_SCHEMA = "export_manifest.v1"
MANIFEST_FILENAME = "MANIFEST.json"

RETRIEVAL_MODES = ("vector_rag", "keyword_lexical")
TENANCY_MODES = ("single_tenant_only", "multi_tenant_partition")

ZIP_REQUIRES_GREEN_CI = "zip_requires_green_ci"
MANIFEST_MISMATCH = "manifest_mismatch"

#: The kernel engine file a manifest claim must be able to back.
ENGINE_FILE = "app/cerebrum_product_kernel/retrieval_engine.py"


class ExportManifestError(ValueError):
    """A named refusal from the export-manifest layer."""


def ci_run_id(env: Optional[Dict[str, str]] = None) -> str:
    """The CI run that greened this build, fed by the pipeline."""
    source = env if env is not None else os.environ
    return str(source.get("FACTORY_CI_RUN_ID") or source.get("GITHUB_RUN_ID") or "").strip()


#: How the Store gate's verdict reaches the ledger when it ran in CI.
_COMMIT_STATUS_VIA = "github-commit-status:"


def ci_evidence(status: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> str:
    """The CI run that greened THIS build, from where that fact is recorded.

    ``ci_run_id`` reads GITHUB_RUN_ID / FACTORY_CI_RUN_ID from the process
    environment -- but the process serving the download is the API server,
    which never runs inside the CI job. In production that variable does not
    exist, so every runner-built platform was refused with "the build was not
    greened by CI", including one whose Store gate had passed 13/13 in Docker
    (live: sess_b4fcca22b6204b68, HTTP 500 on the Floor's Export button).

    The build's own ledger is where the CI verdict lands: the N3 ingest
    records the acceptance result, that it came from a GitHub commit status,
    and the exact cerebrum-builds sha it was measured on. That is stronger
    evidence than an ambient env var -- it is bound to this build, not to
    whatever process happens to be asking.

    Fail-closed: the acceptance must be ok, complete (k == k > 0), sourced
    from a commit status, and pinned to a sha. Anything less is no evidence.
    """
    run = ci_run_id(env)
    if run:
        return run
    acceptance = status.get("acceptance") or {}
    sha = str(status.get("builds_sha") or "").strip()
    via = str(acceptance.get("via") or "")
    try:
        passed, total = int(acceptance.get("passed") or 0), int(acceptance.get("total") or 0)
    except (TypeError, ValueError):
        return ""
    if (
        acceptance.get("ok") is True
        and total > 0
        and passed == total
        and via.startswith(_COMMIT_STATUS_VIA)
        and sha
    ):
        return f"{via}@{sha}"
    return ""


def assert_zip_eligibility(status: Dict[str, Any], run_id: str) -> None:
    """6.3: a zip is produced ONLY after CI is green AND the artifact gate
    (Phase 0.5) passed. Anything else refuses with the named reason."""
    state = str(status.get("state") or "")
    authorship = status.get("authorship") or {}
    agent_written = int(authorship.get("agent_written") or 0)
    if state != "succeeded":
        raise ExportManifestError(
            f"{ZIP_REQUIRES_GREEN_CI}: build state is {state!r}, not succeeded"
        )
    if agent_written <= 0:
        raise ExportManifestError(
            f"{ZIP_REQUIRES_GREEN_CI}: the artifact gate did not pass "
            f"(agent_written={agent_written})"
        )
    if not run_id:
        raise ExportManifestError(
            f"{ZIP_REQUIRES_GREEN_CI}: no CI run id recorded — the build "
            "was not greened by CI"
        )


def build_manifest(
    *,
    product_id: str,
    tenant_id: Optional[str],
    ci_run: str,
    retrieval_mode: str,
    tenancy_mode: str,
    embedder: str,
    vector_store: str,
    engine_version: str,
    prompt_version: str,
    layer_counts: Dict[int, int],
    engine_included: bool,
    provenance: Optional[Dict[str, str]] = None,
    build_level: Optional[Dict[str, str]] = None,
    advisory_checks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Assemble the versioned manifest. Validated, never a free-form dict.

    ``advisory_checks`` lists every gate this build moved to advisory because
    its brief never defined it, or because its findings are in Factory
    substrate ([{check, reason, findings_count, findings}]), so the buyer
    sees what was not held against the product, why, and the evidence rows."""
    if retrieval_mode not in RETRIEVAL_MODES:
        raise ExportManifestError(
            f"{MANIFEST_MISMATCH}: retrieval_mode must be one of "
            f"{RETRIEVAL_MODES}, got {retrieval_mode!r}"
        )
    if tenancy_mode not in TENANCY_MODES:
        raise ExportManifestError(
            f"{MANIFEST_MISMATCH}: tenancy_mode must be one of "
            f"{TENANCY_MODES}, got {tenancy_mode!r}"
        )
    for layer, count in layer_counts.items():
        if layer not in (1, 2, 3, 4) or int(count) < 0:
            raise ExportManifestError(
                f"{MANIFEST_MISMATCH}: layer_counts must be 1-4 -> non-negative, "
                f"got {layer!r}: {count!r}"
            )
    # Which Factory and which Store made this, so the artifact can be traced
    # back to the code that produced it. Absent values are omitted rather
    # than written as "unknown": a field that says "no idea" is the
    # reproducibility hole provenance_complete refuses.
    stamped = {
        key: str(value).strip()
        for key, value in (provenance or {}).items()
        if str(value or "").strip() and str(value).strip().lower() != "unknown"
    }
    return {
        "schema": MANIFEST_SCHEMA,
        "product_id": product_id,
        "tenant_id": tenant_id,
        "provenance": stamped,
        "engine": {
            "version": engine_version,
            "included": bool(engine_included),
        },
        "retrieval": {
            "mode": retrieval_mode,
            "embedder": embedder,
            "vector_store": vector_store,
        },
        "layers": {str(layer): int(count) for layer, count in layer_counts.items()},
        "tenancy_mode": tenancy_mode,
        "prompt_template_version": prompt_version,
        "ci_run_id": ci_run,
        # The level the user chose and the gate the run stopped at, read
        # from the run's own ledger -- None for a run that declared none.
        "build_level": dict(build_level) if build_level else None,
        "advisory_checks": [
            {
                "check": str(row.get("check") or ""),
                "reason": str(row.get("reason") or ""),
                "findings_count": int(row.get("findings_count") or 0),
                "findings": [str(f) for f in row.get("findings") or []],
            }
            for row in (advisory_checks or [])
        ],
    }


def failed_export_manifest(
    status: Dict[str, Any],
    *,
    product_id: str,
    platform_id: Optional[str] = None,
) -> Dict[str, Any]:
    """The MANIFEST.json a FAILED build ships with on "Download as-is".

    A failed build is still a product, but never a certified one: the
    manifest names the exact failing gate, check and finding, the acceptance
    score (k/N), the level it was built to, and ``certified: false``. It makes
    no store, acceptance or pilot claim."""
    from app.factory.build.failure_record import (
        acceptance_score,
        failed_label,
        failure_triple,
    )

    triple = status.get("failed") or failure_triple({}, status.get("failure")) or {
        "gate": "unknown",
        "check": str(status.get("state") or "unknown"),
        "finding": str(status.get("detail") or ""),
    }
    return {
        "schema": MANIFEST_SCHEMA,
        "product_id": product_id,
        "platform_id": platform_id,
        "certified": False,
        "build_state": status.get("state"),
        "failed": dict(triple),
        "failed_label": failed_label(triple),
        "acceptance": acceptance_score(status),
        "build_level": status.get("build_level"),
        "advisory_checks": list(status.get("advisory_checks") or []),
        "next_continue": status.get("next_continue") or "",
        "failure_narrative": status.get("failure_narrative"),
    }


def write_export_manifest(product_root: Path | str, manifest: Dict[str, Any]) -> Path:
    root = Path(product_root)
    path = root / MANIFEST_FILENAME
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def verify_manifest(
    manifest: Dict[str, Any], zip_contents: Set[str]
) -> List[str]:
    """Verify every manifest claim against the zip's ACTUAL contents.

    Returns a list of ``manifest_mismatch`` reasons; empty = the manifest
    is honest. A vector-rag or engine-included claim with no engine file
    in the zip is the forgery P6 exists to catch.
    """
    problems: List[str] = []
    if manifest.get("schema") != MANIFEST_SCHEMA:
        problems.append(
            f"{MANIFEST_MISMATCH}: schema must be {MANIFEST_SCHEMA!r}"
        )
    engine_included = bool(manifest.get("engine", {}).get("included"))
    mode = str(manifest.get("retrieval", {}).get("mode") or "")
    engine_present = ENGINE_FILE in zip_contents
    if mode == "vector_rag" and not engine_present:
        problems.append(
            f"{MANIFEST_MISMATCH}: manifest claims vector_rag but the kit "
            f"engine file ({ENGINE_FILE}) is not in the zip"
        )
    if engine_included and not engine_present:
        problems.append(
            f"{MANIFEST_MISMATCH}: manifest claims the kit engine is "
            f"included but {ENGINE_FILE} is not in the zip"
        )
    tenancy = str(manifest.get("tenancy_mode") or "")
    if tenancy not in TENANCY_MODES:
        problems.append(
            f"{MANIFEST_MISMATCH}: tenancy_mode must be one of {TENANCY_MODES}"
        )
    if mode not in RETRIEVAL_MODES:
        problems.append(
            f"{MANIFEST_MISMATCH}: retrieval mode must be one of {RETRIEVAL_MODES}"
        )
    return problems


def ui_labels_from_manifest(
    manifest: Dict[str, Any], zip_contents: Set[str]
) -> Dict[str, Optional[str]]:
    """The ONLY labels the UI may render for this export.

    Derived, never asserted by the UI itself: "RAG included" appears iff
    the manifest says vector_rag AND the engine file is really in the zip;
    multi-tenant appears iff the mode says so. Absent = the UI renders
    nothing for that slot.
    """
    mode = str(manifest.get("retrieval", {}).get("mode") or "")
    engine_present = ENGINE_FILE in zip_contents
    tenancy = str(manifest.get("tenancy_mode") or "")
    rag_label = None
    if mode == "vector_rag" and engine_present:
        rag_label = "RAG included"
    tenancy_label = (
        "Multi-tenant"
        if tenancy == "multi_tenant_partition"
        else ("Single-tenant" if tenancy == "single_tenant_only" else None)
    )
    return {"rag": rag_label, "tenancy": tenancy_label}
