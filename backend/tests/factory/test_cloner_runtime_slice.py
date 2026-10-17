"""A clone of a real Store block must carry the runtime it stands on.

New-shape tests for defect 1g. Real Store blocks are shims: block.py does
``from app.blocks import get_block`` and the logic lives in the Store's
``app/blocks/<name>.py``, resting on ``app/core``. The CLONER used to vendor
only the shim, so the first build against the real Store failed its own gate
with ``ModuleNotFoundError: No module named 'app'`` -- six blocks out of six.

The name ``app`` cannot be vendored as-is because the delivered platform's own
package is called ``app``. So the CLONER must vendor the slice under
``vendor/cerebrum/`` and mechanically rewrite ``app.blocks``/``app.core``
imports to the vendored names. These tests drive that behaviour against a faux
Store shaped exactly like the real one.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from app.factory.build.authority import BuildRole
from app.factory.build.gates import GateContext, gate_blocks_import_offline
from app.factory.build.roles import RoleContext, RoleError, run_cloner
from app.factory.build.workspace import RoleWorkspace

pytestmark = pytest.mark.usefixtures("_no_paid_calls")


@pytest.fixture(autouse=True)
def _no_paid_calls(monkeypatch):
    monkeypatch.setenv("FACTORY_CODER_ENABLED", "0")


_SHIM = '''\
"""Auto-generated adapter for Cerebrum block: greeting."""

import asyncio
from app.blocks import get_block


def run(**kwargs):
    block_cls = get_block("greeting")
    instance = block_cls()
    input_data = kwargs.get("input", kwargs)
    envelope = asyncio.run(instance.execute(input_data, {}))
    return envelope.get("result", envelope)
'''

_BLOCKS_INIT = '''\
import importlib

_EXTENDED_BLOCK_DEFS = {
    "greeting": ("app.blocks.greeting", "GreetingBlock"),
    "farewell": ("app.blocks.farewell", "FarewellBlock"),
}


def get_block(name):
    module_path, class_name = _EXTENDED_BLOCK_DEFS[name]
    return getattr(importlib.import_module(module_path), class_name)
'''

_GREETING = '''\
from app.core.universal_base import UniversalBlock


class GreetingBlock(UniversalBlock):
    async def execute(self, input_data, params):
        name = (input_data or {}).get("name", "world")
        return {"status": "ok", "result": {"greeting": f"hello {name}"}}
'''

_UNIVERSAL_BASE = '''\
class UniversalBlock:
    def __init__(self, hal_block=None, config=None):
        self.hal_block = hal_block
        self.config = config or {}
'''


def _faux_store(root: Path) -> Path:
    """A Store checkout shaped like the real one: shim + app.blocks + app.core."""
    store = root / "store"
    reg = store / "block_registry" / "greeting"
    reg.mkdir(parents=True)
    (reg / "block.json").write_text(
        json.dumps({"id": "greeting", "name": "Greeting"}), encoding="utf-8"
    )
    (reg / "block.py").write_text(_SHIM, encoding="utf-8")

    blocks = store / "app" / "blocks"
    blocks.mkdir(parents=True)
    (blocks / "__init__.py").write_text(_BLOCKS_INIT, encoding="utf-8")
    (blocks / "greeting.py").write_text(_GREETING, encoding="utf-8")

    core = store / "app" / "core"
    core.mkdir(parents=True)
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "universal_base.py").write_text(_UNIVERSAL_BASE, encoding="utf-8")
    return store


def _clone(tmp_path: Path, store: Path, block_ids=("greeting",)):
    from app.factory.blocks_lock import generate_lock

    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=store,
        blocks_lock=generate_lock(store, consumed_ids=block_ids),
        state={"resolved_blocks": tuple(block_ids)},
    )
    return ws, run_cloner(ctx)


def test_a_real_store_shim_imports_offline_after_cloning(tmp_path):
    """The exact failure of the first real build: the shim's ``from app.blocks``
    must resolve inside the delivered workspace, with no Store checkout and no
    store env configured."""
    store = _faux_store(tmp_path)
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    gate = gate_blocks_import_offline(
        GateContext(
            workspace=ws.destination,
            role=BuildRole.CLONER,
            vendored_blocks=("greeting",),
        )
    )
    assert gate.ok, f"{gate.detail}: {gate.findings}"


def test_the_vendored_shim_executes_offline(tmp_path):
    """Importing is not the bar -- get_block must resolve through the vendored
    registry and the block must run, in a subprocess whose only world is the
    workspace."""
    store = _faux_store(tmp_path)
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    probe = textwrap.dedent(
        """
        import importlib.util, json, os, pathlib, sys
        for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
            os.environ.pop(var, None)
        path = pathlib.Path("vendor/blocks/greeting/block.py")
        spec = importlib.util.spec_from_file_location("vendored_greeting", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.run(input={"name": "site"})
        print(json.dumps(out))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {"greeting": "hello site"}


def test_the_lockfile_records_the_runtime_slice(tmp_path):
    """The registrar cannot answer staleness for files it does not know were
    cloned. The slice is cloned material and must be in the lockfile."""
    store = _faux_store(tmp_path)
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    lock = json.loads((ws.destination / "blocks.lock.json").read_text(encoding="utf-8"))
    runtime = lock.get("runtime")
    assert runtime, "lockfile has no runtime-slice record"
    assert runtime["source"] == "cerebrum-blocks"
    assert runtime.get("commit"), "runtime slice is unpinned"
    files = runtime.get("files", [])
    assert "vendor/cerebrum/blocks/greeting.py" in files
    assert "vendor/cerebrum/core/universal_base.py" in files


def test_the_vendored_registry_lists_only_what_was_cloned(tmp_path):
    """The Store's registry names ~120 modules; the platform carries the ones
    it vendored. A registry entry pointing at a module that is not on disk is
    a latent ModuleNotFoundError in the customer's environment."""
    store = _faux_store(tmp_path)
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    registry = (ws.destination / "vendor" / "cerebrum" / "blocks" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "greeting" in registry
    assert "farewell" not in registry, "registry names a block that was never vendored"



def test_a_standalone_block_vendors_no_runtime_slice(tmp_path):
    """Mirror-style blocks import nothing from the Store runtime; vendoring a
    slice they do not use would ship dead code into every platform."""
    store = _faux_store(tmp_path)
    standalone = store / "block_registry" / "solo"
    standalone.mkdir(parents=True)
    (standalone / "block.json").write_text(json.dumps({"id": "solo"}), encoding="utf-8")
    (standalone / "block.py").write_text(
        "def run(**kwargs):\n    return {'ok': True}\n", encoding="utf-8"
    )

    ws, result = _clone(tmp_path, store, block_ids=("solo",))
    assert result.ok, result.detail
    assert not (ws.destination / "vendor" / "cerebrum").exists()


def test_block_registry_is_exported_for_cross_block_dispatch(tmp_path):
    """Real blocks do ``from app.blocks import BLOCK_REGISTRY`` inside
    functions (workflow chain validation, notification MCP channel). The
    vendored registry must export a lazy BLOCK_REGISTRY over the vendored
    defs, and the parser must not choke on the import line."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        _GREETING
        + textwrap.dedent(
            '''
            def peers():
                from app.blocks import BLOCK_REGISTRY
                return "greeting" in BLOCK_REGISTRY
            '''
        ),
        encoding="utf-8",
    )

    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    probe = textwrap.dedent(
        """
        from vendor.cerebrum.blocks import BLOCK_REGISTRY, get_block
        assert "greeting" in BLOCK_REGISTRY
        assert BLOCK_REGISTRY["greeting"] is get_block("greeting")
        assert BLOCK_REGISTRY.get("farewell") is None
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def test_a_lazy_foreign_import_is_recorded_not_fatal(tmp_path):
    """``from app.dependencies import ...`` inside a function only breaks the
    one feature that runs it. Failing the whole clone for an optional path
    would make every real block unbuildable; hiding it would ship a surprise.
    It is recorded in the lockfile."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        _GREETING
        + textwrap.dedent(
            '''
            def mcp_channel():
                from app.dependencies import create_instance
                return create_instance
            '''
        ),
        encoding="utf-8",
    )

    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    lock = json.loads((ws.destination / "blocks.lock.json").read_text(encoding="utf-8"))
    recorded = lock["runtime"].get("lazy_foreign_imports", [])
    assert any("app.dependencies" in entry for entry in recorded), recorded


def test_a_module_level_foreign_import_fails_the_clone(tmp_path):
    """A top-level import of an unvendorable Store package executes at import
    time -- the block cannot load offline at all, so the clone must fail with
    the module named."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        "from app.dependencies import create_instance\n" + _GREETING,
        encoding="utf-8",
    )

    with pytest.raises(RoleError, match="app.dependencies"):
        _clone(tmp_path, store)


def test_an_unresolvable_runtime_import_fails_the_clone_loudly(tmp_path):
    """A block whose runtime module cannot be found must fail the CLONE with
    the module named -- not pass the clone and fail as a ModuleNotFoundError
    on the customer's machine."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        "from app.core.does_not_exist import Missing\n"
        "from app.core.universal_base import UniversalBlock\n"
        "class GreetingBlock(UniversalBlock):\n"
        "    pass\n",
        encoding="utf-8",
    )

    with pytest.raises(RoleError, match="does_not_exist"):
        _clone(tmp_path, store)


def test_unknown_vendored_import_fails_cloner_as_role_error(tmp_path):
    """Live sess_d1cb9d51c5354bea / CEREBRUMDEV-BACKEND-A.

    ``dependency_obligations`` still fail-closes on an unrecorded import.
    That used to escape ``run_cloner`` as BlockObligationError and crash
    the build thread ("Build failed — build thread crashed"). Converted to
    RoleError so the Floor shows the missing module.
    """
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        "import totally_unknown_factory_pkg\n" + _GREETING,
        encoding="utf-8",
    )

    with pytest.raises(RoleError, match="totally_unknown_factory_pkg") as exc:
        _clone(tmp_path, store)
    assert "DISTRIBUTIONS" in str(exc.value)


def test_the_slice_follows_what_the_block_needs_not_where_it_came_from(tmp_path):
    """The runtime-slice decision was gated on blocks_root being set, and the
    factory's OWN vendor mirror contains real Store shims (audit/, capture/).
    So a build with no Store checkout vendored a shim importing app.blocks,
    shipped no runtime for it, and failed the CLONER gate with "No module
    named 'app'" -- on the production default path.

    With no Store checkout the clone must now REFUSE and name the fix, rather
    than produce an artifact that cannot import.
    """
    store = _faux_store(tmp_path)
    mirror_style = store / "block_registry" / "needs_runtime"
    mirror_style.mkdir(parents=True)
    (mirror_style / "block.json").write_text(
        json.dumps({"id": "needs_runtime"}), encoding="utf-8"
    )
    (mirror_style / "block.py").write_text(_SHIM, encoding="utf-8")

    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "no-store-build")
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=None,  # no Store checkout available
        state={"resolved_blocks": ("needs_runtime",)},
    )

    # Point the mirror lookup at our shim so the source resolves without a
    # blocks_root, exactly as the real vendor mirror does.
    import app.factory.build.roles as roles_mod

    original = roles_mod._block_source_dir
    roles_mod._block_source_dir = lambda bid, root: mirror_style
    try:
        with pytest.raises(RoleError, match="CEREBRUM_BLOCKS_ROOT"):
            run_cloner(ctx)
    finally:
        roles_mod._block_source_dir = original


_FORMULA_SHIM = '''\
"""Auto-generated adapter for Cerebrum block: formula_executor."""

import asyncio
from app.blocks import get_block


def run(**kwargs):
    block_cls = get_block("formula_executor")
    instance = block_cls()
    input_data = kwargs.get("input", kwargs)
    envelope = asyncio.run(instance.execute(input_data, {}))
    return envelope.get("result", envelope)
'''

_FORMULA_V2 = '''\
from app.core.universal_base import UniversalBlock


class FormulaExecutorV2(UniversalBlock):
    async def execute(self, input_data, params):
        expr = (input_data or {}).get("expr", "ok")
        return {"status": "ok", "result": {"value": expr}}
'''


def test_cloner_aliases_kit_id_to_store_v2_registry(tmp_path):
    """Live tasting-room Approve: kit id ``formula_executor``, Store registry
    only lists ``formula_executor_v2``. CLONER used to fail with "has no
    entry for it" instead of vendoring the v2 module under the kit name."""
    store = _faux_store(tmp_path)
    reg = store / "block_registry" / "formula_executor"
    reg.mkdir(parents=True)
    (reg / "block.json").write_text(
        json.dumps({"id": "formula_executor"}), encoding="utf-8"
    )
    (reg / "block.py").write_text(_FORMULA_SHIM, encoding="utf-8")
    (store / "app" / "blocks" / "__init__.py").write_text(
        textwrap.dedent(
            '''
            import importlib

            _EXTENDED_BLOCK_DEFS = {
                "formula_executor_v2": ("app.blocks.formula_executor_v2", "FormulaExecutorV2"),
            }

            def get_block(name):
                module_path, class_name = _EXTENDED_BLOCK_DEFS[name]
                return getattr(importlib.import_module(module_path), class_name)
            '''
        ),
        encoding="utf-8",
    )
    (store / "app" / "blocks" / "formula_executor_v2.py").write_text(
        _FORMULA_V2, encoding="utf-8"
    )

    ws, result = _clone(tmp_path, store, block_ids=("formula_executor",))
    assert result.ok, result.detail

    registry = (ws.destination / "vendor" / "cerebrum" / "blocks" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "formula_executor" in registry
    assert "formula_executor_v2" in registry

    probe = textwrap.dedent(
        """
        import importlib.util, json, os, pathlib, sys
        for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
            os.environ.pop(var, None)
        path = pathlib.Path("vendor/blocks/formula_executor/block.py")
        spec = importlib.util.spec_from_file_location("vendored_formula", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.run(input={"expr": 42})
        print(json.dumps(out))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {"value": 42}


def test_store_registry_single_quotes_still_parse(tmp_path):
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "__init__.py").write_text(
        textwrap.dedent(
            """
            import importlib

            _EXTENDED_BLOCK_DEFS = {
                'greeting': ('app.blocks.greeting', 'GreetingBlock'),
            }

            def get_block(name):
                module_path, class_name = _EXTENDED_BLOCK_DEFS[name]
                return getattr(importlib.import_module(module_path), class_name)
            """
        ),
        encoding="utf-8",
    )
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail
    registry = (ws.destination / "vendor" / "cerebrum" / "blocks" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "greeting" in registry


def test_cloner_rewrites_zero_arg_store_constructors(tmp_path):
    """Live TESTER: DatabaseBlock.__init__ required hal_block and config,
    kit shims called block_cls(), and every capability died on construct."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        textwrap.dedent(
            """
            class GreetingBlock:
                def __init__(self, hal_block, config):
                    self.hal_block = hal_block
                    self.config = config

                async def execute(self, input_data, params):
                    return {"status": "ok", "result": {"greeting": "hello"}}
            """
        ),
        encoding="utf-8",
    )
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail
    shim = (ws.destination / "vendor" / "blocks" / "greeting" / "block.py").read_text(
        encoding="utf-8"
    )
    assert "_instantiate_store_block" in shim
    assert "_ensure_store_block_ready" in shim
    assert "return _ensure_store_block_ready(call())" in shim
    assert "instance = block_cls()" not in shim
    assert "_OfflineHal" in shim
    assert "block_cls(None, {})" not in shim

    probe = textwrap.dedent(
        """
        import importlib.util, json, os, pathlib
        for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
            os.environ.pop(var, None)
        path = pathlib.Path("vendor/blocks/greeting/block.py")
        spec = importlib.util.spec_from_file_location("vendored_greeting", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        print(json.dumps(module.run(input={"name": "site"})))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip()) == {"greeting": "hello"}


def test_cloner_instantiates_store_blocks_with_offline_hal_cursor(tmp_path):
    """Live TESTER: DatabaseBlock died on ``hal_block.cursor()`` because
    instantiate passed None after construct succeeded."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        textwrap.dedent(
            """
            class GreetingBlock:
                def __init__(self, hal_block, config):
                    self.hal_block = hal_block
                    self.config = config

                async def execute(self, input_data, params):
                    cur = self.hal_block.cursor()
                    cur.execute("CREATE TABLE IF NOT EXISTS t (id INTEGER)")
                    cur.execute("INSERT INTO t (id) VALUES (1)")
                    self.hal_block.commit()
                    row = self.hal_block.execute("SELECT COUNT(*) AS n FROM t").fetchone()
                    return {"status": "ok", "result": {"greeting": "hello", "rows": row[0]}}
            """
        ),
        encoding="utf-8",
    )
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail
    probe = textwrap.dedent(
        """
        import importlib.util, json, os, pathlib, tempfile
        os.environ["STORAGE_PATH"] = tempfile.mkdtemp()
        for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
            os.environ.pop(var, None)
        path = pathlib.Path("vendor/blocks/greeting/block.py")
        spec = importlib.util.spec_from_file_location("vendored_greeting_hal", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        print(json.dumps(module.run(input={"name": "site"})))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert json.loads(proc.stdout.strip()) == {"greeting": "hello", "rows": 1}


def test_cloner_emits_store_unwired_adapter_contracts(tmp_path):
    """CLONER, not a post-gate patcher, writes the four offline contracts."""
    store = _faux_store(tmp_path)
    init = (
        "import importlib\n"
        "_EXTENDED_BLOCK_DEFS = {\n"
        '    "notification": ("app.blocks.notification", "NotificationBlock"),\n'
        '    "database": ("app.blocks.database", "DatabaseBlock"),\n'
        '    "storage": ("app.blocks.storage", "StorageBlock"),\n'
        "}\n"
        "def get_block(name):\n"
        "    module_path, class_name = _EXTENDED_BLOCK_DEFS[name]\n"
        "    return getattr(importlib.import_module(module_path), class_name)\n"
    )
    (store / "app" / "blocks" / "__init__.py").write_text(init, encoding="utf-8")
    # Valid Python: the notification transform finds its construct on the
    # syntax tree (a bare ``try:`` with no handler is not a module).
    (store / "app" / "blocks" / "notification.py").write_text(
        "class NotificationBlock:\n"
        "    def send(self, block_name, payload):\n"
        "        try:\n"
        "            from vendor.cerebrum.blocks import BLOCK_REGISTRY\n"
        "            from app.dependencies import _create_block_instance\n"
        "            return BLOCK_REGISTRY\n"
        "        except Exception:\n"
        "            return None\n",
        encoding="utf-8",
    )
    (store / "app" / "blocks" / "database.py").write_text(
        "class DatabaseBlock:\n"
        "    def insert(self):\n"
        "        except Exception as e:\n"
        '            return {"error": f"Insert failed: {str(e)}"}\n'
        "    async def _query(self, data):\n"
        '        """Execute SELECT query"""\n'
        '        sql = data.get("sql")\n'
        '        params = data.get("params", ())\n'
        "        \n"
        "        try:\n"
        "            cursor = self._connection.cursor()\n"
        "            cursor.execute(sql, params)\n",
        encoding="utf-8",
    )
    (store / "app" / "blocks" / "storage.py").write_text(
        "import aiofiles\n"
        "class StorageBlock:\n"
        "    pass\n",
        encoding="utf-8",
    )
    for bid in ("notification", "database", "storage"):
        reg = store / "block_registry" / bid
        reg.mkdir(parents=True)
        (reg / "block.json").write_text(json.dumps({"id": bid}), encoding="utf-8")
        (reg / "block.py").write_text(
            "from app.blocks import get_block\n"
            f"def run(**kwargs):\n"
            f"    return get_block({bid!r})\n",
            encoding="utf-8",
        )

    ws, result = _clone(tmp_path, store, block_ids=("notification", "database", "storage"))
    assert result.ok, result.detail
    cerebrum = ws.destination / "vendor" / "cerebrum" / "blocks"
    notify = (cerebrum / "notification.py").read_text(encoding="utf-8")
    assert "Store-unwired MCP" in notify
    db = (cerebrum / "database.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS" in db
    assert "Store-unwired query" in db
    storage = (cerebrum / "storage.py").read_text(encoding="utf-8")
    assert "Store-unwired aiofiles" in storage


def _document_engine_shim() -> str:
    return (
        "from app.blocks import get_block\n"
        "def run(**kwargs):\n"
        "    return get_block('document_engine')\n"
    )


def test_cloner_copies_document_engine_parsers_package(tmp_path):
    """Live sess_a69c8ce: No module named vendor.cerebrum.blocks.document_engine.parsers."""
    store = _faux_store(tmp_path)
    init = (store / "app" / "blocks" / "__init__.py").read_text(encoding="utf-8")
    (store / "app" / "blocks" / "__init__.py").write_text(
        init.replace(
            '"farewell": ("app.blocks.farewell", "FarewellBlock"),',
            '"farewell": ("app.blocks.farewell", "FarewellBlock"),\n'
            '    "document_engine": ("app.blocks.document_engine", "DocumentEngineBlock"),',
        ),
        encoding="utf-8",
    )
    pkg = store / "app" / "blocks" / "document_engine"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "from app.core.universal_base import UniversalBlock\n"
        "from app.blocks.document_engine.parsers import Parser\n"
        "class DocumentEngineBlock(UniversalBlock):\n"
        "    async def execute(self, input_data, params):\n"
        "        return {'status': 'ok', 'text': Parser().extract_text()}\n",
        encoding="utf-8",
    )
    (pkg / "parsers" / "__init__.py").parent.mkdir()
    (pkg / "parsers" / "__init__.py").write_text(
        "class Parser:\n"
        "    def extract_text(self):\n"
        "        return 'ok'\n",
        encoding="utf-8",
    )
    reg = store / "block_registry" / "document_engine"
    reg.mkdir()
    (reg / "block.json").write_text(
        json.dumps({"id": "document_engine"}), encoding="utf-8"
    )
    (reg / "block.py").write_text(_document_engine_shim(), encoding="utf-8")

    ws, result = _clone(tmp_path, store, block_ids=("document_engine",))
    assert result.ok, result.detail
    parsers = (
        ws.destination
        / "vendor"
        / "cerebrum"
        / "blocks"
        / "document_engine"
        / "parsers"
        / "__init__.py"
    )
    assert parsers.is_file(), "CLONER must copy the parsers package"
    probe = textwrap.dedent(
        """
        from vendor.cerebrum.blocks.document_engine.parsers import Parser
        assert Parser().extract_text() == "ok"
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


_DOC_ENGINE_WRAPPER = '''\
from app.core.universal_base import UniversalBlock


class DocumentEngineBlock(UniversalBlock):
    async def execute(self, input_data, params):
        return {"status": "ok", "result": {"engine": "wrapper"}}
'''

_DOC_ENGINE_SHADOW_INIT = '''\
"""Package that shadows document_engine.py (Cerebrum-Blocks layout)."""
import importlib.util
from pathlib import Path

_BLOCK_MODULE_NAME = "app.blocks.document_engine_block"
_WRAPPER = Path(__file__).resolve().parent.parent / "document_engine.py"
_spec = importlib.util.spec_from_file_location(_BLOCK_MODULE_NAME, _WRAPPER)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
DocumentEngineBlock = _mod.DocumentEngineBlock
'''


def _document_engine_shadow_store(root: Path) -> Path:
    """Store checkout with the live package+wrapper shadow layout.

    Cerebrum-Blocks ships ``app/blocks/document_engine.py`` next to a
    ``document_engine/`` package whose ``__init__`` importlib-loads the
    sibling file under the synthetic name ``app.blocks.document_engine_block``.
    Live sess_000f9a85d339422f died in CLONER because the regex closure
    then demanded a module that is not on disk.
    """
    store = _faux_store(root)
    init = (store / "app" / "blocks" / "__init__.py").read_text(encoding="utf-8")
    (store / "app" / "blocks" / "__init__.py").write_text(
        init.replace(
            '"farewell": ("app.blocks.farewell", "FarewellBlock"),',
            '"farewell": ("app.blocks.farewell", "FarewellBlock"),\n'
            '    "document_engine": ("app.blocks.document_engine", "DocumentEngineBlock"),',
        ),
        encoding="utf-8",
    )
    blocks = store / "app" / "blocks"
    (blocks / "document_engine.py").write_text(_DOC_ENGINE_WRAPPER, encoding="utf-8")
    pkg = blocks / "document_engine"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(_DOC_ENGINE_SHADOW_INIT, encoding="utf-8")
    reg = store / "block_registry" / "document_engine"
    reg.mkdir()
    (reg / "block.json").write_text(
        json.dumps({"id": "document_engine"}), encoding="utf-8"
    )
    (reg / "block.py").write_text(_document_engine_shim(), encoding="utf-8")
    return store


def test_document_engine_package_shadow_does_not_fail_cloner(tmp_path):
    """Live sess_000f9a85d339422f: CLONER RoleError for document_engine_block.

    The Store has the wrapper file and the shadowing package; the synthetic
    importlib name is not a missing block. Closure and vendor must succeed
    and keep the package ``__init__`` load of ``../document_engine.py``
    working after rewrite.
    """
    from app.factory.build.roles import _closure_over_runtime, _store_block_defs

    store = _document_engine_shadow_store(tmp_path)
    defs = _store_block_defs(store)
    block_mods, core_mods = _closure_over_runtime(store, ["document_engine"], defs)
    assert "document_engine" in block_mods
    assert "document_engine_block" in block_mods
    assert "universal_base" in core_mods

    ws, result = _clone(tmp_path, store, block_ids=("document_engine",))
    assert result.ok, result.detail

    cerebrum = ws.destination / "vendor" / "cerebrum" / "blocks"
    assert (cerebrum / "document_engine" / "__init__.py").is_file()
    assert (cerebrum / "document_engine.py").is_file(), (
        "package __init__ loads ../document_engine.py; the wrapper must ship"
    )
    assert (cerebrum / "document_engine_block.py").is_file(), (
        "synthetic importlib name must resolve to a vendored module file"
    )
    pkg_init = (cerebrum / "document_engine" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "document_engine_block" in pkg_init
    assert "document_engine.py" in pkg_init

    probe = textwrap.dedent(
        """
        import os
        for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
            os.environ.pop(var, None)
        from vendor.cerebrum.blocks.document_engine import DocumentEngineBlock
        assert DocumentEngineBlock.__name__ == "DocumentEngineBlock"
        import vendor.cerebrum.blocks.document_engine_block as alias
        assert alias.DocumentEngineBlock.__name__ == "DocumentEngineBlock"
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout


def test_unmapped_synthetic_block_name_still_fails_closed(tmp_path):
    """A ``*_block`` import with no shadowing package must still fail loudly."""
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        "from app.blocks.ghost_block import Ghost\n" + _GREETING,
        encoding="utf-8",
    )
    with pytest.raises(RoleError, match="ghost_block"):
        _clone(tmp_path, store)


def test_cloner_converts_document_engine_module_to_parsers_package(tmp_path):
    """Store file document_engine.py that imports .parsers becomes a package."""
    store = _faux_store(tmp_path)
    init = (store / "app" / "blocks" / "__init__.py").read_text(encoding="utf-8")
    (store / "app" / "blocks" / "__init__.py").write_text(
        init.replace(
            '"farewell": ("app.blocks.farewell", "FarewellBlock"),',
            '"farewell": ("app.blocks.farewell", "FarewellBlock"),\n'
            '    "document_engine": ("app.blocks.document_engine", "DocumentEngineBlock"),',
        ),
        encoding="utf-8",
    )
    (store / "app" / "blocks" / "document_engine.py").write_text(
        "from app.core.universal_base import UniversalBlock\n"
        "from app.blocks.document_engine.parsers import Parser\n"
        "class DocumentEngineBlock(UniversalBlock):\n"
        "    async def execute(self, input_data, params):\n"
        "        return {'status': 'ok', 'parser': Parser}\n",
        encoding="utf-8",
    )
    reg = store / "block_registry" / "document_engine"
    reg.mkdir()
    (reg / "block.json").write_text(
        json.dumps({"id": "document_engine"}), encoding="utf-8"
    )
    (reg / "block.py").write_text(_document_engine_shim(), encoding="utf-8")

    ws, result = _clone(tmp_path, store, block_ids=("document_engine",))
    assert result.ok, result.detail
    assert not (ws.destination / "vendor" / "cerebrum" / "blocks" / "document_engine.py").is_file()
    parsers = (
        ws.destination
        / "vendor"
        / "cerebrum"
        / "blocks"
        / "document_engine"
        / "parsers"
        / "__init__.py"
    )
    assert parsers.is_file()
    assert "Store-unwired document_engine.parsers" in parsers.read_text(encoding="utf-8")
    probe = textwrap.dedent(
        """
        from vendor.cerebrum.blocks.document_engine.parsers import Parser
        assert Parser().extract_text() == ""
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def test_cloner_rewrites_workflow_child_constructors_and_result_key(tmp_path):
    """Live sess_f1fe691: workflow step_0 constructed DatabaseBlock().

    PRODUCT accept-payload:
        appointment_scheduling rejected a payload built from its own schema:
        workflow: step_0 (database): DatabaseBlock.__init__() missing 2
        required positional arguments: 'hal_block' and 'config'

    The kit shim rewrite only matched ``block_cls()``. The Store workflow
    module used ``get_block(name)()`` / ``DatabaseBlock()`` after the
    Store-host ``_create_block_instance`` import failed, and then
    ``envelope["result"]`` (automated_reminders ``RuntimeError: 'result'``).
    """
    store = _faux_store(tmp_path)
    init = (store / "app" / "blocks" / "__init__.py").read_text(encoding="utf-8")
    (store / "app" / "blocks" / "__init__.py").write_text(
        init.replace(
            '"farewell": ("app.blocks.farewell", "FarewellBlock"),',
            '"farewell": ("app.blocks.farewell", "FarewellBlock"),\n'
            '    "database": ("app.blocks.database", "DatabaseBlock"),\n'
            '    "workflow": ("app.blocks.workflow", "WorkflowBlock"),',
        ),
        encoding="utf-8",
    )
    (store / "app" / "blocks" / "database.py").write_text(
        textwrap.dedent(
            """
            class DatabaseBlock:
                def __init__(self, hal_block, config):
                    self.hal_block = hal_block
                    self.config = config

                async def execute(self, input_data, params):
                    return {"status": "ok", "inserted": True}
            """
        ),
        encoding="utf-8",
    )
    (store / "app" / "blocks" / "workflow.py").write_text(
        textwrap.dedent(
            """
            from app.blocks import get_block

            class WorkflowBlock:
                def __init__(self, hal_block, config):
                    self.hal_block = hal_block
                    self.config = config

                async def execute(self, input_data, params):
                    results = []
                    for i, step in enumerate((input_data or {}).get("steps") or []):
                        name = step.get("block")
                        try:
                            from app.dependencies import _create_block_instance
                            inst = _create_block_instance(get_block(name))
                        except ImportError:
                            inst = get_block(name)()
                        envelope = await inst.execute(step.get("input") or {}, {})
                        results.append({
                            "step_id": f"step_{i}",
                            "block": name,
                            "status": "success",
                            "result": envelope["result"],
                        })
                    return {"status": "success", "results": results}
            """
        ),
        encoding="utf-8",
    )
    for bid in ("database", "workflow"):
        reg = store / "block_registry" / bid
        reg.mkdir()
        (reg / "block.json").write_text(json.dumps({"id": bid}), encoding="utf-8")
        (reg / "block.py").write_text(
            textwrap.dedent(
                f"""
                import asyncio
                from app.blocks import get_block

                def run(**kwargs):
                    block_cls = get_block({bid!r})
                    instance = block_cls()
                    envelope = asyncio.run(
                        instance.execute(kwargs.get("input", kwargs), {{}})
                    )
                    return envelope.get("result", envelope)
                """
            ),
            encoding="utf-8",
        )

    ws, result = _clone(tmp_path, store, block_ids=("database", "workflow"))
    assert result.ok, result.detail
    workflow = (
        ws.destination / "vendor" / "cerebrum" / "blocks" / "workflow.py"
    ).read_text(encoding="utf-8")
    assert "_instantiate_store_block(get_block(name))" in workflow
    assert "get_block(name)()" not in workflow
    assert 'envelope.get("result", envelope)' in workflow
    assert 'envelope["result"]' not in workflow
    assert "def _create_block_instance" in workflow
    assert "from app.dependencies import _create_block_instance" not in workflow
    assert "_OfflineHal" in workflow

    probe = textwrap.dedent(
        """
        import importlib.util, json, os, pathlib, tempfile
        os.environ["STORAGE_PATH"] = tempfile.mkdtemp()
        for var in ("CEREBRUM_API_URL", "CEREBRUM_API_KEY", "CEREBRUM_API_TOKEN"):
            os.environ.pop(var, None)
        path = pathlib.Path("vendor/blocks/workflow/block.py")
        spec = importlib.util.spec_from_file_location("vendored_workflow", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        out = module.run(input={"steps": [{"block": "database", "input": {"table": "appointment"}}]})
        print(json.dumps(out, default=str))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    body = json.loads(proc.stdout.strip())
    assert body.get("status") == "success"
    assert body["results"][0]["block"] == "database"
    assert "hal_block" not in str(body)



# ── what the slice scan counts as a dependency ─────────────────────────────
#
# All four below come from one live CLONER refusal against the real Store:
#
#     CLONER failed: runtime slice needs app/core/redline.py which does not
#     exist in the Store checkout
#
# That one was true -- app/blocks/ocr.py had been copied into the Store
# without the two app/core modules it imports, so the block could not read a
# single pixel and the clone was right to refuse. Walking the whole 136-block
# registry afterwards turned up three MORE refusals that were not true, each a
# different way of mis-reading what a dependency is.


def test_a_core_dependency_may_be_a_package_not_only_a_module(tmp_path):
    """``app.core.rag`` is app/core/rag/{__init__,retriever}.py in the real
    Store. The core loop demanded rag.py and failed the clone of every block
    that touches retrieval, while the block loop directly above it had always
    accepted a package. One resolver disagreeing with the other about what a
    module is IS the defect."""
    store = _faux_store(tmp_path)
    rag = store / "app" / "core" / "rag"
    (rag / "sub").mkdir(parents=True)
    (rag / "__init__.py").write_text(
        "from .retriever import retrieve\n", encoding="utf-8"
    )
    (rag / "retriever.py").write_text(
        "def retrieve(q):\n    return [q]\n", encoding="utf-8"
    )
    (rag / "sub" / "__init__.py").write_text("DEPTH = 2\n", encoding="utf-8")
    (store / "app" / "blocks" / "greeting.py").write_text(
        _GREETING
        + textwrap.dedent(
            """
            def search(q):
                from app.core.rag.retriever import retrieve
                return retrieve(q)
            """
        ),
        encoding="utf-8",
    )

    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    # Copied as a PACKAGE. Written as a flat "rag.py" it would be an empty
    # file, turning a resolved slice back into a ModuleNotFoundError on the
    # customer's machine -- a worse failure than refusing the clone.
    vendored = ws.destination / "vendor" / "cerebrum" / "core" / "rag"
    assert (vendored / "__init__.py").is_file()
    assert (vendored / "retriever.py").is_file()
    assert (vendored / "sub" / "__init__.py").is_file(), "nested package dropped"
    assert not (vendored.parent / "rag.py").exists()

    probe = "from vendor.cerebrum.core.rag import retrieve; assert retrieve('x') == ['x']"
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ws.destination),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr


def test_a_submodule_imported_from_app_blocks_is_vendored_not_looked_up(tmp_path):
    """``from app.blocks import _knowledge as kb`` imports a SUBMODULE.

    app/blocks/_knowledge.py is a shared helper no registry entry points at,
    so the class lookup refused construction_advisor with "the Store registry
    maps no block to that class". Two bugs in one line: the alias was never
    stripped, so the lookup key was the whole string ``_knowledge as kb``.
    """
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "_knowledge.py").write_text(
        "def search_knowledge(q, top_k=5):\n    return [q] * top_k\n", encoding="utf-8"
    )
    (store / "app" / "blocks" / "greeting.py").write_text(
        "from app.blocks import _knowledge as kb\n"
        + _GREETING
        + textwrap.dedent(
            """
            def lookup(q):
                return kb.search_knowledge(q, top_k=2)
            """
        ),
        encoding="utf-8",
    )

    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail
    assert (
        ws.destination / "vendor" / "cerebrum" / "blocks" / "_knowledge.py"
    ).is_file()


def test_a_module_named_only_in_a_comment_or_a_string_is_not_a_dependency(tmp_path):
    """The real one: action_contract/registry.py documents an OPTIONAL plugin
    namespace in a comment and names it in a constant --

        DOMAINS_PACKAGE = "app.blocks.domains"

    -- which pkgutil discovers when present. The scan demanded the package
    exist and failed medical_ehr_connector's clone over prose.
    """
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        _GREETING
        + textwrap.dedent(
            '''
            # optional, discovered at runtime: app.blocks.domains.<kit>
            DOMAINS_PACKAGE = "app.blocks.domains"
            HELP = """see app.core.nonexistent for details"""
            '''
        ),
        encoding="utf-8",
    )

    ws, result = _clone(tmp_path, store)

    assert result.ok, result.detail
    assert not (ws.destination / "vendor" / "cerebrum" / "core" / "nonexistent.py").exists()


def test_a_genuinely_missing_core_module_still_fails_the_clone(tmp_path):
    """The guard on the three tests above: they must not have taught the scan
    to shrug. A real import of a module the Store does not have is the live
    app.core.redline case, and vendoring around it would ship a latent
    ImportError to the customer instead of failing here, where it is cheap.
    """
    store = _faux_store(tmp_path)
    (store / "app" / "blocks" / "greeting.py").write_text(
        _GREETING
        + textwrap.dedent(
            """
            def markup(path):
                from app.core.redline import detect_redlines
                return detect_redlines(path)
            """
        ),
        encoding="utf-8",
    )

    with pytest.raises(RoleError, match=r"app/core/redline\.py"):
        _clone(tmp_path, store)


# ── a block the Store registers with a manifest and no adapter ─────────────
#
# Live: "CLONER failed -- registered_block_missing: 3 block(s) registered but
# not on disk". Twenty Store blocks, the domain containers among them, are
# registered with a signed block.json and NO block.py. The CLONER copied the
# manifest, recorded the block as vendored, and its own gate then demanded a
# file that had never existed anywhere.


def _manifest_only_store(tmp_path, *, with_runtime=True):
    """The faux Store plus ``farewell``: registered, signed-shaped, no block.py."""
    store = _faux_store(tmp_path)
    # The generated adapter the Store ships for its other blocks -- the ONLY
    # place the adapter's shape exists; the Factory derives it from here.
    (store / "block_registry" / "greeting" / "block.py").write_text(
        '"""\nAuto-generated adapter for Cerebrum block: greeting\n"""\n'
        "import asyncio\nfrom app.blocks import get_block\n\n\n"
        "def run(**kwargs):\n"
        '    block_cls = get_block("greeting")\n'
        "    envelope = asyncio.run(block_cls().execute(kwargs.get('input', kwargs), {}))\n"
        "    return envelope.get('result', envelope)\n",
        encoding="utf-8",
    )
    reg = store / "block_registry" / "farewell"
    reg.mkdir(parents=True)
    (reg / "block.json").write_text(json.dumps({"id": "farewell"}), encoding="utf-8")
    if with_runtime:
        (store / "app" / "blocks" / "farewell.py").write_text(
            _GREETING.replace("GreetingBlock", "FarewellBlock").replace("hello", "goodbye"),
            encoding="utf-8",
        )
    return store


def test_a_manifest_only_block_clones_and_passes_the_cloners_own_gate(tmp_path):
    store = _manifest_only_store(tmp_path)

    ws, result = _clone(tmp_path, store, block_ids=("farewell",))
    assert result.ok, result.detail

    gate = gate_blocks_import_offline(
        GateContext(workspace=ws.destination, role=BuildRole.CLONER, vendored_blocks=("farewell",))
    )
    assert gate.ok, f"{gate.reason}: {gate.findings}"


def test_the_emitted_adapter_runs_the_real_store_block_not_a_stub(tmp_path):
    """The line this repo drew after the always-ok estate blocks: a stub
    answers on its own. This one can only answer with what the Store's
    runtime returned -- "goodbye", which exists nowhere but farewell.py."""
    store = _manifest_only_store(tmp_path)
    ws, result = _clone(tmp_path, store, block_ids=("farewell",))
    assert result.ok, result.detail

    probe = textwrap.dedent(
        """
        import importlib.util, json, pathlib
        spec = importlib.util.spec_from_file_location("a", pathlib.Path("vendor/blocks/farewell/block.py"))
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        print(json.dumps(mod.run(input={"name": "x"})))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", probe], cwd=str(ws.destination),
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "goodbye x" in proc.stdout


def test_the_adapter_is_derived_from_the_store_and_written_down_nowhere(tmp_path):
    """Owner: "dont hard wire anything". The Factory holds no adapter text.
    Change the adapter the Store ships and the emitted one changes with it."""
    from app.factory.build.roles_handlers import _adapter_shape_cache

    store = _manifest_only_store(tmp_path)
    shipped = store / "block_registry" / "greeting" / "block.py"
    shipped.write_text(
        shipped.read_text(encoding="utf-8") + "\nSTORE_GENERATOR_VERSION = 'next'\n",
        encoding="utf-8",
    )
    _adapter_shape_cache.clear()

    ws, result = _clone(tmp_path, store, block_ids=("farewell",))
    assert result.ok, result.detail

    emitted = (ws.destination / "vendor" / "blocks" / "farewell" / "block.py").read_text(encoding="utf-8")
    assert "STORE_GENERATOR_VERSION = 'next'" in emitted
    assert 'get_block("farewell")' in emitted and "greeting" not in emitted


def test_a_manifest_only_block_with_no_runtime_is_refused_by_name(tmp_path):
    """An adapter around nothing is worse than a refusal. The live pair:
    action_contract and finance_ops have a manifest and no runtime entry."""
    store = _manifest_only_store(tmp_path, with_runtime=False)
    init = store / "app" / "blocks" / "__init__.py"
    init.write_text(
        init.read_text(encoding="utf-8").replace(
            '    "farewell": ("app.blocks.farewell", "FarewellBlock"),\n', ""
        ),
        encoding="utf-8",
    )

    with pytest.raises(RoleError, match=r"farewell.*manifest only"):
        _clone(tmp_path, store, block_ids=("farewell",))


def test_a_block_that_ships_its_own_adapter_is_left_exactly_as_the_store_wrote_it(tmp_path):
    store = _faux_store(tmp_path)
    ws, result = _clone(tmp_path, store)
    assert result.ok, result.detail

    vendored = (ws.destination / "vendor" / "blocks" / "greeting" / "block.py").read_text(encoding="utf-8")
    assert "Auto-generated adapter for Cerebrum block: greeting" in vendored
