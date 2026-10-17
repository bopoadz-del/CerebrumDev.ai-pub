"""Probe ids are data. Modules decide by a probe's shape, class or attribute.

Every test here edits the PROBE SET (never a module) with an invented probe,
``ZQ9``, and shows the module that consumes it treats it exactly like an
existing probe of the same kind -- with no code change.
"""

from __future__ import annotations

import copy

import pytest

from app.factory.build import probe_set


@pytest.fixture
def edited(monkeypatch):
    """A writable copy of the real probe set, served to every consumer."""
    data = copy.deepcopy(probe_set.load())
    monkeypatch.setattr(probe_set, "load", lambda: data)
    return data


def test_the_real_probe_set_is_well_formed():
    data = probe_set.load()
    assert data["defects"] and data["stages"] and data["network_postures"]
    # Every shape names exactly one defect, and that defect answers for it.
    for code, row in data["defects"].items():
        for shape in row.get("shapes") or []:
            assert probe_set.code_for(shape) == code


def test_a_shape_answers_with_whatever_id_the_probe_set_gives_it(edited):
    """Detectors report a SHAPE; the id is the probe set's business."""
    from app.factory.build.domain_acceptance import inspect_lotdesk_domain

    old = probe_set.code_for("hollow_queue")
    edited["defects"]["ZQ9"] = dict(edited["defects"].pop(old))
    result = inspect_lotdesk_domain()
    assert "ZQ9" in result["codes"] and old not in result["codes"]
    assert result["zq9_present"] is True


def test_a_new_health_class_defect_joins_the_health_gate(edited):
    """deploy.py keeps every finding of class 'health' -- not a list of ids."""
    from app.factory.build.deploy import reject_lotdesk_always_200_health

    no_ui = probe_set.code_for("no_ui_surface")
    assert no_ui not in reject_lotdesk_always_200_health()["codes"]
    edited["defects"][no_ui]["class"] = "health"
    # The LotDesk fixture's no-UI finding is now health-class: the gate keeps it.
    assert no_ui in reject_lotdesk_always_200_health()["codes"]


def test_a_new_required_promotion_blocker_is_required(edited):
    """promotion asks for blockers by attribute: a new required one that the
    fixture does not show makes the reject hollow -- and says which."""
    from app.factory.build.promotion import reject_lotdesk_promotion

    edited["defects"]["ZQ9"] = {"family": "F", "promotion_blocker": "required"}
    with pytest.raises(AssertionError, match="ZQ9"):
        reject_lotdesk_promotion()


def test_a_reported_blocker_is_reported_not_required(edited):
    from app.factory.build.promotion import reject_lotdesk_promotion

    edited["defects"]["ZQ9"] = {"family": "F", "promotion_blocker": "reported"}
    result = reject_lotdesk_promotion()
    assert result["zq9_present"] is False


def test_stages_are_found_by_name_and_order_comes_from_data(edited):
    assert probe_set.stage_id("PROMOTION") == edited["stages"][-1]["id"]
    edited["stages"].insert(1, {"id": "ZQ9", "name": "ZQ9_STAGE"})
    assert probe_set.stage_id("ZQ9_STAGE") == "ZQ9"
    assert probe_set.stages_after(edited["stages"][0]["id"])[0] == "ZQ9"


def test_posture_egress_is_an_attribute(edited):
    edited["network_postures"]["ZQ9"] = {"chosen": False, "network": True}
    assert probe_set.posture_egresses("ZQ9") is True
    edited["network_postures"]["ZQ9"]["network"] = False
    assert probe_set.posture_egresses("ZQ9") is False
    assert probe_set.posture_egresses("never-declared") is False


def test_the_probe_set_refuses_ambiguity():
    with pytest.raises(probe_set.ProbeSetError):
        probe_set._validate(
            {"defects": {"A1": {"shapes": ["s"]}, "B2": {"shapes": ["s"]}},
             "stages": [], "network_postures": {"P": {"chosen": True}}}
        )
    with pytest.raises(probe_set.ProbeSetError):
        probe_set._validate(
            {"defects": {}, "stages": [],
             "network_postures": {"A": {"chosen": True}, "B": {"chosen": True}}}
        )
    with pytest.raises(probe_set.ProbeSetError):
        probe_set.code_for("a_shape_nobody_declared")
