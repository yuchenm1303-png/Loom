from __future__ import annotations

import os
from typing import Mapping

from .profiles import ModelContextLimits


_ENV_FIELDS = {
    "context_window_tokens": "LOOM_MODEL_CONTEXT_WINDOW_TOKENS",
    "effective_context_percent": "LOOM_MODEL_EFFECTIVE_CONTEXT_PERCENT",
    "auto_compact_token_limit": "LOOM_MODEL_AUTO_COMPACT_TOKEN_LIMIT",
    "output_reserve_tokens": "LOOM_MODEL_OUTPUT_RESERVE_TOKENS",
    "tool_output_token_limit": "LOOM_MODEL_TOOL_OUTPUT_TOKEN_LIMIT",
}
_MAPPING_KEYS = {
    "context_window_tokens": ("context_window_tokens", "contextWindowTokens"),
    "effective_context_percent": ("effective_context_percent", "effectiveContextPercent"),
    "auto_compact_token_limit": ("auto_compact_token_limit", "autoCompactTokenLimit"),
    "output_reserve_tokens": ("output_reserve_tokens", "outputReserveTokens"),
    "tool_output_token_limit": ("tool_output_token_limit", "toolOutputTokenLimit"),
}


# Vocabulary used by OpenAI-compatible `/models` listings, which is not Loom's
# own config vocabulary. Deliberately excludes bare ``max_tokens``: different
# gateways use it for the context length and for the completion cap, and a wrong
# guess here is exactly the class of invented limit this exists to avoid.
_PROVIDER_LISTING_KEYS = {
    "context_window_tokens": (
        "context_length",
        "context_window",
        "max_context_length",
        "max_context_window",
        "max_model_len",
        "max_input_tokens",
    ),
    "output_reserve_tokens": (
        "max_output_tokens",
        "max_completion_tokens",
    ),
}
_PROVIDER_LISTING_NESTS = ("limits", "meta", "metadata", "spec")


def _optional_int(values: Mapping[str, object], name: str) -> int | None:
    raw = values.get(name)
    if raw is None or raw == "":
        return None
    text = str(raw).strip()
    return int(text) if text else None


def _first_int(payload: Mapping[str, object], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _optional_int(payload, key)
        if value is not None:
            return value
    return None


def model_context_limits_from_provider_listing(entry: object) -> ModelContextLimits:
    """Read a model's real limits out of the provider's own `/models` entry.

    A provider that publishes its window is the one authority Loom can consult
    without guessing and without a hard-coded per-model table that rots as
    gateways rename models. Anything the listing does not state stays ``None``,
    which leaves the window undeclared rather than invented.
    """

    if not isinstance(entry, Mapping):
        return ModelContextLimits()

    scopes: list[Mapping[str, object]] = [entry]
    for nest in _PROVIDER_LISTING_NESTS:
        nested = entry.get(nest)
        if isinstance(nested, Mapping):
            scopes.append(nested)

    def first(keys: tuple[str, ...]) -> int | None:
        for scope in scopes:
            for key in keys:
                try:
                    value = _optional_int(scope, key)
                except (TypeError, ValueError):
                    continue
                if value is not None and value > 0:
                    return value
        return None

    return ModelContextLimits(
        context_window_tokens=first(_PROVIDER_LISTING_KEYS["context_window_tokens"]),
        output_reserve_tokens=first(_PROVIDER_LISTING_KEYS["output_reserve_tokens"]),
    )


def model_context_limits_from_env(
    environ: Mapping[str, str] | None = None,
) -> ModelContextLimits:
    """Return explicit model metadata supplied by the host.

    Loom intentionally does not infer context windows from model-name strings.
    OpenAI-compatible endpoints frequently reuse or alias names with different
    limits. A missing value therefore remains unknown and the agent runtime uses
    its conservative fallback rather than silently overestimating capacity.
    """

    env = os.environ if environ is None else environ
    percent = _optional_int(env, _ENV_FIELDS["effective_context_percent"])
    return ModelContextLimits(
        context_window_tokens=_optional_int(env, _ENV_FIELDS["context_window_tokens"]),
        effective_context_percent=percent if percent is not None else 95,
        auto_compact_token_limit=_optional_int(env, _ENV_FIELDS["auto_compact_token_limit"]),
        output_reserve_tokens=_optional_int(env, _ENV_FIELDS["output_reserve_tokens"]),
        tool_output_token_limit=_optional_int(env, _ENV_FIELDS["tool_output_token_limit"]),
    )


def model_context_limits_from_mapping(
    payload: Mapping[str, object] | None,
    *,
    fallback: ModelContextLimits | None = None,
) -> ModelContextLimits:
    """Normalize camelCase/snake_case context metadata from UI/RPC boundaries."""

    source = payload or {}
    base = fallback or ModelContextLimits()
    window = _first_int(source, _MAPPING_KEYS["context_window_tokens"])
    percent = _first_int(source, _MAPPING_KEYS["effective_context_percent"])
    auto_compact = _first_int(source, _MAPPING_KEYS["auto_compact_token_limit"])
    reserve = _first_int(source, _MAPPING_KEYS["output_reserve_tokens"])
    tool_output = _first_int(source, _MAPPING_KEYS["tool_output_token_limit"])

    return ModelContextLimits(
        context_window_tokens=window if window is not None else base.context_window_tokens,
        effective_context_percent=(
            percent if percent is not None else base.effective_context_percent
        ),
        auto_compact_token_limit=(
            auto_compact if auto_compact is not None else base.auto_compact_token_limit
        ),
        output_reserve_tokens=(
            reserve if reserve is not None else base.output_reserve_tokens
        ),
        tool_output_token_limit=(
            tool_output if tool_output is not None else base.tool_output_token_limit
        ),
    )


def model_context_limits_to_camel(limits: ModelContextLimits) -> dict[str, int | None]:
    return {
        "contextWindowTokens": limits.context_window_tokens,
        "effectiveContextPercent": limits.effective_context_percent,
        "autoCompactTokenLimit": limits.auto_compact_token_limit,
        "outputReserveTokens": limits.output_reserve_tokens,
        "toolOutputTokenLimit": limits.tool_output_token_limit,
    }


__all__ = [
    "model_context_limits_from_env",
    "model_context_limits_from_mapping",
    "model_context_limits_from_provider_listing",
    "model_context_limits_to_camel",
]
