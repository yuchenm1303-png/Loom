from __future__ import annotations

import pytest

from app.agent_runtime import AgentRuntime
from app.agent_runtime.browser_backend_intent import (
    BrowserBackendIntentMixin,
    user_explicitly_requests_isolated_browser,
)


class IntentBase:
    def __init__(self, selected: str = "current-browser"):
        self._browser_requested_backend = selected
        self.started: list[str] = []

    def start_turn(self, session_id, user_text, *, turn_id=None):
        self.started.append(str(user_text))
        return (session_id, user_text, turn_id)

    def _backend_model_selectable(self, backend: str) -> bool:
        return backend == self._browser_requested_backend

    def browser_session_connection(self, connect: str, *, cdp_url: str = ""):
        return connect, cdp_url


class Harness(BrowserBackendIntentMixin, IntentBase):
    pass


def test_normal_current_browser_task_cannot_fall_back_to_isolated():
    runtime = Harness("current-browser")
    runtime.start_turn("s1", "打开我的 Cloudflare 控制台", turn_id="t1")

    assert runtime._backend_model_selectable("isolated") is False
    with pytest.raises(PermissionError, match="change browser identity"):
        runtime.browser_session_connection("launch")


def test_user_can_explicitly_request_clean_isolated_browser_in_chinese():
    runtime = Harness("current-browser")
    runtime.start_turn("s1", "开一个干净浏览器，测试未登录状态", turn_id="t1")

    assert runtime._backend_model_selectable("isolated") is True
    assert runtime.browser_session_connection("launch") == ("launch", "")


def test_user_can_explicitly_request_clean_isolated_browser_in_english():
    runtime = Harness("current-browser")
    runtime.start_turn("s1", "Open a clean browser and test the signed-out experience", turn_id="t1")

    assert runtime._backend_model_selectable("isolated") is True
    assert runtime.browser_session_connection("isolated") == ("isolated", "")


def test_isolated_selected_in_settings_does_not_need_turn_keyword():
    runtime = Harness("isolated")
    runtime.start_turn("s1", "打开 example.com", turn_id="t1")

    assert runtime._backend_model_selectable("isolated") is True
    assert runtime.browser_session_connection("launch") == ("launch", "")


def test_intent_detection_is_conservative():
    assert user_explicitly_requests_isolated_browser("用未登录状态测试") is True
    assert user_explicitly_requests_isolated_browser("Use an incognito browser") is True
    assert user_explicitly_requests_isolated_browser("继续操作我现在的 Edge") is False


def test_production_runtime_contains_turn_scoped_backend_intent_guard():
    assert BrowserBackendIntentMixin in AgentRuntime.mro()
