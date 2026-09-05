from __future__ import annotations

from typing import Any

from .browser_session import BrowserError, BrowserLaunchOptions, BrowserPageState
from .browser_use_backend import BrowserUseBackend, _serialize_state


class BrowserUseSessionBackend(BrowserUseBackend):
    """browser-use BrowserSession adapter with snapshot-scoped element identity.

    browser-use exposes integer selector indexes for the current serialized DOM.
    Loom caches the exact selector map that produced the latest model-visible state
    and never re-resolves an index against a newer DOM before click/type. A stale
    node therefore fails instead of silently targeting a different element.
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
