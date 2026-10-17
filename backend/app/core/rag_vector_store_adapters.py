"""Model-independent vector-store adapter contract and validation-only adapters.

No production vector databases, no similarity search, no retrieval.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

LOCAL_FLAT_JSON_V1 = "local_flat_json_v1"


@dataclass
class IndexSpec:
    """Specification for a vector index artifact location."""

    index_id: str
    document_id: str
    domain: str
    base_path: Path


@dataclass
class RagVectorStoreAdapter:
    """Contract for a vector-store adapter."""

    adapter_id: str
    adapter_version: str
    storage_type: str
    distance_metric: str
    supported_dimensions: List[int]
    supports_upsert: bool
    supports_delete: bool
    supports_metadata: bool
    supports_filtering: bool
    supports_similarity_search: bool
    production_approved: bool

    def create_index(self, index_spec: IndexSpec) -> None:
        """Ensure the index directory exists."""
        raise NotImplementedError

    def write_records(
        self, index_spec: IndexSpec, manifest: dict, records: List[dict]
    ) -> None:
        """Atomically write manifest and records."""
        raise NotImplementedError

    def read_records(self, index_spec: IndexSpec) -> List[dict]:
        """Read all records from the index."""
        raise NotImplementedError

    def read_manifest(self, index_spec: IndexSpec) -> Optional[dict]:
        """Read the index manifest."""
        raise NotImplementedError

    def validate_index(self, index_spec: IndexSpec) -> List[str]:
        """Return validation error messages, or empty list if valid."""
        raise NotImplementedError

    def delete_dry_run_index(self, index_spec: IndexSpec) -> None:
        """Delete a dry-run index artifact."""
        raise NotImplementedError


class _LocalFlatJsonAdapterV1(RagVectorStoreAdapter):
    """Validation-only local flat JSONL adapter.

    Not a production vector database. Not an ANN index. Does not enable
    retrieval. Exists only to validate the vector-store adapter contract.
    """

    _VERSION = "1"
    _STORAGE_TYPE = "local_jsonl"
    _DISTANCE_METRIC = "cosine"
    _SUPPORTED_DIMENSIONS = [384]

    def __init__(self) -> None:
        super().__init__(
            adapter_id=LOCAL_FLAT_JSON_V1,
            adapter_version=self._VERSION,
            storage_type=self._STORAGE_TYPE,
            distance_metric=self._DISTANCE_METRIC,
            supported_dimensions=self._SUPPORTED_DIMENSIONS,
            supports_upsert=False,
            supports_delete=True,
            supports_metadata=True,
            supports_filtering=False,
            supports_similarity_search=False,
            production_approved=False,
        )

    def _manifest_path(self, index_spec: IndexSpec) -> Path:
        return index_spec.base_path / "manifest.json"

    def _records_path(self, index_spec: IndexSpec) -> Path:
        return index_spec.base_path / "records.jsonl"

    def create_index(self, index_spec: IndexSpec) -> None:
        index_spec.base_path.mkdir(parents=True, exist_ok=True)

    def write_records(
        self, index_spec: IndexSpec, manifest: dict, records: List[dict]
    ) -> None:
        self.create_index(index_spec)
        manifest_path = self._manifest_path(index_spec)
        records_path = self._records_path(index_spec)

        tmp_manifest = manifest_path.with_suffix(".json.tmp")
        tmp_records = records_path.with_suffix(".jsonl.tmp")
        try:
            tmp_manifest.write_text(
                json.dumps(manifest, separators=(",", ":"), sort_keys=True),
                encoding="utf-8",
            )
            with tmp_records.open("w", encoding="utf-8") as f:
                for record in records:
                    f.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")
            os.replace(tmp_manifest, manifest_path)
            os.replace(tmp_records, records_path)
        except Exception:
            if tmp_manifest.exists():
                tmp_manifest.unlink(missing_ok=True)
            if tmp_records.exists():
                tmp_records.unlink(missing_ok=True)
            raise

    def read_manifest(self, index_spec: IndexSpec) -> Optional[dict]:
        path = self._manifest_path(index_spec)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read manifest %s: %s", path, exc)
            return None

    def read_records(self, index_spec: IndexSpec) -> List[dict]:
        path = self._records_path(index_spec)
        if not path.exists():
            return []
        records = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    records.append(json.loads(line))
        except Exception as exc:
            logger.warning("Failed to read records %s: %s", path, exc)
        return records

    def validate_index(self, index_spec: IndexSpec) -> List[str]:
        errors: List[str] = []
        manifest = self.read_manifest(index_spec)
        if manifest is None:
            errors.append("Manifest not found.")
            return errors
        records = self.read_records(index_spec)
        if manifest.get("record_count", 0) != len(records):
            errors.append("Record count mismatch.")
        return errors

    def delete_dry_run_index(self, index_spec: IndexSpec) -> None:
        if index_spec.base_path.exists():
            shutil.rmtree(index_spec.base_path)


_ADAPTERS: Dict[str, RagVectorStoreAdapter] = {
    LOCAL_FLAT_JSON_V1: _LocalFlatJsonAdapterV1(),
}


def register_adapter(adapter: RagVectorStoreAdapter) -> None:
    _ADAPTERS[adapter.adapter_id] = adapter


def get_adapter(adapter_id: str) -> Optional[RagVectorStoreAdapter]:
    return _ADAPTERS.get(adapter_id)


def list_adapter_ids() -> List[str]:
    return list(_ADAPTERS.keys())
