from __future__ import annotations

from enum import Enum


class SandboxFailureKind(str, Enum):
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    CONFIGURATION = "configuration"


class SandboxExecutionError(RuntimeError):
    """Structured sandbox failure for execution paths that can prove the cause."""

    def __init__(
        self,
        kind: SandboxFailureKind | str,
        message: str,
        *,
        escalatable: bool = False,
    ) -> None:
        self.kind = SandboxFailureKind(kind)
        self.message = str(message or "").strip() or self.kind.value
        self.escalatable = bool(escalatable)
        super().__init__(self.message)

    def to_result_data(self) -> dict[str, object]:
        return {
            "failure_kind": "sandbox",
            "sandbox_failure": self.kind.value,
            "sandbox_escalatable": self.escalatable,
        }


__all__ = ["SandboxExecutionError", "SandboxFailureKind"]
