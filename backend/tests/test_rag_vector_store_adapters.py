
from app.core.rag_vector_store_adapters import (
    LOCAL_FLAT_JSON_V1,
    IndexSpec,
    get_adapter,
)


def test_get_local_flat_adapter_contract():
    adapter = get_adapter(LOCAL_FLAT_JSON_V1)
    assert adapter.adapter_id == LOCAL_FLAT_JSON_V1
    assert adapter.storage_type == "local_jsonl"
    assert adapter.distance_metric == "cosine"
    assert adapter.production_approved is False
    assert adapter.supports_similarity_search is False


def test_local_flat_adapter_write_and_read_records(tmp_path):
    adapter = get_adapter(LOCAL_FLAT_JSON_V1)
    spec = IndexSpec(
        index_id="idx1",
        document_id="doc1",
        domain="legal",
        base_path=tmp_path / "idx1",
    )
    manifest = {"index_id": "idx1", "record_count": 2}
    records = [{"record_id": "r1"}, {"record_id": "r2"}]
    adapter.write_records(spec, manifest, records)
    assert adapter.read_manifest(spec) == manifest
    assert adapter.read_records(spec) == records


def test_local_flat_adapter_validate_index_detects_count_mismatch(tmp_path):
    adapter = get_adapter(LOCAL_FLAT_JSON_V1)
    spec = IndexSpec(
        index_id="idx1",
        document_id="doc1",
        domain="legal",
        base_path=tmp_path / "idx1",
    )
    adapter.write_records(spec, {"index_id": "idx1", "record_count": 5}, [{"record_id": "r1"}])
    errors = adapter.validate_index(spec)
    assert any("count mismatch" in e.lower() for e in errors)
