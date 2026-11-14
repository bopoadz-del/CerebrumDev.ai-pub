"""Invented product fixtures for the function grader's declared contract.

The grader builds a capability's two inputs from the spec the product's own
``app/models.py`` declares (``declared_specs.specs_from_product_models``), so
every grader fixture workspace declares one: typed fields with a bound. The
names are invented and decide nothing; the declared types and bounds do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

MODEL_CLASS = (
    "class Entry:\n"
    "    ENTITY = 'entry'\n"
    "    FIELDS = ['owner', 'amount', 'approved']\n"
    "    CONSTRAINTS = {'amount': {'min': 0, 'max': 1000}}\n"
    "    owner: str\n"
    "    amount: int\n"
    "    approved: bool\n"
)

#: A handle that computes from its declared fields (no leaf copies an input).
COMPUTES = (
    "def handle(payload):\n"
    "    total = payload['amount'] * 3 + (5 if payload['approved'] else 0)\n"
    "    return {'ok': True, 'total': total, 'tag': payload['owner'][::-1]}\n"
)


def models_source(ids: Iterable[str], declared: Iterable[str] = None) -> str:
    """``app/models.py`` declaring ``Entry`` for every id in ``declared``
    (default: all ``ids``); the rest have no declared contract."""
    keep = list(ids if declared is None else declared)
    return MODEL_CLASS + "\nMODELS = {" + ", ".join(f"{i!r}: Entry" for i in keep) + "}\n"


def write_models(root: Path, ids: Iterable[str], declared: Iterable[str] = None) -> None:
    app = Path(root) / "app"
    app.mkdir(parents=True, exist_ok=True)
    (app / "models.py").write_text(models_source(ids, declared))
