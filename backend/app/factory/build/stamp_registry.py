"""Every Factory stamp that writes into a product tree -- ONE registry.

Rule (owner, 2026-10-07): a Factory stamp NEVER re-renders a product-authored
file. Each stamp is classed by what it may do to the paths it touches:

* ``OWNED``  -- the path is the Factory's outright (the product never authors
  it; the writer's lane excludes it). Whole-file stamp, every pass.
* ``SHARED`` -- the product and the Factory both write the file; the stamp
  edits ONLY its marked block (factory_block) and every other byte stays.
* ``GAP``    -- substrate the Factory writes only where the product left a
  gap; anything already there is left exactly as written. (The D2 stub repair
  replaces only a file that provides NONE of the names the Factory's own
  stamped suite imports -- substrate the agent is never asked for.)

Ownership is read from here and nowhere else: the factory receipt's
``factory_stamped_paths`` is the OWNED paths below, and the structural test
(tests/factory/test_stamps_never_rewrite_product_bytes.py) enumerates this
table -- it never keeps a list of its own.

Stamps that run only on the template path (where the Factory authors the
whole product: ``stamp_acceptance_artifacts``, ``render_routes_files``) and
the Store gate's own workflow (pushed from cerebrum-builds ``main`` by
builds_push, never a product file) do not touch a product-authored tree and
are not listed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional, Tuple

OWNED = "owned"
SHARED = "shared"
GAP = "gap"


@dataclass(frozen=True)
class Stamp:
    name: str
    kind: str
    paths: Tuple[str, ...]
    #: Applies the stamp to a workspace root or a workspace handle (the
    #: structural test drives it); None for a stamp only the runner can drive
    #: (TESTER's suites).
    apply: Optional[Callable[[Any], Any]] = None
    #: True when the stamp runs inside the STAGED WRITER pass, where writes
    #: land in a staging tree and the product's bytes may live only in the
    #: destination. Such a stamp must read through the workspace (staging
    #: first, then the destination), never a raw staging path.
    staged_writer: bool = False


class _Workspace:
    """The workspace surface the stamps write through."""

    def __init__(self, root: Path):
        self.workspace = Path(root)

    def _p(self, rel: Any) -> Path:
        return self.workspace / Path(rel)

    def exists(self, rel: Any) -> bool:
        return self._p(rel).exists()

    def read_text(self, rel: Any) -> str:
        return self._p(rel).read_text(encoding="utf-8")

    def read_path(self, rel: Any) -> Path:
        return self._p(rel)

    def write_text(self, rel: Any, text: str) -> Path:
        path = self._p(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        return path

    def __truediv__(self, rel: Any) -> Path:
        return self._p(rel)


def _as_workspace(target: Any) -> Any:
    """A workspace handle (e.g. the WRITER's staged RoleWorkspace) as given;
    a bare root wrapped."""
    if isinstance(target, (str, Path)):
        return _Workspace(Path(target))
    return target


def _ctx(root: Any) -> SimpleNamespace:
    return SimpleNamespace(
        workspace=_as_workspace(root),
        state={},
        plan=SimpleNamespace(capabilities=()),
        blueprint=None,
    )


def _roster(root: Any) -> Any:
    from app.factory.build.kernel_publish import stamp_roster

    return stamp_roster(_ctx(root))


def _deploy_modules(root: Any) -> Any:
    from app.factory.build.deploy import stamp_factory_deploy_modules

    return stamp_factory_deploy_modules(_as_workspace(root))


def _acceptance(root: Any) -> Any:
    from app.factory.build.store_acceptance import stamp_acceptance_harness

    return stamp_acceptance_harness(_as_workspace(root))


def _self_check(root: Any) -> Any:
    from app.factory.build.writer_behaviour import emit_self_check

    return emit_self_check(_as_workspace(root))


def _refresh(root: Path) -> Any:
    from app.factory.build.factory_refresh import refresh_factory_files

    return refresh_factory_files(root, "platform")


def _platform_gap(root: Any) -> Any:
    from app.factory.build.data_lifecycle import backfill_platform_substrate

    return backfill_platform_substrate(_as_workspace(root))


def _deploy_gap(root: Any) -> Any:
    from app.factory.build.deploy import backfill_deploy_substrate

    return backfill_deploy_substrate(_as_workspace(root))


def _tree_blueprint(root: Any) -> Any:
    """The blueprint the tree was compiled from (its own canonical copy, the
    session's declared intake on it), or None."""
    from app.factory.build.branch_attach import _branch_blueprint

    base = getattr(root, "workspace", root)
    try:
        return _branch_blueprint(Path(base))
    except Exception:  # noqa: BLE001 -- no readable brief: the tree's record stands
        return None


def _money(root: Any) -> Any:
    from app.factory.build.money_contract import emit_money_artifacts

    # The declared pair comes from the build's own record, never from a
    # stamp that has none (PR #719 replay blanked a certified AE / AED build).
    return emit_money_artifacts(_as_workspace(root), _tree_blueprint(root))


def _domain_owned(root: Any) -> Any:
    from app.factory.build.domain_acceptance import stamp_domain_substrate

    return stamp_domain_substrate(_as_workspace(root), {})


def _domain_gap(root: Any) -> Any:
    from app.factory.build.domain_acceptance import backfill_domain_gaps

    return backfill_domain_gaps(_as_workspace(root), {})


def stamps() -> Tuple[Stamp, ...]:
    """The table, built from each stamp's own path constants."""
    from app.factory.build.data_lifecycle import platform_substrate
    from app.factory.build.dependency_pins import CONSTRAINTS_REL
    from app.factory.build.deploy import FACTORY_OWNED_DEPLOY_MODULES, deploy_substrate
    from app.factory.build.domain_acceptance import DOMAIN_GAP_RELS, domain_owned_paths
    from app.factory.build.kernel_publish import JOBS_REL
    from app.factory.build.money_contract import DECLARED_LOCALE_REL, MONEY_SETTINGS_REL
    from app.factory.build.placeholder_connectors import CONTRACT_TEST
    from app.factory.build.product_suites import PRODUCT_SUITES
    from app.factory.build.roles_constants import CONFTEST_REL
    from app.factory.build.store_acceptance import ACCEPTANCE_SCRIPT_REL, GITHUB_CI_REL
    from app.factory.build.writer_behaviour import SELF_CHECK_REL

    def rel(p: Any) -> str:
        return str(p).replace("\\", "/")

    deploy_owned = tuple(rel(p) for p in FACTORY_OWNED_DEPLOY_MODULES)
    return (
        Stamp("kernel roster", SHARED, (rel(JOBS_REL),), _roster, staged_writer=True),
        Stamp("deploy modules", OWNED, deploy_owned, _deploy_modules, staged_writer=True),
        Stamp("acceptance harness", OWNED, (rel(ACCEPTANCE_SCRIPT_REL),), _acceptance, staged_writer=True),
        Stamp("writer self-check", OWNED, (rel(SELF_CHECK_REL),), _self_check, staged_writer=True),
        Stamp(
            "re-entry refresh (Factory files)",
            OWNED,
            ("scripts/release_gate.py", rel(ACCEPTANCE_SCRIPT_REL), rel(SELF_CHECK_REL), rel(GITHUB_CI_REL),
             CONSTRAINTS_REL),
            _refresh,
        ),
        Stamp("re-entry refresh (requirements)", SHARED, ("requirements.txt",), _refresh),
        # TESTER's test bootstrap: its own rootdir file, never the product's
        # tests/conftest.py (the names the product's tests import live there).
        Stamp("TESTER test bootstrap", OWNED, (CONFTEST_REL,)),
        Stamp(
            "TESTER product suites",
            OWNED,
            tuple(dict.fromkeys((*PRODUCT_SUITES, rel(CONTRACT_TEST), "tests/test_data_lifecycle.py",
                                 "tests/test_deploy.py"))),
        ),
        # Re-stamped whole before EVERY writer pass (run_writer): owned, so a
        # writer edit is rejected by name, never silently overwritten.
        Stamp("money settings", OWNED, (rel(MONEY_SETTINGS_REL), rel(DECLARED_LOCALE_REL)), _money,
              staged_writer=True),
        # The driver TESTER's domain acceptance suite performs through, and the
        # kernel it runs on (live cycle 8: vineyard collection failure,
        # construction substrate conflict). Re-rendered with the suite's specs.
        Stamp("domain acceptance driver + product kernel", OWNED, domain_owned_paths(), _domain_owned,
              staged_writer=True),
        Stamp("domain substrate (gaps)", GAP, DOMAIN_GAP_RELS, _domain_gap, staged_writer=True),
        Stamp("platform substrate (gaps)", GAP, tuple(rel(p) for p, _t in platform_substrate()), _platform_gap,
              staged_writer=True),
        Stamp(
            "deploy substrate (gaps)",
            GAP,
            tuple(rel(p) for p, _t in deploy_substrate() if rel(p) not in deploy_owned),
            _deploy_gap,
            staged_writer=True,
        ),
    )


def owned_paths() -> Tuple[str, ...]:
    """Every path a Factory stamp owns outright (the receipt's source)."""
    seen: dict = {}
    for stamp in stamps():
        if stamp.kind == OWNED:
            for p in stamp.paths:
                seen.setdefault(p, None)
    return tuple(seen)


def shared_paths() -> Tuple[str, ...]:
    return tuple(dict.fromkeys(p for s in stamps() if s.kind == SHARED for p in s.paths))
