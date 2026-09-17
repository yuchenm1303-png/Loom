"""Drive a real Edge + Loom extension through the production bridge backend.

This is an opt-in smoke test. It uses a temporary Edge profile and a temporary
extension copy, so it never touches the user's tabs, cookies, or installed bridge.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from app.agent_runtime.browser_extension_bridge import BrowserExtensionBridge, BrowserExtensionSessionBackend
from app.agent_runtime.browser_session import BrowserLaunchOptions


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "extensions" / "browser-current-tab"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")


PAGE = b"""<!doctype html><meta charset=utf-8><title>Loom bridge smoke</title>
<style>body{font:16px sans-serif;height:1800px}#source,#target{padding:24px;margin:12px;border:1px solid}</style>
<input aria-label='Smoke input'><button onclick="out.textContent='clicked: '+document.querySelector('input').value">Apply</button>
<select aria-label='Smoke select'><option>Alpha</option><option>Beta</option></select>
<div id=source tabindex=0 draggable=true>Drag source</div><div id=target tabindex=0>Drop target</div>
<input type=password aria-label='Smoke secret'>
<p id=out>ready</p><script>
source.ondragstart=e=>e.dataTransfer.setData('text/plain','loom');
target.ondragover=e=>e.preventDefault(); target.ondrop=e=>{e.preventDefault();out.textContent='dropped'};
</script>"""


class PageHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def do_GET(self):
        body = PAGE if self.path != "/second" else b"<!doctype html><title>Second</title><p>second page</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, _request, _client_address):
        return


def wait_until(predicate, timeout: float, message: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.2)
    raise TimeoutError(message)


def main() -> None:
    if not EDGE.exists():
        raise FileNotFoundError(f"Microsoft Edge not found: {EDGE}")
    web = QuietThreadingHTTPServer(("127.0.0.1", 0), PageHandler)
    threading.Thread(target=web.serve_forever, daemon=True).start()
    bridge = BrowserExtensionBridge(port=0, token="loom-live-smoke-token", command_timeout=20, poll_timeout=2)
    bridge.start()
    process = None
    created_tab = ""
    temp = Path(tempfile.mkdtemp(prefix="loom-extension-smoke-"))
    try:
        extension = temp / "extension"
        profile = temp / "profile"
        shutil.copytree(EXTENSION, extension)
        (extension / "bridge-config.json").write_text(
            json.dumps({"bridgeUrl": bridge.url, "token": bridge.token}), encoding="utf-8"
        )
        process = subprocess.Popen(
            [
                str(EDGE), f"--user-data-dir={profile}", f"--disable-extensions-except={extension}",
                f"--load-extension={extension}", "--no-first-run", "--no-default-browser-check",
                "--headless=new", "about:blank",
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        wait_until(lambda: bridge.connected, 20, "extension did not connect to the production bridge")
        # Match the repo rather than a pinned literal: what this needs to prove is
        # that the copy under test is the one that connected, and a literal only
        # made every version bump look like a bridge failure.
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        assert bridge.status()["last_client_version"] == manifest["version"]
        backend = BrowserExtensionSessionBackend(options=BrowserLaunchOptions(), bridge=bridge)
        initial = backend.start()
        assert initial.page_info.get("recovery", {}).get("action") == "browser_navigate"
        url = f"http://127.0.0.1:{web.server_address[1]}/"
        state = backend.navigate(url)
        created_tab = str(state.page_info.get("tab_id") or "")
        assert state.url == url and "Smoke input" in state.dom
        assert backend.type_text(0, "hello").url == url
        clicked = backend.click(1)
        assert "clicked: hello" in clicked.dom
        options = backend.dropdown_options(2)
        assert any(item.get("text") == "Beta" for item in options)
        backend.select_option(2, "Beta")
        backend.hover(1)
        backend.drag(3, 4)
        assert "dropped" in backend.state().dom
        assert backend.find_text("dropped").url == url
        evaluated = backend.evaluate("document.title")
        assert evaluated.get("ok") is True and evaluated.get("value") == "Loom bridge smoke"
        assert backend.wait_for(for_text="dropped", timeout_seconds=3).get("satisfied") is True

        # Typing into a password field must be observable as having happened. The
        # value is withheld on purpose, but reporting an empty field either way made
        # a successful type indistinguishable from a no-op: on a real Google Client
        # Secret field the model typed, read the field back as empty, retried, then
        # gave up on the browser tools and escalated to clicking the desktop.
        secret_state = backend.state()
        assert 'type="password"' in secret_state.dom
        assert 'filled="true"' not in secret_state.dom, "an untouched password field reports as filled"
        typed_secret = backend.type_text(5, "correct-horse-battery")
        assert 'filled="true"' in typed_secret.dom, "a typed password field still reads as empty"
        assert "correct-horse-battery" not in typed_secret.dom, "the password value reached the model"
        assert len(backend.screenshot()) > 100
        assert created_tab in {tab.get("tab_id") for tab in backend.tabs().tabs}
        backend.navigate(f"http://127.0.0.1:{web.server_address[1]}/second")
        assert backend.go_back().url == url
        assert backend.go_forward().title == "Second"
        backend.refresh()

        # Downloads must be scoped to the session. The extension can see every
        # download in the browser, so a missing start time reports nothing rather
        # than handing over the user's history.
        assert bridge.call("downloads", {"since_ms": 0}) == {"files": []}
        assert backend.downloaded_files() == []

        # Closing releases the tabs borrowed from the user, and only those. Nothing
        # may call release_tabs before this: doing so consumes the ownership and
        # leaves the checks below passing without close() doing a thing.
        backend.close()

        # A tab Loom opened itself stays Loom's across sessions. Releasing those too
        # meant a new session did not recognise the tab it had just been working in
        # and opened another every time, which in a real Edge was four tabs for one
        # task, each of them taking the foreground.
        second = BrowserExtensionSessionBackend(options=BrowserLaunchOptions(), bridge=bridge)
        second.start()
        before = len(second.tabs().tabs)
        moved = second.navigate(url)
        assert str(moved.page_info.get("tab_id") or "") == created_tab, (
            "a new session opened its own tab instead of reusing Loom's work tab"
        )
        assert len(second.tabs().tabs) == before, "a new session created a duplicate work tab"

        # Releasing again is harmless and reports how many were still held.
        assert isinstance(bridge.call("release_tabs", {}).get("released"), int)

        # Loom must not take the foreground: its tab keeps working in the background
        # while whatever the user was reading keeps focus. This is the behaviour that
        # lets Loom sit beside a person instead of fighting them for the window. The
        # page itself is the witness - a backgrounded tab reports "hidden" - because
        # the tab list this backend returns carries no foreground flag.
        assert second.evaluate("document.visibilityState").get("value") == "hidden", (
            "Loom pulled its own tab into the foreground"
        )

        # A screenshot is the one action that needs the tab visible, and it has to
        # put focus back where it found it.
        assert len(second.screenshot()) > 100
        assert second.evaluate("document.visibilityState").get("value") == "hidden", (
            "a screenshot left Loom's tab in the foreground"
        )

        if created_tab:
            second.close_tab(created_tab)
            created_tab = ""
        print(
            "PASS: real Edge exercised Loom open/state/type/click/select/hover/drag/find/eval/wait/"
            "screenshot/tabs/navigation/history/refresh/close, session-scoped downloads, "
            "work-tab reuse across sessions, and background operation without stealing focus"
        )
    finally:
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        bridge.stop()
        web.shutdown()
        web.server_close()
        shutil.rmtree(temp, ignore_errors=True)


if __name__ == "__main__":
    main()
