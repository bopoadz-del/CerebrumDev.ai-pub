"""Validate Product DNA documents against versioned JSON Schemas."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from app.cerebrum_product_kernel.contract.schema_validation import validate as _validate

SCHEMAS_DIR = Path(__file__).resolve().parent / "schemas"


def load_schema(name: str) -> Dict[str, Any]:
    """Load ``{name}.schema.json`` from the schemas directory."""
    path = SCHEMAS_DIR / f"{name}.schema.json"
    if not path.is_file():
        raise FileNotFoundError(f"Missing Product DNA schema: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_dna_document(name: str, document: Any) -> List[str]:
    """Return schema validation errors for a named DNA document (empty == ok)."""
    schema = load_schema(name)
    return _validate(document, schema)
