"""Vendoring the kit into the product — the step that makes a built platform gate.

The socket used to emit five code files and leave ``app/reasoning/kit/`` empty. The
kernel then disabled itself and refused every statement, which is correct by the
fail-closed rule and useless: the platform gated nothing and could answer nothing
either. These tests drive the vendoring, and the last one takes a REAL Store kit
through it and runs the emitted kernel against it, because "the files were copied"
is not the same claim as "the built platform gates".
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import types

import pytest
import yaml

from app.factory.build import reasoning_socket

from tests.factory.blocks_root import real_blocks_root

#: Resolved once, by the resolver the rest of this suite uses. The three
#: end-to-end tests below used to name ``C:/Users/user/Cerebrum-Blocks`` — a
#: path that exists on one laptop — and ``pytest.skip`` past it everywhere else,
#: so on CI, the only machine whose result anyone reads, the claim this file
#: exists to make was never tested at all. It reported as coverage and provided
#: none.
REAL_STORE = real_blocks_root()


def _store_declaring(kit: str, *files: str) -> pathlib.Path:
    """The real Store checkout, or a FAILURE that names how to point at one.

    Not a skip. CI checks the Store out and exports ``CEREBRUM_BLOCKS_ROOT``
    before pytest runs, so an unresolvable Store there is a broken pipeline, not
    a platform difference to step around — and a Store that no longer declares
    the kit named here is Store drift the Factory must hear about, which is the
    whole point of following the Store's head.
    """
    if REAL_STORE is None:
        pytest.fail(
            "no Store checkout resolved: set CEREBRUM_BLOCKS_ROOT to a "
            "Cerebrum-Blocks checkout. CI does this; an unnamed sibling "
            "checkout is honoured only at blocks.lock.json's pinned sha, "
            "because a floating one makes the same test assert different content"
        )
    for name in files:
        if not (REAL_STORE / "app" / "blocks" / kit / name).is_file():
            pytest.fail(
                f"Store {REAL_STORE} declares no app/blocks/{kit}/{name} — "
                f"the kit moved, was renamed, or this checkout is partial"
            )
    return REAL_STORE


class FakeWorkspace:
    """Records what the build wrote, and writes it, so a test can import it."""

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.written: list = []

    def write_text(self, relative, body: str) -> None:
        target = self.root / pathlib.Path(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        self.written.append(str(pathlib.Path(relative)).replace("\\", "/"))


class FakeCtx:
    def __init__(self, root: pathlib.Path, vertical=None) -> None:
        self.workspace = FakeWorkspace(root)
        self.blueprint = types.SimpleNamespace(vertical=vertical)
        self.plan = None


def _store(tmp_path: pathlib.Path, kit: str, *, manifest=None, invariants=None,
           questions=None) -> pathlib.Path:
    """A minimal Store tree holding one kit."""
    blocks = tmp_path / "store" / "app" / "blocks" / kit
    blocks.mkdir(parents=True, exist_ok=True)
    (blocks / "manifest.yaml").write_text(
        yaml.safe_dump(manifest if manifest is not None else {
            "kit": kit, "version": 1,
            "quantities": {"rate": {"units": ["currency_per_m2"]}},
            "figures": {"rate": {"value": None, "question": "derived: what is the rate?"}},
        }), encoding="utf-8")
    (blocks / "invariants.yaml").write_text(
        yaml.safe_dump(invariants if invariants is not None else {
            "invariants": [{
                "id": "INV-1", "kind": "grounding", "severity": "refuse", "hook": "H3",
                "applies_to": {"quantity": "any"}, "message": "{quantity} is not grounded",
                "measurement": "20 probes. Before: n uncited. After: 0.",
            }]
        }), encoding="utf-8")
    if questions is not None:
        (blocks / "questions.yaml").write_text(
            yaml.safe_dump(questions, allow_unicode=True), encoding="utf-8")
    return tmp_path / "store"


@pytest.fixture
def store_root(monkeypatch):
    """Point the Store resolver wherever a test says."""
    holder = {}

    def resolve():
        return holder.get("root")

    module = importlib.import_module("app.factory.blocks_source")
    monkeypatch.setattr(module, "resolve_blocks_root", resolve)
    return holder


# ── the three stated outcomes ─────────────────────────────────────────────

def test_a_vertical_with_no_kit_vendors_nothing_and_the_platform_still_builds(
        tmp_path, store_root, caplog):
    store_root["root"] = _store(tmp_path, "fitout")
    ctx = FakeCtx(tmp_path / "product", vertical=None)
    with caplog.at_level("WARNING"):
        written = reasoning_socket.emit(ctx)
    assert not [p for p in written if "/kit/" in p]
    assert reasoning_socket.KERNEL_PATH in written, "the socket still ships"
    assert "refuse every figure" in caplog.text


def test_an_unreachable_store_is_an_error_not_a_shrug(tmp_path, store_root, caplog):
    store_root["root"] = None
    ctx = FakeCtx(tmp_path / "product", vertical="fitout")
    with caplog.at_level("ERROR"):
        written = reasoning_socket.emit(ctx)
    assert not [p for p in written if "/kit/" in p]
    assert "Store unreachable" in caplog.text
    assert "refuse every figure" in caplog.text


def test_a_kit_with_no_invariants_fails_the_build_rather_than_shipping_a_dead_gate(
        tmp_path, store_root):
    store_root["root"] = _store(tmp_path, "fitout", invariants={"invariants": []})
    ctx = FakeCtx(tmp_path / "product", vertical="fitout")
    with pytest.raises(ValueError) as exc:
        reasoning_socket.emit(ctx)
    assert "no invariants" in str(exc.value)


def test_a_kit_whose_manifest_names_another_kit_fails_the_build(tmp_path, store_root):
    """Vendoring it would give this platform one domain's vocabulary under
    another domain's name."""
    store_root["root"] = _store(tmp_path, "fitout", manifest={
        "kit": "rail", "version": 1,
        "quantities": {"rate": {"units": ["currency_per_m2"]}},
    })
    ctx = FakeCtx(tmp_path / "product", vertical="fitout")
    with pytest.raises(ValueError) as exc:
        reasoning_socket.emit(ctx)
    assert "rail" in str(exc.value) and "fitout" in str(exc.value)


