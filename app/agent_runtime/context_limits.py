from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ResolvedContextLimits:
    """One model step's authoritative context budget."""

    context_window_tokens: int
    effective_context_window_tokens: int
    input_budget_tokens: int
    output_reserve_tokens: int
    auto_compact_token_limit: int
    tool_output_token_limit: int
    recent_user_token_limit: int
    safety_tokens: int
    source: str

    def as_dict(self) -> dict[str, object]:
        return {
            "context_window_tokens": self.context_window_tokens,
            "effective_context_window_tokens": self.effective_context_window_tokens,
            "input_budget_tokens": self.input_budget_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "auto_compact_token_limit": self.auto_compact_token_limit,
            "tool_output_token_limit": self.tool_output_token_limit,
            "recent_user_token_limit": self.recent_user_token_limit,
            "safety_tokens": self.safety_tokens,
            "source": self.source,
        }


def _positive_env(name: str) -> int | None:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return None
    value = int(raw)
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _profile_limits(platform: Any, profile_id: str):
    registry = getattr(platform, "registry", None)
    if registry is None:
        return None
    try:
        profile = registry.get(profile_id)
    except (AttributeError, KeyError, TypeError, ValueError):
        return None
    return getattr(profile, "context_limits", None)


def resolve_context_limits(rt: Any, session: Any) -> ResolvedContextLimits:
    """Resolve model-scoped limits without guessing provider capacity.

    Precedence for the hard window is an explicit legacy runtime environment
    override, then model-profile metadata, then ``AgentLimits``. This preserves
    backwards compatibility while allowing different models to carry different
    windows in the same process.
    """

    fallback_window = max(2, int(rt.limits.context_window_tokens))
    fallback_reserve = max(1, int(rt.limits.output_reserve_tokens))
    profile_limits = _profile_limits(rt.platform, session.profile_id)

    env_window = _positive_env("LOOM_CONTEXT_WINDOW_TOKENS")
    env_reserve = _positive_env("LOOM_OUTPUT_RESERVE_TOKENS")

    profile_window = getattr(profile_limits, "context_window_tokens", None)
    profile_reserve = getattr(profile_limits, "output_reserve_tokens", None)
    profile_percent = int(getattr(profile_limits, "effective_context_percent", 100) or 100)

    if env_window is not None:
        context_window = env_window
        effective_window = env_window
        source = "runtime_env"
    elif profile_window is not None:
        context_window = int(profile_window)
        effective_window = max(1, context_window * profile_percent // 100)
        source = "model_profile"
    else:
        context_window = fallback_window
        effective_window = fallback_window
        source = "runtime_fallback"

    if env_reserve is not None:
        output_reserve = env_reserve
    elif profile_reserve is not None:
        output_reserve = int(profile_reserve)
    else:
        output_reserve = fallback_reserve

    # A bad endpoint profile should fail locally with a precise configuration
    # error instead of producing an impossible provider request.
    if output_reserve >= effective_window:
        raise ValueError(
            "resolved output reserve must be smaller than the effective model context window"
        )

    input_budget = effective_window - output_reserve
    safety_tokens = max(256, min(2048, input_budget // 100))

    configured_auto = getattr(profile_limits, "auto_compact_token_limit", None)
    if configured_auto is None:
        # Compact before the hard wall. 78% leaves enough headroom for one large
        # observation and the compaction summary itself without wasting half of a
        # model's available window.
        auto_compact = input_budget * 78 // 100
    else:
        auto_compact = int(configured_auto)
    auto_compact = max(512, min(auto_compact, max(512, input_budget - safety_tokens)))

    configured_tool = getattr(profile_limits, "tool_output_token_limit", None)
    if configured_tool is None:
        # Per-result request-visible budget. Canonical tool output stays durable;
        # this only controls how much is repeatedly sent back to the model.
        tool_output_limit = min(6000, max(1200, input_budget // 8))
    else:
        tool_output_limit = max(256, int(configured_tool))
    tool_output_limit = min(tool_output_limit, max(256, input_budget // 2))

    recent_user_limit = min(20_000, max(2_000, input_budget // 2))

    return ResolvedContextLimits(
        context_window_tokens=context_window,
        effective_context_window_tokens=effective_window,
        input_budget_tokens=input_budget,
        output_reserve_tokens=output_reserve,
        auto_compact_token_limit=auto_compact,
        tool_output_token_limit=tool_output_limit,
        recent_user_token_limit=recent_user_limit,
        safety_tokens=safety_tokens,
        source=source,
    )


__all__ = ["ResolvedContextLimits", "resolve_context_limits"]
