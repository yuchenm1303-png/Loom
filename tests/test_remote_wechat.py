from __future__ import annotations

from pathlib import Path

import pytest

import app.remote.channels.weixin.state as weixin_state_module
from app.remote.base import RemoteMessage, sanitize_remote_text
from app.remote.channels.weixin.api import (
    GetUpdatesResponse,
    WeixinApiClient,
    WeixinAuthenticationExpired,
    WeixinCredentials,
    WeixinInboundMessage,
    WeixinQrCode,
    WeixinQrStatus,
    redact_sensitive,
)
from app.remote.channels.weixin.auth import WeixinQrAuthenticator
from app.remote.channels.weixin.channel import WeixinChannel
from app.remote.channels.weixin.monitor import WeixinMonitor
from app.remote.channels.weixin.state import (
    WeixinCredentialStore,
    WeixinRemoteStateStore,
)
from app.remote.service import LoomRemoteService
from loom_remote_wechat import _restore_credentials


class FakeRemoteChannel:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_text(self, text: str) -> None:
        self.sent.append(text)


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


def _bound_state(tmp_path: Path, *, bound_at_ms: int = 1000) -> WeixinRemoteStateStore:
    state = WeixinRemoteStateStore(tmp_path)
    state.bind_login(
        "bot@im.bot",
        "user@im.wechat",
        "https://ilink.example",
        bound_at_ms=bound_at_ms,
    )
    return state


def _remote_message(text: str, *, message_id: str = "m1") -> RemoteMessage:
    return RemoteMessage(
        message_id=message_id,
        sender_id="user@im.wechat",
        text=text,
        created_at_ms=1100,
    )


def _inbound(
    message_id: str,
    *,
    user: str = "user@im.wechat",
    text: str = "hello",
    created_at_ms: int = 1100,
    context_token: str = "ctx-1",
    message_type: int = 1,
    item_types: tuple[int, ...] = (1,),
) -> WeixinInboundMessage:
    return WeixinInboundMessage(
        message_id=message_id,
        from_user_id=user,
        text=text,
        context_token=context_token,
        create_time_ms=created_at_ms,
        message_type=message_type,
        item_types=item_types,
    )


def _service(tmp_path: Path):
    state = _bound_state(tmp_path)
    channel = FakeRemoteChannel()
    app = FakeAppClient()
    service = LoomRemoteService(
        app_client=app,
        channel=channel,
        state=state,
        workspace=tmp_path,
        permission_mode="approval",
    )
    return state, channel, app, service


def test_remote_state_persists_binding_cursor_dedupe_and_thread(tmp_path):
    state = _bound_state(tmp_path)
    state.set_thread_id("thread-1")
    state.set_get_updates_buf("cursor-2")
    state.mark_history_ready()
    assert state.accept_message("m-1") is True
    assert state.accept_message("m-1") is False

    reloaded = WeixinRemoteStateStore(tmp_path)
    assert reloaded.binding is not None
    assert reloaded.binding.ilink_bot_id == "bot@im.bot"
    assert reloaded.binding.ilink_user_id == "user@im.wechat"
    assert reloaded.thread_id == "thread-1"
    assert reloaded.get_updates_buf == "cursor-2"
    assert reloaded.history_ready is True
    assert reloaded.accept_message("m-1") is False


def test_token_is_stored_in_keyring_and_restart_recovers_login(tmp_path, monkeypatch):
    memory: dict[tuple[str, str], str] = {}

    monkeypatch.setattr(
        weixin_state_module.keyring,
        "set_password",
        lambda service, username, password: memory.__setitem__((service, username), password),
    )
    monkeypatch.setattr(
        weixin_state_module.keyring,
        "get_password",
        lambda service, username: memory.get((service, username)),
    )

    state = _bound_state(tmp_path)
    store = WeixinCredentialStore()
    store.set_bot_token("bot@im.bot", "bot-secret-token")

    reloaded_state = WeixinRemoteStateStore(tmp_path)
    recovered = _restore_credentials(reloaded_state, WeixinCredentialStore())
    assert recovered is not None
    assert recovered.bot_token == "bot-secret-token"
    assert recovered.ilink_user_id == "user@im.wechat"
    assert "bot-secret-token" not in reloaded_state.path.read_text(encoding="utf-8")


