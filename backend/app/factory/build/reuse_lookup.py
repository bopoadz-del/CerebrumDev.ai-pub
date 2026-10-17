"""STEP 0 REUSE lookup — exact-id Blocks registry HTTP, feature-detected.

Contract (Cerebrum-Blocks #106; L2.2 report-only until flip):

    GET /v1/registry/blocks/{block_id}   — canonical
    GET /v1/registry/reuse/{block_id}    — STEP 0 alias

Auth-gated, always HTTP 200. Absent is not a 404:

    present: {"present": true, "reuse": true, "id": "...",
              "reads": [...], "writes": [...], "never": [...],
              "acceptance": [...], "manifest": {...}}
    absent:  {"present": false, "id": "...", "reuse": false}

Exact case-sensitive store id (``document_engine``, not ``DocumentEngine``).

Until Blocks lands, this client feature-detects: 404, connect errors, and
an unset ``CEREBRUM_API_URL`` fall back to the local dual-registry id set
plus on-disk ``block.json``. The compiler never invents presence or scopes.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple
from urllib.parse import quote

import httpx

logger = logging.getLogger("cerebrumdev.factory.reuse_lookup")

BLOCKS_PATH = "/v1/registry/blocks/{id}"
REUSE_PATH = "/v1/registry/reuse/{id}"
PROBE_TIMEOUT_S = 3.0
L2_KEYS = ("reads", "writes", "never", "acceptance")

#: Feature-detect cache. Blocks #106 may still be merging; one failed
#: probe means the surface is unavailable for the rest of the process.
_http_surface: Optional[bool] = None


@dataclass
class ReuseRecord:
    block_id: str
    present: bool
    source: str
    reads: List[str] = field(default_factory=list)
    writes: List[str] = field(default_factory=list)
    never: List[str] = field(default_factory=list)
    acceptance: List[str] = field(default_factory=list)
    scope_declared: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "block_id": self.block_id,
            "present": self.present,
            "source": self.source,
            "reads": list(self.reads),
            "writes": list(self.writes),
            "never": list(self.never),
            "acceptance": list(self.acceptance),
            "scope_declared": self.scope_declared,
        }


def store_base_url() -> str:
    return (os.getenv("CEREBRUM_API_URL") or "").strip().rstrip("/")


def reset_http_surface_cache() -> None:
    """Tests only — drop the process-wide feature-detect cache."""
    global _http_surface
    _http_surface = None


def _auth_headers() -> Dict[str, str]:
    key = (os.getenv("CEREBRUM_API_KEY") or os.getenv("CEREBRUM_API_TOKEN") or "").strip()
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def _as_str_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _scope_layers(payload: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    layers: List[Mapping[str, Any]] = [payload]
    scope = payload.get("scope")
    if isinstance(scope, dict):
        layers.append(scope)
    manifest = payload.get("manifest")
    if isinstance(manifest, dict):
        layers.append(manifest)
        inner = manifest.get("scope")
        if isinstance(inner, dict):
            layers.append(inner)
    return layers


def extract_l2_fields(payload: Any) -> Tuple[Dict[str, List[str]], bool]:
    """Pull reads/writes/never/acceptance. Keys missing → not declared.

    First declaring layer wins (body, then ``scope``, then ``manifest``).
    An empty list that is actually present on the JSON is declared-empty,
    not an invitation to invent from another layer or from code.
    """
    out = {key: [] for key in L2_KEYS}
    declared = False
    if not isinstance(payload, Mapping):
        return out, False
    layers = _scope_layers(payload)
    for key in L2_KEYS:
        for layer in layers:
            if key in layer:
                declared = True
                out[key] = _as_str_list(layer.get(key))
                break
    return out, declared


def parse_reuse_body(block_id: str, data: Any, *, source: str) -> ReuseRecord:
    """Interpret a 200 body. Never assume presence; never invent L2.2 scopes.

    * ``present: false`` / ``reuse: false`` is absent (the always-200 miss).
    * ``present: true`` / ``reuse: true`` is present.
    * A raw ``block.json`` 200 with a matching exact ``id`` is present.
    * An ``id`` that does not match the requested id (case-sensitive) is absent.
    """
    payload = data if isinstance(data, dict) else {}
    bid = str(block_id)
    body_id = payload.get("id")
    if body_id is not None and str(body_id) != bid:
        return ReuseRecord(
            block_id=bid,
            present=False,
            source=source,
            raw=dict(payload),
        )
    present_flag = payload.get("present")
    reuse_flag = payload.get("reuse")
    if present_flag is False or reuse_flag is False:
        present = False
    elif present_flag is True or reuse_flag is True:
        present = True
    elif body_id is not None and str(body_id) == bid:
        present = True
    else:
        present = False
    fields, declared = extract_l2_fields(payload) if present else ({k: [] for k in L2_KEYS}, False)
    return ReuseRecord(
        block_id=bid,
        present=present,
        source=source,
        reads=fields["reads"],
        writes=fields["writes"],
        never=fields["never"],
        acceptance=fields["acceptance"],
        scope_declared=declared,
        raw=dict(payload),
    )


def local_block_json_candidates(
    block_id: str,
    *,
    blocks_root: Optional[Path] = None,
) -> List[Path]:
    """Exact-id paths only. Directory name is the store id (case-sensitive)."""
    bid = str(block_id).strip()
    if not bid:
        return []
    out: List[Path] = []
    roots: List[Path] = []
    if blocks_root is not None:
        roots.append(Path(blocks_root))
    env = (os.getenv("CEREBRUM_BLOCKS_ROOT") or os.getenv("CEREBRUM_BLOCKS_PATH") or "").strip()
    if env:
        roots.append(Path(env))
    seen: Set[str] = set()
    for root in roots:
        path = (Path(root) / "block_registry" / bid / "block.json").resolve()
        key = str(path)
        if key not in seen:
            seen.add(key)
            out.append(path)
    # The Factory holds no blocks. The last resort is the Store the build
    # itself clones from (the pinned checkout), not a Factory-local copy: in
    # production CEREBRUM_BLOCKS_ROOT is unset, so this used to fall through
    # to vendor_blocks_mirror and the agent's brief was written from block
    # contracts 45 Store commits stale.
    try:
        from app.factory.blocks_source import resolve_blocks_root

        resolved = resolve_blocks_root()
        if resolved is not None:
            path = (Path(resolved) / "block_registry" / bid / "block.json").resolve()
            if str(path) not in seen:
                seen.add(str(path))
                out.append(path)
    except Exception:  # noqa: BLE001 -- no Store means "absent", never a crash
        pass
    return out


def load_local_block_json(
    block_id: str,
    *,
    blocks_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Read on-disk block.json for an exact store id. None if absent or mismatched."""
    bid = str(block_id).strip()
    if not bid:
        return None
    for path in local_block_json_candidates(bid, blocks_root=blocks_root):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        file_id = data.get("id")
        if file_id is not None and str(file_id) != bid:
            continue
        return data
    return None


