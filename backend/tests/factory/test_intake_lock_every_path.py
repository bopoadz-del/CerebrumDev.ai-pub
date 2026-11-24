"""A reopened brief ends on every start path, and a Save never restarts a build.

Change answers on a FAILED build reopens the brief. The reopening lasts only
while that build is still a terminal failure: any start or resume (Try again,
Start from scratch, Save and try again, a pasted build link) closes it, and a
build that finished never counts as reopened, whatever the stored mark says.
A Save on a running or finished build is refused with a typed reason and
starts nothing. Every route that writes the blueprint obeys the same rule.

The Save also stores exactly what the user picked: an answer equal to the
declared one beats a chat suggestion for that field, and an empty country /
currency clears it. A request refused at the tenant guard parks nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.session_store import get_session, update_session
from app.factory import platform_chat_flow
from app.factory.build.ledger import BuildLedger, EventKind
from tests.factory.test_branch_is_platform import _failed_platform
from tests.factory.test_brief_one_write_path import _events, _infos, _open_session, client  # noqa: F401


def _stub_generate(monkeypatch) -> list:
    """generate_product stand-in: records the call, reuses the given workspace."""
    calls: list = []

    def generate(bp, output_dir, **kwargs):
        calls.append((Path(output_dir).name, kwargs.get("start_over"), kwargs.get("intake_changed")))
        return {"engine": "runner", "output_dir": str(output_dir), "product_id": bp.product_id,
                "inputs_hash": "h", "build": None}

    monkeypatch.setattr(platform_chat_flow, "generate_product", generate)
    monkeypatch.setattr(platform_chat_flow, "_live_build_thread", lambda _o: None)
    return calls


def _finish(output_dir) -> None:
    """The run that was started finishes green."""
    ledger = BuildLedger(Path(output_dir) / "build_ledger.jsonl")
    ledger.start_run(product_id="p", inputs_hash="h")
    ledger.append(EventKind.RUN_SUCCEEDED, detail="done")


def _save(client, sid, **answers):  # noqa: F811
    return client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "confirm_intake", **answers},
    )


def _try_again(state):
    return platform_chat_flow.resume_failed_platform(state, triggered_by="continue")


def _start_from_scratch(state):
    return platform_chat_flow.start_over(state)


@pytest.mark.parametrize("start", [_try_again, _start_from_scratch])
def test_reopen_then_start_then_finish_then_save_is_refused_and_starts_nothing(
    tmp_path, monkeypatch, client, start  # noqa: F811
):
    sid = f"sess_lock_{start.__name__}"
    _out, state = _failed_platform(tmp_path, sid)
    calls = _stub_generate(monkeypatch)
    assert platform_chat_flow.continue_with_intake(state)["ok"] is True
    assert platform_chat_flow.intake_reopened(state) is True

    reply = start(state)
    assert reply["ok"] is True
    assert state.product_design.intake_reopened is False  # the start closed it
    update_session(sid, state)
    _finish(state.product_design.generation["output_dir"])
    assert len(calls) == 1

    resp = _save(client, sid, country="zr", currency="zrx")
    assert resp.status_code == 200, resp.text
    infos = _infos(resp.text)
    assert infos and infos[0]["ok"] is False
    assert infos[0]["refused"] == platform_chat_flow.INTAKE_FROZEN
    assert len(calls) == 1  # nothing started
    pd = get_session(sid).product_design
    assert (pd.country, pd.currency) == (None, None)


@pytest.mark.parametrize("answers", [{}, {"country": "zr", "currency": "zrx"}])
def test_a_stale_reopened_mark_on_a_finished_build_opens_nothing(
    tmp_path, monkeypatch, client, answers  # noqa: F811
):
    """Whatever path started the build (a pasted link included) and whether
    or not it cleared the mark: once the build is no longer a terminal
    failure the brief is frozen and a Save starts nothing."""
    sid = "sess_lock_stale_" + ("changed" if answers else "same")
    out, state = _failed_platform(tmp_path, sid)
    calls = _stub_generate(monkeypatch)
    state.product_design.intake_reopened = True
    update_session(sid, state)
    _finish(out)

    assert platform_chat_flow.intake_reopened(get_session(sid)) is False
    resp = _save(client, sid, **answers)
    assert resp.status_code == 200, resp.text
    assert all(i["ok"] is False for i in _infos(resp.text))
    assert calls == []
    pd = get_session(sid).product_design
    assert (pd.country, pd.currency) == (None, None)


def test_confirm_and_continue_refuses_a_build_that_is_not_a_failure(tmp_path, monkeypatch):
    out, state = _failed_platform(tmp_path, "sess_lock_confirm_refused")
    calls = _stub_generate(monkeypatch)
    state.product_design.intake_reopened = True
    state.product_design.intake_proposal = {"country": "ZR", "currency": "ZRX"}
    _finish(out)

    reply = platform_chat_flow.confirm_and_continue(state)

    assert reply["ok"] is False
    assert reply["refused"] == platform_chat_flow.NOT_A_FAILED_BUILD
    assert calls == []
    assert state.product_design.country is None  # nothing stored
    assert state.product_design.intake_reopened is False


@pytest.mark.parametrize("finished", [True, False])
def test_resume_failed_platform_runs_only_on_a_terminal_failure(tmp_path, monkeypatch, finished):
    out, state = _failed_platform(tmp_path, f"sess_lock_resume_{int(finished)}")
    calls = _stub_generate(monkeypatch)
    if finished:
        _finish(out)

    reply = platform_chat_flow.resume_failed_platform(state)

    assert reply["ok"] is (not finished)
    assert len(calls) == (0 if finished else 1)
    if finished:
        assert reply["refused"] == platform_chat_flow.NOT_A_FAILED_BUILD


def test_save_and_try_again_on_a_reopened_failure_still_resumes(tmp_path, monkeypatch, client):  # noqa: F811
    sid = "sess_lock_reopened_resumes"
    _out, state = _failed_platform(tmp_path, sid)
    calls = _stub_generate(monkeypatch)
    monkeypatch.setattr("app.routers.chat.require_remaining", lambda *a, **k: None)
    monkeypatch.setattr("app.routers.chat.require_within_limit", lambda *a, **k: None)
    monkeypatch.setattr("app.routers.session_product._require_build_tenant", lambda *a, **k: "t")
    monkeypatch.setattr("app.routers.chat.assert_entitled", lambda *a, **k: None)
    platform_chat_flow.continue_with_intake(state)
    update_session(sid, state)

    resp = _save(client, sid, country="zr", currency="zrx")

    assert resp.status_code == 200, resp.text
    assert len(calls) == 1 and calls[0][2] is True
    pd = get_session(sid).product_design
    assert (pd.country, pd.currency, pd.intake_reopened) == ("ZR", "ZRX", False)


# -- the Save stores exactly what the user picked -----------------------------


@pytest.mark.parametrize(
    "suggested, picked, stored",
    [
        # The pick equals the declared answer: it beats the chat's suggestion.
        ({"country": "ZR", "currency": "ZRX"}, {"country": "zq", "currency": "zqx"}, ("ZQ", "ZQX")),
        # Clearing both fields stores "not declared", suggestion or not.
        ({"country": "ZR", "currency": "ZRX"}, {"country": "", "currency": ""}, (None, None)),
        ({}, {"country": "", "currency": ""}, (None, None)),
    ],
)
def test_save_stores_the_users_pick_over_a_chat_suggestion(client, suggested, picked, stored):  # noqa: F811
    sid = "sess_lock_pick_" + (picked["country"] or "clear") + str(len(suggested))
    state = _open_session(sid, suggested)
    state.product_design.country, state.product_design.currency = "ZQ", "ZQX"
    update_session(sid, state)

    resp = _save(client, sid, **picked)

    assert resp.status_code == 200, resp.text
    pd = get_session(sid).product_design
    assert (pd.country, pd.currency) == stored
    assert pd.intake_proposal is None


# -- the tenant guard runs before an edit is parked ---------------------------


def test_a_request_refused_at_the_tenant_guard_parks_no_answer(tmp_path, client):  # noqa: F811
    """The failed platform belongs to another tenant than the dev caller: the
    Save that would resume it is refused 403 and the session keeps nothing."""
    sid = "sess_lock_tenant_first"
    _out, state = _failed_platform(tmp_path, sid)
    platform_chat_flow.continue_with_intake(state)
    state.product_design.intake_proposal = None
    update_session(sid, state)

    resp = _save(client, sid, country="zr", currency="zrx")

    assert resp.status_code == 403, resp.text
    assert get_session(sid).product_design.intake_proposal is None


# -- every route that writes the blueprint obeys the same lock ----------------


def test_draft_route_is_refused_while_a_build_runs(tmp_path, monkeypatch, client):  # noqa: F811
    sid = "sess_lock_draft_running"
    _out, state = _failed_platform(tmp_path, sid)
    before = dict(state.product_design.blueprint)
    monkeypatch.setattr(platform_chat_flow, "has_running_build", lambda _s: True)

    resp = client.post(f"/v1/sessions/{sid}/product/draft", json={"brief": "a new ask", "country": "zr"})

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["refused"] == platform_chat_flow.INTAKE_FROZEN
    pd = get_session(sid).product_design
    assert pd.blueprint == before and pd.country is None


@pytest.mark.parametrize(
    "body",
    [{"approve": True, "blueprint": {"product_id": "other"}}, {"approve": False}],
)
def test_approve_route_cannot_replace_or_unapprove_a_frozen_blueprint(tmp_path, client, body):  # noqa: F811
    sid = "sess_lock_approve_" + str(len(body))
    out, state = _failed_platform(tmp_path, sid)
    _finish(out)
    before = dict(state.product_design.blueprint)

    resp = client.post(f"/v1/sessions/{sid}/product/approve", json=body)

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["refused"] == platform_chat_flow.INTAKE_FROZEN
    pd = get_session(sid).product_design
    assert pd.blueprint == before and pd.blueprint_approved is True


def test_accept_golden_route_is_refused_on_a_frozen_blueprint(tmp_path, client):  # noqa: F811
    sid = "sess_lock_accept_golden"
    out, state = _failed_platform(tmp_path, sid)
    _finish(out)
    before = dict(state.product_design.blueprint)

    resp = client.post(f"/v1/sessions/{sid}/product/draft/accept-golden", json={})

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["refused"] == platform_chat_flow.INTAKE_FROZEN
    assert get_session(sid).product_design.blueprint == before


def test_set_vertical_changes_the_draft_and_proposes_the_industry_only(client):  # noqa: F811
    from app.factory.floor_actions import FloorAction
    from tests.factory.test_platform_chat_refinement import _draft_retail

    state = _open_session("sess_lock_set_vertical", {})
    _draft_retail(state)
    declared = state.product_design.vertical

    result = platform_chat_flow.apply_refinement(state, FloorAction.SET_VERTICAL, "kiln_works")

    assert result["blueprint"]["vertical"] == "kiln_works"
    assert state.product_design.vertical == declared  # never written directly
    assert state.product_design.intake_proposal["vertical"] == "kiln_works"
