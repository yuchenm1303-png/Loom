"""Deterministic, opt-in local completion; every cross-stage command is durable."""
import uuid

from . import integration


def save(store, run, **values):
    with store.transaction() as db:
        current = store._read(db, run["id"])
        current.update(values)
        store.save(db, current, "workflow_transition")
        return current


def advance(service, run):
    if run.get("completionPolicy") != "local_integrate" or run.get("workflowControl") != "active" or run.get("workflowStatus") in {"blocked", "completed"}:
        return False
    store = service.project_store
    child_id = run.get("integrationRunId")
    target = store.get(child_id) if child_id else run
    if target["activeEpisode"]:
        return False
    step = run.get("workflowStep")
    if not step:
        if target["state"] == "integrated":
            save(store, run, workflowStatus="completed", workflowControl="paused", state="integrated",
                 control="paused", tokensUsed=target["tokensUsed"], episodesUsed=target["episodesUsed"],
                 reason="Automatic local integration completed; remote publication pending")
            return True
        if target["state"] == "needs_review" or target["control"] in {"budget_limited", "credential_blocked"} \
                or target["state"] == "cancelled":
            save(store, run, workflowStatus="blocked", workflowError=target.get("reason") or "Integration child needs inspection")
            return True
        action = "deliver" if target["state"] == "ready_for_review" else (
            "apply" if child_id and target["state"] == "delivered" else
            "prepare" if not child_id and target["state"] == "delivered" else None)
        if not action:
            return False
        params = {"runId": target["id"], "operationId": str(uuid.uuid4()),
                  "expectedRevision": target["revision"] + (0 if child_id else 1)}
        save(store, run, workflowStep={"action": action, "params": params}, workflowStatus=action)
        return True
    try:
        action, params = step["action"], step["params"]
        result = (service.project_deliver(params) if action == "deliver" else
                  integration.start(service, params) if action == "prepare" else integration.apply(service, params))
        values = {"workflowStep": None, "workflowStatus": "awaiting_review", "workflowError": ""}
        if action == "prepare":
            values["integrationRunId"] = result["id"]
        save(store, run, **values)
    except Exception as exc:
        # No hot retry loops, especially after an uncertain filesystem side effect.
        save(store, run, workflowStatus="blocked", workflowError=f"{type(exc).__name__}: {exc}")
    return True


def control(service, params, action):
    store = service.project_store
    run_id = service._required_text(params, "runId")
    revision = params.get("expectedRevision")
    if type(revision) is not int or revision < 1:
        raise ValueError("expectedRevision must be a positive integer")
    def apply(db):
        root = store._read(db, run_id)
        if root["revision"] != revision:
            raise ValueError("stale project revision; read the run before retrying")
        if root["state"] == "cancelled" or root.get("workflowStatus") == "completed":
            raise ValueError("project workflow is terminal")
        if root.get("completionPolicy") != "local_integrate" and root["state"] in {"ready_for_review", "delivered", "integrated"}:
            raise ValueError("project run is terminal")
        child_id = root.get("integrationRunId")
        pending = root.get("workflowStep")
        if not child_id and pending and pending["action"] == "prepare":
            row = db.execute("SELECT run_id FROM project_commands WHERE operation=?", (pending["params"]["operationId"],)).fetchone()
            child_id = row[0] if row else None
        child = store._read(db, child_id) if child_id else None
        target = child or root
        for key, used, maximum in (("tokenBudget", "tokensUsed", 1_000_000_000), ("maxEpisodes", "episodesUsed", 10000)):
            if key in params:
                value = params[key]
                if action not in {"start", "resume"} or type(value) is not int or not target[used] < value <= maximum:
                    raise ValueError("Explicit workflow budgets must exceed cumulative usage on start/resume")
                root[key] = target[key] = value
        if action in {"start", "resume"}:
            if target["state"] == "needs_review":
                raise ValueError("Execution or review requires inspection before resuming")
            if target["state"] in {"created", "running"}:
                if target["tokensUsed"] >= target["tokenBudget"] or target["episodesUsed"] >= target["maxEpisodes"]:
                    raise ValueError("project budget exhausted")
                # The person says access works again: no waiting out an earlier refusal.
                target.update(state="running", control="active", credentialStreak=0, retryAfter=0)
            root.update(workflowControl="active", workflowStatus="active", workflowError="", reason="")
            step = root.get("workflowStep")
            if step and not db.execute("SELECT 1 FROM project_commands WHERE operation=?", (step["params"]["operationId"],)).fetchone():
                root["workflowStep"] = None
        elif action == "pause":
            root["workflowControl"] = "paused"
            target["control"] = "paused"
        elif action == "cancel":
            root.update(state="cancelled", control="paused", workflowControl="cancelled")
            if target["state"] != "integrated":
                target.update(state="cancelled", control="paused")
        else:
            raise ValueError("unknown project control action")
        if child:
            store.save(db, child, "workflow_" + action)
        for item in root.get("teamChildren", []):
            team = store._read(db, item["runId"])
            changed = False
            if team.get("teamOwnerId") != root["id"]:
                continue
            if any(key in params for key in ("tokenBudget", "maxEpisodes")):
                for key in ("tokenBudget", "maxEpisodes"):
                    team[key] = root[key]
                changed = True
            if action in {"start", "resume"} and team["state"] == "running" and team["control"] in {"budget_limited", "credential_blocked"}:
                team.update(control="active", reason="", credentialStreak=0, retryAfter=0)
                changed = True
            if changed:
                store.save(db, team, "team_budget_updated")
        store.save(db, root, "workflow_" + action)
        return root
    return store.command(params.get("operationId"), {"action": action, "id": run_id, "expectedRevision": revision,
                         **{key: params[key] for key in ("tokenBudget", "maxEpisodes") if key in params}}, apply)
