"""Soak the persistent project runner: days of scheduler time in minutes, no model or credentials.

A multi-day run fails on what days bring, not on what the model does in an hour:
provider outages, malformed replies, rejected reviews, Host restarts, a clock that
moves through backoffs, and state that grows with every episode. This drives the
real runtime, project runner, review gate, delivery and integration with a
simulated agent that plays planner, workers and reviewers, injects those faults
at chosen rates, restarts the Host every few ticks and advances a fake clock.

It checks invariants rather than a happy path:
* the run is integrated with every task's file, and nothing is charged twice
  (known tokens equal exactly what the simulated provider reported);
* per-request prompt size, tick time and state size stay bounded as history grows;
* no worktree or active episode is left behind.

Scripted-model evidence only: it says nothing about a real model's behavior.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import re
import sys
import time as real_time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai import ModelResponse, ModelUsage, ToolCall  # noqa: E402
from app.ai.contracts import MessageRole  # noqa: E402
from app.ai.errors import AITransportError  # noqa: E402
from app.agent_runtime import AgentTool, FileAgentSessionStore, PermissionMode  # noqa: E402
from app.agent_runtime.context_runtime import ContextAgentRuntime  # noqa: E402
from app.agent_runtime import ToolEffect, ToolRegistry, ToolResult  # noqa: E402
from app.agent_runtime.process_tools import exec_tool  # noqa: E402
from app.app_server import LoomAppServerService  # noqa: E402
from app.project_runner import budget, checks, integration, janitor, parallel, program, runner  # noqa: E402
from app.project_runner import store as store_module  # noqa: E402
from app.project_runner.service import EPISODE_CARRY_TOKENS, ProjectServiceMixin, git  # noqa: E402

OBJECTIVE = "Build the soak project: one file per task under src/, each holding its task's content."


class Service(ProjectServiceMixin, LoomAppServerService):
    pass


class Clock:
    """time.time() for the runner's modules, advanced by the soak; everything else is real."""

    def __init__(self):
        self.now = real_time.time()

    def time(self):
        return self.now

    def __getattr__(self, name):
        return getattr(real_time, name)


