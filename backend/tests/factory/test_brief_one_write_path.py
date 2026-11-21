"""PR-05: one write path for the brief.

An answer the user changes on the Floor (industry, country, currency, build
level) changes the pending proposal; only the typed Confirm stores it. Typed
fields riding beside an action are never stored ahead of it. After approval
the answers are frozen unless "Continue with new answers" reopened the
intake: a changed answer is refused, the action does not run, and the
approved blueprint is unchanged. The shared action spec carries one plain
sentence per build level, put together from the level's own bar.
"""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest

from app.core.session_store import create_session, get_session, update_session
from app.factory import floor_actions as fa
from app.factory import platform_chat_flow
from app.factory.build.build_level import BARS, BuildLevel, LevelBar
from app.factory.floor_actions import FloorAction, FloorActionError, level_sentence
from app.factory.locale_choice import (
    edit_differs_from_declared,
    propose_intake_edit,
    shaped_intake_edit,
)
from app.factory.store_kits import NO_VERTICAL
from tests.factory.test_branch_is_platform import _failed_platform


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setenv("ENV", "test")
    monkeypatch.delenv("CEREBRUM_DEV_API_KEY", raising=False)
    monkeypatch.setenv("ALLOW_ANONYMOUS_DEV", "1")
    return TestClient(app)


def _events(text: str) -> list:
    out, event = [], ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = json.loads(line.split(":", 1)[1].strip())
            out.append((event, json.loads(data) if event in {"intake", "info"} else data))
    return out


def _intake_event(text: str) -> dict:
    return next(data for event, data in _events(text) if event == "intake")


def _open_session(session_id: str, proposal: dict):
    """A session before approval, holding the chat's proposal."""
    create_session(session_id, "tester")
    state = get_session(session_id)
    state.product_design.intake_proposal = dict(proposal)
    update_session(session_id, state)
    return state


# -- acceptance 1: suggest, change the country, confirm ----------------------


def test_a_changed_country_rides_onto_the_proposal_and_confirm_stores_it(client):
    sid = "sess_pr05_change_then_confirm"
    _open_session(sid, {"country": "ZQ", "currency": "ZQX", "build_level": "pilot"})

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "confirm_intake", "country": "zr"},
    )
    assert resp.status_code == 200, resp.text

    pd = get_session(sid).product_design
    assert (pd.country, pd.currency, pd.build_level) == ("ZR", "ZQX", "pilot")
    assert pd.intake_proposal is None
    assert _intake_event(resp.text)["declared"]["country"] == "ZR"


def test_a_typed_answer_without_confirm_is_only_a_proposal(client):
    sid = "sess_pr05_no_ride_along"
    _open_session(sid, {"build_level": "light"})

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "list_capabilities", "country": "zr", "currency": "zrx",
              "vertical": "zorblat yards"},
    )
    assert resp.status_code == 200, resp.text

    pd = get_session(sid).product_design
    assert (pd.vertical, pd.country, pd.currency, pd.build_level) == (None, None, None, None)
    assert pd.intake_proposal == {
        "build_level": "light",
        "vertical": "zorblat_yards",
        "country": "ZR",
        "currency": "ZRX",
    }


def test_a_misshaped_answer_is_refused_and_keeps_nothing(client):
    sid = "sess_pr05_misshaped"
    _open_session(sid, {"country": "ZQ"})

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "confirm_intake", "country": "Z1", "vertical": "kiln"},
    )
    assert resp.status_code == 200, resp.text
    info = next(d for e, d in _events(resp.text) if e == "info")
    assert info["ok"] is False
    pd = get_session(sid).product_design
    assert pd.country is None  # the Confirm did not run
    assert pd.intake_proposal == {"country": "ZQ"}  # and the vertical was not kept


# -- acceptance 2: the same on a reopened intake after a stopped build --------


def test_on_a_reopened_intake_the_changed_country_is_what_resumes(tmp_path, monkeypatch):
    _out, state = _failed_platform(tmp_path, "sess_pr05_reopened")
    resumed: list = []
    monkeypatch.setattr(
        platform_chat_flow,
        "resume_failed_platform",
        lambda st, **kw: resumed.append(
            (st.product_design.country, st.product_design.currency, kw.get("intake_changed"))
        ) or {"ok": True, "summary": "resumed"},
    )

    assert platform_chat_flow.continue_with_intake(state)["ok"] is True
    edited = platform_chat_flow.edit_intake(state, country="zr", currency="zrx")
    assert edited["ok"] is True
    assert (state.product_design.country, state.product_design.currency) == (None, None)

    platform_chat_flow.confirm_and_continue(state)
    assert resumed == [("ZR", "ZRX", True)]
    assert state.product_design.intake_reopened is False


# -- acceptance 3: approved, not reopened: refused, nothing changes -----------


def test_a_changed_country_on_an_approved_session_is_refused(tmp_path, client):
    sid = "sess_pr05_frozen"
    _out, state = _failed_platform(tmp_path, sid)
    state.product_design.country, state.product_design.currency = "ZQ", "ZQX"
    update_session(sid, state)
    before = copy.deepcopy(get_session(sid).product_design.blueprint)

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "take_copy", "country": "zr", "currency": "zqx"},
    )
    assert resp.status_code == 200, resp.text

    infos = [d for e, d in _events(resp.text) if e == "info"]
    assert [i.get("refused") for i in infos] == [platform_chat_flow.INTAKE_FROZEN]
    assert not any("download" in i for i in infos)  # the action did not run
    pd = get_session(sid).product_design
    assert (pd.country, pd.currency) == ("ZQ", "ZQX")
    assert pd.intake_proposal is None
    assert pd.blueprint == before


