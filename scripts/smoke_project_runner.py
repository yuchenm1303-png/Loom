"""Opt-in real-model qualification in a new isolated repository; consumes quota."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
import tempfile
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loom_cli import _build_runtime, build_parser
from app.ai.model_store import ModelConfigStore
from app.agent_runtime import PermissionMode
from app.agent_runtime.contracts import AgentLimits
from app.agent_runtime.contracts import AgentEventKind as E
from app.app_server import LoomAppServerService
from app.project_runner.service import ProjectServiceMixin
from app.project_runner.review import git
from app.agent_runtime.sandbox import SandboxManager


class QualificationService(ProjectServiceMixin, LoomAppServerService):
    pass


class RequestBudgetGuard:
    """Independent cross-check of production admission at response boundaries; not a billing cap."""
    def __init__(self, runtime, maximum):
        self.runtime, self.maximum = runtime, maximum
        self.tokens = 0
        self.stopped = False
        self.seen = set()
        self.lock = threading.Lock()

    def observe(self, event):
        with self.lock:
            if event.kind in {E.MODEL_RESPONSE, E.MODEL_RESPONSE_REJECTED, E.TURN_STOP_CHECKED, E.CONTEXT_CHECKPOINTED} and event.event_id not in self.seen:
                self.seen.add(event.event_id)
                usage = event.data.get("summary_usage" if event.kind is E.CONTEXT_CHECKPOINTED else "usage", {})
                self.tokens += int(usage.get("total_tokens") or 0)
                self.stopped = self.stopped or self.tokens >= self.maximum
            stop = self.stopped and event.kind in {E.MODEL_REQUESTED, E.MODEL_RESPONSE, E.MODEL_RESPONSE_REJECTED, E.TURN_STOP_CHECKED, E.CONTEXT_CHECKPOINTED}
        if stop:
            self.runtime.cancel(event.session_id)


OBJECTIVE = """Build a tiny standard-library-only Python CLI tally.py. It takes exactly
one comma-separated integer list argument, permits whitespace and negative integers,
and prints JSON with sum and unique (number of distinct integers). Example:
python tally.py '3, -2, 3' prints {"sum":4,"unique":2}. Invalid input (empty text,
empty elements, nonintegers or missing argument) must exit nonzero without printing
a success JSON. Include a concise README with usage. Work only on this project;
do not install dependencies, access network, read credentials, or modify external
files. Use the absolute Python executable in README for exec checks, with wait=true
and functional verification records. Keep the plan small (at most two tasks), with
concrete behavioral acceptance including valid and invalid input. Do not start servers.
"""


def oracle(workspace):
    """Fixed external checks; the worker and reviewer cannot rewrite this oracle."""
    cases = [("3, -2, 3", {"sum": 4, "unique": 2}), ("0,0,0", {"sum": 0, "unique": 1}),
             ("-10, 2, 7", {"sum": -1, "unique": 3}), (" 42 ", {"sum": 42, "unique": 1}),
             ("", None), ("1,,2", None), ("hello", None), (None, None)]
    results = []
    for value, expected in cases:
        args = [sys.executable, "-I", "-B", str(workspace / "tally.py")]
        if value is not None:
            args.append(value)
        result = subprocess.run(args, cwd=workspace, capture_output=True, text=True, timeout=10)
        if expected is None:
            passed = result.returncode != 0
        else:
            try:
                passed = result.returncode == 0 and json.loads(result.stdout) == expected
            except (ValueError, TypeError):
                passed = False
        results.append({"input": value, "passed": passed, "exitCode": result.returncode})
    return results


LEDGER_OBJECTIVE = """Build a standard-library-only Python package ledger (a ledger/ directory with
__init__.py and __main__.py) run from the project root as
python -m ledger FILE [--account NAME] [--since YYYY-MM-DD].
FILE is UTF-8 text with one entry per line: DATE,ACCOUNT,AMOUNT. Blank lines and lines whose
first non-space character is # are ignored; fields are trimmed of surrounding whitespace.
- DATE is a real calendar date written YYYY-MM-DD (2026-02-30 is invalid).
- ACCOUNT is nonempty and uses only letters, digits, _ and -.
- AMOUNT is a decimal with an optional leading -, at most two fraction digits (12, -3.5, 0.25);
  no exponent, no +, no thousands separators.
