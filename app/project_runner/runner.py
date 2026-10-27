"""One serial worker; an ended conversation is never a completed project."""
from __future__ import annotations

import json
import os
import threading
import uuid

from .store import encoded
from .review import criteria, StaleCandidate


def object_reply(text):
    text = str(text).strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("worker response must be a JSON object")
    return value


def validate_plan(value):
    tasks = value.get("tasks")
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 16:
        raise ValueError("planner must return 1..16 ordered tasks")
    plan = []
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or not isinstance(task.get("instructions"), str) or not task["instructions"].strip():
            raise ValueError("each task needs instructions")
        acceptance = task.get("acceptance")
        if not isinstance(acceptance, list) or not acceptance or len(acceptance) > 16 or any(
                not isinstance(c, str) or not c.strip() or len(c) > 2000 for c in acceptance):
            raise ValueError("each task needs concrete acceptance criteria")
        if len(task["instructions"]) > 12000:
            raise ValueError("task instructions too long")
        plan.append({"id": f"task-{index+1}", "instructions": task["instructions"], "acceptance": acceptance,
                     "state": "pending"})
    return plan


class ProjectRunner:
    def __init__(self, store, executor):
        self.store, self.executor = store, executor
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

    def prompt(self, run):
        if run["phase"] == "planning":
            return ("Plan this long-running project. Do not edit files or execute commands. Return only JSON: "
                    '{"tasks":[{"instructions":"scoped implementation", "acceptance":["observable behavior"]}]}. '
                    "Tasks run in order; cover the entire objective with at most 16 tasks.\n" + run["objective"])
        if run["phase"] == "verifying":
            return ("Independently verify this frozen project candidate in your separate checkout. "
                    "Do not implement fixes, change source/tests/acceptance, commit, merge, or publish. "
                    "Inspect the actual code and exercise behavior; worker summaries are claims, not proof. "
                    "Build/type-check success alone is not behavioral acceptance; use functional checks or exercised browser assertions. "
                    "Use exec with wait=true, pty=false and verification.case_id equal to each criterion ID, "
                    "verification.expectation exactly equal to its expectation, and subject_paths covering actual candidate source. "
                    "Browser interactions may use browser_run_check with the same IDs. Ignore generated outputs only via the repository's existing ignore rules. "
                    "Check the entire objective for missing behavior, not just the declared test names. "
                    "Return only JSON: {\"verdict\":\"passed|needs_changes|blocked|continue\",\"summary\":\"observed facts\","
                    "\"checks\":[{\"criterionId\":\"task-1:1\",\"eventId\":\"real CHECK_RECORDED event id\"}],"
                    "\"issues\":[{\"taskId\":\"task-1\",\"description\":\"reproducible problem and expected behavior\"}]}. "
                    "eventId is optional: the runner resolves the latest real check for each criterion in this review session; never invent IDs. "
                    "passed requires all criteria with current executed evidence; needs_changes requires concrete scoped issues. "
                    "Use blocked if the environment cannot verify behavior; continue if more checking needs another episode.\n"
                    + encoded({"objective": run["objective"], "scope": run["reviewScope"], "criteria": criteria(run),
                               "plan": run["plan"], "candidate": run["reviewCandidate"]}))
        task = run["plan"][run["taskIndex"]]
        return ("Continue this persistent project in its isolated workspace. Preserve prior work. "
                "Use normal Loom tools and permissions. Run concrete checks for every acceptance criterion. "
                "Do not merge, push, deploy, or change branches. A final response ends only this episode. "
                'Return only JSON: {"action":"continue|candidate|blocked","summary":"facts and next steps"}. '
                "Use candidate only when this task is implemented and checked; it remains subject to independent review.\n"
                + encoded({"objective": run["objective"], "task": task, "priorWork": run.get("handoff", ""),
                           "reviewFeedback": run.get("feedback", [])}))

    def tick(self):
        with self.guard:
            runs = self.store.list()
            # Reconcile even while paused/cancelled; never drop late usage/results.
            for run in runs:
                if run["activeEpisode"]:
                    self._poll(run)
            # One global serial worker; waiting approval also owns the slot.
            if any(r["activeEpisode"] and r["activeEpisode"]["state"] != "outcome_unknown"
                   for r in self.store.list()):
                return
            for run in self.store.list():
                advance = getattr(self.executor, "advance_workflow", None)
                if advance and advance(run):
                    return
                if run["state"] == "running" and run["control"] == "active":
                    if run.get("workflowOwnerId"):
                        owner = self.store.get(run["workflowOwnerId"])
                        if (owner.get("workflowControl") != "active" or owner.get("workflowStatus") in {"blocked", "completed"}
                                or owner.get("integrationRunId") != run["id"]):
                            continue
                    self._dispatch(run)
                    return

    def _dispatch(self, run):
        if run["episodesUsed"] >= run["maxEpisodes"] or run["tokensUsed"] >= run["tokenBudget"]:
            with self.store.transaction() as db:
                current = self.store._read(db, run["id"])
                if current["revision"] == run["revision"]:
                    current.update(control="budget_limited", reason="Episode or between-turn token budget reached")
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
        episode = {"id": str(uuid.uuid4()), "runId": run["id"], "turnId": str(uuid.uuid4()), "sessionId": session_id,
                   "phase": run["phase"], "taskIndex": run["taskIndex"], "prompt": self.prompt(run), "state": "prepared"}
        # Session creation may leave an orphan after a crash; it has no execution authority.
        with self.store.transaction() as db:
            current = self.store._read(db, run["id"])
            if current["revision"] != run["revision"] or current["control"] != "active":
                return
            current["sessions"][self.executor.session_key(run)] = session_id
            current["activeEpisode"] = episode
            current["episodesUsed"] += 1
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
                result = {**result, "state": "failed", "error": f"Candidate review facts rejected: {exc}"}
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
            current["tokensUsed"] += result["tokens"]
            current["activeEpisode"] = None
            db.execute("UPDATE project_episodes SET state=?,data=? WHERE id=?",
                       (state, encoded({**episode, "result": result}), episode["id"]))
            if current["state"] == "cancelled":
                self.store.save(db, current, "cancelled_episode_settled")
                return
            try:
                if state != "completed":
                    raise ValueError(result.get("error") or f"Episode ended with {state}")
                reply = object_reply(result["text"])
                if episode["phase"] == "planning":
                    current["plan"] = validate_plan(reply)
                    current["phase"] = "working"
                elif episode["phase"] == "verifying":
                    self._review_result(db, current, episode, facts)
                else:
                    action, summary = reply.get("action"), reply.get("summary")
                    if action not in {"continue", "candidate", "blocked"} or not isinstance(summary, str) or not summary.strip():
                        raise ValueError("worker must report action and nonempty summary")
                    current["handoff"] = summary[:12000]
                    if action == "blocked":
                        raise ValueError(summary[:2000])
                    if action == "candidate":
                        current["plan"][episode["taskIndex"]]["state"] = "candidate"
                        if current.get("reviewPolicy") == "independent":
                            current.update(phase="verifying", reviewScope="task", verificationStatus="pending")
                            self.store.put_candidate(db, current, facts["candidate"])
                        else:
                            current["taskIndex"] += 1
                            if current["taskIndex"] == len(current["plan"]):
                                current.update(state="ready_for_review", control="paused",
                                               reason="Implementation candidates only; independent acceptance and integration required")
            except (ValueError, TypeError) as exc:
                current.update(state="needs_review", control="paused", reason=str(exc))
            self.store.save(db, current, "episode_settled")
