"""Session ownership guard — one dependency for every session-scoped route.

Every router mounted under ``/v1/sessions/{session_id}/…`` must resolve the
session through :func:`require_owned_session` so that account credentials
(``cdt_`` login tokens / ``cdk_`` API keys) can only touch their own sessions.
Admin (master key) and local-dev principals pass through, mirroring the
ownership rule in ``routers/sessions.py``: cross-account access gets a
non-leaking 404.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException

from ..models.session import SessionState
from .auth import Principal, require_api_key
from .session_store import get_session

_NOT_FOUND = "Session not found"


def owned_session_or_404(session_id: str, principal: Principal) -> SessionState:
    """Load the path session and enforce per-account ownership.

    User credentials only reach their own sessions. Admin (master key) and
    local-dev principals pass through. Cross-account access gets a
    non-leaking 404 — same status and body as a missing session.

    Sync so both ``Depends(require_owned_session)`` handlers and the
    session-product / Drive routers (which stay sync) share one body.
    """
    state = get_session(session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if principal.kind == "user" and state.user_id != principal.account_id:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    return state


async def require_owned_session(
    session_id: str,
    principal: Principal = Depends(require_api_key),
) -> SessionState:
    """FastAPI dependency wrapping :func:`owned_session_or_404`."""
    return owned_session_or_404(session_id, principal)
