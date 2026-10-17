"""Tests for the soft domain-v2 chain-quality metadata check."""

from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient

from app.core.chain_generator import check_chain_quality
from app.core.session_store import create_session, get_session
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
    "blocks": ["pdf", "ocr", "chat", "image", "formula_executor_v2", "legal_v2"],
}

LEGAL_CHAIN_WITH_V2: Dict[str, Any] = {
    "blocks": [
        {"id": "pdf"},
        {"id": "ocr"},
        {"id": "legal_v2"},
        {"id": "chat"},
    ]
}

LEGAL_CHAIN_WITHOUT_V2: Dict[str, Any] = {
    "blocks": [
        {"id": "pdf"},
        {"id": "ocr"},
        {"id": "chat"},
    ]
}


def _store(root, manifests):
    """A fixture Store: block_registry/<id>/block.json for each manifest."""
    for bid, manifest in manifests.items():
        d = root / "block_registry" / bid
        d.mkdir(parents=True)
        (d / "block.json").write_text(json.dumps({"id": bid, **manifest}), encoding="utf-8")
    return root


@pytest.fixture(autouse=True)
def _declared_store(tmp_path, monkeypatch):
    """formula_executor_v2 DECLARES itself shared reasoning; nothing else
    declares a class. The quality check reads that, never the id."""
    root = _store(
        tmp_path / "store",
        {"formula_executor_v2": {"capability_class": "shared_reasoning"}, "legal_v2": {}},
    )
    monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(root))
    return root


class TestCheckChainQuality:
    """Unit tests for check_chain_quality."""

    def test_check_chain_quality_with_domain_v2_ok(self):
        with patch(
            "app.core.chain_generator.get_source_pack", return_value=LEGAL_SOURCE_PACK
        ):
            result = check_chain_quality("legal", LEGAL_CHAIN_WITH_V2, True)

        assert result == {"status": "ok", "warnings": []}

    def test_check_chain_quality_missing_domain_v2_needs_review(self):
        with patch(
            "app.core.chain_generator.get_source_pack", return_value=LEGAL_SOURCE_PACK
        ):
            result = check_chain_quality("legal", LEGAL_CHAIN_WITHOUT_V2, True)

        assert result is not None
        assert result["status"] == "needs_review"
        warning = result["warnings"][0]
        assert warning["code"] == "missing_domain_v2_block"
        assert warning["suggested_block"] == "legal_v2"
        assert "legal_v2" in warning["message"]

    def test_check_chain_quality_validation_failed_returns_none(self):
        with patch(
            "app.core.chain_generator.get_source_pack", return_value=LEGAL_SOURCE_PACK
        ):
            result = check_chain_quality("legal", LEGAL_CHAIN_WITH_V2, False)

        assert result is None

    def test_check_chain_quality_no_chain_returns_none(self):
        with patch(
            "app.core.chain_generator.get_source_pack", return_value=LEGAL_SOURCE_PACK
        ):
            result = check_chain_quality("legal", None, True)

        assert result is None

    def test_check_chain_quality_missing_source_pack_returns_none(self):
        with patch("app.core.chain_generator.get_source_pack", return_value=None):
            result = check_chain_quality("legal", LEGAL_CHAIN_WITH_V2, True)

        assert result is None

    def test_check_chain_quality_source_pack_loader_error_returns_none(self):
        with patch(
            "app.core.chain_generator.get_source_pack",
            side_effect=SourcePackLoaderError("shelf missing"),
        ):
            result = check_chain_quality("legal", LEGAL_CHAIN_WITH_V2, True)

        assert result is None

    def test_check_chain_quality_no_v2_block_returns_none(self):
        pack = {**LEGAL_SOURCE_PACK, "blocks": ["pdf", "ocr", "chat", "image", "formula_executor_v2"]}
        chain = {"blocks": [{"id": "pdf"}, {"id": "ocr"}, {"id": "chat"}]}

        with patch("app.core.chain_generator.get_source_pack", return_value=pack):
            result = check_chain_quality("legal", chain, True)

        assert result is None

    def test_check_chain_quality_ignores_formula_executor_v2_as_domain_v2(self):
        """formula_executor_v2 is shared reasoning support, not the primary domain block."""
        chain = {"blocks": [{"id": "pdf"}, {"id": "ocr"}, {"id": "formula_executor_v2"}, {"id": "chat"}]}

        with patch(
            "app.core.chain_generator.get_source_pack", return_value=LEGAL_SOURCE_PACK
        ):
            result = check_chain_quality("legal", chain, True)

        assert result is not None
        assert result["status"] == "needs_review"
        assert result["warnings"][0]["suggested_block"] == "legal_v2"


