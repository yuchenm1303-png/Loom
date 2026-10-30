"""Bounded task DAGs with isolated runs, pooled usage and serial aggregation."""
import copy
from pathlib import PurePosixPath, Path
import time
import uuid

from .review import candidate, git, snapshot
from .store import encoded
from .errors import ReplyFormatError


def configure(plan, raw):
    for index, (task, supplied) in enumerate(zip(plan, raw)):
        dependencies = supplied.get("dependsOn", [plan[index-1]["id"]] if index else [])
        # Plan-shape errors go back to the planner with the task and value named.
        if not isinstance(dependencies, list) or any(not isinstance(item, str) or item not in {p["id"] for p in plan[:index]} for item in dependencies) or len(set(dependencies)) != len(dependencies):
            raise ReplyFormatError(f"{task['id']} dependsOn must list unique earlier task IDs only, got {dependencies!r}")
        scopes = supplied.get("fileScopes", ["*"])
        if not isinstance(scopes, list) or not 1 <= len(scopes) <= 32:
            raise ReplyFormatError(f"{task['id']} needs 1..32 relative fileScopes, got {scopes!r}")
        normalized = []
        for scope in scopes:
            if scope == "*" and "fileScopes" not in supplied:
                normalized.append(scope)
                continue
            if not isinstance(scope, str) or not scope.strip() or len(scope) > 300 or "\\" in scope or ":" in scope or "*" in scope:
                raise ReplyFormatError(f"{task['id']} fileScopes must be concrete relative paths, got {scope!r}")
            path = PurePosixPath(scope)
            if path.is_absolute() or any(part.casefold() in {"..", ".git"} for part in path.parts) or str(path) == ".":
                raise ReplyFormatError(f"{task['id']} fileScopes must stay inside the workspace, got {scope!r}")
            normalized.append(str(path))
        resources = supplied.get("sharedResources", ["project_environment"])
        if not isinstance(resources, list) or len(resources) > 16 or any(not isinstance(item, str) or not item.strip() or len(item) > 100 for item in resources):
            raise ReplyFormatError(f"{task['id']} sharedResources must be up to 16 short names, got {resources!r}")
        task.update(dependsOn=dependencies, fileScopes=normalized, sharedResources=sorted({item.strip().casefold() for item in resources}))
    return plan


def overlap(left, right):
    return any(a == "*" or b == "*" or a.casefold() == b.casefold() or a.casefold().startswith(b.casefold()+"/") or
               b.casefold().startswith(a.casefold()+"/") for a in left for b in right)


class ScopeViolation(ValueError):
    """A team task changed a file another task owns or is changing; returned to its worker."""


def _in_scope(name, scopes):
    folded = name.casefold()
    return any(scope == "*" or folded == scope.casefold() or folded.startswith(scope.casefold() + "/") for scope in scopes)


def allowed(store, run, source):
    """Scopes exist so concurrent tasks never touch the same file, not to forbid supporting files.

    A real five-task run stopped because its tests task added tests/__init__.py (unittest
    discovery needs it) next to the one test file its plan declared. A change outside the
    declared scope is accepted when no other task declares that path and no unmerged
    sibling has changed it; an aggregation conflict would still stop the merge safely.
    """
    if not run.get("teamOwnerId"):
        return
    baseline = store.candidate(run, run["teamBaseCandidateId"])["files"]
    current = snapshot(source)["files"]
    outside = sorted(name for name in baseline.keys() | current.keys()
                     if baseline.get(name) != current.get(name) and not _in_scope(name, run["plan"][0]["fileScopes"]))
    if not outside:
        return
    root = store.get(run["teamOwnerId"])
    others = [task for task in root["plan"] if task["id"] != run["plan"][0]["id"]]
    owned = [name for name in outside if any(_in_scope(name, task.get("fileScopes", ["*"])) for task in others)]
    for item in root.get("teamChildren", []):
        if item["runId"] == run["id"] or item["state"] == "merged":
            continue
        sibling = store.get(item["runId"])
        sibling_base = store.candidate(sibling, sibling["teamBaseCandidateId"])["files"]
        sibling_now = snapshot(store.home / "workspaces" / sibling["id"])["files"]
        owned += [name for name in outside if sibling_base.get(name) != sibling_now.get(name)]
    if owned:
        raise ScopeViolation("Task changed files outside its declared scope that another task owns or is changing: "
                             + ", ".join(sorted(set(owned))[:20]) + "; restore them or report blocked")


