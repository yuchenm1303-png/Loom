from __future__ import annotations

from typing import Any

from .browser_session import BrowserError, BrowserLaunchOptions, BrowserPageState
from .browser_use_backend import BrowserUseBackend, _serialize_state


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
        return _serialize_state(state)

    async def _node_for_index(self, index: int):
        node = self._selector_map.get(int(index))
        if node is None:
            raise BrowserError(
                f"browser element index {index} is unavailable in the latest state snapshot; "
                "call browser_state and retry with the returned state_revision"
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
            raise BrowserError(f"browser element index {index} has no backend node identity")
        element_session = await session.cdp_client_for_node(node)
        from browser_use.actor.element import Element

        return Element(session, int(backend_node_id), element_session.session_id)

    async def _hover_async(self, index: int) -> BrowserPageState:
        element = await self._actor_element_for_index(index)
        await element.hover()
        return await self._state_async()

    def hover(self, index: int) -> BrowserPageState:
        return self._runner.run(self._hover_async(index), timeout=self.action_timeout_seconds)

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
            raise BrowserError("browser has no active page for keyboard input")
        await page.press(value)
        return await self._state_async()

    def press_key(self, key: str) -> BrowserPageState:
        return self._runner.run(self._press_key_async(key), timeout=self.action_timeout_seconds)

    async def _select_option_async(self, index: int, value: str) -> BrowserPageState:
        option = str(value)
        if not option:
            raise ValueError("browser select value must not be empty")
        if len(option) > 2_000:
            raise ValueError("browser select value exceeds 2,000 characters")
        element = await self._actor_element_for_index(index)
        await element.select_option(option)
        return await self._state_async()

    def select_option(self, index: int, value: str) -> BrowserPageState:
        return self._runner.run(
            self._select_option_async(index, value),
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
        return self._runner.run(
            self._drag_async(source_index, target_index),
            timeout=self.action_timeout_seconds,
        )

    async def _refresh_async(self) -> BrowserPageState:
        from browser_use.browser.events import RefreshEvent

        await self._dispatch(RefreshEvent())
        return await self._state_async()

    def refresh(self) -> BrowserPageState:
        return self._runner.run(self._refresh_async(), timeout=self.action_timeout_seconds)

    def tabs(self) -> BrowserPageState:
        return self.state()

    def _full_tab_id(self, tab_id: str) -> str:
        key = str(tab_id or "").strip()
        if not key:
            raise ValueError("browser tab_id must not be empty")
        full = self._tab_map.get(key)
        if full is not None:
            return full
        matches = [target for short, target in self._tab_map.items() if short.endswith(key) or target.endswith(key)]
        if len(matches) == 1:
            return matches[0]
        raise BrowserError("browser tab_id is unavailable in the latest tab snapshot; call browser_tabs again")

    async def _switch_tab_async(self, tab_id: str) -> BrowserPageState:
        from browser_use.browser.events import SwitchTabEvent

        await self._dispatch(SwitchTabEvent(target_id=self._full_tab_id(tab_id)))
        return await self._state_async()

    def switch_tab(self, tab_id: str) -> BrowserPageState:
        return self._runner.run(self._switch_tab_async(tab_id), timeout=self.action_timeout_seconds)

    async def _close_tab_async(self, tab_id: str) -> BrowserPageState:
        from browser_use.browser.events import CloseTabEvent

        await self._dispatch(CloseTabEvent(target_id=self._full_tab_id(tab_id)))
        return await self._state_async()

    def close_tab(self, tab_id: str) -> BrowserPageState:
        return self._runner.run(self._close_tab_async(tab_id), timeout=self.action_timeout_seconds)


def browser_use_session_backend_factory(options: BrowserLaunchOptions) -> BrowserUseSessionBackend:
    return BrowserUseSessionBackend(options=options)


__all__ = ["BrowserUseSessionBackend", "browser_use_session_backend_factory"]
