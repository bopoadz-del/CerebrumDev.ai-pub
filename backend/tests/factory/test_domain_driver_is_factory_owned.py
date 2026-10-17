"""The domain acceptance driver and the kernel it runs on are the Factory's.

TESTER stamps ``tests/test_domain_acceptance.py``, which imports ``OUTCOMES,
perform_all`` from ``app/domain_ops.py``; domain_ops performs the ten outcomes
through the vendored product kernel (``app/cerebrum_product_kernel``).

Live, release cycle 8 (live commit 19fc746c):

* vineyard anchor (sess_180779be6a7c4f70, build/plt_a33ca5e4ea2643da): the
  CodeWhale writer path wrote domain_ops but never vendored the kernel, so the
  suite died at collection -- ``ModuleNotFoundError: No module named
  'app.cerebrum_product_kernel'`` -- twice, and the build stopped on
  SAME_FAILURE_TWICE with the writer billed for a module it was never asked
  for. domain_ops was also rendered with the WRITER's empty specs while TESTER
  stamped the suite with the product's.
* construction pick (sess_4663392a9d904352, build/plt_b0c6ae1d9d394160):
  domain_ops was absent when the writer started, the writer authored one
  (``perform_all`` but no ``OUTCOMES``), and the run halted on
  ``factory_substrate_conflict``.

The products below are synthetic.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.factory.build import factory_owned

KERNEL_FILE = "app/cerebrum_product_kernel/contract/runtime.py"
#: app modules the writer authors; everything else domain_ops imports must be
#: written by the Factory.
WRITER_MODULES = {"store", "models", "dispatch", "actions", "db"}

MODELS = '''from dataclasses import dataclass


@dataclass
class ZorblatLog:
    label: str = ""
    FIELDS = ["label"]
    CONSTRAINTS = {}
    ENTITY = "zorblat_log"


MODELS = {"zorblat_log": ZorblatLog}
'''


def _run_pass(tmp_path, monkeypatch, write):
    from tests.factory.test_writer_never_authors_factory_files import _run_pass as run

    return run(tmp_path, monkeypatch, write)


def _app_imports(source: str) -> set:
    """Every ``app.<module>`` a module imports, at any depth."""
    out = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            parts = node.module.split(".")
            if parts[0] != "app":
                continue
            if len(parts) == 1:
                out.update(a.name for a in node.names)
            else:
                out.add(".".join(parts[1:]))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("app."):
                    out.add(alias.name[len("app."):])
    return out


def _resolves(root: Path, dotted: str) -> bool:
    rel = Path("app", *dotted.split("."))
    return (root / rel.with_suffix(".py")).is_file() or (root / rel / "__init__.py").is_file()


def test_every_app_module_the_domain_driver_imports_is_shipped(tmp_path, monkeypatch):
    """The vineyard shape: a clean CodeWhale pass must leave a tree in which
    domain_ops' imports resolve -- the kernel most of all."""
    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, lambda _root: None)
    source = (dest / "app" / "domain_ops.py").read_text(encoding="utf-8")
    unresolved = sorted(
        mod
        for mod in _app_imports(source)
        if mod.split(".")[0] not in WRITER_MODULES and not _resolves(dest, mod)
    )
    assert not unresolved, f"domain_ops imports modules no Factory path ships: {unresolved}"
    assert (dest / KERNEL_FILE).is_file()


def test_the_driver_and_the_kernel_are_there_when_the_writer_starts(tmp_path, monkeypatch):
    seen = {}

    def look(root):
        seen.update({rel: (Path(root) / rel).is_file() for rel in ("app/domain_ops.py", KERNEL_FILE)})

    _run_pass(tmp_path, monkeypatch, look)
    assert seen and all(seen.values()), seen


def test_the_driver_and_the_kernel_are_factory_owned():
    owned = set(factory_owned.factory_owned_paths())
    assert {"app/domain_ops.py", "docs/domain_acceptance.json", KERNEL_FILE} <= owned


def test_a_writer_that_authors_the_domain_driver_is_put_back_and_told(tmp_path, monkeypatch):
    """The construction shape: no FACTORY halt on a possible-agent-work
    conflict -- the file is the Factory's, put back, and the pass is sent
    back naming it."""

    def author(root):
        path = Path(root) / "app" / "domain_ops.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("async def perform_all(capability_id=None):\n    return {}\n", encoding="utf-8")

    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, author)
    touched = {row["path"] for row in factory_owned.recorded(dest)}
    assert "app/domain_ops.py" in touched
    body = (dest / "app" / "domain_ops.py").read_text(encoding="utf-8")
    assert "OUTCOMES = (" in body and "async def perform_all" in body


def test_a_kernel_bridge_the_writer_wrote_is_kept(tmp_path, monkeypatch):
    """The kernel bridge is product-called code: gap substrate, never
    overwritten."""
    mine = "# the writer's own bridge\ndef spec_for(capability_id):\n    return None\n"

    def author(root):
        path = Path(root) / "app" / "kernel_bridge.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(mine, encoding="utf-8")

    _ctx, dest, _result = _run_pass(tmp_path, monkeypatch, author)
    assert (dest / "app" / "kernel_bridge.py").read_text(encoding="utf-8") == mine
    assert "app/kernel_bridge.py" not in {row["path"] for row in factory_owned.recorded(dest)}


def test_the_driver_names_the_capability_the_suite_asks_for(tmp_path):
    """Before TESTER judges, the driver is re-rendered from the specs TESTER
    stamps its suite from (the product's declared models): the suite's
    CAPABILITY is one the driver knows."""
    from app.factory.blueprint import load_blueprint
    from app.factory.build.declared_specs import specs_from_product_models
    from app.factory.build.domain_acceptance import render_product_tests
    from app.factory.build.runner import RoleRunner

    root_dir = Path(__file__).resolve().parents[3]
    runner = RoleRunner(load_blueprint(root_dir / "blueprints/examples/runner_smoke.yaml"), tmp_path / "build")
    ws = Path(runner.workspace)
    (ws / "app").mkdir(parents=True, exist_ok=True)
    (ws / "app" / "__init__.py").write_text("", encoding="utf-8")
    (ws / "app" / "models.py").write_text(MODELS, encoding="utf-8")

    runner._refresh_factory_files()

    specs = specs_from_product_models(ws)
    assert "zorblat_log" in specs, specs
    suite_ns: dict = {}
    for line in render_product_tests(specs).splitlines():
        if line.startswith("CAPABILITY = "):
            suite_ns["CAPABILITY"] = ast.literal_eval(line.split("=", 1)[1].strip())
    driver = (ws / "app" / "domain_ops.py").read_text(encoding="utf-8")
    driver_ns: dict = {}
    for node in ast.parse(driver).body:
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) and node.value is not None else []
        )
        if any(isinstance(t, ast.Name) and t.id in {"SPECS", "DEFAULT_CAPABILITY"} for t in targets):
            value = node.value
            for target in targets:
                driver_ns[target.id] = ast.literal_eval(value)
    assert suite_ns["CAPABILITY"] == driver_ns["DEFAULT_CAPABILITY"] == "zorblat_log"
    assert suite_ns["CAPABILITY"] in driver_ns["SPECS"]
