from __future__ import annotations

from unittest.mock import MagicMock

from app.ai.contracts import AIMessage, ChatRequest, MessageRole
from app.ai.capabilities import ModelCapability
from app.ai.credentials import CredentialRef, CredentialSource
from app.ai.openai_runtime import OpenAIChatBackend
from app.ai.profiles import ModelProfile
from app.ai.provider_catalog import ProviderAdapter, ProviderConnection


def _profile(model: str = "cqu-default") -> ModelProfile:
    return ModelProfile(
        profile_id="cqu",
        provider="smirel",
        model=model,
        capabilities=frozenset({ModelCapability.TEXT}),
    )


def _connection(base_url: str = "https://relay.smirel.com") -> ProviderConnection:
    return ProviderConnection(
        provider_id="smirel",
        adapter=ProviderAdapter.OPENAI_COMPATIBLE,
        base_url=base_url,
        credential_ref=CredentialRef(CredentialSource.RUNTIME, "test"),
    )


def test_base_url_auto_appends_v1() -> None:
    """A bare origin must be promoted to ``/v1`` before the SDK dispatches."""
    runtime = OpenAIChatBackend(
        api_key="k",
        connection=_connection("https://relay.smirel.com"),
        profile=_profile(),
    )
    assert str(runtime.client.base_url).rstrip("/") == "https://relay.smirel.com/v1"


def test_base_url_preserves_explicit_v1() -> None:
    """An operator-supplied ``/v1`` must not be duplicated."""
    runtime = OpenAIChatBackend(
        api_key="k",
        connection=_connection("https://relay.smirel.com/v1"),
        profile=_profile(),
    )
    assert str(runtime.client.base_url).rstrip("/") == "https://relay.smirel.com/v1"


def test_base_url_strips_trailing_slash() -> None:
    """A trailing slash must not become ``/v1/``."""
    runtime = OpenAIChatBackend(
        api_key="k",
        connection=_connection("https://relay.smirel.com/"),
        profile=_profile(),
    )
    assert str(runtime.client.base_url).rstrip("/") == "https://relay.smirel.com/v1"


def test_cqu_drops_empty_system_messages() -> None:
    """Whitespace-only system messages must be filtered for the CQU relay."""
    runtime = OpenAIChatBackend(
        api_key="k",
        connection=_connection(),
        profile=_profile("cqu-default"),
    )
    request = ChatRequest(
        messages=[
            AIMessage(role=MessageRole.SYSTEM, content="   "),
            AIMessage(role=MessageRole.USER, content="hi"),
        ]
    )
    kwargs = runtime._request_kwargs(request)
    roles = [m["role"] for m in kwargs["messages"]]
    assert "system" not in roles
    assert roles == ["user"]


def test_non_cqu_keeps_system_messages() -> None:
    runtime = OpenAIChatBackend(
        api_key="k",
        connection=_connection(),
        profile=_profile("gpt-4o"),
    )
    request = ChatRequest(
        messages=[
            AIMessage(role=MessageRole.SYSTEM, content="be helpful"),
            AIMessage(role=MessageRole.USER, content="hi"),
        ]
    )
    kwargs = runtime._request_kwargs(request)
    roles = [m["role"] for m in kwargs["messages"]]
    assert roles == ["system", "user"]