def owner(store, run):
    return store.get(run["teamOwnerId"]) if run.get("teamOwnerId") else None


def cancelled(store, run):
    parent = owner(store, run)
    return run["state"] == "cancelled" or bool(parent and parent["state"] == "cancelled")


def eligible(store, run, active):
    parent = owner(store, run)
    if parent is None:
        return not active
    if parent["state"] != "running" or parent["control"] != "active" or parent["phase"] != "parallel":
        return False
    if run["id"] not in {item["runId"] for item in parent.get("teamChildren", []) if item["state"] == "running"}:
        return False
    return len(active) < parent["maxWorkers"] and all(item.get("teamOwnerId") == parent["id"] for item in active)


def spawn(service, root, task, source):
    store = service.project_store
    child = {key: copy.deepcopy(root[key]) for key in ("sourceThreadId", "repository", "gitCommonDir", "budgetEnforcement",
        "tokenBudget", "maxEpisodes", "maxReviewCycles")}
    child.update(id=str(uuid.uuid4()), revision=0, state="running", control="active", phase="working",
        objective="Implement only this assigned task: " + task["instructions"] + "\nProject context (other tasks are outside this run): " + root["objective"],
        baseCommit=git(source, "rev-parse", "HEAD"), teamOwnerId=root["id"], plan=[{**copy.deepcopy(task), "state": "pending"}],
        taskIndex=0, sessions={}, activeEpisode=None, episodesUsed=0, tokensUsed=0, reason="", createdAt=time.time(),
        reviewPolicy="independent", reviewScope="task", reviewFailures={}, feedback=[], reviewCandidate=None,
        completionPolicy="manual", delivery="isolated_candidate_only")
    workspace = store.home / "workspaces" / child["id"]
    git(root["repository"], "-c", "core.autocrlf=false", "worktree", "add", "--detach", str(workspace), child["baseCommit"])
    baseline = candidate(child, workspace)
    with store.transaction() as db:
        current = store._read(db, root["id"])
        if current["revision"] != root["revision"]:
            raise ValueError("Parallel plan revision changed")
        db.execute("INSERT INTO project_runs VALUES(?,0,?)", (child["id"], encoded(child)))
        store.put_candidate(db, child, baseline)
        child.update(teamBaseCandidateId=baseline["id"], reviewCandidate=None)
        store.save(db, child, "parallel_task_started")
        current.setdefault("teamChildren", []).append({"runId": child["id"], "taskId": task["id"], "state": "running"})
        store.save(db, current, "parallel_task_adopted")


