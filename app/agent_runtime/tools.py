from __future__ import annotations

import sys
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
CapabilitySettings = Mapping[str, bool] | None
_GLOBAL_CAPABILITY_SETTINGS: dict[str, bool] = {}


def _never_cancelled() -> bool:
    return False


def _search_tokens(value: str) -> tuple[str, ...]:
    return tuple(_SEARCH_TOKEN_RE.findall(str(value or "").casefold()))


def tool_capability_name(tool_name: str) -> str:
    name = str(tool_name or "").strip()
    if name.startswith("computer_"):
        return "computerUse"
    if name.startswith("browser_"):
        return "browserUse"
    if name.startswith("web_search"):
        return "webSearch"
    if name.startswith("mcp."):
        return "mcp"
    if name.startswith("skill_"):
        return "skills"
    if name == "tool_search":
        return "toolSearch"
    if name == "code_mode":
        return "codeMode"
    return ""


def set_tool_capability_settings(settings: Mapping[str, bool] | None) -> None:
    global _GLOBAL_CAPABILITY_SETTINGS
    if not settings:
        _GLOBAL_CAPABILITY_SETTINGS = {}
        return
    _GLOBAL_CAPABILITY_SETTINGS = {
        str(key): bool(value)
        for key, value in dict(settings).items()
        if isinstance(key, str)
    }


def get_tool_capability_settings() -> dict[str, bool]:
    return dict(_GLOBAL_CAPABILITY_SETTINGS)


def _capability_allows(tool: "AgentTool", capability_settings: CapabilitySettings) -> bool:
    if capability_settings is None:
        capability_settings = _GLOBAL_CAPABILITY_SETTINGS
    if not capability_settings:
        return True
    capability = tool_capability_name(tool.name)
    if not capability:
        return True
    return capability_settings.get(capability) is not False


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


