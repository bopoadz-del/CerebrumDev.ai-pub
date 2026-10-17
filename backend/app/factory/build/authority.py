"""Role authority kernel — build roles have lanes they cannot write outside of.

The factory's manufacturing run is not one free agent with a long prompt. It
is five roles with defined jobs, run in order, each handing a verified
workspace to the next:

    COLLECTOR -> CLONER -> WRITER -> TESTER -> STORE_MANAGER

"Defined jobs" is enforced here in code rather than asked for in a system
prompt, because a prompt instruction is advice and this needs to be a wall.
A two-hour run is a ratchet only if each phase can leave the workspace in
exactly one shape the next phase can verify; a WRITER that can quietly edit
``tests/`` to make them pass, or a TESTER that can patch ``app/`` instead of
reporting a failure, collapses the whole guarantee.

Two roots exist because the STORE_MANAGER is the one role that writes
*outside* the product being built — promoting reusable work back into the
Block Store is its job (see ``app.factory.store_manager`` for the op
vocabulary and approval gates that govern what it may publish).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class BuildRole(str, Enum):
    COLLECTOR = "COLLECTOR"
    CLONER = "CLONER"
    WRITER = "WRITER"
    TESTER = "TESTER"
    STORE_MANAGER = "STORE_MANAGER"


class LaneRoot(str, Enum):
    """Which tree a lane is anchored to."""

    WORKSPACE = "WORKSPACE"
    STORE = "STORE"


class AgentSeat(str, Enum):
    """How the coding agent sits with this kernel, if at all.

    ``none`` — mechanical; exact answers, no LLM.
    ``consult`` — kernel decides; agent reviews or proposes extras.
    ``manufacture`` — agent writes artifacts inside this kernel's lanes.
    """

    NONE = "none"
    CONSULT = "consult"
    MANUFACTURE = "manufacture"


# Path segments on the generated platform's ``/v1`` router that kernels
# publish. Capability ids may not collide with these.
KERNEL_ROUTE_NAMES: Tuple[str, ...] = (
    "jobs",
    "catalog",
    "inventory",
    "capabilities",
    "gates",
    "provenance",
    "work_queue",
)


# Order is the build order. Phases run front to back; a role may never run
# before a role ahead of it in this tuple has passed its gate.
BUILD_PHASES: Tuple[BuildRole, ...] = (
    BuildRole.COLLECTOR,
    BuildRole.CLONER,
    BuildRole.WRITER,
    BuildRole.TESTER,
    BuildRole.STORE_MANAGER,
)


class AuthorityError(PermissionError):
    """A role attempted something outside its mandate. Never downgrade to a
    warning — the run must fail loudly rather than produce an artifact whose
    provenance is unknown."""


@dataclass(frozen=True)
class RoleContract:
    role: BuildRole
    #: Short job title shown on the Floor and on ``GET /v1/jobs``.
    title: str
    mandate: str
    #: How the coding agent sits with this kernel.
    agent: AgentSeat
    #: Distinctive HTTP this kernel publishes on a delivered platform.
    #: ``GET /v1/jobs`` is the shared roster and is not repeated here.
    http_routes: Tuple[str, ...]
    #: (root, glob) pairs, POSIX-style, relative to that root.
    write_lanes: Tuple[Tuple[LaneRoot, str], ...]
    #: Human-readable name of the gate that must pass before handoff.
    gate: str

    def may_write_anything(self) -> bool:
        return bool(self.write_lanes)


ROLE_CONTRACTS: Mapping[BuildRole, RoleContract] = {
    BuildRole.COLLECTOR: RoleContract(
        role=BuildRole.COLLECTOR,
        title="Binding surveyor",
        mandate=(
            "Emit intake_blueprint.v1 (COLLECTOR output IS that object — "
            "vertical, capabilities with customer words + normalized id, "
            "roles/users, data sources, integrations, constraints, done_when, "
            "each field carrying the chat turn it came from). Resolve each "
            "capability's declared block ids into harvestable contracts. Name "
            "every capability with no block as an explicit gap so the WRITER "
            "authors that logic — never drop it from the plan. Consult the "
            "coding agent for a report-only endorse/mismatch review of each "
            "binding. Do not invent block ids, do not mutate the plan, and "
            "write nothing."
        ),
        agent=AgentSeat.CONSULT,
        http_routes=("GET /v1/catalog",),
        # Read-only by design: the collector reports, it does not materialise.
        write_lanes=(),
        gate="every referenced block id is dual-registered; gaps enumerated",
    ),
    BuildRole.CLONER: RoleContract(
        role=BuildRole.CLONER,
        title="Block stocker",
        mandate=(
            "Vendor each resolved block's source at a pinned commit, plus the "
            "local dispatch runtime those shims stand on, plus the kit packs "
            "the Factory shelf assigns those blocks to, so handlers import "
            "blocks instead of calling the store over HTTP. Write only "
            "vendor/**, kits/**, and blocks.lock.json. Exact answers — no agent."
        ),
        agent=AgentSeat.NONE,
        http_routes=("GET /v1/inventory",),
        write_lanes=(
            (LaneRoot.WORKSPACE, "vendor/**"),
            (LaneRoot.WORKSPACE, "kits/**"),
            (LaneRoot.WORKSPACE, "blocks.lock.json"),
        ),
        gate="every vendored block imports with no network configured",
    ),
    BuildRole.WRITER: RoleContract(
        role=BuildRole.WRITER,
        title="Platform manufacturer",
        mandate=(
            "Manufacture the platform over the vendored stock: capability "
            "handlers, domain models, persistence, the HTTP surface (including "
            "each kernel's job routes), UI wiring, and GENERATE logic for the "
            "gaps the collector reported. The coding agent writes those "
            "artifacts inside WRITER lanes. Do not touch tests/ or vendor/."
        ),
        agent=AgentSeat.MANUFACTURE,
        http_routes=(
            "GET /v1/capabilities",
            "POST /v1/{capability}",
            "GET /v1/{capability}",
            "GET /v1/{capability}/{id}",
            "PUT /v1/{capability}/{id}",
            "DELETE /v1/{capability}/{id}",
            "POST /v1/work_queue",
            "POST /v1/work_queue/{id}/process",
            "GET /v1/work_queue",
        ),
        write_lanes=(
            (LaneRoot.WORKSPACE, "app/**"),
            (LaneRoot.WORKSPACE, "ui/**"),
            # The run and deploy scaffold a delivered platform needs. Named
            # files rather than a root wildcard: the writer produces exactly
            # these and must not be able to drop anything else at the root.
            (LaneRoot.WORKSPACE, "README.md"),
            (LaneRoot.WORKSPACE, "requirements.txt"),
            (LaneRoot.WORKSPACE, "requirements-dev.txt"),
            (LaneRoot.WORKSPACE, "constraints.txt"),
            (LaneRoot.WORKSPACE, "pyproject.toml"),
            (LaneRoot.WORKSPACE, "Dockerfile"),
            (LaneRoot.WORKSPACE, "Procfile"),
            (LaneRoot.WORKSPACE, ".env.example"),
            (LaneRoot.WORKSPACE, ".dockerignore"),
            # Deploy artifacts, derived from deploy/contract.json. Replaces the
            # Render blueprint: that platform is gone. Named, not `deploy/**`,
            # for the same reason as every other entry here -- a wildcard would
            # let the writer drop arbitrary files into a directory the customer
            # is told to trust as generated.
            (LaneRoot.WORKSPACE, "deploy/contract.json"),
            (LaneRoot.WORKSPACE, "deploy/README.md"),
            (LaneRoot.WORKSPACE, "deploy/aws/task-definition.json"),
            # The clone-and-test contract the customer runs, and the build
            # provenance it audits. Named, not a wildcard, for the same
            # reason as the files above.
            (LaneRoot.WORKSPACE, "scripts/release_gate.py"),
            (LaneRoot.WORKSPACE, "scripts/acceptance.py"),
            (LaneRoot.WORKSPACE, "scripts/entrypoint.sh"),
            # Operational scripts the platform ships beside entrypoint.sh:
            # the backup the floor requires to be restorable, and the bench
            # that measures p95 rather than estimating it.
            (LaneRoot.WORKSPACE, "scripts/backup.sh"),
            (LaneRoot.WORKSPACE, "scripts/bench.py"),
            (LaneRoot.WORKSPACE, "scripts/rollback.sh"),
            # The WRITER gate's own probe, stamped by the Factory at WRITER
            # start (writer_behaviour.emit_self_check) so the writer can run
            # what the gate runs. Re-stamped every pass; the gate never reads
            # it, so an edit cannot change a verdict.
            (LaneRoot.WORKSPACE, "scripts/factory_checks.py"),
            (LaneRoot.WORKSPACE, ".github/workflows/ci.yml"),
            # Store ships both named OpenAPI files (openapi_committed).
            # Named, not a docs/** or root wildcard; vendor/** stays sealed.
            (LaneRoot.WORKSPACE, "openapi.json"),
            (LaneRoot.WORKSPACE, "docs/openapi.json"),
            (LaneRoot.WORKSPACE, "docs/store_acceptance.json"),
            (LaneRoot.WORKSPACE, "alembic.ini"),
            (LaneRoot.WORKSPACE, "alembic/**"),
            (LaneRoot.WORKSPACE, "docs/build_provenance.json"),
            (LaneRoot.WORKSPACE, "docs/data_lifecycle.json"),
            (LaneRoot.WORKSPACE, "docs/deploy.json"),
            (LaneRoot.WORKSPACE, "docs/domain_acceptance.json"),
            # 14-class contract surfaces ProductGenerator already emits.
            # Named prefixes, not a root or docs/** wildcard.
            (LaneRoot.WORKSPACE, "product-dna/**"),
            (LaneRoot.WORKSPACE, "docs/blueprint/**"),
            (LaneRoot.WORKSPACE, "docs/provenance/**"),
            (LaneRoot.WORKSPACE, "docs/certification/**"),
            (LaneRoot.WORKSPACE, "docs/edge_profile.json"),
            (LaneRoot.WORKSPACE, "docs/network_posture.json"),
            (LaneRoot.WORKSPACE, "docs/sbom.cdx.json"),
            (LaneRoot.WORKSPACE, "docs/permissions.json"),
            (LaneRoot.WORKSPACE, "docs/domain_pack.json"),
            (LaneRoot.WORKSPACE, "docs/package_identity.json"),
            (LaneRoot.WORKSPACE, "docs/coder_brief.md"),
            (LaneRoot.WORKSPACE, "docs/coder_session.log"),
            (LaneRoot.WORKSPACE, "docs/coder_control.json"),
            (LaneRoot.WORKSPACE, "docs/coder_receipt.json"),
            (LaneRoot.WORKSPACE, "docs/intake_blueprint.json"),
            # The country/currency the user declared on the Floor, stamped by
            # the Factory at WRITER start (money_contract).
            (LaneRoot.WORKSPACE, "docs/declared_locale.json"),
            (LaneRoot.WORKSPACE, "frontend/**"),
        ),
        # Deliberately NOT tests/** — a writer that can edit the tests that
        # judge it has no gate at all. Also not vendor/** or blocks.lock.json:
        # rewriting the vendored source would let the writer make its own
        # handlers pass by changing the blocks underneath them.
        # Owner Option C Hybrid: this in-process Factory WRITER jail stays
        # sealed off tests/** until N2. The cli-pivot BA jail
        # (cli_receipt.ba_allowed_globs) may allow tests/**. Do not
        # blanket-expand these lanes.
        gate="workspace imports and type-checks clean",
    ),
    BuildRole.TESTER: RoleContract(
        role=BuildRole.TESTER,
        title="Acceptance inspector",
        mandate=(
            "Write and run the code-phase suite against what the WRITER "
            "produced (imports, dispatch load, models, routes answer JSON, "
            "handle() returns a mapping) and bounce those failures for another "
            "writer pass. Store-backed execute-all is pilot coverage, not this "
            "gate. The harness's acceptance IS the tester — do not consult "
            "the coding agent for extra cases. Never patch app/. "
            "Never run the suite over HTTP — GET /v1/gates describes coverage "
            "only."
        ),
        agent=AgentSeat.NONE,
        http_routes=("GET /v1/gates",),
        write_lanes=(
            (LaneRoot.WORKSPACE, "tests/**"),
            # The Factory's test bootstrap (roles_constants.CONFTEST_REL): the
            # rootdir conftest, so it never lands on the product's
            # tests/conftest.py. One named file, not a root wildcard.
            (LaneRoot.WORKSPACE, "conftest.py"),
        ),
        gate="code-phase suite green (pytest -m 'not pilot')",
    ),
    BuildRole.STORE_MANAGER: RoleContract(
        role=BuildRole.STORE_MANAGER,
        title="Store registrar",
        mandate=(
            "Keep the store's books: register what this platform cloned and at "
            "which commit. This minimal form records the clone register and "
            "applies no store op. Harvesting improvements back upstream and "
            "admitting client-driven net-new capability remain unbuilt. Exact "
            "answers — no agent."
        ),
        agent=AgentSeat.NONE,
        http_routes=("GET /v1/provenance",),
        write_lanes=(
            (LaneRoot.STORE, "block_registry/**"),
            (LaneRoot.STORE, "registry.json"),
        ),
        gate="store_manager.assert_store_op_allowed passes for every op applied",
    ),
}

# Paths no role may write under any root. ``.git`` would let a role rewrite
# the provenance the whole build is audited against.
FORBIDDEN_SEGMENTS = frozenset({".git", ".hg", ".svn"})

#: Factory residue. The ledger is the orchestrator's record; a role or CLI
#: that writes it as a workspace file is the CEREBRUMDEV-BACKEND-B hole
#: (seq-less NOTE lines that brick ``events()``). Notes go through
#: :meth:`app.factory.build.ledger.BuildLedger.append` only.
FACTORY_RESIDUE = frozenset({"build_ledger.jsonl"})

#: After CLONER's gate passes, ``vendor/**`` is sealed. A later write there
#: (the old prepare_pilot_workspace patch-until-green) is FAILED_AUTHORITY,
#: not a NOTE. CLONER itself is not sealed while it is still running.
SEALED_AFTER_CLONER = ("vendor/**",)


def role_contract(role: BuildRole | str) -> RoleContract:
    return ROLE_CONTRACTS[BuildRole(role)]


def jobs_manifest() -> List[Dict[str, Any]]:
    """Job descriptions shipped on the generated platform as ``GET /v1/jobs``.

    One entry per kernel, in build order. Distinctive HTTP only — the shared
    roster lives at ``GET /v1/jobs`` itself.
    """
    return [
        {
            "kernel": role.value,
            "title": contract.title,
            "mandate": contract.mandate,
            "agent": contract.agent.value,
            "http_routes": list(contract.http_routes),
            "gate": contract.gate,
            "read_only": not contract.write_lanes,
        }
        for role, contract in ROLE_CONTRACTS.items()
    ]


def kernel_seat_brief(role: BuildRole | str) -> str:
    """One-paragraph JD for coder system prompts, derived from the contract."""
    contract = role_contract(role)
    routes = ", ".join(contract.http_routes)
    return (
        f"You are the Factory coding agent sitting with the {contract.role.value} "
        f"kernel ({contract.title}). Agent seat: {contract.agent.value}. "
        f"Mandate: {contract.mandate} Published HTTP: {routes}."
    )


def _resolved_within(path: Path, root: Path) -> Optional[Path]:
    """Return *path* relative to *root*, or None if it escapes.

    Both sides are fully resolved first so that ``..`` segments and symlinks
    pointing out of the tree are caught rather than normalised away.
    """
    try:
        resolved = Path(path).resolve()
        base = Path(root).resolve()
    except OSError:
        return None
    try:
        return resolved.relative_to(base)
    except ValueError:
        return None


def _matches_lane(relative: Path, glob: str) -> bool:
    """Match POSIX-style, so lanes read the same on Windows and Linux.

    ``fnmatch`` treats ``*`` as crossing separators, which is what a ``**``
    lane wants; an exact-file lane like ``blocks.lock.json`` has no wildcard
    and so still only matches itself.
    """
    posix = relative.as_posix()
    if glob.endswith("/**"):
        prefix = glob[:-3]
        return posix == prefix or posix.startswith(prefix + "/")
    return fnmatch.fnmatch(posix, glob)


def assert_write_allowed(
    role: BuildRole | str,
    path: Path | str,
    *,
    workspace: Path | str,
    store_root: Optional[Path | str] = None,
    sealed: Sequence[str] = (),
) -> Path:
    """Authorise one write by *role* and return the resolved target.

    Raises ``AuthorityError`` when the target escapes both roots, lands on a
    VCS directory, falls outside every lane the role owns, or hits a path
    sealed after an earlier gate (vendor/** after CLONER).
    """
    role = BuildRole(role)
    contract = ROLE_CONTRACTS[role]

    roots: Dict[LaneRoot, Path] = {LaneRoot.WORKSPACE: Path(workspace)}
    if store_root is not None:
        roots[LaneRoot.STORE] = Path(store_root)

    if not contract.write_lanes:
        raise AuthorityError(
            f"{role.value} is read-only by contract and may not write {path}"
        )

    for lane_root, glob in contract.write_lanes:
        base = roots.get(lane_root)
        if base is None:
            # The store root is only supplied for runs that promote upstream;
            # a store lane simply does not apply when it is absent.
            continue
        relative = _resolved_within(Path(path), base)
        if relative is None:
            continue
        if FORBIDDEN_SEGMENTS.intersection(relative.parts):
            raise AuthorityError(
                f"{role.value} may not write inside a version-control directory: {path}"
            )
        if relative.name in FACTORY_RESIDUE or relative.as_posix() in FACTORY_RESIDUE:
            raise AuthorityError(
                f"{role.value} may not write {path} — factory residue "
                f"({relative.name}); notes go through BuildLedger.append()"
            )
        if sealed and lane_root is LaneRoot.WORKSPACE:
            for sealed_glob in sealed:
                if _matches_lane(relative, sealed_glob):
                    raise AuthorityError(
                        f"{role.value} may not write {path} — {sealed_glob} is "
                        "sealed after CLONER (patch-until-green is FAILED_AUTHORITY)"
                    )
        if _matches_lane(relative, glob):
            return Path(path).resolve()

    lanes = ", ".join(f"{r.value}:{g}" for r, g in contract.write_lanes)
    raise AuthorityError(
        f"{role.value} may not write {path} — its lanes are [{lanes}]"
    )


def assert_phase_order(role: BuildRole | str, completed: Iterable[BuildRole | str]) -> None:
    """Every earlier phase must have completed before *role* may run."""
    role = BuildRole(role)
    done = {BuildRole(r) for r in completed}
    index = BUILD_PHASES.index(role)
    missing = [p.value for p in BUILD_PHASES[:index] if p not in done]
    if missing:
        raise AuthorityError(
            f"{role.value} cannot start before: {', '.join(missing)}"
        )


def authority_manifest() -> Dict[str, Any]:
    """Serialisable description of the authority model.

    Shipped with the product the same way ``store_manager_manifest`` is, so a
    delivered platform carries the record of which role was permitted to
    write which part of it.
    """
    return {
        "schema_version": "build_authority.v1",
        "phases": [p.value for p in BUILD_PHASES],
        "roles": {
            role.value: {
                "title": contract.title,
                "mandate": contract.mandate,
                "agent": contract.agent.value,
                "http_routes": list(contract.http_routes),
                "gate": contract.gate,
                "write_lanes": [
                    {"root": r.value, "glob": g} for r, g in contract.write_lanes
                ],
                "read_only": not contract.write_lanes,
            }
            for role, contract in ROLE_CONTRACTS.items()
        },
        "forbidden_segments": sorted(FORBIDDEN_SEGMENTS),
        "factory_residue": sorted(FACTORY_RESIDUE),
        "sealed_after_cloner": list(SEALED_AFTER_CLONER),
    }
