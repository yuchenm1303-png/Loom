"""Read-only build details, fetched when a person opens an observation branch.

No Git scans in the normal two-second overview poll. Checks retain their event and
candidate identities; a historical passing check is not a pass of today's source.
"""
from __future__ import annotations

import time

from .review import git


def active_waits(store, family):
    ids = {run["id"] for run in family if run.get("state") == "running" and run.get("control") == "active"}
    with store.lock:
        rows = store.db.execute("SELECT wait_key, reason, since FROM project_wait_open").fetchall()
    return [{"id": key, "runId": key.rsplit(":", 1)[0], "code": reason, "since": since}
            for key, reason, since in rows if key.rsplit(":", 1)[0] in ids]


def changes(store, run):
    folder = store.home / "workspaces" / run["id"]
    base = run.get("baseCommit")
    result = {"runId": run["id"], "baseCommit": base or "", "files": [], "total": 0, "error": ""}
    if not folder.is_dir() or not base:
        result["error"] = "checkout_unavailable"
        return result
    try:
        parts = git(folder, "diff", "--name-status", "--no-renames", "-z", base, "--", raw=True).split("\0")
        files = [{"status": parts[i], "path": parts[i + 1]} for i in range(0, len(parts) - 1, 2)]
        files += [{"status": "?", "path": name} for name in
                  git(folder, "ls-files", "--others", "--exclude-standard", "-z", raw=True).split("\0") if name]
        result.update(files=files[:200], total=len(files))
    except (OSError, ValueError) as exc:
        result["error"] = str(exc)[:500]
    return result


def events_for(service, session_id, turn_id, cache):
    key = (session_id, turn_id)
    if key not in cache:
        reader = getattr(service.store, "turn_events", None)
        cache[key] = reader(session_id, turn_id) if callable(reader) and turn_id else service.store.events(session_id)
    return cache[key]


def evidence(service, assessment, ref, cache):
    session_id = ref.get("sessionId") or assessment.get("sessionId")
    result = {"eventId": ref["eventId"], "sessionId": session_id or "", "candidateId": assessment.get("candidateId", ""),
              "verdict": "unavailable", "command": "", "observed": "", "subjectPaths": []}
    try:
        events = events_for(service, session_id, ref.get("turnId") or assessment.get("turnId"), cache)
        event = next((item for item in events if item.event_id == ref["eventId"] and item.kind.value == "check_recorded"), None)
        if event is None:
            return result
        data = event.data
        requests = {item.data.get("call_id"): item.data for item in events
                    if item.kind.value == "tool_requested" and item.turn_id == event.turn_id}
        call = next((requests[item.get("call_id")] for item in data.get("evidence_refs", []) if item.get("call_id") in requests), {})
        argv = (call.get("arguments") or {}).get("argv") or []
        call_ids = {item.get("call_id") for item in data.get("evidence_refs", []) if item.get("call_id")}
        output = [str(item.data.get("content") or "") for item in events if item.turn_id == event.turn_id
                  and item.kind.value in {"tool_completed", "tool_failed"} and item.data.get("call_id") in call_ids]
        result.update(verdict=data.get("verdict", "unavailable"), observed=str(data.get("observed") or "")[:2000],
                      command=" ".join(map(str, argv))[:4000], subjectPaths=data.get("subject_paths") or [],
                      log="\n".join(output)[-8000:],
                      source=data.get("assessment_source", ""), turnId=event.turn_id, recordedAt=event.created_at,
                      expectation=data.get("expectation", ""),
                      exitCode=(data.get("command_observation") or {}).get("returncode"))
    except (ValueError, OSError, KeyError):
        pass  # A removed session leaves an explicit unavailable evidence row.
    return result


