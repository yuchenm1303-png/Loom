from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent_runtime.browser_native_edit import prepare_edit
from app.agent_runtime.browser_session import BrowserError
from app.agent_runtime.browser_use_backend import BrowserUseBackend


def fake_cdp(outcome=None):
    send = SimpleNamespace(
        DOM=SimpleNamespace(resolveNode=AsyncMock(return_value={"object": {"objectId": "node-17"}})),
        Runtime=SimpleNamespace(
            callFunctionOn=AsyncMock(return_value=outcome or {"result": {"value": True}}),
            releaseObject=AsyncMock(),
        ),
        Input=SimpleNamespace(insertText=AsyncMock(), dispatchKeyEvent=AsyncMock()),
    )
    return SimpleNamespace(cdp_client=SimpleNamespace(send=send), session_id="child-frame"), send


def test_preparation_and_cleanup_are_bound_to_node_frame():
    cdp, send = fake_cdp()
    asyncio.run(prepare_edit(cdp, 17, clear=False))
    send.DOM.resolveNode.assert_awaited_once_with(params={"backendNodeId": 17}, session_id="child-frame")
    assert send.Runtime.callFunctionOn.await_args.kwargs["params"]["arguments"] == [{"value": False}]
    send.Runtime.releaseObject.assert_awaited_once_with(params={"objectId": "node-17"}, session_id="child-frame")


def test_readonly_rejection_cannot_fall_back_to_another_focused_field():
    cdp, send = fake_cdp({"exceptionDetails": {"text": "readonly"}, "result": {"description": "readonly"}})
    with pytest.raises(BrowserError, match="readonly"):
        asyncio.run(prepare_edit(cdp, 17, clear=True))
    send.Input.insertText.assert_not_awaited()
    send.Runtime.releaseObject.assert_awaited_once()


@pytest.mark.parametrize("text,clear", [("中\n🚀", False), ("", True), ("", False)])
def test_type_uses_exact_text_and_browser_default_editing(text, clear):
    cdp, send = fake_cdp()
    backend = object.__new__(BrowserUseBackend)
    backend._node_for_index = AsyncMock(return_value=SimpleNamespace(backend_node_id=17))
    backend._show_page_hud = AsyncMock()
    backend._ensure_session = AsyncMock(return_value=SimpleNamespace(cdp_client_for_node=AsyncMock(return_value=cdp)))
    backend._state_async = AsyncMock(return_value="state")
    assert asyncio.run(backend._type_async(3, text, clear=clear)) == "state"
    if text:
        send.Input.insertText.assert_awaited_once_with(params={"text": text}, session_id="child-frame")
        send.Input.dispatchKeyEvent.assert_not_awaited()
    elif clear:
        assert [call.kwargs["params"]["type"] for call in send.Input.dispatchKeyEvent.await_args_list] == ["keyDown", "keyUp"]
    else:
        send.Input.insertText.assert_not_awaited()
        send.Input.dispatchKeyEvent.assert_not_awaited()
