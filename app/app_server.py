from __future__ import annotations

import copy
import io
import json
import queue
import sys
import threading
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, TextIO

from app.ai import AGENT_FAST_ROLE, ImagePart, MessageRole, TextPart
from app.agent_runtime import AgentEvent, AgentEventKind, AgentStatus, PermissionMode


PROTOCOL_NAME = "loom-app-server"
PROTOCOL_VERSION = 1
_MAX_MESSAGE_BYTES = 1_000_000
_DEFAULT_INGRESS_LIMIT = 64
_DEFAULT_OUTBOUND_LIMIT = 256


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str, data: Any | None = None) -> None:
        super().__init__(message)
        self.code = int(code)
        self.message = str(message)
        self.data = data

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            payload["data"] = self.data
        return payload


NotificationListener = Callable[[str, dict[str, Any]], None]


def _message_text(message: Any) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content:
        if isinstance(part, TextPart):
            parts.append(part.text)
        elif isinstance(part, ImagePart):
            parts.append("[image]")
    return "\n".join(parts)


def _message_record(message: Any) -> dict[str, Any]:
    return {
        "role": message.role.value,
        "content": _message_text(message),
        "name": message.name,
        "toolCallId": message.tool_call_id or None,
        "toolCalls": [
            {
                "callId": call.call_id,
                "name": call.name,
                "arguments": copy.deepcopy(call.arguments),
            }
            for call in message.tool_calls
        ],
    }


