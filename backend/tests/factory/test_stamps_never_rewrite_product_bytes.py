"""No Factory stamp re-renders a product-authored file (owner, 2026-10-07).

Every stamp is read from the ONE registry (stamp_registry) -- this file keeps
no list of its own. On a synthetic product whose shared files carry COMPUTED
values (a capability manifest computed from the product's own models) and
product code around the Factory's block, every stamp is applied twice:

* the second pass changes nothing (idempotent);
* a SHARED file's bytes outside the Factory block are byte-identical;
* a product file at a GAP path is untouched; any other product file is
  untouched;
* nothing outside the registry is written.

Control: #678's whole-file roster stamp (re-render ``app/jobs.py``, keep only
a literal manifest) fails the same check -- it wrote ``CAPABILITIES = []``
over the computed manifest (live 9de69276 vineyard repro, 8 POSTs 404).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict

from app.factory.build.factory_block import outside
from app.factory.build.factory_receipt import factory_stamped_paths
from app.factory.build.kernel_publish import JOBS_REL, declared_capabilities, render_roster
from app.factory.build.stamp_registry import GAP, OWNED, SHARED, stamps

MODELS = '''from dataclasses import dataclass


@dataclass
class TankLog:
    label: str = ""
    FIELDS = ["label"]
    CONSTRAINTS = {}
    ENTITY = "tank_reading"


MODELS = {"tank_log": TankLog}
'''

# A shared file the product wrote: a COMPUTED manifest and product code
# around where the Factory block will sit.
JOBS = '''"""The platform's kernel roster and capability manifest."""
from __future__ import annotations

from app.models import MODELS


def _entity_of(cap_id):
    return getattr(MODELS[cap_id], "ENTITY", None) or cap_id


CAPABILITIES = [
    {"id": cap_id, "entity": _entity_of(cap_id), "source": "agent"}
    for cap_id in sorted(MODELS)
]


def product_helper():
    return len(CAPABILITIES)
