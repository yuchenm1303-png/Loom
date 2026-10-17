"""OpenCode Server v2 opt-in agent adapter security and protocol tests."""
from __future__ import annotations

import io
import json
import urllib.error

import pytest

from services.loom_opencode_agent.client import (
    OpenCodeAgentClient,
    OpenCodeAgentConfig,
    OpenCodeAgentError,
)


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        if self._payload is None:
            return b""
        return json.dumps(self._payload).encode()


class _FakeOpener:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append(req)
        return _Response(self.replies.pop(0))


def client(monkeypatch, *replies, enabled=False, models=()):
    fake = _FakeOpener(replies)
    monkeypatch.setattr("services.loom_opencode_agent.client.urllib.request.build_opener", lambda *_: fake)
    cfg = OpenCodeAgentConfig(
        password="test-password",
        enabled=enabled,
        permitted_models=tuple(models),
    )
    return OpenCodeAgentClient(cfg), fake


@pytest.mark.parametrize("url", [
    "http://public.example.com:4096",
    "http://169.254.169.254",
    "file:///tmp/server",
    "https://someone:pw@example.com",
    "https://example.com/hidden/path",
    "https://example.com?url=x",
    "http://127.0.0.1:99999",
])
def test_server_url_restrictions(url):
    with pytest.raises(ValueError):
        OpenCodeAgentConfig(base_url=url)


def test_loopback_and_https_urls_are_supported():
    assert OpenCodeAgentConfig(base_url="http://127.0.0.1:14096").base_url == "http://127.0.0.1:14096"
    assert OpenCodeAgentConfig(base_url="https://trusted.example.com").base_url == "https://trusted.example.com"


def test_status_read_only_and_protocol_check(monkeypatch):
    bridge, fake = client(monkeypatch, {"version": "2.0.31", "pid": 4})
    assert bridge.status() == {"state": "available", "version": "2.0.31", "agent_enabled": "false"}
    assert len(fake.requests) == 1
    req = fake.requests[0]
    assert req.full_url.endswith("/api/info")
    assert req.get_method() == "GET"
    assert req.get_header("Authorization", "").startswith("Basic ")
    bad, _ = client(monkeypatch, {"version": "1.9.0"})
    with pytest.raises(OpenCodeAgentError, match="v2"):
        bad.status()


def test_agent_does_not_create_sessions_until_enabled(monkeypatch):
    bridge, fake = client(monkeypatch, {"id": "ses_test"}, enabled=False, models=("anthropic/claude-test",))
    with pytest.raises(OpenCodeAgentError) as exc:
        bridge.create_session("anthropic/claude-test")
    assert exc.value.code == "OPENCODE_AGENT_DISABLED"
    assert not fake.requests


@pytest.mark.parametrize("model", [
    "opencode/deepseek-v4.1-flash-free",
    "opencode/glm-5.3-flash-free",
    "opencode/paid-model",
    "opencode-go/deepseek",
    "zen/something",
    "another-provider/model-free",
    "another-provider/model:free",
])
def test_no_open_code_hosted_or_free_relay_even_if_approved(monkeypatch, model):
    bridge, fake = client(monkeypatch, {"id": "ses_test"}, enabled=True, models=(model,))
    with pytest.raises(OpenCodeAgentError) as exc:
        bridge.create_session(model)
    assert exc.value.code == "HOSTED_FREE_RELAY_DISABLED"
    assert fake.requests == []


def test_only_explicit_byok_model_can_be_created(monkeypatch):
    bridge, fake = client(monkeypatch, {"id": "ses_safe"}, enabled=True, models=("anthropic/claude-test",))
    with pytest.raises(OpenCodeAgentError) as exc:
        bridge.create_session("google/gemini-unknown")
    assert exc.value.code == "OPENCODE_MODEL_NOT_APPROVED"
    assert not fake.requests
    assert bridge.create_session("anthropic/claude-test") == "ses_safe"
    created = json.loads(fake.requests[0].data)
    assert created["model"] == {"providerID": "anthropic", "id": "claude-test"}
    assert created["permissions"] == [{"action": "*", "resource": "*", "effect": "deny"}]


def test_session_membership_enforced_for_read_write_and_interrupt(monkeypatch):
    bridge, fake = client(
        monkeypatch,
        {"id": "ses_owned"},
        {"data": {"id": "msg_one"}},
        {"data": []},
        [],
        None,
        None,
        enabled=True,
        models=("anthropic/claude-test",),
    )
    bridge.create_session("anthropic/claude-test")
    for callback in (
        lambda: bridge.send_prompt("ses_other", "x"),
        lambda: bridge.get_messages("ses_other"),
        lambda: bridge.get_permissions("ses_other"),
        lambda: bridge.interrupt("ses_other"),
        lambda: bridge.delete_session("ses_other"),
        lambda: bridge.get_messages("../../etc/passwd"),
    ):
        with pytest.raises(OpenCodeAgentError) as exc:
            callback()
        assert exc.value.code == "OPENCODE_SESSION_NOT_OWNED"
    assert len(fake.requests) == 1
    assert bridge.send_prompt("ses_owned", "hello")["data"]["id"] == "msg_one"
    bridge.get_messages("ses_owned")
    bridge.get_permissions("ses_owned")
    bridge.interrupt("ses_owned")
    bridge.delete_session("ses_owned")
    with pytest.raises(OpenCodeAgentError):
        bridge.get_messages("ses_owned")
    assert len(fake.requests) == 6


def test_server_key_missing_never_falls_back_to_unprotected_request():
    bridge = OpenCodeAgentClient(OpenCodeAgentConfig(password=""))
    with pytest.raises(OpenCodeAgentError) as exc:
        bridge.status()
    assert exc.value.code == "OPENCODE_SERVER_NOT_CONFIGURED"


def test_redirects_are_forbidden_even_with_server_secret():
    from services.loom_opencode_agent.client import _NoRedirect
    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.site") is None


def test_long_prompts_rejected_before_network(monkeypatch):
    bridge, fake = client(monkeypatch, {"id": "ses_safe"}, enabled=True, models=("anthropic/claude-test",))
    bridge.create_session("anthropic/claude-test")
    with pytest.raises(OpenCodeAgentError) as exc:
        bridge.send_prompt("ses_safe", "x" * 16001)
    assert exc.value.code == "INVALID_PROMPT"
    assert len(fake.requests) == 1
