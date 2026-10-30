"""Version-bound independent assessments; model prose alone cannot pass a gate."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import stat
import subprocess
import uuid

from app.agent_runtime.contracts import AgentEventKind as E
from app.agent_runtime.tools import ToolContext
from app.agent_runtime.verification import check_freshness
from .store import encoded


class StaleCandidate(ValueError):
    pass


# Runner-owned checkouts pass core.autocrlf=false: their bytes are what gets reviewed
# and committed, so a machine-wide autocrlf=true must not rewrite them on the way
# (a reviewed LF file came back CRLF from the delivery export). .gitattributes still apply.
def git(root, *args, raw=False):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    if result.returncode:
        raise ValueError(f"Git operation failed: {(result.stderr or result.stdout)[:2000]}")
    return result.stdout if raw else result.stdout.strip()


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def definition_hash(run):
    return digest({"objective": run["objective"], "tasks": [
        {key: task[key] for key in ("id", "instructions", "acceptance")} for task in run["plan"]]})


def _plain(text):
    """A criterion's words without Markdown code marks or spacing: a real reviewer copied
    "`--to csv` or a missing `--to` exits 2" as "--to csv or a missing --to exits 2" and the
    exact comparison rejected three correct reviews. Any change of wording still differs."""
    return " ".join(str(text or "").replace("`", "").split())


def criteria(run):
    tasks = run["plan"] if run.get("reviewScope") == "project" else [run["plan"][run["taskIndex"]]]
    return [{"id": f"{task['id']}:{index+1}", "taskId": task["id"], "expectation": text}
            for task in tasks for index, text in enumerate(task["acceptance"])]


def _read(path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > 32 * 1024 * 1024:
        raise ValueError("Review snapshots require regular source files of at most 32 MiB")
    with path.open("rb") as handle:
        data = handle.read(32 * 1024 * 1024 + 1)
    after = path.lstat()
    if len(data) > 32 * 1024 * 1024 or (before.st_size, before.st_mtime_ns, before.st_mode) != (
            after.st_size, after.st_mtime_ns, after.st_mode):
        raise StaleCandidate("Source changed while being snapshotted")
    return data, stat.S_IMODE(after.st_mode)


def snapshot(root):
    """Bounded tracked + nonignored source view, including tracked deletions."""
    root = Path(root).resolve()
    head = git(root, "rev-parse", "HEAD")
    names = sorted(set(filter(None, git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z", raw=True).split("\0"))))
    if len(names) > 16000:
        raise ValueError("Review snapshot exceeds 16000 source files")
    files, total = {}, 0
    for name in names:
        path = root / name
        if not path.parent.resolve().is_relative_to(root) or path.is_symlink():
            raise ValueError("Review source cannot traverse symlinks outside its workspace")
        if not path.exists():
            files[name] = {"sha256": None, "mode": None}
            continue
        data, mode = _read(path)
        total += len(data)
        if total > 256 * 1024 * 1024:
            raise ValueError("Review snapshot exceeds 256 MiB")
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "mode": mode}
    if git(root, "rev-parse", "HEAD") != head:
        raise StaleCandidate("Git HEAD changed while being snapshotted")
    value = {"head": head, "files": files}
    return {**value, "digest": digest(value)}


def candidate(run, root):
    return {**snapshot(root), "id": str(uuid.uuid4()), "definitionHash": definition_hash(run),
            "scope": run.get("reviewScope", "task"), "taskIndex": run["taskIndex"]}


def candidate_view(root, expected):
    """The candidate's own files as they are in root; files root adds are not part of it.

    A reviewer's checkout collects its test outputs and probe scripts. Comparing the
    whole checkout called that a changed candidate, threw away a three-episode review
    and sent unchanged work back to its worker (real ledger run, 680k tokens).
    """
    current = snapshot(root)["files"]
    return {name: current.get(name, {"sha256": None, "mode": None}) for name in expected["files"]}


def review_workspace(run, expected, source, target):
    if snapshot(source)["digest"] != expected["digest"]:
        raise StaleCandidate("Candidate changed before independent review")
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        git(run["repository"], "-c", "core.autocrlf=false", "worktree", "add", "--detach", str(target), expected["head"])
    if candidate_view(target, expected) != expected["files"]:
        # New checkout, or a reviewer edited candidate files: put back exactly the frozen bytes.
        for name, item in expected["files"].items():
            destination = target / name
            if not destination.parent.resolve().is_relative_to(target.resolve()):
                raise ValueError("Review destination traverses an external directory")
            if item["sha256"] is None:
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
            else:
                data, mode = _read(source / name)
                if hashlib.sha256(data).hexdigest() != item["sha256"] or mode != item["mode"]:
                    raise StaleCandidate("Candidate changed while preparing review")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.is_symlink():
                    raise ValueError("Review destination is a symlink")
                destination.write_bytes(data)
                destination.chmod(mode)
    if snapshot(source)["digest"] != expected["digest"] or candidate_view(target, expected) != expected["files"]:
        raise StaleCandidate("Review source no longer matches the frozen candidate")
    return target


def assess(run, episode, reply, expected, source, workspace, events):
    """Resolve cited check IDs from this independent session, never from the worker."""
    if Path(source).resolve() == Path(workspace).resolve() or episode["sessionId"] in {
            session_id for key, session_id in run.get("sessions", {}).items() if not key.startswith("review-")}:
        raise ValueError("Reviewer must use an independent session and checkout")
    verdict, summary = reply.get("verdict"), reply.get("summary")
    if not isinstance(verdict, str) or verdict not in {"passed", "needs_changes", "blocked", "continue"} or not isinstance(summary, str) or not summary.strip():
        raise ValueError("Reviewer must report verdict and a concrete summary")
    result = {"verdict": verdict, "summary": summary[:4000], "checks": [], "issues": [],
              "candidateId": expected["id"], "candidateDigest": expected["digest"],
              "definitionHash": expected["definitionHash"], "sessionId": episode["sessionId"],
              "turnId": episode["turnId"], "scope": expected["scope"],
              "hostEnvironment": {"os": platform.system(), "python": platform.python_version()}}
    if (expected["definitionHash"] != definition_hash(run) or snapshot(source)["digest"] != expected["digest"]
            or candidate_view(workspace, expected) != expected["files"]):
        return {**result, "verdict": "stale", "summary": "Source or acceptance definition changed during review"}
    if verdict == "needs_changes":
        issues = reply.get("issues")
        allowed = {task["id"] for task in run["plan"]} if expected["scope"] == "project" else {run["plan"][expected["taskIndex"]]["id"]}
        if not isinstance(issues, list) or not 1 <= len(issues) <= 32 or any(
                not isinstance(item, dict) or not isinstance(item.get("taskId"), str) or item["taskId"] not in allowed or
                not isinstance(item.get("description"), str) or not item["description"].strip() or
                len(item["description"]) > 4000 for item in issues):
            raise ValueError("needs_changes requires scoped task IDs and concrete issues")
        result["issues"] = [{"taskId": item["taskId"], "description": item["description"]} for item in issues]
    if verdict != "passed":
        return result
    references = reply.get("checks")
    required = {item["id"]: item for item in criteria(run)}
    if not isinstance(references, list) or len(references) != len(required):
        raise ValueError("Review pass requires executed evidence for every acceptance criterion")
    by_id = {event.event_id: event for event in events if event.kind is E.CHECK_RECORDED and event.session_id == episode["sessionId"]}
    latest = {event.data.get("case_id"): event.event_id for event in by_id.values()}
    context = ToolContext(episode["sessionId"], episode["turnId"], Path(workspace))
    seen = set()
    for ref in references:
        if not isinstance(ref, dict):
            raise ValueError("Invalid check reference")
        criterion_id = ref.get("criterionId")
        event_id = ref.get("eventId", latest.get(criterion_id)) if isinstance(criterion_id, str) else None
        if not isinstance(criterion_id, str) or criterion_id not in required or criterion_id in seen or not isinstance(event_id, str) or event_id not in by_id:
            raise ValueError("Missing, duplicate, or foreign review evidence")
        event = by_id[event_id]
        data = event.data
        if data.get("case_id") != criterion_id or latest.get(criterion_id) != event_id:
            raise ValueError(f"Check for {criterion_id} is not that criterion's latest recorded result in this review")
        if _plain(data.get("expectation")) != _plain(required[criterion_id]["expectation"]):
            # Say what differs: three correct reviews of a real run were rejected without knowing why.
            raise ValueError(f"Check for {criterion_id} records a different expectation. Required, verbatim: "
                             f"{required[criterion_id]['expectation']!r}; recorded: {str(data.get('expectation'))[:400]!r}")
        freshness = check_freshness(data, context)
        if (data.get("verdict") != "passed" or data.get("definition_changed") or data.get("acceptance_evidence") is not True or
                data.get("assessment_source") not in {"runtime_exit_code", "runtime_browser_assertions"} or
                data.get("check_kind") not in {"functional", "visual"} or freshness != "current"):
            # Name the criterion and the failed property: the reviewer is asked to fix exactly this.
            if data.get("definition_changed"):
                # ledger run 10: two whole-project reviews repeated this without knowing why it failed.
                raise ValueError(f"Review evidence for {criterion_id} is a redefined check: its command or verification "
                                 "changed after an earlier run of the same criterion in this episode, and a check "
                                 "changed until it passes is never evidence in that episode. Cite the earlier "
                                 "definition if it was right. If it could not run here (for example a sandbox denial "
                                 "of a path outside your session), keep the corrected script and report verdict "
                                 "continue: the next episode runs it first and it counts.")
            raise ValueError(f"Review evidence must be executed, passing, and current: {criterion_id} has verdict="
                             f"{data.get('verdict')}, check_kind={data.get('check_kind')}, acceptance_evidence="
                             f"{data.get('acceptance_evidence')}, source={data.get('assessment_source')}, freshness={freshness}")
        tested_files = data.get("subject_snapshot", {}).get("files", {})
        source_hashes = {os.path.normcase(path): item["sha256"] for path, item in expected["files"].items()}
        if not any(value is not None and source_hashes.get(os.path.normcase(path)) == value for path, value in tested_files.items()):
            raise ValueError("Check must cover candidate source files")
        seen.add(criterion_id)
        result["checks"].append({"criterionId": criterion_id, "eventId": event_id, "turnId": event.turn_id,
                                 "source": data["assessment_source"]})
    return result
