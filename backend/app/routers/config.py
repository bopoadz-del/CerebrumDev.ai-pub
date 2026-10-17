from datetime import datetime
from fastapi import APIRouter, HTTPException, Body, Depends
from ..models.session import SessionConfig, SessionState
from ..core.session_guard import require_owned_session
from ..core.session_store import update_session
from ..core.domain_loader import load_domain_manifest
from ..core.block_taxonomy import list_optional_blocks

router = APIRouter()


@router.post("/{session_id}/config")
async def save_config(
    config: SessionConfig = Body(...),
    state: SessionState = Depends(require_owned_session),
):
    # Validate domain against the live store
    domain_manifest = await load_domain_manifest(config.domain)
    if not domain_manifest:
        raise HTTPException(status_code=400, detail="Domain not available")
    if domain_manifest.get("status") != "available":
        raise HTTPException(status_code=400, detail="Selected domain is not available")

    state.config = config
    state.phase = 2  # move to Phase 2 (upload) after config
    state.updated_at = datetime.utcnow()
    update_session(state.session_id, state)
    return state


@router.get("/{session_id}/config/optional-blocks")
async def optional_blocks(state: SessionState = Depends(require_owned_session)):
    """The optional primitives this instance can add. A typed request -- the
    chat used to answer this when the user TYPED "list blocks"."""
    return {"session_id": state.session_id, "optional_blocks": list_optional_blocks()}
