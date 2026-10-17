"""Minimal, dependency-free JSON-Schema validator.

Supports the subset of JSON Schema used by action input/output declarations:
``type`` (object/array/string/number/integer/boolean/null), ``properties``,
``required``, ``items``, ``enum``, and ``additionalProperties`` (bool).

Kept intentionally small so the action contract carries no external validation
dependency. Returns a list of human-readable error strings (empty == valid).
"""

from __future__ import annotations

from typing import Any, Dict, List

_TYPE_MAP = {
    "object": dict,
    "array": (list, tuple),
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def validate(instance: Any, schema: Dict[str, Any], path: str = "$") -> List[str]:
    """Validate ``instance`` against ``schema``; return a list of errors."""
    errors: List[str] = []
    if not schema:
        return errors

    expected_type = schema.get("type")
    if expected_type:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        py_types = tuple(
            t
            for name in types
            for t in (
                _TYPE_MAP[name]
                if isinstance(_TYPE_MAP.get(name), tuple)
                else (_TYPE_MAP.get(name),)
            )
            if t is not None
        )
        # bool is a subclass of int; guard integer/number against bool.
        if "boolean" not in types and isinstance(instance, bool):
            errors.append(f"{path}: expected {expected_type}, got boolean")
            return errors
        if py_types and not isinstance(instance, py_types):
            errors.append(
                f"{path}: expected type {expected_type}, got {type(instance).__name__}"
            )
            return errors

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum {schema['enum']}")

    if isinstance(instance, dict) and (
        expected_type == "object" or "properties" in schema or "required" in schema
    ):
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required property '{req}'")
        props = schema.get("properties", {})
        for key, subschema in props.items():
            if key in instance:
                errors.extend(validate(instance[key], subschema, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            extra = set(instance) - set(props)
            if extra:
                errors.append(f"{path}: unexpected properties {sorted(extra)}")

    if isinstance(instance, (list, tuple)) and "items" in schema:
        item_schema = schema["items"]
        for idx, item in enumerate(instance):
            errors.extend(validate(item, item_schema, f"{path}[{idx}]"))

    return errors
