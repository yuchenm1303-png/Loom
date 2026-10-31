"""Bounded task execution; an ended conversation is never a completed project."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

from .store import encoded
from .review import criteria, StaleCandidate
from . import parallel
from .budget import ProjectBudget
from .errors import ReplyFormatError
from . import checks
from .janitor import Janitor

# How an episode ended decides what the runner does next, from the fields the
# runtime records (limit kind, provider_failure, retryable, status). The string
# fallbacks below only read events written before those fields existed.
#
#   kind                what happened                               next
#   budget              a request was refused at the token budget   budget_limited (add budget)
#   episode_bound       the episode hit its step/tool limit         next episode continues
#   interrupted         the Host stopped mid-episode; nothing in    next episode continues (no replay)
#                       flight could reach past its checkout
#   no_progress         it kept repeating one failing operation     next episode, told to change approach
#   provider_transient  provider/network failed, retryable          wait 30 s .. 30 min, continue
#   credential          the provider refused the key or account     wait 5 .. 60 min (an account token is
#                       (HTTP 401/402/403)                          often refreshed meanwhile); after that,
#                                                                   credential_blocked: the person fixes it
#                                                                   and resumes. Never "needs inspection"
#   review_evidence     a review verdict lacked accepted evidence   back to the reviewer, with why
#   reply_format        a final reply the runner cannot read        back to the model, with why
#   scope               a team task changed another task's files    back to the worker, with why
#   (anything else)     settled normally, or stopped for inspection
#
# Each recoverable kind counts consecutive occurrences on the run; one more than its
# allowance stops the run for inspection. Any accepted episode resets every counter.
MAX_EPISODE_BOUND_RETRIES = 7
MAX_TRANSIENT_STREAK = 10
MAX_INTERRUPTIONS = 5
# Retries over about three hours (5, 10, 20, 40, 60, 60 min) before asking the person.
MAX_CREDENTIAL_RETRIES = 6
MAX_REVIEW_REJECTIONS = 2
RECOVERY = {
    "episode_bound": ("episodeLimitStreak", lambda: MAX_EPISODE_BOUND_RETRIES, "episode_limit_reached"),
    # A Host update, sleep or crash mid-episode with nothing in flight that could reach past
    # the episode's own checkout. Anything else in flight stops the run for reconciliation.
    "interrupted": ("interruptionStreak", lambda: MAX_INTERRUPTIONS, "interrupted_episode_continued"),
    # The runtime's loop guard (like OpenHands' stuck detector) ends a turn that keeps
    # repeating a failing operation; the corrective step is a new approach, not a halt.
    "no_progress": ("noProgressStreak", lambda: MAX_REVIEW_REJECTIONS, "no_progress_retry"),
    "provider_transient": ("transientFailureStreak", lambda: MAX_TRANSIENT_STREAK, "transient_failure_retry"),
    "credential": ("credentialStreak", lambda: MAX_CREDENTIAL_RETRIES, "credential_retry"),
    "review_evidence": ("reviewEvidenceRejections", lambda: MAX_REVIEW_REJECTIONS, "review_evidence_rejected"),
    "reply_format": ("replyRejections", lambda: MAX_REVIEW_REJECTIONS, "reply_rejected"),
    "scope": ("scopeRejections", lambda: MAX_REVIEW_REJECTIONS, "scope_rejected"),
}
CONTINUABLE_LIMITS = {"model_steps", "tool_calls"}
PERMANENT_STATUS = {400, 401, 402, 403, 404}
CREDENTIAL_STATUS = {401, 402, 403}
CREDENTIAL_MARKERS = (" 401", " 402", " 403", "status 401", "status 402", "status 403", "unauthorized",
                      "invalid api key", "api key is invalid", "insufficient balance", "insufficient_quota")
LEGACY_EPISODE_LIMITS = {"model step limit reached", "tool call limit reached"}
TRANSIENT_MARKERS = ("apiconnectionerror", "connection error", "apitimeouterror", "timed out", "timeout",
                     "remotedisconnected", "connection reset", "connection aborted", "stream_stall",
                     "stopped producing output", "temporarily", "overloaded", " 429", " 500", " 502", " 503", " 504",
                     "status 429", "status 500", "status 502", "status 503", "status 504")
PERMANENT_MARKERS = (" 401", " 402", " 403", "status 401", "status 402", "status 403", "insufficient balance",
                     "api_key", "invalid api key", "quota", "额度", "unauthorized", "forbidden")


def transient_failure(error):
    """Message-text classification, for failures recorded before provider_failure existed."""
    text = f" {str(error or '').lower()} "
    if not text.strip().startswith("aitransporterror") and "modelrequesttimeout" not in text:
        return False  # only provider transport failures; a tool or logic error is the work's own
    return any(marker in text for marker in TRANSIENT_MARKERS) and not any(marker in text for marker in PERMANENT_MARKERS)


def provider_transient(result):
    if result.get("providerFailure") is None:
        return transient_failure(result.get("error"))
    status = result.get("providerStatus")
    return bool(result["providerFailure"]) and result.get("retryable") is not False and status not in PERMANENT_STATUS


def provider_refused(result):
    """The provider refused the key or the account (not the request): fixable outside the project."""
    if result.get("providerFailure") is None:
        text = str(result.get("error") or "").lower()
        return any(marker in text for marker in CREDENTIAL_MARKERS)
    return bool(result["providerFailure"]) and result.get("providerStatus") in CREDENTIAL_STATUS


def episode_outcome(state, result, refused):
    if state != "completed" and refused:
        return "budget"
    if result.get("interrupted") and (result.get("sideEffectFree") or result.get("confined")):
        return "interrupted"
    if state == "limit_reached" and (result.get("limit") in CONTINUABLE_LIMITS or (
            result.get("limit") is None and result.get("error") in LEGACY_EPISODE_LIMITS)):
        return "episode_bound"
    if state == "limit_reached" and result.get("limit") == "no_progress":
        return "no_progress"
    if state == "failed" and provider_transient(result):
        return "provider_transient"
    if state == "failed" and provider_refused(result):
        return "credential"
    if result.get("reviewRejected"):
        return "review_evidence"
    if result.get("replyRejected"):
        return "reply_format"
    if result.get("scopeRejected"):
        return "scope"
    return None


def object_reply(text):
    # The reply is the first JSON object in the final text. The runtime may append a
    # verification note after it ("Runtime verification: ..."), and models wrap JSON
    # in fences or a lead-in; a strict parse stopped a real five-task project on that.
    text = str(text)
    start = text.find("{")
    try:
        if start < 0:
            raise ValueError("no JSON object")
        value, _ = json.JSONDecoder().raw_decode(text, start)
    except ValueError as exc:
        raise ReplyFormatError(f"final reply must contain one JSON object ({exc})") from None
    if not isinstance(value, dict):
        raise ReplyFormatError("worker response must be a JSON object")
    return value


def review_target(run):
    """The review a worker's candidate goes to.

    The last task of a run goes straight to whole-project review: it covers
    every criterion, that task's included, so a separate task review before it
    only re-verified the same bytes. Earlier tasks keep their own review as the
    gate to the next task. A parallel team task is a one-task run, so its one
    review is at project scope too; the merged aggregate still gets its own.
    """
    if run["taskIndex"] == len(run["plan"]) - 1:
        return {**run, "reviewScope": "project", "taskIndex": len(run["plan"])}
    return {**run, "reviewScope": "task"}


def validate_plan(value):
    tasks = value.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 16:
        raise ReplyFormatError("planner must return 1..16 ordered tasks")
    plan = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or not isinstance(task.get("instructions"), str) or not task["instructions"].strip():
            raise ReplyFormatError("each task needs instructions")
        acceptance = task.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance or len(acceptance) > 16 or any(
                not isinstance(c, str) or not c.strip() or len(c) > 2000 for c in acceptance):
            raise ReplyFormatError("each task needs concrete acceptance criteria")
        if len(task["instructions"]) > 12000:
            raise ReplyFormatError("task instructions too long")
        plan.append({"id": f"task-{index+1}", "instructions": task["instructions"], "acceptance": acceptance,
                     "state": "pending"})
    return plan


class ProjectRunner:
    def __init__(self, store, executor, budget=None):
        self.store, self.executor = store, executor
        self.budget = budget or ProjectBudget(lambda: self.store, set())
        self.janitor = Janitor()
        self.lease = (store.home / "scheduler.lock").open("a+b")
        try:
            if os.fstat(self.lease.fileno()).st_size == 0:
                self.lease.write(b"0")
                self.lease.flush()
            self.lease.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lease.close()
            raise RuntimeError("project scheduler already has an owner") from None
        self.guard = threading.RLock()
        self.stop = threading.Event()
        self.thread = None
        self.error = ""
        self.closed = False

    def start(self):
        if self.closed or (self.thread and self.thread.is_alive()):
            raise RuntimeError("project scheduler is closed or already running")
        self.thread = threading.Thread(target=self._loop, daemon=True, name="loom-project-runner")
        self.thread.start()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.stop.set()
        if self.thread:
            self.thread.join()
        waits = self.executor.shutdown()
        def release():
            for event in waits:
                event.wait()
            self.store.close()
            self.lease.close()
        if any(not event.is_set() for event in waits):
            threading.Thread(target=release, name="loom-project-drain", daemon=True).start()
        else:
            release()

    def _loop(self):
        while not self.stop.wait(1):
            try:
                self.tick()
                self.error = ""
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"

    def prompt(self, run, kept=(), references=()):
        note = run.get("replyNote")
        text = self._prompt(run, list(kept))
        if references:
            # Files the person attached to the program (a spec, sample data): copies in this session's scratch.
            text = ("The person attached reference files for this project. Read them where they bear on your work; "
                    "they are copies, so never edit them and never copy them into the repository unless the plan says so:\n"
                    + "\n".join(f"- {path}" for path in references) + "\n" + text)
        guidance = self._guidance(run)
        if guidance:
            # What the person added while the project ran (projectRun/steer): it outranks the
            # plan where they conflict, and later notes outrank earlier ones.
            text = ("The person directing this project added guidance (newest last; where it conflicts with the plan "
                    "or earlier guidance, follow the newest):\n" + "\n".join(f"- {item}" for item in guidance) + "\n" + text)
        if run["phase"] == "planning" and (run.get("milestone") or {}).get("index", 1) > 1:
            # A later milestone of a program: the answer may also be "nothing left to build".
            text += ('\nIf the integrated repository already achieves the entire objective, return instead only JSON '
                     '{"complete":true,"summary":"what in the repository shows each part of the objective is done"}.')
        return (f"The runner could not accept your previous final reply: {note} End with only the required JSON object.\n" + text) if note else text

    def _guidance(self, run):
        """Guidance is kept on the project's root run; team tasks and the integration run read it there."""
        owner = checks.registry_root(run)
        if owner == run["id"]:
            return list(run.get("guidance") or [])
        try:
            return list(self.store.get(owner).get("guidance") or [])
        except ValueError:
            return []

    def _prompt(self, run, kept):
        if run["phase"] == "planning":
            if run.get("maxWorkers", 1) > 1:
                return ('Plan this project as ordered task IDs task-1, task-2, etc. Return only JSON {"tasks":[{"instructions":"scoped implementation",'
                    '"acceptance":["observable behavior"],"dependsOn":[],"fileScopes":["relative/file.py"],"sharedResources":[]}]}. '
                    "Use 1..16 tasks. Dependencies may reference only earlier task IDs. Only explicitly independent tasks with disjoint file scopes and no shared resources run together. "
                    "A fileScope may name a directory (tests) when a task needs supporting files such as tests/__init__.py. "
                    "List common ports, databases, GUI/browser or environment resources in sharedResources. Omitted declarations serialize safely.\n" + run["objective"])
            return ("Plan this long-running project. Do not edit files or execute commands. Return only JSON: "
                    '{"tasks":[{"instructions":"scoped implementation", "acceptance":["observable behavior"]}]}. '
                    "Tasks run in order; cover the entire objective with at most 16 tasks.\n" + run["objective"])
        if run["phase"] == "verifying":
            return ("Independently verify this frozen project candidate in your separate checkout. "
                    "Do not implement fixes, change source/tests/acceptance, commit, merge, or publish. "
                    "Inspect the actual code and exercise behavior; worker summaries are claims, not proof. "
                    "Build/type-check success alone is not behavioral acceptance; use functional checks or exercised browser assertions. "
                    "check_kind static is only for builds and type checks and never counts as acceptance evidence; a command that "
                    "asserts a criterion (including a source property such as allowed imports) and exits nonzero on violation is functional. "
                    "Use exec with wait=true, pty=false and verification.case_id equal to each criterion ID, "
                    "verification.expectation exactly equal to its expectation, and subject_paths covering actual candidate source. "
                    + ("For parallel task review, do not use browser/desktop, bind server ports or mutate shared environments; report blocked if these are necessary. "
                       if run.get("teamOwnerId") else "Browser interactions may use browser_run_check with the same IDs. ")
                    + "Ignore generated outputs only via the repository's existing ignore rules. "
                    "Check the entire objective for missing behavior, not just the declared test names. "
                    "Return only JSON: {\"verdict\":\"passed|needs_changes|blocked|continue\",\"summary\":\"observed facts\","
                    "\"notes\":[\"optional: environment facts or pitfalls later sessions should know\"],"
                    "\"checks\":[{\"criterionId\":\"task-1:1\",\"eventId\":\"real CHECK_RECORDED event id\"}],"
                    "\"issues\":[{\"taskId\":\"task-1\",\"description\":\"reproducible problem and expected behavior\"}]}. "
                    "eventId is optional: the runner resolves the latest real check for each criterion in this review session; never invent IDs. "
                    "passed requires all criteria with current executed evidence; needs_changes requires concrete scoped issues. "
                    "Use blocked if the environment cannot verify behavior; continue if more checking needs another episode.\n"
                    + (f"The runner rejected your previous verdict: {run['reviewNote']} Record the missing or corrected evidence, then report again. "
                       if run.get("reviewNote") else "")
                    + ("acceptedChecks are commands an earlier independent review ran and accepted for these criteria. Re-run them "
                       "first (they are cheap), with the same verification fields, and judge whether each still exercises its "
                       "criterion; then probe what they miss. They are evidence only once you run them here. A check whose "
                       "command changes after it was recorded is not evidence in this episode: if one fails for an "
                       "environment reason (a sandbox denial, a missing path) rather than behavior, fix it and report "
                       "verdict continue so the next episode records the corrected command first. "
                       if self._scoped(kept, run, review=True) else "")
                    + encoded({"objective": run["objective"], "scope": run["reviewScope"], "criteria": criteria(run),
                               "plan": run["plan"], "candidate": run["reviewCandidate"],
                               "acceptedChecks": self._scoped(kept, run, review=True),
                               "projectNotes": checks.notes_for(self.store, run)}))
        task = run["plan"][run["taskIndex"]]
        return ("Continue this persistent project in its isolated workspace. Preserve prior work. "
                "Use normal Loom tools and permissions. Run concrete checks for every acceptance criterion. "
                "Do not merge, push, deploy, or change branches. A final response ends only this episode. "
                'Return only JSON: {"action":"continue|candidate|blocked","summary":"facts and next steps",'
                '"notes":["optional: environment facts or pitfalls later sessions should know"]}. '
                "Use candidate only when this task is implemented and checked; it remains subject to independent review.\n"
                + ("Work only inside declared fileScopes. Do not use desktop/browser, start servers or mutate shared/external environments. " if run.get("teamOwnerId") else "")
                + ("Before new work, re-run the regressionChecks (accepted checks of earlier tasks); if one fails, fix that first. "
                   if self._scoped(kept, run, review=False) else "")
                + encoded({"objective": run["objective"], "task": task, "priorWork": run.get("handoff", ""),
                           "reviewFeedback": run.get("feedback", []), "regressionChecks": self._scoped(kept, run, review=False),
                           "projectNotes": checks.notes_for(self.store, run)}))

    @staticmethod
    def _scoped(kept, run, *, review):
        """A review gets the kept checks of the criteria it judges; a worker those of other tasks."""
        if review:
            wanted = {item["id"] for item in criteria(run)}
            return [item for item in kept if item["criterionId"] in wanted]
        own = run["plan"][run["taskIndex"]]["id"] + ":" if run["plan"] and run["taskIndex"] < len(run["plan"]) else None
        return [item for item in kept if not (own and item["criterionId"].startswith(own))][:checks.MAX_CHECKS_IN_PROMPT]

    def tick(self):
        with self.guard:
            try:
                self.janitor.sweep(self.store)
            except Exception:
                pass  # cleanup is never worth stopping the scheduler for
            self.budget.apply_late()
            # Reconcile even while paused/cancelled; never drop late usage/results.
            for run in self.store.running():
                self._poll(run)
            # A finished milestone starts the next one of its program.
            advance_programs = getattr(self.executor, "advance_programs", None)
            if advance_programs and advance_programs():
                return
            # Every list here re-read all runs once per run: over a multi-day program (tens of
            # milestones, hundreds of finished runs) a tick took seconds and grew with the square.
            def counted():
                return [r for r in self.store.running() if r["activeEpisode"]["state"] != "outcome_unknown"]
            active = counted()  # read again only after this tick starts an episode
            for run in self.store.live():
                advance_parallel = getattr(self.executor, "advance_parallel", None)
                if advance_parallel and advance_parallel(run):
                    return
                if run["activeEpisode"] or not parallel.eligible(self.store, run, active):
                    continue
                advance = getattr(self.executor, "advance_workflow", None)
                if advance and advance(run):
                    return
                if run["state"] == "running" and run["control"] == "active" and run["phase"] != "parallel":
                    if run.get("workflowOwnerId"):
                        owner = self.store.get(run["workflowOwnerId"])
                        if (owner.get("workflowControl") != "active" or owner.get("workflowStatus") in {"blocked", "completed"}
                                or owner.get("integrationRunId") != run["id"]):
                            continue
                    self._dispatch(run)
                    if not run.get("teamOwnerId"):
                        return
                    active = counted()

    def _dispatch(self, run):
        if run.get("retryAfter", 0) > time.time():
            return  # waiting out a provider/network outage
        budget_owner = parallel.owner(self.store, run) or run
        if (budget_owner["episodesUsed"] >= budget_owner["maxEpisodes"]
                or self.budget.report(run)["chargedTokens"] >= budget_owner["tokenBudget"]):
            with self.store.transaction() as db:
                current = self.store._read(db, run["id"])
                if current["revision"] == run["revision"]:
                    current.update(control="budget_limited", reason="Episode or token budget reached")
                    self.store.save(db, current, "budget_limited")
            return
        try:
            session_id = self.executor.session(run)
        except StaleCandidate as exc:
            with self.store.transaction() as db:
                current = self.store._read(db, run["id"])
                if current["revision"] == run["revision"]:
                    self._rework(current, {"scope": current.get("reviewScope", "task"), "verdict": "stale",
                                          "summary": str(exc), "issues": []})
                    self.store.save(db, current, "review_invalidated")
            return
        except Exception as exc:
            self._block(run, str(exc))
            return
        provide = getattr(self.executor, "accepted_checks", None)
        try:
            kept = provide(run, session_id) if provide else []
        except (OSError, ValueError):
            kept = []
        attached = getattr(self.executor, "references", None)
        try:
            references = attached(run, session_id) if attached else []
        except (OSError, ValueError):
            references = []
        episode = {"id": str(uuid.uuid4()), "runId": run["id"], "turnId": str(uuid.uuid4()), "sessionId": session_id,
                   "phase": run["phase"], "taskIndex": run["taskIndex"], "prompt": self.prompt(run, kept, references), "state": "prepared"}
        # Session creation may leave an orphan after a crash; it has no execution authority.
        with self.store.transaction() as db:
            current = self.store._read(db, run["id"])
            if current["revision"] != run["revision"] or current["control"] != "active":
                return
            current["sessions"][self.executor.session_key(run)] = session_id
            current["activeEpisode"] = episode
            current["episodesUsed"] += 1
            if current.get("teamOwnerId"):
                parent = self.store._read(db, current["teamOwnerId"])
                if parent["episodesUsed"] >= parent["maxEpisodes"] or parent["tokensUsed"] >= parent["tokenBudget"]:
                    raise ValueError("Shared project budget exhausted")
                parent["episodesUsed"] += 1
                self.store.save(db, parent, "parallel_episode_reserved")
            db.execute("INSERT INTO project_episodes VALUES(?,?,?,?,?,?)",
                       (episode["id"], run["id"], session_id, episode["turnId"], "prepared", encoded(episode)))
            self.store.save(db, current, "episode_prepared")
        # Intent precedes dispatch. An absent receipt after restart is UNKNOWN, never replayed.
        try:
            self.executor.dispatch(episode)
        except Exception as exc:
            self._block(self.store.get(run["id"]), f"Dispatch outcome requires reconciliation: {exc}")

    def _block(self, run, reason):
        with self.store.transaction() as db:
            current = self.store._read(db, run["id"])
            if current["state"] != "cancelled":
                current.update(state="needs_review", control="paused", reason=reason)
                self.store.save(db, current, "needs_review")

    @staticmethod
    def _rework(run, assessment):
        scope = assessment["scope"]
        key = "project" if scope == "project" else run["plan"][run["taskIndex"]]["id"]
        failures = run.setdefault("reviewFailures", {})
        failures[key] = failures.get(key, 0) + 1
        issues = assessment["issues"]
        index = min((i for i, task in enumerate(run["plan"]) if any(issue["taskId"] == task["id"] for issue in issues)),
                    default=0 if scope == "project" else run["taskIndex"])
        run["feedback"] = issues or [{"taskId": run["plan"][index]["id"], "description": assessment["summary"]}]
        run.update(phase="working", taskIndex=index, reviewScope="task", reviewCandidate=None,
                   handoff="Independent review requires changes: " + assessment["summary"], verificationStatus="pending")
        for task in run["plan"][index:]:
            task["state"] = "pending"
        if failures[key] >= run.get("maxReviewCycles", 3):
            run.update(state="needs_review", control="paused", reason=f"Review cycle limit reached for {key}")

    def _review_result(self, db, run, episode, facts):
        assessment = facts["assessment"]
        self.store.put_assessment(db, run, episode, assessment)
        verdict = assessment["verdict"]
        if verdict in {"needs_changes", "stale"}:
            self._rework(run, assessment)
        elif verdict == "blocked":
            run.update(state="needs_review", control="paused", reason=assessment["summary"])
        elif verdict == "passed" and run["reviewScope"] == "project":
            for task in run["plan"]:
                task["state"] = "verified"
            run.update(state="ready_for_review", control="paused", verificationStatus="passed",
                       verifiedCandidate=run["reviewCandidate"], reason="Independent acceptance passed; integration and publication remain pending")
        elif verdict == "passed":
            run["plan"][run["taskIndex"]]["state"] = "verified"
            run["taskIndex"] += 1
            run["feedback"] = []
            run["reviewCandidate"] = None
            if run["taskIndex"] == len(run["plan"]):
                run.update(reviewScope="project", phase="verifying")
                self.store.put_candidate(db, run, facts["finalCandidate"])
            else:
                run["phase"] = "working"

    def _poll(self, run):
        episode = run["activeEpisode"]
        result = self.executor.poll(episode)
        if result is None:
            return
        facts = {}
        if result["state"] == "completed":
            try:
                facts = self.executor.result_facts(run, episode, object_reply(result["text"]))
            except (ValueError, TypeError, OSError) as exc:
                result = {**result, "state": "failed", "error": f"Candidate review facts rejected: {exc}",
                          "reviewRejected": episode["phase"] == "verifying",
                          "replyRejected": isinstance(exc, ReplyFormatError) and episode["phase"] != "verifying",
                          "scopeRejected": isinstance(exc, parallel.ScopeViolation)}
        with self.store.transaction() as db:
            current = self.store._read(db, run["id"])
            if not current["activeEpisode"] or current["activeEpisode"]["id"] != episode["id"]:
                return
            state = result["state"]
            if state == "waiting_approval":
                if current["activeEpisode"]["state"] != state:
                    current["activeEpisode"]["state"] = state
                    db.execute("UPDATE project_episodes SET state=? WHERE id=?", (state, episode["id"]))
                    self.store.save(db, current, "waiting_approval")
                return
            if state == "outcome_unknown":
                if current["activeEpisode"]["state"] != state:
                    current["activeEpisode"]["state"] = state
                    db.execute("UPDATE project_episodes SET state=? WHERE id=?", (state, episode["id"]))
                    if current["state"] != "cancelled":
                        current.update(state="needs_review", control="paused", reason=result.get("error", "Unknown execution outcome"))
                    self.store.save(db, current, "needs_review")
                return
            # Usage is charged by event, exactly once, before the episode closes.
            result = {**result, "tokens": self.budget.settle(db, current, episode, result, self.store.save)}
            current["activeEpisode"] = None
            db.execute("UPDATE project_episodes SET state=?,data=? WHERE id=?",
                       (state, encoded({**episode, "result": result}), episode["id"]))
            if current["state"] == "cancelled":
                self.store.save(db, current, "cancelled_episode_settled")
                return
            kind = episode_outcome(state, result, self.budget.refused(db, episode))
            if kind == "budget":
                # The worker was stopped before a request, not by its work: keep
                # the task where it is so explicitly added budget can continue it.
                current.update(control="budget_limited", reason=result.get("error") or "Project token budget reached")
                current["handoff"] = ("The previous episode stopped at the project token budget before reporting; "
                                      "inspect the workspace for its partial work.\n" + current.get("handoff", ""))[:12000]
                self.store.save(db, current, "budget_limited")
                return
            if kind:
                self.store.save(db, current, self._recover(current, kind, result.get("error")))
                return
            try:
                if state != "completed":
                    raise ValueError(result.get("error") or f"Episode ended with {state}")
                reply = object_reply(result["text"])
                checks.add_notes(db, current, reply.get("notes"))
                if episode["phase"] == "planning" and (current.get("milestone") or {}).get("index", 1) > 1 \
                        and reply.get("complete") is True and not reply.get("tasks"):
                    # A later milestone's planner found the objective achieved in the
                    # integrated repository: nothing to build, the program is done.
                    summary = reply.get("summary")
                    if not isinstance(summary, str) or not summary.strip():
                        raise ReplyFormatError("a complete objective needs a summary of the evidence")
                    current.update(state="integrated", workflowStatus="completed", workflowControl="paused",
                                   objectiveComplete=True, plan=[], reason=summary[:2000])
                elif episode["phase"] == "planning":
                    # Validate the whole plan before touching the run: a rejected plan
                    # goes back to the planner and must leave the run in planning.
                    plan = validate_plan(reply)
                    if current.get("maxWorkers", 1) > 1:
                        parallel.configure(plan, reply["tasks"])
                        current.update(plan=plan, phase="parallel", parallelStatus="active", teamChildren=[])
                    else:
                        current.update(plan=plan, phase="working")
                elif episode["phase"] == "verifying":
                    self._review_result(db, current, episode, facts)
                else:
                    action, summary = reply.get("action"), reply.get("summary")
                    if action not in {"continue", "candidate", "blocked"} or not isinstance(summary, str) or not summary.strip():
                        raise ReplyFormatError("worker must report action and nonempty summary")
                    current["handoff"] = summary[:12000]
                    if action == "blocked":
                        raise ValueError(summary[:2000])
                    if action == "candidate":
                        current["plan"][episode["taskIndex"]]["state"] = "candidate"
                        if current.get("reviewPolicy") == "independent":
                            target = review_target(current)
                            current.update(phase="verifying", reviewScope=target["reviewScope"], taskIndex=target["taskIndex"],
                                           verificationStatus="pending")
                            self.store.put_candidate(db, current, facts["candidate"])
                        else:
                            current["taskIndex"] += 1
                            if current["taskIndex"] == len(current["plan"]):
                                current.update(state="ready_for_review", control="paused",
                                               reason="Implementation candidates only; independent acceptance and integration required")
                for counter, _, _ in RECOVERY.values():  # an accepted episode ends every streak
                    current[counter] = 0
                current.update(reviewNote="", replyNote="")
            except ReplyFormatError as exc:
                self.store.save(db, current, self._recover(current, "reply_format", str(exc)))
                return
            except (ValueError, TypeError) as exc:
                current.update(state="needs_review", control="paused", reason=str(exc))
            self.store.save(db, current, "episode_settled")

    @staticmethod
    def _recover(run, kind, error):
        """Apply the recovery for ``kind``; returns the event to record."""
        counter, allowance, event = RECOVERY[kind]
        streak = run.get(counter, 0) + 1
        run[counter] = streak
        error = str(error or "")
        if kind == "credential" and streak > allowance():
            # Not the work's fault: the task stays where it is and the person is asked to fix access.
            run.update(control="credential_blocked", retryAfter=0,
                       reason=f"The model provider refused the credential {streak} times in a row: {error[:400]}")
            return "credential_blocked"
        if streak > allowance():
            reason = {"episode_bound": f"{streak} consecutive episodes ended at their step/tool limit without a result",
                      "provider_transient": f"{streak} consecutive episodes failed on the provider or network: {error[:500]}"
                      }.get(kind, error)
            run.update(state="needs_review", control="paused", reason=reason[:2000])
            return "needs_review"
        if kind == "episode_bound":
            run["handoff"] = (f"The previous episode ended at its per-episode bound ({error}); "
                              "continue from the workspace and session state.\n" + run.get("handoff", ""))[:12000]
        elif kind == "interrupted":
            run["handoff"] = (f"The previous episode was interrupted (the Host stopped) before it finished; "
                              f"{error or 'no tool had run'}. Continue from the workspace and session state.\n"
                              + run.get("handoff", ""))[:12000]
        elif kind == "no_progress":
            run["handoff"] = ("The previous episode was stopped for repeating the same failing operation without "
                              "progress. Do not retry it as before: read the error, change the approach or the "
                              "command, and keep the work already done.\n" + run.get("handoff", ""))[:12000]
        elif kind == "credential":
            run["retryAfter"] = time.time() + min(3600, 300 * 2 ** (streak - 1))
            run["handoff"] = ("The previous episode stopped because the model provider refused the credential; "
                              "continue from the workspace and session state.\n" + run.get("handoff", ""))[:12000]
        elif kind == "provider_transient":
            run["retryAfter"] = time.time() + min(1800, 30 * 2 ** (streak - 1))
            run["handoff"] = ("The previous episode was interrupted by a provider or network failure; "
                              "continue from the workspace and session state.\n" + run.get("handoff", ""))[:12000]
        elif kind == "review_evidence":
            run["reviewNote"] = error[:2000]
        elif kind == "scope":
            run["handoff"] = ("The runner refused the candidate: " + error[:2000] + "\n" + run.get("handoff", ""))[:12000]
        else:
            run["replyNote"] = error[:2000]
        return event