'''

# The product's own lines, in its own line endings.
REQUIREMENTS = "fastapi>=0.110\r\nuvicorn\r\n# the product's comment\r\n"

PRODUCT_FILES = {
    "app/__init__.py": "",
    "app/models.py": MODELS,
    "app/jobs.py": JOBS,
    "requirements.txt": REQUIREMENTS,
    "app/actions/__init__.py": "",
    "app/actions/tank_log.py": "def handle(payload):\n    return {'ok': True}\n",
    # A product-authored file at a GAP path: the backfill must leave it.
    "app/backup.py": "# the product's own backup module\nKEEP = True\n",
    # A stale Factory-OWNED file: a whole-file restamp is allowed here.
    "scripts/acceptance.py": "# an old harness\n",
}


def _write(root: Path) -> None:
    for rel, text in PRODUCT_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def _snapshot(root: Path) -> Dict[str, bytes]:
    return {
        str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def _apply_all(root: Path, roster=None) -> None:
    for stamp in stamps():
        if stamp.apply is None:
            continue
        if roster is not None and str(JOBS_REL).replace("\\", "/") in stamp.paths:
            roster(root)
            continue
        stamp.apply(root)


def _violations(before: Dict[str, bytes], after: Dict[str, bytes]) -> list:
    owned = {p for s in stamps() if s.kind == OWNED for p in s.paths}
    shared = {p for s in stamps() if s.kind == SHARED for p in s.paths}
    gap = {p for s in stamps() if s.kind == GAP for p in s.paths}
    problems = []
    for rel, old in before.items():
        new = after.get(rel)
        if new is None:
            problems.append(f"{rel}: deleted")
        elif rel in owned:
            continue
        elif rel in shared:
            if outside(new.decode("utf-8")) != old.decode("utf-8"):
                problems.append(f"{rel}: product bytes outside the Factory block changed")
        elif new != old:
            problems.append(f"{rel}: a product file was rewritten")
    for rel in set(after) - set(before):
        if rel not in owned | shared | gap:
            problems.append(f"{rel}: written outside the stamp registry")
    return problems


def _old_whole_file_roster(root: Path) -> None:
    """#678's stamp_roster, reproduced: re-render the whole module."""
    from types import SimpleNamespace

    path = root / JOBS_REL
    existing = path.read_text(encoding="utf-8") if path.is_file() else None
    plan = SimpleNamespace(capabilities=())
    path.write_text(render_roster({}, plan, declared_capabilities(existing)), encoding="utf-8")


def _load_jobs(root: Path) -> dict:
    sys.path.insert(0, str(root))
    try:
        for mod in [m for m in sys.modules if m == "app" or m.startswith("app.models")]:
            if mod != "app":
                sys.modules.pop(mod, None)
        src = (root / JOBS_REL).read_text(encoding="utf-8")
        ns: dict = {"__file__": str(root / JOBS_REL), "__name__": "product_jobs"}
        import importlib.util

        spec = importlib.util.spec_from_file_location("product_models", root / "app" / "models.py")
        models = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(models)
        exec(compile(src.replace("from app.models import MODELS", "MODELS = _MODELS"), "jobs", "exec"),
             {**ns, "_MODELS": models.MODELS})
        out: dict = {**ns, "_MODELS": models.MODELS}
        exec(compile(src.replace("from app.models import MODELS", "MODELS = _MODELS"), "jobs", "exec"), out)
        return out
    finally:
        sys.path.remove(str(root))


def test_every_registered_stamp_is_idempotent_and_never_rewrites_product_bytes(tmp_path):
    _write(tmp_path)
    before = _snapshot(tmp_path)
    _apply_all(tmp_path)
    once = _snapshot(tmp_path)
    _apply_all(tmp_path)
    twice = _snapshot(tmp_path)

    assert twice == once, sorted(k for k in set(once) | set(twice) if once.get(k) != twice.get(k))
    assert _violations(before, twice) == []
    ns = _load_jobs(tmp_path)
    assert ns["CAPABILITIES"] == [{"id": "tank_log", "entity": "tank_reading", "source": "agent"}]
    assert ns["product_helper"]() == 1
    assert ns["JOBS"] and ns["CATALOG"]["kernel"] and ns["GATES"]["kernel"]


def test_control_678s_whole_file_roster_stamp_fails_the_same_check(tmp_path):
    _write(tmp_path)
    before = _snapshot(tmp_path)
    _apply_all(tmp_path, roster=_old_whole_file_roster)
    problems = _violations(before, _snapshot(tmp_path))
    assert any(p.startswith("app/jobs.py") for p in problems), problems
    assert _load_jobs(tmp_path)["CAPABILITIES"] == []  # the live defect


def _staged_writer_pass(tmp_path: Path) -> Path:
    """A rework WRITER pass as the runner stages it: the product's bytes live
    in the destination, the staging tree starts EMPTY (the CodeWhale agent
    edits the destination directly), every stamp the staged pass runs is
    applied through the WRITER's RoleWorkspace, then the pass commits."""
    from app.factory.build.authority import BuildRole
    from app.factory.build.workspace import RoleWorkspace

    dest = tmp_path / "product"
    ws = RoleWorkspace(BuildRole.WRITER, dest, staging=tmp_path / ".product.staging-writer")
    for stamp in stamps():
        if stamp.apply is not None and stamp.staged_writer:
            stamp.apply(ws)
    ws.commit()
    return dest


def test_a_staged_writer_pass_keeps_the_product_bytes_its_staging_does_not_hold(tmp_path):
    # Live 5dd46d47 (vineyard repro): the writer's self-check was clean on the
    # tree it edited; then the roster stamp read app/jobs.py from the EMPTY
    # staging tree, saw no product manifest, appended a block binding
    # CAPABILITIES = [] and the commit copied that over the product's file --
    # every declared capability route 404'd ("baseline POST returned HTTP
    # 404"), twice, and the run stopped SAME_FAILURE_TWICE.
    dest = tmp_path / "product"
    _write(dest)
    before = _snapshot(dest)
    _staged_writer_pass(tmp_path)

    assert _violations(before, _snapshot(dest)) == []
    ns = _load_jobs(dest)
    assert ns["CAPABILITIES"] == [{"id": "tank_log", "entity": "tank_reading", "source": "agent"}]
    assert ns["JOBS"] and ns["CATALOG"]["kernel"] and ns["GATES"]["kernel"]


def test_ownership_comes_from_the_one_registry():
    owned = {p for s in stamps() if s.kind == OWNED for p in s.paths}
    shared = {p for s in stamps() if s.kind == SHARED for p in s.paths}
    receipt = set(factory_stamped_paths())
    assert owned <= receipt, owned - receipt
    assert not owned & shared, owned & shared
    # Every stamp the structural test cannot drive is still classed.
    undriven = [s.name for s in stamps() if s.apply is None]
    assert all(s.kind == OWNED for s in stamps() if s.apply is None), undriven
