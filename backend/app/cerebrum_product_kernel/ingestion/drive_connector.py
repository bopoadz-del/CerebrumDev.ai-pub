"""Per-PRODUCT Google Drive connect + sync. PLACEHOLDER, no logic.

SCOPE — this is NOT the Factory's Drive connector.
``backend/app/routers/factory_drive.py`` already exists and works, but its
own docstring says "All routes are scoped to a factory SESSION" — that is
Phase 1: the operator connecting Drive during the Floor chat to seed
COLLECTOR/CLONER while the platform is being assembled.

This module is Phase 2: after the platform is deployed, the CLIENT (not the
Factory operator) connects THEIR OWN Drive to THEIR OWN running platform, on
the platform's own domain, authenticated as the platform's own tenant/user
— not as a Factory session. The OAuth app registration, token storage, and
scopes should very likely be separate from the Factory's (a client's Drive
grant must never be reachable from the Factory's own credentials store).

DO NOT copy factory_drive.py's session-scoping. Re-read it for the OAuth
mechanics (state-token CSRF protection, encrypted-at-rest tokens via
DATA_ENCRYPTION_KEY, no Bearer-header callback) — that part IS reusable
pattern — but replace every "factory session" concept with "authenticated
product tenant".

CONTRACT
--------
- ``begin_oauth(tenant_id)`` returns whatever the caller needs to redirect
  the client's browser to Google's consent screen (mirror
  factory_drive.py's redirect shape).
- ``oauth_callback(...)`` exchanges the code, encrypts and stores the token
  under the TENANT's identity (never the Factory's), same
  DATA_ENCRYPTION_KEY discipline as factory_drive.py.
- ``list_files(tenant_id)`` lists the connected Drive's files for the
  client to pick from — do not auto-ingest everything the client's Drive
  can see; the client must select what becomes part of their platform's
  corpus (informed-consent parallel to The_Fork's "labeled 'Your upload'"
  disclosure in layered-rag-golive.md).
- ``sync_file(tenant_id, file_id)`` fetches one selected file, hands its
  text to ``chunker.chunk_document`` with ``layer=2`` (documents), then
  calls ``RetrievalEngine.ingest()`` with the resulting chunks. This is the
  one function that actually touches the retrieval store — everything
  above it is OAuth plumbing.

Every function below is a stub. No network call, no token storage, no
retrieval_engine call implemented yet.
"""

from __future__ import annotations

from typing import Any, Dict, List


def begin_oauth(tenant_id: str) -> Dict[str, Any]:
    """PLACEHOLDER — not implemented. See module docstring."""
    raise NotImplementedError(
        "begin_oauth is a Phase 2 scaffold placeholder — see "
        "cerebrum_product_kernel/ingestion/__init__.py"
    )


def oauth_callback(tenant_id: str, code: str, state: str) -> Dict[str, Any]:
    """PLACEHOLDER — not implemented. See module docstring."""
    raise NotImplementedError(
        "oauth_callback is a Phase 2 scaffold placeholder — see "
        "cerebrum_product_kernel/ingestion/__init__.py"
    )


def list_files(tenant_id: str) -> List[Dict[str, Any]]:
    """PLACEHOLDER — not implemented. See module docstring."""
    raise NotImplementedError(
        "list_files is a Phase 2 scaffold placeholder — see "
        "cerebrum_product_kernel/ingestion/__init__.py"
    )


def sync_file(tenant_id: str, file_id: str) -> None:
    """PLACEHOLDER — not implemented. See module docstring.

    Intended shape once implemented: fetch -> chunker.chunk_document(...,
    layer=2, tenant_id=tenant_id, object_id=file_id) -> RetrievalEngine.ingest().
    """
    raise NotImplementedError(
        "sync_file is a Phase 2 scaffold placeholder — see "
        "cerebrum_product_kernel/ingestion/__init__.py"
    )
