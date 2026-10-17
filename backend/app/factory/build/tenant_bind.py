"""Phase 1 tenant-store binding for the build worker (option B).

Per-tenant SQLite file + per-tenant Chroma collection. The handle is
resolved from the AUTHENTICATED tenant identity at connection time —
never a client-supplied name — and the store root is a server-derived
digest of that identity, so one tenant's connection cannot address
another tenant's file. This module is the factory-side binding: the
generated platform ships its own ``app/steward/tenant_store.py`` seam
that the same identity digest drives.

The headless worker (codewhale_worker.run_worker_job) refuses an unbound
job before the CLI binary is even looked up; the runner seeds the bound
handle into the role state here.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class TenantBindingError(RuntimeError):
    """A tenant store handle could not be resolved."""


@dataclass(frozen=True)
class TenantStoreBinding:
    """A bound tenant store handle.

    ``tenant_key`` is the server-derived digest of the authenticated
    identity — the handle carries no client-supplied name. ``store_dir``
    is the per-tenant storage root the generated platform's runtime
    resolves under the same digest.
    """

    tenant_key: str
    store_dir: Path
    bound_at: float
    #: The deploy gate's own principal: its builds may use the worker's
    #: reserved slot, so a user build can never starve the post-deploy
    #: smoke. Derived server-side from the authenticated account, like
    #: ``tenant_key``; the caller never supplies it.
    reserved: bool = False


def tenant_stores_root() -> Path:
    """Per-tenant store roots live beside the factory outputs root, on the
    same persistent disk (STORAGE_PATH), never the ephemeral container
    filesystem."""
    from app.factory.paths import factory_outputs_root

    return factory_outputs_root().parent / "tenant_stores"


def bind_tenant_store(account_id: Optional[str]) -> Optional[TenantStoreBinding]:
    """Resolve the per-tenant store handle from the authenticated identity.

    Returns None when no authenticated account is available — the worker
    then fails closed with ``no_authenticated_tenant`` instead of running
    unbound. The digest is derived server-side from the identity: the
    caller can never choose another tenant's store by naming it.
    """
    identity = str(account_id or "").strip()
    if not identity:
        return None
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    store_dir = tenant_stores_root() / digest
    from app.core.trial_limits import is_reserved_smoke_account

    return TenantStoreBinding(
        tenant_key=digest,
        store_dir=store_dir,
        bound_at=time.time(),
        reserved=is_reserved_smoke_account(identity),
    )
