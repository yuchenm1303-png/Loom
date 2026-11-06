"""A rule at the root of a tool's schema stays local: one provider's 400 stopped every request."""
import pytest

from app.agent_runtime.patch_tools import apply_patch_tool
from app.agent_runtime.tools import ToolRequestError, validate_tool_arguments
from app.ai.contracts import ToolDefinition
from app.ai.tool_declarations import chat_completions_tool, messages_tool, responses_tool, wire_schema


FORBIDDEN = {"oneOf", "anyOf", "allOf", "enum", "const", "not"}  # the provider's own list


def definition(tool):
    return ToolDefinition(name=tool.name, description=tool.description, input_schema=tool.input_schema)


@pytest.mark.parametrize("envelope, read", [
    (chat_completions_tool, lambda item: item["function"]["parameters"]),
    (responses_tool, lambda item: item["parameters"]),
    (messages_tool, lambda item: item["input_schema"]),
])
def test_apply_patch_reaches_every_protocol_without_a_root_combinator(envelope, read):
    sent = read(envelope(definition(apply_patch_tool())))
    assert sent["type"] == "object" and not FORBIDDEN & set(sent)
    assert set(sent["properties"]) == {"changes", "patch"} and sent["additionalProperties"] is False


def test_the_root_rule_still_holds_on_loom_s_side():
    schema = apply_patch_tool().input_schema
    assert "oneOf" in schema  # the wire copy did not touch what validates calls
    change = {"action": "delete", "path": "a.txt"}
    validate_tool_arguments(schema, {"changes": [change]})
    for wrong in ({}, {"changes": [change], "patch": "*** Begin Patch"}):
        with pytest.raises(ToolRequestError):
            validate_tool_arguments(schema, wrong)


def test_only_the_root_is_trimmed():
    nested = {"type": "object", "properties": {"x": {"oneOf": [{"type": "string"}, {"type": "integer"}]}}}
    assert wire_schema(nested) is nested  # same object: nothing rebuilt, the request prefix stays byte-stable
    third_party = {"type": "object", "properties": {}, "anyOf": [{"required": ["a"]}], "if": {}, "then": {}}
    assert wire_schema(third_party) == {"type": "object", "properties": {}}
