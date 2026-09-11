from __future__ import annotations

import asyncio
import importlib.util
import os
import threading
import time
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Coroutine

from .browser_diagnostics import BrowserDiagnosticLog, summarize_bridge_args, summarize_browser_state_payload
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
    user_data_dir: str | Path | None = None
    cdp_url: str | None = None
    diagnostics: BrowserDiagnosticLog | None = None

    def __post_init__(self) -> None:
        self.diagnostics = self.diagnostics or BrowserDiagnosticLog.from_environment()
        self._log(
            "browser_use.backend.created",
            backend=self.backend_name,
            mode=self._connection_mode(),
            headless=self.options.headless,
            allowed_domains=list(self.options.allowed_domains),
            diagnostics=self.diagnostics.status(expose_path=False),
        )
        if importlib.util.find_spec("browser_use") is None:
            self._log("browser_use.backend.unavailable", package="browser_use")
            raise BrowserUnavailableError(
                "browser-use is not installed; install Loom with the browser extra"
            )
        self._runner = _AsyncLoopThread(name="loom-browser-use")
        self._session: Any | None = None
        self._closed = False
        self._log("browser_use.loop.started", thread="loom-browser-use")

    @property
    def backend_name(self) -> str:
        return "browser-use"

    @property
    def state_revision(self) -> int:
        return max(0, int(getattr(self, "_state_revision", 0)))

    def _connection_mode(self) -> str:
        if str(self.cdp_url or "").strip():
            return "cdp-attach"
        if self.user_data_dir is not None:
            return "local-launch-persistent"
        return "local-launch-ephemeral"

    def _log(self, event: str, **fields: Any) -> None:
        diagnostics = self.diagnostics
        if diagnostics is None:
            return
        try:
            diagnostics.event(event, backend=self.backend_name, mode=self._connection_mode(), **fields)
        except Exception:
            return

    def _state_summary(self, state: BrowserPageState, *, include_dom_excerpt: bool = True) -> dict[str, Any]:
        return summarize_browser_state_payload(state.to_dict(max_dom_chars=120_000), include_dom_excerpt=include_dom_excerpt)

    def _with_backend_page_info(self, state: BrowserPageState, **extra: Any) -> BrowserPageState:
        page_info = dict(state.page_info or {})
        page_info.update(
            {
                "backend": self.backend_name,
                "connection_mode": self._connection_mode(),
                "diagnostics": self.diagnostics.status(expose_path=False) if self.diagnostics is not None else {"enabled": False},
                **extra,
            }
        )
        return BrowserPageState(
            url=state.url,
            title=state.title,
            dom=state.dom,
            tabs=state.tabs,
            page_info=page_info,
            errors=state.errors,
        )

    def _run_state_action(
        self,
        action: str,
        coroutine: Coroutine[Any, Any, BrowserPageState],
        *,
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
        include_dom_excerpt: bool = True,
    ) -> BrowserPageState:
        started = time.monotonic()
        self._log(
            "browser_use.action.started",
            action=action,
            state_revision=self.state_revision,
            args=summarize_bridge_args(action, args),
        )
        try:
            state = self._runner.run(coroutine, timeout=timeout or self.action_timeout_seconds)
        except Exception as exc:
            self._log(
                "browser_use.action.failed",
                action=action,
                state_revision=self.state_revision,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        self._log(
            "browser_use.action.completed",
            action=action,
            state_revision=self.state_revision,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            state=self._state_summary(state, include_dom_excerpt=include_dom_excerpt),
        )
        return state

    def _run_bytes_action(
        self,
        action: str,
        coroutine: Coroutine[Any, Any, bytes],
        *,
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> bytes:
        started = time.monotonic()
        self._log("browser_use.action.started", action=action, args=summarize_bridge_args(action, args))
        try:
            data = self._runner.run(coroutine, timeout=timeout or self.action_timeout_seconds)
        except Exception as exc:
            self._log(
                "browser_use.action.failed",
                action=action,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        self._log(
            "browser_use.action.completed",
            action=action,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            bytes=len(data) if isinstance(data, (bytes, bytearray)) else 0,
        )
        return data

    async def _ensure_session(self):
        if self._session is not None:
            return self._session
        self._log(
            "browser_use.session.creating",
            cdp_attached=bool(str(self.cdp_url or "").strip()),
            cdp_endpoint_exposed=False,
            profile_path_exposed=False,
            has_user_data_dir=self.user_data_dir is not None,
        )
        # browser-use configures its own logging at import time unless disabled.
        os.environ.setdefault("BROWSER_USE_SETUP_LOGGING", "false")
        from browser_use import BrowserProfile, BrowserSession

        attached = bool(str(self.cdp_url or "").strip())
        try:
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
                # Existing CDP browsers own their own profile. Supplying a second
                # user_data_dir would be misleading and is ignored by browser-use's
                # attach path anyway, so keep the two modes explicitly separate.
                user_data_dir=None if attached else self.user_data_dir,
                cdp_url=str(self.cdp_url).strip() if attached else None,
                keep_alive=True if attached else False,
            )
            self._session = BrowserSession(browser_profile=profile)
        except Exception as exc:
            self._log("browser_use.session.create_failed", error=f"{type(exc).__name__}: {exc}")
            raise
        self._log("browser_use.session.created", session_id=str(getattr(self._session, "id", ""))[-16:])
        return self._session

    async def _start_async(self) -> BrowserPageState:
        session = await self._ensure_session()
        self._log("browser_use.session.starting", session_id=str(getattr(session, "id", ""))[-16:])
        await session.start()
        self._log("browser_use.session.started", session_id=str(getattr(session, "id", ""))[-16:])
        if str(self.cdp_url or "").strip():
            # Do not commandeer whichever user tab happened to be active when Loom
            # attached. browser-use explicitly permits about:blank under its security
            # watchdog, so create/switch to a neutral work tab before returning state.
            from browser_use.browser.events import NavigateToUrlEvent

            self._log("browser_use.cdp.work_tab.creating", reason="avoid commandeering active user tab")
            await self._dispatch(NavigateToUrlEvent(url="about:blank", new_tab=True))
            self._log("browser_use.cdp.work_tab.created")
        return await self._state_async()

    def start(self) -> BrowserPageState:
        try:
            return self._run_state_action(
                "start",
                self._start_async(),
                args={"headless": self.options.headless, "allowed_domains": list(self.options.allowed_domains)},
            )
        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserUnavailableError(f"browser-use failed to start: {type(exc).__name__}: {exc}") from exc

    async def _state_async(self) -> BrowserPageState:
        session = await self._ensure_session()
        started = time.monotonic()
        state = await session.get_browser_state_summary(include_screenshot=False)
        serialized = self._with_backend_page_info(_serialize_state(state), capture_mode="browser_use_summary")
        dom_state = getattr(state, "dom_state", None)
        selector_map = getattr(dom_state, "selector_map", {}) or {}
        self._log(
            "browser_use.state.captured",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            selector_count=len(selector_map),
            tab_count=len(serialized.tabs),
            error_count=len(serialized.errors),
            state=self._state_summary(serialized),
        )
        return serialized

    def state(self) -> BrowserPageState:
        return self._run_state_action("state", self._state_async())

    async def _dispatch(self, event) -> None:
        session = await self._ensure_session()
        event_name = type(event).__name__
        started = time.monotonic()
        self._log("browser_use.event.dispatch.started", event=event_name)
        try:
            dispatched = session.event_bus.dispatch(event)
            await dispatched
            await dispatched.event_result(raise_if_any=True, raise_if_none=False)
        except Exception as exc:
            self._log(
                "browser_use.event.dispatch.failed",
                event=event_name,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        self._log("browser_use.event.dispatch.completed", event=event_name, elapsed_ms=int((time.monotonic() - started) * 1000))

    async def _navigate_async(self, url: str, *, new_tab: bool) -> BrowserPageState:
        from browser_use.browser.events import NavigateToUrlEvent

        await self._dispatch(NavigateToUrlEvent(url=url, new_tab=new_tab))
        return await self._state_async()

    def navigate(self, url: str, *, new_tab: bool = False) -> BrowserPageState:
        return self._run_state_action(
            "navigate",
            self._navigate_async(url, new_tab=new_tab),
            args={"url": url, "new_tab": new_tab},
        )

    async def _node_for_index(self, index: int):
        self._log("browser_use.node.lookup.started", index=int(index), source="fresh_browser_use_state")
        session = await self._ensure_session()
        state = await session.get_browser_state_summary(include_screenshot=False)
        dom_state = getattr(state, "dom_state", None)
        selector_map = getattr(dom_state, "selector_map", {}) or {}
        node = selector_map.get(int(index))
        if node is None:
            self._log("browser_use.node.lookup.missing", index=int(index), selector_count=len(selector_map))
            raise BrowserError(
                f"browser element index {index} is unavailable; refresh browser state before retrying"
            )
        self._log(
            "browser_use.node.lookup.completed",
            index=int(index),
            selector_count=len(selector_map),
            backend_node_id_present=getattr(node, "backend_node_id", None) is not None,
        )
        return node

    async def _click_async(self, index: int) -> BrowserPageState:
        from browser_use.browser.events import ClickElementEvent

        node = await self._node_for_index(index)
        await self._dispatch(ClickElementEvent(node=node))
        return await self._state_async()

    def click(self, index: int) -> BrowserPageState:
        return self._run_state_action("click", self._click_async(index), args={"index": int(index)})

    async def _type_async(self, index: int, text: str, *, clear: bool) -> BrowserPageState:
        from browser_use.browser.events import TypeTextEvent

        node = await self._node_for_index(index)
        await self._dispatch(TypeTextEvent(node=node, text=text, clear=clear))
        return await self._state_async()

    def type_text(self, index: int, text: str, *, clear: bool = True) -> BrowserPageState:
        return self._run_state_action(
            "type_text",
            self._type_async(index, text, clear=clear),
            args={"index": int(index), "text": str(text), "clear": bool(clear)},
            include_dom_excerpt=False,
        )

    async def _scroll_async(self, direction: str, amount: int) -> BrowserPageState:
        from browser_use.browser.events import ScrollEvent

        await self._dispatch(ScrollEvent(direction=direction, amount=amount, node=None))
        return await self._state_async()

    def scroll(self, direction: str, amount: int) -> BrowserPageState:
        return self._run_state_action(
            "scroll",
            self._scroll_async(direction, amount),
            args={"direction": direction, "amount": int(amount)},
        )

    async def _back_async(self) -> BrowserPageState:
        from browser_use.browser.events import GoBackEvent

        await self._dispatch(GoBackEvent())
        return await self._state_async()

    def go_back(self) -> BrowserPageState:
        return self._run_state_action("go_back", self._back_async())

    async def _screenshot_async(self, *, full_page: bool) -> bytes:
        session = await self._ensure_session()
        data = await session.take_screenshot(full_page=full_page)
        return bytes(data)

    def screenshot(self, *, full_page: bool = False) -> bytes:
        return self._run_bytes_action("screenshot", self._screenshot_async(full_page=full_page), args={"full_page": bool(full_page)})

    async def _close_async(self) -> None:
        if self._session is not None:
            try:
                # A CDP-attached Chrome/Edge process belongs to the user, not
                # Loom. browser-use stop() disconnects its event/CDP state without
                # force-killing that external browser. Local Loom launches still
                # use kill() so we do not leak child Chromium processes.
                if str(self.cdp_url or "").strip():
                    self._log("browser_use.session.stopping", strategy="stop_external_cdp")
                    await self._session.stop()
                else:
                    self._log("browser_use.session.stopping", strategy="kill_local_launch")
                    await self._session.kill()
            finally:
                self._session = None
                self._log("browser_use.session.closed")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        started = time.monotonic()
        try:
            self._runner.run(self._close_async(), timeout=20.0)
            self._log("browser_use.backend.closed", elapsed_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:
            self._log("browser_use.backend.close_failed", elapsed_ms=int((time.monotonic() - started) * 1000), error=f"{type(exc).__name__}: {exc}")
        finally:
            self._runner.close()
            self._log("browser_use.loop.closed")


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
