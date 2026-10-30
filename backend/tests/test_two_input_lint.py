"""CI-M-b: the two-input lint reddens on a new single-input value test."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

GATE = next(p / "scripts" / "two_input_lint.py" for p in Path(__file__).resolve().parents if (p / "scripts" / "two_input_lint.py").is_file())
spec = importlib.util.spec_from_file_location("two_input_lint", GATE)
mg = importlib.util.module_from_spec(spec)
sys.modules["two_input_lint"] = mg
spec.loader.exec_module(mg)

FN = (
    "def fee(amount, rate):\n"
    "    if amount < 0:\n"
    "        raise ValueError('negative')\n"
    "    return execute(amount) * rate\n\n"
    "def execute(x):\n"
    "    return x + 1\n"
)
STRONG = (
    "import pytest\nfrom pkg.calc import fee\n\n"
    "@pytest.mark.parametrize('a,r,want', [(10, 2, 22), (3, 3, 12)])\n"
    "def test_fee(a, r, want):\n    assert fee(a, r) == want\n\n"
    "def test_negative():\n    with pytest.raises(ValueError):\n        fee(-1, 2)\n"
)
WEAK = "from pkg.calc import fee\n\ndef test_fee_runs():\n    fee(10, 2)\n"


def _repo(tmp: Path, test_src: str) -> Path:
    (tmp / "src" / "pkg").mkdir(parents=True)
    (tmp / "tests").mkdir()
    (tmp / "src" / "pkg" / "__init__.py").write_text("")
    (tmp / "src" / "pkg" / "calc.py").write_text("def fee(amount, rate):\n    return amount\n")
    (tmp / "tests" / "conftest.py").write_text("import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'src'))\n")
    run = lambda *a: subprocess.run(["git", *a], cwd=tmp, check=True, capture_output=True)
    run("init", "-q", "-b", "main")
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base")
    (tmp / "src" / "pkg" / "calc.py").write_text(FN)
    (tmp / "tests" / "test_calc.py").write_text(test_src)
    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "head")
    return tmp


@pytest.mark.parametrize("tests", [STRONG, WEAK])
def test_two_input_lint_passes_parametrized_or_valueless(tmp_path, tests):
    # STRONG: value test parametrized, raises-test asserts no value; WEAK asserts nothing.
    repo = _repo(tmp_path, tests)
    assert mg.lint_two_inputs(repo, "HEAD~1", ["tests"]) == []


@pytest.mark.parametrize("cases,ok", [("[(1, 1)]", False), ("[(1, 1), (2, 2)]", True)])
def test_lint_counts_distinct_cases(tmp_path, cases, ok):
    repo = _repo(tmp_path, STRONG)
    (repo / "tests" / "test_more.py").write_text(
        f"import pytest\n@pytest.mark.parametrize('a,b', {cases})\ndef test_eq(a, b):\n    assert a == b\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "more"], cwd=repo, check=True)
    assert (mg.lint_two_inputs(repo, "HEAD~2", ["tests"]) == []) is ok


@pytest.mark.parametrize("name", ["test_one", "test_two"])
def test_lint_reddens_unparametrized_value_test(tmp_path, name):
    repo = _repo(tmp_path, STRONG + f"\ndef {name}():\n    assert fee(1, 1) == 2\n")
    assert mg.lint_two_inputs(repo, "HEAD~1", ["tests"]) == [f"tests/test_calc.py::{name}"]




def _commit(repo, msg="c"):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg], cwd=repo, check=True)


@pytest.mark.parametrize("op", ["==", "!="])
def test_lint_catches_eq_and_neq(tmp_path, op):
    repo = _repo(tmp_path, STRONG + f"\ndef test_cmp():\n    assert fee(1, 1) {op} 3\n")
    assert mg.lint_two_inputs(repo, "HEAD~1", ["tests"]) == ["tests/test_calc.py::test_cmp"]


@pytest.mark.parametrize("op", ["<", "is", "in"])
def test_lint_ignores_non_value_compares(tmp_path, op):
    rhs = "[2]" if op == "in" else ("None" if op == "is" else "3")
    repo = _repo(tmp_path, STRONG + f"\ndef test_cmp():\n    assert fee(1, 1) {op} {rhs}\n")
    assert mg.lint_two_inputs(repo, "HEAD~1", ["tests"]) == []


@pytest.mark.parametrize("src", ["def test_x(:\n", "def test_y()\n    pass\n"])
def test_unparsable_test_file_raises(src):
    with pytest.raises(SyntaxError):
        mg._test_fns(src)


@pytest.mark.parametrize("extra,code", [("", 0), ("\ndef test_one():\n    assert fee(1, 1) == 2\n", 1)])
def test_lint_main_exit_code(tmp_path, monkeypatch, extra, code):
    repo = _repo(tmp_path, STRONG + extra)
    monkeypatch.chdir(repo)
    assert mg.main(["--base", "HEAD~1", "--tests", "tests"]) == code


@pytest.mark.parametrize("fname", ["helpers.py", "test_notes.txt"])
def test_lint_only_reads_test_modules(tmp_path, fname):
    repo = _repo(tmp_path, STRONG)
    (repo / "tests" / fname).write_text("def test_z():\n    assert 1 == 1\n")
    _commit(repo)
    assert mg.lint_two_inputs(repo, "HEAD~2", ["tests"]) == []


# --- G2 survivor repairs (#73) -------------------------------------------
import ast as _ast

VALUE_TEST = "def test_v():\n    assert f(1) == 2\n"


def _fake_git(monkeypatch, old):
    def git(*args, cwd):
        if args[0] == "diff":
            return "tests/test_m.py\n"
        if old is None:
            raise subprocess.CalledProcessError(128, "git show")
        return old
    monkeypatch.setattr(mg, "git", git)


@pytest.mark.parametrize("old,bad", [
    (VALUE_TEST, []),                       # existed before: not new, not linted
    ("def test_other():\n    pass\n", ["tests/test_m.py::test_v"]),
    (None, ["tests/test_m.py::test_v"]),    # empty base: file is new
    ("", ["tests/test_m.py::test_v"]),
])
def test_only_new_tests_are_linted(tmp_path, monkeypatch, old, bad):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text(VALUE_TEST)
    _fake_git(monkeypatch, old)
    assert mg.lint_two_inputs(tmp_path, "B", ["tests"]) == bad


def _cases(deco: str) -> int:
    return mg._param_cases(_ast.parse(f"{deco}\ndef test_x(a):\n    assert a == 1\n").body[0])


@pytest.mark.parametrize("deco,n", [
    ("@pytest.mark.parametrize('a', CASES)", 2),                 # G5 computed list
    ("@pytest.mark.parametrize('a', make_cases())", 2),
    ("@pytest.mark.parametrize('a', [1, 1, 1])", 1),             # G6 duplicates
    ("@pytest.mark.parametrize('a', [1, 2, 2])", 2),
    ("@pytest.mark.parametrize('a', [1, 2, 3])", 3),
    ("@pytest.mark.skipif('a', [1, 2])", 0),                     # not parametrize
    ("@pytest.mark.parametrize('a')", 0),                        # args < 2
    ("@pytest.mark.parametrize('a', argvalues=[1, 2])", 2),
    ("@pytest.mark.parametrize(argnames='a', argvalues=[1, 2, 3])", 3),
])
def test_param_case_count(deco, n):
    assert _cases(deco) == n


@pytest.mark.parametrize("decos", [
    "@pytest.mark.slow\n@pytest.mark.parametrize('a', [1, 2])",
    "@pytest.mark.parametrize('a', [1, 2])\n@pytest.mark.slow",
    "@pytest.mark.slow\n@pytest.mark.parametrize('a', [1, 2])\n@pytest.mark.other",
])
def test_parametrize_found_in_any_decorator_position(decos):
    assert _cases(decos) == 2


@pytest.mark.parametrize("src,names", [
    ("async def test_a():\n    pass\ndef test_b():\n    pass\n", {"test_a", "test_b"}),
    ("def helper_test():\n    pass\ndef testing():\n    pass\ndef check_x():\n    pass\n", {"testing"}),
])
def test_test_fns_includes_async_and_needs_test_prefix(src, names):
    assert set(mg._test_fns(src)) == names


@pytest.mark.parametrize("body,value", [
    ("assert a < b == c", True),           # not only the first op
    ("assert a < b != c", True),
    ("assert a and b == c", True),         # compare inside a BoolOp
    ("assert not a == b", True),
    ("assert x\n    assert y == 1", True),  # not only the first assert
    ("assert a < b < c", False),
    ("assert a in b", False),
])
def test_every_compare_is_checked(body, value):
    fn = _ast.parse(f"def test_x():\n    {body}\n").body[0]
    assert mg._asserts_value(fn) is value


@pytest.mark.parametrize("extra,lines", [
    ("", ["New single-input value tests: 0"]),
    ("\ndef test_one():\n    assert fee(1, 1) == 2\n", ["New single-input value tests: 1", "- FAIL: `tests/test_calc.py::test_one`"]),
])
def test_summary_is_written(tmp_path, monkeypatch, extra, lines):
    repo = _repo(tmp_path / "r", STRONG + extra)
    monkeypatch.chdir(repo)
    s = tmp_path / "s.md"
    mg.main(["--base", "HEAD~1", "--tests", "tests", "--summary", str(s)])
    text = s.read_text()
    assert text.startswith("## Two-input test lint") and all(x in text for x in lines)


@pytest.mark.parametrize("decos,n", [
    ("@pytest.mark.parametrize('a', [1, 2, 3])\n@pytest.mark.parametrize('b', [9])", 3),
    ("@pytest.mark.parametrize('b', [9])\n@pytest.mark.parametrize('a', [1, 2, 3])", 3),
    ("@pytest.mark.parametrize('a', [1, 2])\n@pytest.mark.parametrize('b', [7, 8, 9, 10])\n@pytest.mark.parametrize('c', [0])", 4),
])
def test_stacked_parametrize_counts_the_largest(decos, n):
    assert _cases(decos) == n
