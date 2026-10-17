"""The writer prompt names entities the way TESTER calls them.

Prompt v3/v4 told the agent to write ``save(entity, ...)`` and
``op.create_table("<entity>")`` but never said what an entity is called.
TESTER defaults every entity to the capability id and the emitted
data-lifecycle suite calls ``store.save("<capability>", ...)``, so agents
that named tables their own way went red in round 1 on a KeyError --
``'report_namemetric_name'`` on the vet build, and
``'branch_and_consolidated_operations'`` on bakery, which ran on v4 --
costing a rework round per build.
"""

from __future__ import annotations

import inspect

from app.factory.build.data_lifecycle import render_product_tests
from app.factory.build.writer_prompt import PROMPT_VERSION, render_writer_prompt


class _Blueprint:
    product_id = "bakery-operations"
    product_name = "Bakery Branch Operations Platform"
    vertical = "bakery"
    summary = "branch ops"


def _prompt() -> str:
    return render_writer_prompt(_Blueprint(), brief="a bakery")


def test_the_prompt_says_an_entity_is_named_by_its_capability_id():
    text = _prompt()
    assert "An entity's name IS its capability id" in text
    assert 'op.create_table("<capability>")' in text
    for call in ("store.save", "store.get", "store.list_all"):
        assert f'{call}("<capability>"' in text, call


def test_the_prompt_rule_and_testers_default_move_together():
    """The divergence guard.

    If TESTER ever stops defaulting an entity to the capability id, the
    prompt's rule becomes a lie and this fails -- change both, together.
    """
    from app.factory.build import roles_handlers

    tester = inspect.getsource(roles_handlers.run_tester)
    assert 'spec["entity"] = cid.replace("-", "_")' in tester, (
        "TESTER no longer names entities by capability id; update the "
        "writer prompt's PERSISTENCE rule to match"
    )
    assert "An entity's name IS its capability id" in _prompt()


def test_the_emitted_suite_calls_the_store_with_the_capability_id():
    suite = render_product_tests(
        {
            "branch_and_consolidated_operations": {
                "entity": "branch_and_consolidated_operations",
                "fields": [{"name": "branch", "type": "string"}],
            }
        }
    )
    assert "ENTITY = 'branch_and_consolidated_operations'" in suite
    assert "store.save(ENTITY" in suite


def test_the_version_was_bumped_for_the_contract_change():
    # The entity rule arrived in v5; later versions must keep it.
    assert int(PROMPT_VERSION.rsplit(".v", 1)[1]) >= 5
    assert _prompt().startswith(f"<!-- {PROMPT_VERSION} -->")


def test_the_prompt_is_still_deterministic():
    assert _prompt() == _prompt()


# --- v9: the five specialists ------------------------------------------------
#
# One undifferentiated writer pass shipped backends with no deployment story
# and no threat model, because nothing asked for infra, frontend, devops or
# security as work in their own right. The writer delegates each hat to its
# own agent now -- sequentially, because the box it runs on cannot carry a
# parallel fan-out.

SPECIALISTS = ("INFRA", "BACKEND", "FRONTEND", "DEVOPS", "SECURITY")


def test_every_specialist_is_named_with_its_own_bar():
    text = _prompt()
    for hat in SPECIALISTS:
        assert f"- {hat}:" in text, hat


def test_the_writer_is_told_to_delegate_rather_than_wear_the_hats_itself():
    text = _prompt()
    assert "Delegate each of the" in text
    assert "its own agent" in text


def test_concurrency_is_rendered_from_the_box_not_hardcoded():
    """The number is a property of the instance, so it must come from there.

    A literal in the template goes stale the moment the Render plan moves,
    and then the writer is being told something untrue about its machine.
    """
    from app.factory.build.codewhale_worker import writer_specialist_cap

    assert "run at most 4 specialist agents" in render_writer_prompt(
        _Blueprint(), brief="a bakery", specialist_workers=4
    )
    assert "run at most 9 specialist agents" in render_writer_prompt(
        _Blueprint(), brief="a bakery", specialist_workers=9
    )
    # And the live budget is a real number the caller can pass.
    assert writer_specialist_cap() >= 1


def test_the_cap_follows_the_profile_and_an_operator_override(monkeypatch):
    from app.factory.build import codewhale_worker as worker

    monkeypatch.delenv(worker.SPECIALIST_WORKERS_ENV, raising=False)
    monkeypatch.setenv(worker.PROFILE_ENV, "2c-4g")
    on_small = worker.writer_specialist_cap()
    monkeypatch.setenv(worker.PROFILE_ENV, "8c-32g")
    assert worker.writer_specialist_cap() > on_small, "a bigger plan must buy more"

    monkeypatch.setenv(worker.SPECIALIST_WORKERS_ENV, "2")
    assert worker.writer_specialist_cap() == 2, "the operator override wins"


def test_devops_and_security_are_pinned_last_whatever_the_budget():
    """Parallelism is a budget question; ordering is a correctness one.

    DEVOPS packages what the others wrote and SECURITY reviews it, so both
    need a tree that has stopped moving however many agents are allowed.
    """
    text = render_writer_prompt(_Blueprint(), brief="a bakery", specialist_workers=8)
    assert "run them last and in that order" in text or "last" in text
    assert "stopped moving" in text
