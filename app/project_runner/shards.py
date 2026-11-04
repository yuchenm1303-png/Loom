"""Whole-project review split across parallel independent reviewers.

Real runs (docs/project-runner-parallelism-study-2026-11-04.md): one reviewer checking every
acceptance criterion of a 15-task project was 55% of its tokens and over half of its ten hours,
while the rest of the machine sat idle. The criteria are independent, so the checking is too.

How it works:
- When a run reaches whole-project review of a frozen candidate whose plan is big enough
  (``plan``), its criteria are split by task into shards. Each shard is a child run
  (``teamOwnerId`` = the run, so budget, cancellation and the kept-check registry are the
  project's) with a ``reviewShard`` record. It reviews the SAME frozen candidate in its own
  checkout and session, and must record executed, passing, current evidence for every
  criterion of its share: exactly the gate a single reviewer meets (review.accepted_evidence).
- Tasks that need an exclusive resource (browser, ports, a database, or nothing declared)
  share one shard, the only one allowed to use them; the others are told not to.
- When every shard passed, the run's own reviewer judges only what no shard did: whether the
  candidate achieves the whole objective and the tasks work together. Its pass carries the
  shards' evidence, so delivery still sees one project-scope assessment of this candidate.
- If any shard asks for changes, the issues of all shards go back to work at once, counted
  as one project review cycle, like a single reviewer's verdict.
- Shards belong to one candidate. A rework or a new candidate discards them; a shard still
  running is cancelled.
"""
from __future__ import annotations

import copy
import time
import uuid

from . import admission, checks, metrics
from .store import encoded

# Each reviewer first has to read the project; that is the price of a shard. A real
# 21-criterion review (shards1, 2026-11-04) split four ways took 15.6 min and 1.86M tokens
# where one reviewer of the same scenario had taken 6.3 min and 0.63M (ledger12). One
# reviewer of 45 criteria took 23M tokens over 16 episodes (weave-live2): that is the case
# shards are for. Below MIN_CRITERIA one reviewer does it all; shards are kept large.
MIN_CRITERIA = 30
CRITERIA_PER_SHARD = 12
MAX_SHARDS = 4
MAX_REVIEWERS = 4         # shard reviewers out at once


def exclusive(task):
    """A task whose checks may need a resource only one reviewer can hold at a time."""
    resources = task.get("sharedResources")
    return resources is None or bool(resources)


