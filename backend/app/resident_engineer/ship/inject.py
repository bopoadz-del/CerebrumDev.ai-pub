"""Inject Resident Engineer runtime package into generated products."""

from __future__ import annotations

import ast
import shutil
from pathlib import Path
from typing import Any, Dict

from app.resident_engineer.heal.catalog import ALLOWLISTED_HEAL_ACTIONS

_SHIP_FILES = (
    "flags.py",
    "injection_guard.py",
    "dna_loader.py",
    "observe.py",
    "diagnosis.py",
    "modes.py",
)
#: The shipped router's module path (app.resident_engineer.router), derived
#: from this package so the mount and its idempotency check name one module.
_ROUTER_MODULE = __name__.rsplit(".", 2)[0] + ".router"


def _imports_module(source: str, module: str) -> bool:
    """Whether parsed ``source`` imports ``module`` (``from module import ...``
    or ``import module``). Unparseable source counts as not importing it."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            return True
        if isinstance(node, ast.Import) and any(a.name == module for a in node.names):
            return True
    return False


def inject_resident_runtime(product_out: Path) -> Dict[str, Any]:
    """Copy Resident Mode runtime into ``{product}/app/resident_engineer/``.

    Also writes a thin product router module for ``/v1/resident/*``.
    """
    src_root = Path(__file__).resolve().parents[1]
    dst = Path(product_out) / "app" / "resident_engineer"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "__init__.py").write_text(
        '"""Shipped Resident Engineer runtime (Resident Mode)."""\n'
        "from app.resident_engineer.flags import resident_engineer_enabled\n"
        '__all__ = ["resident_engineer_enabled"]\n',
        encoding="utf-8",
    )
    for name in _SHIP_FILES:
        shutil.copy2(src_root / name, dst / name)

    heal_src = src_root / "heal"
    heal_dst = dst / "heal"
    heal_dst.mkdir(parents=True, exist_ok=True)
    for py in heal_src.glob("*.py"):
        shutil.copy2(py, heal_dst / py.name)

    # Self-contained DNA checksum helper (generated products lack app.product_dna)
    (dst / "_dna_checksum.py").write_text(_DNA_CHECKSUM_SOURCE, encoding="utf-8")
    dna_loader = dst / "dna_loader.py"
    text = dna_loader.read_text(encoding="utf-8")
    dna_loader.write_text(
        text.replace(
            "from app.product_dna.emit import DNA_BUNDLE_FILES, verify_checksum_manifest",
            "from app.resident_engineer._dna_checksum import DNA_BUNDLE_FILES, verify_checksum_manifest",
        ),
        encoding="utf-8",
    )

    # Product-local router (flag-gated)
    (dst / "router.py").write_text(
        _PRODUCT_ROUTER_SOURCE,
        encoding="utf-8",
    )

    # Mount router on generated FastAPI app if present
    main_py = Path(product_out) / "app" / "main.py"
    if main_py.is_file() and not _imports_module(main_py.read_text(encoding="utf-8"), _ROUTER_MODULE):
        main_py.write_text(
            main_py.read_text(encoding="utf-8")
            + "\n\n# Resident Mode (flag-gated; RESIDENT_ENGINEER_ENABLED default false)\n"
            "try:\n"
            f"    from {_ROUTER_MODULE} import router as _resident_router\n"
            "    app.include_router(_resident_router)\n"
            "except Exception:\n"
            "    pass\n",
            encoding="utf-8",
        )

    # Repair catalog pointer
    catalog = Path(product_out) / "product-agent" / "repair_catalog"
    catalog.mkdir(parents=True, exist_ok=True)
    (catalog / "allowlisted_heals.json").write_text(
        __import__("json").dumps(
            {
                "schema_version": "1.0.0",
                "actions": list(ALLOWLISTED_HEAL_ACTIONS),
                "confirmation_required": True,
                "shell": False,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "path": str(dst),
        "allowlisted_heals": list(ALLOWLISTED_HEAL_ACTIONS),
    }


_DNA_CHECKSUM_SOURCE = '''"""Minimal DNA checksum helpers for generated products."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List

DNA_BUNDLE_FILES = (
    "product_blueprint.yaml",
    "generation_manifest.json",
    "capability_resolution.json",
    "source_provenance.json",
    "block_lockfile.json",
    "architecture.json",
    "entity_model.json",
    "action_catalog.json",
    "agent_catalog.json",
    "workflow_catalog.json",
    "security_policy.json",
    "deployment_topology.json",
    "test_catalog.json",
    "known_limitations.json",
    "change_history.json",
)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_checksum_manifest(dna_dir: Path | str) -> List[str]:
    root = Path(dna_dir)
    manifest_path = root / "checksum_manifest.json"
    if not manifest_path.is_file():
        return ["missing checksum_manifest.json"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files: Dict[str, str] = dict(manifest.get("files") or {})
    errors: List[str] = []
    for name in DNA_BUNDLE_FILES:
        path = root / name
        if not path.is_file():
            errors.append(f"missing file: {name}")
            continue
        expected = files.get(name)
        if not expected:
            errors.append(f"manifest missing entry: {name}")
            continue
        if _sha256_file(path) != expected:
            errors.append(f"checksum mismatch: {name}")
    return errors
'''


_PRODUCT_ROUTER_SOURCE = '''"""Generated-product Resident Mode HTTP surface (flag-gated)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.resident_engineer.flags import resident_engineer_enabled
from app.resident_engineer.observe import observe
from app.resident_engineer.diagnosis import build_failure_report
from app.resident_engineer.modes import draft_change_request
from app.resident_engineer.heal.approval import create_heal_approval
from app.resident_engineer.heal.catalog import ALLOWLISTED_HEAL_ACTIONS
from app.resident_engineer.heal.executor import HealRejected, execute_heal
from app.resident_engineer.heal.validate import HealValidationError
from app.steward.auth import AuthenticatedPrincipal, get_authenticated_principal
from app.steward.db import init_engine, session_scope

router = APIRouter(prefix="/v1/resident", tags=["resident-engineer"])


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _require_enabled() -> None:
    if not resident_engineer_enabled():
        raise HTTPException(status_code=503, detail="RESIDENT_ENGINEER_ENABLED is false")


@router.get("/status")
async def resident_status() -> Dict[str, Any]:
    return {
        "enabled": resident_engineer_enabled(),
        "mode": "resident",
        "maturity": "APPRENTICE",
        "allowlisted_heal_actions": list(ALLOWLISTED_HEAL_ACTIONS),
        "auth_required": True,
        "levels": {
            "L1": "observe",
            "L2": "allowlisted_heal",
            "L3_L5": "draft_change_request_only",
        },
    }


@router.get("/observe")
async def resident_observe(
    log_text: str = "",
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> Dict[str, Any]:
    _require_enabled()
    return observe(_root(), log_text=log_text)


class DiagnoseBody(BaseModel):
    symptom: str = ""
    related_action_ids: list[str] = Field(default_factory=list)


@router.post("/diagnose")
async def resident_diagnose(
    body: DiagnoseBody,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> Dict[str, Any]:
    _require_enabled()
    return build_failure_report(
        product_root=_root(),
        symptom=body.symptom,
        related_action_ids=body.related_action_ids or None,
    )


class HealApprovalBody(BaseModel):
    action_id: str


@router.post("/heal/approval")
async def resident_heal_approval(
    body: HealApprovalBody,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> Dict[str, Any]:
    _require_enabled()
    if body.action_id not in ALLOWLISTED_HEAL_ACTIONS:
        raise HTTPException(status_code=403, detail="action not allowlisted")
    init_engine()
    with session_scope() as session:
        approval = create_heal_approval(
            session,
            tenant_id=principal.tenant_id,
            principal_id=principal.principal_id,
            action_id=body.action_id,
        )
    return {"ok": True, **approval}


class HealBody(BaseModel):
    action_id: str
    confirmed: bool = False
    approval_id: Optional[str] = None


@router.post("/heal")
async def resident_heal(
    body: HealBody,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> Dict[str, Any]:
    _require_enabled()
    init_engine()
    try:
        with session_scope() as session:
            return await execute_heal(
                body.action_id,
                confirmed=body.confirmed,
                tenant_id=principal.tenant_id,
                user_id=principal.principal_id,
                principal_id=principal.principal_id,
                approval_id=body.approval_id,
                db_session=session,
            )
    except HealRejected as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except HealValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


class DraftBody(BaseModel):
    level: str = "L3"
    kind: str = "ExpansionRequest"
    summary: str = ""
    product_id: Optional[str] = None


@router.post("/draft-change-request")
async def resident_draft(body: DraftBody) -> Dict[str, Any]:
    _require_enabled()
    return draft_change_request(
        level=body.level,  # type: ignore[arg-type]
        kind=body.kind,
        summary=body.summary,
        product_id=body.product_id,
    )
'''
