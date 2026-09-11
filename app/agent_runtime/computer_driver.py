from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol


ComputerDriverEventListener = Callable[["ComputerDriverEvent"], None]
ComputerDriverCancelCheck = Callable[[], bool]


def _never_cancelled() -> bool:
    return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ComputerDriverError(RuntimeError):
    """Base error raised by a Computer Use driver."""


class ComputerDriverUnavailableError(ComputerDriverError):
    """Raised when a configured Computer Use driver cannot be started."""


@dataclass(frozen=True, slots=True)
class ComputerDriverEvent:
    """Provider-neutral event emitted while a desktop task is running.

    Drivers may have very different internal loops (UFO HostAgent/AppAgent,
    UI-TARS GUIAgent, provider-native computer tools, and so on). Loom consumes
    only this bounded event envelope so HUD/Inspector/diagnostics never depend on
    one driver's private action schema.
    """

    task_id: str
    sequence: int
    kind: str
    data: Mapping[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "sequence": int(self.sequence),
            "kind": str(self.kind),
            "created_at": self.created_at,
            "data": dict(self.data),
        }


@dataclass(frozen=True, slots=True)
class ComputerDriverResult:
    task_id: str
    status: str
    ok: bool
    summary: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)

    def to_safe_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "ok": bool(self.ok),
            "summary": self.summary,
            "data": dict(self.data),
        }


class ComputerTaskDriver(Protocol):
    """Stable Loom boundary for mature desktop automation engines."""

    name: str

    def status(self) -> Mapping[str, Any]: ...

    def run_task(
        self,
        task: str,
        *,
        stop_when: str = "",
        max_steps: int | None = None,
        on_event: ComputerDriverEventListener | None = None,
        is_cancelled: ComputerDriverCancelCheck = _never_cancelled,
    ) -> ComputerDriverResult: ...

    def pause(self) -> bool: ...

    def resume(self) -> bool: ...

    def cancel(self, *, reason: str = "user_requested") -> bool: ...

    def close(self) -> None: ...


__all__ = [
    "ComputerDriverCancelCheck",
    "ComputerDriverError",
    "ComputerDriverEvent",
    "ComputerDriverEventListener",
    "ComputerDriverResult",
    "ComputerDriverUnavailableError",
    "ComputerTaskDriver",
]
