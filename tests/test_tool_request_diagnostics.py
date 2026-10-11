import pytest

from app.agent_runtime.tools import ToolRequestError, validate_tool_arguments


@pytest.mark.parametrize("schema,arguments,rule", [
    ({"type": "object", "properties": {"password": {"type": "integer"}}}, {"password": "private-password"}, "type"),
    ({"type": "object", "properties": {"password": {"enum": ["allowed"]}}}, {"password": "private-password"}, "enum"),
    ({"type": "object", "additionalProperties": False}, {"private-password": "secret"}, "additionalProperties"),
])
def test_schema_errors_do_not_echo_rejected_values(schema, arguments, rule):
    with pytest.raises(ToolRequestError) as caught:
        validate_tool_arguments(schema, arguments)
    assert "private-password" not in str(caught.value)
    assert "secret" not in str(caught.value)
    assert caught.value.details["error_code"] == "invalid_arguments"
    assert caught.value.details["validation_rule"] == rule
