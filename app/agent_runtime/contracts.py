from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.ai import AIMessage, ModelUsage, ToolCall


class AgentStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    LIMIT_REACHED = "limit_reached"


class AgentEventKind(str, Enum):
    SESSION_CREATED = "session_created"
    PERMISSION_CHANGED = "permission_changed"
    GOAL_UPDATED = "goal_updated"
    QUEUE_ENQUEUED = "queue_enqueued"
    QUEUE_DISPATCHED = "queue_dispatched"
    QUEUE_REMOVED = "queue_removed"
    HISTORY_REPAIRED = "history_repaired"
    CONTEXT_CHECKPOINTED = "context_checkpointed"
    MEMORY_EXTRACTED = "memory_extracted"
    MEMORY_CONSOLIDATED = "memory_consolidated"
    MEMORY_FORGOTTEN = "memory_forgotten"
    TURN_STARTED = "turn_started"
    USER_MESSAGE = "user_message"
    MODEL_REQUESTED = "model_requested"
    MODEL_RESPONSE_REJECTED = "model_response_rejected"
    MODEL_RESPONSE = "model_response"
    TOOL_REQUESTED = "tool_requested"
    TOOL_APPROVAL_REQUIRED = "tool_approval_required"
    TOOL_APPROVED = "tool_approved"
    TOOL_DENIED = "tool_denied"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    PROCESS_STARTED = "process_started"
    PROCESS_OUTPUT = "process_output"
    PROCESS_EXITED = "process_exited"
    TURN_DIFF_UPDATED = "turn_diff_updated"
    TURN_COMPLETED = "turn_completed"
    TURN_FAILED = "turn_failed"
    TURN_CANCELLED = "turn_cancelled"
    TURN_INTERRUPTED = "turn_interrupted"
    LIMIT_REACHED = "limit_reached"


class ToolEffect(str, Enum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"
    SENSITIVE = "sensitive"


class PermissionMode(str, Enum):
    READ_ONLY = "read-only"
    APPROVAL = "approval"
    WORKSPACE = "workspace"
    FULL_ACCESS = "full-access"


class ApprovalKind(str, Enum):
    INITIAL = "initial"
    SANDBOX_ESCALATION = "sandbox_escalation"


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_model_steps: int = 0
    max_tool_calls: int = 0
    # 0 means unlimited. Context rollover is token-driven by default, matching
    # Codex; a host may still set a positive message cap as an explicit guard.
    max_messages: int = 0
    max_tool_result_chars: int = 20_000
    # ``None`` means the host did not declare this model's limits, which is the
    # normal case for an arbitrary OpenAI-compatible endpoint. A default number
    # here would be indistinguishable from a real declaration, and treating one
    # as the other is what made Loom budget every unknown model as 32k.
    context_window_tokens: int | None = None
    output_reserve_tokens: int | None = None
    model_retries: int = 2

    def __post_init__(self) -> None:
        if self.model_retries < 0 or self.model_retries > 5:
            raise ValueError("model_retries must be within 0..5")
        if (
            self.output_reserve_tokens is not None
            and self.context_window_tokens is not None
            and self.output_reserve_tokens >= self.context_window_tokens
        ):
            raise ValueError("output reserve must be smaller than the context window")
        for name in ("max_model_steps", "max_tool_calls"):
            value = int(getattr(self, name))
            if value < 0:
                raise ValueError(f"{name} must be non-negative; 0 means unlimited")
            object.__setattr__(self, name, value)
        max_messages = int(self.max_messages)
        if max_messages < 0:
            raise ValueError("max_messages must be non-negative; 0 means unlimited")
        object.__setattr__(self, "max_messages", max_messages)
        max_tool_result_chars = int(self.max_tool_result_chars)
        if max_tool_result_chars < 1:
            raise ValueError("max_tool_result_chars must be positive")
        object.__setattr__(self, "max_tool_result_chars", max_tool_result_chars)
        for name in ("context_window_tokens", "output_reserve_tokens"):
            raw = getattr(self, name)
            if raw is None:
                continue
            value = int(raw)
            if value < 1:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class PendingToolApproval:
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    effect: ToolEffect
    reason: str
    kind: ApprovalKind = ApprovalKind.INITIAL
    retry_reason: str = ""

    def __post_init__(self) -> None:
        call_id = str(self.call_id or "").strip()
        tool_name = str(self.tool_name or "").strip()
        reason = str(self.reason or "").strip()
        retry_reason = str(self.retry_reason or "").strip()
        if not call_id or not tool_name:
            raise ValueError("pending approval requires call_id and tool_name")
        if not isinstance(self.arguments, dict):
            raise TypeError("pending approval arguments must be a JSON object")
        object.__setattr__(self, "call_id", call_id)
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(self, "effect", ToolEffect(self.effect))
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "kind", ApprovalKind(self.kind))
        object.__setattr__(self, "retry_reason", retry_reason)


