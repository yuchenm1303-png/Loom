from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolResult

if TYPE_CHECKING:
    from .browser_runtime import BrowserRuntime, BrowserSessionStore, BrowserStateSnapshot


_ELEMENT_INDEX_RE = re.compile(r"\[(\d+)\]")


def _schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        payload["required"] = list(required)
    return payload


def _browser_id_schema() -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "maxLength": 128}


def _snapshot_result(snapshot: "BrowserStateSnapshot", message: str) -> ToolResult:
    return ToolResult(ok=True, content=message, data=snapshot.to_dict())


def _bounded_dom_chars(arguments: dict[str, Any], *, default: int) -> int:
    value = int(arguments.get("max_dom_chars", default))
    if not 1_000 <= value <= 60_000:
        raise ValueError("max_dom_chars must be within 1000..60000")
    return value


def _compact_snapshot_data(
    snapshot: "BrowserStateSnapshot",
    *,
    max_dom_chars: int = 12_000,
) -> dict[str, object]:
    """Return a token-lean state while preserving actionable selector indexes.

    browser-use already serializes a compact DOM, but large pages still contain a
    great deal of non-actionable text. Loom's compact view keeps the beginning of the
    page plus every indexed interactive line and one neighboring text line on either
    side. Element indexes remain from the exact same state_revision.
    """

    source = snapshot.to_dict(max_dom_chars=120_000)
    dom = str(source.get("dom") or "")
    lines = [line.strip() for line in dom.splitlines() if line.strip()]
    selected: set[int] = set(range(min(8, len(lines))))
    for index, line in enumerate(lines):
        if _ELEMENT_INDEX_RE.search(line):
            selected.add(index)
            if index > 0:
                selected.add(index - 1)
            if index + 1 < len(lines):
                selected.add(index + 1)

    output_lines: list[str] = []
    used = 0
    clipped = False
    for index in sorted(selected):
        line = lines[index]
        if len(line) > 1_500:
            line = line[:1_500] + "…"
            clipped = True
        addition = len(line) + (1 if output_lines else 0)
        if used + addition > max_dom_chars:
            clipped = True
            break
        output_lines.append(line)
        used += addition

    compact_dom = "\n".join(output_lines)
    omitted = len(selected) < len(lines) or len(output_lines) < len(selected)
    source["dom"] = compact_dom
    source["dom_view"] = "compact"
    source["dom_source_chars"] = len(dom)
    source["dom_source_lines"] = len(lines)
    source["dom_visible_lines"] = len(output_lines)
    source["dom_truncated"] = bool(source.get("dom_truncated")) or omitted or clipped
    return source


def _full_snapshot_data(
    snapshot: "BrowserStateSnapshot",
    *,
    max_dom_chars: int = 30_000,
) -> dict[str, object]:
    data = snapshot.to_dict(max_dom_chars=max_dom_chars)
    data["dom_view"] = "full"
    return data


def _find_dom_matches(
    snapshot: "BrowserStateSnapshot",
    query: str,
    *,
    max_results: int,
    context_lines: int,
    case_sensitive: bool,
) -> dict[str, object]:
    source = snapshot.to_dict(max_dom_chars=120_000)
    dom = str(source.get("dom") or "")
    lines = dom.splitlines()
    needle = query if case_sensitive else query.casefold()
    matches: list[dict[str, object]] = []
    total = 0

    for index, raw_line in enumerate(lines):
        candidate = raw_line if case_sensitive else raw_line.casefold()
        if needle not in candidate:
            continue
        total += 1
        if len(matches) >= max_results:
            continue
        start = max(0, index - context_lines)
        end = min(len(lines), index + context_lines + 1)
        before = [line.strip()[:1_200] for line in lines[start:index] if line.strip()]
        after = [line.strip()[:1_200] for line in lines[index + 1 : end] if line.strip()]
        text = raw_line.strip()[:1_500]
        direct_indexes = sorted({int(value) for value in _ELEMENT_INDEX_RE.findall(raw_line)})
        nearby_text = "\n".join(lines[start:end])
        nearby_indexes = sorted({int(value) for value in _ELEMENT_INDEX_RE.findall(nearby_text)})
        matches.append(
            {
                "line_number": index + 1,
                "text": text,
                "indexes": direct_indexes,
                "nearby_indexes": nearby_indexes,
                "context_before": before,
                "context_after": after,
            }
        )

    return {
        "browser_id": source["browser_id"],
        "state_revision": source["state_revision"],
        "url": source["url"],
        "title": source["title"],
        "query": query,
        "case_sensitive": case_sensitive,
        "match_count": total,
        "returned_matches": len(matches),
        "matches_truncated": total > len(matches),
        "matches": matches,
    }


