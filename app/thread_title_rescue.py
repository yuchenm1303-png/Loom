from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import re
import sys
import threading
from types import ModuleType
from typing import Any

from . import thread_title_override as title_override

_TARGET_MODULE = "app.app_server_thread_management"
_INSTALLED = False
_PATCHED = False

_GREETING_RE = re.compile(r"^(?:你好|您好|嗨|哈喽|hello|hi|hey)\s*[。.!！?？]*$", re.IGNORECASE)


def _stored_title_prompt(thread_library: Any, session_id: str) -> str:
    try:
        metadata = thread_library.read(session_id)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return ""
    for key in ("autoTitleSourcePrompt", "autoTitlePendingSourcePrompt", "autoTitlePendingSource"):
        value = title_override._clean_title_context(metadata.get(key))
        if value:
            return value
    return ""


def _safe_fallback_title(prompt: str) -> str:
    text = title_override._clean_title_context(prompt)
    if _GREETING_RE.fullmatch(text):
        return "开始对话" if any("\u4e00" <= char <= "\u9fff" for char in text) else "Start Chat"

    title = title_override._heuristic_title_from_prompt(text)
    if title and not title_override._title_looks_like_raw_prompt(title, text):
        return title

    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return "整理对话主题"
    return "New Task"


def _notify_thread_updated(service: Any, module: ModuleType, thread_id: str, reason: str) -> None:
    try:
        latest = service.store.load(thread_id)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return
    try:
        service._notify("thread/updated", {"thread": service._managed_record(latest), "reason": reason})
    except Exception:
        # Title notifications must never break the agent turn path.
        return


def _patch_thread_library(module: ModuleType) -> None:
    store_cls = module.ThreadLibraryStore

    def claim_auto_title_attempt(self: Any, session_id: str, *args: Any, source_prompt: str = "", **kwargs: Any) -> bool:
        with self._guard:
            payload = self._read_unlocked(session_id)
            if bool(payload.get("autoTitleDisabled")):
                return False
            if title_override._metadata_title_blocks_auto_title(payload):
                return False

            title_source = str(payload.get("titleSource") or "").strip().casefold()
            pending = bool(payload.get("autoTitlePending")) or title_source == "pending"
            attempts = max(0, int(payload.get("autoTitleAttempts") or 0))
            if attempts >= title_override._AUTO_TITLE_MAX_ATTEMPTS and not pending:
                return False

            clean_source = title_override._clean_title_context(source_prompt)
            if not clean_source:
                for key in ("autoTitleSourcePrompt", "autoTitlePendingSourcePrompt", "autoTitlePendingSource"):
                    clean_source = title_override._clean_title_context(payload.get(key))
                    if clean_source:
                        break

            payload.update(
                {
                    "titleSource": "pending",
                    "autoTitlePending": True,
                    "autoTitleSourcePrompt": clean_source,
                    "autoTitlePendingSourcePrompt": clean_source,
                    "autoTitleAttemptedAt": title_override._utc_now(),
                    "autoTitleAttempts": attempts + 1 if attempts < title_override._AUTO_TITLE_MAX_ATTEMPTS else attempts,
                    "autoTitleVersion": title_override._AUTO_TITLE_VERSION,
                    "autoTitleLastError": "",
                }
            )
            self._write_unlocked(session_id, payload)
            return True

    def finish_auto_title_attempt(self: Any, session_id: str, error: str = "", *args: Any, source_prompt: str = "", **kwargs: Any) -> None:
        with self._guard:
            payload = self._read_unlocked(session_id)
            if bool(payload.get("autoTitleDisabled")) or title_override._metadata_title_blocks_auto_title(payload):
                return

            clean_source = title_override._clean_title_context(source_prompt)
            if not clean_source:
                for key in ("autoTitleSourcePrompt", "autoTitlePendingSourcePrompt", "autoTitlePendingSource"):
                    clean_source = title_override._clean_title_context(payload.get(key))
                    if clean_source:
                        break

            fallback = _safe_fallback_title(clean_source)
            payload.update(
                {
                    "title": fallback,
                    "titleSource": "auto",
                    "autoTitleGeneratedAt": title_override._utc_now(),
                    "autoTitlePending": False,
                    "autoTitleVersion": title_override._AUTO_TITLE_VERSION,
                    "autoTitleFallback": True,
                    "autoTitleLastError": str(error or "title_generation_fallback"),
                }
            )
            self._write_unlocked(session_id, payload)

    store_cls.claim_auto_title_attempt = claim_auto_title_attempt
    store_cls.finish_auto_title_attempt = finish_auto_title_attempt


