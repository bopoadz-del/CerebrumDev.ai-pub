"""Grounding law: the conversational fallback must never override the platform.

The LLM fallback (generate_chain_suggestion) answers strictly from an
authoritative session-state summary and is forbidden from claiming platform
actions. Regression coverage for the "LLM answers from its ass" hole.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.chain_generator import _build_system_prompt, generate_chain_suggestion
from app.routers.chat import _session_state_summary


class TestSessionStateSummary:
    def test_no_product_design(self):
        state = SimpleNamespace(product_design=None)
        assert "No platform blueprint" in _session_state_summary(state)

    def test_pending_blueprint(self):
        pd = SimpleNamespace(
            blueprint={
                "product_name": "FleetOps Live",
                "vertical": "fleet_operations",
                "capabilities": [{"id": "core"}, {"id": "audit"}],
            },
            blueprint_approved=False,
            generation=None,
            last_error=None,
        )
        summary = _session_state_summary(SimpleNamespace(product_design=pd))
        assert "FleetOps Live" in summary
        assert "Blueprint approved: no." in summary
        assert "No product has been generated yet." in summary

    def test_generated_product(self):
        pd = SimpleNamespace(
            blueprint={
                "product_name": "FleetOps Live",
                "vertical": "fleet_operations",
                "capabilities": [{"id": "core"}],
            },
            blueprint_approved=True,
            generation={"product_id": "fleetops-live"},
            last_error=None,
        )
        summary = _session_state_summary(SimpleNamespace(product_design=pd))
        assert "Blueprint approved: yes." in summary
        assert "fleetops-live" in summary


class TestGroundedSystemPrompt:
    def test_prompt_carries_state_and_laws(self):
        prompt = _build_system_prompt([], "construction", "", session_state="No product has been generated yet.")
        assert "Session state (authoritative" in prompt
        assert "No product has been generated yet." in prompt
        assert "Never claim that you or the platform have built, generated, deployed" in prompt
        assert "Never invent URLs, prices, credentials" in prompt

    def test_prompt_without_state_omits_section(self):
        prompt = _build_system_prompt([], "construction", "")
        assert "Session state (authoritative" not in prompt

    @pytest.mark.asyncio
    async def test_fallback_receives_session_state(self):
        captured = {}

        async def fake_call_llm(messages):
            captured["system"] = messages[0]["content"]
            return {"message": "grounded", "chain": None, "rules": []}

        with patch(
            "app.core.chain_generator.fetch_block_registry",
            new=AsyncMock(return_value={}),
        ), patch(
            "app.core.chain_generator._call_llm",
            new=fake_call_llm,
        ), patch(
            "app.core.chain_generator.get_llm_config",
            return_value={"provider": "kimi", "base_url": "http://x", "model": "m", "api_key": "k"},
        ):
            result = await generate_chain_suggestion(
                domain="construction",
                user_message="did you build it?",
                chat_history=[],
                docs_summary="",
                session_state="No product has been generated yet.",
            )
        assert result["message"] == "grounded"
        assert "No product has been generated yet." in captured["system"]