def plan(run, limit=None):
    """Groups of task IDs, one per shard reviewer (at most ``limit``); [] when one reviewer should do it all."""
    limit = MAX_SHARDS if limit is None else min(limit, MAX_SHARDS)
    tasks = run.get("plan") or []
    if run.get("teamOwnerId") or run.get("reviewPolicy") != "independent" or len(tasks) < 2:
        return []
    if sum(len(task["acceptance"]) for task in tasks) < MIN_CRITERIA:
        return []
    order = {task["id"]: index for index, task in enumerate(tasks)}
    shared = [task for task in tasks if exclusive(task)]
    free = [task for task in tasks if not exclusive(task)]
    room = limit - (1 if shared else 0)
    count = min(room, max(1, -(-sum(len(task["acceptance"]) for task in free) // CRITERIA_PER_SHARD))) if free else 0
    groups, loads = [[] for _ in range(count)], [0] * count
    for task in sorted(free, key=lambda item: (-len(item["acceptance"]), order[item["id"]])):
        index = loads.index(min(loads))
        groups[index].append(task["id"])
        loads[index] += len(task["acceptance"])
    shards = [{"taskIds": sorted(group, key=order.get), "exclusive": False} for group in groups if group]
    if shared:
        shards.append({"taskIds": [task["id"] for task in shared], "exclusive": True})
    return shards if len(shards) >= 2 else []


def source_id(run):
    """The run whose checkout holds the candidate: a shard reviews its owner's."""
    return (run.get("reviewShard") or {}).get("sourceRunId") or run["id"]


def _current(run):
    state = run.get("reviewShards")
    candidate = (run.get("reviewCandidate") or {}).get("id")
    if state and candidate and state.get("candidateId") == candidate \
            and run.get("phase") == "verifying" and run.get("reviewScope") == "project":
        return state
    return None


def waiting(run):
    """The run's shards are reviewing; its own reviewer must not start."""
    state = _current(run)
    return bool(state and state.get("status") == "running")


def aggregating(run):
    state = _current(run)
    return bool(state and state.get("status") == "aggregating")


def eligible(store, parent, run, mine):
    """``mine``: the episodes out for the owner's family (Host slots are checked by the caller)."""
    state = _current(parent) or {}
    if (parent["state"] != "running" or parent["control"] != "active" or state.get("status") != "running"
            or state.get("candidateId") != run["reviewShard"]["candidateId"]
            or run["id"] not in {item["runId"] for item in state.get("children", []) if item["state"] == "running"}):
        return False
    allowed = len(mine) < MAX_REVIEWERS
    metrics.note_wait(store, parent["id"], "review", None if allowed else "reviewerCap")
    return allowed


def _applies(run):
    return (not run.get("teamOwnerId") and run["state"] == "running" and run["control"] == "active"
            and run.get("phase") == "verifying" and run.get("reviewScope") == "project"
            and run.get("reviewPolicy") == "independent" and run.get("reviewCandidate") and not run["activeEpisode"])


def advance(service, run):
    """Start, collect or discard the run's review shards; True when it changed something."""
    store = service.project_store
    state = run.get("reviewShards")
    if state and _current(run) is None:
        return _discard(service, run, state)
    if (state and state["status"] == "running" and not run.get("teamOwnerId") and run["state"] == "running"
            and run["control"] in {"active", "budget_limited", "credential_blocked"}):
        return _collect(service, run, state)  # also while held, so every stopped shard is seen
    if not _applies(run):
        return False
    if state is None:
        # Each shard is a full checkout: plan only as many as the drive holds (admission.py).
        fit = admission.checkouts_that_fit(store.home, store.home / "workspaces" / run["id"], MAX_SHARDS)
        groups = plan(run, fit)
        if not groups:
            return False
        _start(store, run, groups)
        return True
    if state["status"] == "running":
        return _collect(service, run, state)
    if state["status"] == "blocked":
        # Resumed after a shard stopped for inspection: review the candidate afresh.
        return _discard(service, run, state)
    return False  # aggregating: the run's own reviewer judges the whole objective


def _start(store, run, groups):
    with store.transaction() as db:
        current = store._read(db, run["id"])
        if current["revision"] != run["revision"] or current.get("reviewShards"):
            return
        candidate = current["reviewCandidate"]["id"]
        children = []
        for index, group in enumerate(groups):
            child = {key: copy.deepcopy(current[key]) for key in (
                "sourceThreadId", "repository", "gitCommonDir", "budgetEnforcement", "tokenBudget", "maxEpisodes",
                "maxReviewCycles", "permissionMode", "objective", "plan", "baseCommit", "reviewCandidate") if key in current}
            child.update(id=str(uuid.uuid4()), revision=0, state="running", control="active", phase="verifying",
                reviewScope="project", taskIndex=len(current["plan"]), teamOwnerId=current["id"], sessions={},
                activeEpisode=None, episodesUsed=0, tokensUsed=0, reason="", createdAt=time.time(),
                reviewPolicy="independent", reviewFailures={}, feedback=[], verificationStatus="pending",
                completionPolicy="manual", delivery="isolated_candidate_only",
                reviewShard={"sourceRunId": current["id"], "candidateId": candidate, "taskIds": group["taskIds"],
                             "exclusive": group["exclusive"], "index": index + 1, "of": len(groups),
                             "registryRoot": checks.registry_root(current)})
            db.execute("INSERT INTO project_runs VALUES(?,0,?)", (child["id"], encoded(child)))
            store.save(db, child, "review_shard_started")
            children.append({"runId": child["id"], "taskIds": group["taskIds"], "exclusive": group["exclusive"],
                             "state": "running"})
        current["reviewShards"] = {"candidateId": candidate, "status": "running", "children": children,
                                   "startedAt": time.time()}
        store.save(db, current, "review_shards_started")


def _collect(service, run, state):
    store = service.project_store
    from .runner import ProjectRunner  # the runner imports the scheduler side of this module
    with store.transaction() as db:
        current = store._read(db, run["id"])
        state = _current(current)
        if current["revision"] != run["revision"] or not state or state["status"] != "running":
            return False
        changed = False
        children = {}
        for item in state["children"]:
            child = store._read(db, item["runId"])
            children[item["runId"]] = child
            if item["state"] != "running":
                continue
            if child["state"] == "reviewed":
                item["state"] = child.get("shardVerdict") or "blocked"
                changed = True
            elif child["state"] in {"needs_review", "cancelled"}:
                item.update(state="blocked", reason=(child.get("reason") or "")[:2000])
                changed = True
            elif child["control"] in {"budget_limited", "credential_blocked"} and not child["activeEpisode"]:
                # The person sees one project: the owner stops on a shard's limit, and resuming it
                # (with more budget, or after fixing the credential) sends every held shard back.
                holds = current.setdefault("shardHolds", [])
                if child["id"] in holds and current["control"] == "active":
                    child.update(control="active", reason="", credentialStreak=0, retryAfter=0)
                    store.save(db, child, "review_shard_resumed")
                    holds.remove(child["id"])
                    changed = True
                elif child["id"] not in holds:
                    holds.append(child["id"])
                    if current["control"] == "active":
                        current.update(control=child["control"],
                                       reason="A parallel reviewer stopped: " + (child.get("reason") or child["control"]))
                    changed = True
        if any(item["state"] == "running" for item in state["children"]):
            if changed:
                store.save(db, current, "review_shards_progress")
            return changed
        current["shardHolds"] = []
        verdicts = {item["state"] for item in state["children"]}
        assessments = {run_id: child.get("lastAssessment") or {} for run_id, child in children.items()}
        if "stale" in verdicts:
            ProjectRunner._rework(current, {"scope": "project", "verdict": "stale", "issues": [],
                                            "summary": "Source or acceptance definition changed during review"})
            current["reviewShards"] = None
            store.save(db, current, "review_shards_stale")
        elif "needs_changes" in verdicts:
            issues, summaries = [], []
            for item in state["children"]:
                if item["state"] != "needs_changes":
                    continue
                assessment = assessments[item["runId"]]
                issues.extend(assessment.get("issues") or [])
                summaries.append(assessment.get("summary", ""))
            summary = " | ".join(text for text in summaries if text)[:4000] or "Parallel review requires changes"
            current["lastAssessment"] = {"verdict": "needs_changes", "scope": "project", "summary": summary,
                                         "issues": issues[:32], "candidateId": state["candidateId"],
                                         "shards": [item["runId"] for item in state["children"]]}
            ProjectRunner._rework(current, {"scope": "project", "verdict": "needs_changes", "summary": summary,
                                            "issues": issues[:32]})
            current["reviewShards"] = None
            store.save(db, current, "review_shards_need_changes")
        elif verdicts != {"passed"}:
            reasons = [item.get("reason") or assessments[item["runId"]].get("summary", "")
                       for item in state["children"] if item["state"] != "passed"]
            state["status"] = "blocked"
            current.update(state="needs_review", control="paused",
                           reason=("A parallel reviewer could not finish: " + " | ".join(filter(None, reasons)))[:2000])
            if current.get("completionPolicy") == "local_integrate":
                current.update(workflowStatus="blocked", workflowError=current["reason"])
            store.save(db, current, "review_shards_blocked")
        else:
            evidence = []
            for item in state["children"]:
                assessment = assessments[item["runId"]]
                evidence.extend({**check, "runId": item["runId"], "sessionId": assessment.get("sessionId")}
                                for check in assessment.get("checks") or [])
            state.update(status="aggregating", evidence=evidence, finishedAt=time.time(),
                         summaries=[{"taskIds": item["taskIds"], "summary": assessments[item["runId"]].get("summary", "")}
                                    for item in state["children"]])
            store.save(db, current, "review_shards_passed")
        return True


def _discard(service, run, state):
    """Shards of a superseded candidate: cancel those still out, forget them on the owner."""
    store = service.project_store
    open_episodes = []
    with store.transaction() as db:
        current = store._read(db, run["id"])
        if current["revision"] != run["revision"]:
            return False
        for item in (current.get("reviewShards") or {}).get("children", []):
            child = store._read(db, item["runId"])
            if child["state"] in {"running", "needs_review"}:
                child.update(state="cancelled", control="paused", reason="Its candidate was superseded")
                store.save(db, child, "review_shard_discarded")
                if child["activeEpisode"]:
                    open_episodes.append(child["activeEpisode"])
        current["reviewShards"] = None
        current["shardHolds"] = []
        store.save(db, current, "review_shards_discarded")
    for episode in open_episodes:
        service.project_runner.executor.cancel(episode)
    metrics.note_wait(store, run["id"], "review", None)
    return True


def children(run):
    return [item["runId"] for item in (run.get("reviewShards") or {}).get("children", [])]


def aggregate_evidence(run, expected, required):
    """The shards' accepted checks of this candidate, covering every criterion; else ValueError."""
    state = _current(run)
    if not state or state.get("status") != "aggregating" or state.get("candidateId") != expected["id"]:
        raise ValueError("Whole-objective review requires every parallel reviewer to have passed this candidate")
    evidence = state.get("evidence") or []
    covered = {item.get("criterionId") for item in evidence}
    if covered != set(required) or len(evidence) != len(required):
        raise ValueError("Parallel reviewers' evidence does not cover every acceptance criterion exactly once")
    return list(evidence)
