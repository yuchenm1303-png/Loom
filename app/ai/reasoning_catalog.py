from __future__ import annotations

from typing import Any

from .reasoning import ReasoningKind, ReasoningRequest


def _option(value: str, label: str, description: str, *, advanced: bool = False) -> dict[str, Any]:
    return {
        "value": value,
        "label": label,
        "description": description,
        "advanced": bool(advanced),
    }


# MiniMax's hosted OpenAI-compatible M3 endpoint documents adaptive thinking as
# the default and supports disabling it. Keep this catalog conservative even
# though the open-weight model can expose additional deployment-specific modes.
_MINIMAX_M3_OPTIONS = [
    _option("disabled", "Direct", "Disable deliberate thinking for the lowest latency."),
    _option("adaptive", "Adaptive", "Let M3 decide when deeper reasoning is useful."),
]

_DEEPSEEK_OPTIONS = [
    _option("none", "Direct", "Disable thinking mode for the lowest latency."),
    _option("low", "Low", "Use lighter reasoning for straightforward tasks."),
    _option("high", "High", "DeepSeek's default reasoning level for agent work."),
    _option("max", "Max", "Use DeepSeek's maximum supported reasoning effort.", advanced=True),
]

_OPENAI_STANDARD_OPTIONS = [
    _option("low", "Low", "Fast responses with lighter reasoning."),
    _option("medium", "Medium", "Balances speed and reasoning depth for everyday work."),
    _option("high", "High", "Greater reasoning depth for complex problems."),
    _option("xhigh", "Extra high", "Extra reasoning depth for difficult multi-step work."),
]

_OPENAI_ADVANCED_OPTIONS = [
    _option("max", "Max", "Maximum reasoning depth for the hardest problems.", advanced=True),
    _option("ultra", "Ultra", "Maximum reasoning with automatic task delegation.", advanced=True),
]


def reasoning_capability(*, model: str, adapter: str, base_url: str = "") -> dict[str, Any] | None:
    """Return safe UI metadata for reasoning controls supported by a model.

    The shape follows Codex's model-catalog pattern: each model advertises its
    supported choices and one default, instead of clients inventing a universal
    slider. Exact known Codex advanced levels are enabled only for model families
    where Loom has an explicit catalog rule.
    """

    model_key = str(model or "").strip().casefold()
    adapter_key = str(adapter or "").strip().casefold()
    endpoint = str(base_url or "").strip().casefold()
    if not model_key:
        return None

    if "minimax-m3" in model_key or ("minimax" in endpoint and model_key in {"m3", "minimax-m3"}):
        return {
            "kind": ReasoningKind.MINIMAX_THINKING.value,
            "defaultValue": "adaptive",
            "options": list(_MINIMAX_M3_OPTIONS),
            "source": "MiniMax M3 hosted API",
        }

    if "api.deepseek.com" in endpoint and model_key:
        return {
            "kind": ReasoningKind.OPENAI_EFFORT.value,
            "defaultValue": "high",
            "options": list(_DEEPSEEK_OPTIONS),
            "source": "DeepSeek thinking effort",
        }

    is_openai_reasoning = (
        adapter_key == "openai"
        and (
            model_key.startswith("gpt-5")
            or model_key.startswith("gpt-6")
            or model_key.startswith("o1")
            or model_key.startswith("o3")
            or model_key.startswith("o4")
        )
    )
    # OpenAI-compatible relays are common for OpenAI models. If the model id is
    # unambiguously an OpenAI reasoning family, expose the same wire control.
    if not is_openai_reasoning and adapter_key == "openai-compatible":
        is_openai_reasoning = (
            model_key.startswith("gpt-5")
            or model_key.startswith("gpt-6")
            or model_key.startswith("o1")
            or model_key.startswith("o3")
            or model_key.startswith("o4")
        )
    if not is_openai_reasoning:
        return None

    options = list(_OPENAI_STANDARD_OPTIONS)
    if model_key.startswith("gpt-5.6-sol") or model_key.startswith("gpt-6-astra"):
        options.extend(_OPENAI_ADVANCED_OPTIONS)
    return {
        "kind": ReasoningKind.OPENAI_EFFORT.value,
        "defaultValue": "low" if model_key.startswith(("gpt-5.6-sol", "gpt-6-astra")) else "medium",
        "options": options,
        "source": "OpenAI reasoning effort",
    }


def resolved_reasoning(
    *,
    model: str,
    adapter: str,
    base_url: str,
    saved: ReasoningRequest | None,
) -> tuple[dict[str, Any] | None, ReasoningRequest | None]:
    capability = reasoning_capability(model=model, adapter=adapter, base_url=base_url)
    if capability is None:
        return None, None
    kind = str(capability["kind"])
    options = {str(item["value"]) for item in capability["options"]}
    default_value = str(capability["defaultValue"])
    selected = saved
    if selected is None or selected.kind.value != kind or selected.value not in options:
        selected = ReasoningRequest.from_values(kind, default_value)
    return capability, selected


__all__ = ["reasoning_capability", "resolved_reasoning"]
