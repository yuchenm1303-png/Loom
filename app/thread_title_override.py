from __future__ import annotations

import importlib.abc
import importlib.machinery
import json
import os
import re
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

_TARGET_MODULE = "app.app_server_thread_management"
_INSTALLED = False
_PATCHED = False

_MAX_TITLE_CHARS = 120
_MAX_QUERY_CHARS = 160
_AUTO_TITLE_VERSION = 1
_AUTO_TITLE_MAX_ATTEMPTS = 2
_AUTO_TITLE_MAX_CHARS = 36
_AUTO_TITLE_PROMPT_MAX_BYTES = 960
_AUTO_TITLE_RECENT_MESSAGES = 8
_AUTO_TITLE_PENDING_TITLE = "生成标题中…"
_AUTO_TITLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "minLength": 1,
            "maxLength": _AUTO_TITLE_MAX_CHARS,
        }
    },
    "required": ["title"],
    "additionalProperties": False,
}
_AUTO_TITLE_CONTROL_RE = re.compile(
    r"\[\[AI_LEDGER_[A-Z0-9_]+:[^\]\r\n]*(?:\]\])?",
    re.IGNORECASE,
)
_AUTO_TITLE_PREFIX_RE = re.compile(
    r"^(?:title|conversation\s+title|thread\s+title|标题|对话标题|会话标题)\s*[:：\-–—]\s*",
    re.IGNORECASE,
)
_AUTO_TITLE_LIST_RE = re.compile(r"^(?:[-*•]+|\d+[.)、])\s*")
_AUTO_TITLE_THINK_BLOCK_RE = re.compile(
    r"<\s*think\s*>.*?<\s*/\s*think\s*>",
    re.IGNORECASE | re.DOTALL,
)
_AUTO_TITLE_OPEN_THINK_RE = re.compile(r"^\s*<?\s*think\s*>", re.IGNORECASE)
_AUTO_TITLE_CLOSE_THINK_RE = re.compile(r"<\s*/\s*think\s*>", re.IGNORECASE)
_AUTO_TITLE_REASONING_LINE_RE = re.compile(
    r"^(?:<?\s*think\s*>|analysis\s*[:>]|reasoning\s*[:>]|思考\s*[:：>]|让我(?:先)?(?:分析|想)|let\s+me\s+(?:analyze|think)|i\s+(?:need|will|should|can)\b|we\s+(?:need|should|can)\b|the\s+user\s+(?:is|wants|asked)|this\s+conversation\b)",
    re.IGNORECASE,
)
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


def _strip_title_reasoning(value: Any, *, reject_open_block: bool = True) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""
    text = text.replace("```", "").strip()
    text = _AUTO_TITLE_THINK_BLOCK_RE.sub("\n", text).strip()
    if _AUTO_TITLE_OPEN_THINK_RE.match(text):
        return "" if reject_open_block else ""
    close_match = _AUTO_TITLE_CLOSE_THINK_RE.search(text)
    if close_match:
        text = text[close_match.end():].strip()
    return text


def _clean_title_context(value: Any) -> str:
    text = _AUTO_TITLE_CONTROL_RE.sub("", str(value or ""))
    text = _strip_title_reasoning(text, reject_open_block=False)
    return " ".join(text.replace("\x00", " ").split())


def _truncate_utf8(value: str, max_bytes: int) -> str:
    total = 0
    chars: list[str] = []
    for char in value:
        size = len(char.encode("utf-8"))
        if total + size > max_bytes:
            break
        chars.append(char)
        total += size
    return "".join(chars).rstrip()


