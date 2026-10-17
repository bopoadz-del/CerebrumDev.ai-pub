"""The session list is something a person can find their work in.

Owner: "new session deletes the ongoing one, the sessions should be saved on
the left panel, not disappear". Nothing was deleted -- every session is kept
server-side -- but the list endpoint returned bare ids and the rail never
rendered them, so a session left behind was unreachable.
"""

from __future__ import annotations

from app.models.session import ProductDesignState, SessionState
from app.routers.sessions import session_card


def _state(**kw) -> SessionState:
    s = SessionState(session_id="sess-card", user_id="u1", account_id="a1")
    s.product_design = ProductDesignState(**kw)
    return s


def test_a_drafted_session_is_titled_by_its_product():
    s = _state(blueprint={"product_name": "Bakery Chain Operations & Delivery Platform"})
    s.chat_history = [{"role": "user", "content": "i have a chain of bakeries"}]

    card = session_card(s)

    assert card["title"].startswith("Bakery Chain Operations")
    assert card["stage"] == "blueprint"


def test_before_a_draft_the_title_is_what_the_user_said_not_a_greeting():
    s = _state()
    s.chat_history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hi! Tell me about the platform."},
        {"role": "user", "content": "i have chain of bakeries and i do deliveries"},
    ]

    card = session_card(s)

    assert card["title"] == "i have chain of bakeries and i do deliveries"
    assert card["stage"] == "talking"


def test_a_long_title_is_cut_with_an_ellipsis():
    s = _state(blueprint={"product_name": "x" * 200})

    title = session_card(s)["title"]

    assert len(title) <= 48 and title.endswith("\u2026")


def test_stages_follow_the_session():
    assert session_card(_state())["stage"] == "empty"
    built = _state(blueprint={"product_name": "P"}, generation={"product_id": "p"})
    assert session_card(built)["stage"] == "build"


def test_the_card_carries_a_timestamp():
    assert session_card(_state())["updated_at"]
