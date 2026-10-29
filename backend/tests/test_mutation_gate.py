"""CI-M: the mutation gate reddens on a surviving mutant and an untested
function, and the lint reddens on a new single-input value test."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

GATE = next(p / "scripts" / "mutation_gate.py" for p in Path(__file__).resolve().parents if (p / "scripts" / "mutation_gate.py").is_file())
spec = importlib.util.spec_from_file_location("mutation_gate", GATE)
mg = importlib.util.module_from_spec(spec)
sys.modules["mutation_gate"] = mg
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


@pytest.mark.parametrize("tests,red", [(STRONG, set()), (WEAK, set(mg.MUTANTS) - {"bypass_dispatcher"})])
def test_mutants_killed_or_survive(tmp_path, tests, red):
    repo = _repo(tmp_path, tests)
    res = mg.mutation_gate(repo, "HEAD~1", ["src"], ["tests"], ".", [])
    survived = {r.mutant for r in res if r.function == "fee" and r.outcome != "killed"}
    assert {r.mutant for r in res if r.function == "fee"} == set(mg.MUTANTS)
    assert survived >= red and (not red or survived)
    if not red:
        assert survived == set()


@pytest.mark.parametrize("tests", [STRONG, WEAK])
def test_untested_function_is_red(tmp_path, tests):
    repo = _repo(tmp_path, tests)
    (repo / "src" / "pkg" / "other.py").write_text("def lonely(x):\n    if x:\n        return 1\n    return 2\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "o"], cwd=repo, check=True)
    res = mg.mutation_gate(repo, "HEAD~2", ["src"], ["tests"], ".", [])
    assert any(r.function == "lonely" and r.outcome == "uncovered" for r in res)


@pytest.mark.parametrize("root,want", [("src", "pkg.calc"), ("src/pkg", "pkg.calc")])
def test_module_token_follows_package_root(tmp_path, root, want):
    repo = _repo(tmp_path, STRONG)
    assert mg.module_tokens("src/pkg/calc.py", [root], repo) == [want]


def _commit(repo, msg="c"):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg], cwd=repo, check=True)


@pytest.mark.parametrize("tests", [STRONG])
def test_strong_suite_kills_every_mutant_exactly(tmp_path, tests):
    res = mg.mutation_gate(_repo(tmp_path, tests), "HEAD~1", ["src"], ["tests"], ".", [])
    fee = {r.mutant: r.outcome for r in res if r.function == "fee"}
    assert fee == {m: "killed" for m in mg.MUTANTS}


@pytest.mark.parametrize("outcome,red", [("killed", False), ("survived", True), ("uncovered", True), ("timeout", True), ("", True)])
def test_every_non_kill_is_red(outcome, red):
    assert mg.is_red(outcome) is red


@pytest.mark.parametrize("outcomes,code", [
    ([], 0), (["killed"], 0), (["killed", "survived"], 1), (["uncovered"], 1), (["timeout"], 1), (["killed", "killed"], 0),
])
def test_exit_code_reddens_on_any_red(outcomes, code):
    rs = [mg.MutantResult("p.py", "f", "constant_return", o) for o in outcomes]
    assert mg.exit_code(rs) == code


@pytest.mark.parametrize("tests,code", [(STRONG, 0), (WEAK, 1)])
def test_main_exit_code(tmp_path, monkeypatch, tests, code):
    repo = _repo(tmp_path, tests)
    monkeypatch.chdir(repo)
    summary = tmp_path / "s.md"
    assert mg.main(["--base", "HEAD~1", "--src", "src", "--tests", "tests", "--summary", str(summary)]) == code
    assert ("SURVIVED" in summary.read_text()) is bool(code)


@pytest.mark.parametrize("timeout", [1, 600])
def test_timeout_is_its_own_red_outcome_never_a_kill_or_survivor(tmp_path, monkeypatch, timeout):
    def hang(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=timeout)

    monkeypatch.setattr(mg.subprocess, "run", hang)
    out = mg.run_tests(tmp_path, ["tests/test_x.py"], ".", [])
    assert out == "timeout" and mg.is_red(out) and out not in ("killed", "survived")


@pytest.mark.parametrize("rc,want", [(0, "survived"), (1, "killed"), (2, "killed")])
def test_run_tests_maps_returncode(tmp_path, monkeypatch, rc, want):
    monkeypatch.setattr(mg.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, rc, "", ""))
    assert mg.run_tests(tmp_path, ["t"], ".", []) == want


@pytest.mark.parametrize("name,is_test", [
    ("test_calc.py", True), ("calc_test.py", True), ("conftest.py", True), ("calc.py", False), ("testing.py", False),
])
def test_is_test_file(name, is_test):
    assert mg.is_test_file(f"src/pkg/{name}") is is_test


@pytest.mark.parametrize("name", ["test_helpers.py", "conftest.py"])
def test_changed_test_module_under_src_is_never_mutated(tmp_path, name):
    repo = _repo(tmp_path, STRONG)
    (repo / "src" / "pkg" / name).write_text("def helper(x):\n    if x:\n        return 1\n    return 2\n")
    _commit(repo)
    res = mg.mutation_gate(repo, "HEAD~1", ["src"], ["tests"], ".", [])
    assert not any(r.path.endswith(name) for r in res)


# --- G2 survivor repairs (#72) -------------------------------------------
import ast as _ast


def _fn(src: str):
    return _ast.parse(src).body[0]


@pytest.mark.parametrize("src,kept", [
    ("def f(x):\n    if x:\n        return 1\n    else:\n        return 2\n", "return 2"),
    ("def f(x):\n    if x:\n        return 1\n    else:\n        y = x * 3\n        return y\n", "y = x * 3"),
])
def test_drop_rule_keeps_the_else_branch(src, kept):
    fn = _fn(src)
    assert mg.mutate(fn, "drop_rule")
    out = _ast.unparse(fn)
    assert kept in out and "return 1" not in out


@pytest.mark.parametrize("src,applies", [
    ("def f(x):\n    return handle_it(x)\n", True),
    ("def f(x):\n    return compute(x)\n", False),
])
def test_bypass_dispatcher_reports_whether_it_applied(src, applies):
    fn = _fn(src)
    assert mg.mutate(fn, "bypass_dispatcher") is applies
    assert ("handle_it(" in _ast.unparse(fn)) is (not applies)


@pytest.mark.parametrize("src,applies", [
    ("def f(x):\n    return x + 1\n", True),
    ("def f(x):\n    yield x\n", False),
    ("def f(x):\n    '''doc'''\n    pass\n", False),
    ("def f(x):\n    '''doc'''\n", False),
])
def test_constant_return_skips_generators_and_trivial(src, applies):
    fn = _fn(src)
    assert mg.mutate(fn, "constant_return") is applies
    assert ("return None" in _ast.unparse(fn)) is applies


NESTED = "def outer():\n    a = 1\n    def inner():\n        return 2\n    return inner\n"


@pytest.mark.parametrize("lines,want", [({4}, ["inner"]), ({2}, ["outer"]), ({2, 4}, ["outer", "inner"])])
def test_nested_lines_count_only_for_the_innermost(lines, want):
    got = [f.name for f in mg.functions_touching(_ast.parse(NESTED), lines)]
    assert sorted(got) == sorted(want)


@pytest.mark.parametrize("hunk,want", [("@@ -4,2 +5,0 @@", {5}), ("@@ -1 +7,3 @@", {7, 8, 9}), ("@@ -1 +2 @@", {2})])
def test_changed_lines_hunks(monkeypatch, hunk, want):
    monkeypatch.setattr(mg, "git", lambda *a, cwd: f"+++ b/src/a.py\n{hunk}\n")
    assert mg.changed_lines("B", Path("."), ["src"]) == {"src/a.py": want}


@pytest.mark.parametrize("name,kept", [("src/a.py", True), ("src/a.txt", False), ("src/a.pyi", False)])
def test_changed_lines_only_python(monkeypatch, name, kept):
    monkeypatch.setattr(mg, "git", lambda *a, cwd: f"+++ b/{name}\n@@ -1 +1 @@\n")
    assert (name in mg.changed_lines("B", Path("."), ["src"])) is kept


@pytest.mark.parametrize("text,hit", [
    ("# pkg.calc mentioned\ndef test_x():\n    fee\n", True),
    ("# pkg.calc mentioned\ndef test_x():\n    other\n", False),
    ("from pkg.calc import something\n", True),
    ("import pkg.calc\n", True),
])
def test_tests_for_needs_name_or_import(tmp_path, text, hit):
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(text)
    got = mg.tests_for("src/pkg/calc.py", "fee", tmp_path, ["tests"], ["src"])
    assert got == (["tests/test_a.py"] if hit else [])


@pytest.mark.parametrize("path,want", [("src/pkg/__init__.py", ["pkg"]), ("src/pkg/sub/__init__.py", ["pkg.sub"]), ("src/pkg/m.py", ["pkg.m"])])
def test_init_is_stripped(tmp_path, path, want):
    assert mg.module_tokens(path, ["src"], tmp_path) == want


@pytest.mark.parametrize("outcomes", [["survived"], ["uncovered", "timeout"], ["killed", "survived"]])
def test_summary_lists_every_red(tmp_path, monkeypatch, outcomes):
    rs = [mg.MutantResult(f"src/m{i}.py", f"f{i}", "drop_rule", o) for i, o in enumerate(outcomes)]
    monkeypatch.setattr(mg, "git", lambda *a, cwd: str(tmp_path) + "\n")
    monkeypatch.setattr(mg, "mutation_gate", lambda *a: rs)
    s = tmp_path / "s.md"
    assert mg.main(["--base", "B", "--src", "src", "--tests", "t", "--summary", str(s)]) == 1
    text = s.read_text()
    for r in rs:
        line = f"`{r.path}::{r.function}`"
        assert (line in text) is mg.is_red(r.outcome)
        if mg.is_red(r.outcome):
            assert f"{r.outcome.upper()}: {line}" in text
    assert f"red: {sum(mg.is_red(o) for o in outcomes)}" in text
