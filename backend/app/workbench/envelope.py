"""Task envelope — the sole boundary visible to the workbench agent.

Built from: checksum-verified Product DNA + approved change-request + M2 mutable
surfaces (allowlisted heal / request would_change). Nothing outside the envelope
is visible or writable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set

import yaml

from app.product_dna.emit import verify_checksum_manifest
from app.product_dna.validate import validate_dna_document
from app.resident_engineer.heal.catalog import ALLOWLISTED_HEAL_ACTIONS
from app.resident_engineer.injection_guard import sanitize_untrusted


# Paths relative to the sandbox workspace root that the agent may read/write.
DEFAULT_MUTABLE_PATHS = (
    "blueprint/",
    "candidate/",
    "transcript/",
    "task_envelope.json",
)

# Always read-only inside the workspace.
DEFAULT_READONLY_PATHS = (
    "product-dna/",
    "request.json",
    "baseline/",
)


class EnvelopeError(ValueError):
    """Task envelope construction or boundary violation."""


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_verified_dna(product_root: Path) -> Dict[str, Any]:
    """Load product DNA after checksum verification."""
    dna_dir = Path(product_root) / "product-dna"
    if not dna_dir.is_dir():
        raise EnvelopeError(f"missing product-dna under {product_root}")
    errors = verify_checksum_manifest(dna_dir)
    if errors:
        raise EnvelopeError(f"DNA checksum failure: {'; '.join(errors)}")
    files: Dict[str, Any] = {}
    for path in sorted(dna_dir.iterdir()):
        if path.name == "README.md" or path.name.startswith("."):
            continue
        if path.suffix == ".json":
            files[path.name] = json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix in {".yaml", ".yml"}:
            files[path.name] = path.read_text(encoding="utf-8")
        else:
            files[path.name] = path.read_bytes().hex()

    # Checksums prove integrity, not validity: every DNA document that has a
    # versioned schema must also conform to it — fail closed on violations.
    schema_errors: List[str] = []
    for name, content in files.items():
        stem = name.rsplit(".", 1)[0]
        document = content
        if Path(name).suffix in {".yaml", ".yml"}:
            document = yaml.safe_load(content)
        try:
            errors = validate_dna_document(stem, document)
        except FileNotFoundError:
            continue
        schema_errors.extend(f"{stem}: {e}" for e in errors)
    if schema_errors:
        raise EnvelopeError(f"DNA schema validation failure: {'; '.join(schema_errors)}")

    return {
        "path": str(dna_dir),
        "checksum_ok": True,
        "files": sanitize_untrusted(files),
    }


def build_mutable_surfaces(request: Dict[str, Any], dry_run: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Derive M2-style mutable envelope from the approved request + dry-run."""
    kind = request.get("kind")
    would = list((dry_run or {}).get("would_change") or [])
    surfaces: List[str] = []
    if kind == "REPAIR":
        surfaces.append("config_or_runtime_restore")
    elif kind == "EXPANSION":
        surfaces.extend(["blueprint_capabilities", "block_lockfile", "capability_resolution"])
    elif kind == "UPGRADE":
        surfaces.extend(["block_lockfile", "generation_manifest"])
    return {
        "kind": kind,
        "surfaces": surfaces,
        "would_change": sanitize_untrusted(would),
        "allowlisted_heal_actions": list(ALLOWLISTED_HEAL_ACTIONS),
        "forbidden": [
            "production_deploy",
            "store_publish",
            "store_write",
            "certified_tier_promotion",
            "shell_outside_workspace",
            "network_outside_allowlist",
        ],
    }


def build_task_envelope(
    *,
    request: Dict[str, Any],
    dry_run: Optional[Dict[str, Any]],
    dna_bundle: Dict[str, Any],
    workspace_root: Path,
    product_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Assemble the task envelope written into the sandbox."""
    mutable = build_mutable_surfaces(request, dry_run)
    envelope = {
        "schema_version": "1.0.0",
        "request_id": request.get("request_id"),
        "product_id": request.get("product_id"),
        "dna_version": request.get("dna_version"),
        "kind": request.get("kind"),
        "workspace_root": str(workspace_root.resolve()),
        "product_root": str(product_root) if product_root else None,
        "visible_readonly": list(DEFAULT_READONLY_PATHS),
        "mutable_paths": list(DEFAULT_MUTABLE_PATHS),
        "mutable": mutable,
        "dna": {
            "path": dna_bundle.get("path"),
            "checksum_ok": dna_bundle.get("checksum_ok"),
            "file_names": sorted((dna_bundle.get("files") or {}).keys()),
        },
        "request": sanitize_untrusted(
            {k: v for k, v in request.items() if k != "signature"}
        ),
        "acceptance_gates_to_rerun": list((dry_run or {}).get("acceptance_gates_to_rerun") or []),
        "honesty": (
            "Envelope IS the task boundary — paths and actions outside it are invisible "
            "and refused. Candidate only; never applied / never deployed."
        ),
    }
    raw = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
    envelope["envelope_checksum"] = _sha256_bytes(raw)
    return envelope


def path_allowed(workspace_root: Path, target: Path, *, write: bool) -> bool:
    """Return True if target is inside the workspace and matches envelope rules."""
    root = workspace_root.resolve()
    try:
        resolved = target.resolve()
        resolved.relative_to(root)
    except (OSError, ValueError):
        return False
    rel = PurePosixPath(resolved.relative_to(root).as_posix())
    if write:
        # The envelope's declared mutable surfaces, compared as path
        # components: a target is writable when it IS a declared path or lies
        # inside one -- never a text-prefix test on its spelling.
        return any(
            rel == surface or surface in rel.parents
            for surface in (PurePosixPath(p) for p in DEFAULT_MUTABLE_PATHS)
        )
    # Reads: entire workspace is readable (DNA + request + baseline + mutable).
    return True


def allowed_path_set(workspace_root: Path) -> Set[str]:
    root = workspace_root.resolve()
    return {str(root / p.rstrip("/")) for p in DEFAULT_MUTABLE_PATHS + DEFAULT_READONLY_PATHS}
