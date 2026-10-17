import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends

from ..core import accounts_store
from ..core.auth import Principal, require_api_key
from ..core.billing import require_entitled
from ..core.session_guard import owned_session_or_404
from ..core.session_store import _session_store, create_session, get_session
from ..models.session import SessionState

router = APIRouter()


@router.post("/", response_model=SessionState)
async def create_new_session(
    body: Optional[Dict[str, Any]] = Body(default=None),
    principal: Principal = Depends(require_entitled),
):
    # Creating a session is a paid action: require_entitled blocks expired
    # trials / canceled subscriptions with 402 when BILLING_ENFORCEMENT is on.
    owner = principal.account_id or "dev-local"
    session_id = f"sess_{uuid.uuid4().hex[:16]}"
    state = create_session(session_id=session_id, user_id=owner)
    # Honor a requested domain instead of silently defaulting: a caller who
    # asks for a retail session must not get a construction-flavored one.
    domain = (body or {}).get("domain")
    if isinstance(domain, str) and domain.strip():
        state.config.domain = domain.strip()
        from ..core.session_store import update_session

        update_session(session_id, state)
    return state


_TITLE_CHARS = 48


def session_card(state: Any) -> Dict[str, Any]:
    """What the Floor's session list shows: a title, a stage, a time.

    A bare ``sess_0ca5413f`` id is not something a person can find their
    bakery platform by. The title is the product name once one is drafted,
    otherwise the first thing the user actually said. Read from session
    state only -- no ledger or disk access, this runs once per listed
    session.
    """
    pd = getattr(state, "product_design", None)
    blueprint = getattr(pd, "blueprint", None) or {}
    title = str(blueprint.get("product_name") or "").strip()
    if not title:
        for turn in getattr(state, "chat_history", None) or []:
            if isinstance(turn, dict) and turn.get("role") == "user":
                text = " ".join(str(turn.get("content") or "").split())
                if len(text) > 3:  # skip "hi"
                    title = text
                    break
    if len(title) > _TITLE_CHARS:
        title = title[: _TITLE_CHARS - 1].rstrip() + "\u2026"
    if getattr(pd, "generation", None):
        stage = "build"
    elif blueprint:
        stage = "blueprint"
    elif getattr(state, "chat_history", None):
        stage = "talking"
    else:
        stage = "empty"
    updated = getattr(state, "updated_at", None)
    return {
        "title": title,
        "stage": stage,
        "updated_at": updated.isoformat() if hasattr(updated, "isoformat") else None,
    }


@router.get("/")
async def list_sessions(principal: Principal = Depends(require_api_key)) -> List[Dict[str, Any]]:
    """List sessions owned by the caller (admin/dev see all recorded sessions)."""
    if principal.kind == "user":
        ids = accounts_store.sessions_for_owner(principal.account_id or "")
    else:
        ids = set(_session_store.keys())
        try:
            ids.update(accounts_store.all_session_ids())
        except Exception:  # noqa: BLE001 — ownership index is advisory for admin listing
            pass
        ids = sorted(ids)
    out: List[Dict[str, Any]] = []
    for sid in ids:
        state = get_session(sid)
        if state is None:
            continue
        out.append(
            {
                "session_id": state.session_id,
                "user_id": state.user_id,
                "phase": getattr(state, "phase", None),
                "phase_status": getattr(state, "phase_status", None),
                **session_card(state),
            }
        )
    return out


@router.delete("/{session_id}")
async def delete_session(
    session_id: str, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    """Delete one of the caller's sessions, for good.

    Refused while a build thread is actually running in it: deleting the
    workspace out from under a live WRITER would corrupt the run. A dead or
    stalled build does not block the delete.
    """
    from fastapi import HTTPException

    from ..core.data_rights import purge_session

    state = owned_session_or_404(session_id, principal)
    try:
        from app.factory.platform_chat_flow import _generation_output_dir, _live_build_thread

        # This session's own workspace -- another tenant's build of the same
        # product id must never block (or be mistaken for) this one.
        workspace = _generation_output_dir(state)
        if workspace is not None and _live_build_thread(workspace) is not None:
            raise HTTPException(
                status_code=409,
                detail="a build is running in this session -- stop it first, then delete",
            )
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001 -- a broken probe must not make sessions undeletable
        pass
    report = purge_session(session_id)
    if not report["ok"]:
        raise HTTPException(status_code=500, detail=report)
    return report


@router.get("/{session_id}", response_model=SessionState)
async def get_session_state(
    session_id: str, principal: Principal = Depends(require_api_key)
):
    return owned_session_or_404(session_id, principal)
