from __future__ import annotations

import json
from copy import deepcopy
from functools import lru_cache
from dataclasses import replace
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.ai import ToolDefinition

from .contracts import AgentEventKind, ToolEffect


_TOOL_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,127}$")
_SEARCH_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
ToolHandler = Callable[["ToolContext", dict[str, Any]], "ToolResult"]
CancelCheck = Callable[[], bool]
EventEmitter = Callable[[AgentEventKind, dict[str, object]], None]


def _never_cancelled() -> bool:
    return False


def _search_tokens(value: str) -> tuple[str, ...]:
    return tuple(_SEARCH_TOKEN_RE.findall(str(value or "").casefold()))


class ToolExposure(str, Enum):
    DIRECT = "direct"
    DEFERRED = "deferred"
    CODE_MODE_ONLY = "code_mode_only"
    HIDDEN = "hidden"


@dataclass(frozen=True, slots=True)
class ToolContext:
    session_id: str
    turn_id: str
    workspace: Path
    permission_mode: str = "approval"
    is_cancelled: CancelCheck = _never_cancelled
    services: Mapping[str, Any] = field(default_factory=dict)
    emit_event: EventEmitter | None = None

    @property
    def cancelled(self) -> bool:
        return bool(self.is_cancelled())

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RuntimeError("agent turn cancellation requested")

    def resolve_workspace_path(self, relative_path: str) -> Path:
        value = str(relative_path or "").strip()
        if not value:
            raise ValueError("workspace path must not be empty")
        candidate = (self.workspace / value).resolve()
        root = self.workspace.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("tool path escapes the agent workspace") from exc
        return candidate

    def service(self, name: str, *, required: bool = True) -> Any:
        key = str(name or "").strip()
        if not key:
            raise ValueError("tool service name must not be empty")
        value = self.services.get(key)
        if value is None and required:
            raise RuntimeError(f"tool runtime service is unavailable: {key}")
        return value

    def emit(self, kind: AgentEventKind, data: dict[str, object]) -> None:
        if self.emit_event is not None:
            self.emit_event(AgentEventKind(kind), dict(data))


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    content: str
    data: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        content = str(self.content or "")
        if not isinstance(self.data, dict):
            raise TypeError("tool result data must be a JSON object")
        try:
            json.dumps(self.data, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise TypeError("tool result data must be JSON serializable") from exc
        object.__setattr__(self, "content", content)

    def model_payload(self, *, max_chars: int) -> str:
        limit = max(1, int(max_chars))
        payload: dict[str, Any] = {
            "ok": bool(self.ok),
            "content": self.content,
            "data": self.data,
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if len(serialized) <= limit:
            return serialized
        reserve = 180
        truncated = self.content[: max(0, limit - reserve)]
        return json.dumps(
            {
                "ok": bool(self.ok),
                "content": truncated,
                "data": {"truncated": True},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class AgentTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler
    effect: ToolEffect = ToolEffect.READ_ONLY
    exposure: ToolExposure = ToolExposure.DIRECT
    binding_key: str = ""

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        description = str(self.description or "").strip()
        if not _TOOL_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid agent tool name: {self.name!r}")
        if not description:
            raise ValueError("agent tool description must not be empty")
        if not isinstance(self.input_schema, dict) or self.input_schema.get("type") != "object":
            raise ValueError("agent tool input_schema must be an object JSON schema")
        if not callable(self.handler):
            raise TypeError("agent tool handler must be callable")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "effect", ToolEffect(self.effect))
        object.__setattr__(self, "exposure", ToolExposure(self.exposure))

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=deepcopy(self.input_schema),
        )


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    """Legacy approval policy retained for Loom 0.1 callers.

    Runtime v2 uses permission profiles plus approval policy. When a session uses
    the compatibility ``approval`` mode, this policy still controls which tool
    effects are auto-approved so existing embedding code keeps its behavior.
    """

    auto_approved_effects: frozenset[ToolEffect] = frozenset({ToolEffect.READ_ONLY})

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "auto_approved_effects",
            frozenset(ToolEffect(value) for value in self.auto_approved_effects),
        )

    def requires_approval(self, tool: AgentTool) -> bool:
        return tool.effect not in self.auto_approved_effects


class ToolRouter:
    """Immutable per-step view of tools exposed directly to the model."""

    def __init__(self, tools: tuple[AgentTool, ...]) -> None:
        self._tools = {tool.name: replace(tool, input_schema=deepcopy(tool.input_schema)) for tool in tools}

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(str(name or "").strip())

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._tools[name].definition() for name in sorted(self._tools))

    def all(self) -> tuple[AgentTool, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))


