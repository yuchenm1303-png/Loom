"""Live Computer Use suite: real model, real Windows operator, throwaway local apps.

    python scripts/computer_use_suite/run_suite.py OUT_DIR MODEL --stored-model ID [--provider opencode-go]
        [--tasks a,b] [--modes batch,single] [--repeat N] [--label X] [--extra-site DIR] > OUT.log
    python scripts/computer_use_suite/compare.py A.log B.log      # per-task pass, time, steps, failed calls
    python scripts/computer_use_suite/digest.py OUT_DIR/state     # call-by-call view of every run

THIS DRIVES THE REAL MOUSE AND KEYBOARD for several minutes; leave the desktop alone. Each task opens its
own throwaway window (tkinter apps from suite_app.py / form_app.py, or Edge with a temporary profile for
`web`), checks the facts the app wrote to a result file - never the model's own report - and closes it.
Output is one content-free JSON line per run: pass/fail, wall time, model steps, failed calls, batch ends.
The model must accept images; `--stored-model` names an entry of Loom's model store, whose key is read
from the OS credential store and never printed. pywinauto is optional (without it UIA hints are empty);
`--extra-site` appends a site-packages directory that has it. Consumes model quota.
Introduced 2026-11-01; that pass's results are in docs/computer-use-v1.md.
"""
import argparse
import json
import os
import random
import subprocess
import sys
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("out")
parser.add_argument("model")
parser.add_argument("--tasks", default="fill,settings,covered,export,list,seat,code,web,rename")
parser.add_argument("--modes", default="batch")
parser.add_argument("--repeat", type=int, default=1)
parser.add_argument("--label", default="")
parser.add_argument("--max-steps", type=int, default=24)
parser.add_argument("--stored-model", required=True, help="model store entry whose key and base URL are used")
parser.add_argument("--provider", default="opencode-go")
parser.add_argument("--extra-site", default="", help="a site-packages directory to append, for pywinauto")
args = parser.parse_args()

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
if args.extra_site:
    sys.path.append(args.extra_site)

from loom_cli import _build_runtime, build_parser  # noqa: E402
from app.ai import AGENT_FAST_ROLE  # noqa: E402
from app.ai.model_store import ModelConfigStore  # noqa: E402
from app.agent_runtime.computer_single_loop_runtime import SingleLoopComputerRuntime, _single_action_schema, _schema  # noqa: E402
from app.agent_runtime.computer_windows_single_loop import SingleLoopWindowsOperator  # noqa: E402
from app.agent_runtime.contracts import AgentLimits, PermissionMode  # noqa: E402
from app.agent_runtime.sandbox import SandboxManager, SandboxPolicy  # noqa: E402
from app.agent_runtime.storage import FileAgentSessionStore  # noqa: E402
from app.agent_runtime.workspace_tools import loom_default_tools  # noqa: E402
from app.agent_runtime.tools import ToolRegistry  # noqa: E402
from computer_use_report import turns  # noqa: E402

HERE = Path(__file__).parent
OUT = Path(args.out)
OUT.mkdir(parents=True, exist_ok=True)
PYTHONW = str(Path(sys.executable).with_name("pythonw.exe"))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
TAIL = " Use the computer_action tool only (no exec, no files)."


def tk_app(mode, result, extra=""):
    command = [PYTHONW, str(HERE / "suite_app.py"), mode, str(result)] + ([extra] if extra else [])
    return subprocess.Popen(command)


class WebServer:
    def __init__(self, result):
        page = (HERE / "web_form.html").read_bytes()
        result_path = result

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("content-length") or 0))
                Path(result_path).write_bytes(body)
                self.send_response(204)
                self.end_headers()

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.port = self.server.server_address[1]

    def kill(self):
        self.server.shutdown()


class Edge:
    def __init__(self, url, profile):
        self.proc = subprocess.Popen([EDGE, f"--user-data-dir={profile}", f"--app={url}", "--no-first-run",
                                      "--no-default-browser-check", "--disable-extensions", "--disable-sync", "--window-size=640,860",
                                      "--window-position=320,40", "--disable-features=Translate"])

    def kill(self):
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.proc.pid)], capture_output=True)


