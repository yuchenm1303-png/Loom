"""Host adapter and RPC for the first serial project execution slice."""
from __future__ import annotations

import copy
import shutil
import json
import uuid
from pathlib import Path

from app.agent_runtime import PermissionMode
from app.agent_runtime.contracts import AgentEventKind as E
from app.app_server_protocol import pending_approval_record
from app.app_server_local_ipc import runtime_home_from_store
from .runner import ProjectRunner, review_target
from .store import ProjectStore
from .review import git, candidate, review_workspace, assess, snapshot, definition_hash
from . import delivery, integration, workflow, parallel, checks, program
from .janitor import review_folder
from .budget import BudgetedModelExecutor, ProjectBudget, event_tokens
from .store import encoded
from app.agent_runtime.context_budget import estimate_tokens

# History a continued episode may carry before the runner compacts it at the boundary.
EPISODE_CARRY_TOKENS = 12000
# Working context of a project session: auto-compaction keeps requests under it.
PROJECT_WORKING_CONTEXT_TOKENS = 48000


def _describe_tool(tool, arguments):
    """One short line for what a tool call is doing, in the words of its arguments."""
    path = arguments.get("path")
    if isinstance(path, str) and path:
        return f"{tool} {path.replace('scratch:', '')}"[:120]
    argv = arguments.get("argv")
    if isinstance(argv, list) and argv:
        # An absolute path says where, not what: its file name, before the line is cut to length.
        # A switch like cmd's /c is not a path: it has no folder above its name.
        words = [Path(str(item)).name if Path(str(item)).is_absolute() and len(Path(str(item)).parts) > 2 else str(item)
                 for item in argv]
        return " ".join(words)[:120]
    return str(tool or "")[:120]


