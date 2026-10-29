"""Review merged content before a recoverable fast-forward into the source."""
import copy
from pathlib import Path
import time
import uuid
from functools import wraps

from app.project_git_workflow import repository_operation_lock

from .review import candidate, definition_hash, git
from .store import encoded


def identity(run):
    root = Path(run["repository"])
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
    if common != Path(run["gitCommonDir"]).resolve():
        raise ValueError("Integration repository identity changed")
    return root


def serialized(function):
    @wraps(function)
    def call(service, params):
        run = service.project_store.get(service._required_text(params, "runId"))
        lock = repository_operation_lock(identity(run))
        if not lock.acquire(blocking=False):
            raise ValueError("Repository has another Git operation in progress")
        try:
            return function(service, params)
        finally:
            lock.release()
    return call


@serialized
def start(service, params):
    store = service.project_store
    run_id = service._required_text(params, "runId")
    revision = params.get("expectedRevision")
    if type(revision) is not int or revision < 1:
        raise ValueError("expectedRevision must be a positive integer")
    request = {"action": "prepare_integration", "id": run_id, "expectedRevision": revision,
               **{key: params[key] for key in ("tokenBudget", "maxEpisodes") if key in params}}
    operation = params.get("operationId")
    previous = store.lookup(operation, request)
    if previous:
        return previous
    parent = store.get(run_id)
    receipt = parent.get("deliveryReceipt", {})
    if parent["revision"] != revision or parent["state"] != "delivered" or receipt.get("status") != "delivered":
        raise ValueError("Integration requires a delivered project at the current revision")
    if parent.get("integrationTarget"):
        raise ValueError("Prepare a new integration from the original delivered run")
    root = identity(parent)
    ref = git(root, "symbolic-ref", "HEAD")
    if parent.get("completionPolicy") == "local_integrate" and ref != parent.get("completionRef"):
        raise ValueError("Automatic integration source branch changed")
    if not ref.startswith("refs/heads/") or git(root, "status", "--porcelain"):
        raise ValueError("Integration requires a clean source branch")
    head = git(root, "rev-parse", "HEAD")
    git(root, "merge-base", "--is-ancestor", parent["baseCommit"], head)
    if git(root, "rev-parse", receipt["ref"]) != receipt["commit"]:
        raise ValueError("Delivered branch changed")
    budget, episodes = params.get("tokenBudget", parent["tokenBudget"]), params.get("maxEpisodes", parent["maxEpisodes"])
    if (type(budget) is not int or not parent["tokensUsed"] < budget <= 1_000_000_000 or
            type(episodes) is not int or not parent["episodesUsed"] < episodes <= 10000):
        raise ValueError("Provide explicit total integration budgets above cumulative usage")
    # merge-tree writes objects only. Conflicts never touch the source index/files.
    tree = git(root, "merge-tree", "--write-tree", head, receipt["commit"]).splitlines()[0]
    commit = git(root, "-c", "user.name=Loom Project Runner", "-c", "user.email=loom@localhost",
                 "commit-tree", tree, "-p", head, "-p", receipt["commit"], "-m", f"Prepare integration of project {run_id}")
    child = {key: copy.deepcopy(parent[key]) for key in ("sourceThreadId", "objective", "repository", "gitCommonDir",
        "budgetEnforcement", "reviewPolicy", "maxReviewCycles", "plan", "tokensUsed", "episodesUsed")}
    child.update(id=str(uuid.uuid4()), revision=0, state="running", control="active", phase="verifying",
        reviewScope="project", taskIndex=len(child["plan"]), sessions={}, activeEpisode=None,
        reviewFailures={}, feedback=[], verificationStatus="pending", reason="Merged content requires fresh independent acceptance",
        createdAt=time.time(), baseCommit=head, tokenBudget=budget, maxEpisodes=episodes, delivery="isolated_candidate_only",
        integrationTarget={"parentRunId": run_id, "ref": ref, "head": head, "deliveryCommit": receipt["commit"]})
    if parent.get("completionPolicy") == "local_integrate":
        child["workflowOwnerId"] = parent["id"]
    workspace = store.home / "workspaces" / child["id"]
    git(root, "-c", "core.autocrlf=false", "worktree", "add", "--detach", str(workspace), commit)
    frozen = candidate(child, workspace)
    def record(db):
        if store._read(db, run_id)["revision"] != revision:
            raise ValueError("Delivered project changed during integration preparation")
        db.execute("INSERT INTO project_runs VALUES(?,0,?)", (child["id"], encoded(child)))
        store.put_candidate(db, child, frozen)
        store.save(db, child, "integration_review_started")
        return child
    return store.command(operation, request, record)


