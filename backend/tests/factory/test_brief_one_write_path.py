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


def _infos(text: str) -> list:
    return [data for event, data in _events(text) if event == "info"]


def _open_session(session_id: str, proposal: dict):
    """A session before approval, holding the chat's proposal."""
    create_session(session_id, "tester")
    state = get_session(session_id)
    state.product_design.intake_proposal = dict(proposal)
    update_session(session_id, state)
    return state


def _frozen_session(tmp_path, session_id: str):
    """An approved platform whose build stopped; the intake is not reopened."""
    _out, state = _failed_platform(tmp_path, session_id)
    state.product_design.country, state.product_design.currency = "ZQ", "ZQX"
    update_session(session_id, state)
    return get_session(session_id)


# -- acceptance 1: suggest, change an answer, confirm -------------------------


@pytest.mark.parametrize(
    "changed, stored",
    [
        ({"country": "zr"}, ("ZR", "ZQX", "pilot")),
        ({"currency": "zrx"}, ("ZQ", "ZRX", "pilot")),
    ],
)
def test_a_changed_answer_rides_onto_the_proposal_and_confirm_stores_it(client, changed, stored):
    sid = "sess_pr05_confirm_" + "_".join(changed)
    _open_session(sid, {"country": "ZQ", "currency": "ZQX", "build_level": "pilot"})

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "confirm_intake", **changed},
    )
    assert resp.status_code == 200, resp.text

    pd = get_session(sid).product_design
    assert (pd.country, pd.currency, pd.build_level) == stored
    assert pd.intake_proposal is None
    intake = next(data for event, data in _events(resp.text) if event == "intake")
    assert (intake["declared"]["country"], intake["declared"]["currency"]) == stored[:2]


@pytest.mark.parametrize(
    "proposal, saved",
    [
        # The sheet's Save: all four answers and Confirm in one request.
        ({"country": "ZQ", "currency": "ZQX", "build_level": "pilot"},
         {"vertical": "kiln", "country": "zr", "currency": "zrx", "build_level": "light"}),
        ({}, {"vertical": "", "country": "zq", "currency": "zqx", "build_level": "production "}),
    ],
)
def test_one_save_with_all_four_answers_stores_all_four(client, proposal, saved):
    sid = "sess_pr05_save_four_" + saved["country"]
    _open_session(sid, proposal)

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "confirm_intake", **saved},
    )
    assert resp.status_code == 200, resp.text

    pd = get_session(sid).product_design
    assert (pd.vertical, pd.country, pd.currency, pd.build_level) == (
        saved["vertical"] or None,
        saved["country"].upper(),
        saved["currency"].upper(),
        saved["build_level"].strip(),
    )
    assert pd.intake_proposal is None


@pytest.mark.parametrize("level", ["zorblat", "fastest"])
def test_an_unknown_build_level_field_is_refused_and_keeps_nothing(client, level):
    sid = "sess_pr05_bad_level_" + level
    _open_session(sid, {"build_level": "pilot"})

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "confirm_intake", "country": "zr", "build_level": level},
    )
    infos = _infos(resp.text)
    assert [i["ok"] for i in infos] == [False]
    assert "nothing was changed" in infos[0]["summary"]
    pd = get_session(sid).product_design
    assert (pd.country, pd.build_level) == (None, None)
    assert pd.intake_proposal == {"build_level": "pilot"}


@pytest.mark.parametrize("level", ["light", "pilot"])
def test_a_changed_build_level_field_on_an_approved_session_is_refused(tmp_path, client, level):
    sid = "sess_pr05_frozen_level_" + level
    _frozen_session(tmp_path, sid)

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "take_copy", "build_level": level},
    )
    infos = _infos(resp.text)
    assert [i.get("refused") for i in infos] == [platform_chat_flow.INTAKE_FROZEN]
    pd = get_session(sid).product_design
    assert pd.build_level == "production"
    assert pd.intake_proposal is None


@pytest.mark.parametrize(
    "typed",
    [
        {"action": "list_capabilities"},
        {"action": "set_build_level", "value": "light"},
    ],
)
def test_a_typed_answer_without_confirm_is_only_a_proposal(client, typed):
    sid = "sess_pr05_no_ride_along_" + typed["action"]
    _open_session(sid, {"build_level": "light"})

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", **typed, "country": "zr", "currency": "zrx", "vertical": "zorblat yards"},
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


@pytest.mark.parametrize("field, bad", [("country", "Z1"), ("currency", "ZQ1")])
def test_a_misshaped_answer_is_refused_and_keeps_nothing(client, field, bad):
    sid = f"sess_pr05_misshaped_{field}"
    _open_session(sid, {"country": "ZQ"})

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "confirm_intake", field: bad, "vertical": "kiln"},
    )
    assert resp.status_code == 200, resp.text
    infos = _infos(resp.text)
    assert [i["ok"] for i in infos] == [False]
    assert field in infos[0]["summary"]
    pd = get_session(sid).product_design
    assert pd.country is None  # the Confirm did not run
    assert pd.intake_proposal == {"country": "ZQ"}  # and the vertical was not kept


