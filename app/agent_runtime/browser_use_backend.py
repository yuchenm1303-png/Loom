from __future__ import annotations

import asyncio
import importlib.util
import os
import threading
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any, Coroutine

from .browser_session import (
    BrowserBackend,
    BrowserError,
    BrowserLaunchOptions,
    BrowserPageState,
    BrowserUnavailableError,
)


_DEFAULT_ACTION_TIMEOUT = 60.0


class _AsyncLoopThread:
    def __init__(self, *, name: str) -> None:
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread = threading.Thread(target=self._run_loop, name=name, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("browser async loop failed to start")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()

    def run(self, coroutine: Coroutine[Any, Any, Any], *, timeout: float = _DEFAULT_ACTION_TIMEOUT):
        loop = self._loop
        if loop is None or loop.is_closed():
            coroutine.close()
            raise RuntimeError("browser async loop is closed")
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        try:
            return future.result(timeout=max(1.0, float(timeout)))
        except FutureTimeoutError as exc:
            future.cancel()
            raise BrowserError("browser action timed out") from exc

    def close(self) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=5.0)


@dataclass(slots=True)
class BrowserUseBackend(BrowserBackend):
    options: BrowserLaunchOptions
    action_timeout_seconds: float = _DEFAULT_ACTION_TIMEOUT

    def __post_init__(self) -> None:
        if importlib.util.find_spec("browser_use") is None:
            raise BrowserUnavailableError(
                "browser-use is not installed; install Loom with the browser extra"
            )
        self._runner = _AsyncLoopThread(name="loom-browser-use")
        self._session: Any | None = None
        self._closed = False

    @property
    def backend_name(self) -> str:
        return "browser-use"

    async def _ensure_session(self):
        if self._session is not None:
            return self._session
        # browser-use configures its own logging at import time unless disabled.
        os.environ.setdefault("BROWSER_USE_SETUP_LOGGING", "false")
        from browser_use import BrowserProfile, BrowserSession

        profile = BrowserProfile(
            headless=self.options.headless,
            allowed_domains=list(self.options.allowed_domains) or None,
            prohibited_domains=[
                "localhost",
                "*.localhost",
                "metadata.google.internal",
                "host.docker.internal",
                "gateway.docker.internal",
            ],
            block_ip_addresses=True,
            enable_default_extensions=False,
            user_data_dir=None,
            keep_alive=False,
        )
        self._session = BrowserSession(browser_profile=profile)
        return self._session

    async def _start_async(self) -> BrowserPageState:
        session = await self._ensure_session()
        await session.start()
        return await self._state_async()

    def start(self) -> BrowserPageState:
        try:
            return self._runner.run(self._start_async(), timeout=self.action_timeout_seconds)
        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserUnavailableError(f"browser-use failed to start: {type(exc).__name__}: {exc}") from exc

    async def _state_async(self) -> BrowserPageState:
        session = await self._ensure_session()
        state = await session.get_browser_state_summary(include_screenshot=False)
        return _serialize_state(state)

    def state(self) -> BrowserPageState:
        return self._runner.run(self._state_async(), timeout=self.action_timeout_seconds)

    async def _dispatch(self, event) -> None:
        session = await self._ensure_session()
        dispatched = session.event_bus.dispatch(event)
        await dispatched
        await dispatched.event_result(raise_if_any=True, raise_if_none=False)

    async def _navigate_async(self, url: str, *, new_tab: bool) -> BrowserPageState:
        from browser_use.browser.events import NavigateToUrlEvent

        await self._dispatch(NavigateToUrlEvent(url=url, new_tab=new_tab))
        return await self._state_async()

    def navigate(self, url: str, *, new_tab: bool = False) -> BrowserPageState:
        return self._runner.run(
            self._navigate_async(url, new_tab=new_tab),
            timeout=self.action_timeout_seconds,
        )

    async def _node_for_index(self, index: int):
        session = await self._ensure_session()
        state = await session.get_browser_state_summary(include_screenshot=False)
        node = state.dom_state.selector_map.get(int(index))
        if node is None:
            raise BrowserError(
                f"browser element index {index} is unavailable; refresh browser state before retrying"
            )
        return node

    async def _click_async(self, index: int) -> BrowserPageState:
        from browser_use.browser.events import ClickElementEvent

        node = await self._node_for_index(index)
        await self._dispatch(ClickElementEvent(node=node))
        return await self._state_async()

    def click(self, index: int) -> BrowserPageState:
        return self._runner.run(self._click_async(index), timeout=self.action_timeout_seconds)

    async def _type_async(self, index: int, text: str, *, clear: bool) -> BrowserPageState:
        from browser_use.browser.events import TypeTextEvent

        node = await self._node_for_index(index)
        await self._dispatch(TypeTextEvent(node=node, text=text, clear=clear))
        return await self._state_async()

    def type_text(self, index: int, text: str, *, clear: bool = True) -> BrowserPageState:
        return self._runner.run(
            self._type_async(index, text, clear=clear),
            timeout=self.action_timeout_seconds,
        )

    async def _scroll_async(self, direction: str, amount: int) -> BrowserPageState:
        from browser_use.browser.events import ScrollEvent

        await self._dispatch(ScrollEvent(direction=direction, amount=amount, node=None))
        return await self._state_async()

    def scroll(self, direction: str, amount: int) -> BrowserPageState:
        return self._runner.run(
            self._scroll_async(direction, amount),
            timeout=self.action_timeout_seconds,
        )

    async def _back_async(self) -> BrowserPageState:
        from browser_use.browser.events import GoBackEvent

        await self._dispatch(GoBackEvent())
        return await self._state_async()

    def go_back(self) -> BrowserPageState:
        return self._runner.run(self._back_async(), timeout=self.action_timeout_seconds)

    async def _screenshot_async(self, *, full_page: bool) -> bytes:
        session = await self._ensure_session()
        data = await session.take_screenshot(full_page=full_page)
        return bytes(data)

    def screenshot(self, *, full_page: bool = False) -> bytes:
        return self._runner.run(
            self._screenshot_async(full_page=full_page),
            timeout=self.action_timeout_seconds,
        )

    async def _close_async(self) -> None:
        if self._session is not None:
            try:
                await self._session.kill()
            finally:
                self._session = None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._runner.run(self._close_async(), timeout=20.0)
        except Exception:
            pass
        finally:
            self._runner.close()


