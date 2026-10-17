"""The legacy configurator's settings are TYPED requests, never chat words.

The chat router used to parse "set lora rank 64", "use domain legal",
"set hnsw to accurate", "list blocks" ... out of the user's message with
regexes. That parser is gone: POST /v1/sessions/{id}/config takes the same
fields as a typed SessionConfig, and GET .../config/optional-blocks lists the
primitives. A chat message containing those words changes nothing.
"""

from __future__ import annotations

import pytest

from app.core.session_store import create_session, get_session
from app.routers import chat as chat_router


def test_the_chat_router_parses_no_configurator_commands():
    assert not hasattr(chat_router, "_parse_command")
    assert not hasattr(chat_router, "_apply_command")


def test_optional_blocks_are_a_typed_request(client):
    session = create_session(session_id="sess_zorblat_blocks", user_id="anonymous")
    res = client.get(f"/v1/sessions/{session.session_id}/config/optional-blocks")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["session_id"] == session.session_id
    assert isinstance(body["optional_blocks"], list)


@pytest.mark.parametrize(
    "message",
    ["set lora rank to 64", "use domain zorblat", "set hnsw to accurate", "list blocks"],
)
def test_configurator_words_in_chat_change_no_setting(client, message, monkeypatch):
    from app.factory import platform_chat_llm

    monkeypatch.setattr(platform_chat_llm, "chat_llm_enabled", lambda: False)
    session = create_session(session_id="sess_zorblat_cfg", user_id="anonymous")
    before = session.config.model_dump()
    res = client.post(f"/v1/sessions/{session.session_id}/chat", json={"message": message})
    assert res.status_code == 200, res.text
    assert "event: command" not in res.text
    assert get_session(session.session_id).config.model_dump() == before


def test_the_mock_chain_is_built_from_available_blocks_not_words():
    from app.core.chain_generator import _mock_response

    blocks = [{"name": "pdf"}, {"name": "ocr"}, {"name": "chat"}]
    plain = _mock_response("zorblat", "zorblat_domain", blocks)
    worded = _mock_response("always scan this image, rule: urgent", "zorblat_domain", blocks)
    assert plain["chain"] == worded["chain"]
    assert plain["rules"] == worded["rules"] == []
