from __future__ import annotations

import json
from typing import Any

from .browser_session import BrowserError, BrowserLaunchOptions, BrowserPageState
from .browser_use_backend import BrowserUseBackend, _serialize_state


_MAX_DROPDOWN_OPTIONS = 300


def _dropdown_selection_succeeded(outcome: Any) -> bool:
    """Read the dropdown watchdog's own verdict.

    It answers with a mapping whose ``success`` is the string "true" rather than a
    boolean, so this cannot just be truth-tested: every non-empty string is
    truthy, including "false".
    """

    if outcome is None:
        return False
    if isinstance(outcome, bool):
        return outcome
    if isinstance(outcome, dict):
        raw = outcome.get("success")
        if isinstance(raw, bool):
            return raw
        if raw is None:
            # Older shapes report only a message; treat a reported value as proof.
            return bool(str(outcome.get("value") or "").strip())
        return str(raw).strip().casefold() in {"true", "1", "yes", "ok"}
    return True


def _normalize_dropdown_options(raw: Any) -> list[dict[str, Any]]:
    """Reduce a provider dropdown payload to bounded {text, value, selected} rows.

    browser-use returns whatever its watchdog produced, which varies by version
    and can carry the whole option element. Only what browser_select needs to be
    called with is kept, and the list is capped so a pathological page cannot
    push an unbounded payload into the model's context.
    """

    items: Any = raw
    if isinstance(raw, dict):
        for key in ("options", "dropdown_options", "values", "result"):
            candidate = raw.get(key)
            # browser-use hands back this list already serialized to JSON, beside
            # prose fields that name its own tools. Only the structured rows are
            # useful here, and its prose must not reach the model.
            if isinstance(candidate, str) and candidate.strip().startswith("["):
                try:
                    candidate = json.loads(candidate)
                except json.JSONDecodeError:
                    candidate = None
            if isinstance(candidate, (list, tuple)):
                items = candidate
                break
    if not isinstance(items, (list, tuple)):
        return []

    options: list[dict[str, Any]] = []
    for item in items[:_MAX_DROPDOWN_OPTIONS]:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("label") or item.get("value") or "")
            value = str(item.get("value") or "")
            selected = bool(item.get("selected"))
        elif isinstance(item, str):
            text, value, selected = item, "", False
        else:
            text = str(getattr(item, "text", "") or getattr(item, "label", "") or "")
            value = str(getattr(item, "value", "") or "")
            selected = bool(getattr(item, "selected", False))
        text = text.strip()[:300]
        if not text and not value:
            continue
        options.append({"text": text, "value": value.strip()[:300], "selected": selected})
    return options


