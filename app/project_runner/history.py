"""Compact user-owned build history, independent of the sidebar's loaded page."""
from datetime import datetime, timezone

from . import program


def status(current, root, family):
    if current and current.get("state") == "completed":
        return "completed"
    owner = current or root or {}
    if owner.get("state") == "cancelled" or owner.get("control") == "cancelled":
        return "cancelled"
    states = [current or {}, root or {}, *family]
    if any((item.get("activeEpisode") or {}).get("state") in {"waiting_approval", "outcome_unknown"} for item in states):
        return "needs_you"
    if any(item.get("state") in {"needs_review", "budget_limited", "credential_blocked"}
           or item.get("control") in {"budget_limited", "credential_blocked"}
           or item.get("workflowStatus") == "blocked" for item in states):
        return "needs_you"
    if not current and root and root.get("state") == "integrated" and root.get("workflowStatus") == "completed":
        return "completed"
    if root and root.get("state") == "created":
        return "ready"
    if any(item.get("state") == "paused" for item in states) or (root and
            root.get("workflowControl") == "paused" and root.get("control") != "active"):
        return "paused"
    return "running" if current or root else "draft"


def read(service, params):
    # IDs carry draft mode choices only; ownership, titles and states come from the Host.
    requested = params.get("threadIds", [])
    if not isinstance(requested, list) or len(requested) > 200 or not all(isinstance(x, str) for x in requested):
        raise ValueError("threadIds must contain at most 200 conversation IDs")
    store = service.project_store
    with store.lock:
        internal = {row[0] for row in store.db.execute("SELECT DISTINCT session_id FROM project_episodes")}
        runs = store.list()
        programs = program.list_programs(store)
    roots = {}
    for run in sorted(runs, key=lambda x: x.get("createdAt", 0)):
        if not run.get("teamOwnerId") and not run.get("integrationTarget"):
            roots[run["sourceThreadId"]] = run
    latest = {}
    for item in sorted(programs, key=lambda x: x.get("createdAt", 0)):
        latest[item["sourceThreadId"]] = item
    by_id = {run["id"]: run for run in runs}
    children = {}
    for run in runs:
        if run.get("teamOwnerId"):
            children.setdefault(run["teamOwnerId"], []).append(run)
    projects = {item.project_id: item for item in service.projects.list()}
    records = []
    record = getattr(service, "_managed_record", service._record)
    for session in service._list_session_objects(overview=True):
        if session.session_id in internal:
            continue
        row = record(session)
        tid = row["id"]
        if row.get("archived") or row.get("parentThreadId"):
            continue
        if not (row.get("weave") or tid in requested or tid in roots or tid in latest):
            continue
        current = latest.get(tid)
        newest = roots.get(tid)
        # A later standalone build supersedes a finished program in the same conversation.
        if current and newest and newest.get("createdAt", 0) > current.get("createdAt", 0) and (newest.get("milestone") or {}).get("programId") != current["id"]:
            current = None
        root = by_id.get((current or {}).get("currentRunId")) or roots.get(tid)
        family = [root, *children.get(root["id"], [])] if root else []
        if root and root.get("integrationRunId") in by_id:
            family.append(by_id[root["integrationRunId"]])
        objective = (current or root or {}).get("objective", "").strip()
        title = row.get("title", "").strip()
        if objective and row.get("titleSource") != "manual":
            title = objective.splitlines()[0][:160]
        if title == tid:
            title = ""
        plan = (root or {}).get("plan") or []
        project = projects.get(row.get("projectId"))
        updated = row.get("updatedAt", "")
        stamps = [item.get("updatedAt", item.get("createdAt", 0)) for item in [current or {}, *family]]
        stamp = max((x for x in stamps if isinstance(x, (int, float))), default=0)
        try:
            stamp = max(stamp, datetime.fromisoformat(updated.replace("Z", "+00:00")).timestamp())
        except (ValueError, TypeError):
            pass
        if stamp:
            updated = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        records.append({"id": tid, "title": title, "objective": objective[:1000], "updatedAt": updated,
                        "projectId": row.get("projectId", ""), "projectName": project.name if project else "",
                        "workspace": row.get("workspace", ""), "status": status(current, root, family),
                        "live": service._is_active(tid), "done": sum(x.get("state") in {"verified", "integrated"} for x in plan),
                        "total": len(plan), "milestone": ((root or {}).get("milestone") or {}).get("index")})
    return {"conversations": sorted(records, key=lambda x: (x["updatedAt"], x["id"]), reverse=True)}
