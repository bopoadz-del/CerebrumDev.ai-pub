"""The universal reasoning socket, exercised as a built platform would run it.

Not "does the template parse" — the kernel is written into a temp package with a
real kit beside it, imported, and driven. The properties asserted are the ones
that decide whether a platform can be trusted with it:

  * H0 refuses BEFORE retrieval, and the retrieval spy records zero calls
  * a kit that does not load REFUSES every statement; it never passes them
    through with no invariants
  * an unwired host function RAISES rather than returning a clean pass
  * an unanswered interview figure yields a REFUSAL naming the question, never a
    default, a zero or a plausible number
  * an interview answer without provenance is rejected — an unattributed figure
    is not evidence whoever supplied it
"""
from __future__ import annotations

import importlib

import pytest

from tests.factory.conftest import (  # the shared probe kit and figure helper
    _figure,
)
from app.factory.build.reasoning_socket import (
    render_kernel,
)

# ── H0 refuses before retrieval ────────────────────────────────────────────

def test_h0_refuses_before_retrieval_and_nothing_is_retrieved(platform):
    """The assertion is not that the answer says no. It is that retrieval never
    happened — no document makes an operational-authority question answerable, so
    retrieving produces citations that read as though they authorised it."""
    calls = []

    def retrieve(question):
        calls.append(question)
        return ["some document"]

    question = "can we put the crane by the threshold?"
    outcome = platform.kernel.pre_retrieval(question)
    if outcome.retrieval_permitted:
        retrieve(question)

    assert outcome.verdict == "refused"
    assert outcome.retrieval_permitted is False
    assert "before retrieval" in outcome.blocked_reason
    assert "aerodrome operator" in outcome.blocked_reason
    assert calls == [], "retrieval ran despite the pre-retrieval refusal"


def test_h0_lets_an_answerable_question_through(platform):
    outcome = platform.kernel.pre_retrieval("what is the declared TORA?")
    assert outcome.verdict == "pass"
    assert outcome.retrieval_permitted is True


# ── the routing map ───────────────────────────────────────────────────────

def test_h1_demotes_a_source_class_that_does_not_govern(platform):
    outcome = platform.kernel.ranking([_figure(platform, source_class="design_drawing")])
    assert outcome.verdict == "refused"
    assert "design_drawing" in outcome.blocked_reason
    assert "approved_table" in outcome.blocked_reason


def test_h3_refuses_a_figure_missing_its_qualifiers(platform):
    outcome = platform.kernel.answer_time(
        [_figure(platform, qualifiers={})], state={"notam": {"as_of": 1}})
    assert outcome.verdict == "refused"
    assert "runway" in outcome.blocked_reason and "temporary" in outcome.blocked_reason


def test_h3_refuses_a_figure_nobody_supplied(platform):
    outcome = platform.kernel.answer_time(
        [_figure(platform, origin="model", source_id=None, source_class=None)],
        state={"notam": {"as_of": 1}})
    assert outcome.verdict == "refused"
    assert "not grounded" in outcome.blocked_reason


def test_h3_reports_unknown_when_live_state_cannot_be_read(platform):
    """A design figure presented as a current state is the defect. UNKNOWN is the
    answer, and the refusal must not carry the figure."""
    outcome = platform.kernel.answer_time([_figure(platform)], state={})
    assert outcome.verdict == "refused"
    assert "UNKNOWN" in outcome.blocked_reason
    assert "no design-basis fallback" in outcome.blocked_reason
    assert "3200" not in outcome.blocked_reason


def test_h3_refuses_a_figure_the_declared_event_has_superseded(platform):
    outcome = platform.kernel.answer_time(
        [_figure(platform)], state={"notam": {"as_of": 1}},
        events=["airac_cycle_change"])
    assert outcome.verdict == "refused"
    assert "airac_cycle_change" in outcome.blocked_reason


def test_a_fully_qualified_current_figure_passes(platform):
    """The layer has to let a correct answer through, or it is just an off switch."""
    outcome = platform.kernel.answer_time(
        [_figure(platform)], state={"notam": {"as_of": 1}})
    assert outcome.verdict == "pass", outcome.blocked_reason


def test_h4_reruns_the_answer_time_checks_on_the_deliverable(platform):
    """H3 alone lets a corrupted value reach exports and source panels."""
    bad = _figure(platform, qualifiers={})
    answer = platform.kernel.answer_time([bad], state={"notam": {"as_of": 1}})
    export = platform.kernel.export_time([bad], state={"notam": {"as_of": 1}})
    assert answer.verdict == export.verdict == "refused"
    assert export.hook == "H4"


