"""The browser suite through the running Loom desktop app, as a person would send it.

    python scripts/browser_suite/run_app.py OUT_DIR [--tasks a,b] [--repeat N] [--label X] > OUT.log

Each task becomes a conversation in the user's running Loom (local App Server IPC, the same Host, model,
settings and browser route the app uses - with the extension connected, the user's own browser, in tabs
Loom opens and groups). The test app is scripts/browser_suite/suite_site.py on 127.0.0.1, so the app's
"allow private networks" setting must be on. Conversations are archived when a task ends. Consumes the
user's model quota. One content-free JSON line per run, like run_suite.py, so compare.py reads both.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("out")
parser.add_argument("--tasks", default="login,shop,wizard,feed,newtab,iframe,extract,widget,modal")
parser.add_argument("--repeat", type=int, default=1)
parser.add_argument("--label", default="app")
parser.add_argument("--timeout", type=float, default=600.0, help="seconds before a task is interrupted")
args = parser.parse_args()

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.app_server_local_ipc import LoomLocalAppServerClient  # noqa: E402
from suite_tasks import PROMPTS, TAIL, check, metrics  # noqa: E402
from suite_site import Site, serve  # noqa: E402

OUT = Path(args.out)
workspace = (OUT / "workspace").resolve()
workspace.mkdir(parents=True, exist_ok=True)
client = LoomLocalAppServerClient(request_timeout_seconds=60)
HOME = client.runtime_home / "agent_runtime" / "sessions"
client.connect_and_initialize(client_name="loom-browser-suite")


for _ in range(args.repeat):
    for task in args.tasks.split(","):
        site = Site()
        server = serve(site)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        thread_id, status, error, final_text, read = "", "error", "", "", {}
        started = time.perf_counter()
        try:
            thread_id = client.thread_start(workspace=workspace, permission_mode="full-access")["thread"]["id"]
            client.turn_start(thread_id, PROMPTS[task].format(base=base) + TAIL)
            time.sleep(2)
            while True:
                read = client.thread_read(thread_id)
                status = str(read["thread"].get("status") or "")
                if read.get("pendingApproval"):
                    status, error = "approval", "the turn asked for an approval"
                    break
                if status not in {"running", "queued", "starting", "pending", "active"}:
                    break
                if time.perf_counter() - started > args.timeout:
                    status, error = "timeout", f"no end after {args.timeout:.0f}s"
                    break
                time.sleep(1)
            final_text = str(read.get("finalText") or "")
            if status in {"approval", "timeout"} and read["thread"].get("currentTurnId"):
                client.turn_interrupt(thread_id, read["thread"]["currentTurnId"])
        except Exception as exc:
            error = f"{type(exc).__name__}: {str(exc)[:160]}"
        wall = round(time.perf_counter() - started, 1)
        mismatch = check(task, site.expected()[task], site, final_text)
        server.shutdown()
        if thread_id:
            try:
                client.request("thread/archive", {"threadId": thread_id})
            except Exception:
                pass
        print(json.dumps({"label": args.label, "task": task, "passed": not mismatch, "status": status,
                          "error": error, "wall_s": wall, "mismatch": mismatch, "session": thread_id[:8],
                          **(metrics(events) if thread_id and (events := HOME / thread_id / "events.jsonl").exists()
                             else {})}, ensure_ascii=False), flush=True)
client.close()
