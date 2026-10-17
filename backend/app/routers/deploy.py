"""Deployment router – Phase 5 (Ship)."""

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse

from ..core.session_guard import require_owned_session
from ..core.session_store import update_session
from ..core.packager import package_session
from ..core.platform_packager import package_platform_session, PlatformPackagerError
from ..core.deployer import deploy_to_render, poll_deploy_status, generate_edge_package
from ..models.session import SessionState

router = APIRouter()


class DeployTarget:
    CLOUD = "cloud"
    EDGE = "edge"
    PLATFORM = "platform"


def _update_deployment(state: SessionState, **kwargs):
    for key, value in kwargs.items():
        setattr(state.deployment, key, value)
    state.updated_at = datetime.utcnow()
    update_session(state.session_id, state)


@router.post(
    "/{session_id}/deploy",
    summary="Package or deploy a session",
    description=(
        "Packages the approved chain for the requested target. "
        "`platform` (default) produces a production-hardened docker-compose stack. "
        "`cloud` deploys to Render. `edge` is a lightweight preview — no orchestrator "
        "runtime, not standalone; requires an engine checkout to run."
    ),
)
async def start_deploy(
    background_tasks: BackgroundTasks = None,
    target: str = DeployTarget.PLATFORM,
    state: SessionState = Depends(require_owned_session),
):
    session_id = state.session_id

    if not state.chain_approved or not state.proposed_chain:
        raise HTTPException(status_code=400, detail="Chain must be approved before deployment")

    if target not in (DeployTarget.CLOUD, DeployTarget.EDGE, DeployTarget.PLATFORM):
        raise HTTPException(status_code=400, detail="Target must be 'cloud', 'edge', or 'platform'")

    _update_deployment(
        state,
        status="packaging",
        target=target,
        progress=0.1,
        message="Creating deployment package...",
    )

    try:
        if target == DeployTarget.PLATFORM:
            package_info = package_platform_session(state)
        else:
            package_info = package_session(state)
    except PlatformPackagerError as exc:
        _update_deployment(state, status="failed", progress=0.0, message=f"Platform packaging failed: {exc}")
        raise HTTPException(status_code=400, detail=f"Platform packaging failed: {exc}")
    except Exception as exc:
        _update_deployment(state, status="failed", progress=0.0, message=f"Packaging failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Packaging failed: {exc}")

    _update_deployment(
        state,
        status="packaged" if target in (DeployTarget.EDGE, DeployTarget.PLATFORM) else "deploying",
        progress=0.5,
        message="Package ready",
        package_path=package_info["zip_path"],
        api_key=package_info["api_key"],
    )

    if target == DeployTarget.PLATFORM:
        return {
            "status": "packaged",
            "download_url": f"/v1/sessions/{session_id}/deploy/package?variant=platform",
            "message": "Full platform package ready. Use docker compose up or apply render.yaml.",
        }

    if target == DeployTarget.EDGE:
        try:
            edge_zip = generate_edge_package(state, package_info["package_dir"])
            _update_deployment(
                state,
                status="packaged",
                progress=1.0,
                message="Edge preview package ready (lightweight preview — no orchestrator runtime, not standalone; requires engine checkout to run)",
                package_path=edge_zip,
            )
            return {
                "status": "packaged",
                "download_url": f"/v1/sessions/{session_id}/deploy/package?variant=edge",
                "message": "Edge preview package ready (lightweight preview — no orchestrator runtime, not standalone; requires engine checkout to run)",
            }
        except Exception as exc:
            _update_deployment(state, status="failed", message=f"Edge packaging failed: {exc}")
            raise HTTPException(status_code=500, detail=f"Edge packaging failed: {exc}")

    # Cloud deployment
    deploy_info = deploy_to_render(
        session_id,
        state,
        package_info["package_dir"],
        package_info["service_name"],
        package_info["env_vars"],
    )

    if deploy_info.get("status") == "packaged":
        _update_deployment(
            state,
            status="packaged",
            progress=1.0,
            message=deploy_info.get("message", "Package ready for manual deploy"),
        )
        return {
            "status": "packaged",
            "download_url": f"/v1/sessions/{session_id}/deploy/package",
            "message": deploy_info.get("message"),
        }

    if deploy_info.get("status") == "failed":
        _update_deployment(
            state,
            status="failed",
            progress=0.0,
            message=deploy_info.get("message", "Deployment failed"),
        )
        return {
            "status": "failed",
            "download_url": f"/v1/sessions/{session_id}/deploy/package",
            "message": deploy_info.get("message"),
        }

    _update_deployment(
        state,
        status=deploy_info.get("status", "deploying"),
        progress=0.75,
        url=deploy_info.get("url"),
        service_id=deploy_info.get("service_id"),
        deploy_id=deploy_info.get("deploy_id"),
        message=deploy_info.get("message"),
    )

    return {
        "status": state.deployment.status,
        "service_id": deploy_info.get("service_id"),
        "service_name": deploy_info.get("service_name"),
        "url": deploy_info.get("url"),
        "dashboard_url": deploy_info.get("dashboard_url"),
        "api_key": package_info["api_key"],
        "download_url": f"/v1/sessions/{session_id}/deploy/package",
        "message": deploy_info.get("message"),
    }


@router.get("/{session_id}/deploy/status")
async def deploy_status(state: SessionState = Depends(require_owned_session)):
    deployment = state.deployment
    service_id: Optional[str] = None

    # Sessions created before the cloud deploy path was cut still carry a
    # service_id from the provider the company left. poll_deploy_status no
    # longer calls any vendor -- it reports "no_provider", which matches none
    # of the branches below, so a stale record is left exactly as it is rather
    # than being advanced on the strength of a failed lookup.
    if getattr(deployment, "service_id", None):
        service_id = deployment.service_id
        latest = poll_deploy_status(service_id)
        status = latest.get("status", deployment.status)
        if status == "live" and deployment.status != "live":
            _update_deployment(state, status="live", progress=1.0, message="Deployment is live")
        elif status in ("build_in_progress", "update_in_progress", "deploying"):
            _update_deployment(state, status="deploying", progress=0.85, message=latest.get("message"))
        elif status == "failed":
            _update_deployment(state, status="failed", message=latest.get("message"))

    return {
        "status": deployment.status,
        "target": deployment.target,
        "progress": deployment.progress,
        "url": deployment.url,
        "api_key": deployment.api_key,
        "message": deployment.message,
        "package_path": deployment.package_path,
    }


@router.get("/{session_id}/deploy/package")
async def download_package(
    variant: str = "cloud",
    state: SessionState = Depends(require_owned_session),
):
    session_id = state.session_id
    package_path = state.deployment.package_path
    if not package_path or not os.path.exists(package_path):
        # Try to locate the package on disk
        base = os.path.join(os.getenv("STORAGE_PATH", "./storage"), "sessions", session_id, "deploy")
        candidate = os.path.join(base, f"{variant}.zip")
        if not os.path.exists(candidate):
            candidate = os.path.join(base, "package.zip")
        package_path = candidate

    if not package_path or not os.path.exists(package_path):
        raise HTTPException(status_code=404, detail="Package not found. Run deploy first.")

    filename = f"cerebrumdev-{state.config.domain}-{variant}.zip"
    return FileResponse(package_path, filename=filename, media_type="application/zip")
