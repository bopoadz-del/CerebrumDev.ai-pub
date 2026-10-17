"""The one definition of a brief's content lines.

The compiler records the lines it wrote; the lint compares a brief's lines to
that record. Both must cut and normalise lines identically, so the rule lives
here and nowhere else.
"""

from __future__ import annotations

from typing import FrozenSet, List


def content_lines(text: str) -> List[str]:
    """Non-blank, non-rule lines, stripped.

    Headings count as content: the compiler records every line it writes
    (headings included) with this same function, and the lint checks a
    brief's lines against that record -- so no line needs classifying by its
    words.
    """
    lines: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or set(line) <= {"=", "-", " "}:
            continue
        lines.append(line)
    return lines


def line_key(line: str) -> str:
    return " ".join(str(line).split()).lower()


def emitted_line_set(text: str) -> FrozenSet[str]:
    """What a compiler that wrote ``text`` records as its own lines."""
    return frozenset(line_key(line) for line in content_lines(text))
