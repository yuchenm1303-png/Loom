"""Host adapter and RPC for the first serial project execution slice."""
from __future__ import annotations

import copy
from pathlib import Path

from app.agent_runtime import PermissionMode
from app.agent_runtime.contracts import AgentEventKind as E
from app.app_server_local_ipc import runtime_home_from_store
from .runner import ProjectRunner
from .store import ProjectStore
from .review import git, candidate, review_workspace, assess, snapshot, definition_hash
from . import delivery, integration


class ProjectExecutor:
    def __init__(self, service, home):
        self.service, self.home = service, home

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
            git(run["repository"], "worktree", "add", "--detach", str(workspace), run["baseCommit"])
        # Never assume a preexisting directory is the expected checkout.
        common = Path(git(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
        if common != Path(run["gitCommonDir"]):
            raise ValueError("project workspace identity changed; inspect before continuing")
        git(workspace, "merge-base", "--is-ancestor", run["baseCommit"], "HEAD")
        if run["phase"] == "verifying":
            expected = self.service.project_store.candidate(run)
            workspace = review_workspace(run, expected, workspace, self.home / "reviews" / run["id"] / expected["id"])
        if key in run["sessions"]:
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

    def result_facts(self, run, episode, reply):
        source = self.home / "workspaces" / run["id"]
        if run.get("reviewPolicy") != "independent":
            return {}
        if episode["phase"] == "working" and reply.get("action") == "candidate":
            return {"candidate": candidate(run, source)}
        if episode["phase"] == "verifying":
            expected = self.service.project_store.candidate(run)
            workspace = Path(self.service._load(episode["sessionId"]).workspace_dir)
            events = self.service.store.events(episode["sessionId"])
            assessment = assess(run, episode, reply, expected, source, workspace, events)
            facts = {"assessment": assessment}
            if assessment["verdict"] == "passed" and run["reviewScope"] == "task" and run["taskIndex"] + 1 == len(run["plan"]):
                facts["finalCandidate"] = candidate({**run, "reviewScope": "project", "taskIndex": len(run["plan"])}, source)
            return facts
        return {}

    def dispatch(self, episode):
        service = self.service
        session_id = episode["sessionId"]
        def execute():
            def cancelled():
                return service.project_runner.stop.is_set() or service.project_store.get(episode["runId"])["state"] == "cancelled"
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
                E.TURN_COMPLETED, E.TURN_FAILED, E.TURN_CANCELLED, E.TURN_INTERRUPTED}), None)
            if terminal is None or session.current_turn_id != episode["turnId"]:
                return {"state": "outcome_unknown", "error": service._task_errors.get(session.session_id)
                        or "No matching durable terminal turn; execution must not be replayed"}
            tokens = 0
            for e in events:
                if e.kind in {E.MODEL_RESPONSE, E.MODEL_RESPONSE_REJECTED, E.TURN_STOP_CHECKED}:
                    tokens += int(e.data.get("usage", {}).get("total_tokens") or 0)
                elif e.kind is E.CONTEXT_CHECKPOINTED:
                    tokens += int(e.data.get("summary_usage", {}).get("total_tokens") or 0)
            return {"state": session.status.value, "text": session.final_text, "error": session.error, "tokens": tokens}

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


class ProjectServiceMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        home = runtime_home_from_store(self.store) / "project-runner"
        self.project_store = ProjectStore(home)
        with self.project_store.lock:
            self._project_session_ids = {row[0] for row in self.project_store.db.execute("SELECT DISTINCT session_id FROM project_episodes")}
        executor = ProjectExecutor(self, home)
        self.project_runner = ProjectRunner(self.project_store, executor)
        on_activated = self.runtime._on_turn_activated
        def project_activated(session_id, token):
            on_activated(session_id, token)
            if session_id not in self._project_session_ids:
                return
            run = self.project_store.session_run(session_id)
            if run and (run["state"] == "cancelled" or self.project_runner.stop.is_set()):
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
        if not isinstance(policy, str) or policy not in {"manual", "independent"} or type(cycles) is not int or not 1 <= cycles <= 10:
            raise ValueError("reviewPolicy must be manual or independent; maxReviewCycles must be within 1..10")
        if type(budget) is not int or not 1 <= budget <= 1_000_000_000:
            raise ValueError("tokenBudget must be a positive integer")
        if type(episodes) is not int or not 1 <= episodes <= 10000:
            raise ValueError("maxEpisodes must be within 1..10000")
        request = {"threadId": source.session_id, "objective": objective, "tokenBudget": budget, "maxEpisodes": episodes}
        # Omitted fields preserve retry fingerprints for already-created v1 runs.
        for key in ("reviewPolicy", "maxReviewCycles"):
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
        return self.project_store.create(params.get("operationId"), {
            "sourceThreadId": source.session_id, "objective": objective, "tokenBudget": budget, "maxEpisodes": episodes,
            "repository": str(root), "baseCommit": git(root, "rev-parse", "HEAD"),
            "gitCommonDir": str(Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()),
            "budgetEnforcement": "between_episodes", "delivery": "isolated_candidate_only",
            "reviewPolicy": policy, "maxReviewCycles": cycles}, request=request)

    def project_control(self, params, action):
        with self.project_runner.guard:
            run = self.project_store.control(params.get("operationId"), self._required_text(params, "runId"),
                                             params.get("expectedRevision"), action)
            if action == "cancel" and run["activeEpisode"]:
                self.project_runner.executor.cancel(run["activeEpisode"])
            return run

    def project_read(self, run_id):
        run = self.project_store.get(run_id)
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
            "version": 4, "maxWorkers": 1, "delivery": "local_verified_branch", "tokenLimit": "between_episodes",
            "independentReview": True, "automaticRework": True, "reviewedIntegration": True}
        return result

    def _dispatch(self, method, params):
        if method == "projectRun/create":
            return {"run": self.service.project_run_create(params)}
        if method == "projectRun/get":
            return {"run": self.service.project_read(self.service._required_text(params, "runId"))}
        if method == "projectRun/list":
            return {"runs": self.service.project_store.list(), "error": self.service.project_runner.error}
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
