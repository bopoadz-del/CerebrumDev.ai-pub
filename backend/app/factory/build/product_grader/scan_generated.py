"""F-8e -- hardwiring scan over the GENERATED product tree + prompt-leak diff.

Two checks that run on the product a build produced:

* ``forbidden_hits`` -- any brief/fixture/test-input/client-or-project literal
  that appears in the product source, either as a bare string constant or as a
  branch comparison operand (``if x == "<client>"``). A correct product reads
  its inputs; it never branches on a specific client name or bakes a fixture
  value, so any hit is hardwired content.
* ``prompt_leak`` -- a domain literal present in BOTH the rendered writer prompt
  and the product source: content copied out of the prompt into the code.

The forbidden set (brief text tokens, fixture values, test inputs, client/
project names) is passed in by the grader; nothing is hand-listed here.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List


def _forbidden(values: Iterable) -> FrozenSet[str]:
    return frozenset(str(v) for v in values if str(v).strip())


def _string_constants(tree: ast.AST) -> set:
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def forbidden_hits(product_root, forbidden: Iterable) -> Dict[str, List[str]]:
    """{relpath: sorted forbidden literals found in that product .py}. A file
    with none is omitted; an empty result means the product hardwired none of
    the forbidden domain literals.

    A forbidden literal is caught wherever it appears as a string constant --
    a bare assignment, or a branch comparison operand (``if x == "<client>"``),
    which is itself a string constant -- so one constant scan covers both."""
    forbid = _forbidden(forbidden)
    hits: Dict[str, List[str]] = {}
    root = Path(product_root)
    for py in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text("utf-8", "replace"))
        except SyntaxError:
            # Unparseable product source is graded elsewhere; it cannot hide a
            # literal from a later parse, so it is not a hit here.
            continue
        found = _string_constants(tree) & forbid
        if found:
            hits[str(py.relative_to(root).as_posix())] = sorted(found)
    return hits


def prompt_leak(prompt_text: str, product_source: str, forbidden: Iterable) -> List[str]:
    """Domain literals present in BOTH the rendered writer prompt and the product
    source -- content leaked from the prompt into the code. Sorted, deduped."""
    forbid = _forbidden(forbidden)
    return sorted(lit for lit in forbid if lit in prompt_text and lit in product_source)