def test_a_kit_missing_its_manifest_fails_the_build(tmp_path, store_root):
    root = _store(tmp_path, "fitout")
    (root / "app" / "blocks" / "fitout" / "manifest.yaml").unlink()
    store_root["root"] = root
    ctx = FakeCtx(tmp_path / "product", vertical="fitout")
    with pytest.raises(FileNotFoundError):
        reasoning_socket.emit(ctx)


# ── what actually gets vendored ───────────────────────────────────────────

def test_the_kit_and_the_owners_sheet_are_both_vendored(tmp_path, store_root):
    sheet = {
        "kit": "fitout", "title": "Fit-Out — Questions I Cannot Answer",
        "answer_format": ["value", "unit", "quality band", "market", "source", "date"],
        "sections": {"B": {"title": "Rates"}},
        "questions": [{"id": "B.1", "section": "B", "gate": True, "covers": ["rate"],
                       "text": "Cost per m² all-in by your quality band."}],
    }
    store_root["root"] = _store(tmp_path, "fitout", questions=sheet)
    ctx = FakeCtx(tmp_path / "product", vertical="fitout")
    written = reasoning_socket.emit(ctx)
    assert reasoning_socket.KIT_MANIFEST in written
    assert reasoning_socket.KIT_INVARIANTS in written
    assert reasoning_socket.KIT_QUESTIONS in written
    landed = yaml.safe_load(
        (tmp_path / "product" / reasoning_socket.KIT_QUESTIONS).read_text(encoding="utf-8"))
    assert landed["questions"][0]["text"] == sheet["questions"][0]["text"], (
        "the owner's wording must arrive byte-for-byte, not reformatted in transit"
    )


def test_a_kit_without_a_sheet_vendors_and_warns_rather_than_failing(
        tmp_path, store_root, caplog):
    """Two Store kits have no sheet. They must still produce a working platform,
    and the log must not let that pass as an interview that has been done."""
    store_root["root"] = _store(tmp_path, "datacentre")
    ctx = FakeCtx(tmp_path / "product", vertical="datacentre")
    with caplog.at_level("WARNING"):
        written = reasoning_socket.emit(ctx)
    assert reasoning_socket.KIT_MANIFEST in written
    assert reasoning_socket.KIT_QUESTIONS not in written
    assert "NO owner question sheet" in caplog.text
    assert "derived" in caplog.text


# ── end to end: a real Store kit, driven through the emitted kernel ───────

