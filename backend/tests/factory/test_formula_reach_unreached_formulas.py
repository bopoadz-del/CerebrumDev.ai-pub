"""H-1 static reach check: unreached_formulas lists the formula functions
no product code reaches, and audit_product_formulas records them.

Every test builds a small product tree and asserts the exact result; each
is parametrized with two or more inputs.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.factory.build import formula_resolution as fr


def _tree(root: Path, files: dict) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


FORMULAS = (
    "def zeta(x):\n    return x * 2\n\n"
    "def alpha(x):\n    return zeta(x) + 1\n\n"
    "async def beta(x):\n    return x\n\n"
    "def _helper(x):\n    return x\n"
)


@pytest.mark.parametrize("files, expect", [
    # nothing outside the formula module uses any formula
    ({"app/formulas.py": FORMULAS}, ["alpha", "beta", "zeta"]),
    # an action imports alpha: alpha and zeta (called by alpha) are reached
    ({"app/formulas.py": FORMULAS,
      "app/actions/quote.py": "from app.formulas import alpha\n"},
     ["beta"]),
    # attribute use reaches beta only; zeta is used only inside the module
    ({"app/formulas.py": FORMULAS,
      "app/actions/quote.py": "import app.formulas as f\nf.beta(1)\n"},
     ["alpha", "zeta"]),
    # a use from tests does not count as a route reaching it
    ({"app/formulas.py": FORMULAS,
      "app/tests/test_f.py": "from app.formulas import alpha, beta, zeta\n"},
     ["alpha", "beta", "zeta"]),
    # an unparseable action is skipped; the parseable one still counts
    ({"app/formulas.py": FORMULAS,
      "app/actions/bad.py": "def broken(:\n",
      "app/actions/good.py": "from app.formulas import beta, zeta\n"},
     ["alpha"]),
])
def test_unreached_formulas_lists_exactly_the_unreached(tmp_path, files, expect):
    assert fr.unreached_formulas(_tree(tmp_path, files)) == expect


@pytest.mark.parametrize("files", [
    {},
    {"app/formulas.py": "def broken(:\n"},
    {"app/formulas.py": "RATE = 3\n", "app/actions/a.py": "x = 1\n"},
])
def test_unreached_formulas_is_empty_without_a_formula_function(tmp_path, files):
    assert fr.unreached_formulas(_tree(tmp_path, files)) == []


@pytest.mark.parametrize("source, expect", [
    ("from m import a, b as c\nx.y\nz\n", {"a", "b", "x", "y", "z"}),
    ("def f(p):\n    return q.r(p)\n", {"p", "q", "r"}),
])
def test_names_used_reads_names_attributes_and_imports(source, expect):
    assert fr._names_used(ast.parse(source)) == expect


@pytest.mark.parametrize("source, expect", [
    (FORMULAS, ["alpha", "beta", "zeta"]),
    ("class K:\n    def m(self):\n        pass\n\ndef _p():\n    pass\n\ndef q():\n    pass\n", ["q"]),
])
def test_top_functions_are_the_public_module_level_functions(source, expect):
    assert sorted(fr._top_functions(ast.parse(source))) == expect


@pytest.mark.parametrize("action, unreached", [
    ("from app.formulas import alpha\n", ["beta"]),
    ("from app.formulas import alpha, beta\n", []),
])
def test_audit_product_formulas_records_the_unreached(tmp_path, action, unreached):
    root = _tree(tmp_path, {"app/formulas.py": FORMULAS, "app/actions/quote.py": action})
    assert fr.audit_product_formulas(root, [], []) == []
    rec = json.loads((root / fr.FORMULA_FINDINGS_REL).read_text(encoding="utf-8"))
    assert rec == {"errors": [], "unreached": unreached}
