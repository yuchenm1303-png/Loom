from __future__ import annotations

from typing import Any


# These keywords are annotations in JSON Schema: removing them changes model
# guidance, not validation semantics. They may also legally appear as *property
# names*, so callers must never delete matching keys from a ``properties`` map.
NONVALIDATING_ANNOTATION_KEYS = frozenset(
    {
        "description",
        "title",
        "examples",
        "example",
        "$comment",
        "deprecated",
        "readOnly",
        "writeOnly",
    }
)

_SCHEMA_MAP_KEYWORDS = frozenset(
    {
        "properties",
        "patternProperties",
        "$defs",
        "definitions",
        "dependentSchemas",
    }
)
_SCHEMA_ARRAY_KEYWORDS = frozenset(
    {
        "allOf",
        "anyOf",
        "oneOf",
        "prefixItems",
    }
)
_SCHEMA_VALUE_KEYWORDS = frozenset(
    {
        "items",
        "contains",
        "not",
        "if",
        "then",
        "else",
        "propertyNames",
        "additionalProperties",
        "unevaluatedProperties",
        "unevaluatedItems",
        "contentSchema",
    }
)


def _schema_node(value: Any) -> Any:
    if not isinstance(value, dict):
        # Boolean schemas are valid and all literal values should remain exact.
        return value

    output: dict[str, Any] = {}
    for key, item in value.items():
        if key in NONVALIDATING_ANNOTATION_KEYS:
            continue

        if key in _SCHEMA_MAP_KEYWORDS and isinstance(item, dict):
            # Keys in these maps are user property/definition/pattern names, not
            # JSON Schema keywords. Preserve them byte-for-byte and recurse only
            # into their schema values.
            output[key] = {
                child_name: _schema_node(child_schema)
                for child_name, child_schema in item.items()
            }
            continue

        if key in _SCHEMA_ARRAY_KEYWORDS and isinstance(item, list):
            output[key] = [_schema_node(child) for child in item]
            continue

        if key in _SCHEMA_VALUE_KEYWORDS and isinstance(item, dict):
            output[key] = _schema_node(item)
            continue

        if key == "dependencies" and isinstance(item, dict):
            # Draft-07 compatibility: dependency values may be either a list of
            # property names or a schema.
            output[key] = {
                child_name: (
                    _schema_node(child_value)
                    if isinstance(child_value, dict)
                    else child_value
                )
                for child_name, child_value in item.items()
            }
            continue

        # Do not recursively walk arbitrary literal-bearing keywords (enum,
        # const, default, extension payloads). Objects inside them are data and a
        # key named "description" may be validation-significant literal content.
        output[key] = item
    return output


def validating_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy containing validating semantics but no prompt-only annotations.

    This function is shared by request schema compaction and approval binding
    identity. Keeping one canonicalizer prevents a tool from looking equivalent
    to the prompt layer while hashing differently at execution time.
    """

    if not isinstance(schema, dict):
        raise TypeError("schema must be a JSON object")
    return _schema_node(schema)


__all__ = ["NONVALIDATING_ANNOTATION_KEYS", "validating_schema"]