class BrowserUseSessionBackend(BrowserUseBackend):
    """browser-use BrowserSession adapter with snapshot-scoped element identity.

    browser-use exposes integer selector indexes for the current serialized DOM.
    Loom caches the exact selector map that produced the latest model-visible state
    and never re-resolves an index against a newer DOM before click/type/hover/select/drag.
    A stale node therefore fails instead of silently targeting a different element.
    """

    def __init__(self, options: BrowserLaunchOptions, action_timeout_seconds: float = 60.0) -> None:
        super().__init__(options=options, action_timeout_seconds=action_timeout_seconds)
        self._selector_map: dict[int, Any] = {}
        self._tab_map: dict[str, str] = {}
        self._state_revision = 0
        self._log("browser_use.snapshot_backend.created")

    @property
    def state_revision(self) -> int:
        return self._state_revision

    async def _state_async(self) -> BrowserPageState:
        session = await self._ensure_session()
        state = await session.get_browser_state_summary(include_screenshot=False)
        dom_state = getattr(state, "dom_state", None)
        self._selector_map = dict(getattr(dom_state, "selector_map", {}) or {})
        self._tab_map = {}
        for tab in getattr(state, "tabs", ()) or ():
            target_id = str(getattr(tab, "target_id", ""))
            if target_id:
                short = target_id[-12:]
                self._tab_map[short] = target_id
        self._state_revision += 1
        serialized = self._with_backend_page_info(
            _serialize_state(state),
            capture_mode="browser_use_snapshot",
            selector_count=len(self._selector_map),
        )
        self._log(
            "browser_use.selector_snapshot.captured",
            state_revision=self._state_revision,
            selector_count=len(self._selector_map),
            tab_count=len(serialized.tabs),
            state=self._state_summary(serialized),
        )
        return serialized

    async def _node_for_index(self, index: int):
        key = int(index)
        self._log(
            "browser_use.snapshot_node.lookup.started",
            index=key,
            state_revision=self._state_revision,
            selector_count=len(self._selector_map),
        )
        node = self._selector_map.get(key)
        if node is None:
            self._log(
                "browser_use.snapshot_node.lookup.missing",
                index=key,
                state_revision=self._state_revision,
                selector_count=len(self._selector_map),
            )
            raise BrowserError(
                f"browser element index {index} is unavailable in the latest state snapshot; "
                "call browser_state and retry with the returned state_revision"
            )
        self._log(
            "browser_use.snapshot_node.lookup.completed",
            index=key,
            state_revision=self._state_revision,
            backend_node_id_present=getattr(node, "backend_node_id", None) is not None,
        )
        return node

    async def _actor_element_for_index(self, index: int):
        """Resolve a cached snapshot node to browser-use's low-level Actor Element.

        The selector map remains snapshot-scoped. We intentionally do not ask
        browser-use for a fresh selector map here, otherwise the same numeric index
        could silently refer to a different element after a DOM mutation.
        """

        session = await self._ensure_session()
        node = await self._node_for_index(index)
        backend_node_id = getattr(node, "backend_node_id", None)
        if backend_node_id is None:
            self._log("browser_use.actor_element.lookup.missing_backend_node", index=int(index), state_revision=self._state_revision)
            raise BrowserError(f"browser element index {index} has no backend node identity")
        element_session = await session.cdp_client_for_node(node)
        from browser_use.actor.element import Element

        self._log("browser_use.actor_element.lookup.completed", index=int(index), state_revision=self._state_revision)
        return Element(session, int(backend_node_id), element_session.session_id)

    async def _hover_async(self, index: int) -> BrowserPageState:
        element = await self._actor_element_for_index(index)
        await element.hover()
        return await self._state_async()

    def hover(self, index: int) -> BrowserPageState:
        return self._run_state_action("hover", self._hover_async(index), args={"index": int(index)})

    async def _press_key_async(self, key: str) -> BrowserPageState:
        value = str(key or "").strip()
        if not value:
            raise ValueError("browser key must not be empty")
        if len(value) > 100:
            raise ValueError("browser key exceeds 100 characters")
        if value == "Ctrl":
            value = "Control"
        elif value.startswith("Ctrl+"):
            value = "Control+" + value[5:]
        session = await self._ensure_session()
        page = await session.get_current_page()
        if page is None:
            self._log("browser_use.keyboard.no_active_page", key=value)
            raise BrowserError("browser has no active page for keyboard input")
        await page.press(value)
        return await self._state_async()

    def press_key(self, key: str) -> BrowserPageState:
        return self._run_state_action("press_key", self._press_key_async(key), args={"key": str(key or "")})

    async def _select_option_async(self, index: int, value: str) -> BrowserPageState:
        """Pick an option on a select.

        This used to go through the actor Element's select_option, which returned
        without error and without selecting anything: the page value never moved
        and no change event fired, for option text and option value alike. The
        dropdown event is the path browser-use actually implements, and it
        reports which option it matched, so a miss can be raised instead of
        silently reported as done.
        """

        from browser_use.browser.events import SelectDropdownOptionEvent

        option = str(value)
        if not option:
            raise ValueError("browser select value must not be empty")
        if len(option) > 2_000:
            raise ValueError("browser select value exceeds 2,000 characters")
        session = await self._ensure_session()
        node = await self._node_for_index(index)
        dispatched = session.event_bus.dispatch(SelectDropdownOptionEvent(node=node, text=option))
        await dispatched
        outcome = await dispatched.event_result(raise_if_any=True, raise_if_none=False)
        if not _dropdown_selection_succeeded(outcome):
            raise BrowserError(
                f"browser select did not match an option for {option!r}; "
                "read the exact option text with browser_dropdown_options first"
            )
        return await self._state_async()

    def select_option(self, index: int, value: str) -> BrowserPageState:
        return self._run_state_action(
            "select_option",
            self._select_option_async(index, value),
            args={"index": int(index), "value": str(value)},
        )

    async def _upload_file_async(self, index: int, file_path: str) -> BrowserPageState:
        from browser_use.browser.events import UploadFileEvent

        node = await self._node_for_index(index)
        await self._dispatch(UploadFileEvent(node=node, file_path=file_path))
        return await self._state_async()

    def upload_file(self, index: int, file_path: str) -> BrowserPageState:
        # The path is resolved and confined to the workspace by the tool layer;
        # keep it out of diagnostics because it names a real user file.
        return self._run_state_action(
            "upload_file",
            self._upload_file_async(index, file_path),
            args={"index": int(index)},
            include_dom_excerpt=False,
        )

    async def _dropdown_options_async(self, index: int) -> list[dict[str, Any]]:
        from browser_use.browser.events import GetDropdownOptionsEvent

        session = await self._ensure_session()
        node = await self._node_for_index(index)
        dispatched = session.event_bus.dispatch(GetDropdownOptionsEvent(node=node))
        await dispatched
        raw = await dispatched.event_result(raise_if_any=True, raise_if_none=False)
        return _normalize_dropdown_options(raw)

    def dropdown_options(self, index: int) -> list[dict[str, Any]]:
        """Read a select's options.

        browser_select needs an option's exact text, which the serialized DOM does
        not carry, so without this the model has to guess it from the page copy.
        """

        return self._runner.run(
            self._dropdown_options_async(index),
            timeout=self.action_timeout_seconds,
        )

    async def _drag_async(self, source_index: int, target_index: int) -> BrowserPageState:
        if int(source_index) == int(target_index):
            raise ValueError("browser drag source and target must be different elements")
        source = await self._actor_element_for_index(source_index)
        target = await self._actor_element_for_index(target_index)
        await source.drag_to(target)
        return await self._state_async()

    def drag(self, source_index: int, target_index: int) -> BrowserPageState:
        return self._run_state_action(
            "drag",
            self._drag_async(source_index, target_index),
            args={"source_index": int(source_index), "target_index": int(target_index)},
        )

    async def _refresh_async(self) -> BrowserPageState:
        from browser_use.browser.events import RefreshEvent

        await self._dispatch(RefreshEvent())
        return await self._state_async()

    def refresh(self) -> BrowserPageState:
        return self._run_state_action("refresh", self._refresh_async())

    def tabs(self) -> BrowserPageState:
        return self.state()

    def _full_tab_id(self, tab_id: str) -> str:
        key = str(tab_id or "").strip()
        if not key:
            raise ValueError("browser tab_id must not be empty")
        full = self._tab_map.get(key)
        if full is not None:
            self._log("browser_use.tab.resolve.completed", requested=key, matched=full[-12:])
            return full
        matches = [target for short, target in self._tab_map.items() if short.endswith(key) or target.endswith(key)]
        if len(matches) == 1:
            self._log("browser_use.tab.resolve.completed", requested=key, matched=matches[0][-12:])
            return matches[0]
        self._log("browser_use.tab.resolve.failed", requested=key, known_tabs=list(self._tab_map.keys()))
        raise BrowserError("browser tab_id is unavailable in the latest tab snapshot; call browser_tabs again")

    async def _switch_tab_async(self, tab_id: str) -> BrowserPageState:
        from browser_use.browser.events import SwitchTabEvent

        await self._dispatch(SwitchTabEvent(target_id=self._full_tab_id(tab_id)))
        return await self._state_async()

    def switch_tab(self, tab_id: str) -> BrowserPageState:
        return self._run_state_action("switch_tab", self._switch_tab_async(tab_id), args={"tab_id": str(tab_id)})

    async def _close_tab_async(self, tab_id: str) -> BrowserPageState:
        from browser_use.browser.events import CloseTabEvent

        await self._dispatch(CloseTabEvent(target_id=self._full_tab_id(tab_id)))
        return await self._state_async()

    def close_tab(self, tab_id: str) -> BrowserPageState:
        return self._run_state_action("close_tab", self._close_tab_async(tab_id), args={"tab_id": str(tab_id)})


def browser_use_session_backend_factory(options: BrowserLaunchOptions) -> BrowserUseSessionBackend:
    return BrowserUseSessionBackend(options=options)


__all__ = ["BrowserUseSessionBackend", "browser_use_session_backend_factory"]
