from __future__ import annotations

"""Explicit ordinary-conversation ownership for the desktop sidebar.

Projects are durable folders, but a top-level "New conversation" action should
create a normal chat in the Recent bucket even when the default workspace also
matches a registered project root. This patch stores that intent beside the
thread metadata instead of trying to infer it from the filesystem path.
"""

import json
import sys
from typing import Any


_TARGET_MODULE = "app.app_server_thread_management"
_INSTALLED = False

_ORDINARY_KIND = "ordinary"
_PROJECT_KIND = "project"


def _metadata_for(service: Any, session_id: str) -> dict[str, Any]:
    try:
        return dict(service.thread_library.read(session_id))
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError, AttributeError):
        return {}


def _explicit_project_id(metadata: dict[str, Any]) -> str:
    value = metadata.get("projectId")
    if isinstance(value, str):
        return value.strip()
    return ""


def _is_explicit_ordinary(metadata: dict[str, Any]) -> bool:
    kind = str(metadata.get("conversationKind") or "").strip().casefold()
    if kind == _ORDINARY_KIND:
        return True
    return "projectId" in metadata and _explicit_project_id(metadata) == ""


def _apply_project_metadata(record: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    if _is_explicit_ordinary(metadata):
        record["projectId"] = ""
        record["conversationKind"] = _ORDINARY_KIND
        return record

    project_id = _explicit_project_id(metadata)
    if project_id:
        record["projectId"] = project_id
        record["conversationKind"] = _PROJECT_KIND
    elif "conversationKind" not in record:
        record["conversationKind"] = _PROJECT_KIND if str(record.get("projectId") or "").strip() else _ORDINARY_KIND
    return record


def patch(module: Any) -> None:
    service_cls = module.ManagedStreamingLoomAppServerService
    if getattr(service_cls, "_loom_ordinary_conversations_installed", False):
        return

    original_thread_start = service_cls.thread_start
    original_thread_fork = service_cls.thread_fork
    original_project_list = service_cls.project_list
    original_managed_record = service_cls._managed_record
    original_thread_read = service_cls.thread_read

    def managed_record(self: Any, session: Any) -> dict[str, Any]:
        record = original_managed_record(self, session)
        metadata = _metadata_for(self, session.session_id)
        return _apply_project_metadata(record, metadata)

    def thread_read(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        payload = original_thread_read(self, params)
        thread = payload.get("thread") if isinstance(payload, dict) else None
        if isinstance(thread, dict):
            thread_id = str(thread.get("id") or "").strip()
            if thread_id:
                _apply_project_metadata(thread, _metadata_for(self, thread_id))
        return payload

    def thread_start(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        raw_project = str(params.get("projectId") or "").strip()
        raw_workspace = str(params.get("workspace") or "").strip()
        ordinary = bool(params.get("ordinary") or params.get("unfiled")) or not raw_project

        result = original_thread_start(self, params)
        thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = str(thread.get("id") or "").strip() if isinstance(thread, dict) else ""
        if not thread_id:
            return result

        updates: dict[str, Any]
        if ordinary:
            updates = {
                "conversationKind": _ORDINARY_KIND,
                "projectId": "",
                "ordinaryWorkspace": raw_workspace or str(getattr(self, "default_workspace", "") or ""),
            }
        else:
            updates = {
                "conversationKind": _PROJECT_KIND,
                "projectId": raw_project,
            }
        try:
            self.thread_library.write(thread_id, updates)
            latest = self.store.load(thread_id)
            result["thread"] = self._managed_record(latest)
            self._notify("thread/updated", {"thread": result["thread"], "reason": "conversation_kind"})
        except Exception:
            # Project classification is library metadata. A thread should still
            # be created even if the sidecar write fails.
            pass
        return result

    def thread_fork(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        source_id = str(params.get("threadId") or "").strip()
        source_metadata = _metadata_for(self, source_id) if source_id else {}
        result = original_thread_fork(self, params)
        thread = result.get("thread") if isinstance(result, dict) else None
        fork_id = str(thread.get("id") or "").strip() if isinstance(thread, dict) else ""
        if not fork_id:
            return result
        updates: dict[str, Any] = {}
        if _is_explicit_ordinary(source_metadata):
            updates = {"conversationKind": _ORDINARY_KIND, "projectId": ""}
        else:
            project_id = _explicit_project_id(source_metadata) or str(thread.get("projectId") or "").strip()
            if project_id:
                updates = {"conversationKind": _PROJECT_KIND, "projectId": project_id}
        if updates:
            try:
                self.thread_library.write(fork_id, updates)
                latest = self.store.load(fork_id)
                result["thread"] = self._managed_record(latest)
            except Exception:
                pass
        return result

    def project_thread_count(self: Any, project: Any) -> int:
        project_id = str(getattr(project, "project_id", "") or "").strip()
        return sum(
            1
            for session in self._list_session_objects()
            if str(self._managed_record(session).get("projectId") or "").strip() == project_id
        )

    def project_list(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        if not bool(params.get("adopt", True)):
            return original_project_list(self, params)

        try:
            from app.agent_runtime.storage import utc_now
        except Exception:
            from datetime import datetime, timezone

            def utc_now() -> str:  # type: ignore[no-redef]
                return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

        sessions = self._list_session_objects()
        adopt_workspaces = []
        for session in sessions:
            metadata = _metadata_for(self, session.session_id)
            if _is_explicit_ordinary(metadata):
                continue
            adopt_workspaces.append(session.workspace_dir)
        self.projects.adopt(dict.fromkeys(adopt_workspaces), now=utc_now())

        projects = list(self.projects.list())
        counts = {project.project_id: 0 for project in projects}
        unfiled = 0
        for session in sessions:
            project_id = str(self._managed_record(session).get("projectId") or "").strip()
            if project_id and project_id in counts:
                counts[project_id] += 1
            else:
                unfiled += 1

        return {
            "projects": [
                dict(project.as_dict(), threadCount=counts.get(project.project_id, 0))
                for project in projects
            ],
            "unfiledThreadCount": unfiled,
        }

    service_cls._managed_record = managed_record
    service_cls.thread_read = thread_read
    service_cls.thread_start = thread_start
    service_cls.thread_fork = thread_fork
    service_cls.project_list = project_list
    service_cls._project_thread_count = project_thread_count
    service_cls._loom_ordinary_conversations_installed = True


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    existing = sys.modules.get(_TARGET_MODULE)
    if existing is not None:
        patch(existing)
    _INSTALLED = True


__all__ = ["install", "patch"]
