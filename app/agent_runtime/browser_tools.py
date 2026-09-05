from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .contracts import ToolEffect
from .tools import AgentTool, ToolContext, ToolResult

if TYPE_CHECKING:
    from .browser_runtime import BrowserRuntime, BrowserSessionStore, BrowserStateSnapshot


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


def _store(runtime: "BrowserRuntime") -> "BrowserSessionStore":
    store = runtime.browser_sessions
    if store is None:
        raise RuntimeError("browser backend is unavailable; install Loom with the browser extra")
    return store


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
        return _snapshot_result(
            snapshot,
            "Browser state refreshed. Use only this state_revision with browser_click/browser_type.",
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
        text = str(arguments["text"])
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
                description="Refresh the current page and return bounded DOM/tabs plus a new state_revision.",
                input_schema=_schema({"browser_id": _browser_id_schema()}, ("browser_id",)),
                handler=state,
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
                    "Click an element index from the latest browser_state. state_revision is mandatory so stale DOM indexes "
                    "fail closed instead of clicking a newly remapped element."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "index": {"type": "integer", "minimum": 0},
                        "state_revision": {"type": "integer", "minimum": 1},
                    },
                    ("browser_id", "index", "state_revision"),
                ),
                handler=click,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_type",
                description=(
                    "Type ordinary non-secret text into an element from the latest browser_state. Browser v1 intentionally "
                    "does not provide a secret/password/token/cookie injection channel."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "index": {"type": "integer", "minimum": 0},
                        "state_revision": {"type": "integer", "minimum": 1},
                        "text": {"type": "string", "maxLength": 20000},
                        "clear": {"type": "boolean"},
                    },
                    ("browser_id", "index", "state_revision", "text"),
                ),
                handler=type_text,
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