def read(service, params):
    overview = service.project_overview(params)
    root, goal = overview["run"], overview.get("goal")
    now = time.time()
    if root is None:
        return {"observedAt": now, "source": "goal" if goal else "conversation", "goal": goal,
                "waits": [], "criteria": [], "reviews": [], "changes": [], "deliveries": [], "tasks": [], "metrics": None}
    store = service.project_store
    family = [root, *overview["children"], *([overview["integration"]] if overview.get("integration") else [])]
    waits = active_waits(store, family)
    for run in family:
        if run.get("retryAfter", 0) > now and run.get("state") == "running" and run.get("control") == "active":
            waits.append({"id": run["id"] + ":retry", "runId": run["id"], "code": "provider",
                          "recordedAt": run.get("updatedAt"), "retryAt": run["retryAfter"], "detail": run.get("reason", "")})
        elif run.get("control") in {"credential_blocked", "budget_limited"} or run.get("state") == "needs_review":
            waits.append({"id": run["id"] + ":control", "runId": run["id"], "code": run.get("control") if run.get("control") in
                          {"credential_blocked", "budget_limited"} else "inspection", "recordedAt": run.get("updatedAt"), "detail": run.get("reason", "")})
        elif run.get("state") == "running" and run.get("control") == "paused":
            waits.append({"id": run["id"] + ":paused", "runId": run["id"], "code": "paused", "recordedAt": run.get("updatedAt")})
    for agent in overview["agents"]:
        if agent.get("approval"):
            waits.append({"id": agent["id"], "runId": agent["runId"], "code": "approval", "detail": agent["approval"]["command"]})
    effective = overview.get("integration") or root
    try:
        freshness = service.project_read(effective["id"])["verificationFreshness"]
    except (ValueError, OSError):
        freshness = "unavailable"
    reviews, by_criterion, cache = [], {}, {}
    # Root evidence takes precedence over a team's earlier task-only review.
    for run in [*overview["children"], root, *([overview["integration"]] if overview.get("integration") else [])]:
        assessments = store.assessments(run["id"])
        for assessment in assessments:
            reviews.append({**assessment, "runId": run["id"]})
        if run.get("integrationTarget"):
            continue  # Integration has its own objective criteria, not root task IDs.
        history = list(reversed(assessments))
        # Failed checks and an in-progress independent review are visible too;
        # only a gate-accepted reference can establish an acceptance pass.
        if run.get("activeEpisode") and run.get("phase") == "verifying":
            episode = run["activeEpisode"]
            history.append({"sessionId": episode["sessionId"], "turnId": episode["turnId"], "checks": [],
                            "candidateId": (run.get("reviewCandidate") or {}).get("id", "")})
        for assessment in history:
            session_id = assessment.get("sessionId")
            if session_id:
                try:
                    for event in events_for(service, session_id, assessment.get("turnId"), cache):
                        if (event.kind.value != "check_recorded" or event.turn_id != assessment.get("turnId")
                                or event.data.get("assessment_source") not in {"runtime_exit_code", "runtime_browser_assertions"}):
                            continue
                        item = evidence(service, assessment, {"eventId": event.event_id}, cache)
                        status = "executed_pass" if item["verdict"] == "passed" else item["verdict"]
                        by_criterion[event.data.get("case_id")] = {"status": status, "evidence": item}
                except (ValueError, OSError):
                    pass
            for ref in assessment.get("checks", []):
                item = evidence(service, assessment, ref, cache)
                current_id = (effective.get("verifiedCandidate") or {}).get("id")
                status = "passed" if (item["verdict"] == "passed" and freshness == "current"
                    and effective.get("verificationStatus") == "passed" and assessment.get("candidateId") == current_id
                    ) else "recorded_pass" if item["verdict"] == "passed" else "unavailable"
                by_criterion[ref["criterionId"]] = {"status": status, "evidence": item}
    criteria = [{"id": f"{task['id']}:{i+1}", "taskId": task["id"], "expectation": expectation,
                 **by_criterion.get(f"{task['id']}:{i+1}", {"status": "not_run"})}
                for task in root["plan"] for i, expectation in enumerate(task["acceptance"])]
    for criterion in criteria:
        item = criterion.get("evidence")
        plain = lambda text: " ".join(text.replace("`", "").split())
        if item and item.get("expectation") and plain(item["expectation"]) != plain(criterion["expectation"]):
            criterion["status"] = "stale"
    children = {item["taskId"]: item for item in root.get("teamChildren", [])}
    runs = {run["id"]: run for run in overview["children"]}
    def task_state(task):
        if root["state"] == "integrated":
            return "integrated"
        child = children.get(task["id"])
        if child and child["state"] in {"merged", "integrated"}:
            return child["state"]
        run = runs.get((child or {}).get("runId"))
        return (run.get("phase") if run["state"] == "running" else run["state"]) if run else task["state"]
    tasks = [{"id": task["id"], "title": task["instructions"], "state": task_state(task), "dependsOn": task.get("dependsOn", []),
              "fileScopes": task.get("fileScopes", []), "runId": next((item["runId"] for item in root.get("teamChildren", [])
              if item["taskId"] == task["id"]), root["id"])} for task in root["plan"]]
    return {"observedAt": now, "source": "project", "runId": root["id"], "revision": root["revision"], "goal": goal,
            "waits": waits, "criteria": criteria, "reviews": reviews[:100], "freshness": freshness,
            "changes": [changes(store, run) for run in family if not run.get("reviewShard")], "tasks": tasks,
            "metrics": overview.get("metrics"), "budget": {"used": root["ledgerPosition"]["chargedTokens"],
                "limit": (overview.get("program") or {}).get("tokenBudget", root["tokenBudget"]),
                "position": root["ledgerPosition"], "earlierMilestones": sum(item["tokens"] for item in
                    (overview.get("program") or {}).get("milestones", []))},
            "deliveries": [{"runId": run["id"], "state": run["state"], "verification": run.get("verificationStatus", "not_assessed"),
                "branch": run.get("completionRef", ""), "receipt": run.get("deliveryReceipt"), "integration": run.get("integrationStatus", ""),
                "reason": run.get("reason", "")} for run in family if not run.get("reviewShard")],
            "recoveries": root.get("knowledge", {}).get("recoveries", {})}
