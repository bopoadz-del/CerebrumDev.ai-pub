"""Manufacturing kernel — the roles, lanes and gates of a factory build run.

``app.factory.generator`` renders a product's scaffolding. This package is
what turns that scaffolding into a platform: the ordered roles that collect,
clone, write, test and register, and the authority model that keeps each of
them inside its job.
"""

from __future__ import annotations

from app.factory.build.authority import (
    BUILD_PHASES,
    FACTORY_RESIDUE,
    KERNEL_ROUTE_NAMES,
    ROLE_CONTRACTS,
    SEALED_AFTER_CLONER,
    AgentSeat,
    AuthorityError,
    BuildRole,
    LaneRoot,
    RoleContract,
    assert_phase_order,
    assert_write_allowed,
    authority_manifest,
    jobs_manifest,
    kernel_seat_brief,
    role_contract,
)
from app.factory.build.runner import (
    BuildBudget,
    BuildOutcome,
    Outcome,
    RoleRunner,
    blueprint_hash,
    runner_enabled,
)
from app.factory.build.workspace import RoleWorkspace

__all__ = [
    "BUILD_PHASES",
    "FACTORY_RESIDUE",
    "KERNEL_ROUTE_NAMES",
    "ROLE_CONTRACTS",
    "SEALED_AFTER_CLONER",
    "AgentSeat",
    "AuthorityError",
    "BuildBudget",
    "BuildOutcome",
    "BuildRole",
    "LaneRoot",
    "Outcome",
    "RoleContract",
    "RoleRunner",
    "RoleWorkspace",
    "assert_phase_order",
    "assert_write_allowed",
    "authority_manifest",
    "blueprint_hash",
    "jobs_manifest",
    "kernel_seat_brief",
    "role_contract",
    "runner_enabled",
]
