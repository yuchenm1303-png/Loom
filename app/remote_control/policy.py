from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RemoteControlPolicy:
    """A channel exposure ceiling, not a replacement permission engine.

    Loom's PermissionEngine remains authoritative. This policy only prevents a
    remote channel from asking Loom to operate a thread whose existing
    permission profile is broader than the channel is allowed to control.
    """

    channel: str = "chatgpt"
    new_thread_permission_mode: str = "approval"
    allowed_active_permission_modes: tuple[str, ...] = ("read-only", "approval")
    max_thread_list_limit: int = 100

    def __post_init__(self) -> None:
        channel = str(self.channel or "").strip()
        if not channel:
            raise ValueError("remote channel must not be empty")
        allowed = tuple(str(value or "").strip() for value in self.allowed_active_permission_modes)
        if not allowed or any(not value for value in allowed):
            raise ValueError("allowed_active_permission_modes must contain non-empty values")
        if self.new_thread_permission_mode not in allowed:
            raise ValueError("new_thread_permission_mode must be allowed for active remote control")
        if not 1 <= int(self.max_thread_list_limit) <= 200:
            raise ValueError("max_thread_list_limit must be within 1..200")
