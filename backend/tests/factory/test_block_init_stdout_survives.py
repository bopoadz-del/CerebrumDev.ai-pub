"""A block's init-time stdout must not turn a passing capability red.

A vendored block may print a non-ASCII glyph (a checkmark, a currency sign,
an accented place name) the moment it is imported. On an ascii/C-locale CI,
a cp1252 console, or under pytest's fd-level capture, the sink encodes
strictly and has no ``reconfigure`` -- so that one incidental ``print`` raises
UnicodeEncodeError while the block is being loaded. The generated platform's
Factory-rendered block dispatch used to let that encoding accident escape
load_block (or be caught and rewritten to ``ok: False``), sinking every
capability that merely bound the block. A build reddened not because a
capability refused but because a glyph could not be encoded.

This renders a minimal generated product -- a block that prints a glyph at
import, bound through the Factory's own ``app/dispatch.py`` runtime -- loads
it with a strict ascii sink standing in for pytest's C-locale fd-capture,
and asserts the capability still returns its success envelope (ok:True with
record/id). It is general: nothing here is keyed to any product, brief,
queue block, PM, or test name.

FAILS on master (the glyph raises out of load_block / is rewritten to
ok:False); PASSES on the branch (dispatch loads and runs the block under
stdio that cannot raise on a non-ASCII log).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from app.factory.build.roles import _DISPATCH_RUNTIME

# A glyph no ascii/cp1252 sink can encode. General stand-in for any non-ASCII
# log a block may emit; not keyed to any specific block's output.
_GLYPH = "✓"

_BLOCK_SRC = (
    "print('" + _GLYPH + " vendored block ready')  # non-ASCII log at import\n"
    "\n"
    "def run(input=None, **kwargs):\n"
    "    payload = input or {}\n"
    "    return {\n"
    "        'ok': True,\n"
    "        'record': {'id': 'rec-1', 'ping': payload.get('ping')},\n"
    "        'id': 'rec-1',\n"
    "    }\n"
)


class _AsciiCaptureStream:
    """Stand-in for pytest's fd-capture under a C/ascii locale.

    Encodes strictly, raises on the first glyph it cannot map, and -- like
    pytest's EncodedFile -- exposes no ``reconfigure``, so forcing UTF-8 on it
    is a no-op and only tolerant dispatch can carry the call.
    """

    encoding = "ascii"
    errors = "strict"

    def __init__(self) -> None:
        self.buf: list = []

    def write(self, s) -> int:
        s = s if isinstance(s, str) else str(s)
        s.encode("ascii")  # raises UnicodeEncodeError on a non-ASCII glyph
        self.buf.append(s)
        return len(s)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _build_mini_product(root: Path) -> Path:
    (root / "app").mkdir(parents=True, exist_ok=True)
    (root / "vendor" / "blocks" / "glyph_block").mkdir(parents=True, exist_ok=True)
    (root / "app" / "dispatch.py").write_text(_DISPATCH_RUNTIME, encoding="utf-8")
    (root / "vendor" / "blocks" / "glyph_block" / "block.py").write_text(
        _BLOCK_SRC, encoding="utf-8"
    )
    (root / "blocks.lock.json").write_text(
        json.dumps({"blocks": {"glyph_block": "vendor/blocks/glyph_block/block.py"}}),
        encoding="utf-8",
    )
    return root


def _load_dispatch(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        pass
    return module


def test_block_init_glyph_does_not_sink_capability(tmp_path):
    mini = _build_mini_product(tmp_path)

    hostile = _AsciiCaptureStream()
    saved_out, saved_err = sys.stdout, sys.stderr
    mod_name = "mini_dispatch_%s" % abs(hash(str(tmp_path)))
    error = None
    result = None
    try:
        sys.stdout = hostile
        sys.stderr = hostile
        try:
            dispatch = _load_dispatch(mini / "app" / "dispatch.py", mod_name)
            result = dispatch.execute("glyph_block", {"ping": 7}, action="create")
        except Exception as exc:  # noqa: BLE001 -- recorded and asserted below
            error = exc
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
        sys.modules.pop(mod_name, None)

    assert error is None, (
        "a block's init-time non-ASCII log raised instead of being tolerated: "
        "%r" % (error,)
    )
    assert isinstance(result, dict), result
    assert result.get("ok") is True, result
    assert result.get("record"), "success envelope lost its record: %r" % (result,)
    record_id = result.get("id") or (result.get("record") or {}).get("id")
    assert record_id, "success envelope lost its id: %r" % (result,)
