from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReasoningKind(str, Enum):
    """Wire-level reasoning control families supported by Loom providers."""

    OPENAI_EFFORT = "openai-effort"
    MINIMAX_THINKING = "minimax-thinking"


_OPENAI_KNOWN_EFFORTS = frozenset(
    {
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
        "persistent",
    }
)
_MINIMAX_THINKING_MODES = frozenset({"disabled", "adaptive", "enabled"})


@dataclass(frozen=True, slots=True)
class ReasoningRequest:
    """Normalized reasoning selection carried with every model sampling step.

    The UI can expose Codex-style model-specific choices without pretending that
    every provider speaks the same request schema. The OpenAI family receives a
    ``reasoning_effort`` value, while MiniMax M3 receives ``thinking.type``.
    """

    kind: ReasoningKind
    value: str

    def __post_init__(self) -> None:
        kind = ReasoningKind(self.kind)
        value = str(self.value or "").strip().casefold()
        if not value:
            raise ValueError("reasoning value must not be empty")
        if kind is ReasoningKind.MINIMAX_THINKING and value not in _MINIMAX_THINKING_MODES:
            supported = ", ".join(sorted(_MINIMAX_THINKING_MODES))
            raise ValueError(f"MiniMax thinking mode must be one of: {supported}")
        # OpenAI/Codex deliberately accepts future non-empty effort strings. The
        # upstream model catalog can advertise a new value before this client is
        # updated, matching Codex's Custom(String) forward-compatibility model.
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "value", value)

    @property
    def known_openai_effort(self) -> bool:
        return self.kind is ReasoningKind.OPENAI_EFFORT and self.value in _OPENAI_KNOWN_EFFORTS

    def as_safe_dict(self) -> dict[str, str]:
        return {"kind": self.kind.value, "value": self.value}

    @classmethod
    def from_values(cls, kind: str | ReasoningKind | None, value: str | None) -> "ReasoningRequest | None":
        raw_kind = str(kind or "").strip()
        raw_value = str(value or "").strip()
        if not raw_kind and not raw_value:
            return None
        if not raw_kind or not raw_value:
            raise ValueError("reasoning kind and value must be provided together")
        return cls(ReasoningKind(raw_kind), raw_value)


__all__ = ["ReasoningKind", "ReasoningRequest"]