def _patch_service(module: ModuleType) -> None:
    service_cls = module.ManagedStreamingLoomAppServerService
    original_turn_start = service_cls.turn_start
    original_schedule_auto_title = service_cls._schedule_auto_title
    original_generate_auto_title = service_cls._generate_auto_title
    original_managed_record = service_cls._managed_record
    original_thread_read = service_cls.thread_read

    def schedule_auto_title(self: Any, thread_id: str, *, user_prompt: str = "") -> None:
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return
        with self._auto_title_guard:
            if thread_id in self._auto_title_inflight:
                return
            metadata = self.thread_library.read(thread_id)
            if bool(metadata.get("autoTitleDisabled")) or title_override._metadata_title_blocks_auto_title(metadata):
                return
            pending = bool(metadata.get("autoTitlePending")) or str(metadata.get("titleSource") or "").strip().casefold() == "pending"
            attempts = max(0, int(metadata.get("autoTitleAttempts") or 0))
            if attempts >= title_override._AUTO_TITLE_MAX_ATTEMPTS and not pending:
                return
            self._auto_title_inflight.add(thread_id)
        threading.Thread(
            target=self._generate_auto_title,
            args=(thread_id, user_prompt),
            name=f"loom-title-{thread_id[:8]}",
            daemon=True,
        ).start()

    def generate_auto_title(self: Any, thread_id: str, user_prompt: str = "") -> None:
        try:
            try:
                session = self.store.load(thread_id)
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                return

            stored_prompt = _stored_title_prompt(self.thread_library, thread_id)
            request, source_prompt = title_override._build_auto_title_request(
                module,
                session,
                user_prompt=user_prompt or stored_prompt,
            )
            source_prompt = title_override._clean_title_context(source_prompt or user_prompt or stored_prompt)
            if request is None:
                try:
                    self.thread_library.finish_auto_title_attempt(thread_id, "empty_title_context", source_prompt=source_prompt)
                except FileNotFoundError:
                    return
                _notify_thread_updated(self, module, thread_id, "auto_title_fallback")
                return

            try:
                claimed = self.thread_library.claim_auto_title_attempt(thread_id, source_prompt=source_prompt)
            except FileNotFoundError:
                return
            if not claimed:
                return

            _notify_thread_updated(self, module, thread_id, "auto_title_pending")

            title = ""
            last_error = ""
            platform = getattr(self.runtime, "platform", None)
            execute_structured = getattr(platform, "execute_structured_chat", None)
            execute_chat = getattr(platform, "execute_chat", None)

            if callable(execute_structured):
                try:
                    payload = execute_structured(session.profile_id, request)
                    title = title_override._parse_auto_title_payload(payload, source_prompt=source_prompt)
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"

            if not title and callable(execute_chat):
                try:
                    response = execute_chat(session.profile_id, title_override._build_plain_auto_title_request(request))
                    title = title_override._parse_auto_title_payload(getattr(response, "text", ""), source_prompt=source_prompt)
                except Exception as exc:
                    last_error = f"{type(exc).__name__}: {exc}"

            if not title:
                title = _safe_fallback_title(source_prompt)

            try:
                committed = self.thread_library.write_auto_title_if_untitled(thread_id, title)
            except FileNotFoundError:
                return
            if not committed:
                try:
                    self.thread_library.finish_auto_title_attempt(thread_id, last_error or "title_not_committed", source_prompt=source_prompt)
                except FileNotFoundError:
                    return
            _notify_thread_updated(self, module, thread_id, "auto_title" if committed else "auto_title_fallback")
        finally:
            with self._auto_title_guard:
                self._auto_title_inflight.discard(thread_id)

    def turn_start(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        result = original_turn_start(self, params)
        result_thread = result.get("thread") if isinstance(result, dict) else None
        result_thread_id = ""
        if isinstance(result_thread, dict):
            result_thread_id = str(result_thread.get("id") or "").strip()
        if not result_thread_id:
            result_thread_id = str(params.get("threadId") or "").strip()
        user_prompt = str(params.get("input") or params.get("prompt") or "")
        if result_thread_id and user_prompt:
            try:
                self._schedule_auto_title(result_thread_id, user_prompt=user_prompt)
            except TypeError:
                original_schedule_auto_title(self, result_thread_id)
            except Exception:
                pass
        return result

    def managed_record(self: Any, session: Any) -> dict[str, Any]:
        record = original_managed_record(self, session)
        thread_id = str(getattr(session, "session_id", "") or record.get("id") or "").strip()
        if not thread_id:
            return record
        try:
            metadata = self.thread_library.read(thread_id)
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return record
        pending = bool(record.get("autoTitlePending")) or str(record.get("titleSource") or metadata.get("titleSource") or "").strip().casefold() == "pending"
        if pending:
            prompt = _stored_title_prompt(self.thread_library, thread_id) or title_override._first_user_prompt(module, session)
            try:
                self._schedule_auto_title(thread_id, user_prompt=prompt)
            except Exception:
                pass
        return record

    def thread_read(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        payload = original_thread_read(self, params)
        thread = payload.get("thread") if isinstance(payload, dict) else None
        if isinstance(thread, dict) and (thread.get("autoTitlePending") or str(thread.get("titleSource") or "").casefold() == "pending"):
            thread_id = str(thread.get("id") or "").strip()
            if thread_id:
                try:
                    session = self.store.load(thread_id)
                    prompt = _stored_title_prompt(self.thread_library, thread_id) or title_override._first_user_prompt(module, session)
                    self._schedule_auto_title(thread_id, user_prompt=prompt)
                except Exception:
                    pass
        return payload

    service_cls._schedule_auto_title = schedule_auto_title
    service_cls._generate_auto_title = generate_auto_title
    service_cls.turn_start = turn_start
    service_cls._managed_record = managed_record
    service_cls.thread_read = thread_read
    service_cls._loom_title_rescue_installed = True


def patch(module: ModuleType) -> None:
    global _PATCHED
    service_cls = getattr(module, "ManagedStreamingLoomAppServerService", None)
    if service_cls is None or getattr(service_cls, "_loom_title_rescue_installed", False):
        _PATCHED = True
        return
    _patch_thread_library(module)
    _patch_service(module)
    _PATCHED = True


class _ThreadTitleRescueLoader(importlib.abc.Loader):
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


class _ThreadTitleRescueFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None or isinstance(spec.loader, _ThreadTitleRescueLoader):
            return spec
        spec.loader = _ThreadTitleRescueLoader(spec.loader)  # type: ignore[arg-type]
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
    sys.meta_path.insert(0, _ThreadTitleRescueFinder())
    _INSTALLED = True