class FakeQrApi:
    def __init__(self, statuses: list[WeixinQrStatus]) -> None:
        self.statuses = list(statuses)
        self.qr_count = 0
        self.polls: list[tuple[str, str]] = []

    def get_bot_qrcode(self, *, local_token_list=(), bot_type="3"):
        self.qr_count += 1
        return WeixinQrCode(
            qrcode=f"qr-{self.qr_count}",
            image_content=f"https://qr.example/{self.qr_count}",
        )

    def get_qrcode_status(
        self,
        qrcode: str,
        *,
        base_url: str,
        verify_code: str = "",
        timeout_seconds: float = 35.0,
    ):
        self.polls.append((base_url, verify_code))
        return self.statuses.pop(0)


def test_qr_login_state_machine_handles_verify_redirect_and_confirm():
    api = FakeQrApi(
        [
            WeixinQrStatus(status="need_verifycode"),
            WeixinQrStatus(status="scaned_but_redirect", redirect_host="idc.example"),
            WeixinQrStatus(status="scaned"),
            WeixinQrStatus(
                status="confirmed",
                bot_token="secret-token",
                ilink_bot_id="bot@im.bot",
                base_url="https://idc.example",
                ilink_user_id="user@im.wechat",
            ),
        ]
    )
    displayed: list[str] = []
    auth = WeixinQrAuthenticator(api, sleep=lambda _seconds: None)

    credentials = auth.login(
        on_qr=lambda qr: displayed.append(qr.image_content),
        verify_code_provider=lambda _prompt: "246810",
    )

    assert displayed == ["https://qr.example/1"]
    assert credentials.bot_token == "secret-token"
    assert credentials.ilink_bot_id == "bot@im.bot"
    assert credentials.ilink_user_id == "user@im.wechat"
    assert api.polls[1][1] == "246810"
    assert api.polls[2][0] == "https://idc.example"


def test_qr_login_refreshes_expired_code():
    api = FakeQrApi(
        [
            WeixinQrStatus(status="expired"),
            WeixinQrStatus(
                status="confirmed",
                bot_token="new-secret",
                ilink_bot_id="bot@im.bot",
                base_url="https://ilink.example",
                ilink_user_id="user@im.wechat",
            ),
        ]
    )
    displayed: list[str] = []
    auth = WeixinQrAuthenticator(api, sleep=lambda _seconds: None)
    result = auth.login(on_qr=lambda qr: displayed.append(qr.qrcode))
    assert result.bot_token == "new-secret"
    assert api.qr_count == 2
    assert displayed == ["qr-1", "qr-2"]


class FakeUpdatesApi:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requested_cursors: list[str] = []

    def get_updates(self, credentials, *, get_updates_buf="", timeout_ms=35000):
        self.requested_cursors.append(get_updates_buf)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _credentials() -> WeixinCredentials:
    return WeixinCredentials(
        bot_token="secret",
        ilink_bot_id="bot@im.bot",
        base_url="https://ilink.example",
        ilink_user_id="user@im.wechat",
    )


def test_monitor_persists_cursor_filters_binding_history_nontext_and_dedupes(tmp_path):
    state = _bound_state(tmp_path, bound_at_ms=1000)
    delivered: list[str] = []
    api = FakeUpdatesApi(
        [
            GetUpdatesResponse(
                messages=(
                    _inbound("old", created_at_ms=999),
                    _inbound("other", user="someone-else", created_at_ms=1100),
                    _inbound("image", text="", created_at_ms=1101, item_types=(2,)),
                    _inbound("fresh", text="run this", created_at_ms=1102),
                ),
                get_updates_buf="cursor-1",
                longpolling_timeout_ms=41000,
            ),
            GetUpdatesResponse(
                messages=(
                    _inbound("fresh", text="duplicate", created_at_ms=1102),
                    _inbound("next", text="second", created_at_ms=1103),
                ),
                get_updates_buf="cursor-2",
                longpolling_timeout_ms=42000,
            ),
        ]
    )

    def on_message(message: WeixinInboundMessage):
        assert state.get_updates_buf in {"cursor-1", "cursor-2"}
        delivered.append(message.message_id)

    monitor = WeixinMonitor(
        api=api,
        state=state,
        credentials=_credentials(),
        on_message=on_message,
        sleep=lambda _seconds: None,
    )
    monitor.poll_once()
    assert state.get_updates_buf == "cursor-1"
    assert state.history_ready is True
    assert delivered == ["fresh"]

    monitor.poll_once()
    assert api.requested_cursors == ["", "cursor-1"]
    assert state.get_updates_buf == "cursor-2"
    assert delivered == ["fresh", "next"]

    reloaded = WeixinRemoteStateStore(tmp_path)
    assert reloaded.accept_message("fresh") is False
    assert reloaded.accept_message("next") is False