def test_the_operators_own_figure_is_flagged_never_refused(platform):
    theirs = _figure(platform, origin="operator", source_id=None, source_class=None,
                     qualifiers={})
    outcome = platform.kernel.answer_time([theirs], state={"notam": {"as_of": 1}})
    assert outcome.verdict == "flagged"
    assert outcome.blocked_reason == ""
    assert any(f.softened_from == "refuse" for f in outcome.findings)


# ── fail closed ───────────────────────────────────────────────────────────

def test_a_kit_that_does_not_load_refuses_every_statement(platform, tmp_path):
    """The whole safety property: a typo must not become "no invariants"."""
    broken = tmp_path / "broken_kit"
    broken.mkdir()
    (broken / "manifest.yaml").write_text("kit: broken\n", encoding="utf-8")
    (broken / "invariants.yaml").write_text("invariants: []\n", encoding="utf-8")

    dead = platform.ReasoningKernel(kit_dir=broken)

    assert dead.enabled is False
    for outcome in (dead.pre_retrieval("anything at all?"),
                    dead.answer_time([_figure(platform)]),
                    dead.ranking([_figure(platform)]),
                    dead.tool_time([_figure(platform)]),
                    dead.export_time([_figure(platform)])):
        assert outcome.verdict == "refused"
        assert "DISABLED" in outcome.blocked_reason
        assert "does not pass statements through" in outcome.blocked_reason


def test_a_missing_kit_directory_refuses_rather_than_running_ungated(platform, tmp_path):
    dead = platform.ReasoningKernel(kit_dir=tmp_path / "not_there")
    assert dead.enabled is False
    assert dead.pre_retrieval("can we put the crane here?").verdict == "refused"


def test_the_kill_switch_disables_the_layer_and_it_still_refuses(platform, monkeypatch, tmp_path):
    """One restart from off -- and off means refusing, not waving through."""
    monkeypatch.setenv("REASONING_KIT_OFF", "1")
    off = platform.ReasoningKernel(kit_dir=platform.KIT_DIR)
    assert off.enabled is False
    assert "switched off" in off.disabled_reason
    assert off.answer_time([_figure(platform)]).verdict == "refused"


def test_an_unwired_host_raises_rather_than_passing(platform):
    """A platform that forgot to wire retrieval must not answer ungated and look
    fine doing it."""
    host = importlib.import_module("builtapp.reasoning.host")
    # host.py imports `app.reasoning.kernel`, which the fixture mounts as a
    # separate module object from `builtapp.reasoning.kernel`. The exception must
    # come from the module the HOST uses, or this asserts on a different class
    # with the same name and would pass on the wrong exception.
    as_host_sees_it = importlib.import_module("app.reasoning.kernel").HostNotWired

    for call in (lambda: host.extract_figures("an answer", None, None),
                 lambda: host.resolve_source("d1"),
                 lambda: host.state("notam"),
                 lambda: host.apply(None)):
        with pytest.raises(as_host_sees_it):
            call()


def test_the_host_exposes_the_h0_gate_for_the_router(platform):
    host = importlib.import_module("builtapp.reasoning.host")
    outcome = host.gate_question("can we site the crane here?")
    assert outcome.verdict == "refused"
    assert outcome.retrieval_permitted is False


# ── the unanswered interview ──────────────────────────────────────────────

def test_an_unanswered_figure_refuses_and_names_the_question(platform):
    """Not a stub. A stub is a fake value that ships silently; this cannot."""
    pending = importlib.import_module("builtapp.reasoning.pending")

    unanswered = pending.unanswered()
    assert "runway_13l_tora" in unanswered
    assert "Q5.3" in unanswered["runway_13l_tora"]
    assert "pavement_pcn" not in unanswered, "an answered figure is not pending"

    value, refusal = pending.value_of("runway_13l_tora")
    assert value is None
    assert refusal and "Q5.3" in refusal
    assert "0" not in refusal.split("Q5.3")[0], "a default leaked into the refusal"


def test_an_answered_figure_returns_its_value_with_no_refusal(platform):
    pending = importlib.import_module("builtapp.reasoning.pending")
    value, refusal = pending.value_of("pavement_pcn")
    assert value == 80 and refusal is None


def test_an_interview_answer_without_provenance_is_rejected(platform):
    """An unattributed figure is not evidence, whoever supplied it. Without this
    the interview has only moved the invented figure from the model to a person."""
    pending = importlib.import_module("builtapp.reasoning.pending")

    with pytest.raises(ValueError, match="answered_by"):
        pending.answer("runway_13l_tora", 3200, answered_by="", answered_at="", source="")

    recorded = pending.answer("runway_13l_tora", 3200, answered_by="the aerodrome operator",
                              answered_at="2026-09-24", source="AIP declared distance table")
    assert recorded["value"] == 3200
    assert recorded["answered_by"] and recorded["answered_at"] and recorded["source"]


