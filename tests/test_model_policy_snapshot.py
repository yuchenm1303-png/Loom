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


def test_gateway_enforcement_is_only_used_for_the_trusted_account_route(monkeypatch):
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
    checked = []
    service._assert_model_policy = checked.append
    for url in ("https://account.smirel.com/model/v1", "https://other.example/v1"):
        session = SimpleNamespace(session_id="thread", model_selection="builtin:ant-ling", model_base_url=url, model_vision=True)
        service._ensure_thread_model_runtime(session)
    assert checked == ["builtin:ant-ling"]
    assert rebuilt == ["thread"]


def test_explicit_signout_never_falls_back_to_startup_identity(monkeypatch):
    service = object.__new__(ReasoningManagedLoomAppServerService)
    service._loom_account_model_credential = ""
    monkeypatch.setenv("LOOM_ACCOUNT_MODEL_CREDENTIAL", "old-account")
    with pytest.raises(RuntimeError, match="Sign in"):
        service._assert_model_policy("builtin:minimax")
    with pytest.raises(ValueError, match="required"):
        service._model_api_key({"authMode": "loom-account"})


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
