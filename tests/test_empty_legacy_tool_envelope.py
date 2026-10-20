from types import SimpleNamespace as NS

import pytest

from app.ai.errors import AIResponseError
from app.ai.tool_protocol import chat_tool_fragments


def test_empty_legacy_final_chunk_does_not_invent_call():
    chunk = NS(function_call=NS(name="", arguments=""), tool_calls=None)
    assert chat_tool_fragments(chunk, legacy_id="synthetic") == ()


def test_empty_legacy_envelope_preserves_modern_call():
    call = NS(index=0, id="real", function=NS(name="add", arguments="{}"))
    chunk = NS(function_call=NS(name="", arguments=""), tool_calls=[call])
    assert chat_tool_fragments(chunk, legacy_id="synthetic") == (call,)


def test_real_mixed_protocols_still_rejected():
    chunk = NS(function_call=NS(name="add", arguments=""), tool_calls=[NS(id="real")])
    with pytest.raises(AIResponseError, match="mixes modern and legacy"):
        chat_tool_fragments(chunk, legacy_id="synthetic")


def test_argument_only_legacy_fragment_is_preserved():
    legacy = NS(name=None, arguments='{"a":')
    calls = chat_tool_fragments(NS(function_call=legacy), legacy_id="synthetic")
    assert calls[0].function is legacy