def fast_forward(run, receipt):
    root = identity(run)
    target = run["integrationTarget"]
    exported = Path(receipt["workspace"])
    if git(exported, "rev-parse", "HEAD") != receipt["commit"] or git(exported, "status", "--porcelain"):
        raise ValueError("Delivered checkout changed; inspect before integration")
    if git(root, "symbolic-ref", "HEAD") != target["ref"] or git(root, "status", "--porcelain"):
        raise ValueError("Source branch changed or has uncommitted changes; integration retained")
    head = git(root, "rev-parse", "HEAD")
    if head not in {target["head"], receipt["commit"]}:
        raise ValueError("Source advanced; prepare and independently review a new integration")
    if head != receipt["commit"]:
        hooks = Path(receipt["workspace"]).parent / "empty-integration-hooks"
        hooks.mkdir(exist_ok=True)
        git(root, "-c", f"core.hooksPath={hooks}", "merge", "--ff-only", "--no-edit", receipt["commit"])
    if git(root, "symbolic-ref", "HEAD") != target["ref"] or git(root, "rev-parse", "HEAD") != receipt["commit"]:
        raise ValueError("Source changed during integration; inspect before retrying")
    # The user's checkout follows the user's Git filters (autocrlf=true writes an
    # accepted LF file as CRLF), so its bytes are not the reviewed bytes. A clean
    # status over exactly the delivered tree is what "integrated" means there.
    if git(root, "status", "--porcelain") or git(root, "write-tree") != receipt["tree"]:
        raise ValueError("Integrated checkout differs from accepted delivery; inspect before retrying")


@serialized
def apply(service, params):
    store = service.project_store
    run_id = service._required_text(params, "runId")
    revision = params.get("expectedRevision")
    if type(revision) is not int or revision < 1:
        raise ValueError("expectedRevision must be a positive integer")
    request = {"action": "apply_integration", "id": run_id, "expectedRevision": revision}
    operation = params.get("operationId")
    run = store.lookup(operation, request)
    if run and run.get("integrationStatus") == "integrated":
        return run
    if run is None:
        run = store.get(run_id)
        if run["revision"] != revision or run["state"] != "delivered" or not run.get("integrationTarget"):
            raise ValueError("Apply requires a newly reviewed and delivered integration run")
        if run.get("integrationStatus"):
            raise ValueError("Retry prepared integration with its original operationId")
        receipt = run["deliveryReceipt"]
        if (receipt.get("status") != "delivered" or run.get("verificationStatus") != "passed" or
                definition_hash(run) != run["verifiedCandidate"]["definitionHash"]):
            raise ValueError("Integration acceptance is missing or stale")
        git(run["repository"], "merge-base", "--is-ancestor", run["integrationTarget"]["head"], receipt["commit"])
        def record(db):
            current = store._read(db, run_id)
            if current["revision"] != revision:
                raise ValueError("Integration revision changed")
            current["integrationStatus"] = "prepared"
            store.save(db, current, "integration_apply_prepared")
            return current
        run = store.command(operation, request, record)
    # Keep ordinary Host turn launches out of the source while its checkout moves.
    with service._guard:
        root = Path(run["repository"]).resolve()
        for session in service._list_session_objects():
            if Path(session.workspace_dir).resolve().is_relative_to(root) and (
                    service._is_active(session.session_id) or session.status.value in {"running", "waiting_approval"}):
                raise ValueError("Source workspace has an active session; integration retained")
        fast_forward(run, run["deliveryReceipt"])
    with store.transaction() as db:
        current = store._read(db, run_id)
        current.update(integrationStatus="integrated", state="integrated", reason="Reviewed result integrated locally; remote publication pending")
        current["deliveryReceipt"]["integration"] = "integrated"
        store.save(db, current, "integrated")
        return current
