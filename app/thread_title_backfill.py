from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import sys
from types import ModuleType
from typing import Any

from . import thread_title_override as title_override

_TARGET_MODULE = "app.app_server_thread_management"
_INSTALLED = False
_PATCHED = False


def _status_value(session: Any) -> str:
    status = getattr(session, "status", "")
    return str(getattr(status, "value", status) or "").strip().casefold()


def _is_safe_to_backfill_title(module: ModuleType, service: Any, session: Any, metadata: dict[str, Any], title_source: str) -> bool:
    if title_source != "fallback":
        return False
    if str(metadata.get("archivedAt") or "").strip():
        return False
    if service._is_active(session.session_id):
        return False
    return _status_value(session) not in {"running", "waiting_approval"}


def _start_title_backfill_if_needed(
    module: ModuleType,
    service: Any,
    session: Any,
    metadata: dict[str, Any],
    title_source: str,
) -> tuple[str, str, bool]:
    if not _is_safe_to_backfill_title(module, service, session, metadata, title_source):
        return "", title_source, False

    source_prompt = title_override._first_user_prompt(module, session)
    if not source_prompt:
        return "", title_source, False

    try:
        marked = service.thread_library.mark_auto_title_pending(
            session.session_id,
            source_prompt=source_prompt,
        )
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return "", title_source, False

    if not marked:
        return "", title_source, False

    try:
        service._schedule_auto_title(session.session_id, user_prompt=source_prompt)
    except TypeError:
        service._schedule_auto_title(session.session_id)
    return title_override._AUTO_TITLE_PENDING_TITLE, "pending", True


def patch(module: ModuleType) -> None:
    global _PATCHED
    if _PATCHED:
        return

    service_cls = module.ManagedStreamingLoomAppServerService
    original_thread_read = service_cls.thread_read

    def managed_record(self: Any, session: Any) -> dict[str, Any]:
        record = module._thread_record(session, active=self._is_active(session.session_id))
        metadata = self.thread_library.read(session.session_id)
        custom_title, title_source = title_override._metadata_display_title(metadata)
        started_title, started_source, started = _start_title_backfill_if_needed(
            module,
            self,
            session,
            metadata,
            title_source,
        )
        if started:
            custom_title = started_title
            title_source = started_source

        archived_at = str(metadata.get("archivedAt") or "").strip()
        if custom_title:
            record["title"] = custom_title
        record["customTitle"] = bool(custom_title) and title_source not in {"fallback", "pending"}
        record["titleSource"] = title_source
        record["autoTitlePending"] = title_source == "pending"
        record["archived"] = bool(archived_at)
        record["archivedAt"] = archived_at or None
        return record

    def thread_read(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        payload = original_thread_read(self, params)
        thread = payload.get("thread")
        if not isinstance(thread, dict):
            return payload

        thread_id = str(thread.get("id") or "").strip()
        if not thread_id:
            return payload
        try:
            session = self.store.load(thread_id)
            metadata = self.thread_library.read(thread_id)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return payload

        current_source = str(thread.get("titleSource") or "fallback").strip().casefold()
        started_title, started_source, started = _start_title_backfill_if_needed(
            module,
            self,
            session,
            metadata,
            current_source,
        )
        if not started:
            return payload

        thread["title"] = started_title
        thread["customTitle"] = False
        thread["titleSource"] = started_source
        thread["autoTitlePending"] = True
        return payload

    service_cls._managed_record = managed_record
    service_cls.thread_read = thread_read
    _PATCHED = True


class _ThreadTitleBackfillLoader(importlib.abc.Loader):
    def __init__(self, loader: importlib.abc.Loader) -> None:
        self.loader = loader

    def create_module(self, spec: importlib.machinery.ModuleSpec) -> ModuleType | None:
        create_module = getattr(self.loader, "create_module", None)
        if callable(create_module):
            return create_module(spec)
        return None

    def exec_module(self, module: ModuleType) -> None:
        exec_module = getattr(self.loader, "exec_module", None)
        if not callable(exec_module):
            raise ImportError(f"loader for {_TARGET_MODULE} cannot execute modules")
        exec_module(module)
        patch(module)


class _ThreadTitleBackfillFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None or isinstance(spec.loader, _ThreadTitleBackfillLoader):
            return spec
        spec.loader = _ThreadTitleBackfillLoader(spec.loader)  # type: ignore[arg-type]
        return spec


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    existing = sys.modules.get(_TARGET_MODULE)
    if existing is not None:
        patch(existing)
        _INSTALLED = True
        return
    sys.meta_path.insert(0, _ThreadTitleBackfillFinder())
    _INSTALLED = True
