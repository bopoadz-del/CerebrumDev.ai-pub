"""F-8e -- hardwiring scan over the GENERATED product tree + prompt-leak diff.

Two checks that run on the product a build produced:

* ``forbidden_hits`` -- any brief/fixture/test-input/client-or-project literal
  that appears in the product source, either as a bare string constant or as a
  branch comparison operand (``if x == "<client>"``). A correct product reads
  its inputs; it never branches on a specific client name or bakes a fixture
  value, so any hit is hardwired content.
* ``prompt_leak`` -- a clause of the user-supplied brief that the writer was
  given in its dispatched prompt and that appears in a product string value:
  content copied out of the prompt into the code. Factory text is never a
  source (FG-4).

The forbidden set (brief text tokens, fixture values, test inputs, client/
project names) is passed in by the grader; nothing is hand-listed here.
"""

from __future__ import annotations

import ast
import re
import tokenize
from pathlib import Path
from typing import Dict, FrozenSet, Iterable, List, Mapping


def _forbidden(values: Iterable) -> FrozenSet[str]:
    return frozenset(str(v) for v in values if str(v).strip())


def _value_constants(tree: ast.AST) -> set:
    """The product's string VALUES: every string constant except docstrings
    (documentation is not a value the product computes with)."""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docs.add(id(body[0].value))
    return {
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs
    }


def _parsed(py: Path):
    """The module as the interpreter reads it (PEP 263 / BOM-aware decode);
    None when it does not parse -- unparseable source is graded elsewhere."""
    try:
        with tokenize.open(py) as fh:
            return ast.parse(fh.read())
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return None


def _token_in(literal: str, text: str) -> bool:
    """``literal`` appears in ``text`` as whole tokens (no identifier
    character on either side), never as a fragment of a longer word."""
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(literal) + r"(?![A-Za-z0-9_])"
    return re.search(pattern, text) is not None


def _constants_by_file(product_root) -> Dict[str, set]:
    root = Path(product_root)
    out: Dict[str, set] = {}
    for py in sorted(root.rglob("*.py")):
        tree = _parsed(py)
        if tree is not None:
            out[py.relative_to(root).as_posix()] = _value_constants(tree)
    return out


def _found_in(constants_by_file: Mapping[str, set], literals: Iterable[str]) -> Dict[str, List[str]]:
    hits: Dict[str, List[str]] = {}
    lits = sorted(set(literals))
    for rel, constants in constants_by_file.items():
        found = [lit for lit in lits if any(_token_in(lit, c) for c in constants)]
        if found:
            hits[rel] = found
    return hits


def forbidden_hits(product_root, forbidden: Iterable) -> Dict[str, List[str]]:
    """{relpath: sorted forbidden literals found in that product .py}. A file
    with none is omitted; an empty result means the product hardwired none of
    the forbidden domain literals.

    A forbidden literal is caught wherever it appears as whole tokens inside a
    string value -- a bare assignment, a message, or a branch comparison
    operand (``if x == "<client>"``) -- so one constant scan covers all."""
    return _found_in(_constants_by_file(product_root), _forbidden(forbidden))


def _clauses(text: str) -> List[str]:
    """The text's clauses: runs between sentence or list punctuation, kept
    only when they hold more than one word (a single word is vocabulary)."""
    parts = re.split(r'[.,;:!?()\[\]{}"\n]+', text or "")
    return [" ".join(p.split()) for p in parts if len(p.split()) > 1]


def prompt_leak(product_root, supplied_text: str, factory_text: str, prompt_text: str) -> Dict[str, List[str]]:
    """{relpath: clauses of the user-supplied brief copied into that file's
    string values}. A clause counts only when the writer was actually given
    it (it is in the dispatched prompt) and it is not the Factory's own text
    (the prompt with the brief removed) -- so Factory boilerplate the brief
    happens to share is never a leak."""
    spans = [
        clause
        for clause in _clauses(supplied_text)
        if clause in " ".join(prompt_text.split()) and not _token_in(clause, " ".join(factory_text.split()))
    ]
    return _found_in(_constants_by_file(product_root), spans)