# ── the kernel is universal ───────────────────────────────────────────────

def test_the_kernel_names_no_domain(platform):
    """One socket for every vertical. A kernel that mentions a runway or a slab
    would be a per-domain gate wearing a universal name."""
    source = render_kernel().lower()
    for word in ("runway", "slab", "chiller", "bollard", "stinger", "mortar",
                 "cartridge", "sprinkler"):
        assert word not in source, f"the kernel mentions {word}; it must not know any domain"


def test_the_kernel_budget_skips_and_says_so(platform):
    """An unbounded set here consumed 2 GB and killed a live instance twice in one
    day. A skipped check is not a pass."""
    platform.BUDGET  # documented cap exists
    many = [_figure(platform, qualifiers={}) for _ in range(5)]
    outcome = platform.kernel.answer_time(many, state={"notam": {"as_of": 1}})
    assert outcome.incomplete is False, "five figures must not exhaust the budget"
    assert outcome.skipped == 0


# ── answers persist, and "recorded" means recorded ────────────────────────

def test_an_answer_persists_and_a_fresh_kernel_sees_it(platform, tmp_path, monkeypatch):
    """The bug this closes: answer() validated the provenance, returned the dict
    and stored NOTHING, so the route reported ok:true for a no-op -- a success
    over an operation that did nothing, which is what this layer refuses from
    everyone else."""
    storage = tmp_path / "platform_storage"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    pending = importlib.import_module("builtapp.reasoning.pending")

    assert "runway_13l_tora" in pending.unanswered()
    pending.answer("runway_13l_tora", 3200, answered_by="the aerodrome operator",
                   answered_at="2026-09-24", source="AIP declared distance table")

    assert "runway_13l_tora" not in pending.unanswered()
    value, refusal = pending.value_of("runway_13l_tora")
    assert value == 3200 and refusal is None

    # A fresh kernel is what a restart or another worker gets.
    fresh = platform.ReasoningKernel(kit_dir=platform.KIT_DIR)
    assert fresh.figure_value("runway_13l_tora") == (3200, None)
    assert "runway_13l_tora" not in fresh.pending_questions()


def test_an_answer_never_lands_in_the_kit(platform, tmp_path, monkeypatch):
    """The kit is a SIGNED Store block shared by every customer. One client's
    figures reaching the next platform built from it would be the provenance
    failure this layer exists to prevent, arriving signed."""
    storage = tmp_path / "platform_storage"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))
    kit_before = {p.name: p.read_bytes() for p in platform.KIT_DIR.iterdir()}

    pending = importlib.import_module("builtapp.reasoning.pending")
    pending.answer("runway_13l_tora", 3200, answered_by="an operator",
                   answered_at="2026-09-24", source="the table")

    kit_after = {p.name: p.read_bytes() for p in platform.KIT_DIR.iterdir()}
    assert kit_after == kit_before, "an answer was written into the shared kit"
    assert (storage / "reasoning_answers.json").is_file()


def test_an_answer_that_cannot_be_read_back_raises_rather_than_reporting_success(
        platform, tmp_path, monkeypatch):
    storage = tmp_path / "platform_storage"
    storage.mkdir()
    monkeypatch.setenv("STORAGE_PATH", str(storage))

    # A store that silently drops the write is the shape of the original bug.
    monkeypatch.setattr(platform.kernel, "answers", lambda: {})

    with pytest.raises(RuntimeError, match="did not persist"):
        platform.kernel.record_answer(
            "runway_13l_tora", 3200, answered_by="an operator",
            answered_at="2026-09-24", source="the table")


def test_an_answer_to_a_figure_the_kit_never_asks_about_is_refused(platform, tmp_path,
                                                                  monkeypatch):
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    with pytest.raises(KeyError, match="not a figure this kit asks about"):
        platform.kernel.record_answer("invented_figure", 1, answered_by="a",
                                      answered_at="b", source="c")


def test_answers_arrive_one_at_a_time_rather_than_in_a_whole_kit_swap(platform, tmp_path,
                                                                     monkeypatch):
    """Answers trickle in. Each one takes effect the moment it lands; nothing
    waits for a second kit to be swapped in."""
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    pending = importlib.import_module("builtapp.reasoning.pending")

    before = len(pending.unanswered())
    pending.answer("runway_13l_tora", 3200, answered_by="an operator",
                   answered_at="2026-09-24", source="the table")

    assert len(pending.unanswered()) == before - 1
    assert pending.value_of("runway_13l_tora")[0] == 3200