def test_monitor_records_cursor_and_dedupe_before_dispatch_for_at_most_once(tmp_path):
    state = _bound_state(tmp_path, bound_at_ms=1000)
    response = GetUpdatesResponse(
        messages=(_inbound("crash-me", created_at_ms=1100),),
        get_updates_buf="cursor-crash",
        longpolling_timeout_ms=35000,
    )
    api = FakeUpdatesApi([response])

    def crash_after_accept(_message):
        raise RuntimeError("simulated dispatch crash")

    monitor = WeixinMonitor(
        api=api,
        state=state,
        credentials=_credentials(),
        on_message=crash_after_accept,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(RuntimeError):
        monitor.poll_once()

    reloaded = WeixinRemoteStateStore(tmp_path)
    assert reloaded.get_updates_buf == "cursor-crash"
    assert reloaded.accept_message("crash-me") is False


def test_monitor_bubbles_stale_token_for_qr_relogin(tmp_path):
    state = _bound_state(tmp_path)
    api = FakeUpdatesApi([WeixinAuthenticationExpired("expired", errcode=-14)])
    monitor = WeixinMonitor(
        api=api,
        state=state,
        credentials=_credentials(),
        on_message=lambda _message: None,
        sleep=lambda _seconds: None,
    )
    with pytest.raises(WeixinAuthenticationExpired):
        monitor.poll_once()


def test_idle_message_starts_turn_and_acknowledges_immediately(tmp_path):
    state, channel, app, service = _service(tmp_path)
    service.handle_message(_remote_message("检查 Loom 最新 CI"))
    assert state.thread_id == "thread-1"
    assert app.turn_starts == [("thread-1", "检查 Loom 最新 CI")]
    assert channel.sent[-1] == "🟢 收到，Loom 已开始执行。"


def test_running_turn_uses_turn_steer(tmp_path):
    state, channel, app, service = _service(tmp_path)
    state.set_thread_id("thread-1")
    app.threads["thread-1"] = {
        "thread": {
            "id": "thread-1",
            "title": "Remote",
            "status": "running",
            "currentTurnId": "turn-9",
        },
        "pendingApproval": None,
    }
    service.handle_message(_remote_message("顺便把测试补上"))
    assert app.requests == [
        (
            "turn/steer",
            {"threadId": "thread-1", "turnId": "turn-9", "input": "顺便把测试补上"},
        )
    ]
    assert "补充到当前任务" in channel.sent[-1]


@pytest.mark.parametrize(
    ("command", "decision"),
    [("/allow", "accept"), ("/deny", "decline")],
)
def test_approval_allow_and_deny_round_trip(tmp_path, command, decision):
    state, channel, app, service = _service(tmp_path)
    state.set_thread_id("thread-1")
    app.threads["thread-1"] = {
        "thread": {
            "id": "thread-1",
            "title": "Remote",
            "status": "waiting_approval",
            "currentTurnId": "turn-1",
        },
        "pendingApproval": None,
    }
    service.on_notification(
        "approval/requested",
        {
            "threadId": "thread-1",
            "approval": {
                "requestId": "req-1",
                "threadId": "thread-1",
                "turnId": "turn-1",
                "callId": "call-1",
                "toolName": "shell",
                "effect": "sensitive",
                "reason": "writes files",
                "arguments": {"command": "git status"},
            },
        },
    )
    assert "/allow" in channel.sent[-1]
    service.handle_message(_remote_message(command))
    assert app.requests[-1] == (
        "approval/respond",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "requestId": "req-1",
            "callId": "call-1",
            "decision": decision,
        },
    )


def test_stop_interrupts_active_turn(tmp_path):
    state, channel, app, service = _service(tmp_path)
    state.set_thread_id("thread-1")
    app.threads["thread-1"] = {
        "thread": {
            "id": "thread-1",
            "title": "Remote",
            "status": "running",
            "currentTurnId": "turn-1",
        },
        "pendingApproval": None,
    }
    service.handle_message(_remote_message("/stop"))
    assert app.interrupts == [("thread-1", "turn-1")]
    assert "停止请求" in channel.sent[-1]


