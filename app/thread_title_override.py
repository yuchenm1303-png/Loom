from __future__ import annotations

import difflib
import importlib.abc
import importlib.machinery
import json
import re
import sys
import threading
from types import ModuleType
from typing import Any

_TARGET_MODULE = "app.app_server_thread_management"
_INSTALLED = False
_PATCHED = False

_AUTO_TITLE_VERSION = 6
_AUTO_TITLE_MAX_ATTEMPTS = 3
_AUTO_TITLE_MAX_CHARS = 36
_AUTO_TITLE_PROMPT_MAX_BYTES = 960
_AUTO_TITLE_RECENT_MESSAGES = 8
_AUTO_TITLE_RETRY_DELAYS = (0.35, 1.2)
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
_AUTO_TITLE_ATTACHMENT_RE = re.compile(
    r"\[\s*\d+\s+(?:image|images|file|files)\s+attached\s*\]",
    re.IGNORECASE,
)
_AUTO_TITLE_ATTACHMENT_MANIFEST_RE = re.compile(
    r"(?:\r?\n){2,}Attached files \(already saved in this workspace\):(?:\r?\n.*)*\Z",
    re.IGNORECASE,
)
_AUTO_TITLE_JSON_FRAGMENT_RE = re.compile(
    r"^(?:[\[\]{}:,]|\{.*|.*\})$",
    re.DOTALL,
)
_AUTO_TITLE_CONVERSATIONAL_RE = re.compile(
    r"^(?:请|帮我|麻烦|你能|你可以|能否|可以|可否|为什么|怎么|如何|我想|please\b|can\s+you\b|could\s+you\b|would\s+you\b|i\s+(?:need|want|would\s+like)\b)",
    re.IGNORECASE,
)
_AUTO_TITLE_ACTION_RE = re.compile(
    r"^(?:修复|优化|检查|排查|调整|改进|对齐|添加|移除|删除|设计|实现|更新|验证|解决|改善|支持|处理|整理|重构|接入|完善|恢复|统一|简化|增强|迁移|分析|定位)"
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
_GREETING_RE = re.compile(r"^(?:你好|您好|嗨|哈喽|hello|hi|hey)\s*[。.!！?？]*$", re.IGNORECASE)
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
    "生成标题中",
    "生成标题中…",
    "整理对话主题",
}


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


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
    text = _AUTO_TITLE_ATTACHMENT_MANIFEST_RE.sub("", text)
    text = _AUTO_TITLE_ATTACHMENT_RE.sub(" ", text)
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
        f"Generate one concise task title of at most {_AUTO_TITLE_MAX_CHARS} characters. "
        "Describe the concrete task or problem, not the wording of the request. Write in the user's "
        "language. Prefer an action-object phrase: for Chinese, usually 4-12 Chinese characters such "
        "as 修复…, 优化…, 检查…, 排查…, or 对齐…; for English, start with an imperative verb and use "
        "under five words where possible. Preserve product names, repo names, ticket references, "
        "acronyms, and code terms exactly. Use recent conversation only to disambiguate the task. "
        "Ignore greetings, politeness, conversational framing, and attachment boilerplate. Do not copy "
        "a question or full user sentence. Do not answer the request. Do not use quotes, markdown, XML "
        "tags, reasoning, analysis, chain-of-thought, or trailing punctuation."
    )

def _sanitize_title_line(value: str) -> str:
    line = _AUTO_TITLE_LIST_RE.sub("", value.strip())
    line = _AUTO_TITLE_PREFIX_RE.sub("", line)
    line = line.strip(" \t`'\"“”‘’[]【】<>《》")
    line = " ".join(line.split())
    line = re.sub(r"[。.!！?？;；,:：]+$", "", line).strip()
    if len(line) > _AUTO_TITLE_MAX_CHARS:
        line = line[:_AUTO_TITLE_MAX_CHARS].rstrip(" -–—:：,，。.!！?？")
    return line


