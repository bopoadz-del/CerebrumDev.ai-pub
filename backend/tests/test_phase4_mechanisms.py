"""Phase 4 mechanisms on the factory side: scope refusal + layer precedence."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from unittest.mock import AsyncMock, patch

import pytest

from app.core.grounding import check_scope_refusal, verdict_log_path


def _estate_kit():
    """The estate kit lives in the Store (the Factory holds no kits)."""
    from app.factory.kit_pack import store_kit_dir

    kit = store_kit_dir("private_estate_operations")
    assert kit is not None, "Store estate kit not found -- set CEREBRUM_BLOCKS_ROOT"
    return kit


KIT = (_estate_kit() / "steward_runtime")


class TestScopeRefusalCore:
    def test_medication_dosing_is_refused(self):
        assert check_scope_refusal(
            "What morphine dose should I administer to the patient?"
        ) is not None

    def test_benign_estate_question_is_not_refused(self):
        assert check_scope_refusal("When is the next fleet service due?") is None


class TestChatRefusesOutOfScope:
    @pytest.mark.asyncio
    async def test_refused_question_never_reaches_the_llm(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STORAGE_PATH", str(tmp_path / "storage"))
        from app.core.session_store import create_session
        from app.routers.chat import _stream_response

        create_session("scope-test-session", "user-1")
        llm = AsyncMock(return_value={"message": "should never run", "chain": None, "rules": []})
        with patch("app.routers.chat.generate_chain_suggestion", new=llm):
            events = []
            async for evt in _stream_response(
                "scope-test-session",
                "What morphine dose should I administer to the patient?",
            ):
                events.append(evt)

        assert llm.await_count == 0, "refused questions must never reach the LLM"
        joined = "".join(events)
        assert "out_of_scope" in joined or "can't help with that" in joined
        entries = [
            json.loads(l)
            for l in verdict_log_path().read_text(encoding="utf-8").splitlines()
        ]
        assert entries[-1]["verdict"] == "out_of_scope"


def _load_kit_grounding():
    sys.modules.setdefault("app.steward", types.ModuleType("app.steward"))
    fake_store = types.ModuleType("app.steward.audit_store")
    fake_store.persist_audit_event = lambda *a, **k: {"stubbed": True}
    sys.modules["app.steward.audit_store"] = fake_store
    spec = importlib.util.spec_from_file_location(
        "steward_grounding_p4", KIT / "grounding.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestStewardKitMechanisms:
    def test_kit_ships_scope_refusal(self):
        module = _load_kit_grounding()
        hit = module.check_scope_refusal(
            "What morphine dose should I administer to the patient?"
        )
        assert hit is not None and hit["reason"]
        assert module.check_scope_refusal("next service due for vehicle 12?") is None

    def test_steward_query_wires_refusal(self):
        api_src = (KIT / "api.py").read_text(encoding="utf-8")
        assert "check_scope_refusal" in api_src, "steward query path has no scope refusal"

    def test_combined_search_discloses_layer_precedence(self):
        retrieval_src = (KIT / "retrieval.py").read_text(encoding="utf-8")
        assert '"precedence"' in retrieval_src, (
            "combined search must disclose which layer wins and why"
        )
        assert "estate_overrides_platform" in retrieval_src