def test_an_unchanged_answer_on_an_approved_session_lets_the_action_run(tmp_path, client):
    sid = "sess_pr05_frozen_same"
    _out, state = _failed_platform(tmp_path, sid)
    state.product_design.country, state.product_design.currency = "ZQ", "ZQX"
    update_session(sid, state)

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "take_copy", "country": "zq", "currency": "zqx"},
    )
    infos = [d for e, d in _events(resp.text) if e == "info"]
    assert any("download" in i for i in infos)
    assert get_session(sid).product_design.intake_proposal is None


def test_a_running_build_freezes_the_intake_even_before_approval(monkeypatch):
    pd = SimpleNamespace(blueprint_approved=False, intake_reopened=True)
    state = SimpleNamespace(product_design=pd)
    monkeypatch.setattr(platform_chat_flow, "has_running_build", lambda _s: True)
    assert platform_chat_flow.intake_frozen(state) is True
    monkeypatch.setattr(platform_chat_flow, "has_running_build", lambda _s: False)
    assert platform_chat_flow.intake_frozen(state) is False
    pd.blueprint_approved, pd.intake_reopened = True, False
    assert platform_chat_flow.intake_frozen(state) is True


def test_apply_intake_action_refuses_an_action_that_is_not_intake(monkeypatch):
    pd = SimpleNamespace(blueprint_approved=False, intake_reopened=False, intake_proposal=None)
    state = SimpleNamespace(product_design=pd)
    monkeypatch.setattr(platform_chat_flow, "has_running_build", lambda _s: False)
    with pytest.raises(FloorActionError):
        platform_chat_flow.apply_intake_action(state, FloorAction.RENAME, "x")


# -- the edit helpers, two inputs each ----------------------------------------


def test_shaped_intake_edit_keeps_what_fits_and_refuses_what_does_not():
    assert shaped_intake_edit(None, None, None) == ({}, None)
    assert shaped_intake_edit(None, "", "  ") == ({}, None)  # empty is "not changed"
    assert shaped_intake_edit("", None, None) == ({"vertical": NO_VERTICAL}, None)
    assert shaped_intake_edit("Kiln Works", "zr", "zrx") == (
        {"vertical": "kiln_works", "country": "ZR", "currency": "ZRX"},
        None,
    )
    edit, error = shaped_intake_edit("kiln", "zr", "zr1")
    assert edit == {} and "currency" in error
    edit, error = shaped_intake_edit(None, "zrq", None)
    assert edit == {} and "country" in error


def test_edit_differs_from_declared_reads_no_industry_as_unchanged():
    pd = SimpleNamespace(vertical=None, country="ZQ", currency="ZQX")
    assert edit_differs_from_declared(pd, {"vertical": NO_VERTICAL}) is False
    assert edit_differs_from_declared(pd, {"vertical": "kiln"}) is True
    assert edit_differs_from_declared(pd, {"country": "ZQ"}) is False
    assert edit_differs_from_declared(pd, {"country": "ZR"}) is True


def test_propose_intake_edit_merges_onto_the_proposal_and_never_declares():
    pd = SimpleNamespace(country=None, intake_proposal=None)
    propose_intake_edit(pd, {})
    assert pd.intake_proposal is None
    pd.intake_proposal = {"build_level": "pilot", "country": "ZQ"}
    propose_intake_edit(pd, {"country": "ZR"})
    assert pd.intake_proposal == {"build_level": "pilot", "country": "ZR"}
    assert pd.country is None


def test_edit_intake_returns_none_when_nothing_changes():
    pd = SimpleNamespace(vertical=None, country=None, currency=None, intake_proposal=None,
                         blueprint_approved=True, intake_reopened=False, build_level=None)
    state = SimpleNamespace(product_design=pd)
    assert platform_chat_flow.edit_intake(state) is None
    assert platform_chat_flow.edit_intake(state, vertical="") is None


# -- acceptance 4: the action spec, and one sentence per level ----------------


def test_the_spec_carries_one_sentence_per_level_from_its_bar():
    shape = fa.action_spec()["actions"][FloorAction.SET_BUILD_LEVEL.value]["value"]
    assert list(shape["sentences"]) == shape["one_of"] == [lv.value for lv in BuildLevel]
    sentences = [shape["sentences"][lv.value] for lv in BuildLevel]
    assert all(s.strip() for s in sentences)
    assert len(set(sentences)) == len(sentences)  # each level reads differently
    for lv in BuildLevel:
        assert shape["sentences"][lv.value] == level_sentence(BARS[lv])


def test_level_sentence_follows_each_flag_of_the_bar():
    base = LevelBar(BuildLevel.LIGHT, "STORE", False, False)
    variants = {
        level_sentence(base),
        level_sentence(LevelBar(BuildLevel.LIGHT, "CODE", False, False)),
        level_sentence(LevelBar(BuildLevel.LIGHT, "STORE", True, False)),
        level_sentence(LevelBar(BuildLevel.LIGHT, "STORE", False, True)),
    }
    assert len(variants) == 4  # every flag changes the sentence
    assert BuildLevel.LIGHT.value not in level_sentence(base)  # built from the bar, not the name
