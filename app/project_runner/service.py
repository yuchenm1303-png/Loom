"""Host adapter and RPC for the first serial project execution slice."""
from __future__ import annotations

import copy
from pathlib import Path

from app.agent_runtime import PermissionMode
from app.agent_runtime.contracts import AgentEventKind as E
from app.app_server_local_ipc import runtime_home_from_store
from .runner import ProjectRunner, review_target
from .store import ProjectStore
from .review import git, candidate, review_workspace, assess, snapshot, definition_hash
from . import delivery, integration, workflow, parallel, checks, program
from .budget import BudgetedModelExecutor, ProjectBudget, event_tokens
from .store import encoded
from app.agent_runtime.context_budget import estimate_tokens

# History a continued episode may carry before the runner compacts it at the boundary.
EPISODE_CARRY_TOKENS = 12000
# Working context of a project session: auto-compaction keeps requests under it.
PROJECT_WORKING_CONTEXT_TOKENS = 48000


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
            workspace = review_workspace(run, expected, workspace, self.home / "reviews" / run["id"] / expected["id"])
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

    def accepted_checks(self, run, session_id):
        """The project's kept checks, copied into this session's scratch and rendered for it."""
        scratch = self.service.runtime._run_scratch_dir(session_id, "accepted-checks")
        workspace = self.service._load(session_id).workspace_dir
        if scratch is None:
            return []
        scratch.mkdir(parents=True, exist_ok=True)
        return checks.provide(self.service.project_store, run, scratch, workspace)

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
                return {"state": "interrupted", "interrupted": True, "sideEffectFree": no_tools,
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
        if method == "projectProgram/create":
            with self.service.project_runner.guard:
                return {"program": program.create(self.service, params)}
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
