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

_OPENAI_OPTION_LIBRARY: dict[str, dict[str, Any]] = {
    "none": _option("none", "None", "Use the model's non-reasoning / lowest-latency mode."),
    "minimal": _option("minimal", "Minimal", "Use the smallest available reasoning budget."),
    "low": _option("low", "Low", "Fast responses with lighter reasoning."),
    "medium": _option("medium", "Medium", "Balances speed and reasoning depth for everyday work."),
    "high": _option("high", "High", "Greater reasoning depth for complex problems."),
    "xhigh": _option("xhigh", "Extra high", "Extra reasoning depth for difficult multi-step work."),
    "max": _option("max", "Max", "Maximum API reasoning depth for the hardest problems.", advanced=True),
    # ``ultra`` and ``persistent`` remain valid normalized values for forward
    # compatibility with Codex-style catalogs, but Loom's public OpenAI API
    # catalog does not advertise them unless a provider explicitly does so.
    "ultra": _option("ultra", "Ultra", "Provider-defined orchestration reasoning mode.", advanced=True),
    "persistent": _option("persistent", "Persistent", "Provider-defined persistent reasoning mode.", advanced=True),
}


def _openai_options(values: tuple[str, ...]) -> list[dict[str, Any]]:
    return [dict(_OPENAI_OPTION_LIBRARY[value]) for value in values]


def _model_slug(model_key: str) -> str:
    """Return the provider-local model slug from common namespaced ids.

    OpenAI-compatible relays frequently expose ids such as
    ``openai/gpt-5.6-sol`` or ``deepseek/deepseek-v4-pro``. Capability matching
    should follow the underlying model slug rather than disappear merely because
    a relay prepended its provider namespace.
    """

    value = str(model_key or "").strip().casefold()
    return value.rsplit("/", 1)[-1]


def _deepseek_model_spec(model_key: str) -> tuple[str, tuple[str, ...], str] | None:
    """Return the official DeepSeek V4 preset used for Codex-style clients.

    DeepSeek's current Codex integration publishes Low/High/Max with High as the
    default. Chat Completions accepts the same reasoning_effort values, while
    Max remains an advanced picker choice in Loom just as it is in Codex.
    """

    slug = _model_slug(model_key)
    if slug.startswith("deepseek-v4-"):
        return (
            "high",
            ("low", "high", "max"),
            "DeepSeek V4 Codex model catalog",
        )
    return None


def requires_reasoning_content_replay(*, model: str, adapter: str, base_url: str = "") -> bool:
    """Whether tool-bearing chat requests must replay provider reasoning state.

    DeepSeek V4 Chat Completions requires every prior assistant
    ``reasoning_content`` value to be sent back whenever ``tools`` is present.
    Keep this provider quirk behind the model catalog rather than leaking it into
    the provider-neutral runtime or applying it to OpenAI models that do not use
    this wire field.
    """

    adapter_key = str(adapter or "").strip().casefold()
    if adapter_key not in {"openai", "openai-compatible"}:
        return False
    return _deepseek_model_spec(str(model or "").strip().casefold()) is not None


def _openai_model_spec(model_key: str) -> tuple[str, tuple[str, ...], str] | None:
    """Return Loom's conservative public-API reasoning spec for a known model.

    Codex can advertise product-only levels such as ``ultra`` because it owns a
    richer orchestration/runtime layer. Loom currently calls public OpenAI or
    OpenAI-compatible endpoints, so the picker must expose only effort values
    that are valid request parameters for the underlying model family.
    """

    model_key = _model_slug(model_key)

    if model_key.startswith("gpt-6-astra"):
        return (
            "low",
            ("low", "medium", "high", "xhigh", "max"),
            "OpenAI GPT-6 Astra API",
        )

    if model_key.startswith("gpt-5.6"):
        return (
            "low",
            ("none", "low", "medium", "high", "xhigh", "max"),
            "OpenAI GPT-5.6 API",
        )

    if model_key.startswith("gpt-5.5-pro"):
        return (
            "high",
            ("medium", "high", "xhigh"),
            "OpenAI GPT-5.5 Pro API",
        )

    if model_key.startswith("gpt-5.5"):
        return (
            "medium",
            ("none", "low", "medium", "high", "xhigh"),
            "OpenAI GPT-5.5 API",
        )

    if model_key.startswith("gpt-5.4-pro"):
        return (
            "medium",
            ("medium", "high", "xhigh"),
            "OpenAI GPT-5.4 Pro API",
        )

    if model_key.startswith("gpt-5.4"):
        return (
            "medium",
            ("none", "low", "medium", "high", "xhigh"),
            "OpenAI GPT-5.4 API",
        )

    if model_key.startswith("gpt-5.2-pro"):
        return (
            "medium",
            ("medium", "high", "xhigh"),
            "OpenAI GPT-5.2 Pro API",
        )

    if model_key.startswith("gpt-5.2"):
        return (
            "none",
            ("none", "low", "medium", "high", "xhigh"),
            "OpenAI GPT-5.2 API",
        )

    if model_key.startswith("gpt-5.1"):
        return (
            "none",
            ("none", "low", "medium", "high"),
            "OpenAI GPT-5.1 API",
        )

    if model_key.startswith("gpt-5-pro"):
        return (
            "high",
            ("high",),
            "OpenAI GPT-5 Pro API",
        )

    if (
        model_key == "gpt-5"
        or model_key.startswith("gpt-5-")
        or model_key.startswith("gpt-5-mini")
        or model_key.startswith("gpt-5-nano")
    ):
        return (
            "medium",
            ("minimal", "low", "medium", "high"),
            "OpenAI GPT-5 API",
        )

    if model_key.startswith(("o1", "o3", "o4")):
        return (
            "medium",
            ("low", "medium", "high"),
            "OpenAI o-series API",
        )

    return None


def reasoning_capability(*, model: str, adapter: str, base_url: str = "") -> dict[str, Any] | None:
    """Return safe UI metadata for reasoning controls supported by a model."""

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
