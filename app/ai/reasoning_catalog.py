from __future__ import annotations

from typing import Any

from .codex_model_catalog import catalog_reasoning_spec
from .reasoning import ReasoningKind, ReasoningRequest


def _option(value: str, label: str, description: str, *, advanced: bool = False) -> dict[str, Any]:
    return {
        "value": value,
        "label": label,
        "description": description,
        "advanced": bool(advanced),
    }


_MINIMAX_M3_OPTIONS = [
    _option("disabled", "Direct", "Disable deliberate thinking for the lowest latency."),
    _option("adaptive", "Adaptive", "Let M3 decide when deeper reasoning is useful."),
]

_OPENAI_OPTION_LIBRARY: dict[str, dict[str, Any]] = {
    "none": _option("none", "None", "Use the model's non-reasoning / lowest-latency mode."),
    "minimal": _option("minimal", "Minimal", "Use the smallest available reasoning budget."),
    "low": _option("low", "Low", "Fast responses with lighter reasoning."),
    "medium": _option("medium", "Medium", "Balances speed and reasoning depth for everyday work."),
    "high": _option("high", "High", "Greater reasoning depth for complex problems."),
    "xhigh": _option("xhigh", "Extra high", "Extra reasoning depth for difficult multi-step work."),
    "max": _option("max", "Max", "Maximum API reasoning depth for the hardest problems.", advanced=True),
    "ultra": _option("ultra", "Ultra", "Provider-defined orchestration reasoning mode.", advanced=True),
    "persistent": _option("persistent", "Persistent", "Provider-defined persistent reasoning mode.", advanced=True),
}


def _openai_options(values: tuple[str, ...]) -> list[dict[str, Any]]:
    return [dict(_OPENAI_OPTION_LIBRARY[value]) for value in values]


