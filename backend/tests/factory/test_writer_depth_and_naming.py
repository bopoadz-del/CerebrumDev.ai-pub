"""The writer is given room, and the product carries the customer's name.

Two live observations:

- The writer stopped early. Both live passes finished in 10-15 minutes
  against a 25-minute wall and handed over thin work: FinOps
  (sess_065fc3eac75c4f62) shipped an approval workflow that routed by
  invented tiers with no approve/reject step. Nothing in the prompt told the
  agent it may run the gates itself, research a domain fact, or revise. v7
  says the gates are the bar and it can reach them before yielding.

- The product was named by the model, not the customer. A Dubai finance
  platform came back "FinOps Central". The name is the customer's.
"""

from __future__ import annotations

from app.factory.build.writer_prompt import PROMPT_VERSION, render_writer_prompt
from app.factory.product_architect import _LLM_DRAFT_SYSTEM


class _Blueprint:
    product_id = "p"
    product_name = "P"
    vertical = "v"
    summary = "s"


def _writer() -> str:
    return render_writer_prompt(_Blueprint(), brief="build it")


class TestTheWriterIsGivenRoom:
    def test_it_is_told_it_can_run_the_gates_itself(self):
        text = _writer()
        assert 'python -m pytest -m "not pilot" -q' in text
        assert "POST a record, GET it back" in text

    def test_it_is_told_to_research_what_it_does_not_know(self):
        text = _writer()
        assert "Research what you do not know" in text
        assert "where it came from" in text

    def test_it_is_told_not_to_stop_at_the_first_thing_that_compiles(self):
        text = _writer()
        assert "Do not stop at the first thing that compiles" in text
        assert "You have the wall" in text

    def test_researching_a_fact_does_not_make_it_the_customers_decision(self):
        """It may learn a rate; the customer still owns the value."""
        text = _writer()
        assert "stays a named setting they can change" in text

    def test_the_version_records_the_change(self):
        # DEPTH arrived in v7; later versions must keep it.
        assert int(PROMPT_VERSION.rsplit(".v", 1)[1]) >= 7
        assert _writer().startswith(f"<!-- {PROMPT_VERSION} -->")

    def test_nothing_forbids_the_agent_from_inventing(self):
        """The gates hold the work; the prompt does not fence the craft."""
        text = _writer().lower()
        for banned in ("do not invent", "never invent", "you may not create"):
            assert banned not in text, banned


class TestTheProductCarriesTheCustomersName:
    def test_the_architect_is_told_the_name_is_the_customers(self):
        assert "product_name is the customer's, not yours" in _LLM_DRAFT_SYSTEM
        assert "use\n  that name exactly" in _LLM_DRAFT_SYSTEM

    def test_it_may_not_invent_a_brand(self):
        assert "Never invent a\n  brand" in _LLM_DRAFT_SYSTEM

    def test_the_json_shape_asks_for_the_users_words(self):
        assert '"product_name": "<the name the user gave, or their own words for it>"' in _LLM_DRAFT_SYSTEM
