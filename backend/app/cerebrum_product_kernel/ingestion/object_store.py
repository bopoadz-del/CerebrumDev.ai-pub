"""Tenant-bound upload object store (Phase 2 §1, F-1a).

A client's ``+`` attach lands here: the raw uploaded blob is written under a
per-tenant directory whose name is a SERVER-DERIVED digest of the authenticated
tenant identity — never a client-supplied name. The digest is exactly the one
``app.factory.build.tenant_bind`` uses to bind a build's per-tenant store, so an
upload and a build for the same tenant share one isolation boundary by
construction (import it, do not re-derive a second scheme).

The identity passed in is the ALREADY-RESOLVED tenant id (the caller's resolver
— the product's ``app.tenancy`` path, or the Floor's build-tenant binding —
produces it). This module never reads request headers and never maps an empty
identity to a default: it refuses by name.

Layout, under ``tenant_stores_root() / <digest> / uploads``::

    blobs/<object_id>__<safe_source_name>   the stored bytes
    jobs/<job_id>.json                       the parse-job record (parse_job.py)

Keeping blobs under ``blobs/`` means :func:`list_objects` never trips over a
sibling parse-job JSON, and the parse-job layer has a stable ``jobs/`` home.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List

from app.factory.build.tenant_bind import bind_tenant_store


class ObjectStoreError(RuntimeError):
    """A tenant-bound object could not be stored or resolved."""


@dataclass(frozen=True)
class StoredObject:
    """A stored upload blob.

    ``object_id`` is a server-minted uuid4 hex; ``path`` is the absolute stored
    path under the tenant's own digest directory. ``source_name`` is the
    client's original (sanitised) file name, kept for display and the parse job.
    """

    object_id: str
    source_name: str
    path: Path
    size_bytes: int


def _require_identity(identity: str) -> str:
    value = str(identity or "").strip()
    if not value:
        raise ObjectStoreError(
            "tenant identity must be resolved by the caller from the "
            "authenticated principal, never defaulted or client-named"
        )
    return value


def tenant_uploads_dir(identity: str) -> Path:
    """The per-tenant uploads root for ``identity``.

    ``= tenant_stores_root() / <digest> / uploads`` where ``<digest>`` is the
    SAME ``sha256(identity)[:32]`` digest ``tenant_bind`` binds a build under —
    resolved here through :func:`tenant_bind.bind_tenant_store` so the two can
    never drift to different schemes.
    """
    binding = bind_tenant_store(_require_identity(identity))
    if binding is None:  # pragma: no cover - _require_identity already guards
        raise ObjectStoreError("no authenticated tenant")
    return binding.store_dir / "uploads"


def _blobs_dir(identity: str) -> Path:
    return tenant_uploads_dir(identity) / "blobs"


def _safe_name(source_name: str) -> str:
    """Strip any path components a client put in the file name."""
    return Path(str(source_name or "unknown")).name or "unknown"


def save_object(identity: str, source_name: str, data: bytes) -> StoredObject:
    """Write ``data`` for ``identity`` and return its :class:`StoredObject`.

    The stored path carries the server-minted ``object_id`` so two different
    inputs always land at two different paths; the bytes written are the bytes
    handed in (input-derived content, verifiable by reading them back).
    """
    _require_identity(identity)
    object_id = uuid.uuid4().hex
    safe = _safe_name(source_name)
    blobs = _blobs_dir(identity)
    blobs.mkdir(parents=True, exist_ok=True)
    path = blobs / f"{object_id}__{safe}"
    path.write_bytes(data)
    return StoredObject(
        object_id=object_id,
        source_name=safe,
        path=path,
        size_bytes=len(data),
    )


def list_objects(identity: str) -> List[StoredObject]:
    """Every stored object for ``identity`` — and only for ``identity``.

    Another tenant's objects live under a different digest directory and are
    structurally unreachable here: the digest is derived from the identity
    argument alone.
    """
    _require_identity(identity)
    blobs = _blobs_dir(identity)
    if not blobs.is_dir():
        return []
    out: List[StoredObject] = []
    for path in sorted(blobs.iterdir()):
        if not path.is_file() or "__" not in path.name:
            continue
        object_id, _, source_name = path.name.partition("__")
        out.append(
            StoredObject(
                object_id=object_id,
                source_name=source_name,
                path=path,
                size_bytes=path.stat().st_size,
            )
        )
    return out


def _find(identity: str, object_id: str) -> Path:
    oid = str(object_id or "").strip()
    if not oid:
        raise ObjectStoreError("object_id is required")
    for path in _blobs_dir(identity).glob(f"{oid}__*"):
        if path.is_file():
            return path
    raise ObjectStoreError(f"object not found for this tenant: {oid}")


def read_object(identity: str, object_id: str) -> bytes:
    """Read one object's bytes, scoped to ``identity``'s digest directory."""
    _require_identity(identity)
    return _find(identity, object_id).read_bytes()
