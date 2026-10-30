"""Live test of Weave mode: a real Host and model behind the real Weave UI, in an isolated root.

The desktop app needs a signed-in account; this test does not sign in. Instead it runs the
same Host code in this process (isolated home, a new Git repository, the enforced workspace
sandbox, the configured model from the OS credential store) and serves the project calls the
Weave UI makes (projectRun/overview, projectProgram/create, projectRun/steer, pause, resume,
cancel) on a token-protected 127.0.0.1 bridge. scripts/fixtures/weave-live.html renders the
real ProjectMode component against it, so the program is started, watched and steered from the
UI. Kill this process and start it again with --resume to test a Host restart.

    python scripts/weave_live.py --root <new dir> --stored-model <id> [--provider opencode-go]
    python scripts/weave_live.py --root <same dir> --stored-model <id> --resume
    python scripts/weave_live.py --root <same dir> --judge

The objective (objective.txt) needs more than one milestone; --judge runs a fixed external
oracle on the integrated repository. Consumes model quota.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from smoke_project_runner import QualificationService, probe_workspace_sandbox  # noqa: E402
from loom_cli import _build_runtime, build_parser  # noqa: E402
from app.ai.model_store import ModelConfigStore  # noqa: E402
from app.agent_runtime import PermissionMode, sandbox_compat  # noqa: E402
from app.agent_runtime.contracts import AgentLimits  # noqa: E402
from app.agent_runtime.sandbox import SandboxManager  # noqa: E402
from app.project_runner import program  # noqa: E402
from app.project_runner.review import git  # noqa: E402

FIXTURE_ORIGIN = "http://127.0.0.1:5199"

OBJECTIVE = """Build `ledger`, a standard-library-only Python 3 package (a ledger/ directory with __init__.py and
__main__.py) run from the project root as `python -m ledger COMMAND ...`.

Input files are UTF-8 text (an optional BOM, LF or CRLF line ends) with one entry per line:
DATE,ACCOUNT,AMOUNT[,CATEGORY]. Blank lines and lines whose first non-space character is # are ignored;
fields are trimmed of surrounding whitespace. DATE is a real calendar date written YYYY-MM-DD. ACCOUNT is
nonempty and uses only letters, digits, _ and -. AMOUNT is an optional leading -, digits, and optionally a .
followed by one or two digits (no exponent, no +, no thousands separators). CATEGORY is optional; when it is
missing or empty the entry's category is "uncategorized". Any other line is invalid.

Errors: an invalid line, a missing or unreadable file, an invalid budgets file, or bad arguments (an unknown
command, a missing required option, a malformed date or month) exit with status 2, print nothing on stdout,
and print a message on stderr; for an invalid line the message contains "line N", its 1-based physical line.

Every command except `convert --to tsv` prints exactly one JSON object on stdout. Amounts are strings with
exactly two decimals ("-3.50", "0.00"), computed with exact decimal arithmetic.
1. `balance FILE [--account NAME] [--since YYYY-MM-DD] [--until YYYY-MM-DD]` prints
   {"balances": {ACCOUNT: amount, ...}, "total": amount}: per-account sums of the matching entries (date
   bounds inclusive, --account keeps one account); accounts without matching entries are omitted; total is
   the sum of the balances shown.
2. `monthly FILE [--account NAME]` prints {"months": {"YYYY-MM": {"in": amount, "out": amount, "net": amount}}}
   for every month with entries: in sums positive amounts, out sums negative amounts as a positive number,
   net = in - out.
3. `categories FILE --month YYYY-MM` prints {"month": "YYYY-MM", "categories": {CATEGORY: amount}, "total": amount}:
   the sums of that month's entries by category; total is their sum.
4. `budget FILE --budgets BUDGETS --month YYYY-MM`: BUDGETS is a JSON file mapping category to a positive limit
   string with at most two decimals ("120.00", "50"). Prints {"month": "YYYY-MM", "categories": {CATEGORY:
   {"spent": amount, "limit": amount, "remaining": amount, "over": true|false}}} for every category in the
   budgets file: spent is the sum of that category's negative amounts in the month as a positive number,
   remaining = limit - spent, over is spent > limit.
5. `convert FILE --to json|tsv`: json prints {"entries": [{"date", "account", "amount", "category"}, ...]} in
   file order (category filled in as above); tsv prints a header line date<TAB>account<TAB>amount<TAB>category
   followed by one line per entry, nothing else.