def _usage_record(usage: Any) -> dict[str, int]:
    return {
        "inputTokens": int(getattr(usage, "input_tokens", 0) or 0),
        "outputTokens": int(getattr(usage, "output_tokens", 0) or 0),
        "totalTokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _approval_record(pending: Any) -> dict[str, Any] | None:
    if pending is None:
        return None
    return {
        "callId": pending.call_id,
        "toolName": pending.tool_name,
        "arguments": copy.deepcopy(pending.arguments),
        "effect": pending.effect.value,
        "reason": pending.reason,
    }


def _thread_title(session: Any) -> str:
    for message in session.messages:
        if message.role is MessageRole.USER:
            text = _message_text(message).strip().replace("\n", " ")
            if text:
                return text[:80]
    workspace = Path(session.workspace_dir)
    return workspace.name or "New thread"


def _thread_record(session: Any, *, active: bool = False) -> dict[str, Any]:
    return {
        "id": session.session_id,
        "title": _thread_title(session),
        "profileId": session.profile_id,
        "workspace": session.workspace_dir,
        "permissionMode": session.permission_mode.value,
        "status": session.status.value,
        "currentTurnId": session.current_turn_id or None,
        "forkedFromId": getattr(session, "forked_from_id", "") or None,
        "createdAt": session.created_at,
        "updatedAt": session.updated_at,
        "active": bool(active),
        "usage": _usage_record(session.usage),
    }


def _event_record(event: AgentEvent) -> dict[str, Any]:
    return {
        "eventId": event.event_id,
        "threadId": event.session_id,
        "turnId": event.turn_id or None,
        "kind": event.kind.value,
        "createdAt": event.created_at,
        "data": copy.deepcopy(event.data),
    }


def _terminal_turn_status(kind: AgentEventKind) -> str | None:
    mapping = {
        AgentEventKind.TURN_COMPLETED: AgentStatus.COMPLETED.value,
        AgentEventKind.TURN_FAILED: AgentStatus.FAILED.value,
        AgentEventKind.TURN_CANCELLED: AgentStatus.CANCELLED.value,
        AgentEventKind.TURN_INTERRUPTED: AgentStatus.INTERRUPTED.value,
        AgentEventKind.LIMIT_REACHED: AgentStatus.LIMIT_REACHED.value,
    }
    return mapping.get(kind)


def _tool_item_id(call_id: str) -> str:
    return f"tool:{call_id}"


def _approval_item_id(call_id: str) -> str:
    return f"approval:{call_id}"


def _process_item_id(process_id: str) -> str:
    return f"process:{process_id}"


def _assistant_item_id(event_id: str) -> str:
    return f"assistant:{event_id}"


def _user_item_id(event_id: str) -> str:
    return f"user:{event_id}"


def _diff_item_id(event_id: str) -> str:
    return f"diff:{event_id}"


def _error_item_id(event_id: str) -> str:
    return f"error:{event_id}"


def _base_item(event: AgentEvent, *, item_id: str, item_type: str, status: str) -> dict[str, Any]:
    return {
        "id": item_id,
        "threadId": event.session_id,
        "turnId": event.turn_id,
        "type": item_type,
        "status": status,
        "createdAt": event.created_at,
        "updatedAt": event.created_at,
    }


def _event_item_identity(event: AgentEvent) -> tuple[str, str] | None:
    data = event.data
    if event.kind is AgentEventKind.USER_MESSAGE:
        return _user_item_id(event.event_id), "user_message"
    if event.kind is AgentEventKind.MODEL_RESPONSE and str(data.get("text") or ""):
        return _assistant_item_id(event.event_id), "assistant_message"
    if event.kind in {
        AgentEventKind.TOOL_REQUESTED,
        AgentEventKind.TOOL_STARTED,
        AgentEventKind.TOOL_COMPLETED,
        AgentEventKind.TOOL_FAILED,
        AgentEventKind.TOOL_DENIED,
    }:
        call_id = str(data.get("call_id") or "")
        if call_id:
            return _tool_item_id(call_id), "tool_call"
    if event.kind in {
        AgentEventKind.TOOL_APPROVAL_REQUIRED,
        AgentEventKind.TOOL_APPROVED,
    } or (event.kind is AgentEventKind.TOOL_DENIED and str(data.get("source") or "") == "user"):
        call_id = str(data.get("call_id") or "")
        if call_id:
            return _approval_item_id(call_id), "approval"
    if event.kind in {
        AgentEventKind.PROCESS_STARTED,
        AgentEventKind.PROCESS_OUTPUT,
        AgentEventKind.PROCESS_EXITED,
    }:
        process_id = str(data.get("process_id") or "")
        if process_id:
            return _process_item_id(process_id), "process"
    if event.kind is AgentEventKind.TURN_DIFF_UPDATED:
        return _diff_item_id(event.event_id), "file_edit"
    if event.kind in {
        AgentEventKind.TURN_FAILED,
        AgentEventKind.TURN_CANCELLED,
        AgentEventKind.TURN_INTERRUPTED,
        AgentEventKind.LIMIT_REACHED,
    }:
        return _error_item_id(event.event_id), "error"
    return None


def _apply_event_to_item(item: dict[str, Any], event: AgentEvent) -> None:
    data = event.data
    item["updatedAt"] = event.created_at
    kind = event.kind
    if kind is AgentEventKind.USER_MESSAGE:
        item["status"] = "completed"
        item["text"] = str(data.get("text") or "")
        item["source"] = str(data.get("source") or "user")
    elif kind is AgentEventKind.MODEL_RESPONSE:
        item["status"] = "completed"
        item["text"] = str(data.get("text") or "")
        item["finishReason"] = str(data.get("finish_reason") or "") or None
        item["responseId"] = str(data.get("response_id") or "") or None
        usage = data.get("usage")
        if isinstance(usage, dict):
            item["usage"] = {
                "inputTokens": int(usage.get("input_tokens") or 0),
                "outputTokens": int(usage.get("output_tokens") or 0),
                "totalTokens": int(usage.get("total_tokens") or 0),
            }
    elif kind is AgentEventKind.TOOL_REQUESTED:
        item["status"] = "started"
        item["callId"] = str(data.get("call_id") or "")
        item["toolName"] = str(data.get("tool") or "")
        item["arguments"] = copy.deepcopy(data.get("arguments") or {})
        item["nested"] = bool(data.get("nested"))
        item["parentCallId"] = str(data.get("parent_call_id") or "") or None
    elif kind is AgentEventKind.TOOL_STARTED:
        item["status"] = "running"
    elif kind in {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}:
        item["status"] = "completed" if kind is AgentEventKind.TOOL_COMPLETED else "failed"
        item["ok"] = bool(data.get("ok"))
        item["content"] = str(data.get("content") or "")
        item["result"] = copy.deepcopy(data.get("data") or {})
    elif kind is AgentEventKind.TOOL_DENIED:
        item["status"] = "denied"
        item["reason"] = str(data.get("reason") or "")
        item["source"] = str(data.get("source") or "")
    elif kind is AgentEventKind.TOOL_APPROVAL_REQUIRED:
        item["status"] = "waiting"
        item["callId"] = str(data.get("call_id") or "")
        item["toolName"] = str(data.get("tool") or "")
        item["arguments"] = copy.deepcopy(data.get("arguments") or {})
        item["effect"] = str(data.get("effect") or "")
        item["reason"] = str(data.get("reason") or "")
    elif kind is AgentEventKind.TOOL_APPROVED:
        item["status"] = "approved"
    elif kind is AgentEventKind.PROCESS_STARTED:
        item["status"] = "running"
        item["processId"] = str(data.get("process_id") or "")
        item["argv"] = copy.deepcopy(data.get("argv") or [])
        item["cwd"] = str(data.get("cwd") or "")
        item["background"] = bool(data.get("background"))
        item["sandbox"] = copy.deepcopy(data.get("sandbox") or {})
    elif kind is AgentEventKind.PROCESS_OUTPUT:
        item["status"] = "running"
        item.setdefault("stdout", "")
        item.setdefault("stderr", "")
        item["stdout"] += str(data.get("stdout") or "")
        item["stderr"] += str(data.get("stderr") or "")
    elif kind is AgentEventKind.PROCESS_EXITED:
        item["status"] = "completed"
        item["returncode"] = data.get("returncode")
        item["timedOut"] = bool(data.get("timed_out"))
        item["sandbox"] = copy.deepcopy(data.get("sandbox") or item.get("sandbox") or {})
    elif kind is AgentEventKind.TURN_DIFF_UPDATED:
        item["status"] = "completed"
        item["revision"] = int(data.get("revision") or 0)
        item["paths"] = list(data.get("paths") or [])
        item["diff"] = str(data.get("diff") or "")
        item["truncated"] = bool(data.get("truncated"))
    elif kind in {
        AgentEventKind.TURN_FAILED,
        AgentEventKind.TURN_CANCELLED,
        AgentEventKind.TURN_INTERRUPTED,
        AgentEventKind.LIMIT_REACHED,
    }:
        item["status"] = "completed"
        item["error"] = str(data.get("error") or data.get("reason") or kind.value)


def _turn_records(session: Any, events: tuple[AgentEvent, ...]) -> list[dict[str, Any]]:
    turns: OrderedDict[str, dict[str, Any]] = OrderedDict()
    item_maps: dict[str, OrderedDict[str, dict[str, Any]]] = {}
    usage_by_turn: dict[str, dict[str, int]] = {}

    for event in events:
        if not event.turn_id:
            continue
        turn = turns.get(event.turn_id)
        if turn is None:
            turn = {
                "id": event.turn_id,
                "threadId": event.session_id,
                "status": "running",
                "startedAt": None,
                "completedAt": None,
                "items": [],
                "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
            }
            turns[event.turn_id] = turn
            item_maps[event.turn_id] = OrderedDict()
            usage_by_turn[event.turn_id] = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}

        if event.kind is AgentEventKind.TURN_STARTED:
            turn["startedAt"] = event.created_at
            turn["status"] = "running"
            turn["source"] = str(event.data.get("source") or "user")
        terminal = _terminal_turn_status(event.kind)
        if terminal is not None:
            turn["status"] = terminal
            turn["completedAt"] = event.created_at

        if event.kind is AgentEventKind.MODEL_RESPONSE:
            usage = event.data.get("usage")
            if isinstance(usage, dict):
                aggregate = usage_by_turn[event.turn_id]
                aggregate["inputTokens"] += int(usage.get("input_tokens") or 0)
                aggregate["outputTokens"] += int(usage.get("output_tokens") or 0)
                aggregate["totalTokens"] += int(usage.get("total_tokens") or 0)

        identity = _event_item_identity(event)
        if identity is None:
            continue
        item_id, item_type = identity
        items = item_maps[event.turn_id]
        item = items.get(item_id)
        if item is None:
            item = _base_item(event, item_id=item_id, item_type=item_type, status="started")
            items[item_id] = item
        _apply_event_to_item(item, event)

        if event.kind is AgentEventKind.TOOL_APPROVAL_REQUIRED:
            call_id = str(event.data.get("call_id") or "")
            tool_item = items.get(_tool_item_id(call_id))
            if tool_item is not None:
                tool_item["status"] = "waiting_approval"
                tool_item["updatedAt"] = event.created_at
        elif event.kind in {AgentEventKind.TOOL_APPROVED, AgentEventKind.TOOL_DENIED}:
            call_id = str(event.data.get("call_id") or "")
            approval_id = _approval_item_id(call_id)
            approval = items.get(approval_id)
            if approval is not None:
                approval["status"] = (
                    "approved" if event.kind is AgentEventKind.TOOL_APPROVED else "denied"
                )
                approval["updatedAt"] = event.created_at

    for turn_id, turn in turns.items():
        turn["items"] = list(item_maps[turn_id].values())
        turn["usage"] = usage_by_turn[turn_id]
        if turn["startedAt"] is None and turn["items"]:
            turn["startedAt"] = turn["items"][0]["createdAt"]
        if turn_id == session.current_turn_id and turn["completedAt"] is None:
            turn["status"] = session.status.value
    return list(turns.values())


