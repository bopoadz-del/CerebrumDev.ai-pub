"""Tests for the RAG activation/status contract helper."""

from __future__ import annotations

from unittest.mock import patch

from app.core.rag_activation import build_rag_activation_status


LEGAL_RAG_PACK = {
    "id": "legal_core_rag",
    "domain": "legal",
    "name": "Legal Core RAG Pack",
    "description": "contract review knowledge",
    "collection_id": "prebuilt_legal_core",
    "visibility": "platform_prebuilt",
    "data_class": "public_or_licensed_reference",
    "enterprise_specific": False,
    "requires_blocks": ["knowledge", "vector_search"],
    "recommended_with_blocks": ["legal_v2", "formula_executor_v2"],
    "source_types": ["statutes", "templates"],
    "expected_queries": ["what is the governing law?"],
    "expected_outputs": ["cited answer"],
    "fetch_mode": "metadata_only",
    "source_policy": {
        "allowed_source_classes": ["official_statute_or_regulation"],
        "precluded_source_classes": ["private_enterprise_data", "unknown_license"],
        "requires_source_record": True,
        "requires_license_review": True,
        "requires_authority_rating": True,
    },
    "ingestion_status": {
        "state": "not_ingested",
        "documents_total": 0,
        "documents_indexed": 0,
        "chunks_total": 0,
        "last_ingested_at": None,
        "last_error": None,
    },
    "notes": [],
}


def test_build_rag_activation_status_for_legal():
    with patch("app.core.rag_activation.get_rag_pack", return_value=LEGAL_RAG_PACK):
        status = build_rag_activation_status("legal")

    assert status["domain"] == "legal"
    assert status["status"] == "available_metadata_only"
    assert status["attachable"] is True
    assert status["attached"] is False
    assert status["rag_pack"] is not None
    assert status["rag_pack"]["id"] == "legal_core_rag"
    assert status["rag_pack"]["collection_id"] == "prebuilt_legal_core"
    assert status["rag_pack"]["ingestion_status"]["state"] == "not_ingested"
    assert status["rag_pack"]["ingestion_status"]["documents_total"] == 0
    assert status["rag_pack"]["fetch_mode"] == "metadata_only"
    assert status["rag_pack"]["enterprise_specific"] is False
    assert status["rag_pack"]["source_policy"]["requires_source_record"] is True
    assert status["rag_pack"]["source_policy"]["requires_license_review"] is True
    assert status["rag_pack"]["source_policy"]["requires_authority_rating"] is True
    assert "knowledge" in status["requires_blocks"]
    assert "vector_search" in status["requires_blocks"]
    assert "legal_v2" in status["recommended_with_blocks"]
    assert "formula_executor_v2" in status["recommended_with_blocks"]


def test_build_rag_activation_status_for_unknown_domain():
    with patch("app.core.rag_activation.get_rag_pack", return_value=None):
        status = build_rag_activation_status("unknown")

    assert status["domain"] == "unknown"
    assert status["status"] == "not_available"
    assert status["attachable"] is False
    assert status["attached"] is False
    assert status["rag_pack"] is None
    assert status["requires_blocks"] == []
    assert status["recommended_with_blocks"] == []
