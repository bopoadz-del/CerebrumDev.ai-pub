"""CLONER runtime transforms are chosen by STRUCTURE, never by module name.

emit_runtime_module used to dispatch on Store block names (notification,
database, document_engine, vector_search, storage, capture, workflow). Every
transform now finds its own target construct and every transform runs on
every module: a module named like a real block but WITHOUT the construct is
left as it is, and an invented module WITH the construct is transformed.
"""

from __future__ import annotations

import ast
import itertools
import os
from pathlib import Path

import pytest

from app.factory.build.offline_adapters import (
    RUNTIME_TRANSFORMS,
    _inside_import_error_guard,
    emit_runtime_module,
)

NOTIFY = (
    "def send(block_name, payload):\n"
    "    try:\n"
    "        try:\n"
    "            from vendor.cerebrum.blocks import BLOCK_REGISTRY\n"
    "            from app.dependencies import _create_block_instance\n"
    "            block = _create_block_instance(BLOCK_REGISTRY[block_name])\n"
    "        except Exception:\n"
    "            return {}\n"
    "    finally:\n"
    "        pass\n"
)
DB = (
    "class ZorblatStore:\n"
    "    def insert(self, table, values, sql):\n"
    "        try:\n"
    "            pass\n"
    "        except Exception as e:\n"
    '            return {"error": f"Insert failed: {str(e)}"}\n'
    "    def query(self, data):\n"
    "        \"\"\"Execute SELECT query\"\"\"\n"
    "        sql = data.get(\"sql\")\n"
    "        params = data.get(\"params\", ())\n"
    "        \n"
    "        try:\n"
    "            cursor = self._connection.cursor()\n"
    "            cursor.execute(sql, params)\n"
    "        except Exception:\n"
    "            raise\n"
)
PDF = "from zorblat_pdf import PdfReader\n\ndef read(path):\n    return PdfReader(path)\n"
SKLEARN = "import sklearn\n\ndef rank(x):\n    return sklearn\n"
AIO = "import aiofiles\n\nasync def save(p):\n    return aiofiles\n"
PLAIN = "def process(data):\n    return {'status': 'success', 'data': data}\n"


def _host_import_guarded(text: str) -> bool:
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.dependencies":
            return _inside_import_error_guard(tree, node)
    return False


@pytest.mark.parametrize(
    "src, transformed",
    [
        (NOTIFY, lambda out: _host_import_guarded(out)),
        (DB, lambda out: "CREATE TABLE IF NOT EXISTS" in out and "if not sql:" in out),
        (PDF, lambda out: "for _pdf_name in ('zorblat_pdf',):" in out),
        (SKLEARN, lambda out: "def _install_offline_sklearn" in out),
        (AIO, lambda out: "except ImportError:" in out),
    ],
)
def test_an_invented_module_with_the_construct_is_transformed(src, transformed):
    out = emit_runtime_module("zorblat_mod", src)
    assert out != src and transformed(out)
    ast.parse(out)
    assert emit_runtime_module("zorblat_mod", out) == out  # idempotent


@pytest.mark.parametrize(
    "name",
    ["notification", "event_bus", "database", "document_engine", "vector_search",
     "storage", "capture", "workflow"],
)
def test_a_module_named_like_a_real_block_without_the_construct_is_untouched(name):
    assert emit_runtime_module(name, PLAIN) == PLAIN
    assert emit_runtime_module(f"app.blocks.{name}", PLAIN) == PLAIN


def _apply(order, text):
    for transform in order:
        text = transform(text)
    return text


def test_every_order_of_the_transforms_gives_one_result():
    """A module carrying every construct, each in its own statement."""
    src = "\n".join([PDF, SKLEARN, AIO, NOTIFY, DB])
    ast.parse(src)
    results = {_apply(order, src) for order in itertools.permutations(RUNTIME_TRANSFORMS)}
    assert len(results) == 1
    (out,) = results
    ast.parse(out)
    assert _apply(RUNTIME_TRANSFORMS, out) == out


def _store_modules():
    root = os.environ.get("CEREBRUM_BLOCKS_ROOT", "").strip()
    if not root or not (Path(root) / "app" / "blocks").is_dir():
        return []
    base = Path(root) / "app"
    return sorted(
        p for sub in ("blocks", "core") for p in (base / sub).rglob("*.py")
        if "__pycache__" not in p.parts
    )


@pytest.mark.skipif(not _store_modules(), reason="needs CEREBRUM_BLOCKS_ROOT (CI sets it)")
def test_on_every_store_module_the_transforms_commute_are_idempotent_and_keep_it_parsing():
    for path in _store_modules():
        src = path.read_text(encoding="utf-8")
        try:
            ast.parse(src)
        except SyntaxError:
            continue
        active = [t for t in RUNTIME_TRANSFORMS if t(src) != src]
        outputs = {_apply(order, src) for order in itertools.permutations(active)} or {src}
        assert len(outputs) == 1, path
        (out,) = outputs
        assert _apply(RUNTIME_TRANSFORMS, out) == out, path
        ast.parse(out)


def test_no_module_is_replaced_by_the_capture_adapter():
    from app.factory.build.network_posture import P1_CAPTURE_ADAPTER

    for name in ("capture", "zorblat_capture"):
        assert emit_runtime_module(name, PLAIN) != P1_CAPTURE_ADAPTER
