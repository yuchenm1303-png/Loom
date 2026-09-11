from __future__ import annotations

from app.agent_runtime.json_schema_semantics import validating_schema


def test_annotation_stripping_preserves_properties_named_like_annotations():
    schema = {
        "type": "object",
        "title": "Prompt title",
        "description": "Prompt description",
        "properties": {
            "description": {
                "type": "string",
                "description": "Help for a real argument named description",
            },
            "title": {
                "type": "integer",
                "title": "Help for a real argument named title",
            },
        },
        "required": ["description", "title"],
        "additionalProperties": False,
    }

    projected = validating_schema(schema)

    assert "title" not in projected
    assert "description" not in projected
    assert set(projected["properties"]) == {"description", "title"}
    assert projected["properties"]["description"] == {"type": "string"}
    assert projected["properties"]["title"] == {"type": "integer"}
    assert projected["required"] == ["description", "title"]


def test_literal_objects_are_not_rewritten_as_schema_keywords():
    literal = {"description": "must stay literal", "title": "also literal"}
    schema = {
        "type": "object",
        "properties": {
            "mode": {
                "enum": [literal],
                "description": "prompt-only help",
            }
        },
    }

    projected = validating_schema(schema)

    assert projected["properties"]["mode"]["enum"] == [literal]
    assert "description" not in projected["properties"]["mode"]
