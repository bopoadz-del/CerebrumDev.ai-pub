"""The coder's static gate must be AST-based, not a substring deny-list.

A prior audit showed the substring gate accepted ``from os import environ``,
aliased imports, odd spacing, and ``getattr(__builtins__, "o"+"pen")`` — a
prompt-injected brief could get env-reading / process-spawning code into the
export that runs on the customer's machine. These tests pin the AST behavior.
"""

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.factory.coder import CoderError, _validate_body  # noqa: E402


@pytest.mark.parametrize(
    "body",
    [
        "import os\nreturn {}",
        "import os as o\nreturn {}",
        "from os import environ\nreturn {}",
        "from os.path import join\nreturn {}",
        "import  subprocess\nreturn {}",           # odd spacing
        "import importlib\nreturn {}",
        "import ctypes\nreturn {}",
        "import pickle\nreturn {}",
        "eval('1')\nreturn {}",
        "exec('x=1')\nreturn {}",
        "data = open('/etc/passwd').read()\nreturn {}",
        "b = getattr(__builtins__, 'open')\nreturn {}",
        "c = ().__class__.__bases__[0].__subclasses__()\nreturn {}",
        "return {unclosed",                          # syntax error
    ],
)
def test_gate_rejects_escape_constructs(body):
    with pytest.raises(CoderError):
        _validate_body(body, "cap")


@pytest.mark.parametrize(
    "body",
    [
        'return {"ok": True}',
        "import math\nreturn {\"v\": math.sqrt(4)}",
        "import json\nimport datetime\nreturn {\"ok\": True}",
        "import re\nreturn {\"m\": bool(re.match(r'a', arguments.get('x', '')))}",
        "action = arguments.get('action')\n_STATE['n'] = _STATE.get('n', 0) + 1\nreturn {\"ok\": True, \"n\": _STATE['n']}",
    ],
)
def test_gate_allows_safe_stdlib_and_state(body):
    # Should not raise — safe stdlib and _STATE use are legitimate.
    out = _validate_body(body, "cap")
    assert out  # returns the indented body
