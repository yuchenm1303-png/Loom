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
        assert bridge.status()["last_client_version"] == "0.1.5"
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
        assert len(backend.screenshot()) > 100
        assert created_tab in {tab.get("tab_id") for tab in backend.tabs().tabs}
        backend.navigate(f"http://127.0.0.1:{web.server_address[1]}/second")
        assert backend.go_back().url == url
        assert backend.go_forward().title == "Second"
        backend.refresh()
        if created_tab:
            backend.close_tab(created_tab)
            created_tab = ""
        print("PASS: real Edge exercised Loom open/state/type/click/select/hover/drag/find/eval/wait/screenshot/tabs/navigation/history/refresh/close")
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