def setup(task, result):
    """Start a task's apps; returns (prompt, expected, things to close)."""

    if task == "fill":
        return ("A desktop window titled 'Loom Form Test' is open. In it, set Full name to 'Ada Lovelace', Email to "
                "'ada@example.com' and City to 'London', tick 'Subscribe to newsletter', then click Submit. Stop when "
                "the window shows 'Submitted'.",
                {"submitted": True, "name": "Ada Lovelace", "email": "ada@example.com", "city": "London",
                 "subscribe": True},
                [subprocess.Popen([PYTHONW, str(HERE / "form_app.py"), str(result)])])
    if task == "settings":
        return ("A desktop window titled 'Loom Form Test' is open. Click 'Settings...', turn on 'Dark mode' in the dialog "
                "and click OK; then set Full name to 'Grace Hopper' and click Submit. Stop when the window shows "
                "'Submitted'.",
                {"submitted": True, "name": "Grace Hopper", "dark_mode": True},
                [subprocess.Popen([PYTHONW, str(HERE / "form_app.py"), str(result)])])
    if task == "covered":
        form = subprocess.Popen([PYTHONW, str(HERE / "form_app.py"), str(result)])
        time.sleep(2.0)
        cover = subprocess.Popen([PYTHONW, str(HERE / "cover_app.py")])
        return ("A desktop window titled 'Loom Form Test' is open somewhere on this desktop. In it, set Full name to "
                "'Alan Turing' and City to 'Wilmslow', then click Submit. Stop when the window shows 'Submitted'.",
                {"submitted": True, "name": "Alan Turing", "city": "Wilmslow"}, [form, cover])
    if task == "export":
        folder = OUT / "exports"
        folder.mkdir(exist_ok=True)
        target = folder / f"q3-{random.randint(100, 999)}.txt"
        target.unlink(missing_ok=True)
        return (f"The 'Report Exporter' window is open. Export the report and save it as exactly {target} "
                "(use that full path in the save dialog). Stop when the window shows 'Exported'.",
                {"exported_to": str(target)}, [tk_app("export", result)])
    if task == "list":
        return ("In the 'Pick a Country' window, select Portugal in the list and click Choose. Stop when the window "
                "shows 'Chosen: Portugal'.", {"chosen": "Portugal"}, [tk_app("list", result)])
    if task == "seat":
        return ("In the 'Seat Picker' window, select seat F7 and then click Confirm. Stop when it shows "
                "'Confirmed F7'.", {"confirmed": "F7"}, [tk_app("seat", result)])
    if task == "code":
        code = "".join(random.choice("ACEFHJKMNPRTWXY3479") for _ in range(6))
        source = tk_app("source", OUT / "unused.json", code)
        time.sleep(1.5)
        target = tk_app("target", result)
        return ("Two windows are open: 'Verification Code' shows a code. Enter that exact code into the "
                "'Enter Code' window and click Submit. Stop when it shows 'Submitted'.", {"code": code}, [source, target])
    if task == "web":
        server = WebServer(result)
        edge = Edge(f"http://127.0.0.1:{server.port}/", OUT / "edge-profile")
        time.sleep(3.0)
        return ("A browser window titled 'Shipping Form' is open. Fill it in: destination country Norway, speed "
                "Express, quantity 3, delivery date 24 December 2026, tick Gift wrap, then click 'Place order'. Stop "
                "when the page shows 'Order placed'.",
                {"country": "Norway", "speed": "express", "quantity": "3", "date": "2026-12-24", "gift": True},
                [edge, server])
    if task == "rename":
        return ("In the 'Project Settings' window, rename the project to exactly 'Loom Evaluation' (replacing the "
                "current name entirely) and click Save. Stop when it shows 'Saved'.",
                {"name": "Loom Evaluation"}, [tk_app("rename", result)])
    raise SystemExit(f"unknown task {task}")


def check(expected, got):
    def same(a, b):
        if isinstance(a, str) and isinstance(b, str):
            return a.strip().casefold() == b.strip().casefold()
        return a == b
    return sorted(k for k, v in expected.items() if not same(v, got.get(k)))


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
    auto_configure_web_search=False, auto_configure_browser=False,
    computer_operator=SingleLoopWindowsOperator(), auto_configure_computer=False,
)
runtime.limits = AgentLimits(max_model_steps=args.max_steps, max_tool_calls=args.max_steps * 2, model_retries=2,
                             transport_retries=2, max_recovery_seconds=60)
batch_tool = runtime.tools.get("computer_action")
single_tool = replace(batch_tool, input_schema=_schema({"action": _single_action_schema()}, ("action",)))


def use_tool(tool):
    runtime.tools = ToolRegistry(tuple(tool if t.name == "computer_action" else t for t in runtime.tools.all()))


for _ in range(args.repeat):
    for task in args.tasks.split(","):
        for mode in args.modes.split(","):
            use_tool(batch_tool if mode == "batch" else single_tool)
            result = OUT / f"result-{task}.json"
            result.unlink(missing_ok=True)
            prompt, expected, things = setup(task, result)
            time.sleep(2.0)
            workspace = OUT / "workspace"
            workspace.mkdir(exist_ok=True)
            session = runtime.create_session(AGENT_FAST_ROLE.role_id, workspace_dir=workspace,
                                             permission_mode=PermissionMode.FULL_ACCESS)
            started = time.perf_counter()
            error = ""
            try:
                status = runtime.start_turn(session.session_id, prompt + TAIL).status.value
            except Exception as exc:
                status, error = "error", f"{type(exc).__name__}: {str(exc)[:160]}"
            wall = round(time.perf_counter() - started, 1)
            try:
                got = json.loads(result.read_text(encoding="utf-8"))
            except Exception:
                got = {}
            mismatch = check(expected, got)
            for thing in things:
                try:
                    thing.kill()
                except Exception:
                    pass
            time.sleep(1.0)
            rows = [row for row in turns(home / "agent_runtime" / "sessions") if row["session"] == session.session_id]
            row = rows[0] if rows else {}
            print(json.dumps({
                "label": args.label, "task": task, "mode": mode, "model": args.model, "passed": not mismatch,
                "status": status, "error": error, "wall_s": wall, "model_steps": row.get("model_steps"),
                "computer_calls": row.get("calls"), "desktop_actions": row.get("actions"),
                "failed_calls": row.get("failed_calls"), "model_ms": round(row.get("model_ms", 0)),
                "tool_ms": round(row.get("tool_ms", 0)), "batch_ends": dict(row.get("batch_ends", {})),
                "mismatch": mismatch, "session": session.session_id[:8],
            }, ensure_ascii=False), flush=True)
runtime.close()