class LoomAppServerService:
    """Protocol-facing adapter over the existing Loom AgentRuntime.

    The service owns no model/tool execution loop. It launches the real runtime
    asynchronously, serializes durable thread state, and translates observable
    AgentEvents into stable client notifications.
    """

    def __init__(
        self,
        *,
        runtime: Any,
        store: Any,
        model: str,
        default_workspace: str | Path,
        default_permission_mode: PermissionMode | str = PermissionMode.APPROVAL,
    ) -> None:
        self.runtime = runtime
        self.store = store
        self.model = str(model or "").strip()
        self.default_workspace = Path(default_workspace).expanduser().resolve()
        self.default_permission_mode = PermissionMode(default_permission_mode)
        self._guard = threading.RLock()
        self._active_sessions: set[str] = set()
        self._task_errors: dict[str, str] = {}
        self._notification_listeners: list[NotificationListener] = []
        self.runtime.subscribe(self._on_runtime_event)

    def subscribe_notifications(self, listener: NotificationListener) -> None:
        if not callable(listener):
            raise TypeError("app-server notification listener must be callable")
        with self._guard:
            self._notification_listeners.append(listener)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        json.dumps(params, ensure_ascii=False)
        with self._guard:
            listeners = tuple(self._notification_listeners)
        for listener in listeners:
            try:
                listener(method, copy.deepcopy(params))
            except Exception:
                continue

    def _is_active(self, session_id: str) -> bool:
        with self._guard:
            return session_id in self._active_sessions

    def _load(self, session_id: str):
        session = self.runtime.get_session(str(session_id or "").strip())
        if session.status is AgentStatus.RUNNING and not self._is_active(session.session_id):
            self.runtime.recover_interrupted(session.session_id)
            session = self.runtime.get_session(session.session_id)
        return session

    def _list_session_objects(self) -> list[Any]:
        sessions: list[Any] = []
        if self.store.root.is_dir():
            for directory in self.store.root.iterdir():
                if not (directory / "session.json").is_file():
                    continue
                try:
                    sessions.append(self.store.load(directory.name))
                except Exception:
                    continue
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        return sessions

    def runtime_status(self) -> dict[str, Any]:
        with self._guard:
            active = sorted(self._active_sessions)
            task_errors = dict(self._task_errors)
        return {
            "protocol": {"name": PROTOCOL_NAME, "version": PROTOCOL_VERSION},
            "model": self.model,
            "defaultWorkspace": str(self.default_workspace),
            "defaultPermissionMode": self.default_permission_mode.value,
            "permissionModes": [mode.value for mode in PermissionMode],
            "activeThreadIds": active,
            "taskErrors": task_errors,
        }

    def thread_start(self, params: dict[str, Any]) -> dict[str, Any]:
        root = Path(params.get("workspace") or self.default_workspace).expanduser().resolve()
        if not root.exists():
            raise ValueError(f"Workspace does not exist: {root}")
        if not root.is_dir():
            raise ValueError(f"Workspace is not a directory: {root}")
        mode = PermissionMode(
            str(params.get("permissionMode") or self.default_permission_mode.value)
        )
        session = self.runtime.create_session(
            AGENT_FAST_ROLE.role_id,
            workspace_dir=root,
            permission_mode=mode,
        )
        return {"thread": _thread_record(session, active=False)}

    def thread_resume(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        session = self._load(session_id)
        record = _thread_record(session, active=self._is_active(session_id))
        self._notify("thread/started", {"thread": record, "resumed": True})
        return self.thread_read({"threadId": session_id})

    def thread_list(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = int(params.get("limit", 100))
        if not 1 <= limit <= 200:
            raise ValueError("thread/list limit must be within 1..200")
        sessions = self._list_session_objects()[:limit]
        return {
            "threads": [
                _thread_record(session, active=self._is_active(session.session_id))
                for session in sessions
            ],
            "nextCursor": None,
        }

    def thread_read(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        session = self._load(session_id)
        events = self.store.events(session_id)
        return {
            "thread": _thread_record(session, active=self._is_active(session_id)),
            "turns": _turn_records(session, events),
            "messages": [_message_record(message) for message in session.messages],
            "pendingApproval": _approval_record(session.pending_approval),
            "events": [_event_record(event) for event in events],
            "finalText": session.final_text,
            "error": session.error,
        }

    def thread_fork(self, params: dict[str, Any]) -> dict[str, Any]:
        source_id = self._required_text(params, "threadId")
        source = self._load(source_id)
        if source.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
            raise RuntimeError("cannot fork a thread while its current turn is active")
        if params.get("lastTurnId") not in {None, "", source.current_turn_id}:
            raise ValueError(
                "partial historical fork boundaries are reserved for Phase 2.5; v1 forks the latest durable boundary"
            )
        fork = self.runtime.create_session(
            source.profile_id,
            system_prompt=source.system_prompt,
            workspace_dir=source.workspace_dir,
            permission_mode=source.permission_mode,
        )
        fork.messages = copy.deepcopy(source.messages)
        fork.forked_from_id = source.session_id
        fork.status = AgentStatus.IDLE
        fork.current_turn_id = ""
        fork.pending_tool_calls.clear()
        fork.pending_step_id = ""
        fork.pending_approval = None
        fork.model_steps = 0
        fork.tool_calls = 0
        fork.final_text = ""
        fork.error = ""
        self.store.save(fork)
        record = _thread_record(fork, active=False)
        return {"thread": record, "forkedFromId": source.session_id}

    def turn_start(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        text = self._required_text(params, "input")
        session = self._load(session_id)
        if session.status is AgentStatus.WAITING_APPROVAL:
            raise RuntimeError("resolve the pending approval before starting another turn")
        if self._is_active(session_id):
            raise RuntimeError("thread already has an active turn")
        turn_id = str(uuid.uuid4())
        self._launch(
            session_id,
            lambda: self.runtime.start_turn(session_id, text, turn_id=turn_id),
        )
        return {
            "turn": {
                "id": turn_id,
                "threadId": session_id,
                "status": "starting",
                "startedAt": None,
                "completedAt": None,
                "items": [],
                "usage": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
            }
        }

    def turn_interrupt(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        session = self._load(session_id)
        requested_turn_id = str(params.get("turnId") or "").strip()
        if requested_turn_id and session.current_turn_id != requested_turn_id:
            raise ValueError("turnId does not match the thread's current turn")
        if session.status not in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}:
            return {
                "requested": False,
                "threadId": session_id,
                "turnId": session.current_turn_id or None,
                "status": session.status.value,
            }
        result = self.runtime.cancel(session_id)
        return {
            "requested": True,
            "threadId": session_id,
            "turnId": session.current_turn_id or None,
            "status": result.status.value,
        }

    def approval_respond(self, params: dict[str, Any]) -> dict[str, Any]:
        session_id = self._required_text(params, "threadId")
        call_id = self._required_text(params, "callId")
        approved = params.get("approved")
        if not isinstance(approved, bool):
            raise ValueError("approval/respond approved must be a boolean")
        session = self._load(session_id)
        pending = session.pending_approval
        if pending is None:
            raise RuntimeError("thread has no pending approval")
        if pending.call_id != call_id:
            raise ValueError("callId does not match the pending approval")
        self._launch(
            session_id,
            lambda: self.runtime.resume_approval(
                session_id,
                call_id,
                approved=approved,
            ),
        )
        return {"accepted": True, "threadId": session_id, "callId": call_id}

    def _launch(self, session_id: str, operation: Callable[[], Any]) -> None:
        with self._guard:
            if session_id in self._active_sessions:
                raise RuntimeError("thread already has an active app-server operation")
            self._active_sessions.add(session_id)
            self._task_errors.pop(session_id, None)

        def runner() -> None:
            try:
                operation()
            except Exception as exc:
                with self._guard:
                    self._task_errors[session_id] = f"{type(exc).__name__}: {exc}"
            finally:
                with self._guard:
                    self._active_sessions.discard(session_id)

        threading.Thread(
            target=runner,
            name=f"loom-app-{session_id[:8]}-{uuid.uuid4().hex[:6]}",
            daemon=True,
        ).start()

    @staticmethod
    def _required_text(params: dict[str, Any], key: str) -> str:
        value = str(params.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} must not be empty")
        return value

    def _on_runtime_event(self, event: AgentEvent) -> None:
        kind = event.kind
        data = event.data

        if kind is AgentEventKind.SESSION_CREATED:
            try:
                session = self.store.load(event.session_id)
            except Exception:
                return
            self._notify(
                "thread/started",
                {"thread": _thread_record(session, active=self._is_active(event.session_id))},
            )
            return

        if kind is AgentEventKind.TURN_STARTED:
            self._notify(
                "turn/started",
                {
                    "threadId": event.session_id,
                    "turn": {
                        "id": event.turn_id,
                        "threadId": event.session_id,
                        "status": "running",
                        "startedAt": event.created_at,
                        "source": str(data.get("source") or "user"),
                    },
                },
            )
            return

        if kind is AgentEventKind.USER_MESSAGE:
            item = _base_item(
                event,
                item_id=_user_item_id(event.event_id),
                item_type="user_message",
                status="started",
            )
            self._notify("item/started", {"item": copy.deepcopy(item)})
            _apply_event_to_item(item, event)
            self._notify("item/completed", {"item": item})
            return

        if kind is AgentEventKind.MODEL_RESPONSE and str(data.get("text") or ""):
            item = _base_item(
                event,
                item_id=_assistant_item_id(event.event_id),
                item_type="assistant_message",
                status="started",
            )
            self._notify("item/started", {"item": copy.deepcopy(item)})
            text = str(data.get("text") or "")
            # Phase 2.1 preserves the streaming protocol shape while the existing
            # provider adapter is still non-streaming. Phase 2.2 will emit true
            # provider deltas instead of this one full-text chunk.
            self._notify(
                "item/delta",
                {
                    "threadId": event.session_id,
                    "turnId": event.turn_id,
                    "itemId": item["id"],
                    "delta": {"text": text},
                },
            )
            _apply_event_to_item(item, event)
            self._notify("item/completed", {"item": item})
            return

        if kind is AgentEventKind.TOOL_REQUESTED:
            call_id = str(data.get("call_id") or "")
            if not call_id:
                return
            item = _base_item(
                event,
                item_id=_tool_item_id(call_id),
                item_type="tool_call",
                status="started",
            )
            _apply_event_to_item(item, event)
            self._notify("item/started", {"item": item})
            return

        if kind is AgentEventKind.TOOL_STARTED:
            call_id = str(data.get("call_id") or "")
            if call_id:
                self._notify(
                    "item/delta",
                    {
                        "threadId": event.session_id,
                        "turnId": event.turn_id,
                        "itemId": _tool_item_id(call_id),
                        "delta": {"status": "running"},
                    },
                )
            return

        if kind is AgentEventKind.TOOL_APPROVAL_REQUIRED:
            call_id = str(data.get("call_id") or "")
            item = _base_item(
                event,
                item_id=_approval_item_id(call_id),
                item_type="approval",
                status="waiting",
            )
            _apply_event_to_item(item, event)
            self._notify("item/started", {"item": copy.deepcopy(item)})
            self._notify(
                "approval/requested",
                {
                    "threadId": event.session_id,
                    "turnId": event.turn_id,
                    "approval": {
                        "itemId": item["id"],
                        "callId": call_id,
                        "toolName": str(data.get("tool") or ""),
                        "arguments": copy.deepcopy(data.get("arguments") or {}),
                        "effect": str(data.get("effect") or ""),
                        "reason": str(data.get("reason") or ""),
                    },
                },
            )
            self._notify(
                "item/delta",
                {
                    "threadId": event.session_id,
                    "turnId": event.turn_id,
                    "itemId": _tool_item_id(call_id),
                    "delta": {"status": "waiting_approval"},
                },
            )
            return

        if kind is AgentEventKind.TOOL_APPROVED:
            call_id = str(data.get("call_id") or "")
            if call_id:
                item = _base_item(
                    event,
                    item_id=_approval_item_id(call_id),
                    item_type="approval",
                    status="approved",
                )
                item["callId"] = call_id
                self._notify("item/completed", {"item": item})
            return

        if kind is AgentEventKind.TOOL_DENIED and str(data.get("source") or "") == "user":
            call_id = str(data.get("call_id") or "")
            if call_id:
                item = _base_item(
                    event,
                    item_id=_approval_item_id(call_id),
                    item_type="approval",
                    status="denied",
                )
                item["callId"] = call_id
                item["source"] = "user"
                self._notify("item/completed", {"item": item})
            # Continue below as the tool itself is also terminally denied.

        if kind in {
            AgentEventKind.TOOL_COMPLETED,
            AgentEventKind.TOOL_FAILED,
            AgentEventKind.TOOL_DENIED,
        }:
            call_id = str(data.get("call_id") or "")
            if not call_id:
                return
            item = _base_item(
                event,
                item_id=_tool_item_id(call_id),
                item_type="tool_call",
                status="completed",
            )
            item["callId"] = call_id
            item["toolName"] = str(data.get("tool") or "")
            _apply_event_to_item(item, event)
            self._notify("item/completed", {"item": item})
            return

        if kind is AgentEventKind.PROCESS_STARTED:
            process_id = str(data.get("process_id") or "")
            if not process_id:
                return
            item = _base_item(
                event,
                item_id=_process_item_id(process_id),
                item_type="process",
                status="running",
            )
            _apply_event_to_item(item, event)
            self._notify("item/started", {"item": item})
            return

        if kind is AgentEventKind.PROCESS_OUTPUT:
            process_id = str(data.get("process_id") or "")
            if process_id:
                self._notify(
                    "item/delta",
                    {
                        "threadId": event.session_id,
                        "turnId": event.turn_id,
                        "itemId": _process_item_id(process_id),
                        "delta": {
                            "stdout": str(data.get("stdout") or ""),
                            "stderr": str(data.get("stderr") or ""),
                        },
                    },
                )
            return

        if kind is AgentEventKind.PROCESS_EXITED:
            process_id = str(data.get("process_id") or "")
            if not process_id:
                return
            item = _base_item(
                event,
                item_id=_process_item_id(process_id),
                item_type="process",
                status="completed",
            )
            _apply_event_to_item(item, event)
            self._notify("item/completed", {"item": item})
            return

        if kind is AgentEventKind.TURN_DIFF_UPDATED:
            item = _base_item(
                event,
                item_id=_diff_item_id(event.event_id),
                item_type="file_edit",
                status="completed",
            )
            _apply_event_to_item(item, event)
            self._notify("item/completed", {"item": item})
            return

        terminal = _terminal_turn_status(kind)
        if terminal is not None:
            if kind is not AgentEventKind.TURN_COMPLETED:
                item = _base_item(
                    event,
                    item_id=_error_item_id(event.event_id),
                    item_type="error",
                    status="completed",
                )
                _apply_event_to_item(item, event)
                self._notify("item/completed", {"item": item})
            try:
                session = self.store.load(event.session_id)
                usage = _usage_record(session.usage)
                final_text = session.final_text
                error = session.error
            except Exception:
                usage = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
                final_text = str(data.get("text") or "")
                error = str(data.get("error") or data.get("reason") or "")
            self._notify(
                "turn/completed",
                {
                    "threadId": event.session_id,
                    "turn": {
                        "id": event.turn_id,
                        "threadId": event.session_id,
                        "status": terminal,
                        "completedAt": event.created_at,
                        "finalText": final_text,
                        "error": error,
                        "usage": usage,
                    },
                },
            )


class LoomRpcController:
    """Versioned JSON-RPC request dispatcher for one app-server client."""

    def __init__(self, service: LoomAppServerService) -> None:
        self.service = service
        self.initialized = False
        self.client_info: dict[str, Any] = {}

    def handle(self, payload: Any) -> dict[str, Any] | None:
        request_id: Any = None
        try:
            if not isinstance(payload, dict):
                raise JsonRpcError(-32600, "Invalid Request")
            request_id = payload.get("id")
            if payload.get("jsonrpc") != "2.0":
                raise JsonRpcError(-32600, "jsonrpc must be '2.0'")
            method = str(payload.get("method") or "").strip()
            if not method:
                raise JsonRpcError(-32600, "method must not be empty")
            params = payload.get("params") or {}
            if not isinstance(params, dict):
                raise JsonRpcError(-32602, "params must be an object")

            is_notification = "id" not in payload
            if method == "initialize":
                if is_notification:
                    raise JsonRpcError(-32600, "initialize must be a request")
                result = self._initialize(params)
            else:
                if not self.initialized:
                    raise JsonRpcError(-32002, "Not initialized")
                if method == "initialized":
                    return None
                result = self._dispatch(method, params)

            if is_notification:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except JsonRpcError as exc:
            if isinstance(payload, dict) and "id" not in payload:
                return None
            return {"jsonrpc": "2.0", "id": request_id, "error": exc.to_dict()}
        except (ValueError, TypeError) as exc:
            if isinstance(payload, dict) and "id" not in payload:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32602, "message": str(exc)},
            }
        except (FileNotFoundError, KeyError) as exc:
            if isinstance(payload, dict) and "id" not in payload:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32004, "message": str(exc)},
            }
        except RuntimeError as exc:
            if isinstance(payload, dict) and "id" not in payload:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32009, "message": str(exc)},
            }
        except Exception:
            if isinstance(payload, dict) and "id" not in payload:
                return None
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": "Internal server error"},
            }

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.initialized:
            raise JsonRpcError(-32003, "Already initialized")
        version = params.get("protocolVersion")
        if version != PROTOCOL_VERSION:
            raise JsonRpcError(
                -32010,
                "Unsupported protocol version",
                {"supported": [PROTOCOL_VERSION], "requested": version},
            )
        client_info = params.get("clientInfo") or {}
        if not isinstance(client_info, dict):
            raise JsonRpcError(-32602, "clientInfo must be an object")
        self.client_info = copy.deepcopy(client_info)
        self.initialized = True
        status = self.service.runtime_status()
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "serverInfo": {"name": PROTOCOL_NAME, "version": "0.1.0"},
            "capabilities": {
                "threads": {
                    "start": True,
                    "resume": True,
                    "list": True,
                    "read": True,
                    "fork": True,
                },
                "turns": {"start": True, "interrupt": True},
                "approvals": True,
                "notifications": [
                    "thread/started",
                    "turn/started",
                    "item/started",
                    "item/delta",
                    "item/completed",
                    "approval/requested",
                    "turn/completed",
                ],
                "providerStreaming": False,
            },
            "runtime": status,
        }

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        handlers: dict[str, Callable[[dict[str, Any]], Any]] = {
            "runtime/status": lambda value: self.service.runtime_status(),
            "thread/start": self.service.thread_start,
            "thread/resume": self.service.thread_resume,
            "thread/list": self.service.thread_list,
            "thread/read": self.service.thread_read,
            "thread/fork": self.service.thread_fork,
            "turn/start": self.service.turn_start,
            "turn/interrupt": self.service.turn_interrupt,
            "approval/respond": self.service.approval_respond,
        }
        handler = handlers.get(method)
        if handler is None:
            raise JsonRpcError(-32601, f"Method not found: {method}")
        return handler(params)


