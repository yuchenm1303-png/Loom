"""Recoverable local delivery of an independently verified, exact candidate."""
from pathlib import Path
import uuid

from .review import definition_hash, git, review_workspace, snapshot, StaleCandidate


def validate(run, expected, source):
    assessment = run.get("lastAssessment", {})
    if (run["state"] != "ready_for_review" or run["activeEpisode"] or
            run.get("verificationStatus") != "passed" or expected.get("scope") != "project" or
            assessment.get("verdict") != "passed" or assessment.get("scope") != "project" or
            assessment.get("candidateId") != expected["id"] or
            run.get("verifiedCandidate", {}).get("digest") != expected["digest"]):
        raise ValueError("Delivery requires a settled, independently passed whole-project candidate")
    for root in (source, run["repository"]):
        common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()
        if common != Path(run["gitCommonDir"]).resolve():
            raise ValueError("Delivery repository identity changed")
    git(source, "merge-base", "--is-ancestor", run["baseCommit"], expected["head"])
    if definition_hash(run) != expected["definitionHash"] or snapshot(source)["digest"] != expected["digest"]:
        raise StaleCandidate("Candidate or acceptance changed; independently review it again")


def prepare(run, expected, source, home):
    """Commit in a fresh delivery checkout without invoking commit hooks."""
    validate(run, expected, source)
    target = home / "deliveries" / run["id"] / str(uuid.uuid4())
    review_workspace(run, expected, source, target)
    git(target, "-c", "core.autocrlf=false", "add", "--all", "--", ".")
    tree = git(target, "write-tree")
    commit = git(target, "-c", "user.name=Loom Project Runner", "-c", "user.email=loom@localhost",
                 "commit-tree", tree, "-p", run["baseCommit"], "-m",
                 f"Project {run['id']}: independently verified candidate {expected['id']}")
    # Git filters/line-ending conversion must not silently change tested bytes.
    exported = home / "delivery-checks" / run["id"] / commit
    git(run["repository"], "-c", "core.autocrlf=false", "worktree", "add", "--detach", str(exported), commit)
    actual = snapshot(exported)["files"]
    wanted = {name: item for name, item in expected["files"].items() if item["sha256"] is not None}
    if actual != wanted:
        raise StaleCandidate("Committed checkout differs from reviewed bytes or modes; review the exported result")
    validate(run, expected, source)
    return {"commit": commit, "tree": tree, "ref": f"refs/heads/codex/project-{run['id']}",
            "candidateId": expected["id"], "candidateDigest": expected["digest"],
            "baseCommit": run["baseCommit"], "workspace": str(exported), "status": "prepared"}


def publish_local(run, receipt):
    """CAS creates the dedicated ref; a crash after this operation is detectable."""
    root = run["repository"]
    refs = git(root, "for-each-ref", "--format=%(objectname)", receipt["ref"]).splitlines()
    if refs:
        if refs != [receipt["commit"]]:
            raise ValueError("Delivery branch already exists at another commit; refusing to overwrite")
    else:
        git(root, "update-ref", receipt["ref"], receipt["commit"], "0" * len(receipt["commit"]))
    return {**receipt, "status": "delivered", "integration": "pending", "publication": "pending"}
