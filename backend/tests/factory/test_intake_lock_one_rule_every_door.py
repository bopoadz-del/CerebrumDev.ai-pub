"""The intake lock, one rule at every door (follow-up to the lock on every path).

1. The typed industry goes through the one write path: it is proposed, and
   the declared field and the blueprint take it together on Confirm. Approve
   never builds on an answer the brief still shows as unsaved, and a later
   Save never switches the blueprint back.
2. The chat's Draft obeys the same frozen rule as POST /product/draft: a
   draft is refused on a frozen intake, except over a terminal failure,
   where a new brief replaces the dead build at both doors.
3. Every refusal on a frozen intake carries the typed INTAKE_FROZEN, a Save
   whose answers equal the declared ones included.
"""

from __future__ import annotations

import pytest

from app.core.session_store import get_session, update_session
from app.factory import platform_chat_flow
from app.factory.floor_actions import FloorAction
from app.models.session import ProductDesignState, SessionState
from tests.factory.test_branch_is_platform import _failed_platform
from tests.factory.test_brief_one_write_path import _frozen_session, _infos, client  # noqa: F401
from tests.factory.test_intake_lock_every_path import _finish


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    from app.core.rate_limit import reset_rate_limits

    reset_rate_limits()
    yield
    reset_rate_limits()


def _state(blueprint, *, vertical, approved):
    s = SessionState(session_id="sess-one-rule", user_id="user-1", account_id="acct-1")
    s.product_design = ProductDesignState(
        blueprint=blueprint, vertical=vertical, blueprint_approved=approved
    )
    return s


def _draft(vertical: str) -> dict:
    from app.factory.product_architect import draft_blueprint_from_brief

    bp = draft_blueprint_from_brief("a platform for my business", vertical_hint=vertical)
    return bp.model_dump(mode="json")


# -- 1. the industry: proposed, then taken on Confirm only --------------------


@pytest.mark.parametrize("approved", [False, True])
def test_confirmed_industry_reaches_the_blueprint_and_only_a_draft_renames(approved):
    blueprint = _draft("kiln_works")
    product_id = blueprint["product_id"]
    state = _state(blueprint, vertical="quarry_yards", approved=approved)
    domain = state.config.domain

    platform_chat_flow.blueprint_takes_declared_vertical(state)

    bp = state.product_design.blueprint
    assert bp["vertical"] == "quarry_yards"
    if approved:  # a reopened build keeps its own branch
        assert bp["product_id"] == product_id
        assert state.config.domain == domain
    else:
        assert bp["product_id"] == "quarry_yards"
        assert state.config.domain != domain


@pytest.mark.parametrize("approved", [False, True])
def test_an_unchanged_industry_leaves_the_blueprint_alone(approved):
    blueprint = {**_draft("kiln_works"), "product_id": "own_id"}
    state = _state(blueprint, vertical="kiln_works", approved=approved)
    domain = state.config.domain

    platform_chat_flow.blueprint_takes_declared_vertical(state)

    assert state.product_design.blueprint["product_id"] == "own_id"
    assert state.config.domain == domain


@pytest.mark.parametrize("blueprint", [None, "draft"])
def test_no_declared_industry_or_no_blueprint_changes_nothing(blueprint):
    raw = _draft("kiln_works") if blueprint else None
    state = _state(raw and dict(raw), vertical=None, approved=False)

    platform_chat_flow.blueprint_takes_declared_vertical(state)

    assert state.product_design.blueprint == raw


@pytest.mark.parametrize("vertical,confirmed", [
    ("quarry_yards", None),
    (None, {"vertical": None}),
])
def test_a_declared_or_cleared_industry_with_no_blueprint_changes_nothing(vertical, confirmed):
    """An industry Confirm declared or cleared has no blueprint to reach yet:
    the session is left as it is and nothing raises."""
    state = _state(None, vertical=vertical, approved=False)
    domain = state.config.domain

    platform_chat_flow.blueprint_takes_declared_vertical(state, confirmed)

    assert state.product_design.blueprint is None
    assert state.config.domain == domain