def _serialize_state(state: Any) -> BrowserPageState:
    dom_state = getattr(state, "dom_state", None)
    try:
        dom = str(dom_state.llm_representation()) if dom_state is not None else ""
    except Exception as exc:
        dom = f"DOM unavailable: {type(exc).__name__}: {exc}"
    if len(dom) > 120_000:
        dom = dom[:120_000] + "\n...[DOM truncated by browser backend]"

    tabs: list[dict[str, str]] = []
    for tab in getattr(state, "tabs", ()) or ():
        target_id = getattr(tab, "target_id", "")
        tabs.append(
            {
                "url": str(getattr(tab, "url", ""))[:4000],
                "title": str(getattr(tab, "title", ""))[:1000],
                "tab_id": str(target_id)[-12:],
            }
        )

    page_info_obj = getattr(state, "page_info", None)
    page_info = None
    if page_info_obj is not None:
        if hasattr(page_info_obj, "model_dump"):
            page_info = dict(page_info_obj.model_dump())
        elif hasattr(page_info_obj, "dict"):
            page_info = dict(page_info_obj.dict())

    errors = tuple(str(item)[:2000] for item in (getattr(state, "browser_errors", ()) or ()))
    return BrowserPageState(
        url=str(getattr(state, "url", ""))[:4000],
        title=str(getattr(state, "title", ""))[:1000],
        dom=dom,
        tabs=tuple(tabs),
        page_info=page_info,
        errors=errors,
    )


def browser_use_available() -> bool:
    return importlib.util.find_spec("browser_use") is not None


def browser_use_backend_factory(options: BrowserLaunchOptions) -> BrowserUseBackend:
    return BrowserUseBackend(options=options)


__all__ = [
    "BrowserUseBackend",
    "browser_use_available",
    "browser_use_backend_factory",
]
