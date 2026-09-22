from __future__ import annotations

from dataclasses import dataclass

from .reasoning import ReasoningKind


@dataclass(frozen=True, slots=True)
class ModelReasoningSpec:
    """Provider-neutral reasoning controls owned by a model identity.

    The model catalog answers *which* choices exist. Provider transports remain
    responsible for translating one selected choice to their own wire format.
    """

    kind: ReasoningKind
    values: tuple[str, ...]
    default_value: str
    source: str = "Loom model capability catalog"
    advanced: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        values = tuple(str(value or "").strip().casefold() for value in self.values)
        default_value = str(self.default_value or "").strip().casefold()
        if not values or any(not value for value in values):
            raise ValueError("reasoning spec values must not be empty")
        if len(set(values)) != len(values):
            raise ValueError("reasoning spec values must be unique")
        if default_value not in values:
            raise ValueError("reasoning spec default must be one of its values")
        object.__setattr__(self, "kind", ReasoningKind(self.kind))
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "default_value", default_value)
        object.__setattr__(
            self,
            "advanced",
            frozenset(str(value or "").strip().casefold() for value in self.advanced),
        )


def _effort(
    values: tuple[str, ...],
    *,
    default: str,
    advanced: tuple[str, ...] = (),
) -> ModelReasoningSpec:
    return ModelReasoningSpec(
        ReasoningKind.OPENAI_EFFORT,
        values,
        default,
        advanced=frozenset(advanced),
    )


def _thinking(values: tuple[str, ...], *, default: str) -> ModelReasoningSpec:
    return ModelReasoningSpec(ReasoningKind.MINIMAX_THINKING, values, default)


def _budget(values: tuple[str, ...], *, default: str) -> ModelReasoningSpec:
    return ModelReasoningSpec(
        ReasoningKind.THINKING_BUDGET,
        values,
        default,
        advanced=frozenset({"max"}),
    )


# Exact model controls are intentionally independent from provider identity.
# OpenCode Go's current catalog is sourced from models.dev; OpenAI-family entries
# retain Loom's existing official/Codex control surface. A transport may expose a
# strict subset only when it can faithfully encode the selected value.
_EXACT: dict[str, ModelReasoningSpec] = {
    "gpt-5.6-luna": _effort(
        ("none", "low", "medium", "high", "xhigh", "max"),
        default="low",
        advanced=("max",),
    ),
    "gpt-5.6-sol": _effort(
        ("low", "medium", "high", "xhigh", "max", "ultra"),
        default="low",
        advanced=("max", "ultra"),
    ),
    "gpt-6-astra": _effort(
        ("low", "medium", "high", "xhigh", "max", "ultra"),
        default="low",
        advanced=("max", "ultra"),
    ),
    "grok-4.5": _effort(("low", "medium", "high"), default="low"),
    "grok-4.6": _effort(("low", "medium", "high", "xhigh"), default="low"),
    "grok-4.7": _effort(("low", "medium", "high", "xhigh"), default="low"),
    "deepseek-flash": _effort(("none", "low", "high", "max"), default="low", advanced=("max",)),
    "deepseek-v4-flash": _effort(("low", "high", "max"), default="low", advanced=("max",)),
    "deepseek-v4.1-flash": _effort(("low", "high", "max"), default="low", advanced=("max",)),
    "deepseek-v4-flash-vision-exp": _effort(
        ("none", "low", "high", "max"),
        default="low",
        advanced=("max",),
    ),
    "deepseek-v4-pro": _effort(("high", "max"), default="high", advanced=("max",)),
    "glm-5.2": _effort(("high", "max"), default="high", advanced=("max",)),
    "glm-5.3": _effort(("low", "high", "max"), default="low", advanced=("max",)),
    "glm-5.3-flash": _effort(("low", "high", "max"), default="low", advanced=("max",)),
    "hy3": _effort(("none", "low", "high"), default="low"),
    "hy4-preview": _effort(("none", "high"), default="high"),
    "qwen3.8-flash": _effort(("none", "low", "medium", "xhigh"), default="low"),
    "qwen3.8-max": _effort(("none", "low", "medium", "xhigh"), default="low"),
    # These Qwen generations expose thinking as a budget rather than an effort.
    # OpenCode's Messages transport maps the presets to safe bounded token budgets.
    "qwen3.5-plus": _budget(("none", "high", "max"), default="high"),
    "qwen3.6-plus": _budget(("none", "high", "max"), default="high"),
    "qwen3.7-plus": _budget(("none", "high", "max"), default="high"),
    "qwen3.7-max": _budget(("none", "high", "max"), default="high"),
    "minimax-m3": _thinking(("disabled", "adaptive"), default="adaptive"),
    "muse-spark-1.2-contributor": _effort(
        ("minimal", "low", "medium", "high", "xhigh"),
        default="low",
    ),
    "muse-spark-1.3-contributor": _effort(
        ("minimal", "low", "medium", "high", "xhigh"),
        default="low",
    ),
    "omen-alpha": _effort(("low", "high"), default="low"),
    "ox-alpha-free": _effort(("low", "high", "max"), default="low", advanced=("max",)),
}

_ALIASES = {
    "hy3-preview": "hy3",
}


def canonical_model_key(model: str) -> str:
    value = str(model or "").strip().casefold().rstrip("/")
    if "/" in value:
        value = value.rsplit("/", 1)[-1]
    return _ALIASES.get(value, value)


def canonical_reasoning_spec(model: str) -> ModelReasoningSpec | None:
    key = canonical_model_key(model)
    if not key:
        return None
    exact = _EXACT.get(key)
    if exact is not None:
        return exact

    # Known OpenAI reasoning families use the same standard effort wire control
    # across the official API and OpenAI-compatible relays. Keep this fallback
    # narrow: unknown non-OpenAI models are never assigned invented controls.
    if key.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        return _effort(("low", "medium", "high", "xhigh"), default="medium")
    return None


__all__ = [
    "ModelReasoningSpec",
    "canonical_model_key",
    "canonical_reasoning_spec",
]
