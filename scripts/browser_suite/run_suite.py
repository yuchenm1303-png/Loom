"""Live browser-automation suite: real model, Loom's browser tools, a local test web app.

    python scripts/browser_suite/run_suite.py OUT_DIR MODEL --stored-model ID [--provider opencode-go]
        [--tasks a,b] [--repeat N] [--label X] [--browser PATH] > OUT.log
    python scripts/browser_suite/compare.py A.log B.log
    python scripts/browser_suite/digest.py OUT_DIR/state

Each task opens the test app (scripts/browser_suite/suite_site.py, 127.0.0.1 only) through Loom's isolated
browser-use backend - a separate visible browser with a temporary profile, never the user's own - and is
checked from what the page reported to the server (or, for the extraction task, the number in the final
answer). It does not use the mouse or keyboard. Needs browser-use installed for the interpreter; the model
must be an entry of Loom's model store. Consumes model quota. One content-free JSON line per run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("out")
parser.add_argument("model")
parser.add_argument("--stored-model", required=True)
parser.add_argument("--provider", default="opencode-go")
parser.add_argument("--tasks", default="login,shop,wizard,feed,newtab,iframe,extract,widget,modal")
parser.add_argument("--repeat", type=int, default=1)
parser.add_argument("--label", default="")
parser.add_argument("--max-steps", type=int, default=30)
parser.add_argument("--browser", default=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    help="browser executable for the isolated backend (a temporary profile is used)")
parser.add_argument("--headless", action="store_true")
parser.add_argument("--backend", choices=("isolated", "extension"), default="isolated",
                    help="extension: a throwaway Chrome profile with a copy of the Loom extension, paired to a "
                         "suite-only bridge port; the user's Chrome and Loom's own bridge are not touched")
parser.add_argument("--bridge-port", type=int, default=39333)
args = parser.parse_args()

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LOOM_BUNDLED_BROWSER", args.browser)

extension_chrome = None
if args.backend == "extension":
    import secrets
    import shutil
    import subprocess

    work = Path(args.out).resolve()
    copy = work / "extension"
    shutil.rmtree(copy, ignore_errors=True)
    shutil.copytree(ROOT / "extensions" / "browser-current-tab", copy)
    token = secrets.token_urlsafe(24)
    (copy / "bridge-config.json").write_text(json.dumps(
        {"bridgeUrl": f"http://127.0.0.1:{args.bridge_port}", "token": token}), encoding="utf-8")
    os.environ.update({"LOOM_BROWSER_BACKEND": "extension", "LOOM_BROWSER_EXTENSION_PORT": str(args.bridge_port),
                       "LOOM_BROWSER_EXTENSION_TOKEN": token})
    profile = work / "chrome-profile"
    shutil.rmtree(profile, ignore_errors=True)
    # Branded Chrome ignores --load-extension unless this feature is switched off.
    extension_chrome = subprocess.Popen([args.browser, f"--user-data-dir={profile}", f"--load-extension={copy}",
                                         "--disable-features=DisableLoadExtensionCommandLineSwitch",
                                         "--no-first-run", "--no-default-browser-check", "about:blank"])

from loom_cli import _build_runtime, build_parser  # noqa: E402
from app.ai import AGENT_FAST_ROLE  # noqa: E402
from app.ai.model_store import ModelConfigStore  # noqa: E402
from app.agent_runtime.computer_single_loop_runtime import SingleLoopComputerRuntime  # noqa: E402
from app.agent_runtime.contracts import AgentLimits, PermissionMode  # noqa: E402
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy  # noqa: E402
from app.agent_runtime.storage import FileAgentSessionStore  # noqa: E402
from app.agent_runtime.workspace_tools import loom_default_tools  # noqa: E402
from suite_site import Site, serve  # noqa: E402

OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)
TAIL = " Use the browser tools only (no exec, no computer_action). Stop when the task is done."

PROMPTS = {
    "login": "Open {base}/login in the browser. Sign in with username tester and password orange-42 (a test "
             "account of this local app), turn on Email notifications and save the settings.",
    "shop": "On {base}/shop, add the cheapest item in the Garden category to the cart, then check out with full "
            "name Ada Lovelace and Express shipping.",
    "wizard": "Sign up at {base}/wizard with email grace@example.com and the Team plan, accepting the terms.",
    "feed": "On {base}/feed, click the Select button of Item 73.",
    "newtab": "On {base}/docs, get the API key from the developer page (its link opens a new tab) and save it in "
              "the API key field on the docs page.",
    "iframe": "On {base}/embed, pay 25 with card holder Grace Hopper.",
    "extract": "On {base}/orders, what is the total of all orders by Bob Ng dated in October 2026? Reply with the "
               "amount.",
    "widget": "On {base}/widget, set the quantity to 4 and apply it.",
    "modal": "On {base}/drafts, delete the draft named 'Old notes' and nothing else.",
}


def check(task, expected, site, final_text):
    if task == "extract":
        numbers = {round(float(n.replace(",", "")), 2) for n in re.findall(r"\d[\d,]*\.?\d*", final_text or "")}
        return [] if round(float(expected["answer"]), 2) in numbers else ["answer"]
    got = site.results.get(task, {})
    return sorted(k for k, v in expected.items()
                  if (str(got.get(k)).strip().casefold() if isinstance(v, str) else got.get(k)) !=
                  (v.casefold() if isinstance(v, str) else v))


def metrics(events_path):
    steps, calls, failed, model_ms, tool_ms, started = 0, 0, 0, 0.0, 0.0, {}
    by_tool, fail_kinds = {}, {}
    for line in events_path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        d = e.get("data") or {}
        if e["kind"] == "model_response":
            steps += 1
            model_ms += float(d.get("model_execution_ms") or 0)
        elif e["kind"] == "tool_requested":
            calls += 1
            by_tool[d.get("tool")] = by_tool.get(d.get("tool"), 0) + 1
        elif e["kind"] == "tool_started":
            started[d.get("call_id")] = dt.datetime.fromisoformat(e["created_at"])
        elif e["kind"] in {"tool_completed", "tool_failed"}:
            if d.get("call_id") in started:
                tool_ms += (dt.datetime.fromisoformat(e["created_at"]) - started[d["call_id"]]).total_seconds() * 1000
            if e["kind"] == "tool_failed" or d.get("ok") is False:
                failed += 1
                key = f"{d.get('tool')}: {str(d.get('error') or d.get('content') or '')[:60]}"
                fail_kinds[key] = fail_kinds.get(key, 0) + 1
    return {"model_steps": steps, "tool_calls": calls, "failed_calls": failed, "model_ms": round(model_ms),
            "tool_ms": round(tool_ms), "by_tool": by_tool, "failures": fail_kinds}


store_models = ModelConfigStore()
entry = store_models.get(args.stored_model)
secret = store_models.secret_for(entry)
runtime_args = build_parser().parse_args(["--provider", args.provider, "--base-url", entry.base_url, "--model",
                                          args.model, "--home", str(OUT / "throwaway"), "--vision", "--timeout", "120"])
os.environ["LOOM_API_KEY"] = secret
base_runtime, _, _ = _build_runtime(runtime_args)
os.environ.pop("LOOM_API_KEY", None)
secret = None

home = OUT / "state"
runtime = SingleLoopComputerRuntime(
    platform=base_runtime.platform, store=FileAgentSessionStore(home), tools=loom_default_tools(),
    sandbox_manager=SandboxManager(policy=SandboxPolicy.OFF), web_search_provider=None,
    auto_configure_web_search=False, auto_configure_browser=True, browser_headless=args.headless,
    browser_persist_profile=False, auto_configure_computer=False,
)
runtime.browser_set_private_networks(True)  # the test app is on 127.0.0.1
runtime.limits = AgentLimits(max_model_steps=args.max_steps, max_tool_calls=args.max_steps * 2, model_retries=2,
                             transport_retries=2, max_recovery_seconds=60)

for _ in range(args.repeat):
    for task in args.tasks.split(","):
        site = Site()
        server = serve(site)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        workspace = OUT / "workspace"
        workspace.mkdir(exist_ok=True)
        session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace,
                                         permission_mode=PermissionMode.FULL_ACCESS)
        started = time.perf_counter()
        error, final_text = "", ""
        try:
            result = runtime.start_turn(session.session_id, PROMPTS[task].format(base=base) + TAIL)
            status, final_text = result.status.value, result.final_text or ""
        except Exception as exc:
            status, error = "error", f"{type(exc).__name__}: {str(exc)[:160]}"
        wall = round(time.perf_counter() - started, 1)
        mismatch = check(task, site.expected()[task], site, final_text)
        try:
            runtime.release_session_resources(session.session_id, reason="suite task finished")
        except Exception:
            pass
        server.shutdown()
        events = home / "agent_runtime" / "sessions" / session.session_id / "events.jsonl"
        row = metrics(events) if events.exists() else {}
        print(json.dumps({"label": args.label, "task": task, "model": args.model, "passed": not mismatch,
                          "status": status, "error": error, "wall_s": wall, "mismatch": mismatch,
                          "session": session.session_id[:8], **row}, ensure_ascii=False), flush=True)
runtime.close()
if extension_chrome is not None:
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(extension_chrome.pid)], capture_output=True)
