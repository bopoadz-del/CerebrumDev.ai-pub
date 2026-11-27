"""The five stage names come from one table that the screen, the README and the
build code all agree with.

The table is ``frontend/src/stepNames.json`` (the build screen reads it through
``stepNames.ts``). The stored role ids stay as they are; the table only says
what each one is called. A role added to ``BuildRole`` without a display name,
a README whose stage list drifts from the table, a README path that no
longer exists, a README that names a function in a file that does not
define it, or one that lists a function under a stage whose handler and gate
never reach it fails here.
"""

from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Set

import pytest

from app.factory.blocks_lock import generate_lock
from app.factory.build.authority import BUILD_PHASES, BuildRole
from app.factory.build.gates import GATES
from app.factory.build.roles import RoleContext
from app.factory.build.roles_handlers import ROLE_IMPLEMENTATIONS, run_cloner
from app.factory.build.workspace import RoleWorkspace

REPO = Path(__file__).resolve().parents[3]
TABLE = REPO / "frontend" / "src" / "stepNames.json"
README = REPO / "README.md"


def _table() -> Dict[str, Dict[str, str]]:
    return json.loads(TABLE.read_text(encoding="utf-8"))


def roles_without_a_name(roles: Sequence[str], table: Mapping[str, Mapping[str, str]]) -> List[str]:
    """Every role id the table gives no non-empty label and running form."""
    missing = []
    for role in roles:
        entry = table.get(role) or {}
        if not str(entry.get("label") or "").strip() or not str(entry.get("running") or "").strip():
            missing.append(role)
    return missing


_NAME_IN_FILE = re.compile(r"`([A-Za-z_]\w*)`\s+in\s+`([^`]+\.py)`")


