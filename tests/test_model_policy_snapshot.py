from types import SimpleNamespace
import threading
import time
import pytest

from app.model_policy_snapshot import ModelPolicySnapshot
from app.app_server_reasoning import ReasoningManagedLoomAppServerService


def test_authoritative_snapshot_distinguishes_unknown_retired_and_admin_denied():
    cache = ModelPolicySnapshot("https://example.invalid/access", "account-a", start=False,
        fetch=lambda token: {"access": {"schema_version": 2, "enabled": True,
            "models": ["builtin:minimax"], "decisions": [
                {"model_id": "builtin:minimax:retired", "enabled": False, "source": "catalog"},
                {"model_id": "builtin:deepseek", "enabled": False, "source": "global"},
            ]}})
    cache.refresh()
    for selection in ("builtin:minimax:retired", "builtin:minimax:unknown"):
        with pytest.raises(RuntimeError, match="no longer available"):
            cache.check(selection, "account-a")
    with pytest.raises(RuntimeError, match="disabled by Loom Admin"):
        cache.check("builtin:deepseek", "account-a")


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


@pytest.mark.parametrize("allowed", [True, False])
def test_expired_send_waits_for_refresh_and_uses_new_permissions(allowed):
    now = [0.0]
    cache = ModelPolicySnapshot("https://example.invalid/access", "account-a", start=False,
        clock=lambda: now[0], fetch=lambda token: {"access": {"models": ["builtin:minimax"]}})
    cache.refresh()
    now[0] = 11
    cache.fetch = lambda token: {"access": {"models": ["builtin:minimax"] if allowed else []}}
    errors = []
    def send():
        try:
            cache.check("builtin:minimax", "account-a")
        except RuntimeError as exc:
            errors.append(str(exc))
    pending = threading.Thread(target=send)
    pending.start()
    assert cache.wake.wait(1)
    assert pending.is_alive()
    cache.refresh()
    pending.join(1)
    assert not pending.is_alive()
    assert errors == ([] if allowed else ["This built-in model is disabled by Loom Admin."])


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


def test_builtin_threads_migrate_to_trusted_gateway_before_reuse(monkeypatch):
    service = object.__new__(ReasoningManagedLoomAppServerService)
    service._ensure_thread_model_metadata = lambda session: session
    rebuilt = []
    service.runtime = SimpleNamespace(has_session_model=lambda thread_id: True,
        clear_session_model=lambda thread_id: rebuilt.append(thread_id),
        set_session_model=lambda *args, **kwargs: None)
    service._session_reasoning = lambda session: None
    service._thread_uses_default_model = lambda session: False
    service._runtime_home = lambda: None
    service._model_api_key = lambda spec: "new-account-key"
    monkeypatch.setattr("app.app_server_reasoning.resolve_model_spec", lambda *a, **kw: {"model": "test", "provider": "openai-compatible", "baseUrl": "https://account.smirel.com/model/v1"})
    monkeypatch.setattr("app.app_server_reasoning.validate_runtime_reasoning", lambda **kw: None)
    monkeypatch.setattr("app.app_server_reasoning.build_runtime_model_platform", lambda **kw: object())
    service.vision = True
    service.store = SimpleNamespace(save=lambda session: None)
    checked = []
    service._assert_model_policy = checked.append
    for url in ("https://account.smirel.com/model/v1", "https://other.example/v1"):
        session = SimpleNamespace(session_id="thread", model_selection="builtin:ant-ling", model_base_url=url, model_vision=True)
        service._ensure_thread_model_runtime(session)
    assert checked == []
    assert rebuilt == ["thread", "thread"]
    assert service._gateway_enforces_policy("builtin:ant-ling", "https://account.smirel.com/model/v1")
    assert not service._gateway_enforces_policy("builtin:ant-ling", "https://other.example/v1")


def test_explicit_signout_never_falls_back_to_startup_identity(monkeypatch):
    service = object.__new__(ReasoningManagedLoomAppServerService)
    service._loom_account_model_credential = ""
    monkeypatch.setenv("LOOM_ACCOUNT_MODEL_CREDENTIAL", "old-account")
    with pytest.raises(RuntimeError, match="Sign in"):
        service._assert_model_policy("builtin:minimax")
    with pytest.raises(ValueError, match="required"):
        service._model_api_key({"authMode": "loom-account"})


