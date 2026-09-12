from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .browser_session import BrowserTextNotFoundError
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


def _snapshot_result(
    snapshot: "BrowserStateSnapshot",
    message: str,
    *,
    extra: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(ok=True, content=message, data={**snapshot.to_dict(), **(extra or {})})


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
        payload = runtime.browser_status(context.session_id)
        if bool(arguments.get("include_attachable")):
            discover = getattr(runtime, "browser_attachable_browsers", None)
            payload["attachable_browsers"] = list(discover()) if callable(discover) else []
        return ToolResult(ok=True, content="Browser runtime status.", data=payload)

    tools: list[AgentTool] = [
        AgentTool(
            name="browser_status",
            description=(
                "Report whether Loom Browser is available and its honest lifecycle/security capabilities. "
                "This never starts a browser. It performs no network I/O unless include_attachable is "
                "set, which additionally checks a short fixed list of loopback DevTools ports and "
                "returns the local browsers that can be driven with browser_open connect=attach."
            ),
            input_schema=_schema(
                {
                    "include_attachable": {
                        "type": "boolean",
                        "description": (
                            "Also list local browsers that are running with remote debugging "
                            "enabled. Requires model-selected browser connections to be allowed."
                        ),
                    }
                }
            ),
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

        connect = str(arguments.get("connect") or "").strip()
        requested_cdp = str(arguments.get("cdp_url") or "").strip()
        if (connect or requested_cdp) and not bool(
            getattr(runtime, "browser_model_controlled_connection", False)
        ):
            raise ValueError(
                "choosing the browser is disabled; Loom uses the connection configured in "
                "Settings > Browser. Remove connect/cdp_url, or ask the user to enable "
                "model-selected browser connections."
            )
        factory, external, label = runtime.browser_session_connection(
            connect, cdp_url=requested_cdp
        )

        managed = store.start(
            context.session_id,
            headless=runtime.browser_headless,
            allowed_domains=allowed,
            backend_factory=factory,
            external_browser=external,
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
            # Which browser this session drives changes what the model may assume
            # about the tabs it sees, so it is reported rather than inferred.
            extra={"browser_connection": label, "external_browser": bool(external)},
        )

    def state(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        snapshot = store.snapshot(context.session_id, browser_id, refresh=True)
        return _snapshot_result(
            snapshot,
            "Browser state refreshed. Use only this state_revision with element-targeting browser tools.",
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

    def go_forward(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        snapshot = _backend_snapshot_action(store, context.session_id, browser_id, "go_forward")
        return _snapshot_result(snapshot, "Browser moved forward in history.")

    def find_text(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        text = str(arguments["text"] or "").strip()
        if not text:
            raise ValueError("browser_find text must not be empty")
        direction = str(arguments.get("direction") or "down").strip().casefold()
        if direction not in {"up", "down"}:
            raise ValueError("browser_find direction must be up or down")
        try:
            snapshot = _backend_snapshot_action(
                store, context.session_id, browser_id, "find_text", text, direction
            )
        except BrowserTextNotFoundError:
            # Not finding the text is an answer, not a malfunction. Raising made a
            # plain search look like a broken tool call and told the model nothing
            # about the page it is on.
            snapshot = store.snapshot(context.session_id, browser_id, refresh=True)
            return ToolResult(
                ok=False,
                content="That text is not present on the page.",
                data={**snapshot.to_dict(), "found": False},
            )
        return _snapshot_result(
            snapshot,
            "Scrolled to the first match. Confirm against the returned state.",
            extra={"found": True},
        )

    def dropdown_options(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
        item = store._owned(context.session_id, browser_id)
        reader = getattr(item.backend, "dropdown_options", None)
        if not callable(reader):
            raise RuntimeError(
                f"browser backend {item.backend.backend_name!r} does not support dropdown_options"
            )
        options = list(reader(int(arguments["index"])))
        return ToolResult(
            ok=True,
            content=(
                f"Read {len(options)} option(s). Pass one option's text to browser_select."
                if options
                else "That element exposed no options; it may not be a select."
            ),
            data={"browser_id": browser_id, "index": int(arguments["index"]), "options": options},
        )

    def upload_file(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
        # Uploading sends a local file to whatever site the page belongs to, so the
        # path is confined to the workspace rather than taken as an absolute path.
        target = context.resolve_workspace_path(str(arguments["path"]))
        if not target.is_file():
            raise ValueError("browser_upload path must be an existing file inside the workspace")
        snapshot = _backend_snapshot_action(
            store,
            context.session_id,
            browser_id,
            "upload_file",
            int(arguments["index"]),
            str(target),
        )
        return _snapshot_result(
            snapshot,
            "File attached to the page input. The site has not necessarily submitted it yet.",
        )

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
                        "connect": {
                            "type": "string",
                            "enum": ["launch", "attach", "current_tab"],
                            "description": (
                                "Which browser to drive, when Settings > Browser allows the model "
                                "to choose. launch starts Loom's own browser; attach drives an "
                                "already-running local browser named by cdp_url; current_tab drives "
                                "the tab the user is looking at through the Loom bridge extension. "
                                "Omit to use the configured default."
                            ),
                        },
                        "cdp_url": {
                            "type": "string",
                            "maxLength": 2000,
                            "description": (
                                "Loopback DevTools endpoint for connect=attach, taken from "
                                "browser_status attachable_browsers. Must be http/https/ws/wss on "
                                "127.0.0.1 or ::1 with an explicit port."
                            ),
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
                    "Type text into an element from the latest browser_state. Model-produced typed text is kept in a one-shot "
                    "in-memory payload and is not stored as a durable tool-call argument. Browser v1 has no automatic "
                    "credential store or secret injection channel."
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
                    "Hover an element from the latest browser_state to reveal CSS hover menus, tooltips, or hidden controls. "
                    "Requires the matching state_revision so stale element indexes fail closed."
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
                    "Select a value from a native select element identified by an index from the latest browser_state. "
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
                    "Requires the matching state_revision. This presses, moves and releases the mouse, which drives "
                    "the drag handling most page scripts implement. It cannot perform a native HTML5 "
                    "draggable=true drag, and such a page reports no error, so verify the result in the "
                    "returned state rather than assuming the drop happened."
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
                name="browser_forward",
                description="Navigate the active tab forward and return the refreshed browser state.",
                input_schema=_schema({"browser_id": _browser_id_schema()}, ("browser_id",)),
                handler=go_forward,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_find",
                description=(
                    "Scroll to the first occurrence of visible text on the page, so content below the "
                    "fold can be reached and read without guessing scroll amounts. Returns the refreshed "
                    "state; compare it to confirm the text was actually found."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "text": {"type": "string", "minLength": 1, "maxLength": 500},
                        "direction": {"type": "string", "enum": ["up", "down"]},
                    },
                    ("browser_id", "text"),
                ),
                handler=find_text,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_dropdown_options",
                description=(
                    "List the options of a select element from the latest browser_state. browser_select "
                    "needs an option's exact text, which the serialized DOM does not carry, so read the "
                    "options before selecting instead of guessing from page copy."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "index": index_schema,
                        "state_revision": revision_schema,
                    },
                    ("browser_id", "index", "state_revision"),
                ),
                # Reads live page content, exactly like browser_state, so it is not
                # READ_ONLY: that effect skips approval, and Loom closes browser
                # sessions outright in read-only permission mode.
                handler=dropdown_options,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_upload",
                description=(
                    "Attach a workspace file to a file input on the page. The path is resolved inside the "
                    "Loom workspace; absolute paths and paths escaping the workspace are refused. This "
                    "sends the file's contents to the site that owns the page."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "index": index_schema,
                        "state_revision": revision_schema,
                        "path": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                    ("browser_id", "index", "state_revision", "path"),
                ),
                handler=upload_file,
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
