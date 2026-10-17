"""Tests for source-pack enrichment of chain-generation prompts."""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

from app.core.chain_generator import _build_system_prompt
from app.core.source_pack_loader import SourcePackLoaderError


LEGAL_SOURCE_PACK: Dict[str, Any] = {
    "id": "legal",
    "domain": "legal",
    "name": "Legal Source Pack",
    "description": "contract review",
    "expert_prompt": "You are a senior legal analyst reviewing contracts.",
    "workflow": "1) ingest documents 2) OCR if needed 3) legal analysis 4) chat",
    "use_cases": ["review contracts"],
    "example_prompts": ["flag risky clauses"],
    "expected_inputs": ["contracts"],
    "expected_outputs": ["risk flags"],
    "blocks": ["pdf", "ocr", "chat", "image", "legal_v2"],
}


def test_build_system_prompt_includes_source_pack_context():
    """When a source pack exists, its guidance appears in the system prompt."""
    with patch(
        "app.core.chain_generator.get_source_pack", return_value=LEGAL_SOURCE_PACK
    ):
        prompt = _build_system_prompt([], "legal", "")

    assert "Domain guidance for legal" in prompt
    assert LEGAL_SOURCE_PACK["expert_prompt"] in prompt
    assert LEGAL_SOURCE_PACK["workflow"] in prompt
    for block in LEGAL_SOURCE_PACK["blocks"]:
        assert block in prompt
    assert "Prefer the recommended source-pack blocks" in prompt
    assert "The domain v2 block is the primary domain-specific analysis block" in prompt
    assert "prefer the domain v2 block over generic built-in blocks" in prompt
    assert "Do not replace the domain v2 block with generic built-ins" in prompt
    assert "Include chat when the user needs a conversational interface or Q&A" in prompt
    assert "Use recommended blocks only if they are present" in prompt
    assert "Never invent block IDs" in prompt


def test_build_system_prompt_omits_guidance_when_source_pack_missing():
    """When no source pack exists, the legacy prompt shape is preserved."""
    with patch("app.core.chain_generator.get_source_pack", return_value=None):
        prompt = _build_system_prompt([], "legal", "")

    assert "Domain guidance" not in prompt
    assert "Expert role:" not in prompt
    assert "Workflow:" not in prompt
    assert "You are an AI solution architect for CerebrumDev.ai" in prompt
    assert "Chain JSON format:" in prompt


def test_build_system_prompt_falls_back_on_source_pack_loader_error():
    """A source-pack loader failure must not break prompt generation."""
    with patch(
        "app.core.chain_generator.get_source_pack",
        side_effect=SourcePackLoaderError("shelf missing"),
    ):
        prompt = _build_system_prompt([], "legal", "")

    assert "Domain guidance" not in prompt
    assert "Expert role:" not in prompt
    assert "Workflow:" not in prompt
    assert "You are an AI solution architect for CerebrumDev.ai" in prompt
