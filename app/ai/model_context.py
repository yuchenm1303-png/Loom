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


def _optional_int(env: Mapping[str, str], name: str) -> int | None:
    raw = str(env.get(name) or "").strip()
    return int(raw) if raw else None


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


__all__ = ["model_context_limits_from_env"]