# -- acceptance 2: the same on a reopened intake after a stopped build --------


@pytest.mark.parametrize(
    "country, currency, stored",
    [("zr", "zrx", ("ZR", "ZRX")), ("zs", None, ("ZS", None))],
)
def test_on_a_reopened_intake_the_changed_answer_is_what_resumes(
    tmp_path, monkeypatch, country, currency, stored
):
    _out, state = _failed_platform(tmp_path, f"sess_pr05_reopened_{country}")
    resumed: list = []
    monkeypatch.setattr(
        platform_chat_flow,
        "resume_failed_platform",
        lambda st, **kw: resumed.append(
            (st.product_design.country, st.product_design.currency, kw.get("intake_changed"))
        ) or {"ok": True, "summary": "resumed"},
    )

    assert platform_chat_flow.continue_with_intake(state)["ok"] is True
    edited = platform_chat_flow.edit_intake(state, country=country, currency=currency)
    assert edited["ok"] is True
    assert (state.product_design.country, state.product_design.currency) == (None, None)

    platform_chat_flow.confirm_and_continue(state)
    assert resumed == [(*stored, True)]
    assert state.product_design.intake_reopened is False


# -- acceptance 3: approved, not reopened: refused, nothing changes -----------


@pytest.mark.parametrize(
    "changed",
    [{"country": "zr", "currency": "zqx"}, {"vertical": "kiln"}],
)
def test_a_changed_answer_on_an_approved_session_is_refused(tmp_path, client, changed):
    sid = "sess_pr05_frozen_" + "_".join(changed)
    _frozen_session(tmp_path, sid)
    before = copy.deepcopy(get_session(sid).product_design.blueprint)

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "take_copy", **changed},
    )
    assert resp.status_code == 200, resp.text

    infos = _infos(resp.text)
    assert [i.get("refused") for i in infos] == [platform_chat_flow.INTAKE_FROZEN]
    assert not any("download" in i for i in infos)  # the action did not run
    pd = get_session(sid).product_design
    assert (pd.vertical, pd.country, pd.currency) == (None, "ZQ", "ZQX")
    assert pd.intake_proposal is None
    assert pd.blueprint == before


@pytest.mark.parametrize(
    "same",
    [{"country": "zq", "currency": "zqx"}, {"vertical": ""}],
)
def test_an_unchanged_answer_on_an_approved_session_lets_the_action_run(tmp_path, client, same):
    sid = "sess_pr05_frozen_same_" + "_".join(same)
    _frozen_session(tmp_path, sid)

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "take_copy", **same},
    )
    assert [("download" in i) for i in _infos(resp.text)] == [True]
    assert get_session(sid).product_design.intake_proposal is None


@pytest.mark.parametrize(
    "running, approved, reopened, terminal_failure, frozen",
    [
        (True, False, True, True, True),
        (False, False, True, True, False),
        (False, True, False, True, True),
        (False, True, True, True, False),
        # The reopened mark on a build that is no longer a terminal failure
        # (it resumed, or finished) opens nothing.
        (False, True, True, False, True),
    ],
)
def test_intake_frozen_reads_running_approved_and_reopened(
    monkeypatch, running, approved, reopened, terminal_failure, frozen
):
    pd = SimpleNamespace(blueprint_approved=approved, intake_reopened=reopened)
    monkeypatch.setattr(platform_chat_flow, "has_running_build", lambda _s: running)
    monkeypatch.setattr(
        platform_chat_flow, "is_generation_terminal_failure", lambda _s, _o=None: terminal_failure
    )
    assert platform_chat_flow.intake_frozen(SimpleNamespace(product_design=pd)) == frozen


@pytest.mark.parametrize("action", [FloorAction.RENAME, FloorAction.APPROVE])
def test_apply_intake_action_refuses_an_action_that_is_not_intake(monkeypatch, action):
    pd = SimpleNamespace(blueprint_approved=False, intake_reopened=False, intake_proposal=None)
    monkeypatch.setattr(platform_chat_flow, "has_running_build", lambda _s: False)
    with pytest.raises(FloorActionError):
        platform_chat_flow.apply_intake_action(SimpleNamespace(product_design=pd), action, "x")


# -- the edit helpers ---------------------------------------------------------


