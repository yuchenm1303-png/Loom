"""Opt-in regression using real isolated Chromium and the production capture tool.

Set LOOM_BROWSER_CAPTURE_LIVE=1, LOOM_NODE_PATH, LOOM_PLAYWRIGHT_MODULE and
LOOM_CHROMIUM_PATH. This neither attaches to nor reads a user's browser.
"""
import base64
import json
import os
import queue
import subprocess
import threading

import pytest

from app.agent_runtime.browser_session import BrowserPageState
from app.agent_runtime.browser_security import BrowserSecurityPolicy
from app.agent_runtime.browser_runtime_v1 import BrowserRuntime
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy
from app.agent_runtime.storage import FileAgentSessionStore
from app.agent_runtime.tools import ToolContext


WORKER = r"""
const {chromium} = require(process.env.LOOM_PLAYWRIGHT_MODULE || 'playwright');
const readline = require('node:readline');
let browser, page;
(async () => {
  for await (const line of readline.createInterface({input: process.stdin})) {
    try {
      const request = JSON.parse(line);
      let value;
      if (request.action === 'start') {
        browser = await chromium.launch({executablePath: process.env.LOOM_CHROMIUM_PATH, headless:true});
        page = await browser.newPage({viewport:{width:800,height:600}});
        await page.setContent(`<style>body{background:rgb(255,0,0)}button{width:180px;height:80px}</style>
          <button onclick="document.body.style.background='rgb(0,0,255)';this.textContent='Verified'">Toggle</button>`);
        value = {url:page.url(), text:await page.locator('button').innerText()};
      } else if (request.action === 'click') {
        await page.locator('button').click();
        value = {url:page.url(), text:await page.locator('button').innerText()};
      } else if (request.action === 'state') {
        value = {url:page.url(), text:await page.locator('button').innerText()};
      } else if (request.action === 'screenshot') {
        value = (await page.screenshot()).toString('base64');
      } else if (request.action === 'close') {
        await browser?.close(); value = true;
      }
      process.stdout.write(JSON.stringify({ok:true,value})+'\n');
    } catch(error) {process.stdout.write(JSON.stringify({ok:false,error:String(error)})+'\n');}
  }
})().catch(error => {process.stderr.write(String(error));process.exit(1)});
"""


@pytest.mark.skipif(os.environ.get("LOOM_BROWSER_CAPTURE_LIVE") != "1", reason="requires opt-in isolated Chromium")
def test_real_chromium_capture_and_image_readback(tmp_path):
    from PIL import Image
    from app.agent_runtime.workspace_tools import loom_default_tools
    script = tmp_path / "capture-worker.cjs"
    script.write_text(WORKER, encoding="utf-8")

    class Backend:
        backend_name = "live-chromium-test"
        def __init__(self, options):
            self.state_revision = 0
            self.process = subprocess.Popen([os.environ["LOOM_NODE_PATH"], str(script)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.lines = queue.Queue()
            def read():
                for line in self.process.stdout:
                    self.lines.put(line)
            threading.Thread(target=read, daemon=True).start()
        def rpc(self, action):
            self.process.stdin.write(json.dumps({"action": action}) + "\n")
            self.process.stdin.flush()
            result = json.loads(self.lines.get(timeout=30))
            if not result["ok"]:
                raise RuntimeError(result["error"])
            return result["value"]
        def observation(self, action):
            result = self.rpc(action)
            self.state_revision += 1
            return BrowserPageState(url=result["url"], title="Live capture fixture",
                                    dom=f"[1]<button>{result['text']}</button>")
        def start(self):
            return self.observation("start")
        def state(self):
            return self.observation("state")
        def click(self, index):
            assert index == 1
            return self.observation("click")
        def screenshot(self, *, full_page=False):
            return base64.b64decode(self.rpc("screenshot"))
        def close(self):
            try:
                if self.process.poll() is None:
                    self.rpc("close")
                    self.process.stdin.close()
                    self.process.wait(timeout=5)
            finally:
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=5)

    runtime = BrowserRuntime(platform=object(), store=FileAgentSessionStore(tmp_path / "state"),
        tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        web_search_provider=None, auto_configure_web_search=False, auto_configure_browser=False,
        browser_backend_factory=Backend, browser_security_policy=BrowserSecurityPolicy(resolve_dns=False))
    session = runtime.create_session("test", workspace_dir=tmp_path, permission_mode="full-access")
    context = ToolContext(session.session_id, "live", tmp_path, permission_mode="full-access")
    try:
        opened = runtime.tools.get("browser_open").handler(context, {})
        result = runtime.tools.get("browser_capture_sequence").handler(context, {
            "browser_id": opened.data["browser_id"], "state_revision": opened.data["state_revision"],
            "frames": 4, "interval_ms": 40, "trigger": {"type": "click", "index": 1}})
        assert result.ok
        assert "Verified" in result.data["dom"]
        frames = result.data["capture"]["frames"]
        with Image.open(tmp_path / frames[0]["path"]) as image:
            assert image.getpixel((700, 500))[:3] == (255, 0, 0)
        with Image.open(tmp_path / frames[-1]["path"]) as image:
            assert image.getpixel((700, 500))[:3] == (0, 0, 255)
        inspected = runtime.tools.get("view_images").handler(context, {
            "images": [{"path": frames[0]["path"]}, {"path": frames[-1]["path"]}]})
        assert inspected.ok
        assert len(inspected.data["images"]) == 2
    finally:
        runtime.close()
