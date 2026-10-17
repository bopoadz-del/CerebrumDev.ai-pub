"""Phase 6 acceptance: the honest zip manifest. T6.1-T6.3 + P6."""

from __future__ import annotations

import json

import pytest

from app.factory.build.export_manifest import (
    ENGINE_FILE,
    MANIFEST_FILENAME,
    MANIFEST_MISMATCH,
    ZIP_REQUIRES_GREEN_CI,
    ExportManifestError,
    assert_zip_eligibility,
    build_manifest,
    ui_labels_from_manifest,
    verify_manifest,
    write_export_manifest,
)


def _manifest(**overrides):
    base = dict(
        product_id="probe-platform",
        tenant_id="tenant_a",
        ci_run="12345",
        retrieval_mode="vector_rag",
        tenancy_mode="multi_tenant_partition",
        embedder="onnx-minilm",
        vector_store="chroma_tenant_collection",
        engine_version="kernel-4.1",
        prompt_version="writer_worker_prompt.v1",
        layer_counts={1: 2, 2: 1, 3: 1},
        engine_included=True,
    )
    base.update(overrides)
    return build_manifest(**base)


def _zip_with_engine() -> set:
    return {ENGINE_FILE, "app/main.py", "MANIFEST.json"}


# -- T6.1: the manifest must match the zip's actual contents ----------------


def test_manifest_matches_contents():
    manifest = _manifest()
    assert verify_manifest(manifest, _zip_with_engine()) == []


def test_engine_claim_without_engine_files_is_manifest_mismatch():
    manifest = _manifest(engine_included=True, retrieval_mode="vector_rag")
    problems = verify_manifest(manifest, {"app/main.py"})
    assert any(MANIFEST_MISMATCH in p for p in problems)


def test_vector_rag_claim_without_engine_files_is_manifest_mismatch():
    manifest = _manifest(engine_included=False, retrieval_mode="vector_rag")
    problems = verify_manifest(manifest, {"app/main.py"})
    assert any(MANIFEST_MISMATCH in p and "vector_rag" in p for p in problems)


def test_write_and_round_trip(tmp_path):
    manifest = _manifest()
    path = write_export_manifest(tmp_path, manifest)
    assert path.name == MANIFEST_FILENAME
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["schema"] == "export_manifest.v1"
    assert loaded["tenancy_mode"] == "multi_tenant_partition"


# -- T6.2: the UI can never overclaim ----------------------------------------


def test_ui_cannot_render_rag_for_keyword_lexical():
    manifest = _manifest(retrieval_mode="keyword_lexical", engine_included=True)
    labels = ui_labels_from_manifest(manifest, _zip_with_engine())
    assert labels["rag"] is None


def test_ui_cannot_render_rag_without_engine_files():
    manifest = _manifest(retrieval_mode="vector_rag")
    labels = ui_labels_from_manifest(manifest, {"app/main.py"})
    assert labels["rag"] is None


def test_ui_renders_rag_only_when_both_hold():
    manifest = _manifest()
    labels = ui_labels_from_manifest(manifest, _zip_with_engine())
    assert labels["rag"] == "RAG included"


def test_ui_cannot_render_multi_tenant_for_single_tenant():
    manifest = _manifest(tenancy_mode="single_tenant_only")
    labels = ui_labels_from_manifest(manifest, _zip_with_engine())
    assert labels["tenancy"] == "Single-tenant"
    assert labels["tenancy"] != "Multi-tenant"


# -- T6.3: no zip without green CI and the artifact gate ---------------------


def test_no_zip_without_green_ci_and_artifact_gate():
    with pytest.raises(ExportManifestError) as exc:
        assert_zip_eligibility({"state": "failed"}, "ci-1")
    assert ZIP_REQUIRES_GREEN_CI in str(exc.value)

    with pytest.raises(ExportManifestError) as exc:
        assert_zip_eligibility(
            {"state": "succeeded", "authorship": {"agent_written": 0}}, "ci-1"
        )
    assert "artifact gate" in str(exc.value)

    with pytest.raises(ExportManifestError) as exc:
        assert_zip_eligibility(
            {"state": "succeeded", "authorship": {"agent_written": 3}}, ""
        )
    assert "CI run id" in str(exc.value)

    # The honest case passes: succeeded + agent artifacts + CI run recorded.
    assert_zip_eligibility(
        {"state": "succeeded", "authorship": {"agent_written": 3}}, "ci-1"
    )


# -- P6: a forged manifest is detected, not shipped --------------------------


def test_forged_vector_rag_manifest_is_detected(tmp_path):
    """The mutation: forge vector_rag with no engine in the zip. T6.1 goes
    RED — the forged claim is a manifest_mismatch, never shipped."""
    manifest = _manifest(retrieval_mode="vector_rag", engine_included=True)
    problems = verify_manifest(manifest, {"app/main.py"})
    assert problems, "a forged vector_rag manifest verified clean"
    assert all(MANIFEST_MISMATCH in p for p in problems)