Any invalid line: exit code 2, print a message containing "line N" (the 1-based physical line
number in FILE) to stderr, and print nothing to stdout. A missing or unreadable FILE, an invalid
--since date or other bad arguments: exit code 2 and nothing on stdout.
On success exit 0 and print exactly one JSON object
{"balances": {ACCOUNT: "D.DD", ...}, "total": "D.DD"}: per-account sums using exact decimal
arithmetic, every amount formatted with exactly two decimals ("-3.50", "0.00"); --account keeps
only that account (an absent account gives {} and "0.00"); --since keeps entries on or after that
date; total is the sum of the balances shown. Include unittest tests under tests/ runnable with
python -m unittest, and a README with usage. Work only on this project; do not install
dependencies, access network, read credentials, or modify external files. Use the absolute Python
executable in README for exec checks, with wait=true and functional verification records.
Plan 3 to 5 tasks whose dependsOn reflects real dependencies (for example parsing and validation,
aggregation, the command line, tests and README); give independent tasks disjoint fileScopes.
Do not start servers.
"""

LEDGER_SAMPLE = "# sample\n2026-01-05,cash,12.50\n2026-01-06, bank ,100\n  \n2026-02-01,cash,-2.25\n2026-02-03,card-1,-40.10\n"


def ledger_oracle(workspace):
    """Fixed external checks of the ledger CLI; inputs live outside the workspace."""
    def ok(balances, total):
        return {"balances": balances, "total": total}
    files = {"sample": LEDGER_SAMPLE, "empty": "", "bad_date": "2026-01-01,cash,1\n2026-02-30,cash,1\n",
             "bad_amount": "# c\n2026-01-01,cash,1\n2026-01-02,cash,1.234\n", "missing_field": "2026-01-01,cash\n",
             "exponent": "2026-01-01,cash,1e3\n", "bad_account": "2026-01-01,cash,1\n2026-01-02,ca sh,1\n",
             # Binary floating point rounds this sum to ...9.95; exact decimals give ...9.94.
             "large": "2026-01-01,cash,90071992547409.93\n2026-01-02,cash,0.01\n"}
    cases = [(["sample"], ok({"cash": "10.25", "bank": "100.00", "card-1": "-40.10"}, "70.15")),
             (["sample", "--account", "cash"], ok({"cash": "10.25"}, "10.25")),
             (["sample", "--since", "2026-02-01"], ok({"cash": "-2.25", "card-1": "-40.10"}, "-42.35")),
             (["sample", "--account", "nobody"], ok({}, "0.00")),
             (["empty"], ok({}, "0.00")),
             (["large"], ok({"cash": "90071992547409.94"}, "90071992547409.94")),
             (["bad_date"], "line 2"), (["bad_amount"], "line 3"), (["missing_field"], "line 1"),
             (["exponent"], "line 1"), (["bad_account"], "line 2"),
             (["absent"], ""), (["sample", "--since", "2026-13-01"], "")]
    results = []
    with tempfile.TemporaryDirectory(prefix="loom-ledger-oracle-") as folder:
        for name, text in files.items():
            Path(folder, name + ".csv").write_text(text, encoding="utf-8")
        for args, expected in cases:
            argv = [str(Path(folder, args[0] + ".csv")), *args[1:]]
            # -m resolves the package from cwd; -E/-s keep the environment and user site out.
            result = subprocess.run([sys.executable, "-E", "-s", "-B", "-m", "ledger", *argv], cwd=workspace,
                                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            if isinstance(expected, dict):
                try:
                    passed = result.returncode == 0 and json.loads(result.stdout) == expected
                except (ValueError, TypeError):
                    passed = False
            else:
                passed = result.returncode == 2 and not result.stdout.strip() and expected in result.stderr
            results.append({"input": " ".join(args), "passed": passed, "exitCode": result.returncode})
    return results


SCENARIOS = {"tally": (OBJECTIVE, oracle, "Tally"), "ledger": (LEDGER_OBJECTIVE, ledger_oracle, "Ledger")}


def failure_kind(reason):
    """Bounded diagnostic categories; never print provider bodies or credentials."""
    text = str(reason).lower()
    if "api_key_expired" in text:
        return "provider_key_expired"
    if "402" in text and ("insufficient" in text or "balance" in text):
        return "provider_balance"
    if "401" in text or "invalid_api_key" in text:
        return "provider_authentication"
    if "429" in text:
        return "provider_rate_limit"
    return "project_blocked"


def cancel_run(service, run_id):
    # The scheduler can settle an episode between a report read and cancellation.
    # Re-read and issue the revision-bound command under its actual admission lock.
    with service.project_runner.guard:
        current = service.project_store.get(run_id)
        if current["state"] in {"cancelled", "integrated"}:
            return False
        service.project_control({"operationId": str(uuid.uuid4()), "runId": run_id,
                                 "expectedRevision": current["revision"]}, "cancel")
        return True


def settle_usage(service, run_id):
    """Collect completed late results without dispatching another model episode."""
    deadline = time.monotonic() + 5
    with service._guard:
        completions = list(getattr(service, "_operation_done", {}).values())
    for event in completions:
        event.wait(max(0, deadline - time.monotonic()))
    with service.project_runner.guard:
        for item in service.project_store.list():
            if item["activeEpisode"]:
                service.project_runner._poll(item)
        root = service.project_store.get(run_id)
        ledger = service.project_store.get(root["integrationRunId"]) if root.get("integrationRunId") else root
        return {"finalState": root["state"], "ledgerRunId": ledger["id"], "tokensUsed": ledger["tokensUsed"],
                "budgetPosition": service.project_budget.report(ledger),
                "episodesUsed": ledger["episodesUsed"],
                "unsettledEpisodes": sum(bool(item["activeEpisode"]) for item in service.project_store.list())}


def probe_workspace_sandbox(workspace):
    manager = SandboxManager()
    snapshot = manager.snapshot(workspace=workspace, permission_mode=PermissionMode.WORKSPACE)
    if not snapshot.enforced:
        raise ValueError("Workspace sandbox unavailable: " + snapshot.reason)
    # Probing availability alone misses launch/DACL failures. Exercise the exact
    # interpreter before spending model quota. Do not forward development imports.
    environment = {key: value for key, value in os.environ.items() if key.upper() != "PYTHONPATH"}
    with tempfile.TemporaryDirectory(prefix="loom-qualification-probe-") as folder:
        probe = Path(folder).resolve()
        if probe.parent != Path(tempfile.gettempdir()).resolve() or not probe.name.startswith("loom-qualification-probe-"):
            raise ValueError("Unexpected sandbox probe directory")
        command = manager.prepare(argv=(sys.executable, "-I", "-B", "-c", "print('LOOM_QUALIFICATION_READY')"),
            workspace=probe, cwd=probe, permission_mode=PermissionMode.WORKSPACE, environment=environment)
        # Native descendants may retain pipe handles after the launcher exits.
        # Regular files keep waiting/reading bounded even in that failure mode.
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace") as stdout_file, tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(command.argv, cwd=command.cwd, env=environment, stdin=subprocess.DEVNULL,
                stdout=stdout_file, stderr=stderr_file, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            try:
                # MXC may first restore ACEs left by an interrupted sandbox (38 s
                # measured). Killing it then leaves them for the next run.
                process.wait(timeout=120)
            except subprocess.TimeoutExpired:
                if process.poll() is None:
                    if os.name == "nt":
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
                    else:
                        process.kill()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
                raise ValueError("Sandbox interpreter probe timed out before any model call") from None
            stdout_file.seek(0)
            if process.returncode or "LOOM_QUALIFICATION_READY" not in stdout_file.read(4096):
                raise ValueError("Sandbox could not execute the selected Python interpreter before any model call")
    return {"backend": snapshot.backend.value, "enforced": snapshot.enforced, "networkIsolated": snapshot.network_isolated}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="New directory; never reuses a repository")
    parser.add_argument("--stored-model", required=True, help="Configured model ID, resolved through the OS credential store")
    parser.add_argument("--provider", choices=("openai", "openai-compatible", "opencode-go"), help="Explicit transport override for the configured endpoint")
    parser.add_argument("--token-budget", type=int, default=80000)
    parser.add_argument("--max-episodes", type=int, default=12)
    parser.add_argument("--seconds", type=int, default=480)
    parser.add_argument("--max-workers", type=int, choices=(1, 2), default=1)
    parser.add_argument("--scenario", choices=tuple(SCENARIOS), default="tally",
                        help="tally: one small CLI; ledger: a multi-task package with a larger external oracle")
    args = parser.parse_args(argv)
    if not 1 <= args.token_budget <= 3_000_000 or not 1 <= args.max_episodes <= 80 or not 1 <= args.seconds <= 7200:
        parser.error("Qualification limits exceed supported bounds")
    root = args.root.resolve()
    if root.exists():
        parser.error("--root must not exist")
    try:
        sandbox = probe_workspace_sandbox(root / "workspace")
    except ValueError as exc:
        parser.error(str(exc))
    models = ModelConfigStore()
    entry = models.get(args.stored_model)
    # Resolve exactly the selected configured credential; never print or copy it.
    secret = models.secret_for(entry)
    root.mkdir(parents=True)
    workspace = root / "workspace"
    workspace.mkdir()
    objective_text, check, title = SCENARIOS[args.scenario]
    (workspace / "README.md").write_text(f"# {title} qualification\n\nPython executable for checks: " + sys.executable + "\n", encoding="utf-8")
    (workspace / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    git(workspace, "init", "-b", "main")
    git(workspace, "-c", "user.name=Loom Qualification", "-c", "user.email=qualification@localhost", "add", ".")
    git(workspace, "-c", "user.name=Loom Qualification", "-c", "user.email=qualification@localhost", "commit", "-m", "Qualification baseline")
    provider = args.provider or entry.adapter.value
    runtime_args = build_parser().parse_args(["--provider", provider, "--base-url", entry.base_url,
        "--model", entry.model, "--home", str(root / "state"), "--no-vision", "--timeout", "60"])
    old_key = os.environ.get("LOOM_API_KEY")
    old_pythonpath = os.environ.pop("PYTHONPATH", None)
    try:
        os.environ["LOOM_API_KEY"] = secret
        runtime, store, model = _build_runtime(runtime_args)
    except BaseException:
        if old_pythonpath is not None:
            os.environ["PYTHONPATH"] = old_pythonpath
        raise
    finally:
        if old_key is None:
            os.environ.pop("LOOM_API_KEY", None)
        else:
            os.environ["LOOM_API_KEY"] = old_key
        secret = None
    runtime.limits = AgentLimits(max_model_steps=8, max_tool_calls=16, model_retries=2, transport_retries=2, max_recovery_seconds=90)
    budget_guard = RequestBudgetGuard(runtime, args.token_budget)
    runtime.subscribe(budget_guard.observe)
    service = QualificationService(runtime=runtime, store=store, model=model, default_workspace=workspace)
    started = time.monotonic()
    report = {"model": entry.model, "provider": provider, "profileId": args.stored_model, "maxWorkers": args.max_workers,
              "scenario": args.scenario,
              "tokenBudget": args.token_budget, "maxEpisodes": args.max_episodes, "wallLimitSeconds": args.seconds,
              "realModel": True, "sandbox": sandbox, "outcome": "not_started", "oracle": []}
    run = None
    try:
        source = service.thread_start({"workspaceDir": str(workspace), "permissionMode": PermissionMode.WORKSPACE.value})["thread"]["id"]
        session = runtime.get_session(source)
        selected = {"list_workspace_files", "read_workspace_text", "write_workspace_text", "replace_workspace_text", "apply_patch", "exec"}
        session.tool_selection = [tool.name for tool in runtime.tools.definitions() if tool.name in selected]
        store.save(session)
        objective = objective_text + ("" if args.scenario != "tally" else "\nFor this serial smoke, return exactly one task covering CLI, README and all acceptance cases."
            if args.max_workers == 1 else
            # Without this the planner reasonably orders README after tally.py, and
            # nothing runs concurrently: the parallel path is never exercised.
            "\nFor this parallel smoke, return exactly two independent tasks with dependsOn [] and no shared resources: "
            "task-1 owns only tally.py (the CLI and its behavioral checks); task-2 owns only README.md, written from the "
            "specification above rather than from tally.py, and checked by inspecting README.md itself.")
        objective += "\nRuntime Python executable (use this exact argv[0], not python/py or interpreter discovery): " + json.dumps(sys.executable)
        objective += "\nDo not execute Git commands; the Host owns Git delivery and integration for this qualification."
        run = service.project_run_create({"operationId": str(uuid.uuid4()), "threadId": source, "objective": objective,
            "tokenBudget": args.token_budget, "maxEpisodes": args.max_episodes, "maxWorkers": args.max_workers,
            "reviewPolicy": "independent", "completionPolicy": "local_integrate"})
        service.project_control({"operationId": str(uuid.uuid4()), "runId": run["id"], "expectedRevision": run["revision"]}, "start")
        previous = None
        report["peakActiveWorkers"] = 0
        while True:
            run = service.project_store.get(run["id"])
            projection = (run["state"], run["phase"], run.get("workflowStatus"), run["control"], run["episodesUsed"], run["tokensUsed"])
            if projection != previous:
                print(json.dumps(dict(zip(("state", "phase", "workflow", "control", "episodes", "tokens"), projection))), flush=True)
                previous = projection
            children = [item for item in service.project_store.list() if item.get("teamOwnerId") == run["id"] or item.get("workflowOwnerId") == run["id"]]
            # Sampled once a second: evidence that team tasks really overlapped.
            report["peakActiveWorkers"] = max(report["peakActiveWorkers"], sum(
                bool(item["activeEpisode"]) for item in children if item.get("teamOwnerId") == run["id"]))
            if budget_guard.stopped:
                report["outcome"] = "request_budget_reached"
                break
            if run.get("workflowStatus") == "completed":
                report["oracle"] = check(workspace)
                report["outcome"] = "passed" if all(item["passed"] for item in report["oracle"]) else "oracle_failed"
                break
            if any(item["control"] == "budget_limited" for item in [run, *children]):
                report["outcome"] = "token_budget_reached"
                break
            if run.get("workflowStatus") == "blocked" or run["state"] == "needs_review":
                report["outcome"] = "blocked"
                report["failureKind"] = failure_kind(run.get("workflowError") or run.get("reason"))
                break
            if any(item["activeEpisode"] and item["activeEpisode"]["state"] == "waiting_approval" for item in [run, *children]):
                report["outcome"] = "approval_required"
                break
            if time.monotonic() - started >= args.seconds:
                report["outcome"] = "deadline"
                break
            time.sleep(1)
        report.update(runId=run["id"], state=run["state"], phase=run["phase"], workflow=run.get("workflowStatus"),
                      tokensUsed=run["tokensUsed"], episodesUsed=run["episodesUsed"])
        if report["outcome"] != "passed" and run["state"] not in {"cancelled", "integrated"}:
            report["cancellationRequested"] = cancel_run(service, run["id"])
    except Exception as exc:
        report.update(outcome="error", errorType=type(exc).__name__)
        if run:
            try:
                report["cancellationRequested"] = cancel_run(service, run["id"])
            except Exception as cleanup:
                report["cancellationErrorType"] = type(cleanup).__name__
    finally:
        if run:
            try:
                report.update(settle_usage(service, run["id"]))
            except Exception as cleanup:
                report["settlementErrorType"] = type(cleanup).__name__
        try:
            runtime.close()
        except Exception as cleanup:
            report["shutdownErrorType"] = type(cleanup).__name__
            if report["outcome"] == "passed":
                report["outcome"] = "shutdown_error"
        if old_pythonpath is not None:
            os.environ["PYTHONPATH"] = old_pythonpath
        report.update(observedRequestTokens=budget_guard.tokens, requestBudgetStop=budget_guard.stopped)
        report["elapsedSeconds"] = round(time.monotonic() - started, 2)
        (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