def _title_has_invalid_structure(value: Any) -> bool:
    """Reject protocol debris without re-grading an older title's wording."""

    title = str(value or "").strip()
    if not title or _AUTO_TITLE_JSON_FRAGMENT_RE.match(title):
        return True
    return title.count("{") != title.count("}") or title.count("[") != title.count("]")


def _title_looks_like_raw_prompt(title: str, prompt: str = "") -> bool:
    title_text = str(title or "").strip()
    prompt_text = str(prompt or "").strip()
    folded = " ".join(title_text.casefold().split())
    prompt_folded = " ".join(prompt_text.casefold().split())
    if not folded or folded in _GENERIC_AUTO_TITLES:
        return True
    if _AUTO_TITLE_CONVERSATIONAL_RE.match(title_text):
        return True
    if folded.endswith(("吗", "么", "呢", "吧", "?", "？")):
        return True
    if prompt_folded and folded == prompt_folded:
        return True

    # Reject short CJK clauses lifted verbatim from the prompt unless the
    # model has rewritten them into an explicit task phrase. This catches the
    # old sidebar failure mode ("右上角的标签数字和文字重叠…") without rejecting
    # useful generated titles such as "修复标签数字文字重叠".
    compact_title = re.sub(r"[\W_]+", "", title_text.casefold())
    compact_prompt = re.sub(r"[\W_]+", "", prompt_text.casefold())
    if _has_cjk(title_text) and len(compact_title) >= 6 and not _AUTO_TITLE_ACTION_RE.match(title_text):
        if compact_prompt and compact_title in compact_prompt:
            return True
        # Near-verbatim first-clause copies (for example dropping only "的/和")
        # are just as poor as exact copies. Compare against individual source
        # clauses so a long follow-up instruction does not hide that similarity.
        for clause in re.split(r"[，。！？!?；;：:\n]+", prompt_text):
            compact_clause = re.sub(r"[\W_]+", "", clause.casefold())
            if not compact_clause:
                continue
            similarity = difflib.SequenceMatcher(None, compact_title, compact_clause).ratio()
            if similarity >= 0.72:
                return True

    if prompt_folded and len(folded) >= 18 and folded in prompt_folded:
        return True
    return False

def _placeholder_title(prompt: str) -> str:
    return "新对话" if _has_cjk(_clean_title_context(prompt)) else "New conversation"


def _safe_initial_title_from_prompt(prompt: str) -> str:
    """Return a neutral provisional title instead of clipping user text.

    Real task titles are model-generated. While that detached request is
    pending, the UI receives only a language-matched placeholder.
    """

    return _placeholder_title(prompt)

def _sanitize_generated_title(value: Any, *, source_prompt: str = "") -> str:
    raw = _strip_title_reasoning(value)
    if not raw:
        return ""
    for item in raw.splitlines():
        line = _sanitize_title_line(item)
        if not line:
            continue
        if _title_has_invalid_structure(line):
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
    if stripped.startswith(("{", "[")):
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                title = _sanitize_generated_title(payload.get("title"), source_prompt=source_prompt)
                if title:
                    return title
        except json.JSONDecodeError:
            return ""
        # Structured title requests accept an object with a valid `title`
        # field only. Never reinterpret arrays, scalars, or schema debris as a
        # plain title after JSON parsing has begun.
        return ""
    return _sanitize_generated_title(stripped, source_prompt=source_prompt)


def _stored_title_prompt(thread_library: Any, session_id: str) -> str:
    try:
        metadata = thread_library.read(session_id)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return ""
    for key in (
        "autoTitleSourcePrompt",
        "autoTitlePendingSourcePrompt",
        "autoTitlePendingSource",
    ):
        value = _clean_title_context(metadata.get(key))
        if value:
            return value
    return ""


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


