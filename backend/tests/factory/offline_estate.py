"""Remap Store-shim block_ids to lock-era estate stubs for keyless RoleRunner.

Lock-parity re-vendor replaced echo stubs with Store ``get_block`` shims.
CLONER fail-closes those without a Store checkout. Golden product YAMLs
keep their Store ids; CI RoleRunner walks that must run offline swap in
estate factory-vendor-mirror blocks (same contract as runner_smoke.yaml).
"""

from __future__ import annotations

from typing import Iterable

from app.factory.blueprint import CapabilitySpec, ProductBlueprint

ESTATE_OFFLINE_BLOCKS: tuple[str, ...] = (
    "estate_registry",
    "estate_maintenance",
    "evidence_verifier",
    "portfolio_rollup",
    "readiness_engine",
)


def remap_block_ids_to_estate(block_ids: Iterable[str]) -> list[str]:
    mapping: dict[str, str] = {}
    next_i = 0
    remapped: list[str] = []
    for bid in block_ids:
        if bid in ESTATE_OFFLINE_BLOCKS:
            remapped.append(bid)
            continue
        if bid not in mapping:
            mapping[bid] = ESTATE_OFFLINE_BLOCKS[next_i % len(ESTATE_OFFLINE_BLOCKS)]
            next_i += 1
        remapped.append(mapping[bid])
    return remapped


def remap_blueprint_to_estate_stubs(blueprint: ProductBlueprint) -> ProductBlueprint:
    """Return a copy whose REUSE blocks are estate stubs (capability ids unchanged)."""
    caps: list[CapabilitySpec] = []
    mapping: dict[str, str] = {}
    next_i = 0
    for cap in blueprint.capabilities:
        ids: list[str] = []
        for bid in cap.block_ids:
            if bid in ESTATE_OFFLINE_BLOCKS:
                ids.append(bid)
                continue
            if bid not in mapping:
                mapping[bid] = ESTATE_OFFLINE_BLOCKS[next_i % len(ESTATE_OFFLINE_BLOCKS)]
                next_i += 1
            ids.append(mapping[bid])
        caps.append(cap.model_copy(update={"block_ids": ids}))
    return blueprint.model_copy(update={"capabilities": caps})