def test_new_is_rejected_while_running_or_waiting_approval(tmp_path):
    state, channel, app, service = _service(tmp_path)
    state.set_thread_id("thread-1")
    app.threads["thread-1"] = {
        "thread": {
            "id": "thread-1",
            "title": "Remote",
            "status": "running",
            "currentTurnId": "turn-1",
        },
        "pendingApproval": None,
    }
    service.handle_message(_remote_message("/new"))
    assert state.thread_id == "thread-1"
    assert app._next_thread == 0
    assert "不能直接切换会话" in channel.sent[-1]

    app.threads["thread-1"]["thread"]["status"] = "waiting_approval"
    service.handle_message(_remote_message("/new", message_id="m2"))
    assert state.thread_id == "thread-1"
    assert app._next_thread == 0


def test_final_reply_strips_all_internal_sticker_protocol_markers(tmp_path):
    state, channel, app, service = _service(tmp_path)
    state.set_thread_id("thread-1")
    service.on_notification(
        "turn/completed",
        {
            "threadId": "thread-1",
            "turn": {
                "id": "turn-1",
                "status": "completed",
                "finalText": (
                    "完成 [[AI_LEDGER_INLINE_STICKER:ok]] "
                    "[[AI_LEDGER_STICKER_PLAN_V1_BEGIN]]secret-plan"
                    "[[AI_LEDGER_STICKER_PLAN_V1_END]]"
                ),
                "error": "",
            },
        },
    )
    assert channel.sent[-1] == "完成"
    assert "AI_LEDGER" not in channel.sent[-1]


class FakeSendApi:
    def __init__(self) -> None:
        self.calls = []

    def send_text(self, credentials, *, to_user_id, context_token, text):
        self.calls.append((credentials, to_user_id, context_token, text))
        return ("client-1",)


class FakeService:
    def __init__(self) -> None:
        self.messages: list[RemoteMessage] = []

    def handle_message(self, message: RemoteMessage) -> None:
        self.messages.append(message)


def test_channel_requires_bound_user_and_uses_inbound_context_token(tmp_path):
    state = _bound_state(tmp_path)
    api = FakeSendApi()
    channel = WeixinChannel(
        api=api,
        credentials=_credentials(),
        state=state,
    )
    service = FakeService()
    channel.attach_service(service)

    channel.handle_inbound(_inbound("wrong", user="attacker@im.wechat", context_token="bad"))
    assert service.messages == []

    channel.handle_inbound(_inbound("right", text="task", context_token="ctx-live"))
    assert service.messages[-1].text == "task"
    channel.send_text("reply")
    assert api.calls[-1][1:] == ("user@im.wechat", "ctx-live", "reply")


def test_channel_does_not_execute_text_without_context_token(tmp_path):
    state = _bound_state(tmp_path)
    channel = WeixinChannel(
        api=FakeSendApi(),
        credentials=_credentials(),
        state=state,
    )
    service = FakeService()
    channel.attach_service(service)
    channel.handle_inbound(_inbound("m1", context_token=""))
    assert service.messages == []


def test_long_reply_is_utf8_safe_and_each_chunk_has_unique_client_id(monkeypatch):
    client = WeixinApiClient()
    requests: list[dict] = []

    def fake_request(method, path, **kwargs):
        requests.append(kwargs["body"])
        return {"ret": 0, "message_id": str(len(requests))}

    monkeypatch.setattr(client, "_request", fake_request)
    text = "这一段很长。\n" * 1500
    client_ids = client.send_text(
        _credentials(),
        to_user_id="user@im.wechat",
        context_token="ctx-secret",
        text=text,
    )

    assert len(client_ids) > 1
    assert len(client_ids) == len(set(client_ids))
    for body in requests:
        msg = body["msg"]
        assert msg["context_token"] == "ctx-secret"
        assert msg["message_type"] == 2
        assert msg["message_state"] == 2
        chunk = msg["item_list"][0]["text_item"]["text"]
        assert len(chunk.encode("utf-8")) <= 3000


def test_sensitive_values_are_redacted_from_diagnostics():
    raw = (
        '{"bot_token":"bot-secret","context_token":"ctx-secret",'
        '"authorization":"Bearer nested-secret"} '
        "Authorization: Bearer header-secret "
        "https://example.test?qrcode=qr-secret&verify_code=246810"
    )
    clean = redact_sensitive(raw)
    for secret in ["bot-secret", "ctx-secret", "nested-secret", "header-secret", "qr-secret", "246810"]:
        assert secret not in clean


def test_sanitize_remote_text_removes_unfinished_internal_plan():
    clean = sanitize_remote_text(
        "用户可见内容\n[[AI_LEDGER_STICKER_PLAN_V1_BEGIN]]should-never-leak"
    )
    assert clean == "用户可见内容"