def _record_builds(monkeypatch) -> list:
    """generate_product stand-in: records the blueprint each build runs on."""
    built: list = []

    def generate(bp, output_dir, **kwargs):
        built.append((bp.vertical, bp.product_id))
        return {"engine": "runner", "output_dir": str(output_dir), "product_id": bp.product_id,
                "inputs_hash": "h", "build": None}

    monkeypatch.setattr(platform_chat_flow, "generate_product", generate)
    monkeypatch.setattr(platform_chat_flow, "_live_build_thread", lambda _o: None)
    return built


@pytest.mark.parametrize("industry", ["quarry_yards", "salt_pans"])
def test_typed_industry_then_approve_builds_on_the_declared_answer(tmp_path, monkeypatch, industry):
    """The reviewer's path: declared Y, typed X, Approve with X unsaved. The
    build runs on Y; only Save moves the declared answer and the blueprint
    together, and a build approved after the Save runs on X."""
    built = _record_builds(monkeypatch)

    unsaved = _state(_draft("kiln_works"), vertical="kiln_works", approved=False)
    platform_chat_flow.apply_refinement(unsaved, FloorAction.SET_VERTICAL, industry)
    pd = unsaved.product_design
    assert (pd.vertical, pd.blueprint["vertical"]) == ("kiln_works", "kiln_works")
    platform_chat_flow.approve_and_generate(unsaved, output_root=tmp_path / "unsaved")
    assert built[-1][0] == "kiln_works"

    saved = _state(_draft("kiln_works"), vertical="kiln_works", approved=False)
    platform_chat_flow.apply_refinement(saved, FloorAction.SET_VERTICAL, industry)
    platform_chat_flow.apply_intake_action(saved, FloorAction.CONFIRM_INTAKE)
    pd = saved.product_design
    assert (pd.vertical, pd.blueprint["vertical"]) == (industry, industry)
    platform_chat_flow.approve_and_generate(saved, output_root=tmp_path / "saved")
    assert built[-1][0] == industry
    assert len(built) == 2


@pytest.mark.parametrize("cleared", ["product", "Product"])
def test_clearing_the_industry_on_a_draft_takes_it_off_the_blueprint(cleared):
    """Confirm clears the declared industry: the blueprint takes the value a
    draft without an industry carries, so Approve never builds on the old one."""
    from app.factory.store_kits import NO_VERTICAL

    state = _state(_draft("kiln_works"), vertical="kiln_works", approved=False)
    state.product_design.intake_proposal = {"vertical": cleared}

    reply = platform_chat_flow.apply_intake_action(state, FloorAction.CONFIRM_INTAKE)

    assert reply["ok"] is True
    pd = state.product_design
    assert pd.vertical is None
    assert (pd.blueprint["vertical"], pd.blueprint["product_id"]) == (NO_VERTICAL, NO_VERTICAL)


@pytest.mark.parametrize("industry", ["quarry_yards", "salt_pans"])
def test_confirm_on_a_reopened_failed_build_resumes_on_the_declared_industry(
    tmp_path, monkeypatch, industry
):
    """The second Confirm door (Change answers on a dead build): the changed
    industry reaches the blueprint the resumed build runs on, and the build
    keeps its own product id so it stays on its branch of record."""
    _out, state = _failed_platform(tmp_path, "sess_one_rule_reopen_" + industry)
    built = _record_builds(monkeypatch)
    product_id = state.product_design.blueprint["product_id"]
    assert platform_chat_flow.continue_with_intake(state)["ok"] is True
    state.product_design.intake_proposal = {"vertical": industry}

    reply = platform_chat_flow.confirm_and_continue(state)

    assert reply["ok"] is True, reply
    bp = state.product_design.blueprint
    assert (bp["vertical"], bp["product_id"]) == (industry, product_id)
    assert built == [(industry, product_id)]


