"""F-8e -- hardwiring scan over the generated tree + prompt-leak. Invented
literals; no real product/client."""
from __future__ import annotations

import pytest

import app.factory.build.product_grader.scan_generated as scan_generated

FORBIDDEN = ["acme_corp", "Project Zephyr", "42.5", "widget-xl"]


def _product(tmp_path, files):
    root = tmp_path / "product"
    (root / "app").mkdir(parents=True)
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    return root


@pytest.mark.parametrize("body, flagged", [
    ("def handle(p):\n    if p['client'] == 'acme_corp':\n        return 1\n    return 2\n", True),   # branch on a client literal
    ("def handle(p):\n    return {'result': p['base'] * p['rate']}\n", False),                         # clean, reads inputs
])
def test_forbidden_hits_flags_branch_on_a_client_literal(body, flagged, tmp_path):
    root = _product(tmp_path, {"app/actions/x.py": body})
    hits = scan_generated.forbidden_hits(root, FORBIDDEN)
    assert bool(hits) is flagged
    if flagged:
        assert "acme_corp" in hits["app/actions/x.py"]


@pytest.mark.parametrize("lit", ["widget-xl", "Project Zephyr"])
def test_forbidden_hits_flags_a_baked_literal(lit, tmp_path):
    root = _product(tmp_path, {"app/actions/y.py": f"CONST = {lit!r}\ndef handle(p):\n    return CONST\n"})
    hits = scan_generated.forbidden_hits(root, FORBIDDEN)
    assert hits and lit in hits["app/actions/y.py"]


@pytest.mark.parametrize("lit", ["acme_corp", "Project Zephyr"])
def test_forbidden_hits_reads_whole_tokens_inside_a_value(lit, tmp_path):
    root = _product(tmp_path, {
        "app/a.py": f"MSG = 'Report for {lit} today'\n",
        "app/b.py": f"KEY = 'x{lit}y'\n",  # a fragment of a longer token
        "app/c.py": f'def f():\n    """About {lit}."""\n    return 1\n',  # a docstring is not a value
        "app/d.py": f"﻿NAME = {lit!r}\n",  # BOM-prefixed source is still read
    })
    hits = scan_generated.forbidden_hits(root, FORBIDDEN)
    assert sorted(hits) == ["app/a.py", "app/d.py"]


@pytest.mark.parametrize("head", ["class C:", "async def f():", '"""Module about acme_corp."""\nclass C:'])
def test_forbidden_hits_skips_docstrings_but_not_a_second_string(head, tmp_path):
    docs_only = f'{head}\n    """About acme_corp."""\n    pass\n'
    assert scan_generated.forbidden_hits(_product(tmp_path / "a", {"app/k.py": docs_only}), FORBIDDEN) == {}
    # The same string as a statement after the docstring is a value.
    valued = f'{head}\n    """About it."""\n    "acme_corp"\n'
    hits = scan_generated.forbidden_hits(_product(tmp_path / "b", {"app/k.py": valued}), FORBIDDEN)
    assert hits == {"app/k.py": ["acme_corp"]}


def test_forbidden_hits_skips_unparseable_source(tmp_path):
    root = _product(tmp_path, {"app/bad.py": "NAME = 'acme_corp'\ndef (:\n"})
    assert scan_generated.forbidden_hits(root, FORBIDDEN) == {}


SUPPLIED = "Remind the tenant thirty days before renewal. Use the standard ledger view."
FACTORY = "Use the standard ledger view"


@pytest.mark.parametrize("value, leaked", [
    ("Remind the tenant thirty days before renewal", True),   # a copied clause of the brief
    ("Use the standard ledger view", False),                  # the Factory's own text, shared
    ("tenant", False),                                        # one word is vocabulary
])
def test_prompt_leak_fires_on_a_copied_brief_clause_only(value, leaked, tmp_path):
    root = _product(tmp_path, {"app/x.py": f"MSG = {value!r}\n"})
    prompt = f"{FACTORY}.\n{SUPPLIED}"
    found = scan_generated.prompt_leak(root, SUPPLIED, FACTORY, prompt)
    assert found == ({"app/x.py": [value]} if leaked else {})


def test_prompt_leak_needs_the_clause_in_the_dispatched_prompt(tmp_path):
    root = _product(tmp_path, {"app/x.py": "MSG = 'Remind the tenant thirty days before renewal'\n"})
    assert scan_generated.prompt_leak(root, SUPPLIED, "", "a prompt that never carried the brief") == {}
