from types import SimpleNamespace
import threading
import time
import pytest

from app.model_policy_snapshot import ModelPolicySnapshot
from app.app_server_reasoning import ReasoningManagedLoomAppServerService


@pytest.mark.parametrize("enabled", [True, False])
def test_dynamic_model_uses_authoritative_check_and_refresh_invalidates_cache(monkeypatch, enabled):
    import io
    import json
    selection = "builtin:minimax:future-model"
    requests = []
    def urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"decision": {"model_id": selection, "enabled": enabled}}).encode())
    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    cache = ModelPolicySnapshot("https://example.invalid/access", "account-a", start=False,
        fetch=lambda token: {"access": {"enabled": True, "models": ["builtin:minimax"], "decisions": []}})
    cache.refresh()
    for _ in range(2):
        if enabled:
            cache.check(selection, "account-a")
        else:
            with pytest.raises(RuntimeError, match="disabled"):
                cache.check(selection, "account-a")
    assert requests == [{"model_id": selection}]
    cache.refresh()
    assert cache.dynamic == {}


def test_warm_sends_do_not_request_network_and_revocation_is_applied():
    requests = []
    allowed = ["builtin:minimax"]
    now = [0.0]

    def fetch(token):
        requests.append(token)
        return {"access": {"enabled": True, "models": list(allowed)}}

    cache = ModelPolicySnapshot("https://example.invalid/access", "account-a", fetch=fetch, clock=lambda: now[0], start=False)
    cache.refresh()
    for _ in range(100):
        cache.check("builtin:minimax", "account-a")
    assert requests == ["account-a"]
    allowed.clear()
    cache.refresh()
    with pytest.raises(RuntimeError, match="disabled"):
        cache.check("builtin:minimax", "account-a")


def test_outage_cannot_extend_permission_lease():
    now = [0.0]
    cache = ModelPolicySnapshot("https://example.invalid/access", "account-a", fetch=lambda token: {"access": {"models": ["builtin:minimax"]}}, clock=lambda: now[0], start=False)
    cache.refresh()
    def unavailable(token):
        raise OSError("offline")
    cache.fetch = unavailable
    now[0] = 8
    cache.refresh()
    cache.check("builtin:minimax", "account-a")
    now[0] = 10
    with pytest.raises(RuntimeError, match="refreshed"):
        cache.check("builtin:minimax", "account-a")


def test_slow_refresh_does_not_extend_lease_from_response_time():
    now = [0.0]
    def slow(token):
        now[0] = 11
        return {"access": {"models": ["builtin:minimax"]}}
    cache = ModelPolicySnapshot("https://example.invalid/access", "account-a", fetch=slow, clock=lambda: now[0], start=False)
    cache.refresh()
    with pytest.raises(RuntimeError, match="expired"):
        cache.check("builtin:minimax", "account-a")


def test_gateway_enforcement_is_only_used_for_the_trusted_account_route():
    service = object.__new__(ReasoningManagedLoomAppServerService)
    service._ensure_thread_model_metadata = lambda session: session
    service.runtime = SimpleNamespace(has_session_model=lambda thread_id: True)
    checked = []
    service._assert_model_policy = checked.append
    for url in ("https://account.smirel.com/model/v1", "https://other.example/v1"):
        session = SimpleNamespace(session_id="thread", model_selection="builtin:ant-ling", model_base_url=url)
        service._ensure_thread_model_runtime(session)
    assert checked == ["builtin:ant-ling"]


def test_account_switch_cannot_reuse_previous_accounts_grants():
    started = threading.Event()
    release = threading.Event()
    def fetch(token):
        if token == "account-a":
            started.set()
            assert release.wait(2)
            return {"access": {"models": ["builtin:minimax"]}}
        return {"access": {"models": []}}
    cache = ModelPolicySnapshot("https://example.invalid/access", "account-a", fetch=fetch, start=False)
    old = threading.Thread(target=cache.refresh)
    old.start()
    assert started.wait(1)
    errors = []
    def switched_check():
        try:
            cache.check("builtin:minimax", "account-b")
        except RuntimeError as exc:
            errors.append(str(exc))
    switched = threading.Thread(target=switched_check)
    switched.start()
    deadline = time.monotonic() + 1
    while cache.credential != "account-b" and time.monotonic() < deadline:
        time.sleep(.001)
    assert cache.credential == "account-b"
    cache.refresh()
    release.set()
    old.join(2)
    switched.join(2)
    assert errors == ["This built-in model is disabled by Loom Admin."]
    assert cache.allowed == frozenset()