@pytest.mark.parametrize(("industry", "cleared"), [("kiln_works", ""), ("salt_pans", "product")])
def test_clearing_the_industry_on_a_reopened_failed_build_resumes_without_it(
    tmp_path, monkeypatch, industry, cleared
):
    """The second Confirm door clears too: a dead build on an industry,
    Change answers, Confirm with the industry cleared. The resumed build runs
    on the no-industry value, never on the old industry, and keeps its own
    product id."""
    from app.factory.store_kits import NO_VERTICAL

    _out, state = _failed_platform(tmp_path, "sess_one_rule_clear_" + industry)
    pd = state.product_design
    pd.vertical = industry
    pd.blueprint["vertical"] = industry
    built = _record_builds(monkeypatch)
    product_id = pd.blueprint["product_id"]
    assert platform_chat_flow.continue_with_intake(state)["ok"] is True
    # The answer goes through the one write path the chat request uses.
    platform_chat_flow.edit_intake(state, vertical=cleared)

    reply = platform_chat_flow.confirm_and_continue(state)

    assert reply["ok"] is True, reply
    assert pd.vertical is None
    bp = pd.blueprint
    assert (bp["vertical"], bp["product_id"]) == (NO_VERTICAL, product_id)
    assert built == [(NO_VERTICAL, product_id)]


@pytest.mark.parametrize("answer",[{"country": "zr"}, {"currency": "zrx"}])
def test_confirming_another_answer_on_a_draft_keeps_its_identity(answer):
    blueprint = {**_draft("kiln_works"), "product_id": "own_id"}
    state = _state(blueprint, vertical="kiln_works", approved=False)
    state.product_design.intake_proposal = dict(answer)

    reply = platform_chat_flow.apply_intake_action(state, FloorAction.CONFIRM_INTAKE)

    assert reply["ok"] is True
    bp = state.product_design.blueprint
    assert (bp["vertical"], bp["product_id"]) == ("kiln_works", "own_id")


# -- 2. Draft: the chat door and the route refuse the same request ------------


@pytest.mark.parametrize("brief", ["a new ask", "another platform entirely"])
def test_chat_draft_on_a_frozen_intake_is_refused_like_the_route(tmp_path, client, brief):  # noqa: F811
    sid = "sess_one_rule_draft_" + str(len(brief))
    out, state = _failed_platform(tmp_path, sid)
    _finish(out)
    before = dict(state.product_design.blueprint)

    chat = client.post(f"/v1/sessions/{sid}/chat", json={"message": brief, "action": "draft"})
    route = client.post(f"/v1/sessions/{sid}/product/draft", json={"brief": brief})

    assert chat.status_code == 200, chat.text
    infos = _infos(chat.text)
    assert infos and infos[0]["ok"] is False
    assert infos[0]["refused"] == platform_chat_flow.INTAKE_FROZEN
    assert route.status_code == 409, route.text
    assert route.json()["detail"]["refused"] == platform_chat_flow.INTAKE_FROZEN
    pd = get_session(sid).product_design
    # The door syncs the declared locale onto the blueprint; nothing is drafted.
    assert {k: v for k, v in pd.blueprint.items() if k != "locale"} == {
        k: v for k, v in before.items() if k != "locale"
    }
    assert pd.blueprint_approved is True


@pytest.mark.parametrize("door", ["chat", "route"])
def test_a_new_brief_over_a_dead_build_drafts_at_both_doors(tmp_path, client, door):  # noqa: F811
    """A terminal failure is the one approved state a new brief may replace:
    both doors draft it, and the draft starts nothing."""
    sid = "sess_one_rule_dead_" + door
    state = _frozen_session(tmp_path, sid)
    assert platform_chat_flow.intake_frozen(state) is True

    if door == "chat":
        resp = client.post(
            f"/v1/sessions/{sid}/chat", json={"message": "a kiln rental desk", "action": "draft"}
        )
        assert resp.status_code == 200, resp.text
        assert all(i.get("refused") != platform_chat_flow.INTAKE_FROZEN for i in _infos(resp.text))
    else:
        resp = client.post(f"/v1/sessions/{sid}/product/draft", json={"brief": "a kiln rental desk"})
        assert resp.status_code == 200, resp.text

    pd = get_session(sid).product_design
    assert pd.blueprint_approved is False
    assert platform_chat_flow.has_running_build(get_session(sid)) is False


