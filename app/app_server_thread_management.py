from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from app.agent_runtime import AgentStatus, PermissionMode

from .app_server import JsonRpcError, _thread_record
from .app_server_streaming import (
    StreamingJsonRpcStdioServer,
    StreamingLoomAppServerService,
    StreamingLoomRpcController,
)


_THREAD_META_FILENAME = "thread-library.json"
_MAX_TITLE_CHARS = 120
_MAX_QUERY_CHARS = 160


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


class ThreadLibraryStore:
    """Small durable metadata layer beside Runtime-owned session snapshots.

    Rename/archive state belongs to the client-facing conversation library, not
    the Agent execution contract. Keeping it in a sidecar file means Runtime can
    continue to own ``session.json`` without a UI action racing or rewriting its
    canonical turn state.
    """

    def __init__(self, session_store: Any) -> None:
        self.session_store = session_store

    def _path(self, session_id: str) -> Path:
        return self.session_store.session_dir(session_id) / _THREAD_META_FILENAME

    def read(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    def write(self, session_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        directory = self.session_store.session_dir(session_id)
        if not (directory / "session.json").is_file():
            raise FileNotFoundError(session_id)
        payload = self.read(session_id)
        payload.update(updates)
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

    def delete_session(self, session_id: str) -> None:
        directory = self.session_store.session_dir(session_id)
        if not (directory / "session.json").is_file():
            raise FileNotFoundError(session_id)
        shutil.rmtree(directory)


class ManagedStreamingLoomAppServerService(StreamingLoomAppServerService):
    """Streaming App Server plus durable conversation-library operations."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.thread_library = ThreadLibraryStore(self.store)

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
        if custom_title:
            record["title"] = custom_title
        record["customTitle"] = bool(custom_title)
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
            if custom_title:
                thread["title"] = custom_title
            thread["customTitle"] = bool(custom_title)
            thread["archived"] = bool(archived_at)
            thread["archivedAt"] = archived_at or None
        return payload

    def thread_rename(self, params: dict[str, Any]) -> dict[str, Any]:
        session = self._session_or_rpc_error(params.get("threadId"))
        title = _normalized_title(params.get("title"))
        try:
            self.thread_library.write(session.session_id, {"title": title})
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
    reader: TextIO | None = None,
    writer: TextIO | None = None,
) -> int:
    service = ManagedStreamingLoomAppServerService(
        runtime=runtime,
        store=store,
        model=model,
        default_workspace=default_workspace,
        default_permission_mode=default_permission_mode,
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