class ToolRegistry:
    def __init__(self, tools: tuple[AgentTool, ...] = ()) -> None:
        self._tools: dict[str, AgentTool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: AgentTool) -> None:
        if not isinstance(tool, AgentTool):
            raise TypeError("tool must be AgentTool")
        if tool.name in self._tools:
            raise ValueError(f"agent tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> AgentTool | None:
        return self._tools.get(str(name or "").strip())

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return self.router().definitions()

    def all(self) -> tuple[AgentTool, ...]:
        return tuple(self._tools[name] for name in sorted(self._tools))

    def deferred(self) -> tuple[AgentTool, ...]:
        return tuple(tool for tool in self.all() if tool.exposure is ToolExposure.DEFERRED)

    def search_deferred(self, query: str, *, limit: int = 5) -> tuple[AgentTool, ...]:
        raw_query = str(query or "").strip()
        if not raw_query:
            raise ValueError("tool search query must not be empty")
        resolved_limit = max(1, min(20, int(limit)))
        query_folded = raw_query.casefold()
        query_tokens = _search_tokens(raw_query)
        scored: list[tuple[int, str, AgentTool]] = []

        for tool in self.deferred():
            name_folded = tool.name.casefold()
            description_folded = tool.description.casefold()
            name_tokens = set(_search_tokens(tool.name))
            description_tokens = set(_search_tokens(tool.description))
            score = 0

            if query_folded == name_folded:
                score += 10_000
            elif name_folded.startswith(query_folded):
                score += 7_000
            elif query_folded in name_folded:
                score += 5_000
            elif query_folded in description_folded:
                score += 1_500

            for token in query_tokens:
                if token in name_tokens:
                    score += 700
                elif any(part.startswith(token) or token.startswith(part) for part in name_tokens):
                    score += 350
                if token in description_tokens:
                    score += 120

            if score > 0:
                scored.append((score, tool.name, tool))

        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in scored[:resolved_limit])

    def router(self, *, activated_names: Sequence[str] = ()) -> ToolRouter:
        activated = {str(name or "").strip() for name in activated_names if str(name or "").strip()}
        visible: list[AgentTool] = []
        for tool in self.all():
            if tool.exposure is ToolExposure.DIRECT:
                visible.append(tool)
            elif tool.exposure is ToolExposure.DEFERRED and tool.name in activated:
                visible.append(tool)
        return ToolRouter(tuple(visible))


@lru_cache(maxsize=256)
def _schema_validator(serialized: str):
    from jsonschema import Draft202012Validator, validators
    from referencing import Registry
    from referencing.exceptions import NoSuchResource
    def no_network(uri):
        raise NoSuchResource(ref=uri)
    schema = json.loads(serialized)
    cls = validators.validator_for(schema, default=Draft202012Validator)
    cls.check_schema(schema)
    return cls(schema, registry=Registry(retrieve=no_network))


def validate_tool_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    if schema.get("type") != "object":
        raise ValueError("tool root schema must be type=object")
    try:
        validator = _schema_validator(json.dumps(schema, sort_keys=True))
        error = next(validator.iter_errors(arguments), None)
    except Exception as exc:
        raise ValueError(f"invalid or unresolved tool schema: {exc}") from exc
    if error is not None:
        path = "$" + "".join(f".{p}" for p in error.absolute_path)
        raise ValueError(f"{path}: {error.message}")


__all__ = [
    "AgentTool",
    "CancelCheck",
    "EventEmitter",
    "ToolContext",
    "ToolExposure",
    "ToolHandler",
    "ToolPolicy",
    "ToolRegistry",
    "ToolResult",
    "ToolRouter",
    "validate_tool_arguments",
]
