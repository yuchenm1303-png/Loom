from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


# Used only as an absolute sanity ceiling for structural impossibility checks
# when nothing authoritative declares the model's window. It is deliberately not
# derived from AgentLimits: that value is a Loom construction default, not a
# statement about anyone's model, and treating it as one is what produced a
# 32,768 ceiling for models many times larger.
_UNKNOWN_WINDOW_SANITY_CEILING = 272_000
# Headroom Loom subtracts from the input budget when nothing declared an output
# size. Used for budgeting arithmetic only — it is never sent as ``max_tokens``,
# because an undeclared cap is Loom's bookkeeping, not the user's choice.
_UNDECLARED_OUTPUT_RESERVE_TOKENS = 4096


@dataclass(frozen=True, slots=True)
class ResolvedContextLimits:
    """One model step's resolved context window and compaction threshold.

    ``tool_output_token_limit`` is the request-only budget used to keep very
    large historical tool observations from forcing a full model compaction.
    Durable history remains unchanged. ``recent_user_token_limit`` is retained
    for compatibility with existing product/runtime contracts.
    """

    context_window_tokens: int
    effective_context_window_tokens: int
    input_budget_tokens: int
    output_reserve_tokens: int
    auto_compact_token_limit: int
    auto_compact_token_limit_scope: str
    tool_output_token_limit: int
    recent_user_token_limit: int
    safety_tokens: int
    source: str
    # True only when a profile or env var declared an output cap. Otherwise the
    # reserve is Loom's own bookkeeping number and must not be sent as a
    # provider-side ``max_tokens``: a reasoning model spends this budget on its
    # chain of thought and gets truncated mid-answer by a limit nobody chose.
    output_reserve_declared: bool = False
    # False when no authoritative metadata declared this model's window, so every
    # token limit above is a guess about somebody else's model. Codex leaves the
    # window ``None`` in that case and lets the provider be the authority instead
    # of budgeting against a number it invented.
    window_known: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "context_window_tokens": self.context_window_tokens,
            "effective_context_window_tokens": self.effective_context_window_tokens,
            "input_budget_tokens": self.input_budget_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
            "auto_compact_token_limit": self.auto_compact_token_limit,
            "auto_compact_token_limit_scope": self.auto_compact_token_limit_scope,
            "tool_output_token_limit": self.tool_output_token_limit,
            "recent_user_token_limit": self.recent_user_token_limit,
            "safety_tokens": self.safety_tokens,
            "source": self.source,
            "output_reserve_declared": self.output_reserve_declared,
            "window_known": self.window_known,
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
    """Resolve the current model step's window using Codex-compatible defaults.

    Codex derives the default automatic compaction threshold from the model's
    resolved *raw* context window (90%). The effective-window percentage is a
    separate hard usability cap. Re-running this function for every model step
    also means a profile/model change immediately changes the threshold.

    Unknown/custom models use Codex's conservative 272k fallback and a 95%
    effective window instead of silently behaving like 32k models. Explicit
    environment overrides and authoritative model-profile metadata remain higher
    priority. Loom historically constructed AgentRuntime with a hard-coded 32k
    default; that legacy value is normalized only on the unknown-model fallback
    path. A real 32k model remains representable through profile metadata or an
    explicit LOOM_CONTEXT_WINDOW_TOKENS override.

    Current Loom model-profile metadata has no field for Codex's optional
    ``body_after_prefix`` scope and no AutoCompactWindow prefill baseline. The
    only faithfully representable scope is therefore the Codex default ``total``.
    If a future profile object supplies another scope, fail closed instead of
    silently treating it as ``total``.
    """

    runtime_window = getattr(rt.limits, "context_window_tokens", None)
    runtime_window = max(2, int(runtime_window)) if runtime_window else None
    runtime_reserve = getattr(rt.limits, "output_reserve_tokens", None)
    runtime_reserve = max(1, int(runtime_reserve)) if runtime_reserve else None
    profile_limits = _profile_limits(rt.platform, session.profile_id)

    env_window = _positive_env("LOOM_CONTEXT_WINDOW_TOKENS")
    env_reserve = _positive_env("LOOM_OUTPUT_RESERVE_TOKENS")

    profile_window = getattr(profile_limits, "context_window_tokens", None)
    profile_reserve = getattr(profile_limits, "output_reserve_tokens", None)
    profile_percent = int(getattr(profile_limits, "effective_context_percent", 100) or 100)
    auto_compact_scope = str(
        getattr(profile_limits, "auto_compact_token_limit_scope", "total") or "total"
    ).strip().casefold()
    if auto_compact_scope != "total":
        raise ValueError(
            "auto_compact_token_limit_scope='body_after_prefix' requires an active "
            "prefill-window state contract that Loom ModelContextLimits does not expose"
        )

    window_known = True
    if env_window is not None:
        context_window = env_window
        effective_window = env_window
        source = "runtime_env"
    elif profile_window is not None:
        context_window = int(profile_window)
        effective_window = max(1, context_window * profile_percent // 100)
        source = "model_profile"
    elif runtime_window is not None:
        # The host constructed this runtime with an explicit window, which is a
        # declaration about the model even though it did not come from a profile.
        context_window = runtime_window
        effective_window = max(1, context_window * profile_percent // 100)
        source = "runtime_limits"
    else:
        context_window = _UNKNOWN_WINDOW_SANITY_CEILING
        effective_window = max(1, context_window * 95 // 100)
        source = "runtime_fallback"
        window_known = False

    # Whether anything authoritative asked for an output cap. Loom has to put a
    # number somewhere to reserve input headroom, but only a declared one may be
    # imposed on the provider as ``max_tokens``.
    output_reserve_declared = (
        env_reserve is not None or profile_reserve is not None or runtime_reserve is not None
    )
    if env_reserve is not None:
        output_reserve = env_reserve
    elif profile_reserve is not None:
        output_reserve = int(profile_reserve)
    elif runtime_reserve is not None:
        output_reserve = runtime_reserve
    else:
        output_reserve = _UNDECLARED_OUTPUT_RESERVE_TOKENS

    if output_reserve >= effective_window:
        raise ValueError(
            "resolved output reserve must be smaller than the effective model context window"
        )

    # Chat Completions needs an explicit output reservation; this is a Loom
    # transport adaptation, not the signal used to decide whether to compact.
    input_budget = effective_window - output_reserve
    safety_tokens = max(0, min(2048, input_budget // 100))

    configured_auto = getattr(profile_limits, "auto_compact_token_limit", None)
    if configured_auto is None:
        auto_compact = context_window * 9 // 10
    else:
        auto_compact = int(configured_auto)
    # Codex independently enforces the effective context window as a hard cap.
    # Loom exposes one trigger threshold here, so clamp the 90%-of-raw default to
    # that hard cap to preserve the same earliest compaction point.
    auto_compact = max(1, min(auto_compact, effective_window))

    configured_tool = getattr(profile_limits, "tool_output_token_limit", None)
    tool_output_limit = (
        max(256, int(configured_tool))
        if configured_tool is not None
        else min(6000, max(1200, input_budget // 8))
    )
    recent_user_limit = min(20_000, max(2_000, input_budget // 2))

    return ResolvedContextLimits(
        context_window_tokens=context_window,
        effective_context_window_tokens=effective_window,
        input_budget_tokens=input_budget,
        output_reserve_tokens=output_reserve,
        auto_compact_token_limit=auto_compact,
        auto_compact_token_limit_scope=auto_compact_scope,
        tool_output_token_limit=tool_output_limit,
        recent_user_token_limit=recent_user_limit,
        safety_tokens=safety_tokens,
        source=source,
        output_reserve_declared=output_reserve_declared,
        window_known=window_known,
    )


__all__ = ["ResolvedContextLimits", "resolve_context_limits"]
