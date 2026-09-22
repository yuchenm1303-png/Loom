from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from .browser_session import BrowserSessionLimitError, BrowserTextNotFoundError
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


# The three ways the model's view of a page goes stale: the DOM node behind an
# index was replaced, the index is gone entirely, or the revision it held has
# been superseded. All three are the same situation to recover from.
_STALE_VIEW_MARKERS = (
    "element is no longer available",
    "element index is not available",
    "stale browser state_revision",
)


def _is_stale_view(error: Exception) -> bool:
    text = str(error).casefold()
    return any(marker in text for marker in _STALE_VIEW_MARKERS)


def _with_fresh_view(
    context: ToolContext,
    store: "BrowserSessionStore",
    browser_id: str,
    action: Callable[[], ToolResult],
) -> ToolResult:
    """Run an element action; when the view is stale, fail with the current page.

    A stale view used to come back as one sentence telling the model to call
    browser_state and retry. Measured on a real session, that cost three round
    trips - 35 seconds of wall clock for 0.55 seconds of actual browser work -
    and the model spent the first of them repeating the identical call, because
    nothing in the message distinguished "your indexes are old" from "that click
    did not land". Re-reading the page is the cheap part, so it happens here and
    travels back attached to the failure: the next turn can act instead of
    asking what changed.

    Still a failure, deliberately. Retrying the same index against a re-rendered
    page is how a click lands on the wrong element, so choosing again is the
    model's job.
    """

    try:
        return action()
    except Exception as exc:
        if not _is_stale_view(exc):
            raise
        try:
            snapshot = store.snapshot(context.session_id, browser_id, refresh=True)
        except Exception:
            # The re-read is a courtesy. If the page cannot be read at all the
            # original error is the honest one to report.
            raise exc from None
        return ToolResult(
            ok=False,
            content=(
                f"{exc} Loom has already re-read the page: this result carries the current state at "
                f"state_revision {snapshot.state_revision}. Choose the element again from the indexes "
                "below and send the next action with that revision. Do not repeat the previous call "
                "unchanged - the index it used no longer points at what you saw."
            ),
            data=snapshot.to_dict(),
        )


_MAX_EVAL_VALUE_CHARS = 30_000
_DOWNLOADS_DIR = "browser-downloads"
_SESSION_STATE_PATH = "browser-state/session.json"
# browser_type used to swap typed text for a one-shot reference with this prefix,
# which meant conversations carry references the model can read back and copy into
# the text argument. That is no longer resolved to anything, so without this guard
# the placeholder is typed into the page verbatim and reported as success.
_RETIRED_TRANSIENT_PREFIX = "loom-transient-browser-text:"
# CDP's Browser.PermissionType. Kept here so a typo fails with the valid names
# instead of reaching the browser, which rejects the whole grant on one bad name.
_CDP_PERMISSIONS = frozenset({
    "ar", "audioCapture", "automaticFullscreen", "backgroundFetch", "backgroundSync",
    "cameraPanTiltZoom", "capturedSurfaceControl", "clipboardReadWrite",
    "clipboardSanitizedWrite", "displayCapture", "durableStorage", "geolocation",
    "handTracking", "idleDetection", "keyboardLock", "localFonts", "localNetworkAccess",
    "midi", "midiSysex", "nfc", "notifications", "paymentHandler",
    "periodicBackgroundSync", "pointerLock", "protectedMediaIdentifier", "sensors",
    "smartCard", "speakerSelection", "storageAccess", "topLevelStorageAccess",
    "videoCapture", "vr", "wakeLockScreen", "wakeLockSystem", "webAppInstallation",
    "webPrinting", "windowManagement",
})


def _bounded_json_value(value: Any) -> tuple[Any, bool]:
    """Keep an arbitrary script result from flooding the model's context.

    A page can hand back a whole document or a multi-megabyte array, and the
    result goes straight into tool output, so an oversized value is replaced by a
    truncated rendering rather than trimmed in place: half a JSON structure would
    read as real data.
    """

    try:
        encoded = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return (str(value)[:_MAX_EVAL_VALUE_CHARS], True)
    if len(encoded) <= _MAX_EVAL_VALUE_CHARS:
        return (value, False)
    return (encoded[:_MAX_EVAL_VALUE_CHARS], True)


def _cookie_row(raw: Any, *, include_values: bool) -> dict[str, Any]:
    """Describe one cookie, withholding its value unless it was asked for.

    A cookie value is usually the session itself. Listing cookies to see what a
    site set is a different act from reading those credentials, so the value only
    appears when the caller says so.
    """

    item = dict(raw) if isinstance(raw, dict) else {}
    row: dict[str, Any] = {
        "name": str(item.get("name") or "")[:300],
        "domain": str(item.get("domain") or "")[:300],
        "path": str(item.get("path") or "")[:300],
        "secure": bool(item.get("secure")),
        "http_only": bool(item.get("httpOnly")),
        "same_site": str(item.get("sameSite") or ""),
        "expires": item.get("expires"),
        "value_chars": len(str(item.get("value") or "")),
    }
    if include_values:
        row["value"] = str(item.get("value") or "")[:4000]
    return row