def aggregate(service, root, child, source):
    store = service.project_store
    allowed(store, child, store.home / "workspaces" / child["id"])
    intent = root.get("parallelMerge")
    if not intent:
        if child["state"] == "ready_for_review":
            params = root.get("parallelDelivery")
            if not params:
                params = {"operationId": f"team-deliver-{child['id']}", "runId": child["id"], "expectedRevision": child["revision"]}
                with store.transaction() as db:
                    current = store._read(db, root["id"])
                    current["parallelDelivery"] = params
                    store.save(db, current, "parallel_delivery_prepared")
            if params["runId"] != child["id"]:
                raise ValueError("Reconcile the previously prepared delivery first")
            child = service.project_deliver(params)
        if git(source, "status", "--porcelain"):
            raise ValueError("Aggregate workspace has user changes; inspect before continuing")
        old = git(source, "rev-parse", "HEAD")
        tree = git(source, "merge-tree", "--write-tree", old, child["deliveryReceipt"]["commit"]).splitlines()[0]
        commit = git(source, "-c", "user.name=Loom Project Runner", "-c", "user.email=loom@localhost", "commit-tree", tree,
            "-p", old, "-p", child["deliveryReceipt"]["commit"], "-m", f"Aggregate independently reviewed task {child['id']}")
        intent = {"runId": child["id"], "before": old, "commit": commit}
        with store.transaction() as db:
            current = store._read(db, root["id"])
            current["parallelMerge"] = intent
            store.save(db, current, "parallel_merge_prepared")
    if intent["runId"] != child["id"]:
        raise ValueError("Reconcile the previously prepared task merge first")
    if git(source, "status", "--porcelain") or git(source, "rev-parse", "HEAD") not in {intent["before"], intent["commit"]}:
        raise ValueError("Aggregate changed since merge preparation")
    hooks = store.home / "empty-parallel-hooks"
    hooks.mkdir(exist_ok=True)
    git(source, "-c", "core.autocrlf=false", "-c", f"core.hooksPath={hooks}", "checkout", "--detach", intent["commit"])
    if git(source, "status", "--porcelain") or git(source, "rev-parse", "HEAD") != intent["commit"]:
        raise ValueError("Aggregate checkout requires inspection")
    with store.transaction() as db:
        current = store._read(db, root["id"])
        item = next(item for item in current["teamChildren"] if item["runId"] == child["id"])
        item.update(state="merged", commit=intent["commit"])
        next(task for task in current["plan"] if task["id"] == item["taskId"])["state"] = "candidate"
        current["parallelMerge"] = None
        current["parallelDelivery"] = None
        store.save(db, current, "parallel_task_merged")


def advance(service, root):
    if root.get("maxWorkers", 1) <= 1 or root["phase"] != "parallel" or root["state"] != "running" or root["control"] != "active":
        return False
    store = service.project_store
    try:
        source = store.home / "workspaces" / root["id"]
        if Path(git(source, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve() != Path(root["gitCommonDir"]).resolve():
            raise ValueError("Aggregate repository identity changed")
        children = [(item, store.get(item["runId"])) for item in root.get("teamChildren", [])]
        pending_id = (root.get("parallelMerge") or root.get("parallelDelivery") or {}).get("runId")
        children.sort(key=lambda pair: pair[0]["runId"] != pending_id)
        for item, child in children:
            if item["state"] == "merged":
                continue
            if child["state"] == "needs_review" or child["control"] in {"budget_limited", "credential_blocked"} \
                    or child["state"] == "cancelled":
                raise ValueError("Parallel task needs inspection: " + (child.get("reason") or child["id"]))
            if child["state"] in {"ready_for_review", "delivered"} and not child["activeEpisode"]:
                aggregate(service, root, child, source)
                return True
        merged = {item["taskId"] for item, _ in children if item["state"] == "merged"}
        if len(merged) == len(root["plan"]):
            frozen = candidate({**root, "reviewScope": "project", "taskIndex": len(root["plan"])}, source)
            with store.transaction() as db:
                current = store._read(db, root["id"])
                current.update(phase="verifying", reviewScope="project", taskIndex=len(root["plan"]), verificationStatus="pending", parallelStatus="merged")
                store.put_candidate(db, current, frozen)
                store.save(db, current, "parallel_aggregate_review")
            return True
        occupied = [child for item, child in children if item["state"] != "merged"]
        if len(occupied) >= root["maxWorkers"]:
            return False
        assigned = {item["taskId"] for item, _ in children}
        for task in root["plan"]:
            if task["id"] in assigned or not set(task["dependsOn"]) <= merged:
                continue
            if any(overlap(task["fileScopes"], child["plan"][0]["fileScopes"]) or
                   set(task["sharedResources"]) & set(child["plan"][0]["sharedResources"]) for child in occupied):
                continue
            spawn(service, root, task, source)
            return True
    except Exception as exc:
        with store.transaction() as db:
            current = store._read(db, root["id"])
            current.update(control="paused", parallelStatus="blocked", reason=f"Parallel coordination stopped: {exc}")
            if current.get("completionPolicy") == "local_integrate":
                current.update(workflowStatus="blocked", workflowError=current["reason"])
            store.save(db, current, "parallel_blocked")
        return True
    return False
