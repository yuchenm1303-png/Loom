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
      } else if (request.action === 'navigate') {
        await page.goto(request.url);
        value = {url:page.url(), text:await page.locator('button').innerText()};
      } else if (request.action === 'state') {
        value = {url:page.url(), text:await page.locator('button').innerText()};
      } else if (request.action === 'screenshot') {
        value = (await page.screenshot()).toString('base64');
      } else if (request.action === 'evaluate') {
        value = await page.evaluate(request.expression);
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
        def rpc(self, action, **kwargs):
            self.process.stdin.write(json.dumps({"action": action, **kwargs}) + "\n")
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
        def navigate(self, url, *, new_tab=False):
            result = self.rpc("navigate", url=url)
            self.state_revision += 1
            return BrowserPageState(url=result["url"], title="Local UI fixture", dom=f"[1]<button>{result['text']}</button>")
        def screenshot(self, *, full_page=False):
            return base64.b64decode(self.rpc("screenshot"))
        def evaluate(self, expression, *, await_promise=True):
            return {"ok": True, "value": self.rpc("evaluate", expression=expression), "value_type": "boolean"}
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
        # Exercise the reusable acceptance tool against real browser DOM/actions.
        from dataclasses import replace
        session.current_turn_id = "live"
        runtime.store.save(session)
        step = runtime._build_step_context(session, next_model_step=True)
        context = replace(context, services={"execution_step": step})
        (tmp_path / "Panel.tsx").write_text("export const Panel = () => <button>Verified</button>")
        recipe = {"browser_id": opened.data["browser_id"],
            "state_revision": runtime.browser_sessions.snapshot(session.session_id, opened.data["browser_id"]).state_revision,
            "verification": {"case_id": "live-ui", "expectation": "Button updates text and background",
                             "subject_paths": ["Panel.tsx"]},
            "steps": [{"type": "action", "tool": "browser_click", "arguments": {"index": 1}},
                      {"type": "assert", "label": "Updated button and background", "expression":
                       "document.querySelector('button').textContent === 'Verified' && getComputedStyle(document.body).backgroundColor === 'rgb(0, 0, 255)'"}]}
        checked = runtime.tools.get("browser_run_check").handler(context, recipe)
        assert checked.ok, checked.content
        assert checked.data["verification"]["verdict"] == "passed"
        assert checked.data["images"][0]["path"].endswith(".png")
        # An intentionally broken assertion must fail; the runner must not heal it into a pass.
        recipe["state_revision"] = runtime.browser_sessions.snapshot(session.session_id, opened.data["browser_id"]).state_revision
        recipe["steps"][-1]["expression"] = "document.querySelector('button').textContent === 'BROKEN EXPECTATION'"
        failed = runtime.tools.get("browser_run_check").handler(context, recipe)
        assert not failed.ok
        assert failed.data["verification"]["verdict"] == "failed"
        assert failed.data["images"]
        if os.environ.get("LOOM_VERIFICATION_LIVE_MODEL") == "1":
            _live_model_eval(tmp_path, Backend)
    finally:
        runtime.close()


def _live_model_eval(tmp_path, backend):
    """Opt-in paid-model behavior evaluation; the model chooses its own tools.

    No simulated model responses are used. Credentials remain in environment/RAM.
    This intentionally has no prescribed screenshot call sequence in its prompt.
    """
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from app.agent_runtime import AgentRuntime
    from app.agent_runtime.contracts import AgentLimits, AgentStatus, AgentEventKind as E
    from app.agent_runtime.workspace_tools import loom_default_tools
    from app.runtime_model_switch import build_runtime_model_platform

    secret = os.environ.get("LOOM_VERIFICATION_MODEL_KEY", "")
    assert secret, "LOOM_VERIFICATION_MODEL_KEY is required for explicit live-model evaluation"
    project = tmp_path / "model-demo"
    project.mkdir()
    (project / "index.html").write_text('<!doctype html><style>body{margin:40px}#clip{height:12px;overflow:hidden}button{height:40px;width:160px}</style>'
        '<div id="clip"><button onclick="this.textContent=\'Confirmed\'">Confirm</button></div>', encoding="utf-8")
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(project)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    platform = build_runtime_model_platform(provider=os.environ.get("LOOM_VERIFICATION_MODEL_PROVIDER", "openai-compatible"),
        base_url=os.environ["LOOM_VERIFICATION_MODEL_URL"], model=os.environ["LOOM_VERIFICATION_MODEL_NAME"],
        api_key=secret, vision=True, request_timeout_seconds=90)
    runtime = AgentRuntime(platform=platform, store=FileAgentSessionStore(tmp_path / "model-state"),
        tools=loom_default_tools(), sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF),
        browser_backend_factory=backend, browser_security_policy=BrowserSecurityPolicy(resolve_dns=False, allow_private_networks=True),
        auto_configure_browser=False, auto_configure_computer=False, auto_configure_web_search=False, mcp_servers=(),
        limits=AgentLimits(max_model_steps=18, max_tool_calls=30, transport_retries=0))
    try:
        session = runtime.create_session("agent.fast", workspace_dir=project, permission_mode="full-access")
        result = runtime.start_turn(session.session_id,
            f"The action button at http://127.0.0.1:{server.server_port}/index.html is visibly cut off at the bottom. "
            "Fix the local index.html so the entire button is visible, keeping its size and click behavior.")
        events = runtime.store.events(session.session_id)
        terminal = next((e for e in reversed(events) if e.kind is E.TURN_COMPLETED), None)
        report = {"status": result.status.value, "steps": result.model_steps,
                  "verification": terminal.data.get("development_verification") if terminal else None,
                  "tools": [e.data.get("tool") for e in events if e.kind is E.TOOL_REQUESTED],
                  "recovery_requests": sum(e.kind is E.VERIFICATION_FINISH_REQUESTED for e in events)}
        (tmp_path / "live-model-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        assert result.status is AgentStatus.COMPLETED, report
        assert report["verification"]["status"] == "passed", report
        assert any(name in report["tools"] for name in ("browser_screenshot", "browser_run_check", "view_images")), report
        # Independent browser oracle, not the model's own test or final prose.
        verifier = backend(None)
        try:
            verifier.start()
            verifier.navigate(f"http://127.0.0.1:{server.server_port}/index.html")
            observed = verifier.evaluate("(() => { const b=document.querySelector('button'), r=b.getBoundingClientRect(); "
                "return r.height >= 40 && document.elementFromPoint(r.x+r.width/2,r.bottom-2)?.closest('button') === b; })()")
            assert observed["value"] is True, report
        finally:
            verifier.close()
    finally:
        runtime.close()
        server.shutdown()
        server.server_close()