class TestStreamResponseQuality:
    """Integration tests for quality metadata in chat streaming."""

    def test_stream_response_includes_quality_in_chain_event(self, client: TestClient):
        session = create_session(session_id="sess_quality_stream", user_id="test_user")
        session.config.domain = "legal"

        fake_chain = LEGAL_CHAIN_WITHOUT_V2
        fake_suggestion = {
            "message": "Here is a chain without the legal v2 block.",
            "chain": fake_chain,
            "rules": [],
        }
        fake_registry = {b["id"]: {"name": b["id"]} for b in fake_chain["blocks"]}

        with patch(
            "app.routers.chat.generate_chain_suggestion",
            return_value=fake_suggestion,
        ), patch(
            "app.routers.chat.fetch_block_registry", return_value=fake_registry
        ), patch(
            "app.core.chain_generator.get_source_pack", return_value=LEGAL_SOURCE_PACK
        ):
            response = client.post(
                f"/v1/sessions/{session.session_id}/chat",
                json={"message": "Review this contract.", "action": "chain"},
            )

        assert response.status_code == 200

        chain_event = None
        for line in response.text.splitlines():
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = line.split(":", 1)[1].strip()
                if event_type == "chain":
                    chain_event = json.loads(json.loads(data))

        assert chain_event is not None
        assert "quality" in chain_event
        assert chain_event["quality"]["status"] == "needs_review"
        assert chain_event["quality"]["warnings"][0]["code"] == "missing_domain_v2_block"

        state = get_session(session.session_id)
        assert state.chain_quality == chain_event["quality"]


class TestPreviewChainQuality:
    """Integration tests for quality metadata in the chain preview endpoint."""

    def test_preview_chain_includes_quality(self, client: TestClient):
        session = create_session(session_id="sess_quality_preview", user_id="test_user")
        session.proposed_chain = LEGAL_CHAIN_WITHOUT_V2
        session.chain_quality = {
            "status": "needs_review",
            "warnings": [
                {
                    "code": "missing_domain_v2_block",
                    "message": "This legal chain does not include legal_v2, the primary legal analysis block.",
                    "suggested_block": "legal_v2",
                }
            ],
        }

        response = client.get(f"/v1/sessions/{session.session_id}/chain/preview")

        assert response.status_code == 200
        payload = response.json()
        assert "quality" in payload
        assert payload["quality"] == session.chain_quality


class TestPrimaryBlockIsReadFromDeclarations:
    """The shared-support exclusion follows the declaration, not the id."""

    def test_an_invented_id_declaring_shared_reasoning_is_never_primary(self, tmp_path, monkeypatch):
        root = _store(
            tmp_path / "other",
            {"zq_support_v2": {"capability_class": "shared_reasoning"}, "zq_domain_v2": {}},
        )
        monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(root))
        pack = {**LEGAL_SOURCE_PACK, "blocks": ["pdf", "zq_support_v2", "zq_domain_v2"]}
        with patch("app.core.chain_generator.get_source_pack", return_value=pack):
            result = check_chain_quality("legal", {"blocks": [{"id": "pdf"}]}, True)
        assert result["warnings"][0]["suggested_block"] == "zq_domain_v2"

    def test_the_old_id_without_the_declaration_gets_no_exemption(self, tmp_path, monkeypatch):
        root = _store(tmp_path / "bare", {"formula_executor_v2": {}, "legal_v2": {}})
        monkeypatch.setenv("CEREBRUM_BLOCKS_ROOT", str(root))
        with patch(
            "app.core.chain_generator.get_source_pack", return_value=LEGAL_SOURCE_PACK
        ):
            result = check_chain_quality("legal", {"blocks": [{"id": "pdf"}]}, True)
        assert result["warnings"][0]["suggested_block"] == "formula_executor_v2"