def _catalog_options(levels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert Codex catalog levels into Loom picker metadata.

    Unknown future effort names are preserved instead of being rejected. That
    mirrors Codex's custom reasoning-effort handling while still letting the
    catalog parser filter product-only levels Loom cannot implement yet.
    """

    options: list[dict[str, Any]] = []
    for level in levels:
        value = str(level.get("effort") or "").strip().casefold()
        if not value:
            continue
        description = str(level.get("description") or "").strip()
        known = _OPENAI_OPTION_LIBRARY.get(value)
        if known is not None:
            option = dict(known)
            if description:
                option["description"] = description
            option["advanced"] = bool(level.get("advanced")) or bool(option.get("advanced"))
        else:
            label = value.replace("_", " ").replace("-", " ").title()
            option = _option(
                value,
                label,
                description or f"Provider-defined reasoning level: {value}.",
                advanced=bool(level.get("advanced")),
            )
        options.append(option)
    return options


def _model_slug(model_key: str) -> str:
    value = str(model_key or "").strip().casefold()
    return value.rsplit("/", 1)[-1]


def _deepseek_model_spec(model_key: str) -> tuple[str, tuple[str, ...], str] | None:
    """Return the official DeepSeek V4 preset used for Codex-style clients."""

    slug = _model_slug(model_key)
    if slug.startswith("deepseek-v4-"):
        return (
            "high",
            ("low", "high", "max"),
            "DeepSeek V4 Codex model catalog",
        )
    return None


def requires_reasoning_content_replay(*, model: str, adapter: str, base_url: str = "") -> bool:
    """Whether tool-bearing chat requests must replay provider reasoning state."""

    adapter_key = str(adapter or "").strip().casefold()
    if adapter_key not in {"openai", "openai-compatible"}:
        return False
    return _deepseek_model_spec(str(model or "").strip().casefold()) is not None


def _openai_model_spec(model_key: str) -> tuple[str, tuple[str, ...], str] | None:
    """Return Loom's conservative public-API reasoning spec for a known model."""

    model_key = _model_slug(model_key)

    if model_key.startswith("gpt-6-astra"):
        return ("low", ("low", "medium", "high", "xhigh", "max"), "OpenAI GPT-6 Astra API")
    if model_key.startswith("gpt-5.6"):
        return ("low", ("none", "low", "medium", "high", "xhigh", "max"), "OpenAI GPT-5.6 API")
    if model_key.startswith("gpt-5.5-pro"):
        return ("high", ("medium", "high", "xhigh"), "OpenAI GPT-5.5 Pro API")
    if model_key.startswith("gpt-5.5"):
        return ("medium", ("none", "low", "medium", "high", "xhigh"), "OpenAI GPT-5.5 API")
    if model_key.startswith("gpt-5.4-pro"):
        return ("medium", ("medium", "high", "xhigh"), "OpenAI GPT-5.4 Pro API")
    if model_key.startswith("gpt-5.4"):
        return ("medium", ("none", "low", "medium", "high", "xhigh"), "OpenAI GPT-5.4 API")
    if model_key.startswith("gpt-5.2-pro"):
        return ("medium", ("medium", "high", "xhigh"), "OpenAI GPT-5.2 Pro API")
    if model_key.startswith("gpt-5.2"):
        return ("none", ("none", "low", "medium", "high", "xhigh"), "OpenAI GPT-5.2 API")
    if model_key.startswith("gpt-5.1"):
        return ("none", ("none", "low", "medium", "high"), "OpenAI GPT-5.1 API")
    if model_key.startswith("gpt-5-pro"):
        return ("high", ("high",), "OpenAI GPT-5 Pro API")
    if (
        model_key == "gpt-5"
        or model_key.startswith("gpt-5-")
        or model_key.startswith("gpt-5-mini")
        or model_key.startswith("gpt-5-nano")
    ):
        return ("medium", ("minimal", "low", "medium", "high"), "OpenAI GPT-5 API")
    if model_key.startswith(("o1", "o3", "o4")):
        return ("medium", ("low", "medium", "high"), "OpenAI o-series API")
    return None


def reasoning_capability(*, model: str, adapter: str, base_url: str = "") -> dict[str, Any] | None:
    """Return safe UI metadata for reasoning controls supported by a model.

    Native provider protocols win over generic catalog metadata. For remaining
    OpenAI-compatible transports an explicit ``~/.loom/models.json`` (or
    ``LOOM_MODEL_CATALOG_JSON``) is authoritative. The file uses Codex's model
    catalog reasoning fields, so third-party providers can install capability
    metadata without requiring a Loom source-code change.
    """

    model_key = str(model or "").strip().casefold()
    adapter_key = str(adapter or "").strip().casefold()
    endpoint = str(base_url or "").strip().casefold()
    if not model_key:
        return None

    # MiniMax M3 has a provider-native thinking contract. Never let a generic
    # Codex-style effort catalog reinterpret it as ``openai-effort`` because the
    # resulting wire payload would use the wrong protocol.
    if "minimax-m3" in model_key or ("minimax" in endpoint and model_key in {"m3", "minimax-m3"}):
        return {
            "kind": ReasoningKind.MINIMAX_THINKING.value,
            "defaultValue": "adaptive",
            "options": list(_MINIMAX_M3_OPTIONS),
            "source": "MiniMax M3 hosted API",
        }

    if adapter_key in {"openai", "openai-compatible"}:
        catalog = catalog_reasoning_spec(model_key)
        if catalog is not None:
            options = _catalog_options(list(catalog.get("levels") or []))
            if options:
                return {
                    "kind": ReasoningKind.OPENAI_EFFORT.value,
                    "defaultValue": str(catalog["default"]),
                    "options": options,
                    "source": str(catalog["source"]),
                }

    if adapter_key not in {"openai", "openai-compatible"}:
        return None

    spec = _deepseek_model_spec(model_key) or _openai_model_spec(model_key)
    if spec is None:
        return None
    default_value, values, source = spec
    return {
        "kind": ReasoningKind.OPENAI_EFFORT.value,
        "defaultValue": default_value,
        "options": _openai_options(values),
        "source": source,
    }


def _supported_openai_efforts(capability: dict[str, Any] | None) -> tuple[set[str], str]:
    if not isinstance(capability, dict):
        return set(), ""
    supported = {
        str(option.get("value") or "")
        for option in capability.get("options", [])
        if isinstance(option, dict)
    }
    return supported, str(capability.get("defaultValue") or "")


def resolve_reasoning_wire_value(
    *,
    model: str,
    adapter: str,
    base_url: str,
    reasoning: ReasoningRequest,
) -> str:
    """Resolve a normalized selection to a transport-safe provider value."""

    if reasoning.kind is not ReasoningKind.OPENAI_EFFORT:
        return reasoning.value
    if reasoning.value not in {"ultra", "persistent"}:
        return reasoning.value

    capability = reasoning_capability(model=model, adapter=adapter, base_url=base_url)
    supported, default_value = _supported_openai_efforts(capability)

    if reasoning.value == "persistent" and default_value in supported:
        return default_value

    for fallback in ("max", "xhigh", "high", "medium", "low", "minimal", "none"):
        if fallback in supported:
            return fallback
    return "medium"


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


__all__ = [
    "reasoning_capability",
    "requires_reasoning_content_replay",
    "resolve_reasoning_wire_value",
    "resolved_reasoning",
]
