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


@pytest.mark.parametrize("shared", ["acme_corp", "Project Zephyr"])
def test_prompt_leak_flags_a_literal_in_both_prompt_and_source(shared):
    prompt = f"Build a platform for {shared} that tracks widgets."
    source = f"# generated\nNAME = {shared!r}\n"
    assert scan_generated.prompt_leak(prompt, source, FORBIDDEN) == [shared]


@pytest.mark.parametrize("prompt, source", [
    ("Build for acme_corp", "def handle(p): return p['x']\n"),   # literal in prompt only
    ("Build a thing", "NAME = 'acme_corp'\n"),                    # literal in source only
])
def test_prompt_leak_needs_the_literal_in_both(prompt, source):
    assert scan_generated.prompt_leak(prompt, source, FORBIDDEN) == []