def _store(runtime: "BrowserRuntime") -> "BrowserSessionStore":
    store = runtime.browser_sessions
    if store is None:
        raise RuntimeError("browser backend is unavailable; install Loom with the browser extra")
    return store


def _backend_snapshot_action(
    store: "BrowserSessionStore",
    owner_session_id: str,
    browser_id: str,
    method_name: str,
    *args: object,
) -> "BrowserStateSnapshot":
    """Run an optional backend interaction and pass the result through Loom validation.

    Extended interactions deliberately live behind the backend boundary. Custom
    backends can opt into them without changing the BrowserSessionManager protocol,
    while Loom still validates post-action URLs and refreshes the managed snapshot.
    """

    item = store._owned(owner_session_id, browser_id)
    method = getattr(item.backend, method_name, None)
    if not callable(method):
        raise RuntimeError(
            f"browser backend {item.backend.backend_name!r} does not support {method_name}"
        )
    state = method(*args)
    store._update_state(item, state)
    return store.snapshot(owner_session_id, browser_id)


def browser_tools(runtime: "BrowserRuntime") -> tuple[AgentTool, ...]:
    def status(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        return ToolResult(
            ok=True,
            content="Browser runtime status.",
            data=runtime.browser_status(context.session_id),
        )

    tools: list[AgentTool] = [
        AgentTool(
            name="browser_status",
            description=(
                "Report whether Loom Browser is available and its honest lifecycle/security capabilities. "
                "This never starts a browser or performs network I/O."
            ),
            input_schema=_schema({}),
            handler=status,
            effect=ToolEffect.READ_ONLY,
        )
    ]
    if runtime.browser_sessions is None:
        return tuple(tools)

    def open_browser(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        allowed_raw = arguments.get("allowed_domains") or []
        if not isinstance(allowed_raw, list):
            raise ValueError("allowed_domains must be an array")
        if len(allowed_raw) > 32:
            raise ValueError("allowed_domains supports at most 32 entries")
        allowed = runtime.effective_allowed_domains(tuple(str(item) for item in allowed_raw))
        managed = store.start(
            context.session_id,
            headless=runtime.browser_headless,
            allowed_domains=allowed,
        )
        try:
            url = str(arguments.get("url") or "").strip()
            if url:
                store.navigate(context.session_id, managed.browser_id, url, new_tab=False)
            snapshot = store.snapshot(context.session_id, managed.browser_id)
        except Exception:
            try:
                store.close(context.session_id, managed.browser_id)
            except Exception:
                pass
            raise
        return _snapshot_result(
            snapshot,
            "Browser session opened. Element indexes are valid only for the returned state_revision.",
        )

    def state(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        snapshot = store.snapshot(context.session_id, browser_id, refresh=True)
        view = str(arguments.get("view") or "full").casefold()
        if view == "compact":
            data = _compact_snapshot_data(
                snapshot,
                max_dom_chars=_bounded_dom_chars(arguments, default=12_000),
            )
            message = (
                "Browser state refreshed in compact view. Indexed elements still belong to this exact state_revision; "
                "use browser_find or request full view if surrounding page text is missing."
            )
        elif view == "full":
            data = _full_snapshot_data(
                snapshot,
                max_dom_chars=_bounded_dom_chars(arguments, default=30_000),
            )
            message = "Browser state refreshed. Use only this state_revision with element-targeting browser tools."
        else:
            raise ValueError("browser_state view must be full or compact")
        return ToolResult(ok=True, content=message, data=data)

    def find(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        query = str(arguments["query"] or "").strip()
        if not query:
            raise ValueError("browser_find query must not be empty")
        if len(query) > 500:
            raise ValueError("browser_find query exceeds 500 characters")
        max_results = int(arguments.get("max_results", 12))
        if not 1 <= max_results <= 25:
            raise ValueError("browser_find max_results must be within 1..25")
        context_lines = int(arguments.get("context_lines", 1))
        if not 0 <= context_lines <= 2:
            raise ValueError("browser_find context_lines must be within 0..2")
        snapshot = store.snapshot(context.session_id, browser_id, refresh=True)
        data = _find_dom_matches(
            snapshot,
            query,
            max_results=max_results,
            context_lines=context_lines,
            case_sensitive=bool(arguments.get("case_sensitive", False)),
        )
        return ToolResult(
            ok=True,
            content=(
                "Browser DOM search completed on a freshly refreshed state. Any returned element indexes and nearby_indexes "
                "are valid only with the returned state_revision."
            ),
            data=data,
        )

    def navigate(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.navigate(
            context.session_id,
            browser_id,
            str(arguments["url"]),
            new_tab=bool(arguments.get("new_tab", False)),
        )
        return _snapshot_result(store.snapshot(context.session_id, browser_id), "Browser navigation completed.")

    def click(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
        store.click(context.session_id, browser_id, int(arguments["index"]))
        return _snapshot_result(store.snapshot(context.session_id, browser_id), "Browser click completed.")

    def type_text(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
        raw_value = arguments["text"]
        resolver = getattr(runtime, "consume_browser_type_text", None)
        text = str(resolver(raw_value)) if callable(resolver) else str(raw_value)
        if len(text) > 20_000:
            raise ValueError("browser_type text exceeds 20,000 characters")
        store.type_text(
            context.session_id,
            browser_id,
            int(arguments["index"]),
            text,
            clear=bool(arguments.get("clear", True)),
        )
        return _snapshot_result(store.snapshot(context.session_id, browser_id), "Browser text input completed.")

    def hover(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
        snapshot = _backend_snapshot_action(
            store,
            context.session_id,
            browser_id,
            "hover",
            int(arguments["index"]),
        )
        return _snapshot_result(snapshot, "Browser hover completed.")

    def press_key(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
        key = str(arguments["key"] or "").strip()
        if not key:
            raise ValueError("browser_press key must not be empty")
        snapshot = _backend_snapshot_action(
            store,
            context.session_id,
            browser_id,
            "press_key",
            key,
        )
        return _snapshot_result(snapshot, "Browser key press completed.")

    def select_option(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
        value = str(arguments["value"])
        snapshot = _backend_snapshot_action(
            store,
            context.session_id,
            browser_id,
            "select_option",
            int(arguments["index"]),
            value,
        )
        return _snapshot_result(snapshot, "Browser dropdown selection completed.")

    def drag(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
        snapshot = _backend_snapshot_action(
            store,
            context.session_id,
            browser_id,
            "drag",
            int(arguments["source_index"]),
            int(arguments["target_index"]),
        )
        return _snapshot_result(snapshot, "Browser drag-and-drop completed.")

    def scroll(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.scroll(
            context.session_id,
            browser_id,
            str(arguments.get("direction", "down")),
            int(arguments.get("amount", 700)),
        )
        return _snapshot_result(store.snapshot(context.session_id, browser_id), "Browser scroll completed.")

    def back(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.go_back(context.session_id, browser_id)
        return _snapshot_result(store.snapshot(context.session_id, browser_id), "Browser went back.")

    def refresh(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        browser_id = str(arguments["browser_id"])
        snapshot = _store(runtime).refresh(context.session_id, browser_id)
        return _snapshot_result(snapshot, "Browser page refreshed.")

    def tabs(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        browser_id = str(arguments["browser_id"])
        snapshot = _store(runtime).tabs(context.session_id, browser_id)
        data = snapshot.to_dict()
        return ToolResult(
            ok=True,
            content="Browser tabs refreshed.",
            data={
                "browser_id": data["browser_id"],
                "state_revision": data["state_revision"],
                "url": data["url"],
                "tabs": data["tabs"],
            },
        )

    def switch_tab(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        browser_id = str(arguments["browser_id"])
        snapshot = _store(runtime).switch_tab(context.session_id, browser_id, str(arguments["tab_id"]))
        return _snapshot_result(snapshot, "Browser tab switched.")

    def close_tab(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        browser_id = str(arguments["browser_id"])
        snapshot = _store(runtime).close_tab(context.session_id, browser_id, str(arguments["tab_id"]))
        return _snapshot_result(snapshot, "Browser tab closed.")

    def screenshot(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        raw_path = str(arguments.get("path") or "").strip()
        if raw_path:
            relative = Path(raw_path)
            if relative.suffix.casefold() != ".png":
                raise ValueError("browser_screenshot path must end in .png")
        else:
            relative = Path("browser-screenshots") / f"{browser_id[:8]}-{uuid.uuid4().hex[:12]}.png"
        target = context.resolve_workspace_path(relative.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        data = store.screenshot(
            context.session_id,
            browser_id,
            full_page=bool(arguments.get("full_page", False)),
        )
        target.write_bytes(data)
        return ToolResult(
            ok=True,
            content="Browser screenshot saved to the workspace. Image bytes are not stored in ToolResult/Session.",
            data={"browser_id": browser_id, "path": relative.as_posix(), "bytes": len(data)},
        )

    def close_browser(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        browser_id = str(arguments["browser_id"])
        closed = _store(runtime).close(context.session_id, browser_id)
        return ToolResult(
            ok=closed,
            content="Browser session closed." if closed else "Browser session was not closed.",
            data={"browser_id": browser_id, "closed": closed},
        )

    sensitive = ToolEffect.SENSITIVE
    revision_schema = {"type": "integer", "minimum": 1}
    index_schema = {"type": "integer", "minimum": 0}
    tools.extend(
        [
            AgentTool(
                name="browser_open",
                description=(
                    "Open an ephemeral Loom browser session, optionally navigate to an http/https URL, and return "
                    "a bounded LLM-facing DOM state. allowed_domains can restrict this browser session. Browser v1 "
                    "does not persist cookies/storage state and has no automatic secret injection."
                ),
                input_schema=_schema(
                    {
                        "url": {"type": "string", "maxLength": 8000},
                        "allowed_domains": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 255},
                            "maxItems": 32,
                        },
                    }
                ),
                handler=open_browser,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_state",
                description=(
                    "Refresh the page and return a new state_revision. Use view='compact' for a smaller actionable DOM "
                    "that preserves indexed elements plus nearby text, or view='full' for the broader bounded DOM."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "view": {"type": "string", "enum": ["full", "compact"]},
                        "max_dom_chars": {"type": "integer", "minimum": 1000, "maximum": 60000},
                    },
                    ("browser_id",),
                ),
                handler=state,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_find",
                description=(
                    "Refresh and search the current model-visible DOM for plain text without returning the whole page. "
                    "Returns matching lines, nearby context, selector indexes, and a fresh state_revision so the result can "
                    "be followed directly by browser_click/type/hover/select when appropriate."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "query": {"type": "string", "minLength": 1, "maxLength": 500},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 25},
                        "context_lines": {"type": "integer", "minimum": 0, "maximum": 2},
                        "case_sensitive": {"type": "boolean"},
                    },
                    ("browser_id", "query"),
                ),
                handler=find,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_navigate",
                description="Navigate the current browser tab or open a new tab. URL policy is enforced before and after navigation.",
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "url": {"type": "string", "minLength": 1, "maxLength": 8000},
                        "new_tab": {"type": "boolean"},
                    },
                    ("browser_id", "url"),
                ),
                handler=navigate,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_click",
                description=(
                    "Click an element index from the latest browser_state/browser_find. state_revision is mandatory so stale "
                    "DOM indexes fail closed instead of clicking a newly remapped element."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "index": index_schema,
                        "state_revision": revision_schema,
                    },
                    ("browser_id", "index", "state_revision"),
                ),
                handler=click,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_type",
                description=(
                    "Type text into an element from the latest browser_state/browser_find. Model-produced typed text is kept "
                    "in a one-shot in-memory payload and is not stored as a durable tool-call argument. Browser v1 has no "
                    "automatic credential store or secret injection channel."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "index": index_schema,
                        "state_revision": revision_schema,
                        "text": {"type": "string", "maxLength": 20000},
                        "clear": {"type": "boolean"},
                    },
                    ("browser_id", "index", "state_revision", "text"),
                ),
                handler=type_text,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_hover",
                description=(
                    "Hover an element from the latest browser_state/browser_find to reveal CSS hover menus, tooltips, or "
                    "hidden controls. Requires the matching state_revision so stale element indexes fail closed."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "index": index_schema,
                        "state_revision": revision_schema,
                    },
                    ("browser_id", "index", "state_revision"),
                ),
                handler=hover,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_press",
                description=(
                    "Press a keyboard key or combination such as Enter, Escape, Tab, Control+A, or Shift+Tab in the active page. "
                    "Requires the latest state_revision to avoid acting on stale focus state."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "state_revision": revision_schema,
                        "key": {"type": "string", "minLength": 1, "maxLength": 100},
                    },
                    ("browser_id", "state_revision", "key"),
                ),
                handler=press_key,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_select",
                description=(
                    "Select a value from a native select element identified by an index from the latest browser_state/browser_find. "
                    "Requires the matching state_revision."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "index": index_schema,
                        "state_revision": revision_schema,
                        "value": {"type": "string", "minLength": 1, "maxLength": 2000},
                    },
                    ("browser_id", "index", "state_revision", "value"),
                ),
                handler=select_option,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_drag",
                description=(
                    "Drag one DOM element onto another using indexes from the same latest browser_state snapshot. "
                    "Requires the matching state_revision."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "source_index": index_schema,
                        "target_index": index_schema,
                        "state_revision": revision_schema,
                    },
                    ("browser_id", "source_index", "target_index", "state_revision"),
                ),
                handler=drag,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_scroll",
                description="Scroll the active page and return the refreshed browser state.",
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                        "amount": {"type": "integer", "minimum": 1, "maximum": 20000},
                    },
                    ("browser_id",),
                ),
                handler=scroll,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_back",
                description="Navigate the active tab back and return the refreshed browser state.",
                input_schema=_schema({"browser_id": _browser_id_schema()}, ("browser_id",)),
                handler=back,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_refresh",
                description="Reload the active tab and return the refreshed browser state.",
                input_schema=_schema({"browser_id": _browser_id_schema()}, ("browser_id",)),
                handler=refresh,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_tabs",
                description="List current tabs with opaque tab IDs, URLs and titles.",
                input_schema=_schema({"browser_id": _browser_id_schema()}, ("browser_id",)),
                handler=tabs,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_switch_tab",
                description="Switch focus to a tab ID returned by browser_tabs.",
                input_schema=_schema(
                    {"browser_id": _browser_id_schema(), "tab_id": {"type": "string", "minLength": 1, "maxLength": 128}},
                    ("browser_id", "tab_id"),
                ),
                handler=switch_tab,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_close_tab",
                description="Close a tab ID returned by browser_tabs and return the remaining browser state.",
                input_schema=_schema(
                    {"browser_id": _browser_id_schema(), "tab_id": {"type": "string", "minLength": 1, "maxLength": 128}},
                    ("browser_id", "tab_id"),
                ),
                handler=close_tab,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_screenshot",
                description=(
                    "Capture a PNG screenshot into the Loom workspace. Screenshot bytes are intentionally not returned to "
                    "the model or persisted in ToolResult/Session."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "full_page": {"type": "boolean"},
                        "path": {"type": "string", "maxLength": 1000},
                    },
                    ("browser_id",),
                ),
                handler=screenshot,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_close",
                description="Close the browser session and its underlying browser process/resources.",
                input_schema=_schema({"browser_id": _browser_id_schema()}, ("browser_id",)),
                handler=close_browser,
                effect=sensitive,
            ),
        ]
    )
    return tuple(tools)


__all__ = ["browser_tools"]