def _escape_prompt_text(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _thread_title_instructions() -> str:
    return (
        f"Generate a concise, single-line task title of at most {_AUTO_TITLE_MAX_CHARS} characters "
        "and under five words where possible. For English titles, start with an imperative verb. "
        "For Chinese titles, use a compact verb-object task phrase, usually 4-12 Chinese characters. "
        "Preserve product names, repo names, ticket references, acronyms, and code terms exactly. "
        "Write in the user's language. Do not copy the whole user prompt. Do not use quotes, markdown, "
        "XML tags, reasoning, analysis, or trailing punctuation. Do not answer the request."
    )


def _message_text(module: ModuleType, message: Any) -> str:
    return str(module._message_text(message) or "")


def _first_user_prompt(module: ModuleType, session: Any) -> str:
    for message in session.messages:
        if message.role is not module.MessageRole.USER:
            continue
        text = _clean_title_context(_message_text(module, message))
        if text:
            return text
    return ""


def _recent_conversation_prompt(module: ModuleType, session: Any) -> str:
    messages: list[str] = []
    for message in session.messages:
        if message.role not in {module.MessageRole.USER, module.MessageRole.ASSISTANT}:
            continue
        text = _clean_title_context(_message_text(module, message))
        if not text:
            continue
        label = "User" if message.role is module.MessageRole.USER else "Assistant"
        messages.append(f"{label}: {_escape_prompt_text(text)}")
    return "\n".join(messages[-_AUTO_TITLE_RECENT_MESSAGES:])


def _auto_title_prompt(module: ModuleType, session: Any, *, user_prompt: str = "") -> tuple[str, str]:
    source_prompt = _clean_title_context(user_prompt) or _first_user_prompt(module, session)
    if not source_prompt:
        return "", ""
    prompt_body = _escape_prompt_text(source_prompt)
    recent = _recent_conversation_prompt(module, session)
    if recent and recent != prompt_body:
        prompt_body = f"Current user prompt:\n{prompt_body}\n\nRecent conversation:\n{recent}"
    prefix = f"{_thread_title_instructions()}\n\nUser prompt:\n"
    remaining = max(1, _AUTO_TITLE_PROMPT_MAX_BYTES - len(prefix.encode("utf-8")))
    return prefix + _truncate_utf8(prompt_body, remaining), source_prompt


def _sanitize_title_line(value: str) -> str:
    line = _AUTO_TITLE_LIST_RE.sub("", value.strip())
    line = _AUTO_TITLE_PREFIX_RE.sub("", line)
    line = line.strip(" \t`'\"“”‘’[]【】<>《》")
    line = " ".join(line.split())
    line = re.sub(r"[。.!！?？;；,:：]+$", "", line).strip()
    if len(line) > _AUTO_TITLE_MAX_CHARS:
        line = line[:_AUTO_TITLE_MAX_CHARS].rstrip(" -–—:：,，。.!！?？")
    return line


def _title_looks_like_raw_prompt(title: str, prompt: str = "") -> bool:
    folded = " ".join(title.casefold().split())
    prompt_folded = " ".join(str(prompt or "").casefold().split())
    if not folded or folded in _GENERIC_AUTO_TITLES:
        return True
    if folded.endswith(("吗", "么")) or folded.endswith("?") or folded.endswith("？"):
        return True
    if prompt_folded and (folded == prompt_folded or folded in prompt_folded or prompt_folded in folded):
        return True
    if prompt_folded and len(folded) >= 8:
        overlap = sum(1 for char in folded if char in prompt_folded)
        if overlap / max(1, len(folded)) > 0.86:
            return True
    return False


def _heuristic_title_from_prompt(prompt: str) -> str:
    text = _clean_title_context(prompt)
    if not text:
        return ""
    folded = text.casefold()
    if ("computeruse" in folded or "computer use" in folded) and any(token in text for token in ("电脑", "操作", "控制", "能力")):
        return "确认 Computer Use 能力"
    if "电脑" in text and any(token in text for token in ("操作", "控制", "接管", "远程")):
        return "确认电脑操控能力"
    if "codex" in folded and "标题" in text:
        return "对齐 Codex 标题生成"
    if "标题" in text and any(token in text for token in ("自动", "总结", "生成")):
        return "优化自动标题"
    if "浏览器" in text and any(token in text for token in ("插件", "bridge", "连接", "控制")):
        return "排查浏览器控制"
    if "左栏" in text or "侧栏" in text:
        return "优化侧栏交互"
    if "顶栏" in text:
        return "优化顶栏视觉"
    line = re.split(r"[。.!！?？\n\r]", text, maxsplit=1)[0]
    line = re.sub(r"^(?:帮我|请|麻烦|仔细|直接|继续|看看|看一下|给我|把这个|这个|现在|你可以|能不能|可以)", "", line).strip()
    line = re.sub(r"(?:一下|一下吧|吧|呢|吗|么)$", "", line).strip()
    if len(line) > _AUTO_TITLE_MAX_CHARS:
        line = line[:_AUTO_TITLE_MAX_CHARS].rstrip(" -–—:：,，。.!！?？")
    return line


def _sanitize_generated_title(value: Any, *, source_prompt: str = "") -> str:
    raw = _strip_title_reasoning(value)
    if not raw:
        return ""
    for item in raw.splitlines():
        line = _sanitize_title_line(item)
        if not line:
            continue
        folded = line.casefold()
        if folded in _GENERIC_AUTO_TITLES:
            continue
        if _AUTO_TITLE_REASONING_LINE_RE.match(line):
            continue
        if "create a concise title" in folded or "conversation to create" in folded:
            continue
        if _title_looks_like_raw_prompt(line, source_prompt):
            continue
        return line
    return ""


def _parse_auto_title_payload(value: Any, *, source_prompt: str = "") -> str:
    if isinstance(value, dict):
        title = _sanitize_generated_title(value.get("title"), source_prompt=source_prompt)
        if title:
            return title
    raw = _strip_title_reasoning(value)
    if not raw:
        return ""
    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                title = _sanitize_generated_title(payload.get("title"), source_prompt=source_prompt)
                if title:
                    return title
        except json.JSONDecodeError:
            pass
    return _sanitize_generated_title(stripped, source_prompt=source_prompt)


def _metadata_title_blocks_auto_title(metadata: dict[str, Any]) -> bool:
    title = str(metadata.get("title") or "").strip()
    if not title:
        return False
    title_source = str(metadata.get("titleSource") or "").strip().casefold()
    if title_source == "auto":
        source_prompt = str(metadata.get("autoTitleSourcePrompt") or "")
        if not _sanitize_generated_title(title, source_prompt=source_prompt):
            return False
    return True


def _metadata_display_title(metadata: dict[str, Any]) -> tuple[str, str]:
    custom_title = str(metadata.get("title") or "").strip()
    title_source = str(metadata.get("titleSource") or "").strip().casefold()
    if not custom_title:
        if metadata.get("autoTitlePending"):
            return _AUTO_TITLE_PENDING_TITLE, "pending"
        return "", "fallback"
    if title_source == "auto":
        source_prompt = str(metadata.get("autoTitleSourcePrompt") or "")
        custom_title = _sanitize_generated_title(custom_title, source_prompt=source_prompt)
        if not custom_title:
            if metadata.get("autoTitlePending"):
                return _AUTO_TITLE_PENDING_TITLE, "pending"
            return "", "fallback"
        return custom_title, "auto"
    if title_source not in {"auto", "manual"}:
        title_source = "manual"
    return custom_title, title_source


def _build_auto_title_request(module: ModuleType, session: Any, *, user_prompt: str = "") -> tuple[Any | None, str]:
    from app.ai import AIMessage, ChatRequest, MessageRole, StructuredOutputMode, StructuredRequest, ToolChoice

    prompt, source_prompt = _auto_title_prompt(module, session, user_prompt=user_prompt)
    if not prompt:
        return None, ""
    chat = ChatRequest(
        messages=(AIMessage(role=MessageRole.USER, content=prompt),),
        tools=(),
        tool_choice=ToolChoice.NONE,
        temperature=0.2,
        max_output_tokens=48,
    )
    return (
        StructuredRequest(
            chat=chat,
            json_schema=_AUTO_TITLE_SCHEMA,
            schema_name="thread_title",
            mode=StructuredOutputMode.AUTO,
        ),
        source_prompt,
    )


def _build_plain_auto_title_request(structured: Any) -> Any:
    from app.ai import AIMessage, ChatRequest, MessageRole, ToolChoice

    prompt = str(structured.chat.messages[-1].content)
    return ChatRequest(
        messages=(
            AIMessage(
                role=MessageRole.USER,
                content=(
                    f"{prompt}\n\n"
                    "Return exactly one JSON object matching this schema and nothing else:\n"
                    '{"title":"concise title"}'
                ),
            ),
        ),
        tools=(),
        tool_choice=ToolChoice.NONE,
        temperature=structured.chat.temperature,
        max_output_tokens=structured.chat.max_output_tokens,
    )


def _patch_thread_library(module: ModuleType) -> None:
    store_cls = module.ThreadLibraryStore

    def mark_auto_title_pending(self: Any, session_id: str, *, source_prompt: str = "") -> bool:
        with self._guard:
            payload = self._read_unlocked(session_id)
            if bool(payload.get("autoTitleDisabled")) or _metadata_title_blocks_auto_title(payload):
                return False
            payload.update(
                {
                    "autoTitlePending": True,
                    "autoTitleSourcePrompt": _clean_title_context(source_prompt),
                    "autoTitleVersion": _AUTO_TITLE_VERSION,
                }
            )
            self._write_unlocked(session_id, payload)
            return True

    def claim_auto_title_attempt(self: Any, session_id: str, *, source_prompt: str = "") -> bool:
        with self._guard:
            payload = self._read_unlocked(session_id)
            if bool(payload.get("autoTitleDisabled")):
                return False
            if _metadata_title_blocks_auto_title(payload):
                return False
            title_source = str(payload.get("titleSource") or "").strip().casefold()
            raw_title = str(payload.get("title") or "").strip()
            if title_source == "auto" and raw_title:
                payload["title"] = ""
                payload["autoTitleAttempts"] = 0
                payload["autoTitleLastError"] = "invalid_auto_title_rejected"
            attempts = max(0, int(payload.get("autoTitleAttempts") or 0))
            if attempts >= _AUTO_TITLE_MAX_ATTEMPTS:
                return False
            clean_source = _clean_title_context(source_prompt) or str(payload.get("autoTitleSourcePrompt") or "")
            payload.update(
                {
                    "autoTitleAttempts": attempts + 1,
                    "autoTitleAttemptedAt": _utc_now(),
                    "autoTitlePending": True,
                    "autoTitleSourcePrompt": clean_source,
                    "autoTitleVersion": _AUTO_TITLE_VERSION,
                }
            )
            self._write_unlocked(session_id, payload)
            return True

    def write_auto_title_if_untitled(self: Any, session_id: str, title: str) -> bool:
        with self._guard:
            payload = self._read_unlocked(session_id)
            if bool(payload.get("autoTitleDisabled")) or _metadata_title_blocks_auto_title(payload):
                return False
            payload.update(
                {
                    "title": title,
                    "titleSource": "auto",
                    "autoTitleGeneratedAt": _utc_now(),
                    "autoTitlePending": False,
                    "autoTitleVersion": _AUTO_TITLE_VERSION,
                    "autoTitleLastError": "",
                }
            )
            self._write_unlocked(session_id, payload)
            return True

    def finish_auto_title_attempt(self: Any, session_id: str, error: str) -> None:
        with self._guard:
            payload = self._read_unlocked(session_id)
            if _metadata_title_blocks_auto_title(payload) or bool(payload.get("autoTitleDisabled")):
                return
            payload["autoTitlePending"] = False
            payload["autoTitleLastError"] = error
            try:
                self._write_unlocked(session_id, payload)
            except FileNotFoundError:
                pass

    store_cls.mark_auto_title_pending = mark_auto_title_pending
    store_cls.claim_auto_title_attempt = claim_auto_title_attempt
    store_cls.write_auto_title_if_untitled = write_auto_title_if_untitled
    store_cls.finish_auto_title_attempt = finish_auto_title_attempt


def _patch_service(module: ModuleType) -> None:
    service_cls = module.ManagedStreamingLoomAppServerService
    old_turn_start = service_cls.turn_start

    def managed_record(self: Any, session: Any) -> dict[str, Any]:
        record = module._thread_record(session, active=self._is_active(session.session_id))
        metadata = self.thread_library.read(session.session_id)
        custom_title, title_source = _metadata_display_title(metadata)
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
        payload = super(service_cls, self).thread_read(params)
        thread = payload.get("thread")
        if isinstance(thread, dict):
            thread_id = str(thread.get("id") or "")
            metadata = self.thread_library.read(thread_id)
            custom_title, title_source = _metadata_display_title(metadata)
            archived_at = str(metadata.get("archivedAt") or "").strip()
            if custom_title:
                thread["title"] = custom_title
            thread["customTitle"] = bool(custom_title) and title_source not in {"fallback", "pending"}
            thread["titleSource"] = title_source
            thread["autoTitlePending"] = title_source == "pending"
            thread["archived"] = bool(archived_at)
            thread["archivedAt"] = archived_at or None
        return payload

    def turn_start(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        result = old_turn_start(self, params)
        result_thread = result.get("thread") if isinstance(result, dict) else None
        result_thread_id = str(result_thread.get("id") or params.get("threadId") or "").strip() if isinstance(result_thread, dict) else str(params.get("threadId") or "").strip()
        user_prompt = str(params.get("input") or params.get("prompt") or "")
        if result_thread_id and user_prompt:
            try:
                session = self.store.load(result_thread_id)
                if self.thread_library.mark_auto_title_pending(result_thread_id, source_prompt=user_prompt):
                    self._notify(
                        "thread/updated",
                        {"thread": self._managed_record(session), "reason": "auto_title_pending"},
                    )
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                pass
        return result

    def schedule_auto_title(self: Any, thread_id: str, *, user_prompt: str = "") -> None:
        thread_id = str(thread_id or "").strip()
        if not thread_id:
            return
        with self._auto_title_guard:
            if thread_id in self._auto_title_inflight:
                return
            metadata = self.thread_library.read(thread_id)
            if bool(metadata.get("autoTitleDisabled")) or _metadata_title_blocks_auto_title(metadata):
                return
            if int(metadata.get("autoTitleAttempts") or 0) >= _AUTO_TITLE_MAX_ATTEMPTS:
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
            metadata = self.thread_library.read(thread_id)
            stored_prompt = str(metadata.get("autoTitleSourcePrompt") or "")
            request, source_prompt = _build_auto_title_request(module, session, user_prompt=user_prompt or stored_prompt)
            if request is None:
                return
            try:
                claimed = self.thread_library.claim_auto_title_attempt(thread_id, source_prompt=source_prompt)
            except FileNotFoundError:
                return
            if not claimed:
                return
            try:
                pending = self.store.load(thread_id)
                self._notify(
                    "thread/updated",
                    {"thread": self._managed_record(pending), "reason": "auto_title_pending"},
                )
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                pass

            platform = getattr(self.runtime, "platform", None)
            execute_structured = getattr(platform, "execute_structured_chat", None)
            execute_chat = getattr(platform, "execute_chat", None)
            if not callable(execute_structured) and not callable(execute_chat):
                self.thread_library.finish_auto_title_attempt(thread_id, "platform_unavailable")
                return

            title = ""
            last_error = ""
            if callable(execute_structured):
                try:
                    payload = execute_structured(session.profile_id, request)
                    title = _parse_auto_title_payload(payload, source_prompt=source_prompt)
                except Exception as exc:
                    last_error = type(exc).__name__
            if not title and callable(execute_chat):
                try:
                    response = execute_chat(session.profile_id, _build_plain_auto_title_request(request))
                    title = _parse_auto_title_payload(getattr(response, "text", ""), source_prompt=source_prompt)
                except Exception as exc:
                    last_error = type(exc).__name__
            if not title:
                fallback = _heuristic_title_from_prompt(source_prompt)
                if fallback and not _title_looks_like_raw_prompt(fallback, source_prompt):
                    title = fallback
            if not title:
                self.thread_library.finish_auto_title_attempt(thread_id, last_error or "empty_or_generic_title")
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

    service_cls._managed_record = managed_record
    service_cls.thread_read = thread_read
    service_cls.turn_start = turn_start
    service_cls._schedule_auto_title = schedule_auto_title
    service_cls._generate_auto_title = generate_auto_title


def patch(module: ModuleType) -> None:
    global _PATCHED
    if _PATCHED:
        return
    module._AUTO_TITLE_VERSION = _AUTO_TITLE_VERSION
    module._AUTO_TITLE_MAX_ATTEMPTS = _AUTO_TITLE_MAX_ATTEMPTS
    module._AUTO_TITLE_OUTPUT_CHARS = _AUTO_TITLE_MAX_CHARS
    module._sanitize_generated_title = _sanitize_generated_title
    module._metadata_title_blocks_auto_title = _metadata_title_blocks_auto_title
    module._metadata_display_title = _metadata_display_title
    module._build_auto_title_request = lambda session, user_prompt="": _build_auto_title_request(module, session, user_prompt=user_prompt)
    _patch_thread_library(module)
    _patch_service(module)
    _PATCHED = True


class _ThreadTitlePatchLoader(importlib.abc.Loader):
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


class _ThreadTitlePatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any, target: ModuleType | None = None):
        if fullname != _TARGET_MODULE:
            return None
        try:
            sys.meta_path.remove(self)
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None or isinstance(spec.loader, _ThreadTitlePatchLoader):
            return spec
        spec.loader = _ThreadTitlePatchLoader(spec.loader)  # type: ignore[arg-type]
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
    sys.meta_path.insert(0, _ThreadTitlePatchFinder())
    _INSTALLED = True
