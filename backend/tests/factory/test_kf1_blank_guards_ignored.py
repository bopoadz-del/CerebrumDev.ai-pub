"""K-F1 (G6 mutant 64-08392be-g): blank guard entries ('' / whitespace) are
not rules and must not refuse the answer; a real guard alongside them is still
enforced. Two grounded corpora give two different computed results."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import pytest

from app.cerebrum_product_kernel.answer import KIND_KERNEL, answer_query
from app.cerebrum_product_kernel.formulas import resolve_definitions


@dataclass
class _Hit:
    text: str
    file: str = "figures.txt"
    location: Dict[str, Any] = field(default_factory=dict)


class _Engine:
    def __init__(self, text):
        self.text = text

    def retrieve(self, collection, query):
        return [_Hit(self.text)]


@pytest.fixture(autouse=True)
def _roots(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "s"))
    monkeypatch.setenv("FACTORY_OUTPUTS_ROOT", str(tmp_path / "f"))


def _resolved():
    overlay = {"set_id": "tenant-x", "definitions": [{
        "id": "widget_yield_ratio", "definition_version": 1,
        "expression": "widgets / batches", "guards": ["", "   ", "batches > 0"],
        "provenance": {"kind": "client_submitted", "reference": "r"}}]}
    return resolve_definitions(overlays=[overlay])


@pytest.mark.parametrize("widgets,batches", [(40, 8), (90, 4)])
def test_blank_guards_do_not_refuse(widgets, batches):
    ans = answer_query("t-a", "widget yield ratio", "p",
                       engine=_Engine(f"widgets = {widgets}\nbatches = {batches}"),
                       resolved=_resolved())
    assert ans.kind == KIND_KERNEL
    assert not ans.unknown, ans.text
    assert ans.kernel_value == pytest.approx(widgets / batches)


def test_real_guard_beside_blanks_still_refuses():
    ans = answer_query("t-a", "widget yield ratio", "p",
                       engine=_Engine("widgets = 40\nbatches = 0"), resolved=_resolved())
    assert ans.unknown and "batches > 0" in ans.text
