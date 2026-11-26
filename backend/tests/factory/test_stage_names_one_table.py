"""The five stage names come from one table that the screen, the README and the
build code all agree with.

The table is ``frontend/src/stepNames.json`` (the build screen reads it through
``stepNames.ts``). The stored role ids stay as they are; the table only says
what each one is called. A role added to ``BuildRole`` without a display name,
a README whose stage list drifts from the table, or a README path that no
longer exists fails here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Mapping, Sequence

import pytest

from app.factory.build.authority import BUILD_PHASES, BuildRole

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
                "Collector (planning the parts)",
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