def reuse_payload_for(
    block_id: str,
    *,
    local_ids: Optional[Iterable[str]] = None,
    blocks_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Always-200 body used by the Factory stub (and tests). Never 404."""
    bid = str(block_id).strip()
    known = {str(x) for x in (local_ids or ()) if str(x).strip()}
    local = load_local_block_json(bid, blocks_root=blocks_root)
    present = bid in known or local is not None
    if not present:
        return {"present": False, "id": bid, "reuse": False}
    fields, declared = extract_l2_fields(local or {})
    body: Dict[str, Any] = {"present": True, "reuse": True, "id": bid}
    if declared:
        body["reads"] = fields["reads"]
        body["writes"] = fields["writes"]
        body["never"] = fields["never"]
        body["acceptance"] = fields["acceptance"]
    if local:
        body["manifest"] = local
    return body


def _overlay_local_scope(
    record: ReuseRecord,
    *,
    blocks_root: Optional[Path] = None,
) -> ReuseRecord:
    """When HTTP says present but L2.2 keys are missing, harvest local block.json."""
    if not record.present or record.scope_declared:
        return record
    local = load_local_block_json(record.block_id, blocks_root=blocks_root)
    if not local:
        return record
    fields, declared = extract_l2_fields(local)
    if not declared:
        return record
    return ReuseRecord(
        block_id=record.block_id,
        present=True,
        source=f"{record.source}+local_block_json",
        reads=fields["reads"],
        writes=fields["writes"],
        never=fields["never"],
        acceptance=fields["acceptance"],
        scope_declared=True,
        raw=record.raw,
    )


def _local_reuse_record(
    bid: str,
    known: Set[str],
    *,
    blocks_root: Optional[Path] = None,
) -> ReuseRecord:
    local = load_local_block_json(bid, blocks_root=blocks_root)
    if local is not None:
        fields, declared = extract_l2_fields(local)
        return ReuseRecord(
            block_id=bid,
            present=True,
            source="local_block_json",
            reads=fields["reads"],
            writes=fields["writes"],
            never=fields["never"],
            acceptance=fields["acceptance"],
            scope_declared=declared,
            raw=dict(local),
        )
    in_known = bid in known
    return ReuseRecord(
        block_id=bid,
        present=in_known,
        source="local_dual_registry" if in_known else "absent",
    )


def _get(url: str) -> Optional[httpx.Response]:
    try:
        return httpx.get(url, headers=_auth_headers(), timeout=PROBE_TIMEOUT_S)
    except (httpx.HTTPError, OSError) as exc:
        logger.info("reuse lookup unreachable at %s: %s", url, exc)
        return None


def lookup_reuse_http(block_id: str, *, base_url: Optional[str] = None) -> Optional[ReuseRecord]:
    """One id against the Blocks REUSE surface. None = surface unavailable."""
    global _http_surface
    if _http_surface is False:
        return None
    root = (base_url if base_url is not None else store_base_url()).rstrip("/")
    if not root or not str(block_id).strip():
        return None
    bid = str(block_id).strip()
    encoded = quote(bid, safe="")
    saw_connection = False
    for path, label in (
        (BLOCKS_PATH.format(id=encoded), "registry/blocks"),
        (REUSE_PATH.format(id=encoded), "registry/reuse"),
    ):
        resp = _get(root + path)
        if resp is None:
            continue
        saw_connection = True
        if resp.status_code == 404:
            continue
        if resp.status_code == 200:
            _http_surface = True
            try:
                body = resp.json()
            except ValueError:
                _http_surface = False
                return None
            return parse_reuse_body(bid, body, source=label)
        logger.info(
            "reuse lookup %s returned %s — treating surface as unavailable",
            path,
            resp.status_code,
        )
        _http_surface = False
        return None
    if not saw_connection:
        _http_surface = False
    return None


def lookup_reuse(
    block_id: str,
    *,
    local_ids: Optional[Iterable[str]] = None,
    base_url: Optional[str] = None,
    http_get=None,
    blocks_root: Optional[Path] = None,
) -> ReuseRecord:
    """HTTP first when the surface exists; else local exact-id + block.json.

    An injected ``http_get`` is always called — CI has no ``CEREBRUM_API_URL``,
    and STEP 0 tests must still fail-closed on ``present: false``. Live HTTP
    only fires when a base URL is configured.
    """
    bid = str(block_id).strip()
    known = {str(x) for x in (local_ids or ()) if str(x).strip()}
    if http_get is not None:
        remote = http_get(bid, base_url=base_url)
    elif base_url or store_base_url():
        remote = lookup_reuse_http(bid, base_url=base_url)
    else:
        remote = None
    if remote is not None:
        return _overlay_local_scope(remote, blocks_root=blocks_root)
    return _local_reuse_record(bid, known, blocks_root=blocks_root)


def resolve_store_presence(
    claimed_ids: Iterable[str],
    *,
    local_ids: Optional[Iterable[str]] = None,
    base_url: Optional[str] = None,
    http_get=None,
    blocks_root: Optional[Path] = None,
) -> Dict[str, ReuseRecord]:
    """Map each claimed id to a ReuseRecord. Never invent presence."""
    out: Dict[str, ReuseRecord] = {}
    for bid in claimed_ids:
        name = str(bid).strip()
        if not name or name in out:
            continue
        out[name] = lookup_reuse(
            name,
            local_ids=local_ids,
            base_url=base_url,
            http_get=http_get,
            blocks_root=blocks_root,
        )
    return out


def present_ids(records: Dict[str, ReuseRecord]) -> Set[str]:
    return {bid for bid, rec in records.items() if rec.present}


def is_store_exact_id_miss(record: Optional[ReuseRecord]) -> bool:
    """True when the Blocks exact-id surface said ``present: false``.

    That is not a ghost id (local ``absent``) and not a local shelf hit.
    STEP 0 must drop the REUSE claim rather than invent presence from the
    Factory vendor mirror / dual-registry.
    """
    if record is None or record.present:
        return False
    src = (record.source or "").strip().lower()
    return src.startswith("registry/")