def write_tool():
    def write(context, arguments):
        path = context.resolve_workspace_path(arguments["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments["content"], encoding="utf-8")
        return ToolResult(ok=True, content=f"Wrote {arguments['path']}")
    return AgentTool(name="sim_write", description="Write a workspace file",
                     input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                                   "required": ["path", "content"]},
                     effect=ToolEffect.MUTATING, handler=write)


def _text(message):
    content = message.content
    return content if isinstance(content, str) else "".join(getattr(part, "text", "") for part in content)


def _payload(prompt):
    """The JSON object the runner appends to each project prompt."""
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{\"", prompt):
        try:
            value, _ = decoder.raw_decode(prompt, match.start())
        except ValueError:
            continue
        if isinstance(value, dict) and "objective" in value:
            return value
    return {}


class SimAgent:
    """Plays every project role; deterministic for a seed."""

    def __init__(self, rng, tasks, workers, *, outage, malformed, rework, continues=0, milestones=0):
        self.rng, self.tasks, self.workers, self.continues = rng, tasks, workers, continues
        self.milestones = milestones  # program mode: report the objective complete after this many
        self.episodes = {}         # task id -> worker episodes that wrote
        self.outage, self.malformed, self.rework = outage, malformed, rework
        self.reported = 0          # tokens reported on returned responses
        self.raised = 0            # simulated provider failures
        self.requests = []         # (prompt chars, role)
        self.reworked = set()
        self.auth_expired = False  # while set, every request is refused as an expired credential would be
        self.refused = 0

    def execute_chat(self, _profile, request):
        messages = request.messages
        # The runtime adds user messages of its own (verification notes, summaries);
        # the role and the turn's progress follow the runner's last project prompt.
        marks = ("Plan this", "Independently verify", "Continue this persistent project")
        at = max((i for i, m in enumerate(messages) if m.role is MessageRole.USER
                  and any(mark in _text(m) for mark in marks)), default=len(messages) - 1)
        prompt = _text(messages[at])
        size = sum(len(_text(m)) for m in messages)
        role = ("planner" if "Plan this" in prompt
                else "reviewer" if "Independently verify" in prompt else "worker")
        payload = _payload(prompt)
        # fresh: the first request of an episode (no reply after the runner's prompt yet;
        # the runtime may add context messages of its own)
        fresh = not any(m.role is MessageRole.ASSISTANT for m in messages[at + 1:])
        self.requests.append((size, role, (payload.get("task") or {}).get("id") or payload.get("scope") or "",
                              len(prompt), len(messages), fresh))
        if self.auth_expired:
            self.refused += 1
            raise AITransportError("401 Unauthorized: the API key is invalid or expired", retryable=False, status_code=401)
        if self.rng.random() < self.outage:
            self.raised += 1
            raise AITransportError("simulated provider outage", retryable=True)
        after_tools = any(m.role is MessageRole.TOOL for m in messages[at + 1:])
        response = getattr(self, role)(prompt, after_tools)
        if not response.tool_calls and self.rng.random() < self.malformed:
            response = ModelResponse(text="Done; everything is in place.")
        tokens = max(1, size // 4)
        self.reported += tokens + 20
        return ModelResponse(text=response.text, tool_calls=response.tool_calls,
                             usage=ModelUsage(tokens, 20, tokens + 20))

    def planner(self, prompt, _after_tools):
        found = re.search(r"This is milestone (\d+) of a long-running program", prompt)
        milestone = int(found.group(1)) if found else 0
        if self.milestones and milestone > self.milestones:
            return ModelResponse(text=json.dumps({"complete": True, "summary": f"all {self.milestones} milestones' "
                                                  "files are integrated under src/"}))
        prefix = f"m{milestone}-" if milestone else ""
        tasks = []
        for index in range(1, self.tasks + 1):
            name = f"{prefix}task-{index}"
            task = {"instructions": f"Create src/{name}.txt holding '{name}'.",
                    "acceptance": [f"src/{name}.txt holds the text '{name}'"]}
            if self.workers > 1:
                task.update(dependsOn=[], fileScopes=[f"src/{name}.txt"], sharedResources=[])
            tasks.append(task)
        return ModelResponse(text=json.dumps({"tasks": tasks}))

    def worker(self, prompt, after_tools):
        task = _payload(prompt).get("task") or {}
        path, final = re.search(r"Create (src/\S+\.txt) holding '([^']*)'", task.get("instructions", "")).groups()
        number = path
        if not after_tools:
            # A long task: each episode writes a slice of work before the file is final.
            done = self.episodes.get(number, 0)
            content = final if done >= self.continues else f"{final} part {done}\n" + "x" * 2000
            return ModelResponse(tool_calls=(ToolCall(f"w-{uuid.uuid4().hex[:8]}", "sim_write",
                {"path": path, "content": content}),))
        done = self.episodes[number] = self.episodes.get(number, 0) + 1
        if done <= self.continues:
            return ModelResponse(text=json.dumps({"action": "continue", "summary": f"task {number}: part {done} of "
                                                  f"{self.continues + 1} written; continue with the next part"}))
        return ModelResponse(text=json.dumps({"action": "candidate", "summary": f"task {number} written",
                                              "notes": [f"soak note for task {number}"]}))

    def reviewer(self, prompt, after_tools):
        payload = _payload(prompt)
        wanted = payload.get("criteria") or []
        if not after_tools:
            calls = []
            for item in wanted:
                path, content = re.search(r"(src/\S+\.txt) holds the text '([^']*)'", item["expectation"]).groups()
                code = f"from pathlib import Path; assert Path({path!r}).read_text(encoding='utf-8') == {content!r}"
                calls.append(ToolCall(f"c-{uuid.uuid4().hex[:8]}", "exec", {
                    "argv": [sys.executable, "-B", "-c", code], "wait": True,
                    "verification": {"case_id": item["id"], "expectation": item["expectation"],
                                     "subject_paths": [path], "check_kind": "functional"}}))
            return ModelResponse(tool_calls=tuple(calls))
        scope = (payload.get("scope"), tuple(item["id"] for item in wanted))
        if wanted and scope not in self.reworked and self.rng.random() < self.rework:
            self.reworked.add(scope)
            return ModelResponse(text=json.dumps({"verdict": "needs_changes", "summary": "simulated defect",
                "issues": [{"taskId": wanted[0]["taskId"], "description": "Rewrite the file; expected its content"}]}))
        return ModelResponse(text=json.dumps({"verdict": "passed", "summary": "all criteria executed",
                                              "checks": [{"criterionId": item["id"]} for item in wanted]}))


def open_host(home, workspace, agent):
    session_store = FileAgentSessionStore(home)
    # The production chain includes context management; a bare runtime has no compaction at all.
    runtime = ContextAgentRuntime(platform=agent, store=session_store, tools=ToolRegistry((write_tool(), exec_tool())),
                                  auto_drain_queue=False)
    service = Service(runtime=runtime, store=session_store, model="soak-model", default_workspace=workspace)
    # Drive the real scheduler deterministically; restarts happen between ticks.
    service.project_runner.stop.set()
    service.project_runner.thread.join()
    service.project_runner.stop.clear()
    return service, runtime


def tick(service):
    service.project_runner.tick()
    with service._guard:
        completions = list(getattr(service, "_operation_done", {}).values())
    for done in completions:
        if not done.wait(60):
            import faulthandler
            faulthandler.dump_traceback(all_threads=True)
            raise RuntimeError("an episode did not finish within 60 s")


def tree_size(path):
    return sum(item.stat().st_size for item in Path(path).rglob("*") if item.is_file())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True, help="New directory")
    parser.add_argument("--tasks", type=int, default=16)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--outage", type=float, default=0.1, help="Share of model requests that fail at the provider")
    parser.add_argument("--malformed", type=float, default=0.1, help="Share of final replies that are not JSON")
    parser.add_argument("--rework", type=float, default=0.3, help="Share of review scopes rejected once")
    parser.add_argument("--continues", type=int, default=0, help="Episodes each task continues before its candidate")
    parser.add_argument("--milestones", type=int, default=0,
                        help="Run a program of this many milestones (then the planner reports the objective complete)")
    parser.add_argument("--restart-every", type=int, default=7, help="Ticks between Host restarts")
    parser.add_argument("--minutes-per-tick", type=float, default=10.0, help="Fake clock advance per tick")
    parser.add_argument("--max-ticks", type=int, default=3000)
    parser.add_argument("--auth-expired", default="",
                        help="FROM:TO ticks during which the credential is refused (HTTP 401), e.g. 40:55")
    parser.add_argument("--resume-after-auth", action="store_true",
                        help="When the run stops while the credential is refused, resume it once the credential works "
                             "again, as a person would from the hub")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if root.exists():
        parser.error("--root must not exist")
    workspace, home = root / "workspace", root / "home"
    workspace.mkdir(parents=True)
    (workspace / "README.md").write_text("soak\n", encoding="utf-8")
    git(workspace, "init", "-b", "main")
    git(workspace, "config", "user.name", "Loom Soak")
    git(workspace, "config", "user.email", "soak@localhost")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "baseline")

    clock = Clock()
    for module in (budget, checks, integration, janitor, parallel, program, runner, store_module):
        module.time = clock
    agent = SimAgent(random.Random(args.seed), args.tasks, args.workers,
                     outage=args.outage, malformed=args.malformed, rework=args.rework, continues=args.continues,
                     milestones=args.milestones)
    service, runtime = open_host(home, workspace, agent)
    source = service.thread_start({"workspaceDir": str(workspace), "permissionMode": "workspace"})["thread"]["id"]
    runtime.set_permission_mode(source, PermissionMode.FULL_ACCESS)  # the simulated checks need exec; no sandbox here
    program_id = None
    if args.milestones:
        with service.project_runner.guard:
            program_id = program.create(service, {"operationId": str(uuid.uuid4()), "threadId": source,
                "objective": OBJECTIVE, "tokenBudget": 1_000_000_000, "maxEpisodes": 10000, "maxWorkers": args.workers,
                "maxMilestones": args.milestones + 1})["id"]
        run_id = program.get(service.project_store, program_id)["currentRunId"]
    else:
        run = service.project_run_create({"operationId": str(uuid.uuid4()), "threadId": source, "objective": OBJECTIVE,
            "tokenBudget": 1_000_000_000, "maxEpisodes": 10000, "maxWorkers": args.workers, "maxReviewCycles": 3,
            "reviewPolicy": "independent", "completionPolicy": "local_integrate"})
        service.project_control({"operationId": str(uuid.uuid4()), "runId": run["id"], "expectedRevision": run["revision"]}, "start")
        run_id = run["id"]

    samples, restarts, started = [], 0, real_time.monotonic()
    outcome = "max_ticks"
    expired = tuple(int(part) for part in args.auth_expired.split(":")) if args.auth_expired else None
    auth_stops, auth_resumes = [], 0
    for number in range(1, args.max_ticks + 1):
        clock.now += args.minutes_per_tick * 60
        if expired:
            agent.auth_expired = expired[0] <= number < expired[1]
        began = real_time.perf_counter()
        tick(service)
        elapsed = real_time.perf_counter() - began
        if program_id:
            state = program.get(service.project_store, program_id)
            run_id = state["currentRunId"]
        run = service.project_store.get(run_id)
        samples.append({"tick": number, "ms": round(elapsed * 1000, 1), "episodes": run["episodesUsed"],
                        "requests": len(agent.requests)})
        if program_id and state["state"] != "running":
            outcome = "completed" if state["state"] == "completed" else "stopped"
            break
        if not program_id and run.get("workflowStatus") == "completed":
            outcome = "completed"
            break
        if run["state"] == "needs_review" or run.get("workflowStatus") == "blocked" or run["control"] == "budget_limited" \
                or (program_id and state["state"] in {"needs_review", "paused"}):
            blocked_on_access = run["control"] == "credential_blocked" or any(
                service.project_store.get(item["runId"])["control"] == "credential_blocked" for item in run.get("teamChildren", []))
            if expired and args.resume_after_auth and blocked_on_access:
                # What the hub offers for a stopped run: once the credential works again, recover and go on.
                auth_stops.append({"tick": number, "state": run["state"], "control": run["control"],
                                   "workflow": run.get("workflowStatus"), "reason": (run.get("reason") or run.get("workflowError") or "")[:300]})
                if number < expired[1]:
                    continue  # still refused: a person would wait
                try:
                    service.project_recover({"operationId": str(uuid.uuid4()), "runId": run_id})
                    auth_resumes += 1
                    continue
                except Exception as exc:  # noqa: BLE001 - reported, not hidden
                    auth_stops[-1]["recoverError"] = f"{type(exc).__name__}: {exc}"[:300]
            outcome = "stopped"
            break
        if number % args.restart_every == 0:
            # A Host restart between ticks: every in-memory object is rebuilt from durable state.
            runtime.close()
            service, runtime = open_host(home, workspace, agent)
            restarts += 1

    for _ in range(3):  # a real Host keeps ticking after completion: cleanup runs then
        clock.now += args.minutes_per_tick * 60
        tick(service)
    run = service.project_store.get(run_id)
    everyone = [item for item in service.project_store.list()]
    # Each top-level run (the one run, or every milestone) holds its whole usage in its
    # ledger: its integration run's when it has one.
    tops = ([item["runId"] for item in program.get(service.project_store, program_id)["milestones"]]
            if program_id else [run_id])
    if run_id not in tops:  # a program that stopped inside a milestone
        tops.append(run_id)
    positions = []
    for top in tops:
        item = service.project_store.get(top)
        ledger = service.project_store.get(item["integrationRunId"]) if item.get("integrationRunId") else item
        positions.append(service.project_budget.report(ledger))
    position = {key: sum(item[key] for item in positions)
                for key in ("knownTokens", "assumedUnknownTokens", "unknownRequests", "inFlightTokens")}
    files = {path.name: path.read_text(encoding="utf-8") for path in (workspace / "src").glob("*.txt")} \
        if (workspace / "src").is_dir() else {}
    names = [f"m{m}-task-{n}" for m in range(1, args.milestones + 1) for n in range(1, args.tasks + 1)] \
        if program_id else [f"task-{n}" for n in range(1, args.tasks + 1)]
    expected = {f"{name}.txt": name for name in names}
    sizes = [item[0] for item in agent.requests if item[1] != "planner"]
    tenth = max(1, len(sizes) // 10)
    starts = {}
    for size, role, key, _, _, fresh in agent.requests:
        if role == "worker" and fresh:
            starts.setdefault(key, []).append(size)
    starts = {key: values for key, values in starts.items() if len(values) >= 3}
    # About four characters a token, plus one episode's own messages on top of the carry.
    carry_chars = 4 * (EPISODE_CARRY_TOKENS + 4000)
    ticks_ms = [item["ms"] for item in samples]
    worktrees = [path for path in (home / "project-runner").rglob("*") if path.is_dir() and (path / ".git").is_file()] \
        if (home / "project-runner").is_dir() else []
    report = {
        "outcome": outcome, "state": run["state"], "workflow": run.get("workflowStatus"), "reason": run.get("reason", "")[:500],
        "ticks": len(samples), "restarts": restarts, "simulatedDays": round(len(samples) * args.minutes_per_tick / 1440, 2),
        "episodes": run["episodesUsed"], "requests": len(agent.requests), "providerFailures": agent.raised,
        "program": program.get(service.project_store, program_id) if program_id else None,
        "integratedFilesMatch": files == expected,
        "tokens": {"reportedBySimulatedProvider": agent.reported, "known": position["knownTokens"],
                   "assumedUnknown": position["assumedUnknownTokens"], "unknownRequests": position["unknownRequests"],
                   "inFlight": position["inFlightTokens"]},
        "promptChars": {"firstTenthMax": max(sizes[:tenth], default=0), "lastTenthMax": max(sizes[-tenth:], default=0),
                        "max": max(sizes, default=0),
                        "episodeStarts": {key: [values[0], max(values), values[-1], len(values)] for key, values in starts.items()}},
        "tickMs": {"firstTenthMedian": sorted(ticks_ms[:max(1, len(ticks_ms) // 10)])[len(ticks_ms[:max(1, len(ticks_ms) // 10)]) // 2],
                   "lastTenthMedian": sorted(ticks_ms[-max(1, len(ticks_ms) // 10):])[len(ticks_ms[-max(1, len(ticks_ms) // 10):]) // 2],
                   "max": max(ticks_ms, default=0)},
        "storage": {"projectDbBytes": sum(path.stat().st_size for path in (home / "project-runner").glob("projects.sqlite3*")),
                  "homeBytes": tree_size(home), "worktreesLeft": len(worktrees),
                  "activeEpisodesLeft": sum(1 for item in everyone if item["activeEpisode"])},
        "recoveries": {kind: count for kind, count in service.project_store.db.execute(
            "SELECT kind, COUNT(*) FROM project_events GROUP BY kind") if "recover" in kind or "continued" in kind
            or "rejected" in kind or "retry" in kind or "transient" in kind or "format" in kind},
        "elapsedSeconds": round(real_time.monotonic() - started, 1),
        "credential": {"expiredTicks": args.auth_expired, "refusedRequests": agent.refused,
                       "stops": auth_stops[:3] + (auth_stops[-1:] if len(auth_stops) > 3 else []), "stopTicks": len(auth_stops),
                       "resumes": auth_resumes},
    }
    report["invariants"] = {
        "integrated": outcome == "completed" and report["integratedFilesMatch"],
        "chargedOnce": report["tokens"]["known"] == agent.reported and report["tokens"]["inFlight"] == 0,
        # Over many episodes of one task, what an episode starts with must stay within the
        # carried-history bound (compaction at an episode boundary), not grow with them.
        "episodeStartBounded": all(max(values) <= values[0] + carry_chars for values in starts.values()),
        # ...and must not drift upward: later episodes start no larger than earlier ones.
        "episodeStartFlat": all(max(values[-len(values) // 3:]) <= 1.2 * max(values[:len(values) // 3]) + 2000
                                for values in starts.values() if len(values) >= 20),
        "nothingLeftRunning": report["storage"]["activeEpisodesLeft"] == 0,
        "noWorktreesLeft": report["storage"]["worktreesLeft"] == 0,
    }
    runtime.close()
    (root / "samples.json").write_text(json.dumps({"ticks": samples, "requests": agent.requests}), encoding="utf-8")
    (root / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if all(report["invariants"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