# -- 3. every frozen refusal is typed ----------------------------------------


@pytest.mark.parametrize("answers", [{"country": "ZQ", "currency": "ZQX"}, {}])
def test_a_save_equal_to_the_declared_answers_is_refused_typed(tmp_path, client, answers):  # noqa: F811
    sid = "sess_one_rule_same_" + str(len(answers))
    _frozen_session(tmp_path, sid)

    resp = client.post(
        f"/v1/sessions/{sid}/chat",
        json={"message": "", "action": "confirm_intake", **answers},
    )

    assert resp.status_code == 200, resp.text
    infos = _infos(resp.text)
    assert infos and infos[0]["ok"] is False
    assert infos[0]["refused"] == platform_chat_flow.INTAKE_FROZEN
    pd = get_session(sid).product_design
    assert (pd.country, pd.currency) == ("ZQ", "ZQX")


@pytest.mark.parametrize(
    "action, value", [(FloorAction.CONFIRM_INTAKE, None), (FloorAction.SET_BUILD_LEVEL, "pilot")]
)
def test_every_intake_action_on_a_frozen_intake_is_refused_typed(tmp_path, action, value):
    state = _frozen_session(tmp_path, "sess_one_rule_action_" + action.value)
    state.product_design.intake_proposal = {"country": "ZR"}
    update_session(state.session_id, state)

    reply = platform_chat_flow.apply_intake_action(state, action, value)

    assert reply["ok"] is False
    assert reply["refused"] == platform_chat_flow.INTAKE_FROZEN
    assert state.product_design.country == "ZQ"


# -- 4. the chat model's Draft obeys the same rule ---------------------------


@pytest.mark.parametrize("finished", [True, False])
def test_the_chat_models_draft_obeys_the_frozen_rule(tmp_path, monkeypatch, client, finished):  # noqa: F811
    """Free text the chat model turns into a Draft is the third door that
    replaces the blueprint: refused on a finished build, allowed over a
    terminal failure, like the typed Draft and POST /product/draft."""
    from app.factory import platform_chat_llm

    sid = f"sess_one_rule_model_draft_{int(finished)}"
    out, state = _failed_platform(tmp_path, sid)
    if finished:
        _finish(out)
    before = dict(state.product_design.blueprint)
    decision = {
        "action": "draft_platform",
        "brief": "a kiln rental desk",
        "message": "",
        "refine": {"op": "", "value": ""},
        "connectors": [],
        "missing_connectors": [],
    }
    monkeypatch.setattr(platform_chat_llm, "should_orchestrate", lambda *a, **k: True)
    monkeypatch.setattr(platform_chat_llm, "try_decide_with_reason", lambda *a, **k: (dict(decision), None))
    drafted: list = []
    real_draft = platform_chat_flow.draft_from_chat

    def draft(st, brief):
        drafted.append(brief)
        return real_draft(st, brief)

    monkeypatch.setattr(platform_chat_flow, "draft_from_chat", draft)

    resp = client.post(f"/v1/sessions/{sid}/chat", json={"message": "make it a kiln rental desk"})

    assert resp.status_code == 200, resp.text
    pd = get_session(sid).product_design
    refusals = [i for i in _infos(resp.text) if i.get("refused") == platform_chat_flow.INTAKE_FROZEN]
    if finished:
        assert refusals and drafted == []
        assert pd.blueprint_approved is True
        assert {k: v for k, v in pd.blueprint.items() if k != "locale"} == {
            k: v for k, v in before.items() if k != "locale"
        }
    else:
        assert refusals == [] and len(drafted) == 1
        assert pd.blueprint_approved is False
