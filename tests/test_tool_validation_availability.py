"""A broken install must not look like a broken tool.

Loom was launched by an interpreter that had Loom but not `jsonschema`, which
`validate_tool_arguments` imports. Every tool call -- including `echo` -- came
back as "Invalid tool request: invalid or unresolved tool schema: No module
named 'jsonschema'". That reads like a per-call schema problem, so the model
reported Loom's schema layer as faulty, stopped using `exec`, and offered the
user a PowerShell script to run by hand instead.

The import failure now says what is actually wrong and how to fix it.
"""

from __future__ import annotations

import builtins

import pytest

from app.agent_runtime.tools import (
    ToolValidationUnavailable,
    _schema_validator,
    validate_tool_arguments,
)


SCHEMA = {
    "type": "object",
    "properties": {"argv": {"type": "array"}},
    "required": ["argv"],
    "additionalProperties": False,
}


@pytest.fixture
def missing_jsonschema(monkeypatch):
    _schema_validator.cache_clear()
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "jsonschema" or name.startswith("jsonschema."):
            raise ImportError("No module named 'jsonschema'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    yield
    _schema_validator.cache_clear()


def test_a_missing_validator_is_reported_as_a_broken_install(missing_jsonschema):
    with pytest.raises(ToolValidationUnavailable) as caught:
        validate_tool_arguments(SCHEMA, {"argv": ["echo", "hi"]})

    message = str(caught.value)
    assert "schema validator" in message
    assert "No tool can run" in message
    # The fix has to name the interpreter, because the usual cause is Loom being
    # launched by a different one from the environment it was installed into.
    assert "pip install -e" in message
    assert "python" in message.casefold()


def test_it_is_not_disguised_as_a_bad_schema(missing_jsonschema):
    with pytest.raises(ToolValidationUnavailable) as caught:
        validate_tool_arguments(SCHEMA, {"argv": []})

    # The old wording sent the model looking for a fault in the tool's schema.
    assert "invalid or unresolved tool schema" not in str(caught.value)


def test_it_is_not_a_value_error_so_it_cannot_become_one_failed_tool(missing_jsonschema):
    # AgentRuntime turns ValueError from prepare() into a single failed tool
    # result and keeps going, which is how one broken install produced a run of
    # identical failures. This has to stop the turn instead.
    with pytest.raises(ToolValidationUnavailable):
        validate_tool_arguments(SCHEMA, {"argv": ["echo"]})
    assert not issubclass(ToolValidationUnavailable, ValueError)


def test_a_genuinely_bad_schema_is_still_a_value_error():
    with pytest.raises(ValueError) as caught:
        validate_tool_arguments({"type": "object", "properties": 5}, {})
    assert not isinstance(caught.value, ToolValidationUnavailable)


def test_valid_arguments_still_pass_and_invalid_ones_still_fail():
    validate_tool_arguments(SCHEMA, {"argv": ["echo", "hi"]})

    with pytest.raises(ValueError) as caught:
        validate_tool_arguments(SCHEMA, {"argv": "echo"})
    assert not isinstance(caught.value, ToolValidationUnavailable)