@pytest.mark.parametrize(
    "vertical, country, currency, edit",
    [
        (None, None, None, {}),
        # An empty country / currency SENT is the user's "clear it" (it was
        # "not changed" before; the sheet could not clear a field). Not sent
        # (None) is still "not changed".
        (None, "", "  ", {"country": "", "currency": ""}),
        ("", None, None, {"vertical": NO_VERTICAL}),  # the explicit "no industry"
        ("Kiln Works", "zr", "zrx", {"vertical": "kiln_works", "country": "ZR", "currency": "ZRX"}),
    ],
)
def test_shaped_intake_edit_keeps_what_fits(vertical, country, currency, edit):
    assert shaped_intake_edit(vertical, country, currency) == (edit, None)


@pytest.mark.parametrize(
    "level, edit",
    [(None, {}), ("  ", {}), (" light", {"build_level": "light"}), ("pilot", {"build_level": "pilot"})],
)
def test_shaped_intake_edit_shapes_the_build_level(level, edit):
    assert shaped_intake_edit(None, None, None, level) == (edit, None)


@pytest.mark.parametrize("level", ["zorblat", "fast"])
def test_shaped_intake_edit_refuses_an_unknown_build_level(level):
    edit, error = shaped_intake_edit("kiln", "zr", "zrx", level)
    assert edit == {}
    assert error.endswith("nothing was changed.")


@pytest.mark.parametrize(
    "country, currency, named",
    [("zr", "zr1", "currency"), ("zrq", None, "country")],
)
def test_shaped_intake_edit_refuses_what_does_not_fit_and_keeps_nothing(country, currency, named):
    edit, error = shaped_intake_edit("kiln", country, currency)
    assert edit == {}
    assert named in error


@pytest.mark.parametrize(
    "edit, differs",
    [
        ({"vertical": NO_VERTICAL}, False),
        ({"vertical": "kiln"}, True),
        ({"country": "ZQ"}, False),
        ({"country": "ZR"}, True),
    ],
)
def test_edit_differs_from_declared_reads_no_industry_as_unchanged(edit, differs):
    pd = SimpleNamespace(vertical=None, country="ZQ", currency="ZQX")
    assert edit_differs_from_declared(pd, edit) is differs


@pytest.mark.parametrize(
    "before, edit, after",
    [
        (None, {}, None),
        ({"build_level": "pilot", "country": "ZQ"}, {"country": "ZR"},
         {"build_level": "pilot", "country": "ZR"}),
    ],
)
def test_propose_intake_edit_merges_onto_the_proposal_and_never_declares(before, edit, after):
    pd = SimpleNamespace(country=None, intake_proposal=before)
    propose_intake_edit(pd, edit)
    assert pd.intake_proposal == after
    assert pd.country is None


@pytest.mark.parametrize("fields", [{}, {"vertical": ""}])
def test_edit_intake_returns_none_when_nothing_changes(fields):
    pd = SimpleNamespace(vertical=None, country=None, currency=None, intake_proposal=None,
                         blueprint_approved=True, intake_reopened=False, build_level=None)
    assert platform_chat_flow.edit_intake(SimpleNamespace(product_design=pd), **fields) is None
    assert pd.intake_proposal is None


@pytest.mark.parametrize("action", ["zorblat", "approve_everything"])
def test_an_action_that_is_not_a_floor_action_is_refused_422_and_starts_nothing(client, action):
    sid = "sess_pr05_bad_action_" + action
    _open_session(sid, {"build_level": "pilot"})

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": action},
    )
    assert resp.status_code == 422, resp.text
    assert action in resp.json()["detail"]
    pd = get_session(sid).product_design
    assert (pd.build_level, pd.blueprint) == (None, None)
    assert pd.intake_proposal == {"build_level": "pilot"}


# -- acceptance 4: the action spec, and one sentence per level ----------------


@pytest.mark.parametrize("level", list(BuildLevel))
def test_the_spec_carries_each_level_its_sentence_from_its_bar(level):
    shape = fa.action_spec()["actions"][FloorAction.SET_BUILD_LEVEL.value]["value"]
    assert list(shape["sentences"]) == shape["one_of"]
    assert shape["sentences"][level.value] == level_sentence(BARS[level])
    others = [s for lv, s in shape["sentences"].items() if lv != level.value]
    assert shape["sentences"][level.value] not in others  # each level reads differently


@pytest.mark.parametrize(
    "variant",
    [
        LevelBar(BuildLevel.LIGHT, "CODE", False, False),
        LevelBar(BuildLevel.LIGHT, "STORE", True, False),
        LevelBar(BuildLevel.LIGHT, "STORE", False, True),
    ],
)
def test_level_sentence_follows_each_flag_of_the_bar(variant):
    base = LevelBar(BuildLevel.LIGHT, "STORE", False, False)
    assert level_sentence(variant) != level_sentence(base)
    assert BuildLevel.LIGHT.value not in level_sentence(variant)  # from the bar, not the name