@pytest.mark.parametrize("selection", ["profile:m123456789abc", "saved:legacy"])
def test_personal_connections_never_use_central_policy(selection):
    service = object.__new__(ReasoningManagedLoomAppServerService)
    service._loom_account_model_credential = "account-a"
    service._policy_snapshot = SimpleNamespace(check=lambda *args: pytest.fail("personal connection reached central policy"))
    service._assert_model_policy(selection)


def test_switch_immediately_invalidates_permission_snapshot():
    cache = ModelPolicySnapshot("https://example.invalid/access", "old", start=False,
        fetch=lambda token: {"access": {"models": ["builtin:minimax"]}})
    cache.refresh()
    cache.set_credential("new")
    assert cache.allowed == frozenset()
    assert cache.expires == 0
    assert not cache.ready.is_set()


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
    cache.set_credential("account-b")
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


def test_execution_cannot_restore_retired_credential():
    cache = ModelPolicySnapshot("https://example.invalid/access", "old", start=False)
    cache.set_credential("new")
    with pytest.raises(RuntimeError, match="account changed"):
        cache.check("builtin:test", "old")
    assert cache.credential == "new"


def test_model_401_requests_recovery_once_per_credential_revision():
    from urllib.error import HTTPError
    notifications = []
    def fetch(token):
        raise HTTPError("https://example.invalid/access", 401, "invalid", {}, None)
    cache = ModelPolicySnapshot("https://example.invalid/access", "a", start=False, fetch=fetch,
        on_authentication_failure=lambda: notifications.append(1))
    cache.refresh(); cache.refresh()
    assert notifications == [1]
    assert not cache.allowed
    cache.set_credential("b"); cache.refresh()
    assert notifications == [1, 1]


@pytest.mark.parametrize("status", [401, 403])
def test_explicit_auth_rejection_invalidates_a_warm_model_lease(status):
    from urllib.error import HTTPError
    cache = ModelPolicySnapshot("https://example.invalid/access", "a", start=False,
        fetch=lambda token: {"access": {"models": ["builtin:test"]}})
    cache.refresh()
    assert cache.allowed == frozenset({"builtin:test"})
    def rejected(token):
        raise HTTPError("https://example.invalid/access", status, "denied", {}, None)
    cache.fetch = rejected
    cache.refresh()
    assert not cache.allowed
    assert cache.expires == 0


def test_malformed_snapshot_cannot_partially_publish_a_fresh_grant():
    cache = ModelPolicySnapshot("https://example.invalid/access", "a", start=False,
        fetch=lambda token: {"access": {"models": ["builtin:test"], "decisions": "invalid"}})
    cache.refresh()
    assert not cache.allowed
    assert cache.expires == 0


def test_partial_credential_delivery_does_not_clear_other_lane(monkeypatch):
    from app.app_server import LoomAppServerService
    from app.agent_runtime import account_tool_access as gate
    monkeypatch.setenv("LOOM_ACCOUNT_AUTOMATION_CREDENTIAL", "")
    service = object.__new__(LoomAppServerService)
    service._guard = threading.RLock()
    service._loom_account_model_credential = "old-model"
    observed = []
    service._policy_snapshot = SimpleNamespace(set_credential=observed.append)
    try:
        service.account_tool_access_credential_set({"credential": "new-account"})
        assert service._loom_account_model_credential == "old-model"
        service.account_tool_access_credential_set({"modelCredential": "new-model"})
        assert gate._credential() == "new-account"
        assert service._loom_account_model_credential == "new-model"
        service.account_tool_access_credential_set({"credential": ""})
        assert gate._credential() == ""
        assert service._loom_account_model_credential == ""
        assert observed == ["new-model", ""]
    finally:
        gate.set_account_tool_access_credential(None)


def test_refresh_from_previous_identity_cannot_publish_after_aba_switch():
    entered, release = threading.Event(), threading.Event()
    def fetch(token):
        entered.set()
        assert release.wait(2)
        return {"access": {"models": ["builtin:test"], "enabled": True}}
    cache = ModelPolicySnapshot("https://example.invalid/access", "a", start=False, fetch=fetch)
    thread = threading.Thread(target=cache.refresh)
    thread.start(); assert entered.wait(1)
    cache.set_credential("b"); cache.set_credential("a")
    release.set(); thread.join(2)
    assert not cache.allowed
    assert not cache.ready.is_set()
