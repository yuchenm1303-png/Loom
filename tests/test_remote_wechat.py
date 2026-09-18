from __future__ import annotations

from pathlib import Path

from app.remote.bridge import WeChatRemoteBridge
from app.remote.state import WeChatRemoteStateStore
from app.remote.wechat_customer_service import WeChatInboundMessage


class FakeWeChat:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, str]] = []

    def send_text(self, external_user_id: str, text: str, *, open_kf_id: str | None = None) -> str:
        self.sent.append((external_user_id, str(open_kf_id or ""), text))
        return f"reply-{len(self.sent)}"


class FakeAppClient:
    def __init__(self) -> None:
        self.listeners = []
        self.requests: list[tuple[str, dict]] = []
        self.turn_starts: list[tuple[str, str]] = []
        self.interrupts: list[tuple[str, str]] = []
        self.threads: dict[str, dict] = {}
        self._next_thread = 0

    def subscribe_notifications(self, listener):
        self.listeners.append(listener)

    def thread_start(self, *, workspace: Path, permission_mode: str | None = None):
        self._next_thread += 1
        thread_id = f"thread-{self._next_thread}"
        self.threads[thread_id] = {
            "thread": {
                "id": thread_id,
                "title": "Remote",
                "status": "idle",
                "currentTurnId": None,
            },
            "pendingApproval": None,
        }
        return {"thread": self.threads[thread_id]["thread"]}

    def thread_read(self, thread_id: str):
        return self.threads[thread_id]

    def turn_start(self, thread_id: str, text: str):
        self.turn_starts.append((thread_id, text))
        self.threads[thread_id]["thread"]["status"] = "running"
        self.threads[thread_id]["thread"]["currentTurnId"] = "turn-1"
        return {"turn": {"id": "turn-1"}}

    def turn_interrupt(self, thread_id: str, turn_id: str):
        self.interrupts.append((thread_id, turn_id))
        return {"requested": True}

    def request(self, method: str, params: dict):
        self.requests.append((method, dict(params)))
        return {"accepted": True}


def _message(
    text: str,
    *,
    msgid: str = "m1",
    user: str = "wx-user",
    send_time: int = 1,
) -> WeChatInboundMessage:
    return WeChatInboundMessage(
        message_id=msgid,
        external_user_id=user,
        open_kf_id="wk-loom",
        text=text,
        send_time=send_time,
        origin=3,
    )


def test_remote_state_persists_binding_cursor_and_dedupe(tmp_path):
    store = WeChatRemoteStateStore(tmp_path)
    assert store.binding is None
    assert store.accept_message("m-1") is True
    assert store.accept_message("m-1") is False

    bound = store.bind("wx-user", "wk-loom", paired_at_unix=123)
    assert bound.external_user_id == "wx-user"
    assert bound.paired_at_unix == 123
    store.set_thread_id("thread-1")
    store.set_cursor("cursor-2")

    reloaded = WeChatRemoteStateStore(tmp_path)
    assert reloaded.binding is not None
    assert reloaded.binding.thread_id == "thread-1"
    assert reloaded.binding.paired_at_unix == 123
    assert reloaded.cursor == "cursor-2"
    assert reloaded.accept_message("m-1") is False


def test_wechat_message_parser_accepts_only_customer_text():
    raw = {
        "msgid": "m-1",
        "open_kfid": "wk-loom",
        "external_userid": "wx-user",
        "send_time": 123,
        "origin": 3,
        "msgtype": "text",
        "text": {"content": "  hello Loom  "},
    }
    message = WeChatInboundMessage.from_api(raw)
    assert message is not None
    assert message.text == "hello Loom"

    assert WeChatInboundMessage.from_api({**raw, "origin": 5}) is None
    assert WeChatInboundMessage.from_api({**raw, "msgtype": "image"}) is None


def test_pairing_then_task_uses_app_server_protocol(tmp_path):
    state = WeChatRemoteStateStore(tmp_path)
    wechat = FakeWeChat()
    app = FakeAppClient()
    bridge = WeChatRemoteBridge(
        app_client=app,
        wechat=wechat,
        state=state,
        workspace=tmp_path,
        pairing_code="731842",
        permission_mode="approval",
    )

    bridge.handle_message(_message("/bind 000000"))
    assert state.binding is None
    assert wechat.sent == []

    bridge.handle_message(_message("/bind 731842", msgid="m2", send_time=100))
    assert state.binding is not None
    assert state.binding.paired_at_unix == 100
    assert "已绑定" in wechat.sent[-1][2]

    bridge.handle_message(_message("检查 Loom 最新 CI", msgid="m3", send_time=101))
    assert state.binding.thread_id == "thread-1"
    assert app.turn_starts == [("thread-1", "检查 Loom 最新 CI")]
    assert "已开始执行" in wechat.sent[-1][2]


