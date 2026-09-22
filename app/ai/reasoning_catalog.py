from __future__ import annotations

from typing import Any

from .model_reasoning_catalog import ModelReasoningSpec, canonical_reasoning_spec
from .reasoning import ReasoningKind, ReasoningRequest


_LABELS = {
    "none": "Direct",
    "disabled": "Direct",
    "adaptive": "Adaptive",
    "minimal": "Minimal",
    "low": "Low",
    "medium": "Medium",
    "high": "High",
    "xhigh": "Extra high",
    "max": "Max",
    "ultra": "Ultra",
}

_DESCRIPTIONS = {
    "none": "Disable deliberate reasoning when the transport supports it.",
    "disabled": "Disable deliberate thinking for the lowest latency.",
    "adaptive": "Let the model decide when deeper reasoning is useful.",
    "minimal": "Use the model's smallest deliberate reasoning effort.",
    "low": "Use lighter reasoning for straightforward agent work.",
    "medium": "Balance latency and reasoning depth for everyday work.",
    "high": "Use deeper reasoning for complex multi-step work.",
    "xhigh": "Use extra reasoning depth for difficult multi-step work.",
    "max": "Use the model's maximum supported reasoning setting.",
    "ultra": "Use the model's highest extended reasoning mode.",
}


def _option(value: str, *, advanced: bool = False) -> dict[str, Any]:
    return {
        "value": value,
        "label": _LABELS.get(value, value.replace("-", " ").title()),
        "description": _DESCRIPTIONS.get(value, "Use this model-specific reasoning setting."),
        "advanced": bool(advanced),
    }


def _transport_supports(spec: ModelReasoningSpec, adapter: str) -> bool:
    adapter_key = str(adapter or "").strip().casefold()
    if spec.kind is ReasoningKind.OPENAI_EFFORT:
        return adapter_key in {"openai", "openai-compatible", "opencode-go"}
    if spec.kind is ReasoningKind.MINIMAX_THINKING:
        return adapter_key in {"openai-compatible", "opencode-go"}
    if spec.kind is ReasoningKind.THINKING_BUDGET:
        # Budget presets currently have a faithful encoder only on OpenCode Go's
        # Anthropic Messages surface. Do not show a control on arbitrary custom
        # endpoints and then silently pretend that it applied.
        return adapter_key == "opencode-go"
    return False


def _capability_from_spec(spec: ModelReasoningSpec) -> dict[str, Any]:
    return {
        "kind": spec.kind.value,
        "defaultValue": spec.default_value,
        "options": [
            _option(value, advanced=value in spec.advanced)
            for value in spec.values
        ],
        "source": spec.source,
    }


def reasoning_capability(*, model: str, adapter: str, base_url: str = "") -> dict[str, Any] | None:
    """Return truthful UI metadata for model-owned reasoning controls.

    Model identity determines which choices exist; provider identity only decides
    whether Loom has a faithful wire encoder for those choices. This keeps the
    same Luna/DeepSeek/Grok controls when a user switches between OpenCode Go,
    an official endpoint, and an OpenAI-compatible relay without inventing
    controls for unknown models.
    """

    model_key = str(model or "").strip().casefold()
    endpoint = str(base_url or "").strip().casefold()
    if not model_key:
        return None

    spec = canonical_reasoning_spec(model)
    if spec is not None and _transport_supports(spec, adapter):
        return _capability_from_spec(spec)

    # DeepSeek may add model IDs before the bundled catalog is refreshed. Its
    # official endpoint has a stable reasoning_effort contract, so preserve a
    # conservative provider fallback for newly discovered DeepSeek models.
    if "api.deepseek.com" in endpoint:
        fallback = ModelReasoningSpec(
            ReasoningKind.OPENAI_EFFORT,
            ("none", "low", "high", "max"),
            "low",
            source="DeepSeek thinking effort",
            advanced=frozenset({"max"}),
        )
        return _capability_from_spec(fallback)
    return None


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
