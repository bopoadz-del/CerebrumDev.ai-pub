"""A SAFE arithmetic evaluator for kernel definition expressions (F-1c BAR 3).

A kernel-covered answer's NUMBER is the kernel's own: the certified definition's
``expression`` evaluated over GROUNDED inputs, never a number the model's prose
invented. This module supplies two generic, product-neutral pieces:

* :func:`free_names` / :func:`safe_eval` — parse and evaluate the expression with
  ``ast`` (NO ``eval``): numbers, + - * / // % **, unary +/-, parentheses, and
  the comparison operators used by ``guards``. A name with no bound value raises
  :class:`MissingInput`; it is never defaulted.
* :func:`extract_grounded_inputs` — read each named input's value out of the
  retrieved chunk text generically (``name`` followed by a number, with the
  ``is``/``=``/``:`` noise in between tolerated). Which names are read comes from
  the definition's own expression — nothing vertical or customer-specific.
"""

from __future__ import annotations

import ast
import operator
import re
from typing import Dict, List, Tuple

__all__ = [
    "MissingInput",
    "UnsafeExpression",
    "free_names",
    "safe_eval",
    "extract_grounded_inputs",
]


class MissingInput(KeyError):
    """A name in the expression has no grounded value."""


class UnsafeExpression(ValueError):
    """The expression uses a construct this evaluator refuses to run."""


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_CMP_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def free_names(expression: str) -> List[str]:
    """The distinct variable names an expression references (its inputs)."""
    try:
        tree = ast.parse(str(expression or ""), mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpression(f"cannot parse expression: {exc}") from exc
    names: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in names:
            names.append(node.id)
    return names


def _eval(node: ast.AST, variables: Dict[str, float]):
    if isinstance(node, ast.Expression):
        return _eval(node.body, variables)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise UnsafeExpression(f"non-numeric constant: {node.value!r}")
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise MissingInput(node.id)
        return variables[node.id]
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise UnsafeExpression(f"operator not allowed: {type(node.op).__name__}")
        return op(_eval(node.left, variables), _eval(node.right, variables))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise UnsafeExpression(f"unary operator not allowed: {type(node.op).__name__}")
        return op(_eval(node.operand, variables))
    if isinstance(node, ast.Compare):
        left = _eval(node.left, variables)
        result = True
        for op_node, comparator in zip(node.ops, node.comparators):
            op = _CMP_OPS.get(type(op_node))
            if op is None:
                raise UnsafeExpression(f"comparison not allowed: {type(op_node).__name__}")
            right = _eval(comparator, variables)
            result = result and op(left, right)
            left = right
        return result
    raise UnsafeExpression(f"syntax not allowed: {type(node).__name__}")


def safe_eval(expression: str, variables: Dict[str, float]):
    """Evaluate ``expression`` over ``variables`` with ast only (no ``eval``).

    Raises :class:`MissingInput` for an unbound name, :class:`UnsafeExpression`
    for a disallowed construct, and lets ``ZeroDivisionError`` propagate (a
    guard-violating divisor is a refusal the caller surfaces).
    """
    try:
        tree = ast.parse(str(expression or ""), mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpression(f"cannot parse expression: {exc}") from exc
    return _eval(tree, variables)


def _number_variants(name: str) -> List[str]:
    """Surface forms of an input name to look for (``unit_cost`` / ``unit cost``)."""
    base = str(name or "").strip()
    variants = {base, base.replace("_", " "), base.replace("_", "")}
    return [v for v in variants if v]


def extract_grounded_inputs(
    names: List[str], text: str
) -> Tuple[Dict[str, float], List[str]]:
    """Read each named input's numeric value out of ``text`` (grounded chunks).

    Generic: for each name, the first ``<name> ... <number>`` within a short
    window wins (tolerating ``is`` / ``=`` / ``:`` / ``of`` and currency/percent
    symbols). Returns ``(values, missing)`` — ``missing`` names have no grounded
    figure and must trigger a refusal, never a default.
    """
    blob = str(text or "")
    values: Dict[str, float] = {}
    missing: List[str] = []
    for name in names:
        found = None
        for variant in _number_variants(name):
            pattern = (
                r"(?i)\b" + re.escape(variant)
                + r"\b[^0-9\-+]{0,24}?([-+]?\d[\d,]*(?:\.\d+)?)"
            )
            match = re.search(pattern, blob)
            if match:
                try:
                    found = float(match.group(1).replace(",", ""))
                except ValueError:
                    found = None
                if found is not None:
                    break
        if found is None:
            missing.append(name)
        else:
            values[name] = found
    return values, missing