def test_running_turn_accepts_live_steering(tmp_path):
    state = WeChatRemoteStateStore(tmp_path)
    state.bind("wx-user", "wk-loom")
    state.set_thread_id("thread-1")
    wechat = FakeWeChat()
    app = FakeAppClient()
    app.threads["thread-1"] = {
        "thread": {
            "id": "thread-1",
            "title": "Remote",
            "status": "running",
            "currentTurnId": "turn-9",
        },
        "pendingApproval": None,
    }
    bridge = WeChatRemoteBridge(
        app_client=app,
        wechat=wechat,
        state=state,
        workspace=tmp_path,
    )

    bridge.handle_message(_message("顺便把测试补上"))
    assert app.requests == [
        (
            "turn/steer",
            {"threadId": "thread-1", "turnId": "turn-9", "input": "顺便把测试补上"},
        )
    ]
    assert "补充到当前任务" in wechat.sent[-1][2]


def test_approval_round_trip_keeps_correlation_fields(tmp_path):
    state = WeChatRemoteStateStore(tmp_path)
    state.bind("wx-user", "wk-loom")
    state.set_thread_id("thread-1")
    wechat = FakeWeChat()
    app = FakeAppClient()
    app.threads["thread-1"] = {
        "thread": {
            "id": "thread-1",
            "title": "Remote",
            "status": "waiting_approval",
            "currentTurnId": "turn-1",
        },
        "pendingApproval": None,
    }
    bridge = WeChatRemoteBridge(
        app_client=app,
        wechat=wechat,
        state=state,
        workspace=tmp_path,
    )

    bridge.on_notification(
        "approval/requested",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "approval": {
                "requestId": "req-1",
                "threadId": "thread-1",
                "turnId": "turn-1",
                "callId": "call-1",
                "toolName": "shell",
                "effect": "sensitive",
                "reason": "writes to GitHub",
                "arguments": {"command": "git push"},
            },
        },
    )
    assert "/allow" in wechat.sent[-1][2]

    bridge.handle_message(_message("/allow", msgid="m2"))
    assert app.requests[-1] == (
        "approval/respond",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "requestId": "req-1",
            "callId": "call-1",
            "decision": "accept",
        },
    )


def test_turn_completion_is_replied_to_paired_user(tmp_path):
    state = WeChatRemoteStateStore(tmp_path)
    state.bind("wx-user", "wk-loom")
    state.set_thread_id("thread-1")
    wechat = FakeWeChat()
    app = FakeAppClient()
    bridge = WeChatRemoteBridge(
        app_client=app,
        wechat=wechat,
        state=state,
        workspace=tmp_path,
    )

    bridge.on_notification(
        "turn/completed",
        {
            "threadId": "thread-1",
            "turn": {
                "id": "turn-1",
                "status": "completed",
                "finalText": "CI 已修复，测试通过。",
                "error": "",
            },
        },
    )
    assert wechat.sent[-1][2] == "CI 已修复，测试通过。"


def test_messages_older_than_pairing_boundary_are_never_executed(tmp_path):
    state = WeChatRemoteStateStore(tmp_path)
    wechat = FakeWeChat()
    app = FakeAppClient()
    bridge = WeChatRemoteBridge(
        app_client=app,
        wechat=wechat,
        state=state,
        workspace=tmp_path,
        pairing_code="731842",
    )

    bridge.handle_message(_message("/bind 731842", msgid="bind", send_time=100))
    assert state.binding is not None

    bridge.handle_message(_message("这是绑定前的历史任务", msgid="old", send_time=99))
    assert app.turn_starts == []

    bridge.handle_message(_message("这是绑定后的新任务", msgid="new", send_time=101))
    assert app.turn_starts == [("thread-1", "这是绑定后的新任务")]


def test_turn_completion_strips_internal_inline_sticker_marker(tmp_path):
    state = WeChatRemoteStateStore(tmp_path)
    state.bind("wx-user", "wk-loom")
    state.set_thread_id("thread-1")
    wechat = FakeWeChat()
    app = FakeAppClient()
    bridge = WeChatRemoteBridge(
        app_client=app,
        wechat=wechat,
        state=state,
        workspace=tmp_path,
    )

    bridge.on_notification(
        "turn/completed",
        {
            "threadId": "thread-1",
            "turn": {
                "id": "turn-1",
                "status": "completed",
                "finalText": "完成 [[AI_LEDGER_INLINE_STICKER:ok]]",
                "error": "",
            },
        },
    )
    assert wechat.sent[-1][2] == "完成"


def test_new_thread_is_rejected_while_current_turn_is_active(tmp_path):
    state = WeChatRemoteStateStore(tmp_path)
    state.bind("wx-user", "wk-loom")
    state.set_thread_id("thread-1")
    wechat = FakeWeChat()
    app = FakeAppClient()
    app.threads["thread-1"] = {
        "thread": {
            "id": "thread-1",
            "title": "Remote",
            "status": "running",
            "currentTurnId": "turn-1",
        },
        "pendingApproval": None,
    }
    bridge = WeChatRemoteBridge(
        app_client=app,
        wechat=wechat,
        state=state,
        workspace=tmp_path,
    )

    bridge.handle_message(_message("/new"))
    assert state.binding.thread_id == "thread-1"
    assert app._next_thread == 0
    assert "不能直接切换会话" in wechat.sent[-1][2]