def test_a_real_store_kit_vendors_and_the_emitted_kernel_gates_with_it(
        tmp_path, store_root, monkeypatch):
    """The claim this file exists to support: not that files were copied, but that
    the built platform gates, and asks the owner's questions while doing it."""
    store_root["root"] = _store_declaring("fitout", "questions.yaml")

    product = tmp_path / "product"
    ctx = FakeCtx(product, vertical="fitout")
    written = reasoning_socket.emit(ctx)
    assert reasoning_socket.KIT_QUESTIONS in written

    # Import it the way a product does: emit writes to app/reasoning/*, so the
    # product's own `app` package is what has to be mounted under that name.
    inner = product / "app"
    (inner / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.syspath_prepend(str(product))
    for name in [n for n in list(sys.modules)
                 if n in ("app", "builtapp") or n.startswith("app.")
                 or n.startswith("builtapp.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    kernel_mod = importlib.import_module("app.reasoning.kernel")

    kernel = kernel_mod.kernel
    assert kernel.enabled, f"the vendored kit did not load: {kernel.disabled_reason}"

    # It gates: an ungrounded figure is refused by the real fit-out invariants.
    outcome = kernel.answer_time([kernel_mod.Figure(quantity="rate", value=2500, unit="currency_per_m2")])
    assert outcome.verdict == "refused"

    # And it asks the owner's own questions, marked as the owner marked them.
    state = kernel.interview()
    # fit-out carries BOTH now: every Store kit has its own figure register, and the
    # owner's question sheet beside it. They are not alternatives — the register holds
    # this asset's figures, the sheet asks the organisation's rules.
    assert state["questions_source"] == "design_basis+owner_sheet"
    assert state["design_basis_supplied"] is True
    assert state["design_basis"]["answered"] == 0, "no interview has run on this build"
    assert state["questions"] == 62 and state["gating"] == 21
    assert state["ready"] is False
    assert state["required_fields"] == [
        "unit", "quality_band", "market", "source", "date", "confirmed_or_indicative"]
    first = state["next"][0]
    assert first["id"] == "B.1" and first["marked"] == "GATE"
    assert "quality band" in first["text"]

    # The rate figure's question is the owner's wording, not the derived one — and
    # it is B.2 ("your rate per package"), not B.1 ("cost per m² by quality band"),
    # because the link is an exact naming of the quantity rather than a guess at
    # which question sounds relevant.
    pending = importlib.import_module("app.reasoning.pending")
    asked = pending.unanswered()["rate"]
    assert "B.2" in asked and "rate per package" in asked
    assert "what is the rate" not in asked.lower(), (
        "the derived question won over the owner's own"
    )

    # An answer needs every field this domain requires, and then it sticks.
    with pytest.raises(ValueError):
        kernel.record_question_answer("B.1", "2500/m2", {"unit": "currency_per_m2"})
    kernel.record_question_answer("B.1", "2500/m2", {
        "unit": "currency_per_m2", "quality_band": "standard", "market": "Dubai",
        "source": "our 2026 tender book", "date": "2026-09-24",
        "confirmed_or_indicative": "confirmed"})
    assert kernel.interview()["outstanding"] == 20

    # And it never landed in the kit.
    kit_dir = inner / "reasoning" / "kit"
    assert sorted(p.name for p in kit_dir.iterdir()) == [
        "design_basis.yaml", "invariants.yaml", "manifest.yaml", "questions.yaml"], (
        "the kit arrives as its four declarative files and NOTHING else — no answer "
        "file, because answers are per-platform and this kit is signed and shared"
    )


# ── the kit's own figure register ──────────────────────────────────────────

def test_a_real_datacentre_build_arrives_holding_its_answers(tmp_path, store_root, monkeypatch):
    """datacentre has no question sheet and needs none: its own register,
    design_basis.yaml, is filled in from the completed encoding sheet for
    facility_01. A platform built on it must arrive with those 17 figures in hand.
    Reporting it as an un-interviewed domain, which an earlier version did, called
    the one answered domain in the Store an empty one."""
    store_root["root"] = _store_declaring("datacentre", "design_basis.yaml")

    product = tmp_path / "product"
    ctx = FakeCtx(product, vertical="datacentre")
    written = reasoning_socket.emit(ctx)
    assert reasoning_socket.KIT_DESIGN_BASIS in written
    assert reasoning_socket.KIT_QUESTIONS not in written, "datacentre has no sheet"

    inner = product / "app"
    (inner / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.syspath_prepend(str(product))
    for name in [n for n in list(sys.modules)
                 if n in ("app", "builtapp") or n.startswith("app.")
                 or n.startswith("builtapp.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    kernel_mod = importlib.import_module("app.reasoning.kernel")
    kernel = kernel_mod.kernel
    assert kernel.enabled, f"the vendored kit did not load: {kernel.disabled_reason}"

    state = kernel.interview()
    assert state["questions_source"] == "design_basis"
    assert state["interview_ran"] is True
    assert state["design_basis"]["answered"] == 17
    assert state["design_basis"]["figures"] == 18
    assert state["design_basis"]["open_figures"] == ["pue_guaranteed"]
    assert state["design_basis"]["facility"] == "facility_01"
    assert "facility_01" not in state["note"] or "17 of 18" in state["note"]

    # The answered figures are usable, by their own names, with no refusal.
    value, refusal = kernel.figure_value("generator_fuel_autonomy_hours")
    assert value == 120 and refusal is None
    value, refusal = kernel.figure_value("pue_design")
    assert value == 1.4 and refusal is None

    # The one open figure refuses, and names the register's scope rather than
    # offering a plausible number.
    value, refusal = kernel.figure_value("pue_guaranteed")
    assert value is None
    assert refusal and "figure register" in refusal
    assert "never carry to another site" in refusal

    pending = importlib.import_module("app.reasoning.pending")
    assert list(pending.unanswered()) == ["pue_guaranteed"]


def test_an_empty_register_vendors_and_every_figure_refuses(tmp_path, store_root, monkeypatch):
    """Five of the six registers are declared and empty. Empty is not "nothing to
    ask" -- it is everything still to ask, and every one of those figures refuses."""
    store_root["root"] = _store_declaring(
        "rail", "design_basis.yaml", "questions.yaml")

    product = tmp_path / "product"
    ctx = FakeCtx(product, vertical="rail")
    written = reasoning_socket.emit(ctx)
    # rail has BOTH: its own register and the owner's question sheet.
    assert reasoning_socket.KIT_DESIGN_BASIS in written
    assert reasoning_socket.KIT_QUESTIONS in written

    inner = product / "app"
    (inner / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.syspath_prepend(str(product))
    for name in [n for n in list(sys.modules)
                 if n in ("app", "builtapp") or n.startswith("app.")
                 or n.startswith("builtapp.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    kernel = importlib.import_module("app.reasoning.kernel").kernel
    assert kernel.enabled, kernel.disabled_reason

    state = kernel.interview()
    assert state["questions_source"] == "design_basis+owner_sheet", (
        "they are not alternatives: the register holds this route's figures, the "
        "sheet asks the organisation's rules"
    )
    assert state["design_basis"]["interview_ran"] is False
    assert state["design_basis"]["answered"] == 0
    assert state["questions"] == 82 and state["gating"] == 61

    for name in state["design_basis"]["open_figures"]:
        value, refusal = kernel.figure_value(name)
        assert value is None and refusal, f"{name} returned a value from an empty register"


def test_a_broken_register_disables_rather_than_losing_the_answers(tmp_path, store_root):
    """A register can arrive already answered. Treating an unreadable one as
    "no register" turns a platform that holds real figures into one that refuses
    them while reporting nothing wrong."""
    root = _store(tmp_path, "datacentre")
    (root / "app" / "blocks" / "datacentre" / "design_basis.yaml").write_text(
        "source: x\nscope: y\n", encoding="utf-8")
    store_root["root"] = root
    ctx = FakeCtx(tmp_path / "product", vertical="datacentre")
    reasoning_socket.emit(ctx)

    from app.factory.build.reasoning_socket import render_kernel

    # Drive the emitted kernel against the broken register directly. It must be a
    # real registered module, not a bare exec namespace: it resolves KIT_DIR from
    # __file__, and its dataclasses resolve their field types through sys.modules.
    emitted = tmp_path / "product" / "app" / "reasoning" / "kernel.py"
    module = types.ModuleType("emitted_kernel_probe")
    module.__file__ = str(emitted)
    sys.modules[module.__name__] = module
    try:
        exec(compile(render_kernel(), str(emitted), "exec"), module.__dict__)
        kernel = module.ReasoningKernel(kit_dir=emitted.parent / "kit")
    finally:
        sys.modules.pop(module.__name__, None)
    assert kernel.enabled is False
    assert "figure register" in kernel.disabled_reason


# ── which kit a vertical gets ───────────────────────────────────────────────

def test_a_kit_the_store_publishes_is_reachable_with_no_factory_edit(tmp_path):
    """The defect this covers is a missing entry, not a wrong one.

    ``VERTICAL_TO_KIT`` was a committed copy of the Store's kit list, so a kit
    that shipped in the Store was unreachable until somebody remembered to edit
    the Factory. ``stadium_venue`` shipped, the entry was absent, and every
    stadium build took the no-kit path and refused every figure — which reads as
    fail-closed, the one way for a missing kit to look like correct behaviour.
    """
    root = _store(tmp_path, "a_kit_nobody_hardcoded")
    blueprint = types.SimpleNamespace(vertical="a_kit_nobody_hardcoded")
    assert reasoning_socket.kit_for_vertical(
        blueprint, store_root=root) == "a_kit_nobody_hardcoded"


def test_the_store_is_asked_only_by_a_caller_that_already_holds_a_root(tmp_path):
    """Resolving the Store's root can CLONE it, so kit_for_vertical never does:
    a caller with no root gets no kit. Aliases are the KIT'S declaration
    (``serves_verticals`` in its manifest), so they resolve through a root --
    the Factory holds no alias table."""
    blueprint = types.SimpleNamespace(vertical="a_kit_nobody_hardcoded")
    assert reasoning_socket.kit_for_vertical(blueprint) is None
    assert reasoning_socket.kit_for_vertical(blueprint, store_root=None) is None
    root = _store(tmp_path, "zorblat_yards", manifest={
        "kit": "zorblat_yards", "version": 1, "serves_verticals": ["zorblat", "yards"]})
    alias = types.SimpleNamespace(vertical="yards")
    assert reasoning_socket.kit_for_vertical(alias) is None
    assert reasoning_socket.kit_for_vertical(alias, store_root=root) == "zorblat_yards"


def test_a_vertical_the_store_does_not_publish_still_gets_no_kit(tmp_path):
    """Asking the Store is not the same as guessing. A vertical with no kit must
    still resolve to None, or the socket vendors a directory that isn't there."""
    root = _store(tmp_path, "fitout")
    blueprint = types.SimpleNamespace(vertical="no_such_domain")
    assert reasoning_socket.kit_for_vertical(blueprint, store_root=root) is None


def test_a_directory_that_is_not_a_kit_is_not_reported_as_one(tmp_path):
    """``app/blocks`` holds plain blocks too. A kit is its two required
    declarative files, so a directory holding neither must not resolve."""
    root = _store(tmp_path, "fitout")
    (root / "app" / "blocks" / "plain_block").mkdir(parents=True)
    (root / "app" / "blocks" / "plain_block" / "block.py").write_text(
        "x", encoding="utf-8")
    blueprint = types.SimpleNamespace(vertical="plain_block")
    assert reasoning_socket.kit_for_vertical(blueprint, store_root=root) is None


def test_half_a_kit_does_not_resolve(tmp_path):
    """Half a kit is not a kit: a manifest with no invariants would build a
    platform whose gate has no rules to enforce."""
    root = _store(tmp_path, "half_a_kit")
    (root / "app" / "blocks" / "half_a_kit" / "invariants.yaml").unlink()
    blueprint = types.SimpleNamespace(vertical="half_a_kit")
    assert reasoning_socket.kit_for_vertical(blueprint, store_root=root) is None


def test_a_vertical_cannot_escape_the_blocks_directory(tmp_path):
    """The vertical arrives from a blueprint, so a name that walks out of
    ``app/blocks`` must not be turned into a path."""
    root = _store(tmp_path, "fitout")
    for hostile in ("../../etc", "..", ".ssh", "a/b", "a\b"):
        blueprint = types.SimpleNamespace(vertical=hostile)
        assert reasoning_socket.kit_for_vertical(
            blueprint, store_root=root) is None, hostile


def test_the_stadium_aliases_all_reach_the_sports_venue_kit():
    """Four archetypes, one kit. A touring event is a guest in someone else's
    building, so "mega_event" and "stadium" are the same kit and must not become
    two. The aliases are the Store kit's own serves_verticals."""
    root = _store_declaring("stadium_venue", "manifest.yaml", "invariants.yaml")
    for vertical in ("stadium", "stadium_venue", "sports_venue", "arena", "mega_event"):
        blueprint = types.SimpleNamespace(vertical=vertical)
        assert reasoning_socket.kit_for_vertical(
            blueprint, store_root=root) == "stadium_venue", vertical


def test_a_build_whose_vertical_only_the_store_knows_vendors_the_kit(
        tmp_path, store_root):
    """End to end on the leg that matters: a kit with no alias entry is vendored
    by a real build, because ``emit`` resolves the Store before the kit."""
    store_root["root"] = _store(tmp_path, "a_kit_nobody_hardcoded")
    ctx = FakeCtx(tmp_path / "product", vertical="a_kit_nobody_hardcoded")
    written = reasoning_socket.emit(ctx)
    assert reasoning_socket.KIT_MANIFEST in written
    assert reasoning_socket.KIT_INVARIANTS in written


def test_a_build_with_no_vertical_never_asks_the_store_at_all(tmp_path, monkeypatch):
    """No vertical means no kit, decided without touching the Store — resolving
    it can clone, and a build that states no vertical has nothing to look up."""
    asked = []
    module = importlib.import_module("app.factory.blocks_source")
    monkeypatch.setattr(module, "resolve_blocks_root",
                        lambda: asked.append(1) or None)
    ctx = FakeCtx(tmp_path / "product", vertical=None)
    reasoning_socket.emit(ctx)
    assert not asked, "a build with no vertical resolved the Store for nothing"


def test_a_real_stadium_build_gates_with_the_sports_venue_kit(
        tmp_path, store_root, monkeypatch):
    """The stadium path, end to end, because it was the one that silently did not work.

    ``stadium_venue`` had no entry in the alias table, so ``kit_for_vertical``
    returned None, nothing was vendored, and the platform refused every figure
    while logging that refusing every figure was fail-closed behaviour. Nothing
    here asserted otherwise, so this test is the reason that cannot recur.

    It also drives the kit's GROUNDING record, which the kit shipped without: 43
    invariants checked how a figure was qualified, sourced, banded, aged and
    carried, and none of them asked whether it came from anywhere. An uncited
    licensed capacity passed every one of them.
    """
    store_root["root"] = _store_declaring(
        "stadium_venue", "manifest.yaml", "invariants.yaml", "design_basis.yaml")

    product = tmp_path / "product"
    ctx = FakeCtx(product, vertical="stadium")
    written = reasoning_socket.emit(ctx)
    assert reasoning_socket.KIT_MANIFEST in written
    assert reasoning_socket.KIT_INVARIANTS in written
    assert reasoning_socket.KIT_DESIGN_BASIS in written
    # No owner sheet for this domain, and the platform must not imply one.
    assert reasoning_socket.KIT_QUESTIONS not in written

    inner = product / "app"
    (inner / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
    monkeypatch.syspath_prepend(str(product))
    for name in [n for n in list(sys.modules)
                 if n in ("app", "builtapp") or n.startswith("app.")
                 or n.startswith("builtapp.")]:
        monkeypatch.delitem(sys.modules, name, raising=False)
    kernel_mod = importlib.import_module("app.reasoning.kernel")

    kernel = kernel_mod.kernel
    assert kernel.enabled, f"the vendored stadium kit did not load: {kernel.disabled_reason}"

    # GROUNDING: a capacity with no source is refused, naming source_id. Without
    # the grounding record this figure passed every other rule in the kit.
    ungrounded = kernel_mod.Figure(quantity="event_capacity", value=52000, unit="persons")
    outcome = kernel.answer_time([ungrounded])
    assert outcome.verdict == "refused"
    # The emitted kernel's Finding names the field `invariant`; the Store's names
    # it `invariant_id`. Same concept, two spellings, so assert on the built one.
    ids = [f.invariant for f in outcome.findings]
    assert "INV-SV-GROUNDING" in ids, f"grounding did not fire: {ids}"
    assert any("source_id" in (f.message or "") for f in outcome.findings)

    # SCOPE, at H0: an operational go/no-go is refused BEFORE retrieval, and the
    # block's contract for "nothing was searched" is what must say so.
    refused = kernel.pre_retrieval("can we start the match")
    assert refused.verdict == "refused"
    assert any(f.invariant.startswith("SCOPE:") for f in refused.findings)

    # Not an over-refusing kit: a venue question with no operational authority in
    # it is allowed through to retrieval.
    allowed = kernel.pre_retrieval("what is the licensed capacity in the football configuration")
    assert allowed.verdict != "refused", (
        "H0 refused an ordinary venue question — a scope gate that refuses "
        "everything is indistinguishable from a broken corpus"
    )

    # No interview has run, so the register refuses rather than inventing.
    value, refusal = kernel.figure_value("event_capacity")
    assert value is None
    assert refusal, "an unanswered figure must refuse, not return None silently"
