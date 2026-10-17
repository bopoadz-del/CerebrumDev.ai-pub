"""Strict schema validation — unknown fields rejected."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from app.cerebrum_product_kernel.contract.schema_validation import validate as _validate

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"

KIND_TO_SCHEMA = {
    "REPAIR": "repair_request",
    "EXPANSION": "expansion_request",
    "UPGRADE": "upgrade_request",
}


def load_schema(name: str) -> Dict[str, Any]:
    path = SCHEMAS_DIR / f"{name}.schema.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def validate_change_request(document: Dict[str, Any]) -> List[str]:
    """Return human-readable errors (empty == valid). Unknown fields fail."""
    kind = document.get("kind")
    schema_name = KIND_TO_SCHEMA.get(str(kind or ""))
    if not schema_name:
        return [f"$.kind: unknown or missing kind {kind!r}; expected REPAIR|EXPANSION|UPGRADE"]
    schema = load_schema(schema_name)
    return _validate(document, schema)


def validate_or_raise(document: Dict[str, Any]) -> None:
    errors = validate_change_request(document)
    if errors:
        raise ValueError("; ".join(errors))