Also: a unittest suite under tests/ runnable with `python -m unittest` that covers every command and the error
rules, and a README documenting every command with an example.

Work only in this project; do not install dependencies, access the network, read credentials or modify files
outside it. Do not execute Git commands; the Host owns Git delivery and integration. Do not start servers.
Runtime Python executable (use this exact argv[0], not python/py or interpreter discovery): {python}
"""

SAMPLE = ("# sample ledger\n2026-01-05,cash,12.50,food\n2026-01-06, bank ,100,salary\n2026-01-20,cash,-30,food\n\n"
          "2026-02-01,cash,-2.25,\n2026-02-03,card-1,-40.10,travel\n2026-02-10,bank,-60,rent\n2026-02-15,cash,5\n")


def oracle(workspace: Path):
    """Fixed external checks with hand-computed answers; inputs live outside the workspace."""
    def bal(balances, total):
        return {"balances": balances, "total": total}
    expected_entries = [
        ["2026-01-05", "cash", "12.50", "food"], ["2026-01-06", "bank", "100.00", "salary"],
        ["2026-01-20", "cash", "-30.00", "food"], ["2026-02-01", "cash", "-2.25", "uncategorized"],
        ["2026-02-03", "card-1", "-40.10", "travel"], ["2026-02-10", "bank", "-60.00", "rent"],
        ["2026-02-15", "cash", "5.00", "uncategorized"]]
    def budget(month, rows):
        return {"month": month, "categories": {name: {"spent": s, "limit": l, "remaining": r, "over": o}
                                               for name, (s, l, r, o) in rows.items()}}
    cases = [
        ("balance", ["balance", "{sample}"], bal({"cash": "-14.75", "bank": "40.00", "card-1": "-40.10"}, "-14.85")),
        ("balance account", ["balance", "{sample}", "--account", "cash"], bal({"cash": "-14.75"}, "-14.75")),
        ("balance since", ["balance", "{sample}", "--since", "2026-02-01"], bal({"cash": "2.75", "card-1": "-40.10", "bank": "-60.00"}, "-97.35")),
        ("balance until", ["balance", "{sample}", "--until", "2026-01-31"], bal({"cash": "-17.50", "bank": "100.00"}, "82.50")),
        ("balance window", ["balance", "{sample}", "--since", "2026-01-06", "--until", "2026-02-03"],
         bal({"bank": "100.00", "cash": "-32.25", "card-1": "-40.10"}, "27.65")),
        ("balance absent", ["balance", "{sample}", "--account", "nobody"], bal({}, "0.00")),
        ("monthly", ["monthly", "{sample}"], {"months": {"2026-01": {"in": "112.50", "out": "30.00", "net": "82.50"},
                                                         "2026-02": {"in": "5.00", "out": "102.35", "net": "-97.35"}}}),
        ("monthly account", ["monthly", "{sample}", "--account", "cash"],
         {"months": {"2026-01": {"in": "12.50", "out": "30.00", "net": "-17.50"}, "2026-02": {"in": "5.00", "out": "2.25", "net": "2.75"}}}),
        ("categories feb", ["categories", "{sample}", "--month", "2026-02"],
         {"month": "2026-02", "categories": {"uncategorized": "2.75", "travel": "-40.10", "rent": "-60.00"}, "total": "-97.35"}),
        ("categories jan", ["categories", "{sample}", "--month", "2026-01"],
         {"month": "2026-01", "categories": {"food": "-17.50", "salary": "100.00"}, "total": "82.50"}),
        ("categories empty month", ["categories", "{sample}", "--month", "2026-03"], {"month": "2026-03", "categories": {}, "total": "0.00"}),
        ("budget feb", ["budget", "{sample}", "--budgets", "{budgets}", "--month", "2026-02"],
         budget("2026-02", {"food": ("0.00", "20.00", "20.00", False), "rent": ("60.00", "50.00", "-10.00", True),
                            "travel": ("40.10", "100.00", "59.90", False)})),
        ("budget jan", ["budget", "{sample}", "--budgets", "{budgets}", "--month", "2026-01"],
         budget("2026-01", {"food": ("30.00", "20.00", "-10.00", True), "rent": ("0.00", "50.00", "50.00", False),
                            "travel": ("0.00", "100.00", "100.00", False)})),
        ("convert json", ["convert", "{sample}", "--to", "json"],
         {"entries": [dict(zip(("date", "account", "amount", "category"), row)) for row in expected_entries]}),
        ("convert tsv", ["convert", "{sample}", "--to", "tsv"],
         "\n".join(["date\taccount\tamount\tcategory", *("\t".join(row) for row in expected_entries)])),
        ("invalid date", ["balance", "{bad_date}"], ("error", 2)),
        ("invalid amount", ["balance", "{bad_amount}"], ("error", 3)),
        ("missing file", ["balance", "{missing}"], ("error", None)),
        ("bad budgets", ["budget", "{sample}", "--budgets", "{bad_budgets}", "--month", "2026-02"], ("error", None)),
        ("bad month", ["categories", "{sample}", "--month", "2026-13"], ("error", None)),
        ("unknown command", ["frobnicate", "{sample}"], ("error", None)),
    ]
    # -m needs the project root on the path, so not -I; nothing else of this process leaks in.
    clean = {key: value for key, value in os.environ.items() if key.upper() not in {"PYTHONPATH", "PYTHONSAFEPATH", "PYTHONHOME"}}
    clean["PYTHONIOENCODING"] = "utf-8"
    results = []
    with tempfile.TemporaryDirectory(prefix="weave-oracle-") as folder:
        base = Path(folder)
        files = {"sample": SAMPLE, "bad_date": "2026-01-01,cash,1\n2026-02-30,cash,1\n",
                 "bad_amount": "# c\n2026-01-01,cash,1\n2026-01-02,cash,1.234\n",
                 "budgets": json.dumps({"food": "20.00", "rent": "50", "travel": "100.00"}), "bad_budgets": "{not json"}
        paths = {name: base / f"{name}.txt" for name in files}
        for name, text in files.items():
            paths[name].write_text(text, encoding="utf-8")
        paths["missing"] = base / "missing.txt"
        for name, argv, expected in cases:
            args = [sys.executable, "-B", "-m", "ledger", *(a.format(**{k: str(v) for k, v in paths.items()}) for a in argv)]
            try:
                run = subprocess.run(args, cwd=workspace, capture_output=True, text=True, encoding="utf-8", errors="replace",
                                     env=clean, timeout=20)
            except subprocess.TimeoutExpired:
                results.append({"case": name, "passed": False, "why": "timeout"})
                continue
            if isinstance(expected, tuple):
                line = expected[1]
                passed = run.returncode == 2 and not run.stdout.strip() and (line is None or f"line {line}" in run.stderr)
            elif isinstance(expected, str):
                passed = run.returncode == 0 and run.stdout.replace("\r\n", "\n").strip() == expected
            else:
                try:
                    passed = run.returncode == 0 and json.loads(run.stdout) == expected
                except ValueError:
                    passed = False
            results.append({"case": name, "passed": passed, "exit": run.returncode,
                            **({} if passed else {"stdout": run.stdout[:300], "stderr": run.stderr[-300:]})})
    return results


def prepare(root: Path):
    sandbox = probe_workspace_sandbox(root / "workspace")
    root.mkdir(parents=True)
    compat = sandbox_compat.run_suite(SandboxManager(), root / "sandbox-compat")
    failures = [item["name"] for item in sandbox_compat.failures(compat)]
    if failures:
        raise SystemExit("Sandbox compatibility failed before any model call: " + ", ".join(failures))
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("# ledger\n\nPython executable for checks: " + sys.executable + "\n", encoding="utf-8")
    (workspace / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    git(workspace, "init", "-b", "main")
    who = ["-c", "user.name=Loom Weave Test", "-c", "user.email=weave@localhost"]
    git(workspace, *who, "add", ".")
    git(workspace, *who, "commit", "-m", "Baseline")
    (root / "objective.txt").write_text(OBJECTIVE.replace("{python}", json.dumps(sys.executable)), encoding="utf-8")
    return sandbox


def build_service(root: Path, stored_model: str, provider: str | None):
    models = ModelConfigStore()
    entry = models.get(stored_model)
    secret = models.secret_for(entry)  # resolved for this process only; never printed or stored
    runtime_args = build_parser().parse_args(["--provider", provider or entry.adapter.value, "--base-url", entry.base_url,
        "--model", entry.model, "--home", str(root / "state"), "--no-vision", "--timeout", "60"])
    old_key, old_path = os.environ.get("LOOM_API_KEY"), os.environ.pop("PYTHONPATH", None)
    try:
        os.environ["LOOM_API_KEY"] = secret
        runtime, store, model = _build_runtime(runtime_args)
    finally:
        if old_key is None:
            os.environ.pop("LOOM_API_KEY", None)
        else:
            os.environ["LOOM_API_KEY"] = old_key
        if old_path is not None:
            os.environ["PYTHONPATH"] = old_path
        secret = None
    runtime.limits = AgentLimits(max_model_steps=40, max_tool_calls=80, model_retries=2, transport_retries=2, max_recovery_seconds=90)
    service = QualificationService(runtime=runtime, store=store, model=model, default_workspace=root / "workspace")
    return service, runtime, entry.model


def serve(service, root: Path, thread_id: str, model: str, token: str, port: int):
    """The project calls of the Weave UI, for one page origin, with a per-run token."""
    calls = {
        "projectRun/overview": lambda p: service.project_overview(p),
        "projectRun/steer": lambda p: {"run": service.project_steer(p)},
        "projectRun/pause": lambda p: {"run": service.project_control(p, "pause")},
        "projectRun/resume": lambda p: {"run": service.project_control(p, "resume")},
        "projectRun/cancel": lambda p: {"run": service.project_control(p, "cancel")},
    }
    def create(params):
        with service.project_runner.guard:
            return {"program": program.create(service, params)}
    calls["projectProgram/create"] = create
    log = (root / "bridge.log").open("a", encoding="utf-8")

    class Handler(BaseHTTPRequestHandler):
        def _headers(self, status=200):
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", FIXTURE_ORIGIN)
            self.send_header("Access-Control-Allow-Headers", "content-type, x-weave-token")
            self.send_header("Content-Type", "application/json")
            self.end_headers()

        def do_OPTIONS(self):  # noqa: N802
            self._headers(204)

        def do_POST(self):  # noqa: N802
            if self.headers.get("Origin") != FIXTURE_ORIGIN or self.headers.get("x-weave-token") != token:
                self._headers(403)
                return
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            method, params = body.get("method"), body.get("params") or {}
            if method not in calls:
                self._headers(404)
                self.wfile.write(b'{"error":"unknown method"}')
                return
            if method == "projectProgram/create":
                params["threadId"] = thread_id
            try:
                result = calls[method](params)
                status = 200
            except Exception as exc:  # the UI shows the message, as it does for the Host's errors
                result, status = {"error": f"{type(exc).__name__}: {exc}"}, 400
            if method != "projectRun/overview":
                log.write(json.dumps({"at": time.time(), "method": method, "status": status}) + "\n")
                log.flush()
            self._headers(status)
            self.wfile.write(json.dumps(result, ensure_ascii=False, default=str).encode("utf-8"))

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    (root / "bridge.json").write_text(json.dumps({"port": server.server_address[1], "token": token, "threadId": thread_id,
                                                  "model": model}), encoding="utf-8")
    print(json.dumps({"bridge": f"http://127.0.0.1:{server.server_address[1]}", "threadId": thread_id}), flush=True)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--stored-model")
    parser.add_argument("--provider", choices=("openai", "openai-compatible", "opencode-go"))
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--judge", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.judge:
        results = oracle(root / "workspace")
        print(json.dumps({"passed": sum(item["passed"] for item in results), "of": len(results), "cases": results}, ensure_ascii=False, indent=2))
        return 0 if all(item["passed"] for item in results) else 1
    if not args.stored_model:
        parser.error("--stored-model is required")
    if args.resume:
        state = json.loads((root / "bridge.json").read_text(encoding="utf-8"))
    else:
        if root.exists():
            parser.error("--root must not exist (use --resume)")
        prepare(root)
        state = {"port": 0, "token": secrets.token_urlsafe(18), "threadId": None}
    service, runtime, model = build_service(root, args.stored_model, args.provider)
    thread_id = state["threadId"]
    if not thread_id:
        thread_id = service.thread_start({"workspaceDir": str(root / "workspace"), "permissionMode": PermissionMode.WORKSPACE.value})["thread"]["id"]
        session = runtime.get_session(thread_id)
        selected = {"list_workspace_files", "read_workspace_text", "write_workspace_text", "replace_workspace_text", "apply_patch", "exec"}
        session.tool_selection = [tool.name for tool in runtime.tools.definitions() if tool.name in selected]
        runtime.store.save(session)
    server = serve(service, root, thread_id, model, state["token"], state["port"])
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        runtime.close()


if __name__ == "__main__":
    raise SystemExit(main())