class JsonRpcStdioServer:
    """Bounded JSONL transport for the local app-server control plane."""

    _STOP = object()

    def __init__(
        self,
        service: LoomAppServerService,
        *,
        ingress_limit: int = _DEFAULT_INGRESS_LIMIT,
        outbound_limit: int = _DEFAULT_OUTBOUND_LIMIT,
    ) -> None:
        self.service = service
        self.controller = LoomRpcController(service)
        self.ingress_limit = max(1, int(ingress_limit))
        self.outbound_limit = max(4, int(outbound_limit))
        self._ingress: queue.Queue[Any] = queue.Queue(maxsize=self.ingress_limit)
        self._outbound: queue.Queue[Any] = queue.Queue(maxsize=self.outbound_limit)
        self._write_lock = threading.Lock()
        self._writer: TextIO | None = None
        self._dropped_notifications = 0
        self.service.subscribe_notifications(self._on_notification)

    @property
    def dropped_notifications(self) -> int:
        return self._dropped_notifications

    def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        if not self.controller.initialized:
            return
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            self._outbound.put_nowait(payload)
        except queue.Full:
            # Notifications are reconstructible from durable thread/read state.
            # Responses are never silently dropped; see _send_response.
            self._dropped_notifications += 1

    def _send_response(self, payload: dict[str, Any]) -> None:
        try:
            self._outbound.put(payload, timeout=0.5)
        except queue.Full:
            self._write_direct(payload)

    def _write_direct(self, payload: dict[str, Any]) -> None:
        writer = self._writer
        if writer is None:
            return
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            writer.write(encoded + "\n")
            writer.flush()

    def _writer_loop(self) -> None:
        while True:
            payload = self._outbound.get()
            if payload is self._STOP:
                return
            self._write_direct(payload)

    def _worker_loop(self) -> None:
        while True:
            raw = self._ingress.get()
            if raw is self._STOP:
                return
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                self._send_response(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {exc.msg}"},
                    }
                )
                continue
            response = self.controller.handle(payload)
            if response is not None:
                self._send_response(response)

    @staticmethod
    def _request_id_from_raw(raw: str) -> Any:
        try:
            payload = json.loads(raw)
        except Exception:
            return None
        if isinstance(payload, dict):
            return payload.get("id")
        return None

    def serve(self, reader: TextIO | None = None, writer: TextIO | None = None) -> int:
        source = reader or sys.stdin
        self._writer = writer or sys.stdout
        worker = threading.Thread(target=self._worker_loop, name="loom-app-rpc", daemon=True)
        output = threading.Thread(target=self._writer_loop, name="loom-app-writer", daemon=True)
        worker.start()
        output.start()
        try:
            for line in source:
                raw = line.rstrip("\r\n")
                if not raw.strip():
                    continue
                if len(raw.encode("utf-8")) > _MAX_MESSAGE_BYTES:
                    self._send_response(
                        {
                            "jsonrpc": "2.0",
                            "id": self._request_id_from_raw(raw),
                            "error": {
                                "code": -32600,
                                "message": "Request exceeds the 1 MB app-server message limit",
                            },
                        }
                    )
                    continue
                try:
                    self._ingress.put_nowait(raw)
                except queue.Full:
                    self._send_response(
                        {
                            "jsonrpc": "2.0",
                            "id": self._request_id_from_raw(raw),
                            "error": {
                                "code": -32001,
                                "message": "Server overloaded; retry later.",
                            },
                        }
                    )
        finally:
            self._ingress.put(self._STOP)
            worker.join(timeout=5)
            self._outbound.put(self._STOP)
            output.join(timeout=5)
        return 0


def serve_stdio(
    *,
    runtime: Any,
    store: Any,
    model: str,
    default_workspace: str | Path,
    default_permission_mode: PermissionMode | str,
    reader: TextIO | None = None,
    writer: TextIO | None = None,
) -> int:
    service = LoomAppServerService(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=default_workspace,
        default_permission_mode=default_permission_mode,
    )
    server = JsonRpcStdioServer(service)
    try:
        return server.serve(reader=reader, writer=writer)
    finally:
        close = getattr(runtime, "close", None)
        if callable(close):
            close()


__all__ = [
    "JsonRpcError",
    "JsonRpcStdioServer",
    "LoomAppServerService",
    "LoomRpcController",
    "PROTOCOL_NAME",
    "PROTOCOL_VERSION",
    "serve_stdio",
]