def names_not_defined_where_said(text: str, root: Path) -> List[str]:
    """Each ``"`name` in `path.py`"`` whose file does not define ``name``.

    Also every backticked ``.py`` path the text names without saying which
    function or class in it does the work, so a path cannot point at the
    wrong file unchecked.
    """
    problems: List[str] = []
    paired = set()
    for name, rel in _NAME_IN_FILE.findall(text):
        paired.add(rel)
        path = root / rel
        if not path.is_file():
            problems.append(f"{rel}: no such file")
            continue
        defined = {
            node.name
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        if name not in defined:
            problems.append(f"{name} is not defined in {rel}")
    for rel in re.findall(r"`([^`]+\.py)`", text):
        if rel not in paired:
            problems.append(f"{rel}: named without the function that does the work")
    return problems


def _first_section(text: str) -> str:
    """The README's first ``## `` section, up to the next one."""
    parts = re.split(r"^## ", text, flags=re.M)
    assert len(parts) > 1, "README has no ## section"
    return parts[1]


def _stage_lines(section: str) -> List[str]:
    """The numbered items of a section, each joined onto one line."""
    items: List[str] = []
    for line in section.splitlines():
        if re.match(r"^\d+\. ", line):
            items.append(line)
        elif items and line.startswith("   "):
            items[-1] += " " + line.strip()
    return items


@pytest.mark.parametrize("role", list(BuildRole), ids=lambda r: r.value)
def test_every_build_role_has_a_display_name(role):
    entry = _table()[role.value]
    assert entry["label"].strip()
    # The running form names the stage first, so the headline and the stop
    # sentence carry the same name as the stepper.
    assert entry["running"].startswith(entry["label"] + " (")
    # A name is a label, never the stored id itself.
    assert role.value not in entry["label"]


@pytest.mark.parametrize(
    "roles, table, missing",
    [
        (
            ["COLLECTOR", "NEW_ROLE"],
            {"COLLECTOR": {"label": "Collector", "running": "Collector (x)"}},
            ["NEW_ROLE"],
        ),
        (
            ["A", "B"],
            {"A": {"label": "", "running": "A (x)"}, "B": {"label": "B", "running": " "}},
            ["A", "B"],
        ),
        (
            ["A", "B"],
            {"A": {"label": "A", "running": "A (x)"}, "B": {"label": "B", "running": "B (y)"}},
            [],
        ),
        ([], {}, []),
    ],
)
def test_a_role_without_a_name_is_reported(roles, table, missing):
    assert roles_without_a_name(roles, table) == missing


@pytest.mark.parametrize("roles", [[r.value for r in BuildRole], [r.value for r in BUILD_PHASES]])
def test_the_table_covers_the_roles_in_build_order(roles):
    table = _table()
    assert roles_without_a_name(roles, table) == []
    # Same ids, same order: the screen's default step order is the table's.
    assert list(table) == roles


@pytest.mark.parametrize(
    "field, expected",
    [
        ("label", ["Collector", "Researcher", "Workers", "Tester", "Store Manager"]),
        (
            "running",
            [
                "Collector (finding the parts)",
                "Researcher (gathering building blocks)",
                "Workers (writing the code)",
                "Tester (testing)",
                "Store Manager (packaging)",
            ],
        ),
    ],
)
def test_the_display_names_are_the_five_stage_names(field, expected):
    names = [entry[field] for entry in _table().values()]
    assert names == expected
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("index", range(len(BUILD_PHASES)))
def test_the_readme_lists_each_stage_in_order_under_its_table_name(index):
    section = _first_section(README.read_text(encoding="utf-8"))
    assert section.startswith("The five build stages")
    lines = _stage_lines(section)
    assert len(lines) == len(BUILD_PHASES)
    role = BUILD_PHASES[index].value
    line = lines[index]
    assert line.startswith(f"{index + 1}. **{_table()[role]['label']}** (`{role}`")
    # Every file the item points at exists, and it points at one at least.
    paths = [p for p in re.findall(r"`([^`]+)`", line) if "/" in p]
    assert paths, line
    for rel in paths:
        assert (REPO / rel).is_file(), rel
    # The stage's own handler, as the build code maps it, is named in its item.
    handler = ROLE_IMPLEMENTATIONS[BUILD_PHASES[index]].__name__
    assert re.search(rf"`{handler}`\s+in\s+`", line), (handler, line)


@pytest.mark.parametrize("part", [0, 1], ids=["before the stages", "the stages"])
def test_every_function_the_readme_names_is_defined_in_the_file_it_names(part):
    section = _first_section(README.read_text(encoding="utf-8"))
    # The text before the numbered list, and the list itself.
    text = re.split(r"^1\. ", section, maxsplit=1, flags=re.M)[part]
    assert re.findall(r"`[^`]+\.py`", text), "this part names no file"
    assert names_not_defined_where_said(text, REPO) == []


_HANDLERS = "backend/app/factory/build/roles_handlers.py"
_GATES = "backend/app/factory/build/gates.py"


@pytest.mark.parametrize(
    "text, problems",
    [
        (f"`run_cloner` in `{_HANDLERS}`; `gate_tester_contract` in `{_GATES}`.", []),
        (
            f"`run_clonerXX` in `{_HANDLERS}`; `gate_tester_contract` in `{_GATES}`.",
            [f"run_clonerXX is not defined in {_HANDLERS}"],
        ),
        (
            f"`gate_tester_contract` in `{_HANDLERS}`.",
            [f"gate_tester_contract is not defined in {_HANDLERS}"],
        ),
        (
            f"the gate (`{_GATES}`) and `run_tester` in `{_HANDLERS}`.",
            [f"{_GATES}: named without the function that does the work"],
        ),
        ("`run_tester` in `backend/no_such_file.py`.", ["backend/no_such_file.py: no such file"]),
        ("", []),
    ],
)
def test_a_name_in_the_wrong_file_is_reported(text, problems):
    assert names_not_defined_where_said(text, REPO) == problems


_NOT_A_MODULE = {"self", "cls", "ctx"}


@lru_cache(maxsize=1)
def _factory_calls() -> Mapping[str, frozenset]:
    """Each function in ``backend/app/factory`` and the names its body uses.

    A plain name, or ``module.name``. A method on ``self``/``ctx`` is left out:
    by attribute name alone it would join every role to every other.
    """
    return call_graph(sorted((REPO / "backend" / "app" / "factory").rglob("*.py")))


def call_graph(paths: Iterable[Path]) -> Dict[str, frozenset]:
    uses: Dict[str, Set[str]] = {}
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = uses.setdefault(node.name, set())
            for sub in ast.walk(node):
                if isinstance(sub, ast.Name):
                    names.add(sub.id)
                elif (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id not in _NOT_A_MODULE
                ):
                    names.add(sub.attr)
    return {name: frozenset(names) for name, names in uses.items()}


def names_not_reached(text: str, roots: Iterable[str], calls: Mapping[str, Iterable[str]]) -> List[str]:
    """Each ``"`name` in `path.py`"`` of ``text`` that no root reaches.

    A root reaches itself and every function its body names, and theirs.
    """
    seen = {r for r in roots}
    todo = list(seen)
    while todo:
        for name in calls.get(todo.pop(), ()):
            if name in calls and name not in seen:
                seen.add(name)
                todo.append(name)
    return [name for name, _rel in _NAME_IN_FILE.findall(text) if name not in seen]


@pytest.mark.parametrize(
    "text, roots, missing",
    [
        ("`b` in `x.py`; `c` in `y.py`.", ["a"], []),
        ("`b` in `x.py`; `d` in `y.py`.", ["a"], ["d"]),
        ("`a` in `x.py`.", ["a"], []),
        ("`b` in `x.py`.", ["d"], ["b"]),
        ("`b` in `x.py`.", [], ["b"]),
        ("no pairs here", ["a"], []),
    ],
)
def test_a_function_its_stage_never_reaches_is_reported(text, roots, missing):
    calls = {"a": ["b", "len"], "b": ["c"], "c": [], "d": []}
    assert names_not_reached(text, roots, calls) == missing


@pytest.mark.parametrize("index", range(len(BUILD_PHASES)), ids=lambda i: BUILD_PHASES[i].value)
def test_every_function_listed_under_a_stage_is_reached_by_that_stage(index):
    """A function listed under a stage is run by that stage's handler or gate.

    So a real function from another stage (the WRITER's brief compiler under
    the Researcher, say) cannot stand in the wrong item.
    """
    lines = _stage_lines(_first_section(README.read_text(encoding="utf-8")))
    phase = BUILD_PHASES[index]
    roots = [ROLE_IMPLEMENTATIONS[phase].__name__, GATES[phase].__name__]
    assert _NAME_IN_FILE.findall(lines[index])
    assert names_not_reached(lines[index], roots, _factory_calls()) == []


def _store(root: Path, block_ids: Sequence[str]) -> Path:
    store = root / "store"
    for bid in block_ids:
        d = store / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.py").write_text("VALUE = 1\n", encoding="utf-8")
        (d / "block.json").write_text(json.dumps({"id": bid, "version": "1.0.0"}) + "\n", encoding="utf-8")
    store.mkdir(exist_ok=True)
    return store


@pytest.mark.parametrize("block_ids", [(), ("analytics",), ("analytics", "workflow")], ids=["none", "one", "two"])
def test_the_researcher_writes_the_brief_only_when_a_block_is_reused(tmp_path, block_ids):
    """What README stage 2 says: no reused block, no brief; one or more, a brief."""
    store = _store(tmp_path, block_ids)
    ws = RoleWorkspace(BuildRole.CLONER, tmp_path / "build")
    ctx = RoleContext(
        role=BuildRole.CLONER,
        workspace=ws,
        blueprint=None,
        plan=None,
        blocks_root=store,
        blocks_lock=generate_lock(store, consumed_ids=list(block_ids)),
        state={"resolved_blocks": tuple(block_ids)},
    )
    result = run_cloner(ctx)
    assert result.ok, result.detail
    assert (ws.destination / "docs" / "coder_brief.md").is_file() is bool(block_ids)
    stage_two = _stage_lines(_first_section(README.read_text(encoding="utf-8")))[1]
    if not block_ids:
        # The README quotes the stage's own word for the no-reuse case.
        assert f'"{result.detail}"' in stage_two


@pytest.mark.parametrize(
    "body, used, not_used",
    [
        ("def a():\n    helpers.b()\n", {"helpers", "b"}, set()),
        ("def a():\n    self.b()\n    ctx.c()\n    cls.d()\n", {"self", "ctx", "cls"}, {"b", "c", "d"}),
        ("def a():\n    return b\n", {"b"}, set()),
    ],
)
def test_the_call_graph_follows_module_names_and_skips_methods(tmp_path, body, used, not_used):
    path = tmp_path / "m.py"
    path.write_text(body, encoding="utf-8")
    graph = call_graph([path])
    assert set(graph) == {"a"}
    assert used <= graph["a"]
    assert not (not_used & graph["a"])