class ProjectExecutor:
    def __init__(self, service, home):
        self.service, self.home = service, home

    def advance_workflow(self, run):
        return workflow.advance(self.service, run)

    def advance_parallel(self, run):
        return parallel.advance(self.service, run)

    def advance_programs(self):
        return program.advance(self.service)

    @staticmethod
    def session_key(run):
        if run["phase"] == "verifying":
            return f"review-{run['reviewCandidate']['id']}"
        return "planner" if run["phase"] == "planning" else f"worker-{run['taskIndex']}"

    def session(self, run):
        key = self.session_key(run)
        workspace = self.home / "workspaces" / run["id"]
        if not workspace.exists():
            workspace.parent.mkdir(parents=True, exist_ok=True)
            git(run["repository"], "-c", "core.autocrlf=false", "worktree", "add", "--detach", str(workspace), run["baseCommit"])
        # Never assume a preexisting directory is the expected checkout.
        common = Path(git(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
        if common != Path(run["gitCommonDir"]):
            raise ValueError("project workspace identity changed; inspect before continuing")
        git(workspace, "merge-base", "--is-ancestor", run["baseCommit"], "HEAD")
        if run["phase"] == "verifying":
            expected = self.service.project_store.candidate(run)
            # One level under reviews/: the Windows sandbox needs every folder above a checkout
            # listable, and its one-time preparation can only open folders that already exist.
            workspace = review_workspace(run, expected, workspace, self.home / "reviews" / review_folder(run["id"], expected["id"]))
        if key in run["sessions"]:
            self.compact_between_episodes(run, run["sessions"][key])
            return run["sessions"][key]
        source = self.service._load(run["sourceThreadId"])
        session = self.service.runtime.create_session(source.profile_id, workspace_dir=workspace,
            system_prompt=source.system_prompt,
            permission_mode=PermissionMode.READ_ONLY if run["phase"] == "planning" else source.permission_mode)
        for field in ("model_selection", "model", "model_provider", "model_base_url", "model_vision",
                      "reasoning_kind", "reasoning_value", "tool_selection", "communication_language"):
            setattr(session, field, copy.deepcopy(getattr(source, field)))
        self.service.store.save(session)
        library = getattr(self.service, "thread_library", None)
        if library is not None:
            # Internal episodes have deterministic labels. Background title
            # inference would otherwise spend tokens outside the run's ledger.
            library.write(session.session_id, {"title": f"Project {run['id'][:8]} · {key}",
                          "titleSource": "manual", "autoTitleDisabled": True, "projectRunId": run["id"]})
        return session.session_id

    def session_scratch(self, session_id):
        """The scratch every episode of a project session shares (and its sandbox opens).

        A project session is registered as one before its first episode's prompt is built:
        otherwise the runtime names a per-turn folder, and what is copied there before the
        turn starts lands where the sandboxed episode cannot read it.
        """
        self.service._project_session_ids.add(session_id)
        return self.service.runtime._run_scratch_dir(session_id, "session")

    def accepted_checks(self, run, session_id):
        """The project's kept checks, copied into this session's scratch and rendered for it."""
        scratch = self.session_scratch(session_id)
        workspace = self.service._load(session_id).workspace_dir
        if scratch is None:
            return []
        scratch.mkdir(parents=True, exist_ok=True)
        return checks.provide(self.service.project_store, run, scratch, workspace)

    def references(self, run, session_id):
        """The program's attached files, copied into this session's scratch; returns their paths for the prompt."""
        store = self.service.project_store
        owner = checks.registry_root(run)
        milestone = (store.get(owner) if owner != run["id"] else run).get("milestone") or {}
        if not milestone.get("programId"):
            return []
        records = program.get(store, milestone["programId"]).get("references") or []
        scratch = self.session_scratch(session_id)
        if not records or scratch is None:
            return []
        scratch = scratch / "references"
        scratch.mkdir(parents=True, exist_ok=True)
        source = program.references_folder(store, milestone["programId"])
        paths = []
        for item in records:
            target = scratch / item["name"]
            if not target.exists():
                shutil.copyfile(source / item["name"], target)
            paths.append(target.as_posix())
        return paths

    def compact_between_episodes(self, run, session_id):
        """Start a continued episode from the run's own handoff, not the whole history.

        Without this every episode re-sends all earlier ones: in a real run the
        worker's requests grew from 8k to 60k tokens and cost grew quadratically.
        The summary is built from durable run state, so no model call is spent;
        recorded checks stay in the event log, where review evidence is read.
        """
        compact = getattr(self.service.runtime, "compact_context", None)
        session = self.service._load(session_id)
        if compact is None or estimate_tokens(session.messages) <= EPISODE_CARRY_TOKENS:
            return
        recorded = {}
        for event in self.service.store.events(session_id):
            if event.kind is E.CHECK_RECORDED:
                recorded[str(event.data.get("case_id"))] = str(event.data.get("verdict"))
        task = run["plan"][run["taskIndex"]] if run["phase"] == "working" and run["plan"] else None
        # A real README task spent five of every eight steps re-listing the tree and
        # re-reading every source file after each boundary, and never finished. Give
        # the layout up front and ask for targeted reads only.
        try:
            names = git(session.workspace_dir, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
            files = {name: (Path(session.workspace_dir) / name).stat().st_size
                     for name in names[:200] if (Path(session.workspace_dir) / name).is_file()}
        except (ValueError, OSError):
            files = {}
        summary = encoded({"note": "Earlier episodes of this session were compacted by the project runner at an "
                                   "episode boundary. The workspace holds all file changes; workspaceFiles lists them "
                                   "with sizes, so do not list directories again. Read only the files you are about "
                                   "to change or check, and continue from the handoff rather than starting over. "
                                   "Scratch files from earlier episodes are still there. Do not repeat checks already "
                                   "recorded unless the code changed.",
                           "objective": run["objective"][:4000], "phase": run["phase"], "task": task,
                           "handoff": run.get("handoff", "")[:6000], "reviewFeedback": run.get("feedback", []),
                           "recordedChecks": recorded, "workspaceFiles": files})
        try:
            # Carry no earlier episode prompts: the summary holds the task, handoff and
            # feedback, and every episode sends its own prompt. Chat compaction keeps up
            # to 20k tokens of user messages; a soak of 42 episodes showed every one of
            # them carried, re-sent on each request of a multi-day session.
            compact(session_id, summary, user_message_tokens=0)
        except (ValueError, RuntimeError):
            pass  # an uncompacted session costs more, but is not wrong

    def result_facts(self, run, episode, reply):
        source = self.home / "workspaces" / run["id"]
        if run.get("reviewPolicy") != "independent":
            return {}
        if episode["phase"] == "working" and reply.get("action") == "candidate":
            parallel.allowed(self.service.project_store, run, source)
            return {"candidate": candidate(review_target(run), source)}
        if episode["phase"] == "verifying":
            expected = self.service.project_store.candidate(run)
            workspace = Path(self.service._load(episode["sessionId"]).workspace_dir)
            events = self.service.store.events(episode["sessionId"])
            assessment = assess(run, episode, reply, expected, source, workspace, events)
            facts = {"assessment": assessment}
            try:
                checks.harvest(self.service.project_store, run, assessment, events, workspace,
                               self.service.store.session_dir(episode["sessionId"]) / "run-artifacts")
            except OSError:
                pass  # a check that could not be kept costs a later review time, not correctness
            if assessment["verdict"] == "passed" and run["reviewScope"] == "task" and run["taskIndex"] + 1 == len(run["plan"]):
                facts["finalCandidate"] = candidate({**run, "reviewScope": "project", "taskIndex": len(run["plan"])}, source)
            return facts
        return {}

    def dispatch(self, episode):
        service = self.service
        session_id = episode["sessionId"]
        def execute():
            def cancelled():
                return service.project_runner.stop.is_set() or parallel.cancelled(service.project_store, service.project_store.get(episode["runId"]))
            try:
                if cancelled():
                    service.project_store.preparation_receipt(episode["id"], {"state": "cancelled", "tokens": 0})
                    return
                session = service._load(session_id)
                service._prepare_turn(session)  # existing credentials/capability boundary
                if cancelled():
                    service.project_store.preparation_receipt(episode["id"], {"state": "cancelled", "tokens": 0})
                    return
            except Exception as exc:
                service.project_store.preparation_receipt(episode["id"], {"state": "failed", "tokens": 0,
                                                                          "error": f"Preparation failed: {exc}"})
                return
            service.runtime.start_turn(session_id, episode["prompt"], turn_id=episode["turnId"])
        with service._guard:
            if service._is_active(session_id):
                raise RuntimeError("project session is already active")
            if service._load(session_id).status.value in {"running", "waiting_approval"}:
                raise RuntimeError("project session needs reconciliation")
            service._project_session_ids.add(session_id)
            service._launch(session_id, execute)

    def poll(self, episode):
        service = self.service
        with service._guard:
            if service._is_active(episode["sessionId"]):
                return None
            receipt = service.project_store.preparation_receipt(episode["id"])
            if receipt:
                return receipt
            session = service._load(episode["sessionId"])
            if session.current_turn_id == episode["turnId"] and session.status.value == "waiting_approval":
                return {"state": "waiting_approval"}
            events = [e for e in service.store.events(session.session_id) if e.turn_id == episode["turnId"]]
            terminal = next((e for e in reversed(events) if e.kind in {
                E.TURN_COMPLETED, E.TURN_FAILED, E.TURN_CANCELLED, E.TURN_INTERRUPTED, E.LIMIT_REACHED}), None)
            # Each usage event keeps its ID: the ledger charges an event once,
            # however often it is offered (live listener, this poll, a restart).
            usage = [(e.event_id, tokens) for e in events if (tokens := event_tokens(e)) is not None]
            # The turn's durable events show whether anything beyond model requests ran.
            # Without a tool call there is no side effect to replay or reconcile.
            no_tools = not any(e.kind in {E.TOOL_REQUESTED, E.TOOL_STARTED} for e in events)
            if terminal is None or session.current_turn_id != episode["turnId"]:
                unfinished = None if no_tools else confined_in_flight(events)
                if no_tools or unfinished is not None:
                    # The Host stopped (update, sleep, crash) before the turn ended, and
                    # nothing in flight could reach past this episode's own checkout. Close
                    # the turn durably (in-flight tools read as aborted, never replayed); a
                    # new episode continues and is told what may have partly run.
                    if session.current_turn_id == episode["turnId"] and session.status.value == "running":
                        service.runtime.recover_interrupted(session.session_id)
                    detail = ("no tool had run" if no_tools else
                              "no tool was running" if not unfinished else
                              "these tools were still running and may have partly run: " + ", ".join(unfinished)
                              + "; their effects are confined to your workspace, so check the files they touch first")
                    return {"state": "interrupted", "interrupted": True, "sideEffectFree": no_tools,
                            "confined": True, "error": detail,
                            "tokens": sum(tokens for _, tokens in usage), "usage": usage}
                return {"state": "outcome_unknown", "error": service._task_errors.get(session.session_id)
                        or "No matching durable terminal turn; execution must not be replayed"}
            if terminal.kind is E.TURN_INTERRUPTED:
                # Closed durably (a restart, or an approval that could not be resumed): it
                # continues on the same terms as above, judged by what was still in flight.
                return {"state": "interrupted", "interrupted": True, "sideEffectFree": no_tools,
                        "confined": no_tools or confined_in_flight(events) is not None,
                        "error": session.error or "Turn interrupted", "tokens": sum(tokens for _, tokens in usage), "usage": usage}
            error = session.error or (terminal.data.get("reason", "Runtime limit reached") if terminal.kind is E.LIMIT_REACHED else "")
            # The outcome as the runtime recorded it, so the runner decides on fields,
            # not on message text. Older events lack them; the runner falls back then.
            data = terminal.data
            return {"state": session.status.value, "error": error,
                    "text": data.get("model_text", session.final_text) if terminal.kind is E.TURN_COMPLETED else session.final_text,
                    "runtimeNotice": data.get("verification_notice", "") if terminal.kind is E.TURN_COMPLETED else "",
                    "limit": data.get("limit") if terminal.kind is E.LIMIT_REACHED else None,
                    "providerFailure": data.get("provider_failure") if terminal.kind is E.TURN_FAILED else None,
                    "retryable": data.get("retryable") if terminal.kind is E.TURN_FAILED else None,
                    "providerStatus": data.get("provider_status_code") if terminal.kind is E.TURN_FAILED else None,
                    "tokens": sum(tokens for _, tokens in usage), "usage": usage}

    def cancel(self, episode):
        # _launch owns preparation too. runtime.cancel is safe if it has started;
        # the dispatch closure also checks the durable cancellation state below.
        session = self.service._load(episode["sessionId"])
        if session.current_turn_id == episode["turnId"]:
            self.service.runtime.cancel(session.session_id)

    def shutdown(self):
        waits = []
        for run in self.service.project_store.list():
            episode = run["activeEpisode"]
            if episode:
                self.cancel(episode)
                with self.service._guard:
                    event = getattr(self.service, "_operation_done", {}).get(episode["sessionId"])
                    if event:
                        waits.append(event)
        return waits


# Built-in tools whose effects stay inside the session's workspace or scratch folder.
WORKSPACE_TOOLS = {"read_workspace_text", "list_workspace_files", "write_workspace_text",
                   "replace_workspace_text", "apply_patch"}


def confined_in_flight(events):
    """Names of tools left running in a turn, or None if any could reach past its checkout.

    A Host crash, update or sleep most often lands while a tool runs; on a multi-day run
    stopping every such project for a person made autonomy impossible. A project
    episode works in its own isolated checkout and its result is independently
    reviewed, so a partial edit there is ordinary unfinished work. Only effects that
    could reach elsewhere (an unsandboxed or networked process, any other tool) still
    need reconciliation.
    """
    finished = {e.data.get("call_id") for e in events if e.kind in {E.TOOL_COMPLETED, E.TOOL_FAILED}}
    running = [e.data for e in events if e.kind is E.TOOL_STARTED and e.data.get("call_id") not in finished]
    processes = [e.data.get("sandbox") or {} for e in events if e.kind is E.PROCESS_STARTED]
    sandboxed = all(item.get("enforced") is True and item.get("mode") == "workspace"
                    and item.get("network_isolated") is True for item in processes)
    for call in running:
        if call.get("effect") == "read_only" or call.get("tool") in WORKSPACE_TOOLS:
            continue
        if call.get("tool") == "exec" and sandboxed:
            continue
        return None
    return [str(call.get("tool")) for call in running]


class ProjectServiceMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        home = runtime_home_from_store(self.store) / "project-runner"
        self.project_store = ProjectStore(home)
        with self.project_store.lock:
            self._project_session_ids = {row[0] for row in self.project_store.db.execute("SELECT DISTINCT session_id FROM project_episodes")}
        idle = getattr(self.runtime, "idle_compaction", None)
        if idle is not None:
            original_schedule = idle.schedule
            def project_schedule(session, observed):
                # Background speculation would bypass project admission and usage.
                # In-turn compaction remains charged to its prepared episode.
                if session.session_id not in self._project_session_ids:
                    original_schedule(session, observed)
            idle.schedule = project_schedule
        executor = ProjectExecutor(self, home)
        self.project_budget = ProjectBudget(lambda: self.project_store, self._project_session_ids)
        self.project_runner = ProjectRunner(self.project_store, executor, self.project_budget)
        # After the scheduler lease: only its owner may decide that an open
        # request of a previous process has unknown usage.
        self.project_budget.recover()
        self.runtime.model_executor = BudgetedModelExecutor(self.runtime.model_executor, self.project_budget)
        # A project task spans many episodes (turns); its scratch must too. A
        # reviewer lost nine episodes to scripts that vanished at each boundary.
        self.runtime.session_scoped_scratch = self._project_session_ids
        # Nobody sits at a project's threads: a command the sandbox cannot start goes back to the
        # agent as a failure (Codex's model) instead of asking to run it unsandboxed, which
        # stalled a live review for an hour. Approvals a tool itself asks for still wait in the hub.
        self.runtime.unattended_sessions = self._project_session_ids
        # Compact inside long episodes too, not only near the model's window.
        self.runtime.session_working_context_tokens = (
            lambda session_id: PROJECT_WORKING_CONTEXT_TOKENS if session_id in self._project_session_ids else None)
        self.runtime.subscribe(self.project_budget.observe)
        on_activated = self.runtime._on_turn_activated
        def project_activated(session_id, token):
            on_activated(session_id, token)
            if session_id not in self._project_session_ids:
                return
            run = self.project_store.session_run(session_id)
            if run and (parallel.cancelled(self.project_store, run) or self.project_runner.stop.is_set()):
                token.cancel()  # closes the cancellation race before the first model sample
        self.runtime._on_turn_activated = project_activated
        self.project_runner.start()
        original_close = self.runtime.close
        def close():
            self.project_runner.close()
            original_close()
        self.runtime.close = close

    def project_run_create(self, params):
        source = self._load(self._required_text(params, "threadId"))
        objective = self._required_text(params, "objective")
        if len(objective) > 20000:
            raise ValueError("objective is too long")
        budget, episodes = params.get("tokenBudget"), params.get("maxEpisodes")
        policy, cycles = params.get("reviewPolicy", "independent"), params.get("maxReviewCycles", 3)
        completion = params.get("completionPolicy", "manual")
        workers = params.get("maxWorkers", 1)
        if type(workers) is not int or not 1 <= workers <= 2 or (workers > 1 and policy != "independent"):
            raise ValueError("maxWorkers must be 1..2 and parallel work requires independent review")
        if completion not in ("manual", "local_integrate") or (completion == "local_integrate" and policy != "independent"):
            raise ValueError("completionPolicy must be manual or local_integrate with independent review")
        if not isinstance(policy, str) or policy not in {"manual", "independent"} or type(cycles) is not int or not 1 <= cycles <= 10:
            raise ValueError("reviewPolicy must be manual or independent; maxReviewCycles must be within 1..10")
        if type(budget) is not int or not 1 <= budget <= 1_000_000_000:
            raise ValueError("tokenBudget must be a positive integer")
        if type(episodes) is not int or not 1 <= episodes <= 10000:
            raise ValueError("maxEpisodes must be within 1..10000")
        request = {"threadId": source.session_id, "objective": objective, "tokenBudget": budget, "maxEpisodes": episodes}
        # Omitted fields preserve retry fingerprints for already-created v1 runs.
        for key in ("reviewPolicy", "maxReviewCycles", "completionPolicy", "maxWorkers"):
            if key in params:
                request[key] = params[key]
        previous = self.project_store.lookup(params.get("operationId"), {"action": "create", **request})
        if previous:
            return previous
        root = Path(source.workspace_dir).resolve()
        if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
            raise ValueError("first project-runner version requires a Git repository root")
        if git(root, "status", "--porcelain"):
            raise ValueError("commit or separate existing workspace changes before creating a project run")
        completion_ref = git(root, "symbolic-ref", "HEAD") if completion == "local_integrate" else None
        return self.project_store.create(params.get("operationId"), {
            "sourceThreadId": source.session_id, "objective": objective, "tokenBudget": budget, "maxEpisodes": episodes,
            "repository": str(root), "baseCommit": git(root, "rev-parse", "HEAD"),
            "gitCommonDir": str(Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()),
            "budgetEnforcement": "request_boundary", "delivery": "isolated_candidate_only",
            "reviewPolicy": policy, "maxReviewCycles": cycles, "completionPolicy": completion,
            "workflowControl": "paused", "workflowStatus": "idle", "completionRef": completion_ref, "maxWorkers": workers}, request=request)

    def project_control(self, params, action):
        with self.project_runner.guard:
            current = self.project_store.get(self._required_text(params, "runId"))
            if current.get("completionPolicy") == "local_integrate" or current.get("maxWorkers", 1) > 1:
                run = workflow.control(self, params, action)
                self._raise_program_budget(current, params)
                if action == "cancel":
                    for target in (run, self.project_store.get(run["integrationRunId"]) if run.get("integrationRunId") else run):
                        if target["activeEpisode"]:
                            self.project_runner.executor.cancel(target["activeEpisode"])
                self._project_team_cancel(run, action)
                return run
            run = self.project_store.control(params.get("operationId"), self._required_text(params, "runId"),
                                             params.get("expectedRevision"), action)
            if action == "cancel" and run["activeEpisode"]:
                self.project_runner.executor.cancel(run["activeEpisode"])
            self._project_team_cancel(run, action)
            return run

    def _raise_program_budget(self, before, params):
        """Budget added to a milestone is added to its program, or the next milestone would stop at the old total."""
        owner = (before.get("milestone") or {}).get("programId")
        if not owner:
            return
        raised = {key: params[key] - before[key] for key in ("tokenBudget", "maxEpisodes")
                  if type(params.get(key)) is int and params[key] > before[key]}
        if raised:
            current = program.get(self.project_store, owner)
            for key, amount in raised.items():
                current[key] += amount
            program._save(self.project_store, current)

    def _project_team_cancel(self, run, action):
        if action != "cancel":
            return
        for item in run.get("teamChildren", []):
            if item["state"] == "merged":
                continue
            with self.project_store.transaction() as db:
                child = self.project_store._read(db, item["runId"])
                child.update(state="cancelled", control="paused")
                self.project_store.save(db, child, "team_cancelled")
            if child["activeEpisode"]:
                self.project_runner.executor.cancel(child["activeEpisode"])

    def project_budget_position(self, run):
        return {**run, "budgetPosition": self.project_budget.report(run)}

    def project_knowledge(self, run):
        """What a long run has learned and recovered from, for the person watching it."""
        from .runner import RECOVERY
        store = self.project_store
        ids = [run["id"], *(item["runId"] for item in run.get("teamChildren", []))]
        if run.get("integrationRunId"):
            ids.append(run["integrationRunId"])
        events = {event for _, _, event in RECOVERY.values()}
        with store.lock:
            marks = ",".join("?" * len(ids))
            recoveries = {kind: count for kind, count in store.db.execute(
                f"SELECT kind, COUNT(*) FROM project_events WHERE run_id IN ({marks}) GROUP BY kind", ids) if kind in events}
            kept = store.db.execute("SELECT COUNT(*) FROM project_checks WHERE root_id=?",
                                    (checks.registry_root(run),)).fetchone()[0]
        return {"notes": checks.notes_for(store, run), "acceptedChecks": kept, "recoveries": recoveries}

    def project_overview(self, params):
        """Everything persistent-project mode shows for one conversation, in one read.

        The project is the conversation's newest program (its current milestone) or else
        its newest root run. Agents are the runs' active episodes with what each is doing
        now, read from its current turn; events are the project's recent history.
        """
        thread_id = self._required_text(params, "threadId")
        store = self.project_store
        current = program.for_thread(store, thread_id)
        runs = store.list()
        roots = [run for run in runs if run.get("sourceThreadId") == thread_id
                 and not run.get("teamOwnerId") and not run.get("integrationTarget")]
        if current and current.get("currentRunId"):
            root = store.get(current["currentRunId"])
        else:
            root = max(roots, key=lambda run: run.get("createdAt", 0)) if roots else None
        if root is None:
            # Nothing started yet: what would stop a start (uncommitted changes) is shown before it is tried.
            return {"run": None, "program": current, "children": [], "agents": [], "events": [],
                    "workspace": self._project_workspace_state(thread_id)}
        children = [run for run in runs if run.get("teamOwnerId") == root["id"]]
        integration = store.get(root["integrationRunId"]) if root.get("integrationRunId") else None
        ledger = integration or root
        family = [root, *children, *([integration] if integration else [])]
        agents = [self._agent_view(run, run["activeEpisode"]) for run in family if run.get("activeEpisode")]
        ids = [run["id"] for run in family]
        with store.lock:
            marks = ",".join("?" * len(ids))
            rows = store.db.execute(
                f"SELECT sequence, run_id, kind, json_extract(data,'$.updatedAt'), json_extract(data,'$.reason') "
                f"FROM project_events WHERE run_id IN ({marks}) ORDER BY sequence DESC LIMIT 14", ids).fetchall()
        events = [{"sequence": seq, "runId": run_id, "kind": kind, "at": at, "reason": (reason or "")[:300],
                   "taskId": next((item["taskId"] for item in root.get("teamChildren", []) if item["runId"] == run_id), None)}
                  for seq, run_id, kind, at, reason in rows]
        view = {**self.project_budget_position(root), "knowledge": self.project_knowledge(root),
                "ledgerPosition": self.project_budget.report(ledger), "ledgerTokens": ledger["tokensUsed"],
                "ledgerEpisodes": ledger["episodesUsed"], "guidance": list(root.get("guidance") or [])}
        return {"run": view, "program": current, "children": children, "integration": integration,
                "agents": agents, "events": events, "timeline": self._project_timeline(root, family)}

    def _project_workspace_state(self, thread_id):
        """The conversation's repository as a project would start from it: branch, commit, uncommitted changes."""
        try:
            folder = Path(self._load(thread_id).workspace_dir).resolve()
            top = Path(git(folder, "rev-parse", "--show-toplevel")).resolve()
        except (OSError, ValueError, TypeError):
            return {"git": False, "changes": [], "changeCount": 0}
        if top != folder:
            return {"git": False, "changes": [], "changeCount": 0}
        lines = [line for line in git(folder, "-c", "core.quotepath=false", "status", "--porcelain=v1", raw=True).splitlines() if line.strip()]
        try:
            branch = git(folder, "symbolic-ref", "--short", "HEAD")
        except ValueError:
            branch = ""
        try:
            head = git(folder, "rev-parse", "--short", "HEAD")
        except ValueError:
            head = ""  # a repository without a commit yet
        return {"git": True, "branch": branch, "head": head, "changeCount": len(lines),
                "changes": [{"status": line[:2].strip() or "?", "path": line[3:].strip().strip('"')} for line in lines[:40]]}

    def project_commit_workspace(self, params):
        """Commit every uncommitted change of the conversation's repository, so a project can start from it.

        All or nothing: a project needs a clean checkout to start and to merge back into at the
        end, so committing part of the changes would leave it blocked. The person asks for this
        from the hub, with the message they typed; hooks run as for any commit.
        """
        thread_id = self._required_text(params, "threadId")
        message = " ".join(self._required_text(params, "message").split())[:200]
        state = self._project_workspace_state(thread_id)
        if not state["git"]:
            raise ValueError("This conversation's folder is not the root of a Git repository")
        if not state["changeCount"]:
            return state
        if params.get("expectedCount") != state["changeCount"]:
            raise ValueError("The changes moved since they were shown; look at them again before committing")
        folder = Path(self._load(thread_id).workspace_dir).resolve()
        git(folder, "add", "--all")
        git(folder, "commit", "-m", message)
        return self._project_workspace_state(thread_id)

    def _project_timeline(self, root, family):
        """Every episode of this milestone as a span on a clock: who, on what, from when to when, what it cost.

        Episodes keep no times of their own; each project event stores the run as it was, so an
        episode starts at the first event naming it active and ends at the first after that does
        not. The event that closed it says how it ended (merged, rejected, interrupted, ...).
        """
        store = self.project_store
        ids = [run["id"] for run in family]
        tasks = {item["runId"]: item["taskId"] for item in root.get("teamChildren", [])}
        plans = {run["id"]: run.get("plan") or [] for run in family}
        with store.lock:
            marks = ",".join("?" * len(ids))
            rows = store.db.execute(
                f"SELECT run_id, kind, json_extract(data,'$.updatedAt'), json_extract(data,'$.activeEpisode.id') "
                f"FROM project_events WHERE run_id IN ({marks}) ORDER BY sequence", ids).fetchall()
            episodes = {row[0]: json.loads(row[1]) for row in store.db.execute(
                f"SELECT id, data FROM project_episodes WHERE run_id IN ({marks})", ids)}
        spans, open_ = [], {}
        def close(run_id, end, outcome):
            episode_id, start = open_.pop(run_id)
            episode = episodes.get(episode_id) or {}
            phase = episode.get("phase") or ""
            plan = plans.get(run_id) or []
            index = episode.get("taskIndex", 0)
            task = tasks.get(run_id) or (plan[index]["id"] if phase == "working" and index < len(plan) else None)
            spans.append({"id": episode_id, "runId": run_id, "phase": phase, "taskId": task, "start": start, "end": end,
                          "tokens": int(((episode.get("result") or {}).get("tokens")) or 0), "outcome": outcome})
        for run_id, kind, at, active in rows:
            if run_id in open_ and open_[run_id][0] != active:
                close(run_id, at, kind)
            if active and run_id not in open_:
                open_[run_id] = (active, at)
        for run_id in list(open_):
            close(run_id, None, "")  # still running
        return spans[-120:]

    def _agent_view(self, run, episode):
        """An active episode as a person reads it: who, on what, how far, doing what."""
        role = "planner" if episode.get("phase") == "planning" else "reviewer" if episode.get("phase") == "verifying" else "worker"
        plan = run.get("plan") or []
        index = episode.get("taskIndex", 0)
        task = plan[index] if role == "worker" and index < len(plan) else None
        steps, activity, started, approval = 0, "", None, None
        try:
            # Only the current turn: a long session's log is megabytes, and this runs every refresh.
            reader = getattr(self.store, "turn_events", None)
            events = (reader(episode["sessionId"], episode.get("turnId") or "") if callable(reader)
                      else [event for event in self.store.events(episode["sessionId"]) if event.turn_id == episode.get("turnId")])
            for event in events:
                started = started or event.created_at
                if event.kind is E.MODEL_RESPONSE:
                    steps += 1
                elif event.kind is E.TOOL_REQUESTED:
                    activity = _describe_tool(event.data.get("tool"), event.data.get("arguments") or {})
            if episode.get("state") == "waiting_approval":
                approval = self._project_pending_approval(episode, events)
        except (OSError, ValueError, KeyError):
            pass
        return {"id": episode["sessionId"], "runId": run["id"], "role": role, "state": episode.get("state"),
                "taskId": task["id"] if task else None, "taskTitle": (task or {}).get("instructions", "")[:120],
                "scope": run.get("reviewScope") if role == "reviewer" else None,
                "step": steps, "activity": activity, "startedAt": started, "approval": approval}

    def _project_pending_approval(self, episode, events):
        """What an agent of an unattended project waits for a person to allow, if anything.

        A project runs without anyone watching its threads, so a request for approval (a
        command the sandbox could not start, say) would otherwise stall it with no sign.
        """
        session = self._load(episode["sessionId"])
        if session.status.value != "waiting_approval" or session.current_turn_id != episode.get("turnId"):
            return None
        record = pending_approval_record(session, events)
        if not record or not record.get("requestId"):
            return None
        return {"callId": record["callId"], "requestId": record["requestId"], "tool": record.get("toolName") or "",
                "command": _describe_tool(record.get("toolName"), record.get("arguments") or {}),
                "reason": str(record.get("retryReason") or record.get("reason") or "")[:300],
                # An answer that could not be carried out (the resume failed): say so instead of asking silently again.
                "error": str((getattr(self, "_task_errors", {}) or {}).get(episode["sessionId"]) or "")[:300],
                "kind": (record.get("sandboxDenial") and "sandbox") or record.get("effect") or ""}

    def project_approval(self, params):
        """A person's answer to what a project agent waits for: allow that one call, or decline it.

        Only the two one-call answers: a wider grant (a saved rule, a session-wide
        allowance) is a decision for the conversation's own approval card, not the hub.
        """
        run_id, session_id = self._required_text(params, "runId"), self._required_text(params, "sessionId")
        decision = self._required_text(params, "decision")
        if decision not in {"accept", "decline"}:
            raise ValueError("decision must be accept or decline")
        root = self.project_store.get(run_id)
        family = [root, *(self.project_store.get(item["runId"]) for item in root.get("teamChildren", []))]
        if root.get("integrationRunId"):
            family.append(self.project_store.get(root["integrationRunId"]))
        episode = next((run["activeEpisode"] for run in family
                        if run.get("activeEpisode") and run["activeEpisode"]["sessionId"] == session_id), None)
        if episode is None:
            raise ValueError("That agent is no longer waiting in this project")
        events = [event for event in self.store.events(session_id) if event.turn_id == episode.get("turnId")]
        pending = self._project_pending_approval({**episode, "state": "waiting_approval"}, events)
        if pending is None:
            raise ValueError("That agent is no longer waiting for approval")
        # Like the runner's own dispatch: the turn resumes on this project's prepared model boundary
        # (credentials, per-thread model), which a restarted Host has not set up for this session.
        call_id = pending["callId"]
        def resume():
            if not self.runtime.can_resume_approval(session_id):
                # Asked before the Host restarted: the call never ran and cannot be resumed.
                # The episode ends interrupted and the runner continues with a new one, told why.
                self.runtime.abandon_pending_approval(session_id, (
                    f"The Host restarted while {pending['command']} waited for approval; it did not run. "
                    + ("The person declined it." if decision == "decline" else "The person allowed it; ask again if it is still needed.")))
                return
            self._prepare_turn(self._load(session_id))
            self.runtime.resume_approval(session_id, call_id, approved=decision == "accept")
        with self._guard:
            if self._is_active(session_id):
                raise RuntimeError("That agent is already resuming")
            self._project_session_ids.add(session_id)
            self._launch(session_id, resume)
        return {"accepted": True, "threadId": session_id, "turnId": episode["turnId"], "requestId": pending["requestId"],
                "callId": call_id, "decision": decision}

    def project_recover(self, params):
        """One step back to work after a team task stopped for inspection: review it again, resume.

        Parallel coordination blocks the whole project when one team task needs inspection
        (three rejected reviews, say). From the hub a person can only ask for the obvious
        remedy, so it is one command: a fresh independent review of every stopped team task
        that has a candidate, then the root's workflow resumes and coordination carries on.
        """
        root_id = self._required_text(params, "runId")
        started = []
        with self.project_runner.guard:
            root = self.project_store.get(root_id)
            for item in root.get("teamChildren", []):
                child = self.project_store.get(item["runId"])
                if child["state"] != "needs_review":
                    continue
                if not self.project_store.can_review(child):
                    raise ValueError(f"{item['taskId']} needs inspection that a fresh review cannot settle: {child.get('reason', '')[:300]}")
                self.project_review({"operationId": f"{params.get('operationId', uuid.uuid4())}:{child['id']}",
                                     "runId": child["id"], "expectedRevision": child["revision"]})
                started.append(item["taskId"])
            root = self.project_store.get(root_id)
            if root["state"] == "needs_review" and self.project_store.can_review(root):
                self.project_review({"operationId": f"{params.get('operationId', uuid.uuid4())}:{root_id}",
                                     "runId": root_id, "expectedRevision": root["revision"]})
                root = self.project_store.get(root_id)
            if root.get("workflowStatus") == "blocked" or root["control"] == "paused":
                self.project_control({"operationId": f"{params.get('operationId', uuid.uuid4())}:resume",
                                      "runId": root_id, "expectedRevision": root["revision"]}, "resume")
        return {"run": self.project_store.get(root_id), "reviewed": started}

    def project_steer(self, params):
        """Guidance from the person for the rest of the project; it reaches every later prompt."""
        text = " ".join(self._required_text(params, "text").split())[:program.MAX_GUIDANCE_CHARS]
        run_id = self._required_text(params, "runId")
        with self.project_store.transaction() as db:
            run = self.project_store._read(db, run_id)
            if run is None:
                raise ValueError("Unknown project run")
            run["guidance"] = [*(run.get("guidance") or []), text][-program.MAX_GUIDANCE:]
            self.project_store.save(db, run, "guidance_added")
        if (run.get("milestone") or {}).get("programId"):
            program.add_guidance(self.project_store, run["milestone"]["programId"], text)
        return self.project_store.get(run_id)

    def project_read(self, run_id):
        run = self.project_budget_position(self.project_store.get(run_id))
        run["knowledge"] = self.project_knowledge(run)
        run["verificationFreshness"] = "not_assessed"
        if run.get("verifiedCandidate"):
            try:
                expected = run["verifiedCandidate"]
                current = snapshot(self.project_store.home / "workspaces" / run["id"])
                run["verificationFreshness"] = "current" if current["digest"] == expected["digest"] and definition_hash(run) == expected["definitionHash"] else "stale"
            except (ValueError, OSError):
                run["verificationFreshness"] = "unavailable"
        return run

    def project_review(self, params):
        with self.project_runner.guard:
            run_id = self._required_text(params, "runId")
            revision = params.get("expectedRevision")
            if type(revision) is not int or revision < 1:
                raise ValueError("expectedRevision must be a positive integer")
            request = {"action": "review", "id": run_id, "expectedRevision": revision,
                       **{key: params[key] for key in ("tokenBudget", "maxEpisodes", "maxReviewCycles") if key in params}}
            previous = self.project_store.lookup(params.get("operationId"), request)
            if previous:
                return previous
            run = self.project_store.get(run_id)
            if run.get("deliveryReceipt", {}).get("status") == "prepared":
                raise ValueError("Reconcile prepared delivery before starting another review")
            if run["revision"] != revision or not self.project_store.can_review(run):
                raise ValueError("Review requires a settled candidate at the current revision")
            policy = {key: params.get(key, run.get(key, default)) for key, default in
                      (("tokenBudget", 1000), ("maxEpisodes", 8), ("maxReviewCycles", 3))}
            for key, maximum in (("tokenBudget", 1_000_000_000), ("maxEpisodes", 10000), ("maxReviewCycles", 10)):
                if type(policy[key]) is not int or not 1 <= policy[key] <= maximum:
                    raise ValueError(f"Invalid review policy {key}")
            source = self.project_store.home / "workspaces" / run["id"]
            frozen = candidate({**run, "reviewScope": "project", "taskIndex": len(run["plan"])}, source)
            return self.project_store.begin_review(params.get("operationId"), request, frozen, policy)

    def project_deliver(self, params):
        with self.project_runner.guard:
            run_id = self._required_text(params, "runId")
            revision = params.get("expectedRevision")
            if type(revision) is not int or revision < 1:
                raise ValueError("expectedRevision must be a positive integer")
            request = {"action": "deliver", "id": run_id, "expectedRevision": revision}
            operation = params.get("operationId")
            run = self.project_store.lookup(operation, request)
            if run and run.get("deliveryReceipt", {}).get("status") == "delivered":
                return run
            if run is None:
                run = self.project_store.get(run_id)
                if run["revision"] != revision or run.get("deliveryReceipt"):
                    raise ValueError("Read the current revision; retry a prepared delivery with its original operationId")
                if not run.get("verifiedCandidate") or not run.get("reviewCandidate"):
                    raise ValueError("Delivery requires independent whole-project acceptance")
                expected = self.project_store.candidate(run)
                parallel.allowed(self.project_store, run, self.project_store.home / "workspaces" / run_id)
                receipt = delivery.prepare(run, expected, self.project_store.home / "workspaces" / run_id,
                                           self.project_store.home)
                def record(db):
                    current = self.project_store._read(db, run_id)
                    if current["revision"] != revision:
                        raise ValueError("Project changed while preparing delivery")
                    current["deliveryReceipt"] = receipt
                    self.project_store.save(db, current, "delivery_prepared")
                    return current
                run = self.project_store.command(operation, request, record)
            expected = self.project_store.candidate(run)
            delivery.validate(run, expected, self.project_store.home / "workspaces" / run_id)
            receipt = delivery.publish_local(run, run["deliveryReceipt"])
            with self.project_store.transaction() as db:
                current = self.project_store._read(db, run_id)
                current.update(deliveryReceipt=receipt, state="delivered", control="paused",
                               delivery="local_verified_branch", reason="Verified local branch delivered; merge and remote publication pending")
                self.project_store.save(db, current, "delivered")
                return current


class ProjectControllerMixin:
    def _initialize(self, params):
        result = super()._initialize(params)
        result.setdefault("capabilities", {})["projectRunner"] = {
            "version": 7, "maxWorkers": 2, "delivery": "local_verified_branch", "tokenLimit": "request_boundary",
            "independentReview": True, "automaticRework": True, "reviewedIntegration": True, "automaticLocalCompletion": True}
        return result

    def _dispatch(self, method, params):
        if method == "projectRun/create":
            return {"run": self.service.project_run_create(params)}
        if method == "projectRun/overview":
            return self.service.project_overview(params)
        if method == "projectRun/steer":
            return {"run": self.service.project_steer(params)}
        if method == "projectRun/recover":
            return self.service.project_recover(params)
        if method == "projectRun/commitWorkspace":
            return self.service.project_commit_workspace(params)
        if method == "projectRun/approval":
            return self.service.project_approval(params)
        if method == "projectProgram/create":
            with self.service.project_runner.guard:
                return {"program": program.create(self.service, params)}
        if method == "projectProgram/attach":
            with self.service.project_runner.guard:
                return {"program": program.attach(self.service, params)}
        if method == "projectProgram/get":
            return {"program": program.get(self.service.project_store, self.service._required_text(params, "programId"))}
        if method == "projectProgram/list":
            return {"programs": program.list_programs(self.service.project_store)}
        if method == "projectRun/get":
            return {"run": self.service.project_read(self.service._required_text(params, "runId"))}
        if method == "projectRun/list":
            return {"runs": [{**self.service.project_budget_position(run), "knowledge": self.service.project_knowledge(run)}
                             for run in self.service.project_store.list()],
                    "error": self.service.project_runner.error}
        if method == "projectRun/events":
            return {"events": self.service.project_store.events(self.service._required_text(params, "runId"),
                    int(params.get("afterSequence", 0)), int(params.get("limit", 100)))}
        if method == "projectRun/assessments":
            return {"assessments": self.service.project_store.assessments(self.service._required_text(params, "runId"))}
        if method == "projectRun/review":
            return {"run": self.service.project_review(params)}
        if method == "projectRun/deliver":
            return {"run": self.service.project_deliver(params)}
        if method in {"projectRun/prepareIntegration", "projectRun/applyIntegration"}:
            with self.service.project_runner.guard:
                handler = integration.start if method.endswith("prepareIntegration") else integration.apply
                return {"run": handler(self.service, params)}
        if method in {f"projectRun/{action}" for action in ("start", "pause", "resume", "cancel")}:
            return {"run": self.service.project_control(params, method.split("/")[1])}
        return super()._dispatch(method, params)