@dataclass(slots=True)
class AgentSession:
    session_id: str
    profile_id: str
    system_prompt: str
    workspace_dir: str
    created_at: str
    updated_at: str
    system_prompt_version: int = 0
    permission_mode: PermissionMode = PermissionMode.APPROVAL
    status: AgentStatus = AgentStatus.IDLE
    current_turn_id: str = ""
    forked_from_id: str = ""
    communication_language: str = "auto"
    model_selection: str = ""
    model: str = ""
    model_provider: str = ""
    model_base_url: str = ""
    model_vision: bool = True
    reasoning_kind: str = ""
    reasoning_value: str = ""
    messages: list[AIMessage] = field(default_factory=list)
    pending_tool_calls: list[ToolCall] = field(default_factory=list)
    pending_step_id: str = ""
    pending_bindings: dict[str, str] = field(default_factory=dict)
    steering_ids: list[str] = field(default_factory=list)
    active_skills: dict[str, str] = field(default_factory=dict)
    pending_approval: PendingToolApproval | None = None
    model_steps: int = 0
    tool_calls: int = 0
    usage: ModelUsage = field(default_factory=ModelUsage)
    final_text: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        self.session_id = str(self.session_id or "").strip()
        self.profile_id = str(self.profile_id or "").strip().casefold()
        self.system_prompt = str(self.system_prompt or "").strip()
        self.workspace_dir = str(self.workspace_dir or "").strip()
        self.system_prompt_version = max(0, int(self.system_prompt_version or 0))
        self.forked_from_id = str(self.forked_from_id or "").strip()
        self.model_selection = str(self.model_selection or "").strip()
        self.model = str(self.model or "").strip()
        self.model_provider = str(self.model_provider or "").strip().casefold()
        self.model_base_url = str(self.model_base_url or "").strip().rstrip("/")
        self.model_vision = bool(self.model_vision)
        self.reasoning_kind = str(self.reasoning_kind or "").strip()
        self.reasoning_value = str(self.reasoning_value or "").strip()
        language = str(self.communication_language or "auto").strip().casefold()
        self.communication_language = language if language in {
            "auto", "zh", "ja", "ko", "cyrillic", "arabic", "latin"
        } else "auto"
        self.permission_mode = PermissionMode(self.permission_mode)
        self.status = AgentStatus(self.status)
        self.messages = list(self.messages)
        self.pending_tool_calls = list(self.pending_tool_calls)
        if not self.session_id or not self.profile_id or not self.workspace_dir:
            raise ValueError("agent session requires session_id, profile_id and workspace_dir")
        if not self.system_prompt:
            raise ValueError("agent session system_prompt must not be empty")


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_id: str
    session_id: str
    turn_id: str
    kind: AgentEventKind
    created_at: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    session_id: str
    turn_id: str
    status: AgentStatus
    final_text: str = ""
    pending_approval: PendingToolApproval | None = None
    usage: ModelUsage = field(default_factory=ModelUsage)
    error: str = ""


__all__ = [
    "AgentEvent",
    "AgentEventKind",
    "AgentLimits",
    "AgentRunResult",
    "AgentSession",
    "AgentStatus",
    "ApprovalKind",
    "PendingToolApproval",
    "PermissionMode",
    "ToolEffect",
]