def _recent_title_messages(module: ModuleType, session: Any, *, source_prompt: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    source_skipped = False
    for message in getattr(session, "messages", ()):
        if message.role is module.MessageRole.USER:
            role = "user"
        elif message.role is module.MessageRole.ASSISTANT:
            role = "assistant"
        else:
            continue
        text = _clean_title_context(_message_text(module, message))
        if not text:
            continue
        if role == "user" and not source_skipped and text == source_prompt:
            source_skipped = True
            continue
        rows.append((role, text))
    return rows[-_AUTO_TITLE_RECENT_MESSAGES:]


def _auto_title_prompt(module: ModuleType, session: Any, *, user_prompt: str = "") -> tuple[str, str]:
    source_prompt = _clean_title_context(user_prompt) or _first_user_prompt(module, session)
    if not source_prompt:
        return "", ""

    instructions = _thread_title_instructions()
    prefix = "Canonical first user request:\n<request>"
    middle = "</request>\n\nRecent substantive conversation:\n<conversation>\n"
    suffix = "\n</conversation>"
    fixed_bytes = len((prefix + middle + suffix).encode("utf-8"))
    available = max(96, _AUTO_TITLE_PROMPT_MAX_BYTES - fixed_bytes)
    source_budget = max(64, int(available * 0.55))
    recent_budget = max(32, available - source_budget)

    source_text = _truncate_utf8(_escape_prompt_text(source_prompt), source_budget)
    rows = _recent_title_messages(module, session, source_prompt=source_prompt)
    rendered_reversed: list[str] = []
    remaining = recent_budget
    for role, text in reversed(rows):
        open_tag = f'<message role="{role}">'
        close_tag = "</message>"
        overhead = len((open_tag + close_tag + "\n").encode("utf-8"))
        if remaining <= overhead:
            break
        body = _truncate_utf8(_escape_prompt_text(text), remaining - overhead)
        if not body:
            continue
        rendered_reversed.append(f"{open_tag}{body}{close_tag}")
        remaining -= len((open_tag + body + close_tag + "\n").encode("utf-8"))
    rendered = "\n".join(reversed(rendered_reversed))

    prompt = f"{prefix}{source_text}{middle}{rendered}{suffix}"
    return prompt, source_prompt

def _build_auto_title_request(module: ModuleType, session: Any, *, user_prompt: str = "") -> tuple[Any | None, str]:
    from app.ai import AIMessage, ChatRequest, MessageRole, StructuredOutputMode, StructuredRequest, ToolChoice

    prompt, source_prompt = _auto_title_prompt(module, session, user_prompt=user_prompt)
    if not prompt:
        return None, ""
    chat = ChatRequest(
        messages=(
            AIMessage(role=MessageRole.SYSTEM, content=_thread_title_instructions()),
            AIMessage(role=MessageRole.USER, content=prompt),
        ),
        tools=(),
        tool_choice=ToolChoice.NONE,
        temperature=0.2,
        max_output_tokens=48,
        session_id=str(getattr(session, "session_id", "") or ""),
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

    return ChatRequest(
        messages=(
            *structured.chat.messages,
            AIMessage(
                role=MessageRole.USER,
                content=(
                    "Return exactly one JSON object matching this schema and nothing else:\n"
                    '{"title":"concise task title"}'
                ),
            ),
        ),
        tools=(),
        tool_choice=ToolChoice.NONE,
        temperature=structured.chat.temperature,
        max_output_tokens=structured.chat.max_output_tokens,
        session_id=structured.chat.session_id,
    )

def _metadata_has_committed_title(metadata: dict[str, Any]) -> bool:
    """Whether the conversation already owns a durable title.

    Title quality checks belong at generation time. Once a model title has been
    accepted and persisted, later turns and future sanitizer changes must not
    reinterpret it as provisional and erase it. This mirrors manual rename
    semantics: a committed title is immutable unless the user explicitly
    renames it.

    autoTitleFallback is the only auto-title state that remains retryable;
    those records were never successfully committed.
    """

    title = str(metadata.get("title") or "").strip()
    if not title:
        return False
    title_source = str(metadata.get("titleSource") or "").strip().casefold()
    if title_source == "manual":
        return True
    if title_source == "auto":
        return not bool(metadata.get("autoTitleFallback")) and not _title_has_invalid_structure(title)
    return bool(title and title_source not in {"pending", "fallback"})


def _metadata_title_blocks_auto_title(metadata: dict[str, Any]) -> bool:
    if bool(metadata.get("autoTitleDisabled")):
        return True
    return _metadata_has_committed_title(metadata)


def _metadata_display_title(metadata: dict[str, Any]) -> tuple[str, str]:
    custom_title = str(metadata.get("title") or "").strip()
    title_source = str(metadata.get("titleSource") or "").strip().casefold()
    source_prompt = _clean_title_context(
        metadata.get("autoTitleSourcePrompt") or metadata.get("autoTitlePendingSourcePrompt") or ""
    )

    if title_source == "manual" and custom_title:
        return custom_title, "manual"

    # A successfully persisted auto title is already sanitized at commit time.
    # Never run it through today's semantic validator again: doing so allowed a
    # stricter future validator to turn yesterday's real title into "新对话" on
    # the next user message.
    if (
        title_source == "auto"
        and custom_title
        and not bool(metadata.get("autoTitleFallback"))
        and not _title_has_invalid_structure(custom_title)
    ):
        return custom_title, "auto"

    if title_source == "auto" and custom_title and _title_has_invalid_structure(custom_title):
        return _placeholder_title(source_prompt or custom_title), "fallback"

    if title_source in {"pending", "fallback"} or metadata.get("autoTitlePending") or metadata.get("autoTitleFallback"):
        return _placeholder_title(source_prompt or custom_title), (
            "pending" if bool(metadata.get("autoTitlePending")) else "fallback"
        )

    if custom_title:
        return custom_title, "manual"

    return "", "fallback"

def _display_title_for_session(module: ModuleType, metadata: dict[str, Any], session: Any) -> tuple[str, str]:
    title, source = _metadata_display_title(metadata)
    if title:
        return title, source
    first_prompt = _first_user_prompt(module, session)
    if first_prompt:
        return _placeholder_title(first_prompt), "pending"
    return "", source

def _patch_thread_library(module: ModuleType) -> None:
    store_cls = module.ThreadLibraryStore

    def mark_auto_title_pending(self: Any, session_id: str, *args: Any, source_prompt: str = "", **kwargs: Any) -> bool:
        clean_source = _clean_title_context(source_prompt)
        if not clean_source:
            return False
        with self._guard:
            payload = self._read_unlocked(session_id)
            if bool(payload.get("autoTitleDisabled")) or _metadata_title_blocks_auto_title(payload):
                return False

            stored_source = _clean_title_context(
                payload.get("autoTitleSourcePrompt") or payload.get("autoTitlePendingSourcePrompt") or ""
            )
            canonical_source = stored_source or clean_source
            version = max(0, int(payload.get("autoTitleVersion") or 0))
            attempts = max(0, int(payload.get("autoTitleAttempts") or 0))
            if version < _AUTO_TITLE_VERSION:
                attempts = 0
            if attempts >= _AUTO_TITLE_MAX_ATTEMPTS:
                return False

            payload.update(
                {
                    "title": "",
                    "titleSource": "pending",
                    "autoTitlePending": True,
                    "autoTitleFallback": False,
                    "autoTitleSourcePrompt": canonical_source,
                    "autoTitlePendingSourcePrompt": canonical_source,
                    "autoTitlePendingAt": _utc_now(),
                    "autoTitleAttempts": attempts,
                    "autoTitleVersion": _AUTO_TITLE_VERSION,
                    "autoTitleLastError": "",
                }
            )
            self._write_unlocked(session_id, payload)
            return True

    def claim_auto_title_attempt(self: Any, session_id: str, *args: Any, source_prompt: str = "", **kwargs: Any) -> bool:
        with self._guard:
            payload = self._read_unlocked(session_id)
            if bool(payload.get("autoTitleDisabled")) or _metadata_title_blocks_auto_title(payload):
                return False

            stored_source = _clean_title_context(
                payload.get("autoTitleSourcePrompt") or payload.get("autoTitlePendingSourcePrompt") or ""
            )
            requested_source = _clean_title_context(source_prompt)
            if stored_source and requested_source and stored_source != requested_source:
                return False
            clean_source = stored_source or requested_source
            if not clean_source:
                return False

            version = max(0, int(payload.get("autoTitleVersion") or 0))
            attempts = max(0, int(payload.get("autoTitleAttempts") or 0))
            if version < _AUTO_TITLE_VERSION:
                attempts = 0
            if attempts >= _AUTO_TITLE_MAX_ATTEMPTS:
                return False

            payload.update(
                {
                    "title": "",
                    "titleSource": "pending",
                    "autoTitlePending": True,
                    "autoTitleFallback": False,
                    "autoTitleSourcePrompt": clean_source,
                    "autoTitlePendingSourcePrompt": clean_source,
                    "autoTitleAttemptedAt": _utc_now(),
                    "autoTitleAttempts": attempts + 1,
                    "autoTitleVersion": _AUTO_TITLE_VERSION,
                    "autoTitleLastError": "",
                }
            )
            self._write_unlocked(session_id, payload)
            return True

    def write_auto_title_if_untitled(
        self: Any,
        session_id: str,
        title: str,
        *args: Any,
        source_prompt: str = "",
        **kwargs: Any,
    ) -> bool:
        with self._guard:
            payload = self._read_unlocked(session_id)
            if bool(payload.get("autoTitleDisabled")):
                return False
            title_source = str(payload.get("titleSource") or "").strip().casefold()
            if title_source == "manual" or _metadata_title_blocks_auto_title(payload):
                return False

            stored_source = _clean_title_context(
                payload.get("autoTitleSourcePrompt") or payload.get("autoTitlePendingSourcePrompt") or ""
            )
            expected_source = _clean_title_context(source_prompt)
            if expected_source and stored_source and stored_source != expected_source:
                return False
            canonical_source = stored_source or expected_source
            clean_title = _sanitize_generated_title(title, source_prompt=canonical_source)
            if not clean_title:
                return False

            payload.update(
                {
                    "title": clean_title,
                    "titleSource": "auto",
                    "autoTitleGeneratedAt": _utc_now(),
                    "autoTitlePending": False,
                    "autoTitleFallback": False,
                    "autoTitleSourcePrompt": canonical_source,
                    "autoTitleVersion": _AUTO_TITLE_VERSION,
                    "autoTitleLastError": "",
                }
            )
            self._write_unlocked(session_id, payload)
            return True

    def finish_auto_title_attempt(
        self: Any,
        session_id: str,
        error: str = "",
        *args: Any,
        source_prompt: str = "",
        **kwargs: Any,
    ) -> None:
        with self._guard:
            payload = self._read_unlocked(session_id)
            if bool(payload.get("autoTitleDisabled")):
                return
            title_source = str(payload.get("titleSource") or "").strip().casefold()
            if title_source == "manual":
                return
            if title_source == "auto" and _metadata_title_blocks_auto_title(payload):
                return

            stored_source = _clean_title_context(
                payload.get("autoTitleSourcePrompt") or payload.get("autoTitlePendingSourcePrompt") or ""
            )
            clean_source = _clean_title_context(source_prompt)
            if clean_source and stored_source and clean_source != stored_source:
                return
            canonical_source = stored_source or clean_source
            attempts = max(0, int(payload.get("autoTitleAttempts") or 0))
            pending = attempts < _AUTO_TITLE_MAX_ATTEMPTS

            payload.update(
                {
                    "title": "",
                    "titleSource": "pending" if pending else "fallback",
                    "autoTitlePending": pending,
                    "autoTitleFallback": not pending,
                    "autoTitleSourcePrompt": canonical_source,
                    "autoTitlePendingSourcePrompt": canonical_source,
                    "autoTitleVersion": _AUTO_TITLE_VERSION,
                    "autoTitleLastError": str(error or "title_generation_failed"),
                    "autoTitleLastAttemptFinishedAt": _utc_now(),
                }
            )
            self._write_unlocked(session_id, payload)

    store_cls.mark_auto_title_pending = mark_auto_title_pending
    store_cls.claim_auto_title_attempt = claim_auto_title_attempt
    store_cls.write_auto_title_if_untitled = write_auto_title_if_untitled
    store_cls.finish_auto_title_attempt = finish_auto_title_attempt

def _notify_thread_updated(service: Any, thread_id: str, reason: str) -> None:
    try:
        latest = service.store.load(thread_id)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
        return
    try:
        service._notify("thread/updated", {"thread": service._managed_record(latest), "reason": reason})
    except Exception:
        return


def _patch_service(module: ModuleType) -> None:
    service_cls = module.ManagedStreamingLoomAppServerService
    if getattr(service_cls, "_loom_first_prompt_titles_installed", False):
        return

    original_record = service_cls._record
    original_turn_start = service_cls.turn_start
    original_thread_read = service_cls.thread_read
    original_runtime_event = service_cls._on_runtime_event

    def record(self: Any, session: Any, *, active: bool = False) -> dict[str, Any]:
        """Return the same canonical title on every notification path.

        Several mixins publish ``self._record(...)`` directly. Leaving the
        inherited first-message fallback there allowed a late runtime event to
        overwrite the active header while the sidebar kept the generated
        title from ``_managed_record``.
        """

        result = original_record(self, session, active=active)
        metadata = self.thread_library.read(session.session_id)
        display_title, title_source = _display_title_for_session(module, metadata, session)
        if display_title:
            result["title"] = display_title
        result["customTitle"] = title_source in {"manual", "auto"}
        result["titleSource"] = title_source
        result["autoTitlePending"] = bool(metadata.get("autoTitlePending")) or title_source == "pending"
        result["autoTitleFallback"] = bool(metadata.get("autoTitleFallback")) or title_source == "fallback"
        return result

    def managed_record(self: Any, session: Any) -> dict[str, Any]:
        record = original_record(self, session, active=self._is_active(session.session_id))
        metadata = self.thread_library.read(session.session_id)
        display_title, title_source = _display_title_for_session(module, metadata, session)
        archived_at = str(metadata.get("archivedAt") or "").strip()
        if display_title:
            record["title"] = display_title
        record["customTitle"] = title_source in {"manual", "auto"}
        record["titleSource"] = title_source
        record["autoTitlePending"] = bool(metadata.get("autoTitlePending")) or title_source == "pending"
        record["autoTitleFallback"] = bool(metadata.get("autoTitleFallback"))
        record["archived"] = bool(archived_at)
        record["archivedAt"] = archived_at or None
        return record

    def thread_read(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        payload = original_thread_read(self, params)
        thread = payload.get("thread") if isinstance(payload, dict) else None
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

        source_prompt = _stored_title_prompt(self.thread_library, thread_id) or _first_user_prompt(module, session)
        version = max(0, int(metadata.get("autoTitleVersion") or 0))
        attempts = max(0, int(metadata.get("autoTitleAttempts") or 0))
        attempt_budget_available = version < _AUTO_TITLE_VERSION or attempts < _AUTO_TITLE_MAX_ATTEMPTS
        if (
            source_prompt
            and not bool(metadata.get("autoTitleDisabled"))
            and not _metadata_title_blocks_auto_title(metadata)
            and attempt_budget_available
            and session.status not in {module.AgentStatus.RUNNING, module.AgentStatus.WAITING_APPROVAL}
        ):
            try:
                self.thread_library.mark_auto_title_pending(thread_id, source_prompt=source_prompt)
                metadata = self.thread_library.read(thread_id)
                self._schedule_auto_title(thread_id, user_prompt=source_prompt)
            except Exception:
                pass

        display_title, title_source = _display_title_for_session(module, metadata, session)
        archived_at = str(metadata.get("archivedAt") or "").strip()
        if display_title:
            thread["title"] = display_title
        thread["customTitle"] = title_source in {"manual", "auto"}
        thread["titleSource"] = title_source
        thread["autoTitlePending"] = bool(metadata.get("autoTitlePending")) or title_source == "pending"
        thread["autoTitleFallback"] = bool(metadata.get("autoTitleFallback"))
        thread["archived"] = bool(archived_at)
        thread["archivedAt"] = archived_at or None
        return payload

    def schedule_auto_title(self: Any, thread_id: str, *, user_prompt: str = "") -> None:
        thread_id = str(thread_id or "").strip()
        if not thread_id or self._is_sub_agent_session(thread_id):
            return
        with self._auto_title_guard:
            if thread_id in self._auto_title_inflight:
                return
            metadata = self.thread_library.read(thread_id)
            if bool(metadata.get("autoTitleDisabled")) or _metadata_title_blocks_auto_title(metadata):
                return
            version = max(0, int(metadata.get("autoTitleVersion") or 0))
            attempts = max(0, int(metadata.get("autoTitleAttempts") or 0))
            if version >= _AUTO_TITLE_VERSION and attempts >= _AUTO_TITLE_MAX_ATTEMPTS:
                return
            self._auto_title_inflight.add(thread_id)
        threading.Thread(
            target=self._generate_auto_title,
            args=(thread_id, user_prompt),
            name=f"loom-title-{thread_id[:8]}",
            daemon=True,
        ).start()

    def generate_auto_title(self: Any, thread_id: str, user_prompt: str = "") -> None:
        source_prompt = ""

        def finish(error: str) -> None:
            try:
                self.thread_library.finish_auto_title_attempt(
                    thread_id,
                    error,
                    source_prompt=source_prompt,
                )
                metadata = self.thread_library.read(thread_id)
                pending = bool(metadata.get("autoTitlePending"))
                reason = "auto_title_retry" if pending else "auto_title_unavailable"
                _notify_thread_updated(self, thread_id, reason)

                # Transient provider errors and malformed title samples should
                # heal without requiring the user to reopen the conversation.
                # The detached timer is bounded; the normal scheduler re-checks
                # manual renames, committed titles, sub-agents, and attempt limits.
                attempts = max(0, int(metadata.get("autoTitleAttempts") or 0))
                if pending and 1 <= attempts <= len(_AUTO_TITLE_RETRY_DELAYS):
                    timer = threading.Timer(
                        _AUTO_TITLE_RETRY_DELAYS[attempts - 1],
                        self._schedule_auto_title,
                        args=(thread_id,),
                        kwargs={"user_prompt": source_prompt},
                    )
                    timer.daemon = True
                    timer.start()
            except Exception:
                pass

        try:
            try:
                session = self.store.load(thread_id)
            except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
                return
            if session.status in {module.AgentStatus.RUNNING, module.AgentStatus.WAITING_APPROVAL}:
                return

            source_prompt = (
                _stored_title_prompt(self.thread_library, thread_id)
                or _first_user_prompt(module, session)
                or _clean_title_context(user_prompt)
            )
            source_prompt = _clean_title_context(source_prompt)
            if not source_prompt:
                return

            try:
                self.thread_library.mark_auto_title_pending(thread_id, source_prompt=source_prompt)
            except Exception:
                pass

            request, request_source = _build_auto_title_request(module, session, user_prompt=source_prompt)
            source_prompt = _clean_title_context(request_source or source_prompt)
            if request is None:
                finish("empty_title_context")
                return

            if not self.thread_library.claim_auto_title_attempt(thread_id, source_prompt=source_prompt):
                return

            title = ""
            errors: list[str] = []
            platform_for_session = getattr(self.runtime, "platform_for_session", None)
            platform = (
                platform_for_session(thread_id)
                if callable(platform_for_session)
                else getattr(self.runtime, "platform", None)
            )
            execute_structured = getattr(platform, "execute_structured_chat", None)
            execute_chat = getattr(platform, "execute_chat", None)

            # Plain chat is Loom's compatibility baseline across OpenAI-compatible
            # providers. Native structured output is a recovery lane, matching the
            # stricter Codex contract without making it a requirement for every
            # configured model.
            if callable(execute_chat):
                try:
                    response = execute_chat(session.profile_id, _build_plain_auto_title_request(request))
                    title = _parse_auto_title_payload(getattr(response, "text", ""), source_prompt=source_prompt)
                except Exception as exc:
                    errors.append(f"plain:{type(exc).__name__}: {exc}")

            if not title and callable(execute_structured):
                try:
                    structured_payload = execute_structured(session.profile_id, request)
                    title = _parse_auto_title_payload(structured_payload, source_prompt=source_prompt)
                except Exception as exc:
                    errors.append(f"structured:{type(exc).__name__}: {exc}")

            if not title:
                finish("; ".join(errors) or "empty_or_invalid_title")
                return

            if self.thread_library.write_auto_title_if_untitled(
                thread_id,
                title,
                source_prompt=source_prompt,
            ):
                _notify_thread_updated(self, thread_id, "auto_title")
        except Exception as exc:
            finish(f"{type(exc).__name__}: {exc}")
        finally:
            with self._auto_title_guard:
                self._auto_title_inflight.discard(thread_id)

    def turn_start(self: Any, params: dict[str, Any]) -> dict[str, Any]:
        result = original_turn_start(self, params)
        result_thread = result.get("thread") if isinstance(result, dict) else None
        thread_id = str(result_thread.get("id") or "").strip() if isinstance(result_thread, dict) else ""
        if not thread_id:
            thread_id = str(params.get("threadId") or "").strip()
        user_prompt = _clean_title_context(params.get("input") or params.get("prompt") or "")
        if thread_id and user_prompt:
            try:
                if self.thread_library.mark_auto_title_pending(thread_id, source_prompt=user_prompt):
                    _notify_thread_updated(self, thread_id, "auto_title_pending")
            except Exception:
                pass
        # Title generation is deliberately detached from the active task. The
        # inherited TURN_COMPLETED hook schedules it after the task finishes so
        # cosmetic metadata never competes with the user's model request.
        return result

    def on_runtime_event(self: Any, event: Any) -> None:
        original_runtime_event(self, event)
        if event.kind in {
            module.AgentEventKind.TURN_FAILED,
            module.AgentEventKind.TURN_CANCELLED,
            module.AgentEventKind.TURN_INTERRUPTED,
            module.AgentEventKind.LIMIT_REACHED,
        }:
            self._schedule_auto_title(event.session_id)

    service_cls._record = record
    service_cls._managed_record = managed_record
    service_cls.thread_read = thread_read
    service_cls._schedule_auto_title = schedule_auto_title
    service_cls._generate_auto_title = generate_auto_title
    service_cls.turn_start = turn_start
    service_cls._on_runtime_event = on_runtime_event
    service_cls._loom_first_prompt_titles_installed = True

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
    module._build_auto_title_request = lambda session, user_prompt="": _build_auto_title_request(
        module,
        session,
        user_prompt=user_prompt,
    )
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
