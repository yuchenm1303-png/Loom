"""Web Search settings: provider choice, credential storage, and status.

These cover ``app/web_search_settings.py``, which is what the desktop settings
page drives through the ``web_search/status``, ``web_search/configure`` and
``web_search/test`` App Server methods. Nothing here touches the network: the
providers are either keyless (DuckDuckGo) or injected fakes.

The recurring assertion in this file is the one about *not* leaking: the module
promises that no status, test result, or settings snapshot ever carries the API
key, so several tests search the whole payload for the secret rather than
checking a single field.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_runtime import (
    FileAgentSessionStore,
    SandboxManager,
    SandboxPolicy,
    WebSearchResponse,
    WebSearchResult,
    WebSearchRuntime,
)
from app.agent_runtime.workspace_tools import loom_default_tools
from app.settings import LoomSettingsStore
from app.web_search_settings import (
    WEB_SEARCH_PROVIDER_CHOICES,
    WebSearchConfigurator,
    WebSearchCredentialVault,
)

SECRET = "tvly-live-do-not-leak-0123456789"


class _Platform:
    """The configurator never runs a turn, so this only has to exist."""

    def execute_chat(self, profile_id, request):  # pragma: no cover - never called
        raise AssertionError("web search settings tests must not run a model turn")


class _Vault:
    """In-memory stand-in for the OS credential store.

    Uses the real ``key_name`` so the per-provider scoping is exercised rather
    than assumed.
    """

    def __init__(self, entries: dict[str, str] | None = None) -> None:
        self.entries: dict[str, str] = dict(entries or {})

    def get(self, provider: str) -> str:
        return self.entries.get(WebSearchCredentialVault.key_name(provider), "")

    def set(self, provider: str, value: str) -> None:
        secret = str(value or "").strip()
        if not secret:
            raise ValueError("web search API key must not be empty")
        self.entries[WebSearchCredentialVault.key_name(provider)] = secret

    def delete(self, provider: str) -> None:
        self.entries.pop(WebSearchCredentialVault.key_name(provider), None)

    def configured(self, provider: str) -> bool:
        return bool(self.get(provider))


class _FakeProvider:
    """A provider whose ``search`` is observable and never hits the network."""

    provider_name = "fake"

    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[tuple[str, int]] = []
        self.fail = fail

    def search(self, query: str, *, count: int = 8) -> WebSearchResponse:
        self.calls.append((query, count))
        if self.fail:
            raise RuntimeError("upstream exploded")
        return WebSearchResponse(
            provider=self.provider_name,
            query=query,
            results=(
                WebSearchResult(
                    title="Loom result",
                    url="https://example.com/loom",
                    snippet="A current search result for Loom.",
                    source="example.com",
                ),
            ),
            request_id="req-fake",
        )


def _runtime(tmp_path: Path) -> WebSearchRuntime:
    return WebSearchRuntime(
        platform=_Platform(),
        store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(),
        sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        auto_configure_web_search=False,
    )


def _configurator(
    runtime: WebSearchRuntime,
    tmp_path: Path,
    *,
    vault: _Vault | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[WebSearchConfigurator, LoomSettingsStore]:
    """An explicit empty ``environ`` keeps the developer's own shell out of the
    test: otherwise an exported TAVILY_API_KEY would silently change the result."""

    store = LoomSettingsStore(tmp_path)
    configurator = WebSearchConfigurator(
        runtime,
        store,
        vault=vault if vault is not None else _Vault(),
        environ={} if environ is None else environ,
    )
    return configurator, store


# --- preferences -----------------------------------------------------------


def test_an_unknown_choice_cannot_be_persisted_at_all(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        _, store = _configurator(runtime, tmp_path)
        # app/settings.py declares webSearch.provider with an allowed-value set,
        # so the invalid state is unreachable through the normal write path.
        with pytest.raises(ValueError):
            store.set_value("webSearch.provider", "not-a-provider")
    finally:
        runtime.close()


def test_unknown_choice_in_a_hand_edited_snapshot_falls_back_to_auto(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        # settings.json is a plain file the user can edit, and an older build may
        # have written a choice this build no longer knows. preferences() is the
        # second line of defence, so it is fed the snapshot directly.
        class _SnapshotStore:
            def snapshot(self):
                return {"webSearch": {"provider": "not-a-provider"}}

        configurator = WebSearchConfigurator(
            runtime,
            _SnapshotStore(),
            vault=_Vault(),
            environ={},
        )
        assert configurator.preferences()["provider"] == "auto"
    finally:
        runtime.close()


def test_every_documented_choice_round_trips(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, store = _configurator(runtime, tmp_path)
        for choice in WEB_SEARCH_PROVIDER_CHOICES:
            store.set_value("webSearch.provider", choice)
            assert configurator.preferences()["provider"] == choice
    finally:
        runtime.close()


# --- resolution ------------------------------------------------------------


def test_auto_without_any_key_falls_back_to_the_keyless_provider(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, _ = _configurator(runtime, tmp_path)
        status = configurator.apply()

        assert status["provider"] == "duckduckgo"
        assert status["state"] == "ready"
        assert status["keyRequired"] is False
        assert status["keyConfigured"] is False
        assert status["keySource"] == "none"
    finally:
        runtime.close()


def test_keyed_provider_without_a_key_fails_closed_with_a_reason(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, store = _configurator(runtime, tmp_path)
        store.set_value("webSearch.provider", "tavily")
        status = configurator.apply()

        # No provider at all, rather than a silent fallback to DuckDuckGo: the
        # user asked for Tavily and must be told it is not usable yet.
        assert status["enabled"] is False
        assert status["provider"] == "disabled"
        assert status["state"] == "not_configured"
        assert status["keyRequired"] is True
        assert status["keyConfigured"] is False
        assert "API key" in str(status["reason"])
    finally:
        runtime.close()


def test_stored_key_is_used_and_reported_as_the_credential_store(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, store = _configurator(runtime, tmp_path, vault=_Vault({"tavily/api-key": SECRET}))
        store.set_value("webSearch.provider", "tavily")
        status = configurator.apply()

        assert status["provider"] == "tavily"
        assert status["state"] == "ready"
        assert status["keySource"] == "keyring"
        assert status["keyConfigured"] is True
    finally:
        runtime.close()


def test_environment_key_is_used_when_nothing_is_stored(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, store = _configurator(
            runtime,
            tmp_path,
            environ={"TAVILY_API_KEY": SECRET},
        )
        store.set_value("webSearch.provider", "tavily")
        status = configurator.apply()

        assert status["provider"] == "tavily"
        assert status["keySource"] == "environment"
        assert status["keyConfigured"] is True
    finally:
        runtime.close()


def test_stored_key_wins_over_the_environment(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, store = _configurator(
            runtime,
            tmp_path,
            vault=_Vault({"brave/api-key": SECRET}),
            environ={"BRAVE_SEARCH_API_KEY": "env-key-should-lose"},
        )
        store.set_value("webSearch.provider", "brave")
        status = configurator.apply()

        assert status["keySource"] == "keyring"
    finally:
        runtime.close()


def test_switching_provider_does_not_reuse_the_previous_providers_key(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        # A key stored for Tavily must not silently satisfy Brave.
        configurator, store = _configurator(runtime, tmp_path, vault=_Vault({"tavily/api-key": SECRET}))
        store.set_value("webSearch.provider", "brave")
        status = configurator.apply()

        assert status["enabled"] is False
        assert status["state"] == "not_configured"
        assert status["keyConfigured"] is False
    finally:
        runtime.close()


def test_off_removes_the_web_search_tool(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, store = _configurator(runtime, tmp_path)
        store.set_value("webSearch.provider", "off")
        status = configurator.apply()

        assert status["state"] == "disabled"
        assert status["enabled"] is False
        assert runtime.tools.get("web_search") is None
        # The status tool stays exposed so the model can still report why.
        assert runtime.tools.get("web_search_status") is not None
    finally:
        runtime.close()


def test_installing_a_provider_swaps_the_tool_family_in_place(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, store = _configurator(runtime, tmp_path, vault=_Vault({"tavily/api-key": SECRET}))

        store.set_value("webSearch.provider", "tavily")
        configurator.apply()
        assert runtime.tools.get("web_search") is not None

        store.set_value("webSearch.provider", "off")
        configurator.apply()
        assert runtime.tools.get("web_search") is None

        store.set_value("webSearch.provider", "duckduckgo")
        configurator.apply()
        assert runtime.tools.get("web_search") is not None
    finally:
        runtime.close()


# --- configure -------------------------------------------------------------


def test_configure_persists_the_choice_and_applies_it_live(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, _ = _configurator(runtime, tmp_path)
        result = configurator.configure(provider="duckduckgo")

        assert result["status"]["choice"] == "duckduckgo"
        assert result["status"]["provider"] == "duckduckgo"
        # The persisted snapshot must carry the choice...
        assert result["settings"]["webSearch"]["provider"] == "duckduckgo"
        # ...and the live runtime must already agree, without a restart.
        assert runtime.web_search_status()["provider"] == "duckduckgo"
    finally:
        runtime.close()


def test_configure_stores_a_key_for_a_keyed_provider(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        vault = _Vault()
        configurator, _ = _configurator(runtime, tmp_path, vault=vault)
        result = configurator.configure(provider="tavily", api_key=SECRET)

        assert vault.get("tavily") == SECRET
        assert result["status"]["keySource"] == "keyring"
        assert result["status"]["keyConfigured"] is True
    finally:
        runtime.close()


def test_configure_refuses_a_key_for_a_provider_that_has_no_key(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, _ = _configurator(runtime, tmp_path)
        with pytest.raises(ValueError):
            configurator.configure(provider="duckduckgo", api_key=SECRET)
    finally:
        runtime.close()


def test_configure_refuses_an_unknown_provider(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, _ = _configurator(runtime, tmp_path)
        with pytest.raises(ValueError):
            configurator.configure(provider="hooli")
    finally:
        runtime.close()


def test_clear_key_removes_every_stored_key(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        vault = _Vault()
        configurator, _ = _configurator(runtime, tmp_path, vault=vault)
        configurator.configure(provider="tavily", api_key=SECRET)
        assert vault.configured("tavily") is True

        result = configurator.configure(clear_key=True)

        assert vault.configured("tavily") is False
        assert vault.configured("brave") is False
        assert result["status"]["keyConfigured"] is False
    finally:
        runtime.close()


# --- secret containment ----------------------------------------------------


def test_no_status_or_settings_payload_carries_the_key(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, _ = _configurator(runtime, tmp_path)
        result = configurator.configure(provider="tavily", api_key=SECRET)

        # Search the whole serialised payload, not one field: the promise is
        # that no part of the status reaches the renderer with key material in it.
        blob = repr(result)
        assert SECRET not in blob
        # A prefix or a length is just as identifying as the whole key.
        assert SECRET[:8] not in blob
    finally:
        runtime.close()


# --- test search -----------------------------------------------------------


def test_test_reports_failure_when_nothing_is_configured(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, store = _configurator(runtime, tmp_path)
        store.set_value("webSearch.provider", "off")
        configurator.apply()

        result = configurator.test("anything")

        assert result["ok"] is False
        assert result["provider"] == "disabled"
        assert result["resultCount"] == 0
        assert result["state"] == "not_configured"
    finally:
        runtime.close()


def test_test_reports_latency_and_result_count_from_a_live_provider(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, _ = _configurator(runtime, tmp_path)
        provider = _FakeProvider()
        runtime.web_search_install_provider(provider, choice="auto", key_source="none")

        result = configurator.test("Loom")

        assert result["ok"] is True
        assert result["provider"] == "fake"
        assert result["resultCount"] == 1
        assert result["state"] == "ready"
        assert result["latencyMs"] >= 0
        assert provider.calls == [("Loom", 5)]
        # The configurator reports only the search facts; the App Server wrapper
        # (`web_search_test`) is what attaches the refreshed status to the reply.
        assert "status" not in result
    finally:
        runtime.close()


def test_test_reports_a_provider_error_without_raising(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, _ = _configurator(runtime, tmp_path)
        runtime.web_search_install_provider(_FakeProvider(fail=True), choice="auto", key_source="none")

        result = configurator.test("Loom")

        # A broken upstream is a result the settings page renders, not a crash.
        assert result["ok"] is False
        assert result["state"] == "error"
        assert "upstream exploded" in str(result["error"])
        assert SECRET not in repr(result)
    finally:
        runtime.close()


def test_test_falls_back_to_a_default_query(tmp_path):
    runtime = _runtime(tmp_path)
    try:
        configurator, _ = _configurator(runtime, tmp_path)
        provider = _FakeProvider()
        runtime.web_search_install_provider(provider, choice="auto", key_source="none")

        configurator.test("   ")

        assert provider.calls == [("OpenAI", 5)]
    finally:
        runtime.close()


# --- credential vault scoping ----------------------------------------------


def test_vault_key_names_are_scoped_per_provider():
    assert WebSearchCredentialVault.key_name("Brave") == "brave/api-key"
    assert WebSearchCredentialVault.key_name("tavily") == "tavily/api-key"
    # The real vault refuses anything outside the two keyed providers, so a typo
    # cannot read or write an unrelated entry.
    assert WebSearchCredentialVault().get("hooli") == ""
