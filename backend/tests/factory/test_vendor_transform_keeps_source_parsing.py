"""Vendoring rewrites must never turn Store source that parses into source that does not.

emit_store_host_di deleted every ``from app.dependencies import
_create_block_instance`` line -- written for the Store's old shape. The
Store has since wrapped that import in its own ``try/except ImportError``
with a plain-construction fallback for vendored runtimes. Deleting the line
there left ``try:`` with no body: build sess_065fc3eac75c4f62 (FinOps)
shipped a notification.py that did not parse, every notification fell back
to a local outbox while the product reported email/Slack as "ready", and the
build still went 13/13 because nothing exercised the block.
"""

from __future__ import annotations

import pytest

from app.factory.build import roles_handlers
from app.factory.build.offline_adapters import emit_runtime_module, emit_store_host_di
from app.factory.build.roles_handlers import (
    VENDOR_TRANSFORM_BROKE_SOURCE,
    _prepare_cloned_python,
    _rewrite_runtime_imports,
)
from app.factory.build.roles_models import RoleError

# The Store's notification MCP dispatch, verbatim in shape (Cerebrum-Blocks
# app/blocks/notification.py at d8987205).
STORE_GUARDED = '''\
import json


class NotificationBlock:
    async def _send_mcp(self, data):
        block_name = data.get("block") or data.get("tool")
        payload = data.get("payload") or {}
        params = data.get("params", {})
        try:
            from app.blocks import BLOCK_REGISTRY

            try:
                from app.dependencies import _create_block_instance
            except ImportError:
                # Standalone/vendored runtime (a factory-built platform).
                def _create_block_instance(block_class, config=None, allow_platform=True):
                    return block_class()

            if block_name not in BLOCK_REGISTRY:
                return {"status": "error", "error": "not found"}
            block = _create_block_instance(BLOCK_REGISTRY[block_name])
            result = await block.execute(payload, params)
            return {"status": "success", "result_preview": json.dumps(result, default=str)[:500]}
        except Exception as e:
            return {"status": "error", "error": f"MCP dispatch failed: {e}"}
'''

OLD_UNGUARDED = '''\
def build():
    try:
        from vendor.cerebrum.blocks import BLOCK_REGISTRY
        from app.dependencies import _create_block_instance
    except Exception:
        pass
'''

IMPORT = "from app.dependencies import _create_block_instance"


def _vendor(source: str, module: str = "app.blocks.notification") -> str:
    return _prepare_cloned_python(
        emit_runtime_module(module, _rewrite_runtime_imports(source)),
        label=f"vendor/cerebrum/blocks/{module.rsplit('.', 1)[-1]}.py",
    )


def test_the_stores_guarded_notification_still_parses_after_vendoring():
    out = _vendor(STORE_GUARDED)

    compile(out, "notification.py", "exec")
    assert IMPORT in out, "the guarded import must stay so the Store's fallback runs"
    assert "def _create_block_instance(block_class, config=None, allow_platform=True)" in out


def test_the_old_unguarded_import_is_still_stripped():
    out = emit_store_host_di(OLD_UNGUARDED)

    assert IMPORT not in out
    compile(out, "old.py", "exec")


def test_a_rewrite_that_breaks_parsing_fails_cloner_by_name(monkeypatch):
    from app.factory.build import offline_adapters

    def _breaks(text: str) -> str:  # any future drift of the same kind
        return text.replace("    return 1\n", "")

    monkeypatch.setattr(offline_adapters, "emit_store_host_di", _breaks)
    source = "def f():\n    try:\n        return 1\n    except ImportError:\n        pass\n"

    with pytest.raises(RoleError) as err:
        _prepare_cloned_python(source, label="vendor/cerebrum/blocks/example.py")

    assert err.value.reason == VENDOR_TRANSFORM_BROKE_SOURCE
    assert err.value.location == "CLONER"
    assert "vendor/cerebrum/blocks/example.py" in str(err.value)
    assert "line" in str(err.value)


def test_source_the_store_already_shipped_broken_is_not_blamed_on_the_rewrite():
    """The guard fires on damage the factory did, not on the Store's own."""
    already_broken = "def f(:\n    pass\n"

    out = _prepare_cloned_python(already_broken, label="x.py")

    assert out is not None


def test_a_bom_prefixed_store_file_vendors_cleanly():
    """The guard's first catch: the helper injection PREPENDS text, which
    buried a leading BOM mid-file where it is a SyntaxError."""
    out = _prepare_cloned_python("\ufeff" + OLD_UNGUARDED, label="bom.py")

    compile(out, "bom.py", "exec")
    assert "\ufeff" not in out
    assert IMPORT not in out


def test_every_vendored_module_goes_through_the_guard():
    """Both call sites in _vendor_runtime_slice label their module."""
    import inspect

    src = inspect.getsource(roles_handlers._vendor_runtime_slice)
    assert src.count("_prepare_cloned_python(") == 2
    assert src.count("label=") >= 2


# app/blocks/video_anomaly_trigger.py shape: a docstring, then
# ``from __future__ import annotations``, then an unguarded host import.
FUTURE_FIRST = '''\
"""Video anomaly trigger."""

from __future__ import annotations

from typing import Any


def build() -> Any:
    try:
        from vendor.cerebrum.blocks import BLOCK_REGISTRY
        from app.dependencies import _create_block_instance
    except Exception:
        return None
    return BLOCK_REGISTRY
'''


def test_a_future_import_module_still_parses_after_the_helper_is_added():
    """The sweep's second catch: the helper was PREPENDED at byte 0, ahead of
    ``from __future__``, which Python refuses. video_anomaly_trigger.py
    shipped unparseable on every build that vendored it."""
    out = _vendor(FUTURE_FIRST, module="app.blocks.video_anomaly_trigger")

    compile(out, "video_anomaly_trigger.py", "exec")
    lines = [l for l in out.splitlines() if l.strip()]
    assert lines[0].startswith('"""Video anomaly trigger')
    assert lines[1] == "from __future__ import annotations"


def test_insert_after_future_imports_places_code_where_python_allows_it():
    from app.factory.build.offline_adapters import insert_after_future_imports

    block = "HELPER = 1\n"
    for source in (
        FUTURE_FIRST,
        "from __future__ import annotations\nx = 1\n",
        '"""Doc only."""\nx = 1\n',
        "x = 1\n",
        "",
    ):
        out = insert_after_future_imports(source, block)
        compile(out, "t.py", "exec")
        assert "HELPER = 1" in out
