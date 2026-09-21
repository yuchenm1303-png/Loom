from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OpenCodeGoModelLimits:
    context_tokens: int
    max_output_tokens: int


# Generated from the Models.dev ``opencode-go`` provider catalog on 2026-09-21.
# OpenCode's own /models endpoint publishes IDs only, so this small bundled
# snapshot keeps model budgeting correct and available offline. Provider-listed
# limits still win when OpenCode starts publishing them directly.
_LIMITS: dict[str, OpenCodeGoModelLimits] = {
    "minimax-m3": OpenCodeGoModelLimits(1_000_000, 131_072),
    "minimax-m2.7": OpenCodeGoModelLimits(204_800, 131_072),
    "minimax-m2.5": OpenCodeGoModelLimits(204_800, 65_536),
    "kimi-k3": OpenCodeGoModelLimits(1_048_576, 131_072),
    "kimi-k2.7-code": OpenCodeGoModelLimits(262_144, 262_144),
    "kimi-k2.6": OpenCodeGoModelLimits(262_144, 65_536),
    "longcat-2.0": OpenCodeGoModelLimits(1_000_000, 131_072),
    "kimi-k2.5": OpenCodeGoModelLimits(262_144, 65_536),
    "glm-5.2": OpenCodeGoModelLimits(1_000_000, 131_072),
    "glm-5.3-flash": OpenCodeGoModelLimits(1_000_000, 131_072),
    "glm-5.3": OpenCodeGoModelLimits(1_000_000, 131_072),
    "glm-5.1": OpenCodeGoModelLimits(202_752, 32_768),
    "glm-5": OpenCodeGoModelLimits(202_752, 32_768),
    "deepseek-v4-pro": OpenCodeGoModelLimits(1_000_000, 384_000),
    "deepseek-v4-flash": OpenCodeGoModelLimits(1_000_000, 384_000),
    "deepseek-v4.1-flash": OpenCodeGoModelLimits(1_000_000, 384_000),
    "deepseek-v4-flash-vision-exp": OpenCodeGoModelLimits(1_000_000, 384_000),
    "qwen3.7-max": OpenCodeGoModelLimits(1_000_000, 65_536),
    "qwen3.8-max": OpenCodeGoModelLimits(1_000_000, 131_072),
    "qwen3.8-flash": OpenCodeGoModelLimits(1_000_000, 131_072),
    "qwen3.7-plus": OpenCodeGoModelLimits(1_000_000, 65_536),
    "qwen3.6-plus": OpenCodeGoModelLimits(1_000_000, 65_536),
    "qwen3.5-plus": OpenCodeGoModelLimits(262_144, 65_536),
    "mimo-v2-pro": OpenCodeGoModelLimits(1_048_576, 128_000),
    "mimo-v2-omni": OpenCodeGoModelLimits(262_144, 128_000),
    "mimo-v2.5-pro": OpenCodeGoModelLimits(1_048_576, 128_000),
    "mimo-v2.5": OpenCodeGoModelLimits(1_000_000, 128_000),
    "hy4-preview": OpenCodeGoModelLimits(1_024_000, 64_000),
    "hy3": OpenCodeGoModelLimits(256_000, 128_000),
    "gpt-5.6-luna": OpenCodeGoModelLimits(1_050_000, 128_000),
    "grok-4.5": OpenCodeGoModelLimits(500_000, 500_000),
    "grok-4.6": OpenCodeGoModelLimits(500_000, 500_000),
    "muse-spark-1.3-contributor": OpenCodeGoModelLimits(1_048_576, 131_072),
    "muse-spark-1.2-contributor": OpenCodeGoModelLimits(1_048_576, 131_072),
    "omen-alpha": OpenCodeGoModelLimits(500_000, 128_000),
}

_ALIASES = {
    "deepseek-flash": "deepseek-v4.1-flash",
    "hy3-preview": "hy3",
}


def opencode_go_model_limits(model: str) -> OpenCodeGoModelLimits | None:
    key = str(model or "").strip().casefold()
    key = _ALIASES.get(key, key)
    return _LIMITS.get(key)


__all__ = ["OpenCodeGoModelLimits", "opencode_go_model_limits"]