def _approx_model_tokens(value: str) -> int:
    """Cheap tokenizer-independent bound used for model-visible tool payloads."""

    return max(1, (len(str(value or "").encode("utf-8")) + 2) // 3)


def _truncate_middle_for_model(value: str, max_tokens: int) -> str:
    """Retain both the beginning and the outcome-bearing tail of a tool result."""

    text = str(value or "")
    budget = max(1, int(max_tokens))
    if _approx_model_tokens(text) <= budget:
        return text

    marker = "\n\n[... middle of tool output omitted from model context ...]\n\n"
    byte_budget = max(24, budget * 3 - len(marker.encode("utf-8")))
    raw = text.encode("utf-8")
    head_budget = byte_budget * 3 // 5
    tail_budget = byte_budget - head_budget
    head = raw[:head_budget].decode("utf-8", errors="ignore")
    tail = raw[-tail_budget:].decode("utf-8", errors="ignore") if tail_budget else ""
    return head + marker + tail


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

    def model_payload(
        self,
        *,
        max_tokens: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        """Return the bounded copy stored in active model history.

        The durable TOOL_COMPLETED/TOOL_FAILED event stores the original content
        and data separately and remains authoritative. Model history can therefore
        keep a compact, explicitly recoverable projection from the moment the
        result is recorded, matching Codex's history-boundary truncation model.

        max_chars remains as a compatibility fallback for callers outside the
        agent runtime. New runtime code should pass max_tokens.
        """

        payload: dict[str, Any] = {
            "ok": bool(self.ok),
            "content": self.content,
            "data": self.data,
        }
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

        if max_tokens is not None:
            token_limit = max(128, int(max_tokens))
            original_tokens = _approx_model_tokens(serialized)
            if original_tokens <= token_limit:
                return serialized

            # Reserve room for the JSON envelope and recovery metadata, then keep
            # a head+tail preview. Command/test failures usually live at the tail.
            preview_budget = max(96, token_limit - 180)
            preview = _truncate_middle_for_model(self.content, preview_budget)
            projected = {
                "ok": bool(self.ok),
                "content": preview,
                "data": {
                    "truncated": True,
                    "truncation": "head_tail",
                    "original_approx_tokens": original_tokens,
                    "model_context_token_limit": token_limit,
                    "exact_result_remains_in_durable_transcript": True,
                },
            }
            bounded = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
            if _approx_model_tokens(bounded) > token_limit:
                tighter_budget = max(
                    32,
                    preview_budget - (_approx_model_tokens(bounded) - token_limit) - 16,
                )
                projected["content"] = _truncate_middle_for_model(self.content, tighter_budget)
                bounded = json.dumps(projected, ensure_ascii=False, separators=(",", ":"))
            return bounded

        if max_chars is None:
            return serialized
        char_limit = max(1, int(max_chars))
        if len(serialized) <= char_limit:
            return serialized
        reserve = 220
        preview = self.content[: max(0, char_limit - reserve)]
        return json.dumps(
            {
                "ok": bool(self.ok),
                "content": preview,
                "data": {
                    "truncated": True,
                    "truncation": "legacy_char_prefix",
                    "exact_result_remains_in_durable_transcript": True,
                },
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

    def deferred(self, *, capability_settings: CapabilitySettings = None) -> tuple[AgentTool, ...]:
        return tuple(
            tool
            for tool in self.all()
            if tool.exposure is ToolExposure.DEFERRED and _capability_allows(tool, capability_settings)
        )

    def search_deferred(
        self,
        query: str,
        *,
        limit: int = 5,
        capability_settings: CapabilitySettings = None,
    ) -> tuple[AgentTool, ...]:
        raw_query = str(query or "").strip()
        if not raw_query:
            raise ValueError("tool search query must not be empty")
        resolved_limit = max(1, min(20, int(limit)))
        query_folded = raw_query.casefold()
        query_tokens = _search_tokens(raw_query)
        scored: list[tuple[int, str, AgentTool]] = []

        for tool in self.deferred(capability_settings=capability_settings):
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

    def router(
        self,
        *,
        activated_names: Sequence[str] = (),
        capability_settings: CapabilitySettings = None,
    ) -> ToolRouter:
        activated = {str(name or "").strip() for name in activated_names if str(name or "").strip()}
        visible: list[AgentTool] = []
        for tool in self.all():
            if not _capability_allows(tool, capability_settings):
                continue
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


class ToolValidationUnavailable(RuntimeError):
    """Loom cannot validate any tool call, so nothing can run.

    Kept distinct from a bad schema. A missing validator library made every
    tool -- including ``echo`` -- fail with "invalid or unresolved tool schema",
    which reads like a per-tool problem. The model reported Loom's schema layer
    as broken and invented an explanation, because that is what it was told.
    """


# A browser call whose URL carried a credential is rewritten to carry this
# marker instead of being executed: the redacted URL still reaches durable
# history, the secret never reaches the browser. No tool declares the marker, so
# validation refuses the call on its own - but "Additional properties are not
# allowed" tells the model nothing it can act on, and it spent its next turns
# guessing at the schema instead of at the URL.
BLOCKED_SENSITIVE_INPUT_ARGUMENT = "_loom_blocked_sensitive_input"


def validate_tool_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be a JSON object")
    if BLOCKED_SENSITIVE_INPUT_ARGUMENT in arguments:
        raise ValueError(
            "this call was refused before it reached the browser because its URL carried "
            "something credential-shaped (a token, key, password, or session id). The secret "
            "is not available to you. Reach the page another way, or ask the user to open it."
        )
    if schema.get("type") != "object":
        raise ValueError("tool root schema must be type=object")
    try:
        validator = _schema_validator(json.dumps(schema, sort_keys=True))
        error = next(validator.iter_errors(arguments), None)
    except ImportError as exc:
        raise ToolValidationUnavailable(
            f"Loom is installed without its schema validator ({exc}). No tool can run. "
            f"Reinstall Loom for the interpreter running it: "
            f'"{sys.executable}" -m pip install -e .'
        ) from exc
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
    "get_tool_capability_settings",
    "set_tool_capability_settings",
    "tool_capability_name",
    "validate_tool_arguments",
]
