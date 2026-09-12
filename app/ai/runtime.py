from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .capabilities import ModelCapability
from .configuration import AIConfiguration
from .credential_resolver import CredentialResolver
from .openai_responses import OpenAIResponsesBackend
from .openai_streaming import OpenAIStreamingChatBackend
from .profiles import ModelProfile
from .provider_catalog import ProviderAdapter, ProviderConnection
from .streaming_platform import StreamingAIPlatform


ClientFactory = Callable[[ProviderConnection, ModelProfile, str], Any]


def build_ai_platform(
    configuration: AIConfiguration,
    *,
    credential_resolver: CredentialResolver,
    client_factory: ClientFactory | None = None,
    request_timeout_seconds: float = 120.0,
) -> StreamingAIPlatform:
    """Build an executable platform from a validated, secret-free configuration.

    Credential values are resolved only here at runtime. They never enter
    ``AIConfiguration`` / ``ModelProfile`` snapshots. The returned platform is
    stream-capable but starts with streaming disabled; Loom's top-level Runtime
    enables it so detached callers retain the legacy completion behavior.

    Direct OpenAI connections use the Responses API. OpenAI-compatible relays
    intentionally remain on Chat Completions because their protocol extensions
    (for example DeepSeek and MiniMax thinking) are not interchangeable with the
    native OpenAI Responses contract.

    Local in-process drivers sometimes need to reuse the same active model (for
    example the isolated Windows Computer Driver). A private connection snapshot
    is therefore attached to the live platform only. It may contain the resolved
    credential, is never part of configuration/status serialization, and dies with
    the process.
    """

    if not isinstance(configuration, AIConfiguration):
        raise TypeError("configuration must be AIConfiguration")
    if not isinstance(credential_resolver, CredentialResolver):
        raise TypeError("credential_resolver must be CredentialResolver")

    platform = StreamingAIPlatform(prefer_streaming=False)
    runtime_connections: list[dict[str, Any]] = []
    for profile in configuration.profiles.all():
        connection = configuration.providers.require_executable(profile.provider)
        secret = credential_resolver.resolve(connection.credential_ref)
        client = (
            client_factory(connection, profile, secret)
            if client_factory is not None
            else None
        )
        if connection.adapter is ProviderAdapter.OPENAI:
            backend = OpenAIResponsesBackend(
                connection=connection,
                profile=profile,
                api_key=secret,
                client=client,
                request_timeout_seconds=request_timeout_seconds,
            )
        elif connection.adapter is ProviderAdapter.OPENAI_COMPATIBLE:
            backend = OpenAIStreamingChatBackend(
                connection=connection,
                profile=profile,
                api_key=secret,
                client=client,
                request_timeout_seconds=request_timeout_seconds,
            )
        else:  # pragma: no cover - catalog blocks non-executable adapters today
            raise RuntimeError(
                f"no runtime backend for provider adapter {connection.adapter.value!r}"
            )
        platform.register(profile, backend)
        runtime_connections.append(
            {
                "profile_id": profile.profile_id,
                "provider": connection.adapter.value,
                "base_url": connection.base_url,
                "model": profile.model,
                "api_key": secret,
                "vision": ModelCapability.VISION in profile.capabilities,
            }
        )

    # Private RAM-only metadata: never expose this through runtime status/logging.
    setattr(platform, "_loom_model_connections", tuple(runtime_connections))
    if len(runtime_connections) == 1:
        setattr(platform, "_loom_model_connection", dict(runtime_connections[0]))
    return platform


__all__ = ["ClientFactory", "build_ai_platform"]
