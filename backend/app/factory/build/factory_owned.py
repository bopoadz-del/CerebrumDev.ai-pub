"""The files the Factory owns in a product, and whether the writer touched one.

Owner rule (2026-10-08): the writer never authors a Factory-owned file. A
writer that creates, edits or deletes one is sent back to rework with the path
named -- never silently overwritten afterwards, never silently kept.

Live 2026-10-08 (cycle 2 fintech, sess_59090c3bd0964425,
build/plt_464389e32e544810 @ 22e6be03): the writer invented
docs/provenance/provenance.json from the blueprint and blocks.lock.json; the
Factory's gap-filling converge then kept it, so the Factory's own provenance
(factory_commit, blocks_commit) never landed and provenance_complete failed.

One declared list, built from the sources that already own these paths --
nothing here names a product, a blueprint or a branch:

* every path a Factory stamp owns outright (``stamp_registry.owned_paths``):
  the acceptance harness, the writer self-check, the deploy modules, the
  release gate, CI, the TESTER bootstrap and suites, the pins;
* the product's provenance record, which only the Factory writes (converge);
* the Store gate workflow the checkpoint carries from cerebrum-builds main.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

#: The product's provenance record (cerebrum_product_kernel.provenance; written
#: by converge.converge_writer_emitters). Factory-only by construction.
PROVENANCE_REL = "docs/provenance/provenance.json"
#: Where one writer pass records the Factory-owned paths it touched. Factory
#: internal (builds_push.FACTORY_INTERNAL_PATHS): read by the WRITER gate,
#: never shipped.
VIOLATIONS_REL = "docs/writer_factory_owned.json"

#: The WRITER gate's reason for a pass that touched a Factory-owned file. The
#: runner re-prompts on it without spending a rework round.
WRITER_AUTHORED = "writer_authored_factory_file"

CREATED = "created"
MODIFIED = "modified"
DELETED = "deleted"


def factory_owned_paths() -> Tuple[str, ...]:
    """Every product path the Factory owns, sorted. The one list."""
    from app.factory.build.builds_push import STORE_GATE_PATH
    from app.factory.build.stamp_registry import owned_paths

    return tuple(sorted({*owned_paths(), PROVENANCE_REL, STORE_GATE_PATH}))


def prestamp(root: Path | str, blueprint: Any = None, ctx: Any = None) -> List[str]:
    """Render, before a writer pass, every Factory-owned file the Factory can
    render without the writer's output; returns what changed.

    Live (cycle 5, d024b231): the deploy modules were stamped after the pass
    and the refresh set before TESTER, so a writer pass found ci.yml,
    app/health.py, app/observe.py, app/revision.py and constraints.txt
    absent, created them for a complete product, and was stopped for
    authoring Factory files. Present from the start, they are the Factory's
    to keep and the writer's to leave alone -- touching one is still a
    violation. What only TESTER can render (its bootstrap and suites) and
    what is carried from cerebrum-builds stays as it is.

    Cycle 7: TESTER's bootstrap and the provenance record are rendered here
    too (``prestamp_late_files``); their final versions still come later."""
    from app.factory.build.deploy import stamp_factory_deploy_modules
    from app.factory.build.factory_refresh import product_display_name, refresh_factory_files
    from app.factory.build.stamp_registry import _as_workspace

    base = Path(root)
    changed = list(
        refresh_factory_files(base, product_display_name(blueprint), blueprint, render_absent=True)
    )
    for rel in stamp_factory_deploy_modules(_as_workspace(base)):
        if rel not in changed:
            changed.append(rel)
    for rel in prestamp_late_files(base, ctx):
        if rel not in changed:
            changed.append(rel)
    # Cycle 8 (19fc746c): the domain acceptance driver was absent when the
    # writer started, so the construction writer authored its own and the run
    # halted on a substrate conflict. Present from the start, it is the
    # Factory's to keep; its final version follows the suite's specs.
    from app.factory.build.domain_acceptance import domain_specs, stamp_domain_substrate

    state = getattr(ctx, "state", None) or {}
    specs = domain_specs(dict(state.get("model_specs") or {}), base)
    for rel in stamp_domain_substrate(_as_workspace(base), specs):
        if rel not in changed:
            changed.append(rel)
    # The money settings (re-stamped before every pass by run_writer; here too,
    # so every writer path finds them as the Factory renders them).
    from app.factory.build.money_contract import DECLARED_LOCALE_REL, MONEY_SETTINGS_REL, emit_money_artifacts

    money = [Path(r).as_posix() for r in (MONEY_SETTINGS_REL, DECLARED_LOCALE_REL)]
    money_before = {rel: (base / rel).read_bytes() if (base / rel).is_file() else None for rel in money}
    emit_money_artifacts(_as_workspace(base), blueprint)
    for rel in money:
        if (base / rel).read_bytes() != money_before[rel] and rel not in changed:
            changed.append(rel)
    # Cycle 8 smoke B / fintech: whatever owned path is still absent.
    for rel in prestamp_absent_owned(base, ctx):
        if rel not in changed:
            changed.append(rel)
    return changed


#: Owned paths a build carries in rather than renders: the Store gate workflow
#: comes with the checkpoint from cerebrum-builds ``main``.
def carried_paths() -> Tuple[str, ...]:
    from app.factory.build.builds_push import STORE_GATE_PATH

    return (STORE_GATE_PATH,)


def prestamp_absent_owned(root: Path | str, ctx: Any = None) -> List[str]:
    """Every Factory-owned path still ABSENT before a writer pass, rendered by
    the renderer that owns it; returns what was written. Driven by the one
    list (:func:`factory_owned_paths`), never by naming a file.

    Live cycle 8 (19fc746c): smoke B's writer created constraints.txt and the
    fintech writer created TESTER's suites -- Factory-owned files that did not
    exist yet when the writer ran -- and each was charged a rework round.
    constraints.txt was rendered only beside a requirements.txt; TESTER's
    suites only by TESTER. Here TESTER's own stamping runs against the tree as
    it stands, into a scratch tree, and only the owned paths still absent are
    copied in (TESTER renders the final versions as before); constraints.txt
    is rendered from whatever the tree declares so far."""
    import shutil
    import tempfile

    base = Path(root)
    absent = [rel for rel in factory_owned_paths() if rel not in carried_paths() and not (base / rel).exists()]
    if not absent:
        return []
    changed: List[str] = []
    from app.factory.build.dependency_pins import CONSTRAINTS_REL, constraints_for_tree

    if CONSTRAINTS_REL in absent:
        (base / CONSTRAINTS_REL).write_text(constraints_for_tree(base), encoding="utf-8")
        changed.append(CONSTRAINTS_REL)
    rest = [rel for rel in absent if rel not in changed]
    if not rest or ctx is None:
        return changed
    from types import SimpleNamespace

    from app.factory.build.authority import BuildRole
    from app.factory.build.roles import RoleContext
    from app.factory.build.roles_handlers import run_tester
    from app.factory.build.workspace import RoleWorkspace

    with tempfile.TemporaryDirectory(prefix="tester-prestamp-") as scratch:
        tester = RoleContext(
            role=BuildRole.TESTER,
            workspace=RoleWorkspace(BuildRole.TESTER, base, staging=scratch),
            blueprint=getattr(ctx, "blueprint", None),
            plan=getattr(ctx, "plan", None) or SimpleNamespace(capabilities=()),
            state=dict(getattr(ctx, "state", None) or {}),
        )
        try:
            run_tester(tester)
        except Exception:  # noqa: BLE001 -- TESTER still stamps its suites later
            import logging

            logging.getLogger(__name__).warning("TESTER suites not pre-rendered", exc_info=True)
        for rel in rest:
            src = Path(scratch) / rel
            if src.is_file():
                (base / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, base / rel)
                changed.append(rel)
    return changed


def prestamp_late_files(root: Path | str, ctx: Any = None) -> List[str]:
    """The owned files the Factory otherwise renders only AFTER the writer:
    TESTER's rootdir test bootstrap (a fixed template) and the provenance
    record (converge; ``ctx`` gives its inputs). Written only when absent --
    TESTER and converge still render their final versions later.

    Live cycle 7 (a3e1fd7): smoke A, fintech and vineyard writers found
    conftest.py and docs/provenance/provenance.json absent, created them for
    their own self-check, were restored and sent back, created them again,
    and stopped on the WRITER gate budget."""
    from app.factory.build.roles_constants import CONFTEST_REL

    base = Path(root)
    changed: List[str] = []
    bootstrap = base / CONFTEST_REL
    if not bootstrap.exists():
        from app.factory.build.roles_handlers import _CONFTEST

        bootstrap.write_text(_CONFTEST, encoding="utf-8")
        changed.append(CONFTEST_REL)
    prov_path = base / PROVENANCE_REL
    if ctx is not None and not prov_path.exists():
        from app.factory.build.converge import factory_provenance_text, provenance_record

        record = provenance_record(ctx)
        if record:
            prov_path.parent.mkdir(parents=True, exist_ok=True)
            prov_path.write_text(factory_provenance_text(None, record), encoding="utf-8")
            changed.append(PROVENANCE_REL)
    return changed


def finding_shape(touched: Iterable[Mapping[str, str]]) -> str:
    """The failure's shape for the same-failure-twice rule: WHICH files were
    touched and HOW. Touching other files is a different failure, with its own
    rework round; the same files touched the same way again is the same one."""
    rows = sorted(f"{row['path']}:{row.get('change', MODIFIED)}" for row in touched)
    return "writer_authored_factory_file:" + ",".join(rows)


def snapshot(root: Path | str, paths: Optional[Iterable[str]] = None) -> Dict[str, Optional[bytes]]:
    """``{path: bytes or None}`` for every Factory-owned path under ``root``."""
    base = Path(root)
    out: Dict[str, Optional[bytes]] = {}
    for rel in paths if paths is not None else factory_owned_paths():
        target = base / rel
        try:
            out[rel] = target.read_bytes() if target.is_file() else None
        except OSError:
            out[rel] = None
    return out


def writer_touched(
    before: Mapping[str, Optional[bytes]], after: Mapping[str, Optional[bytes]]
) -> List[Dict[str, str]]:
    """Each Factory-owned path the writer created, modified or deleted."""
    touched: List[Dict[str, str]] = []
    for rel in sorted(set(before) | set(after)):
        old, new = before.get(rel), after.get(rel)
        if old == new:
            continue
        if old is None:
            how = CREATED
        elif new is None:
            how = DELETED
        else:
            how = MODIFIED
        touched.append({"path": rel, "change": how})
    return touched


def restore(root: Path | str, before: Mapping[str, Optional[bytes]], touched: Iterable[Mapping[str, str]]) -> None:
    """Put every touched path back as the Factory left it before the writer
    ran: its bytes, or absent. The rework names the path, so this is never a
    silent overwrite -- it keeps the next pass and the Factory's own
    renderers working from the Factory's file, not the writer's."""
    base = Path(root)
    for row in touched:
        rel = row["path"]
        target = base / rel
        old = before.get(rel)
        if old is None:
            if target.is_file():
                target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(old)


def record(root: Path | str, touched: List[Dict[str, str]]) -> None:
    """Write this pass's record (an empty list on a clean pass)."""
    target = Path(root) / VIOLATIONS_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"touched": touched}, indent=2) + "\n", encoding="utf-8")


def recorded(root: Path | str) -> List[Dict[str, str]]:
    """The last writer pass's record; empty when there is none."""
    try:
        data = json.loads((Path(root) / VIOLATIONS_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("touched") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict) and r.get("path")]


def rework_findings(touched: Iterable[Mapping[str, str]]) -> List[str]:
    """One rework line per path, naming it."""
    return [
        f"{row['path']}: {row.get('change', MODIFIED)} by the writer -- this file is "
        "Factory-owned (the Factory renders it); leave it to the Factory and do not write it"
        for row in touched
    ]

