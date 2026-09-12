from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import threading
import time
from collections import deque
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
    BrowserTextNotFoundError,
    BrowserUnavailableError,
)


_DEFAULT_ACTION_TIMEOUT = 60.0
_WAIT_POLL_SECONDS = 0.25
_WAIT_MAX_SECONDS = 60.0
# The async runner has to outlive the wait itself, or a wait that legitimately
# runs to its timeout is reported as a backend timeout instead of an unmet
# condition.
_WAIT_RUNNER_MARGIN = 10.0
# A page can issue thousands of requests, so the log is a ring: the recent ones
# are what a model is asking about.
_NETWORK_LOG_LIMIT = 500
_NETWORK_BODY_CHARS = 30_000


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
    # browser-use channel name ("chrome"/"msedge"); empty keeps its own default.
    browser_channel: str = ""
    # Where downloads land. Set by the tool layer to a directory inside the Loom
    # workspace, which is the only place the model can read files back from.
    downloads_dir: str | Path | None = None
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
        # An attached browser is already running, so its build is not ours to pick.
        channel = "" if attached else str(self.browser_channel or "").strip()
        try:
            downloads = str(self.downloads_dir) if self.downloads_dir else None
            profile = BrowserProfile(
                **({"channel": channel} if channel else {}),
                **({"downloads_path": downloads, "accept_downloads": True} if downloads else {}),
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
        # The diagnostics field cannot be called "event": _log and the diagnostics
        # sink both take the record name as their first positional parameter, so a
        # keyword of that name raises TypeError before either body runs. Every
        # browser action dispatches through here, so that mistake made navigate,
        # click, type, scroll, back, refresh and tab switching fail identically.
        self._log("browser_use.event.dispatch.started", browser_event=event_name)
        try:
            dispatched = session.event_bus.dispatch(event)
            await dispatched
            await dispatched.event_result(raise_if_any=True, raise_if_none=False)
        except Exception as exc:
            self._log(
                "browser_use.event.dispatch.failed",
                browser_event=event_name,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        self._log(
            "browser_use.event.dispatch.completed",
            browser_event=event_name,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )

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

    async def _evaluate_async(self, expression: str, *, await_promise: bool) -> dict[str, Any]:
        session = await self._ensure_session()
        cdp = await session.get_or_create_cdp_session()
        response = await cdp.cdp_client.send.Runtime.evaluate(
            params={
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": bool(await_promise),
                # Page scripts are what the model is reaching for, so a plain
                # expression should behave the way it would in the console.
                "userGesture": True,
            },
            session_id=cdp.session_id,
        )
        details = response.get("exceptionDetails") if isinstance(response, dict) else None
        if details:
            thrown = details.get("exception") or {}
            message = (
                str(thrown.get("description") or thrown.get("value") or details.get("text") or "")
            ).strip()
            return {"ok": False, "error": message[:2000] or "the page script raised"}

        raw = (response.get("result") or {}) if isinstance(response, dict) else {}
        if "value" in raw:
            value = raw["value"]
        elif raw.get("type") == "undefined":
            value = None
        else:
            # Functions, DOM nodes and other non-serializable results come back
            # only as a description; report that instead of a silent null.
            value = str(raw.get("description") or raw.get("className") or raw.get("type") or "")
        return {"ok": True, "value": value, "value_type": str(raw.get("type") or "undefined")}

    def evaluate(self, expression: str, *, await_promise: bool = True) -> dict[str, Any]:
        """Run a JavaScript expression in the active page and return its value."""

        source = str(expression or "")
        if not source.strip():
            raise ValueError("browser evaluate expression must not be empty")
        if len(source) > 20_000:
            raise ValueError("browser evaluate expression exceeds 20,000 characters")
        started = time.monotonic()
        # The expression and its result both come from, and can reveal, page
        # content, so neither is written to diagnostics.
        self._log("browser_use.evaluate.started", expression_chars=len(source))
        try:
            outcome = self._runner.run(
                self._evaluate_async(source, await_promise=await_promise),
                timeout=self.action_timeout_seconds,
            )
        except Exception as exc:
            self._log(
                "browser_use.evaluate.failed",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error=type(exc).__name__,
            )
            raise
        self._log(
            "browser_use.evaluate.completed",
            elapsed_ms=int((time.monotonic() - started) * 1000),
            ok=bool(outcome.get("ok")),
            value_type=str(outcome.get("value_type") or ""),
        )
        return outcome

    def _network_log(self) -> deque:
        log = getattr(self, "_network_entries", None)
        if log is None:
            log = deque(maxlen=_NETWORK_LOG_LIMIT)
            self._network_entries = log
        return log

    async def _network_start_async(self) -> dict[str, Any]:
        session = await self._ensure_session()
        cdp = await session.get_or_create_cdp_session()
        log = self._network_log()
        if getattr(self, "_network_registered", False):
            await cdp.cdp_client.send.Network.enable(session_id=cdp.session_id)
            return {"capturing": True, "entries": len(log)}

        def on_request(event, session_id=None):
            request = event.get("request") or {}
            log.append(
                {
                    "request_id": str(event.get("requestId") or ""),
                    "method": str(request.get("method") or ""),
                    "url": str(request.get("url") or "")[:2000],
                    "resource_type": str(event.get("type") or ""),
                    "status": None,
                    "mime_type": "",
                    "bytes": 0,
                    "from_cache": False,
                    "started_at": float(event.get("timestamp") or 0.0),
                }
            )

        def on_response(event, session_id=None):
            response = event.get("response") or {}
            request_id = str(event.get("requestId") or "")
            # A cache hit transfers nothing, so bytes is legitimately 0 there. The
            # flag is recorded because "0 bytes" on its own reads as an empty
            # response.
            cached = bool(response.get("fromDiskCache") or response.get("fromPrefetchCache"))
            for entry in reversed(log):
                if entry["request_id"] == request_id:
                    entry["status"] = response.get("status")
                    entry["mime_type"] = str(response.get("mimeType") or "")
                    entry["bytes"] = int(response.get("encodedDataLength") or 0)
                    entry["from_cache"] = cached
                    return
            log.append(
                {
                    "request_id": request_id,
                    "method": "",
                    "url": str(response.get("url") or "")[:2000],
                    "resource_type": "",
                    "status": response.get("status"),
                    "mime_type": str(response.get("mimeType") or ""),
                    "bytes": int(response.get("encodedDataLength") or 0),
                    "from_cache": cached,
                    "started_at": 0.0,
                }
            )

        def on_failed(event, session_id=None):
            request_id = str(event.get("requestId") or "")
            for entry in reversed(log):
                if entry["request_id"] == request_id:
                    entry["status"] = None
                    entry["error"] = str(event.get("errorText") or "failed")[:300]
                    return

        def on_finished(event, session_id=None):
            # responseReceived fires before the body has arrived, so its
            # encodedDataLength is 0 for almost everything. The transferred size
            # is only known here.
            request_id = str(event.get("requestId") or "")
            size = int(event.get("encodedDataLength") or 0)
            if size <= 0:
                return
            for entry in reversed(log):
                if entry["request_id"] == request_id:
                    entry["bytes"] = size
                    return

        client = cdp.cdp_client
        client.register.Network.requestWillBeSent(on_request)
        client.register.Network.responseReceived(on_response)
        client.register.Network.loadingFailed(on_failed)
        client.register.Network.loadingFinished(on_finished)
        # Registration is on the client and cumulative, so a second start would
        # double-count every request.
        self._network_registered = True
        await client.send.Network.enable(session_id=cdp.session_id)
        return {"capturing": True, "entries": len(log)}

    async def _network_stop_async(self) -> dict[str, Any]:
        session = await self._ensure_session()
        cdp = await session.get_or_create_cdp_session()
        # The listeners stay registered; disabling the domain is what stops the
        # events, and re-enabling later must not add a second set of handlers.
        await cdp.cdp_client.send.Network.disable(session_id=cdp.session_id)
        return {"capturing": False, "entries": len(self._network_log())}

    async def _network_body_async(self, request_id: str) -> dict[str, Any]:
        session = await self._ensure_session()
        cdp = await session.get_or_create_cdp_session()
        try:
            payload = await cdp.cdp_client.send.Network.getResponseBody(
                params={"requestId": request_id}, session_id=cdp.session_id
            )
        except Exception as exc:
            # A body is only retained while the response is still in the
            # browser's buffer, so this legitimately fails for older requests.
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        body = str(payload.get("body") or "")
        return {
            "ok": True,
            "base64_encoded": bool(payload.get("base64Encoded")),
            "body": body[:_NETWORK_BODY_CHARS],
            "truncated": len(body) > _NETWORK_BODY_CHARS,
        }

    def network_start(self) -> dict[str, Any]:
        return self._runner.run(self._network_start_async(), timeout=self.action_timeout_seconds)

    def network_stop(self) -> dict[str, Any]:
        return self._runner.run(self._network_stop_async(), timeout=self.action_timeout_seconds)

    def network_entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._network_log()]

    def network_clear(self) -> None:
        self._network_log().clear()

    def network_body(self, request_id: str) -> dict[str, Any]:
        value = str(request_id or "").strip()
        if not value:
            raise ValueError("browser network request_id must not be empty")
        return self._runner.run(
            self._network_body_async(value), timeout=self.action_timeout_seconds
        )

    async def _wait_async(
        self,
        *,
        seconds: float,
        condition: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        started = time.monotonic()
        if not condition:
            await asyncio.sleep(seconds)
            return {"satisfied": True, "waited_ms": int((time.monotonic() - started) * 1000)}

        # Poll rather than race a provider event: the thing being waited for is a
        # page-defined condition, and a page can satisfy it without firing
        # anything Loom or browser-use would see.
        deadline = started + timeout_seconds
        last_error = ""
        while True:
            outcome = await self._evaluate_async(condition, await_promise=True)
            if outcome.get("ok"):
                if bool(outcome.get("value")):
                    return {
                        "satisfied": True,
                        "waited_ms": int((time.monotonic() - started) * 1000),
                    }
                last_error = ""
            else:
                last_error = str(outcome.get("error") or "")
            if time.monotonic() >= deadline:
                return {
                    "satisfied": False,
                    "waited_ms": int((time.monotonic() - started) * 1000),
                    "error": last_error,
                }
            await asyncio.sleep(_WAIT_POLL_SECONDS)

    def wait_for(
        self,
        *,
        seconds: float = 0.0,
        for_text: str = "",
        until: str = "",
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        """Wait for a page condition, or just settle for a fixed time.

        Without this the only way to wait out an async page was to re-request the
        whole state in a loop, which costs a DOM serialization per attempt and
        puts a stream of near-identical snapshots in front of the model.
        """

        timeout = max(0.5, min(float(timeout_seconds), _WAIT_MAX_SECONDS))
        text = str(for_text or "").strip()
        expression = str(until or "").strip()
        if text and expression:
            raise ValueError("browser wait takes either for_text or until, not both")
        if text:
            if len(text) > 500:
                raise ValueError("browser wait text exceeds 500 characters")
            # json.dumps produces a safe JS string literal, so page text cannot
            # break out of the expression.
            literal = json.dumps(text)
            expression = (
                "(() => { const t = document.body && document.body.innerText;"
                f" return !!t && t.indexOf({literal}) !== -1; }})()"
            )
        elif expression:
            if len(expression) > 20_000:
                raise ValueError("browser wait condition exceeds 20,000 characters")
        delay = max(0.0, min(float(seconds or 0.0), _WAIT_MAX_SECONDS))
        if not expression and delay <= 0:
            raise ValueError("browser wait needs seconds, for_text, or until")
        return self._runner.run(
            self._wait_async(seconds=delay, condition=expression, timeout_seconds=timeout),
            timeout=timeout + _WAIT_RUNNER_MARGIN,
        )

    async def _downloads_async(self) -> list[str]:
        session = await self._ensure_session()
        raw = getattr(session, "downloaded_files", None) or []
        return [str(item) for item in raw]

    def downloaded_files(self) -> list[str]:
        return self._runner.run(self._downloads_async(), timeout=self.action_timeout_seconds)

    async def _cookies_async(self) -> list[dict[str, Any]]:
        session = await self._ensure_session()
        raw = await session._cdp_get_cookies()
        return [dict(item) for item in (raw or [])]

    def cookies(self) -> list[dict[str, Any]]:
        return self._runner.run(self._cookies_async(), timeout=self.action_timeout_seconds)

    async def _storage_state_async(self) -> dict[str, Any]:
        session = await self._ensure_session()
        raw = await session._cdp_get_storage_state()
        payload = dict(raw or {})
        return {
            "cookies": [dict(item) for item in (payload.get("cookies") or [])],
            "origins": [dict(item) for item in (payload.get("origins") or [])],
        }

    def storage_state(self) -> dict[str, Any]:
        return self._runner.run(self._storage_state_async(), timeout=self.action_timeout_seconds)

    async def _set_cookies_async(self, cookies: list[dict[str, Any]]) -> None:
        session = await self._ensure_session()
        await session._cdp_set_cookies(cookies)

    def set_cookies(self, cookies: list[dict[str, Any]]) -> None:
        self._runner.run(
            self._set_cookies_async([dict(item) for item in cookies]),
            timeout=self.action_timeout_seconds,
        )

    async def _clear_cookies_async(self) -> None:
        session = await self._ensure_session()
        await session._cdp_clear_cookies()

    def clear_cookies(self) -> None:
        self._runner.run(self._clear_cookies_async(), timeout=self.action_timeout_seconds)

    async def _storage_origins_async(self) -> list[dict[str, Any]]:
        session = await self._ensure_session()
        raw = await session._cdp_get_origins()
        return [dict(item) for item in (raw or [])]

    def storage_origins(self) -> list[dict[str, Any]]:
        return self._runner.run(self._storage_origins_async(), timeout=self.action_timeout_seconds)

    async def _forward_async(self) -> BrowserPageState:
        from browser_use.browser.events import GoForwardEvent

        await self._dispatch(GoForwardEvent())
        return await self._state_async()

    def go_forward(self) -> BrowserPageState:
        return self._run_state_action("go_forward", self._forward_async())

    async def _find_text_async(self, text: str, *, direction: str) -> BrowserPageState:
        from browser_use.browser.events import ScrollToTextEvent

        try:
            await self._dispatch(ScrollToTextEvent(text=text, direction=direction))
        except Exception as exc:
            # Translate "no match" into Loom's own type at the boundary. The
            # provider signals it with a class that happens to share the name
            # BrowserError but is unrelated to Loom's, so callers cannot catch it.
            if "not found" not in str(exc).casefold():
                raise
            raise BrowserTextNotFoundError("browser find matched no text on the page") from exc
        return await self._state_async()

    # direction is positional because _backend_snapshot_action forwards *args.
    def find_text(self, text: str, direction: str = "down") -> BrowserPageState:
        value = str(text or "").strip()
        if not value:
            raise ValueError("browser find text must not be empty")
        if len(value) > 500:
            raise ValueError("browser find text exceeds 500 characters")
        wanted = str(direction or "down").strip().casefold()
        if wanted not in {"up", "down"}:
            raise ValueError("browser find direction must be up or down")
        return self._run_state_action(
            "find_text",
            self._find_text_async(value, direction=wanted),
            args={"direction": wanted},
            include_dom_excerpt=False,
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
