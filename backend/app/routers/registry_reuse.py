"""Factory stub of the Blocks exact-id REUSE surface (Cerebrum-Blocks #106).

Always HTTP 200. Absent is ``present: false``, never a 404. Exact
case-sensitive store id. L2.2 fields (reads/writes/never/acceptance) are
echoed only when ``block.json`` declares them — never invented.

Canonical: ``GET /v1/registry/blocks/{block_id}``
STEP 0 alias: ``GET /v1/registry/reuse/{block_id}``

Auth-gated like the rest of ``/v1``. STEP 0 prefers a live Blocks URL
(``CEREBRUM_API_URL``) when that surface exists; this stub lets the
compiler feature-detect the same contract in-repo before Blocks merges.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends

from ..core.auth import Principal, require_api_key
from ..factory.build.reuse_lookup import reuse_payload_for
from ..factory.dual_registry import dual_registered_ids

router = APIRouter()


def _lookup(block_id: str) -> Dict[str, Any]:
    return reuse_payload_for(block_id, local_ids=dual_registered_ids())


@router.get("/blocks/{block_id}")
async def registry_block(
    block_id: str, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    return _lookup(block_id)


@router.get("/reuse/{block_id}")
async def registry_reuse(
    block_id: str, principal: Principal = Depends(require_api_key)
) -> Dict[str, Any]:
    return _lookup(block_id)
