from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from app.ai import AIMessage, ChatRequest, MessageRole, ToolChoice
from app.agent_runtime import AgentEvent, AgentEventKind, AgentStatus, PermissionMode

from .app_server import JsonRpcError, _message_text, _thread_record
from .app_server_streaming import (
    StreamingJsonRpcStdioServer,
    StreamingLoomAppServerService,
    StreamingLoomRpcController,
)


_THREAD_META_FILENAME = "thread-library.json"
_MAX_TITLE_CHARS = 120
_MAX_QUERY_CHARS = 160
_AUTO_TITLE_VERSION = 1
_AUTO_TITLE_MAX_ATTEMPTS = 2
_AUTO_TITLE_CONTEXT_CHARS = 1800
_AUTO_TITLE_OUTPUT_CHARS = 56
_AUTO_TITLE_CONTROL_RE = re.compile(
    r"\[\[AI_LEDGER_[A-Z0-9_]+:[^\]\r\n]*(?:\]\])?",
    re.IGNORECASE,
)
_AUTO_TITLE_PREFIX_RE = re.compile(
    r"^(?:title|conversation\s+title|thread\s+title|标题|对话标题|会话标题)\s*[:：\-–—]\s*",
    re.IGNORECASE,
)
_AUTO_TITLE_LIST_RE = re.compile(r"^(?:[-*•]+|\d+[.)、])\s*")
_GENERIC_AUTO_TITLES = {
    "chat",
    "conversation",
    "new conversation",
    "new thread",
    "untitled",
    "help",
    "聊天",
    "对话",
    "新对话",
    "新会话",
    "问题",
    "帮助",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _normalized_title(value: Any) -> str:
    title = " ".join(str(value or "").split())
    if not title:
        raise JsonRpcError(-32602, "thread title must not be empty")
    if len(title) > _MAX_TITLE_CHARS:
        raise JsonRpcError(
            -32602,
            f"thread title must be at most {_MAX_TITLE_CHARS} characters",
        )
    return title


def _clean_title_context(value: Any) -> str:
    """Keep only visible conversation text for the detached title request."""

    text = _AUTO_TITLE_CONTROL_RE.sub("", str(value or ""))
    text = " ".join(text.replace("\x00", " ").split())
    if len(text) > _AUTO_TITLE_CONTEXT_CHARS:
        text = text[:_AUTO_TITLE_CONTEXT_CHARS].rstrip() + "…"
    return text


def _auto_title_context(session: Any) -> tuple[str, str]:
    """Use the first user/assistant exchange, not tool traces or private reasoning."""

    user_text = ""
    assistant_text = ""
    for message in session.messages:
        if message.role not in {MessageRole.USER, MessageRole.ASSISTANT}:
            continue
        text = _clean_title_context(_message_text(message))
        if not text:
            continue
        if message.role is MessageRole.USER and not user_text:
            user_text = text
            continue
        if message.role is MessageRole.ASSISTANT and user_text and not assistant_text:
            assistant_text = text
            break
    return user_text, assistant_text


def _sanitize_generated_title(value: Any) -> str:
    """Turn model output into one compact sidebar-safe title."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    raw = raw.replace("```", "").strip()
    line = next((item.strip() for item in raw.splitlines() if item.strip()), "")
    line = _AUTO_TITLE_LIST_RE.sub("", line)
    line = _AUTO_TITLE_PREFIX_RE.sub("", line)
    line = line.strip(" \t`'\"“”‘’[]【】<>《》")
    line = " ".join(line.split())
    line = re.sub(r"[。.!！?？;；,:：]+$", "", line).strip()
    if len(line) > _AUTO_TITLE_OUTPUT_CHARS:
        line = line[:_AUTO_TITLE_OUTPUT_CHARS].rstrip(" -–—:：,，。.!！?？")
    if line.casefold() in _GENERIC_AUTO_TITLES:
        return ""
    return line


def _build_auto_title_request(session: Any) -> ChatRequest | None:
    user_text, assistant_text = _auto_title_context(session)
    if not user_text:
        return None

    system = (
        "Create a concise title for this desktop-agent conversation. Return only the title, with no "
        "quotes, markdown, prefix, explanation, or ending punctuation. Match the user's main language. "
        "Capture the concrete subject and task rather than copying a greeting. For Chinese, prefer 6-16 "
        "Chinese characters; otherwise prefer 3-8 words. Avoid generic titles such as Chat, Conversation, "
        "Help, 对话, 聊天, 问题, or 帮助."
    )
    user = f"User request:\n{user_text}"
    if assistant_text:
        user += f"\n\nAssistant outcome:\n{assistant_text}"

    return ChatRequest(
        messages=(
            AIMessage(role=MessageRole.SYSTEM, content=system),
            AIMessage(role=MessageRole.USER, content=user),
        ),
        tools=(),
        tool_choice=ToolChoice.NONE,
        temperature=0.2,
        max_output_tokens=48,
    )


class ThreadLibraryStore:
    """Small durable metadata layer beside Runtime-owned session snapshots.

    Rename/archive/title state belongs to the client-facing conversation library,
    not the Agent execution contract. Keeping it in a sidecar file means Runtime
    can continue to own ``session.json`` without a UI action racing or rewriting
    its canonical turn state.
    """

    def __init__(self, session_store: Any) -> None:
        self.session_store = session_store
        self._guard = threading.RLock()

    def _path(self, session_id: str) -> Path:
        return self.session_store.session_dir(session_id) / _THREAD_META_FILENAME

    def _read_unlocked(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def read(self, session_id: str) -> dict[str, Any]:
        with self._guard:
            return self._read_unlocked(session_id)

    def _write_unlocked(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        directory = self.session_store.session_dir(session_id)
        if not (directory / "session.json").is_file():
            raise FileNotFoundError(session_id)
        payload = dict(payload)
        payload["updatedAt"] = _utc_now()

        target = self._path(session_id)
        temp = directory / f".{_THREAD_META_FILENAME}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        return payload

    def write(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._guard:
            payload = self._read_unlocked(session_id)
            payload.update(updates)
            return self._write_unlocked(session_id, payload)

    def claim_auto_title_attempt(self, session_id: str) -> bool:
        """Atomically reserve one model call unless a real title already exists."""

        with self._guard:
            payload = self._read_unlocked(session_id)
            if str(payload.get("title") or "").strip() or bool(payload.get("autoTitleDisabled")):
                return False
            attempts = max(0, int(payload.get("autoTitleAttempts") or 0))
            if attempts >= _AUTO_TITLE_MAX_ATTEMPTS:
                return False
            payload.update(
                {
                    "autoTitleAttempts": attempts + 1,
                    "autoTitleAttemptedAt": _utc_now(),
                    "autoTitleVersion": _AUTO_TITLE_VERSION,
                }
            )
            self._write_unlocked(session_id, payload)
            return True

    def write_auto_title_if_untitled(self, session_id: str, title: str) -> bool:
        """Commit an AI title only if a manual rename did not win the race."""

        with self._guard:
            payload = self._read_unlocked(session_id)
            if str(payload.get("title") or "").strip() or bool(payload.get("autoTitleDisabled")):
                return False
            payload.update(
                {
                    "title": title,
                    "titleSource": "auto",
                    "autoTitleGeneratedAt": _utc_now(),
                    "autoTitleVersion": _AUTO_TITLE_VERSION,
                    "autoTitleLastError": "",
                }
            )
            self._write_unlocked(session_id, payload)
            return True

    def delete_session(self, session_id: str) -> None:
        with self._guard:
            directory = self.session_store.session_dir(session_id)
            if not (directory / "session.json").is_file():
                raise FileNotFoundError(session_id)
            shutil.rmtree(directory)


class ManagedStreamingLoomAppServerService(StreamingLoomAppServerService):
    """Streaming App Server plus durable conversation-library operations."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.thread_library = ThreadLibraryStore(self.store)
        self._auto_title_guard = threading.RLock()
        self._auto_title_inflight: set[str] = set()

    def _session_or_rpc_error(self, thread_id: str) -> Any:
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            raise JsonRpcError(-32602, "threadId is required")
        try:
            return self.store.load(thread_id)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise JsonRpcError(-32004, "thread not found", {"threadId": thread_id}) from exc

    def _managed_record(self, session: Any) -> dict[str, Any]:
        record = _thread_record(session, active=self._is_active(session.session_id))
        metadata = self.thread_library.read(session.session_id)
        custom_title = str(metadata.get("title") or "").strip()
        archived_at = str(metadata.get("archivedAt") or "").strip()
        title_source = str(metadata.get("titleSource") or "").strip().casefold()
        if custom_title:
            record["title"] = custom_title
            if title_source not in {"auto", "manual"}:
                # Titles written before titleSource existed were user renames.
                title_source = "manual"
        else:
            title_source = "fallback"
        record["customTitle"] = bool(custom_title)
        record["titleSource"] = title_source
        record["archived"] = bool(archived_at)
        record["archivedAt"] = archived_at or None
        return record

    def thread_list(self, params: dict[str, Any]) -> dict[str, Any]:
        limit = int(params.get("limit", 100))
        if not 1 <= limit <= 200:
            raise JsonRpcError(-32602, "thread/list limit must be within 1..200")

        view = str(params.get("view") or "active").strip().casefold()
        if view not in {"active", "archived", "all"}:
            raise JsonRpcError(-32602, "thread/list view must be active, archived, or all")

        query = " ".join(str(params.get("query") or "").split())
        if len(query) > _MAX_QUERY_CHARS:
            raise JsonRpcError(
                -32602,
                f"thread/list query must be at most {_MAX_QUERY_CHARS} characters",
            )
        query_folded = query.casefold()

        all_records = [self._managed_record(session) for session in self._list_session_objects()]
        active_count = sum(not bool(record.get("archived")) for record in all_records)
        archived_count = len(all_records) - active_count

        if view == "active":
            records = [record for record in all_records if not record.get("archived")]
        elif view == "archived":
            records = [record for record in all_records if record.get("archived")]
            records.sort(
                key=lambda record: str(record.get("archivedAt") or record.get("updatedAt") or ""),
                reverse=True,
            )
        else:
            records = all_records

        if query_folded:
            records = [
                record
                for record in records
                if query_folded in str(record.get("title") or "").casefold()
                or query_folded in str(record.get("workspace") or "").casefold()
            ]

        return {
            "threads": records[:limit],
            "view": view,
            "query": query,
            "counts": {
                "active": active_count,
                "archived": archived_count,
                "all": len(all_records),
            },
        }

    def thread_read(self, params: dict[str, Any]) -> dict[str, Any]:
        payload = super().thread_read(params)
        thread = payload.get("thread")
        if isinstance(thread, dict):
            thread_id = str(thread.get("id") or "")
            metadata = self.thread_library.read(thread_id)
            custom_title = str(metadata.get("title") or "").strip()
            archived_at = str(metadata.get("archivedAt") or "").strip()
            title_source = str(metadata.get("titleSource") or "").strip().casefold()
            if custom_title:
                thread["title"] = custom_title
                if title_source not in {"auto", "manual"}:
                    title_source = "manual"
            else:
                title_source = "fallback"
            thread["customTitle"] = bool(custom_title)
            thread["titleSource"] = title_source
            thread["archived"] = bool(archived_at)
            thread["archivedAt"] = archived_at or None
        return payload

    def thread_rename(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session_or_rpc_error(params.get("threadId"))
        title = _normalized_title(params.get("title"))
        try:
            self.thread_library.write(
                session.session_id,
                {
                    "title": title,
                    "titleSource": "manual",
                    "autoTitleDisabled": True,
                },
            )
        except FileNotFoundError as exc:
            raise JsonRpcError(-32004, "thread not found") from exc
        record = self._managed_record(session)
        self._notify("thread/updated", {"thread": record, "reason": "renamed"})
        return {"thread": record}

    def thread_archive(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session_or_rpc_error(params.get("threadId"))
        archived = bool(params.get("archived", True))
        if archived and (
            self._is_active(session.session_id)
            or session.status in {AgentStatus.RUNNING, AgentStatus.WAITING_APPROVAL}
        ):
            raise JsonRpcError(
                -32021,
                "active thread cannot be archived",
                {"threadId": session.session_id, "status": session.status.value},
            )
        try:
            self.thread_library.write(
                session.session_id,
                {"archivedAt": _utc_now() if archived else ""},
            )
        except FileNotFoundError as exc:
            raise JsonRpcError(-32004, "thread not found") from exc
        record = self._managed_record(session)
        self._notify(
            "thread/updated",
            {"thread": record, "reason": "archived" if archived else "restored"},
        )
        return {"thread": record}

    def thread_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session_or_rpc_error(params.get("threadId"))
        if self._is_active(session.session_id) or session.status in {
            AgentStatus.RUNNING,
            AgentStatus.WAITING_APPROVAL,
        }:
            raise JsonRpcError(
                -32022,
                "active thread cannot be deleted",
                {"threadId": session.session_id, "status": session.status.value},
            )

        directory = self.store.session_dir(session.session_id)
        internal_workspace = (directory / "workspace").resolve()
        try:
            session_workspace = Path(session.workspace_dir).expanduser().resolve()
        except OSError:
            session_workspace = Path(session.workspace_dir).expanduser().absolute()
        if session_workspace == internal_workspace and internal_workspace.is_dir():
            try:
                has_workspace_files = any(internal_workspace.iterdir())
            except OSError:
                has_workspace_files = True
            if has_workspace_files:
                raise JsonRpcError(
                    -32024,
                    "thread owns an internal workspace; archive it instead of deleting it",
                    {"threadId": session.session_id},
                )

        try:
            self.thread_library.delete_session(session.session_id)
        except FileNotFoundError as exc:
            raise JsonRpcError(-32004, "thread not found") from exc
        self._notify("thread/deleted", {"threadId": session.session_id})
        return {"deleted": True, "threadId": session.session_id}

    def thread_set_permission_mode(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session_or_rpc_error(params.get("threadId"))
        if self._is_active(session.session_id) or session.status in {
            AgentStatus.RUNNING,
            AgentStatus.WAITING_APPROVAL,
        }:
            raise JsonRpcError(
                -32025,
                "permission profile cannot change while the thread is active",
                {"threadId": session.session_id, "status": session.status.value},
            )

        raw_mode = str(params.get("permissionMode") or "").strip()
        if not raw_mode:
            raise JsonRpcError(-32602, "permissionMode is required")
        try:
            permission_mode = PermissionMode(raw_mode)
        except ValueError as exc:
            raise JsonRpcError(
                -32602,
                "unsupported permissionMode",
                {
                    "permissionMode": raw_mode,
                    "supported": [mode.value for mode in PermissionMode],
                },
            ) from exc

        if session.permission_mode is permission_mode:
            return {"thread": self._managed_record(session)}

        session.permission_mode = permission_mode
        try:
            self.store.save(session)
        except (OSError, ValueError) as exc:
            raise JsonRpcError(
                -32026,
                "failed to persist permission profile",
                {"threadId": session.session_id},
            ) from exc

        record = self._managed_record(session)
        self._notify(
            "thread/updated",
            {"thread": record, "reason": "permission_changed"},
        )
        return {"thread": record}

    def turn_start(self, params: dict[str, Any]) -> dict[str, Any]:
        thread_id = str(params.get("threadId") or "").strip()
        if thread_id and self.thread_library.read(thread_id).get("archivedAt"):
            raise JsonRpcError(
                -32023,
                "archived thread is read-only; restore it before starting a turn",
                {"threadId": thread_id},
            )
        return super().turn_start(params)

    def _schedule_auto_title(self, thread_id: str) -> None:
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return
        with self._auto_title_guard:
            if thread_id in self._auto_title_inflight:
                return
            metadata = self.thread_library.read(thread_id)
            if (
                str(metadata.get("title") or "").strip()
                or bool(metadata.get("autoTitleDisabled"))
                or int(metadata.get("autoTitleAttempts") or 0) >= _AUTO_TITLE_MAX_ATTEMPTS
            ):
                return
            self._auto_title_inflight.add(thread_id)

        threading.Thread(
            target=self._generate_auto_title,
            args=(thread_id,),
            name=f"loom-title-{thread_id[:8]}",
            daemon=True,
        ).start()

    def _generate_auto_title(self, thread_id: str) -> None:
        try:
            try:
                session = self.store.load(thread_id)
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                return
            request = _build_auto_title_request(session)
            if request is None:
                return
            try:
                claimed = self.thread_library.claim_auto_title_attempt(thread_id)
            except FileNotFoundError:
                return
            if not claimed:
                return

            platform = getattr(self.runtime, "platform", None)
            execute_chat = getattr(platform, "execute_chat", None)
            if not callable(execute_chat):
                self.thread_library.write(thread_id, {"autoTitleLastError": "platform_unavailable"})
                return

            try:
                response = execute_chat(session.profile_id, request)
                title = _sanitize_generated_title(getattr(response, "text", ""))
            except Exception as exc:
                try:
                    self.thread_library.write(
                        thread_id,
                        {"autoTitleLastError": type(exc).__name__},
                    )
                except FileNotFoundError:
                    pass
                return

            if not title:
                try:
                    self.thread_library.write(thread_id, {"autoTitleLastError": "empty_or_generic_title"})
                except FileNotFoundError:
                    pass
                return

            try:
                committed = self.thread_library.write_auto_title_if_untitled(thread_id, title)
            except FileNotFoundError:
                return
            if not committed:
                return

            try:
                latest = self.store.load(thread_id)
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                return
            self._notify(
                "thread/updated",
                {"thread": self._managed_record(latest), "reason": "auto_title"},
            )
        finally:
            with self._auto_title_guard:
                self._auto_title_inflight.discard(thread_id)

    def _on_runtime_event(self, event: AgentEvent) -> None:
        # Preserve the streaming/durable notification path, then title the
        # completed conversation in a detached daemon task. The visible answer
        # is never delayed by title generation.
        super()._on_runtime_event(event)
        if event.kind is AgentEventKind.TURN_COMPLETED:
            self._schedule_auto_title(event.session_id)


class ManagedStreamingLoomRpcController(StreamingLoomRpcController):
    service: ManagedStreamingLoomAppServerService

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        result = super()._initialize(params)
        result["capabilities"]["threadManagement"] = {
            "rename": True,
            "archive": True,
            "delete": True,
            "search": True,
            "permissionMode": True,
            "autoTitle": True,
        }
        return result

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        handlers = {
            "thread/rename": self.service.thread_rename,
            "thread/archive": self.service.thread_archive,
            "thread/delete": self.service.thread_delete,
            "thread/set_permission_mode": self.service.thread_set_permission_mode,
        }
        handler = handlers.get(method)
        if handler is not None:
            return handler(params)
        return super()._dispatch(method, params)


class ManagedStreamingJsonRpcStdioServer(StreamingJsonRpcStdioServer):
    def __init__(self, service: ManagedStreamingLoomAppServerService, **kwargs: Any) -> None:
        super().__init__(service, **kwargs)
        self.controller = ManagedStreamingLoomRpcController(service)


def serve_managed_streaming_stdio(
    *,
    runtime: Any,
    store: Any,
    model: str,
    default_workspace: str | Path,
    default_permission_mode: PermissionMode | str,
    vision: bool = False,
    reader: TextIO | None = None,
    writer: TextIO | None = None,
) -> int:
    service = ManagedStreamingLoomAppServerService(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=default_workspace,
        default_permission_mode=default_permission_mode,
        vision=vision,
    )
    server = ManagedStreamingJsonRpcStdioServer(service)
    try:
        return server.serve(reader=reader, writer=writer)
    finally:
        close = getattr(runtime, "close", None)
        if callable(close):
            close()


__all__ = [
    "ManagedStreamingJsonRpcStdioServer",
    "ManagedStreamingLoomAppServerService",
    "ManagedStreamingLoomRpcController",
    "ThreadLibraryStore",
    "serve_managed_streaming_stdio",
]
