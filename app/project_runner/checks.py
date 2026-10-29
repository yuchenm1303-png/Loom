"""Accepted acceptance checks, kept so later reviews and workers re-run them instead of rediscovering them.

Real runs spent most of their tokens on reviewers working out, again and again, how to
exercise criteria an earlier review had already verified: task review, then the
whole-project review, then the merged-result review, each rebuilding probe scripts and
data. Long-running harnesses that hold up (Anthropic's long-running-agent harness, its
feature list with test steps) persist the verification itself and re-run it at the start
of every session as a regression check.

Only checks an independent review cited for a passing verdict are kept - never a
worker's own claims. A later reviewer still executes and records every check in its own
session; the registry only tells it what already worked.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time

from app.agent_runtime.contracts import AgentEventKind as E

WORKSPACE, CHECKS = "{workspace}", "{checks}"
MAX_CHECKS_IN_PROMPT = 24


def registry_root(run):
    """Checks belong to the project, shared by its team tasks and its integration run."""
    return run.get("teamOwnerId") or (run.get("integrationTarget") or {}).get("parentRunId") or run["id"]


def _folder(store, root_id):
    return store.home / "checks" / root_id


def harvest(store, run, assessment, events, workspace, scratch):
    """Record the commands behind a passing review's cited checks; returns how many were kept."""
    if assessment.get("verdict") != "passed":
        return 0
    by_id = {event.event_id: event for event in events}
    requests = {event.data.get("call_id"): event.data for event in events if event.kind is E.TOOL_REQUESTED}
    root_id = registry_root(run)
    folder = _folder(store, root_id)
    kept = []
    for ref in assessment.get("checks", []):
        check = by_id.get(ref.get("eventId"))
        if check is None or check.kind is not E.CHECK_RECORDED or check.data.get("verdict") != "passed":
            continue
        call = next((requests.get(item.get("call_id")) for item in check.data.get("evidence_refs") or []
                     if requests.get(item.get("call_id"))), None)
        arguments = (call or {}).get("arguments") or {}
        argv = arguments.get("argv")
        if (call or {}).get("tool") != "exec" or not isinstance(argv, list) or not argv:
            continue
        criterion = str(ref["criterionId"])
        target = folder / criterion.replace(":", "_")
        rendered = []
        for item in map(str, argv):
            path = Path(item)
            if scratch and path.is_absolute() and path.is_file() and _inside(path, scratch):
                target.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target / path.name)
                rendered.append(f"{CHECKS}/{criterion.replace(':', '_')}/{path.name}")
            elif path.is_absolute() and _inside(path, workspace):
                rendered.append(_in_workspace(path, workspace))
            else:
                rendered.append(item)
        cwd = arguments.get("cwd")
        if cwd and _inside(Path(cwd), workspace):
            cwd = _in_workspace(Path(cwd), workspace)
        kept.append({"criterionId": criterion, "expectation": check.data.get("expectation"),
                     "argv": rendered, "cwd": cwd or WORKSPACE, "subjectPaths": check.data.get("subject_paths") or [],
                     "candidateId": assessment.get("candidateId")})
    if kept:
        with store.transaction() as db:
            for item in kept:
                db.execute("INSERT OR REPLACE INTO project_checks VALUES(?,?,?)",
                           (root_id, item["criterionId"], json.dumps(item, ensure_ascii=False)))
    return len(kept)


def provide(store, run, scratch, workspace):
    """Copy the project's kept check scripts into a session's scratch; returns them rendered for it."""
    root_id = registry_root(run)
    with store.lock:
        rows = [json.loads(row[0]) for row in store.db.execute(
            "SELECT data FROM project_checks WHERE root_id=? ORDER BY criterion_id", (root_id,))]
    if not rows:
        return []
    source = _folder(store, root_id)
    destination = Path(scratch) / "accepted-checks"
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    def render(text):
        return str(text).replace(CHECKS, destination.as_posix()).replace(WORKSPACE, Path(workspace).as_posix())
    return [{"criterionId": item["criterionId"], "expectation": item["expectation"],
             "argv": [render(part) for part in item["argv"]], "cwd": render(item["cwd"]),
             "subjectPaths": item["subjectPaths"]} for item in rows[:MAX_CHECKS_IN_PROMPT]]


def _in_workspace(path, workspace):
    relative = Path(path).resolve().relative_to(Path(workspace).resolve()).as_posix()
    return WORKSPACE if relative == "." else f"{WORKSPACE}/{relative}"


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError, TypeError):
        return False


# Project notes: environment facts and pitfalls a session learned (the role of the
# progress file in Anthropic's harness). Real runs rediscovered the same ones in every
# task - cmd quoting of a non-ASCII interpreter path, tests/__init__.py for discovery,
# reading README as UTF-8. Facts only; a note never stands in for a check.
MAX_NOTES, MAX_NOTE_CHARS, NOTES_PER_REPLY = 30, 300, 5


def add_notes(db, run, notes):
    if not isinstance(notes, list):
        return 0
    root_id, added = registry_root(run), 0
    for note in notes[:NOTES_PER_REPLY]:
        if isinstance(note, str) and note.strip():
            added += db.execute("INSERT OR IGNORE INTO project_notes VALUES(?,?,?)",
                                (root_id, " ".join(note.split())[:MAX_NOTE_CHARS], time.time())).rowcount
    return added


def notes_for(store, run):
    with store.lock:
        return [row[0] for row in store.db.execute(
            "SELECT note FROM (SELECT note, created_at FROM project_notes WHERE root_id=? ORDER BY created_at DESC LIMIT ?) "
            "ORDER BY created_at", (registry_root(run), MAX_NOTES))]


SCHEMA = """
    CREATE TABLE IF NOT EXISTS project_checks(root_id TEXT NOT NULL, criterion_id TEXT NOT NULL, data TEXT NOT NULL,
        PRIMARY KEY(root_id, criterion_id));
    INSERT OR IGNORE INTO project_runner_schema_versions VALUES(4);
    CREATE TABLE IF NOT EXISTS project_notes(root_id TEXT NOT NULL, note TEXT NOT NULL, created_at REAL NOT NULL,
        PRIMARY KEY(root_id, note));
    INSERT OR IGNORE INTO project_runner_schema_versions VALUES(5);
"""