def _validated_viewport(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("browser_emulate viewport must be an object")
    try:
        width = int(raw["width"])
        height = int(raw["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("browser_emulate viewport needs integer width and height") from exc
    if not (1 <= width <= 10_000 and 1 <= height <= 10_000):
        raise ValueError("browser_emulate viewport width and height must be within 1..10000")
    scale = float(raw.get("device_scale_factor") or 1.0)
    if not 0.1 <= scale <= 5.0:
        raise ValueError("browser_emulate device_scale_factor must be within 0.1..5")
    return {
        "width": width,
        "height": height,
        "device_scale_factor": scale,
        "mobile": bool(raw.get("mobile")),
    }


def _validated_geolocation(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("browser_emulate geolocation must be an object")
    try:
        latitude = float(raw["latitude"])
        longitude = float(raw["longitude"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("browser_emulate geolocation needs latitude and longitude") from exc
    if not -90.0 <= latitude <= 90.0:
        raise ValueError("browser_emulate latitude must be within -90..90")
    if not -180.0 <= longitude <= 180.0:
        raise ValueError("browser_emulate longitude must be within -180..180")
    accuracy = float(raw.get("accuracy") or 100.0)
    if accuracy < 0:
        raise ValueError("browser_emulate accuracy must not be negative")
    return {"latitude": latitude, "longitude": longitude, "accuracy": accuracy}


def _validated_permissions(raw: Any) -> list[str]:
    """Check permission names before sending them.

    CDP rejects the whole grant when one name is unknown, so a typo would
    silently leave every requested permission ungranted.
    """

    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("browser_emulate grant_permissions must be an array")
    names = [str(item or "").strip() for item in raw if str(item or "").strip()]
    if len(names) > 37:
        raise ValueError("browser_emulate grant_permissions has too many entries")
    unknown = [name for name in names if name not in _CDP_PERMISSIONS]
    if unknown:
        raise ValueError(
            "unknown browser permission(s): "
            + ", ".join(sorted(unknown))
            + ". Valid names include geolocation, notifications, camera, videoCapture, "
            "audioCapture, clipboardReadWrite, midi."
        )
    return names


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

        # Downloads have to land where the model can read them back, which is the
        # workspace and nowhere else. The backend is built inside store.start and
        # connects immediately, so the directory is attached through the factory
        # rather than set on the session afterwards.
        downloads_dir = context.resolve_workspace_path(_DOWNLOADS_DIR)
        downloads_dir.mkdir(parents=True, exist_ok=True)
        connection_factory = factory

        def factory_with_downloads(options):
            backend = connection_factory(options)
            if hasattr(backend, "downloads_dir"):
                backend.downloads_dir = str(downloads_dir)
            return backend

        factory = factory_with_downloads
        url = str(arguments.get("url") or "").strip()

        # Persistent-profile, CDP and extension modes intentionally expose only one
        # live browser slot. browser_open used to treat a second call from the same
        # Loom session as a request for a second browser, so an already-controlled,
        # healthy browser deterministically produced "browser session limit reached
        # (1)". Make the operation idempotent for that exclusive configured
        # connection: reuse the owned browser when its policy still matches, and
        # replace it when the requested policy/connection changed.
        recovered_stale = False
        replaced_owned = False
        owner_is_exclusive = int(getattr(store, "max_sessions_per_owner", 2)) == 1
        configured_connection = not connect and not requested_cdp
        owned = store.list(context.session_id) if owner_is_exclusive else ()

        def domains_match(row: dict[str, object]) -> bool:
            def signature(values) -> tuple[str, ...]:
                normalized = {
                    str(value or "").strip().casefold().rstrip(".")
                    for value in values
                    if str(value or "").strip()
                }
                return tuple(sorted(normalized))

            return signature(row.get("allowed_domains") or ()) == signature(allowed)

        if len(owned) == 1:
            row = owned[0]
            browser_id = str(row.get("browser_id") or "")
            compatible = (
                configured_connection
                and bool(row.get("headless")) == bool(runtime.browser_headless)
                and domains_match(row)
            )
            if compatible and browser_id:
                try:
                    snapshot = store.snapshot(context.session_id, browser_id, refresh=True)
                except Exception:
                    # The bookkeeping entry outlived its backend. Closing removes
                    # the stale lease even when backend.close itself fails, then the
                    # normal start path below recreates the connection once.
                    recovered_stale = True
                    try:
                        store.close(context.session_id, browser_id)
                    except Exception:
                        pass
                else:
                    if url:
                        store.navigate(context.session_id, browser_id, url, new_tab=False)
                        snapshot = store.snapshot(context.session_id, browser_id)
                    return _snapshot_result(
                        snapshot,
                        "Browser session resumed. Element indexes are valid only for the returned state_revision.",
                        extra={
                            "browser_connection": label,
                            "external_browser": bool(external),
                            "browser_session_reused": True,
                            "browser_session_recovered": False,
                        },
                    )
            elif browser_id:
                # A single-slot owner cannot open a differently-scoped connection
                # until its previous handle is released. Replacing our own handle is
                # safe; never steal a slot owned by another Loom session.
                replaced_owned = True
                try:
                    store.close(context.session_id, browser_id)
                except Exception:
                    pass

        try:
            managed = store.start(
                context.session_id,
                headless=runtime.browser_headless,
                allowed_domains=allowed,
                backend_factory=factory,
                external_browser=external,
            )
        except BrowserSessionLimitError as exc:
            # A concurrent browser_open may have finished between the lookup above
            # and start(). If it is now our compatible browser, coalesce onto it.
            if owner_is_exclusive and configured_connection:
                raced = store.list(context.session_id)
                if len(raced) == 1 and domains_match(raced[0]):
                    raced_id = str(raced[0].get("browser_id") or "")
                    if raced_id:
                        try:
                            snapshot = store.snapshot(context.session_id, raced_id, refresh=True)
                        except Exception:
                            pass
                        else:
                            if url:
                                store.navigate(context.session_id, raced_id, url, new_tab=False)
                                snapshot = store.snapshot(context.session_id, raced_id)
                            return _snapshot_result(
                                snapshot,
                                "Browser session resumed after a concurrent open.",
                                extra={
                                    "browser_connection": label,
                                    "external_browser": bool(external),
                                    "browser_session_reused": True,
                                    "browser_session_recovered": False,
                                },
                            )
            return ToolResult(
                ok=False,
                content=(
                    "Browser capacity is temporarily busy in another Loom task. "
                    "This is retryable and does not mean the browser task failed; "
                    "keep the turn alive and retry browser_open instead of stopping."
                ),
                data={
                    "reason": "browser_capacity_busy",
                    "retryable": True,
                    "scope": exc.scope,
                    "limit": exc.limit,
                    "active_sessions": store.active_count(),
                    "owned_sessions": len(store.list(context.session_id)),
                },
            )

        try:
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
            (
                "Browser session recovered and reopened. "
                if recovered_stale
                else "Browser session opened. "
            )
            + "Element indexes are valid only for the returned state_revision.",
            # Which browser this session drives changes what the model may assume
            # about the tabs it sees, so it is reported rather than inferred.
            extra={
                "browser_connection": label,
                "external_browser": bool(external),
                "browser_session_reused": False,
                "browser_session_recovered": bool(recovered_stale),
                "browser_session_replaced": bool(replaced_owned),
            },
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

        def run() -> ToolResult:
            store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
            store.click(context.session_id, browser_id, int(arguments["index"]))
            return _snapshot_result(store.snapshot(context.session_id, browser_id), "Browser click completed.")

        return _with_fresh_view(context, store, browser_id, run)

    def click_at(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        x = int(arguments["x"])
        y = int(arguments["y"])
        button = str(arguments.get("button") or "left").strip().casefold()
        if x < 0 or y < 0:
            raise ValueError("browser_click_at coordinates must be non-negative")
        if button not in {"left", "right", "middle"}:
            raise ValueError("browser_click_at button must be left, right, or middle")

        def run() -> ToolResult:
            store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
            item = store._owned(context.session_id, browser_id)
            info = item.last_state.page_info if isinstance(item.last_state.page_info, dict) else {}
            width = int(info.get("viewport_width") or 0)
            height = int(info.get("viewport_height") or 0)
            if width > 0 and height > 0 and (x >= width or y >= height):
                raise ValueError(
                    f"browser_click_at coordinates {x},{y} are outside the current viewport {width}x{height}"
                )
            snapshot = _backend_snapshot_action(
                store,
                context.session_id,
                browser_id,
                "click_at",
                x,
                y,
                button,
            )
            return _snapshot_result(
                snapshot,
                "Browser coordinate click completed. The returned state has a new revision; use it for focused input.",
            )

        return _with_fresh_view(context, store, browser_id, run)

    def type_text(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        text = str(arguments["text"])
        if text.startswith(_RETIRED_TRANSIENT_PREFIX):
            raise ValueError(
                "browser_type was given Loom's retired internal placeholder instead of text, so "
                "nothing was typed. Placeholders like this appear in earlier conversation history; "
                "they are not tool input and resolve to nothing. Send the literal characters to type."
            )
        if len(text) > 20_000:
            raise ValueError("browser_type text exceeds 20,000 characters")

        def run() -> ToolResult:
            store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
            store.type_text(
                context.session_id,
                browser_id,
                int(arguments["index"]),
                text,
                clear=bool(arguments.get("clear", True)),
            )
            return _snapshot_result(store.snapshot(context.session_id, browser_id), "Browser text input completed.")

        return _with_fresh_view(context, store, browser_id, run)

    def send_text(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        text = str(arguments["text"])
        if not text:
            raise ValueError("browser_send_text text must not be empty")
        if len(text) > 8000:
            raise ValueError("browser_send_text text exceeds 8000 characters")

        def run() -> ToolResult:
            store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
            snapshot = _backend_snapshot_action(
                store,
                context.session_id,
                browser_id,
                "send_text",
                text,
            )
            return _snapshot_result(
                snapshot,
                f"Sent {len(text)} character(s) to the currently focused browser surface in one action.",
            )

        return _with_fresh_view(context, store, browser_id, run)

    def hover(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])

        def run() -> ToolResult:
            store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
            snapshot = _backend_snapshot_action(
                store,
                context.session_id,
                browser_id,
                "hover",
                int(arguments["index"]),
            )
            return _snapshot_result(snapshot, "Browser hover completed.")

        return _with_fresh_view(context, store, browser_id, run)

    def press_key(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        key = str(arguments["key"] or "").strip()
        if not key:
            raise ValueError("browser_press key must not be empty")

        def run() -> ToolResult:
            store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
            snapshot = _backend_snapshot_action(
                store,
                context.session_id,
                browser_id,
                "press_key",
                key,
            )
            return _snapshot_result(snapshot, "Browser key press completed.")

        return _with_fresh_view(context, store, browser_id, run)

    def select_option(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        value = str(arguments["value"])

        def run() -> ToolResult:
            store.ensure_revision(context.session_id, browser_id, int(arguments["state_revision"]))
            snapshot = _backend_snapshot_action(
                store,
                context.session_id,
                browser_id,
                "select_option",
                int(arguments["index"]),
                value,
            )
            return _snapshot_result(snapshot, "Browser dropdown selection completed.")

        return _with_fresh_view(context, store, browser_id, run)

    def drag(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])

        def run() -> ToolResult:
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

        return _with_fresh_view(context, store, browser_id, run)

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
        """Resolve a tab from fresh state and recover expired extension handles.

        Browser and tab IDs are live handles, not durable identifiers. Models can
        retain them in conversation history across an App Server or extension
        restart, so blindly forwarding both made a healthy browser look
        disconnected. Refresh first, prefer an exact current ID, and allow a URL
        or title to re-identify the same page when Chrome assigned a new ID.
        """

        context.raise_if_cancelled()
        store = _store(runtime)
        requested_browser_id = str(arguments.get("browser_id") or "").strip()
        item = None
        if requested_browser_id:
            try:
                item = store._owned(context.session_id, requested_browser_id)
            except (KeyError, PermissionError):
                # A stale handle must never grant access to another task. It may
                # only fall back to a session already owned by this same task.
                item = None

        recovered_browser = False
        if item is None:
            owned = store.list(context.session_id)
            if len(owned) == 1:
                item = store._owned(context.session_id, str(owned[0]["browser_id"]))
                recovered_browser = bool(requested_browser_id and item.browser_id != requested_browser_id)
            elif not owned and bool(getattr(runtime, "browser_extension_attached", False)):
                status = runtime.browser_status(context.session_id).get("extension_bridge") or {}
                if not bool(status.get("connected")):
                    raise RuntimeError(
                        "the Loom browser extension is not connected; open or reload the installed extension"
                    )
                factory, external, _label = runtime.browser_session_connection("")
                downloads_dir = context.resolve_workspace_path(_DOWNLOADS_DIR)
                downloads_dir.mkdir(parents=True, exist_ok=True)

                def factory_with_downloads(options):
                    backend = factory(options)
                    if hasattr(backend, "downloads_dir"):
                        backend.downloads_dir = str(downloads_dir)
                    return backend

                item = store.start(
                    context.session_id,
                    headless=runtime.browser_headless,
                    allowed_domains=runtime.effective_allowed_domains(()),
                    backend_factory=factory_with_downloads,
                    external_browser=external,
                )
                recovered_browser = True
            elif len(owned) > 1:
                raise RuntimeError(
                    "browser_id is stale and this task owns multiple browser sessions; call browser_tabs "
                    "with the intended current browser_id"
                )
            else:
                raise RuntimeError("browser session is no longer available; call browser_open and retry")

        fresh = store.tabs(context.session_id, item.browser_id)
        requested_tab_id = str(arguments.get("tab_id") or "").strip()
        target_url = str(arguments.get("url") or "").strip()
        target_title = str(arguments.get("title") or "").strip()
        if not any((requested_tab_id, target_url, target_title)):
            raise ValueError("browser_switch_tab requires tab_id, url, or title")

        rows = [row for row in fresh.state.tabs if isinstance(row, dict)]
        matches = [row for row in rows if requested_tab_id and str(row.get("tab_id") or "") == requested_tab_id]
        resolution = "tab_id"
        if not matches and target_url:
            matches = [row for row in rows if str(row.get("url") or "") == target_url]
            resolution = "url"
        if not matches and target_title:
            wanted = target_title.casefold()
            matches = [row for row in rows if str(row.get("title") or "").strip().casefold() == wanted]
            resolution = "title"
            if not matches:
                matches = [row for row in rows if wanted in str(row.get("title") or "").casefold()]
                resolution = "title_contains"

        if len(matches) != 1:
            reason = "no current tab matches the requested target" if not matches else "the requested target matches multiple tabs"
            return ToolResult(
                ok=False,
                content=f"Browser tab was not switched: {reason}. Choose one tab_id from this freshly refreshed list.",
                data={
                    **fresh.to_dict(),
                    "switched": False,
                    "match_count": len(matches),
                    "browser_session_recovered": recovered_browser,
                },
            )

        resolved_tab_id = str(matches[0].get("tab_id") or "")
        snapshot = store.switch_tab(context.session_id, item.browser_id, resolved_tab_id)
        return _snapshot_result(
            snapshot,
            "Browser tab switched from a freshly refreshed cross-window tab list.",
            extra={
                "switched": True,
                "resolved_tab_id": resolved_tab_id,
                "resolution": resolution,
                "browser_session_recovered": recovered_browser,
            },
        )

    def close_tab(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        browser_id = str(arguments["browser_id"])
        snapshot = _store(runtime).close_tab(context.session_id, browser_id, str(arguments["tab_id"]))
        return _snapshot_result(snapshot, "Browser tab closed.")

    def evaluate(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        item = store._owned(context.session_id, browser_id)
        runner = getattr(item.backend, "evaluate", None)
        if not callable(runner):
            raise RuntimeError(
                f"browser backend {item.backend.backend_name!r} does not support evaluate"
            )
        expression = str(arguments["expression"])
        outcome = dict(
            runner(expression, await_promise=bool(arguments.get("await_promise", True)))
        )
        if not outcome.get("ok"):
            return ToolResult(
                ok=False,
                content=f"The page script raised: {outcome.get('error') or 'unknown error'}",
                data={"browser_id": browser_id, "error": outcome.get("error") or ""},
            )
        value, truncated = _bounded_json_value(outcome.get("value"))
        return ToolResult(
            ok=True,
            content=(
                "Script evaluated. The value is reported verbatim; nothing about the page was verified."
                + (" The value was truncated." if truncated else "")
            ),
            data={
                "browser_id": browser_id,
                "value": value,
                "value_type": str(outcome.get("value_type") or ""),
                "truncated": truncated,
            },
        )

    def wait(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        item = store._owned(context.session_id, browser_id)
        waiter = getattr(item.backend, "wait_for", None)
        if not callable(waiter):
            raise RuntimeError(
                f"browser backend {item.backend.backend_name!r} does not support waiting"
            )
        outcome = dict(
            waiter(
                seconds=float(arguments.get("seconds") or 0.0),
                for_text=str(arguments.get("for_text") or ""),
                until=str(arguments.get("until") or ""),
                timeout_seconds=float(arguments.get("timeout_seconds") or 15.0),
            )
        )
        snapshot = store.snapshot(context.session_id, browser_id, refresh=True)
        satisfied = bool(outcome.get("satisfied"))
        payload = {
            **snapshot.to_dict(),
            "satisfied": satisfied,
            "waited_ms": int(outcome.get("waited_ms") or 0),
        }
        if outcome.get("error"):
            payload["condition_error"] = str(outcome["error"])[:500]
        # A wait that times out is a fact about the page, not a broken call, but
        # it must not read as success either.
        return ToolResult(
            ok=satisfied,
            content=(
                f"Condition met after {payload['waited_ms']}ms."
                if satisfied
                else f"Still not satisfied after {payload['waited_ms']}ms; the returned state is current."
            ),
            data=payload,
        )

    def cookies(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        item = store._owned(context.session_id, browser_id)
        action = str(arguments.get("action") or "list").strip().casefold()
        if action == "clear":
            clear = getattr(item.backend, "clear_cookies", None)
            if not callable(clear):
                raise RuntimeError(
                    f"browser backend {item.backend.backend_name!r} does not support clearing cookies"
                )
            clear()
            return ToolResult(
                ok=True,
                content="Browser cookies cleared for this browser session.",
                data={"browser_id": browser_id, "action": "clear"},
            )
        reader = getattr(item.backend, "cookies", None)
        if not callable(reader):
            raise RuntimeError(
                f"browser backend {item.backend.backend_name!r} does not support reading cookies"
            )
        include_values = bool(arguments.get("include_values"))
        rows = [_cookie_row(raw, include_values=include_values) for raw in reader()][:500]
        return ToolResult(
            ok=True,
            content=(
                f"{len(rows)} cookie(s)."
                + ("" if include_values else " Values are withheld; pass include_values to read them.")
            ),
            data={"browser_id": browser_id, "action": "list", "cookies": rows,
                  "values_included": include_values},
        )

    def emulate(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        item = store._owned(context.session_id, browser_id)
        runner = getattr(item.backend, "emulate", None)
        if not callable(runner):
            raise RuntimeError(
                f"browser backend {item.backend.backend_name!r} does not support emulation"
            )

        viewport = _validated_viewport(arguments.get("viewport"))
        geolocation = _validated_geolocation(arguments.get("geolocation"))
        permissions = _validated_permissions(arguments.get("grant_permissions"))
        user_agent = str(arguments.get("user_agent") or "").strip()
        if len(user_agent) > 1000:
            raise ValueError("browser_emulate user_agent exceeds 1,000 characters")
        reset = bool(arguments.get("reset"))
        if not any((viewport, geolocation, permissions, user_agent, reset)):
            raise ValueError(
                "browser_emulate needs viewport, user_agent, geolocation, grant_permissions, or reset"
            )

        outcome = dict(
            runner(
                viewport=viewport,
                user_agent=user_agent,
                geolocation=geolocation,
                grant_permissions=permissions,
                reset=reset,
            )
        )
        snapshot = store.snapshot(context.session_id, browser_id, refresh=True)
        return ToolResult(
            ok=True,
            content=(
                "Applied: "
                + ", ".join(outcome.get("applied") or ["nothing"])
                + ". A page already loaded may not re-read these until it reloads."
            ),
            data={**snapshot.to_dict(), "applied": outcome.get("applied") or []},
        )

    def session_state(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        item = store._owned(context.session_id, browser_id)
        relative = str(arguments.get("path") or _SESSION_STATE_PATH)
        target = context.resolve_workspace_path(relative)
        action = str(arguments.get("action") or "save").strip().casefold()

        if action == "save":
            reader = getattr(item.backend, "storage_state", None)
            if not callable(reader):
                raise RuntimeError(
                    f"browser backend {item.backend.backend_name!r} does not expose session state"
                )
            state = dict(reader())
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            origins = [
                str(origin.get("origin") or "")
                for origin in state.get("origins") or []
                if origin.get("localStorage") or origin.get("sessionStorage")
            ]
            return ToolResult(
                ok=True,
                content=(
                    f"Saved {len(state.get('cookies') or [])} cookie(s) and {len(origins)} origin(s) "
                    f"of web storage to {relative}. The file holds live session credentials in plain "
                    "text inside the workspace."
                ),
                data={
                    "browser_id": browser_id,
                    "path": relative,
                    "cookies": len(state.get("cookies") or []),
                    "storage_origins": origins,
                },
            )

        if action != "load":
            raise ValueError("browser_session_state action must be save or load")
        if not target.is_file():
            raise ValueError(f"no saved browser session state at {relative}")
        try:
            saved = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"saved browser session state at {relative} is unreadable") from exc
        if not isinstance(saved, dict):
            raise ValueError(f"saved browser session state at {relative} is not an object")
        writer = getattr(item.backend, "set_cookies", None)
        if not callable(writer):
            raise RuntimeError(
                f"browser backend {item.backend.backend_name!r} cannot restore session state"
            )
        cookies = [dict(row) for row in (saved.get("cookies") or []) if isinstance(row, dict)]
        writer(cookies)
        pending = [
            str(origin.get("origin") or "")
            for origin in saved.get("origins") or []
            if isinstance(origin, dict) and (origin.get("localStorage") or origin.get("sessionStorage"))
        ]
        return ToolResult(
            ok=True,
            content=(
                f"Restored {len(cookies)} cookie(s). Web storage is not restored automatically, "
                "because writing it requires being on each origin: navigate there and set the keys "
                f"with browser_eval, reading the values from {relative}."
                if pending
                else f"Restored {len(cookies)} cookie(s)."
            ),
            data={
                "browser_id": browser_id,
                "path": relative,
                "cookies_restored": len(cookies),
                "storage_origins_not_restored": pending,
            },
        )

    def network(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        item = store._owned(context.session_id, browser_id)
        action = str(arguments.get("action") or "list").strip().casefold()

        def backend_call(name: str):
            method = getattr(item.backend, name, None)
            if not callable(method):
                raise RuntimeError(
                    f"browser backend {item.backend.backend_name!r} does not support network capture"
                )
            return method

        if action == "start":
            outcome = dict(backend_call("network_start")())
            return ToolResult(
                ok=True,
                content=(
                    "Network capture is on. Requests from now on are recorded; earlier ones were not."
                ),
                data={"browser_id": browser_id, **outcome},
            )
        if action == "stop":
            outcome = dict(backend_call("network_stop")())
            return ToolResult(
                ok=True,
                content="Network capture is off. Already recorded requests remain readable.",
                data={"browser_id": browser_id, **outcome},
            )
        if action == "clear":
            backend_call("network_clear")()
            return ToolResult(
                ok=True,
                content="Recorded requests discarded.",
                data={"browser_id": browser_id, "entries": 0},
            )
        if action == "body":
            request_id = str(arguments.get("request_id") or "").strip()
            if not request_id:
                raise ValueError("browser_network body requires request_id")
            outcome = dict(backend_call("network_body")(request_id))
            if not outcome.get("ok"):
                return ToolResult(
                    ok=False,
                    content=(
                        "That response body is no longer available. The browser keeps bodies only "
                        "briefly; read it closer to the request, or re-issue it."
                    ),
                    data={"browser_id": browser_id, "request_id": request_id,
                          "error": str(outcome.get("error") or "")},
                )
            return ToolResult(
                ok=True,
                content=(
                    "Response body returned verbatim."
                    + (" It was truncated." if outcome.get("truncated") else "")
                ),
                data={"browser_id": browser_id, "request_id": request_id, **outcome},
            )

        entries = list(backend_call("network_entries")())
        needle = str(arguments.get("url_contains") or "").strip().casefold()
        if needle:
            entries = [e for e in entries if needle in str(e.get("url") or "").casefold()]
        if bool(arguments.get("failures_only")):
            entries = [
                e
                for e in entries
                if e.get("error") or (e.get("status") is not None and int(e["status"]) >= 400)
            ]
        limit = max(1, min(int(arguments.get("limit") or 50), 200))
        entries = entries[-limit:]
        return ToolResult(
            ok=True,
            content=(
                f"{len(entries)} recorded request(s). Use action=body with a request_id to read one "
                "response. Nothing is recorded until action=start."
                if entries
                else "No recorded requests match. Nothing is recorded until action=start."
            ),
            data={"browser_id": browser_id, "requests": entries},
        )

    def downloads(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        item = store._owned(context.session_id, browser_id)
        reader = getattr(item.backend, "downloaded_files", None)
        if not callable(reader):
            raise RuntimeError(
                f"browser backend {item.backend.backend_name!r} does not report downloads"
            )
        root = context.resolve_workspace_path(_DOWNLOADS_DIR)
        rows: list[dict[str, Any]] = []
        for raw in list(reader())[:200]:
            path = Path(str(raw))
            try:
                relative = path.resolve().relative_to(root.resolve().parent)
            except (OSError, ValueError):
                # A download that escaped the workspace is reported by name only;
                # the model cannot read it and should not be handed the real path.
                rows.append({"name": path.name, "in_workspace": False})
                continue
            exists = path.is_file()
            rows.append(
                {
                    "name": path.name,
                    "path": relative.as_posix(),
                    "in_workspace": True,
                    "bytes": path.stat().st_size if exists else 0,
                    "exists": exists,
                }
            )
        return ToolResult(
            ok=True,
            content=(
                f"{len(rows)} file(s) downloaded in this browser session, under {_DOWNLOADS_DIR}/."
                if rows
                else "No files have been downloaded in this browser session."
            ),
            data={"browser_id": browser_id, "directory": _DOWNLOADS_DIR, "files": rows},
        )

    def storage(context: ToolContext, arguments: dict[str, Any]) -> ToolResult:
        context.raise_if_cancelled()
        store = _store(runtime)
        browser_id = str(arguments["browser_id"])
        item = store._owned(context.session_id, browser_id)
        reader = getattr(item.backend, "storage_origins", None)
        if not callable(reader):
            raise RuntimeError(
                f"browser backend {item.backend.backend_name!r} does not support storage inspection"
            )
        origins = [dict(row) for row in reader()][:200]
        return ToolResult(
            ok=True,
            content=(
                f"{len(origins)} origin(s) hold storage in this browser session. Read or write "
                "individual values with browser_eval against localStorage/sessionStorage."
            ),
            data={"browser_id": browser_id, "origins": origins},
        )

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

        def run() -> ToolResult:
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

        return _with_fresh_view(context, store, browser_id, run)

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
                    "Open or resume a Loom browser session, optionally navigate to an http/https URL, and return "
                    "a bounded LLM-facing DOM state. In single-browser modes, repeated calls from the same Loom session "
                    "reuse the healthy controlled browser automatically; a stale owned handle is closed and reopened once "
                    "instead of failing on the session limit. allowed_domains can restrict this browser session."
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
                description=("Refresh the current page and return bounded DOM/tabs plus a new state_revision. "
                    "Canvas/video/iframe/application regions are also reported in page_info.visual_surfaces with viewport rectangles."),
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
                name="browser_click_at",
                description=(
                    "Click viewport coordinates from the latest browser_state. Use this for canvas, WebGL, VNC/RDP/KVM, "
                    "maps, remote desktops, and other visual surfaces that do not expose a DOM element index. Coordinates "
                    "are CSS pixels relative to the visible page viewport and must come from the matching state/screenshot. "
                    "The action focuses the hit surface so browser_send_text or browser_press can drive it next."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "x": {"type": "integer", "minimum": 0, "maximum": 20000},
                        "y": {"type": "integer", "minimum": 0, "maximum": 20000},
                        "button": {"type": "string", "enum": ["left", "right", "middle"]},
                        "state_revision": revision_schema,
                    },
                    ("browser_id", "x", "y", "state_revision"),
                ),
                handler=click_at,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_type",
                description=(
                    "Type the exact string supplied in text into an element from the latest browser_state. Pass ordinary "
                    "text such as names, URLs, and form values directly; the text is handled as a normal tool argument "
                    "and entered as supplied. Browser v1 has no automatic credential store or secret injection channel."
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
                name="browser_send_text",
                description=(
                    "Send a whole string in one tool call to the browser surface that currently owns keyboard focus. "
                    "This is for canvas/WebGL remote terminals and desktops after browser_click_at has focused them; "
                    "use browser_type for ordinary input/textarea/contenteditable elements. Newline becomes Enter, Tab "
                    "and Backspace are preserved, and the entire string shares one state_revision instead of one revision per key."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "state_revision": revision_schema,
                        "text": {"type": "string", "minLength": 1, "maxLength": 8000},
                    },
                    ("browser_id", "state_revision", "text"),
                ),
                handler=send_text,
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
                    "This also drives the currently focused canvas/remote-desktop surface. Requires the latest "
                    "state_revision to avoid acting on stale focus state."
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
                name="browser_eval",
                description=(
                    "Run a JavaScript expression in the active tab and return its value. This is the "
                    "escape hatch for what the other browser tools cannot express: reading computed "
                    "state, driving a widget that ignores synthetic clicks, working with "
                    "localStorage/sessionStorage, or replacing window.confirm before an action so a "
                    "native dialog does not get auto-answered. It runs with the page's own privileges "
                    "on whatever site the tab is on, including one the user is signed into. Large "
                    "values are truncated, and a thrown error is returned rather than raised."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "expression": {"type": "string", "minLength": 1, "maxLength": 20000},
                        "await_promise": {
                            "type": "boolean",
                            "description": "Await a returned promise before reporting. Defaults to true.",
                        },
                    },
                    ("browser_id", "expression"),
                ),
                handler=evaluate,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_wait",
                description=(
                    "Wait for the page to reach a condition instead of re-reading the whole state in a "
                    "loop. Give until with a JavaScript expression that becomes truthy (the general "
                    "form, e.g. a spinner disappearing or a row count settling), for_text to wait for "
                    "visible text to appear, or seconds for a plain settle. Returns ok=false when the "
                    "condition never held, with the current state attached either way."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "until": {"type": "string", "maxLength": 20000},
                        "for_text": {"type": "string", "maxLength": 500},
                        "seconds": {"type": "number", "minimum": 0, "maximum": 60},
                        "timeout_seconds": {"type": "number", "minimum": 0.5, "maximum": 60},
                    },
                    ("browser_id",),
                ),
                handler=wait,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_cookies",
                description=(
                    "List or clear the cookies of this browser session. Values are withheld unless "
                    "include_values is set, because a cookie value is usually the signed-in session "
                    "itself. Clearing signs the browser out of the affected sites."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "action": {"type": "string", "enum": ["list", "clear"]},
                        "include_values": {"type": "boolean"},
                    },
                    ("browser_id",),
                ),
                handler=cookies,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_emulate",
                description=(
                    "Change what the page believes about its device and what it is allowed to do: "
                    "viewport size and mobile flag for responsive layouts, user_agent for sites that "
                    "gate on it, geolocation for sites that ask where you are, and grant_permissions "
                    "to pre-approve prompts such as geolocation or notifications that would otherwise "
                    "block. A page already loaded may not re-read these until it reloads. reset "
                    "clears every override, including the viewport the browser backend set when it "
                    "connected, so the page ends up at the real window size rather than back at "
                    "whatever it reported before."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "viewport": {
                            "type": "object",
                            "properties": {
                                "width": {"type": "integer", "minimum": 1, "maximum": 10000},
                                "height": {"type": "integer", "minimum": 1, "maximum": 10000},
                                "device_scale_factor": {"type": "number", "minimum": 0.1, "maximum": 5},
                                "mobile": {"type": "boolean"},
                            },
                            "required": ["width", "height"],
                            "additionalProperties": False,
                        },
                        "user_agent": {"type": "string", "maxLength": 1000},
                        "geolocation": {
                            "type": "object",
                            "properties": {
                                "latitude": {"type": "number", "minimum": -90, "maximum": 90},
                                "longitude": {"type": "number", "minimum": -180, "maximum": 180},
                                "accuracy": {"type": "number", "minimum": 0},
                            },
                            "required": ["latitude", "longitude"],
                            "additionalProperties": False,
                        },
                        "grant_permissions": {
                            "type": "array",
                            "items": {"type": "string", "maxLength": 60},
                            "maxItems": 37,
                            "description": (
                                "CDP permission names, e.g. geolocation, notifications, camera, "
                                "videoCapture, audioCapture, clipboardReadWrite. This replaces the "
                                "whole grant set rather than adding to it."
                            ),
                        },
                        "reset": {"type": "boolean"},
                    },
                    ("browser_id",),
                ),
                handler=emulate,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_session_state",
                description=(
                    "Save this browser session's cookies and web-storage inventory to a workspace "
                    "file, or restore the cookies from one. This is how a sign-in survives closing "
                    "and reopening the browser. The saved file holds live session credentials in "
                    "plain text. Loading restores cookies only: web storage cannot be written without "
                    "being on its origin, so navigate there and set the keys with browser_eval, "
                    "reading them from the saved file."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "action": {"type": "string", "enum": ["save", "load"]},
                        "path": {
                            "type": "string",
                            "maxLength": 1000,
                            "description": (
                                f"Workspace-relative file. Defaults to {_SESSION_STATE_PATH}."
                            ),
                        },
                    },
                    ("browser_id", "action"),
                ),
                handler=session_state,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_network",
                description=(
                    "Inspect the requests a page makes. Call action=start first, since nothing is "
                    "recorded until then and earlier requests cannot be recovered; then action=list to "
                    "see method, URL, status and size, optionally filtered by url_contains or "
                    "failures_only; then action=body with a request_id to read one response. This is "
                    "how to tell a failing API call from a rendering problem. The browser keeps "
                    "response bodies only briefly, so read one soon after the request."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "action": {
                            "type": "string",
                            "enum": ["start", "stop", "list", "body", "clear"],
                        },
                        "request_id": {"type": "string", "maxLength": 200},
                        "url_contains": {"type": "string", "maxLength": 500},
                        "failures_only": {"type": "boolean"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                    },
                    ("browser_id",),
                ),
                handler=network,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_downloads",
                description=(
                    "List files this browser session downloaded, and only those: files the user "
                    "downloaded before it started are not reported. Loom's own browser writes into "
                    f"the {_DOWNLOADS_DIR}/ directory of the workspace, so those can then be read "
                    "with the ordinary file tools. A download the user's browser put elsewhere is "
                    "reported by name without a path, and cannot be read."
                ),
                input_schema=_schema({"browser_id": _browser_id_schema()}, ("browser_id",)),
                handler=downloads,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_storage",
                description=(
                    "List the origins that hold local/session storage in this browser session. Read or "
                    "write individual keys with browser_eval against localStorage or sessionStorage."
                ),
                input_schema=_schema({"browser_id": _browser_id_schema()}, ("browser_id",)),
                handler=storage,
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
                description="List tabs across all open browser windows with opaque tab and window IDs, URLs, titles, and active-window status.",
                input_schema=_schema({"browser_id": _browser_id_schema()}, ("browser_id",)),
                handler=tabs,
                effect=sensitive,
            ),
            AgentTool(
                name="browser_switch_tab",
                description=(
                    "Switch to and focus a tab in any open browser window. The tool refreshes the complete tab list "
                    "before switching, recovers an expired current-browser session when safe, and can re-identify a "
                    "tab by exact URL or unique title if its tab ID changed. Pass the latest tab_id when available; "
                    "also pass url or title for restart-safe recovery."
                ),
                input_schema=_schema(
                    {
                        "browser_id": _browser_id_schema(),
                        "tab_id": {"type": "string", "minLength": 1, "maxLength": 128},
                        "url": {"type": "string", "minLength": 1, "maxLength": 4000},
                        "title": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
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
