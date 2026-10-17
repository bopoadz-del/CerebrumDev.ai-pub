"""C-BRIEF staging: ``ensure_coder_brief`` is the whole of domain_handoff.

The module used to carry a ~250-line domain-spec table (DomainSpec,
FINANCE_SPEC, detect_domain, is_finance_domain and friends) with ZERO
production consumers, and a webhook flow that was excised earlier. Both
are gone. What survives is the one function with a real caller:
roles_handlers' CLONER step, which freezes ``docs/coder_brief.md`` so the
CodeWhale (DeepSeek) WRITER reads a compiled C-BRIEF instead of authoring
the platform blind.

That function previously had NO coverage anywhere in backend/tests. These
tests cover all four of its branches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.factory.build import domain_handoff
from app.factory.build.domain_handoff import BRIEF_REL, ensure_coder_brief


class _Compiled:
    """Stands in for brief_compiler.CompiledBrief — only .text is read."""

    def __init__(self, text: str) -> None:
        self.text = text


class _Blueprint:
    product_id = "probe-platform"
    product_name = "Probe Platform"
    vertical = "probe"
    summary = "deterministic probe"


def _patch_compiler(monkeypatch, *, text: str = "# COMPILED C-BRIEF\n\nblocks\n"):
    """Replace the real compiler; record the kwargs it was handed."""
    seen: dict = {}

    def fake_compose(blueprint, *, plan=None, blocks_root=None, **kw):
        seen["blueprint"] = blueprint
        seen["plan"] = plan
        seen["blocks_root"] = blocks_root
        return _Compiled(text)

    import app.factory.build.cbrief as cbrief_mod

    monkeypatch.setattr(cbrief_mod, "compose_cbrief", fake_compose)
    return seen


def _forbid_compiler(monkeypatch):
    """Any call to the compiler is a failure for this test."""

    def boom(*a, **kw):  # pragma: no cover - the assertion is that it is unused
        raise AssertionError(
            "compose_cbrief was called — an existing non-empty brief was "
            "recompiled and the frozen C-BRIEF is not stable"
        )

    import app.factory.build.cbrief as cbrief_mod

    monkeypatch.setattr(cbrief_mod, "compose_cbrief", boom)


# -- branch 1: an existing, non-empty brief is frozen ------------------------


def test_existing_nonempty_brief_is_not_recompiled(tmp_path, monkeypatch):
    _forbid_compiler(monkeypatch)
    dest = tmp_path / BRIEF_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("# frozen\n\nthe brief CLONER already staged\n", encoding="utf-8")
    before = dest.read_bytes()

    out = ensure_coder_brief(tmp_path, blueprint=_Blueprint())

    assert out == dest
    assert dest.read_bytes() == before


# -- branch 2: a zero-byte brief is NOT treated as staged --------------------


def test_a_zero_byte_existing_brief_is_recompiled(tmp_path, monkeypatch):
    """The ``st_size > 0`` guard. A zero-byte file on disk means the WRITER
    reads an EMPTY C-BRIEF and authors blind — the file existing is not
    the same as the brief having landed."""
    seen = _patch_compiler(monkeypatch, text="# recompiled\n")
    dest = tmp_path / BRIEF_REL
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"")
    assert dest.stat().st_size == 0

    out = ensure_coder_brief(tmp_path, blueprint=_Blueprint())

    assert out == dest
    assert dest.read_text(encoding="utf-8") == "# recompiled\n"
    assert seen["blueprint"] is not None


# -- branch 3: no blueprint -> a VISIBLE placeholder, never silence ---------


def test_no_blueprint_writes_a_visible_placeholder(tmp_path):
    out = ensure_coder_brief(tmp_path, blueprint=None)

    assert out == tmp_path / BRIEF_REL
    assert out.is_file()
    text = out.read_text(encoding="utf-8")
    assert text.strip()
    assert "brief unavailable" in text


# -- branch 4: a blueprint compiles the C-BRIEF to docs/ ---------------------


def test_blueprint_compiles_the_cbrief_to_docs(tmp_path, monkeypatch):
    seen = _patch_compiler(monkeypatch, text="# COMPILED C-BRIEF\n\nCUT 1 INVENTORY\n")
    blocks_root = tmp_path / "blocks"
    plan = object()

    out = ensure_coder_brief(
        tmp_path / "nested" / "workspace",
        blueprint=_Blueprint(),
        plan=plan,
        blocks_root=blocks_root,
    )

    assert out == tmp_path / "nested" / "workspace" / "docs" / "coder_brief.md"
    assert out.parent.is_dir(), "parents must be created, not assumed"
    assert out.read_text(encoding="utf-8") == "# COMPILED C-BRIEF\n\nCUT 1 INVENTORY\n"
    # The caller's plan and blocks_root reach the compiler unchanged: CLONER
    # passes ctx.plan/ctx.blocks_root and re-planning would not be free.
    assert seen["plan"] is plan
    assert seen["blocks_root"] == blocks_root


def test_brief_rel_is_the_path_the_writer_reads_back():
    assert BRIEF_REL == Path("docs") / "coder_brief.md"


# -- the module is C-BRIEF staging ONLY -------------------------------------


@pytest.mark.parametrize(
    "cut",
    [
        "DomainSpec",
        "FINANCE_SPEC",
        "AUTOMOTIVE_SPEC",
        "FINANCE_VERTICALS",
        "AUTOMOTIVE_VERTICALS",
        "SPEC_BY_DOMAIN",
        "detect_domain",
        "is_finance_domain",
    ],
)
def test_the_zero_consumer_domain_table_stays_cut(cut):
    """A guard against the table being re-justified back into existence.

    It had no consumer in backend/app, backend/scripts or frontend/src —
    only the tests that existed to reference it. The module's job is to
    stage a C-BRIEF; resolving verticals is not something anything asks it
    to do.
    """
    assert not hasattr(domain_handoff, cut)


def test_the_cbrief_compiler_does_not_come_from_cli_pivot():
    """The C-BRIEF compiler must not be hostage to the Cursor seam.

    ``cli_pivot`` is scheduled for deletion. While ``ensure_coder_brief``
    imported ``compose_cbrief`` from it, deleting it would have silently
    degraded every build's brief (roles_handlers swallows the failure into
    a note), leaving the WRITER to author blind.
    """
    import inspect

    source = inspect.getsource(ensure_coder_brief)
    assert "cli_pivot" not in source
    assert "from app.factory.build.cbrief import compose_cbrief" in source
