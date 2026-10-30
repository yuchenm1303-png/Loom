"""A program: one objective pursued through successive milestones, each an ordinary project run.

A project run plans at most 16 tasks and ends when they are integrated. An objective
meant to run for days or a week is larger than that, and nobody knows its whole plan
up front. Mature long-running harnesses work in increments: Anthropic's long-running
agent harness keeps a feature list and takes the next item each session; Devin and
OpenHands re-plan from the repository as it now is. A program does the same at
milestone grain:

* milestone 1 is a project run whose planner is told it plans only the first batch;
* when a milestone is integrated, the next one starts from the new commit, and its
  planner (read-only, in the integrated repository) plans the next batch or reports
  that the objective is achieved;
* tokens and episodes are one budget across all milestones.

Each milestone keeps every guarantee of a project run (independent review, reviewed
integration, request-boundary budget, recovery). The program only decides what comes
next; it never edits anything itself.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
import uuid


def encoded(value):  # as store.encoded; the store imports this module's schema
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

SCHEMA = """
    CREATE TABLE IF NOT EXISTS project_programs(id TEXT PRIMARY KEY, data TEXT NOT NULL);
    INSERT OR IGNORE INTO project_runner_schema_versions VALUES(6);
"""
MAX_MILESTONES = 50
MAX_GUIDANCE, MAX_GUIDANCE_CHARS = 20, 2000
HISTORY_IN_OBJECTIVE = 12


def objective_for(program, index):
    """The milestone run's objective: the program's, plus where it stands."""
    done = program["milestones"][-HISTORY_IN_OBJECTIVE:]
    history = "".join(f"\n- Milestone {item['index']}: {item['summary']}" for item in done)
    if index == 1:
        stance = ("This is milestone 1 of a long-running program for the objective above. Plan only a first coherent "
                  "batch of work (at most 16 tasks) that later milestones can build on; do not try to fit the whole "
                  "objective into it.")
    else:
        stance = (f"This is milestone {index} of a long-running program for the objective above. Earlier milestones are "
                  f"integrated in this repository:{history}\nRead the repository to see what exists. Plan only the next "
                  "coherent batch (at most 16 tasks) toward the whole objective; never redo integrated work. If the "
                  "repository already achieves the entire objective, report it complete instead of planning.")
    guidance = "".join(f"\n- {item}" for item in program.get("guidance", [])[-MAX_GUIDANCE:])
    if guidance:
        stance += "\nGuidance the person added while the program ran (newest last):" + guidance
    return (program["objective"][:14000] + "\n\n" + stance)[:20000]


MAX_REFERENCES = 12
MAX_REFERENCE_BYTES = 4_000_000


def references_folder(store, program_id):
    return store.home / "references" / program_id


def _stage_references(store, program, paths):
    """Copy files the person attached into the program's own store; returns their records.

    Agents work in sandboxed checkouts and cannot read the person's folders, so each episode
    gets a copy in its scratch (ProjectExecutor.references). The originals are never touched.
    """
    if paths is None:
        return []
    if not isinstance(paths, list) or not all(isinstance(item, str) and item.strip() for item in paths):
        raise ValueError("attachments must be a list of file paths")
    folder = references_folder(store, program["id"])
    have = list(program.get("references") or [])
    if len(have) + len(paths) > MAX_REFERENCES:
        raise ValueError(f"at most {MAX_REFERENCES} reference files per program")
    added = []
    for raw in paths:
        source = Path(raw).expanduser()
        if not source.is_file():
            raise ValueError(f"Not a file: {raw}")
        size = source.stat().st_size
        if size > MAX_REFERENCE_BYTES:
            raise ValueError(f"{source.name} is larger than {MAX_REFERENCE_BYTES // 1_000_000} MB")
        taken = {item["name"] for item in have + added}
        name, index = source.name, 2
        while name in taken:  # two files of one name keep both
            name = f"{source.stem}-{index}{source.suffix}"
            index += 1
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, folder / name)
        added.append({"name": name, "bytes": size, "source": str(source), "addedAt": time.time()})
    return added


def attach(service, params):
    """Add reference files to a running program; the next episodes read them."""
    store = service.project_store
    current = get(store, service._required_text(params, "programId"))
    added = _stage_references(store, current, params.get("attachments"))
    with store.transaction() as db:
        row = db.execute("SELECT data FROM project_programs WHERE id=?", (current["id"],)).fetchone()
        latest = json.loads(row[0])
        latest["references"] = [*(latest.get("references") or []), *added]
        db.execute("INSERT OR REPLACE INTO project_programs VALUES(?,?)", (latest["id"], encoded(latest)))
    return latest


def create(service, params):
    """Create a program and its first milestone run, started."""
    store = service.project_store
    objective = service._required_text(params, "objective")
    budget, episodes = params.get("tokenBudget"), params.get("maxEpisodes")
    milestones = params.get("maxMilestones", 20)
    if type(milestones) is not int or not 1 <= milestones <= MAX_MILESTONES:
        raise ValueError(f"maxMilestones must be within 1..{MAX_MILESTONES}")
    operation = params.get("operationId") or str(uuid.uuid4())
    with store.lock:
        row = store.db.execute("SELECT data FROM project_programs WHERE json_extract(data,'$.operationId')=?",
                               (operation,)).fetchone()
    if row:
        return json.loads(row[0])
    program = {"id": str(uuid.uuid4()), "operationId": operation, "objective": objective,
               "sourceThreadId": service._required_text(params, "threadId"), "tokenBudget": budget,
               "maxEpisodes": episodes, "maxWorkers": params.get("maxWorkers", 1), "maxMilestones": milestones,
               "state": "running", "reason": "", "milestones": [], "currentRunId": None, "createdAt": time.time()}
    program["references"] = _stage_references(store, program, params.get("attachments"))
    _save(store, program)  # first, so a retried create finds it rather than starting another
    try:
        # The first run validates the shared parameters (budget, workers, repository state).
        _start_milestone(service, program, 1, budget, episodes)
    except Exception:
        with store.transaction() as db:
            db.execute("DELETE FROM project_programs WHERE id=?", (program["id"],))
        raise
    return program


def get(store, program_id):
    with store.lock:
        row = store.db.execute("SELECT data FROM project_programs WHERE id=?", (program_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown project program")
    return json.loads(row[0])


def list_programs(store):
    with store.lock:
        return [json.loads(row[0]) for row in store.db.execute("SELECT data FROM project_programs")]


def _save(store, program):
    with store.transaction() as db:
        db.execute("INSERT OR REPLACE INTO project_programs VALUES(?,?)", (program["id"], encoded(program)))


def _start_milestone(service, program, index, budget, episodes):
    # Deterministic operation IDs: a Host that dies between creating the run and saving
    # the program finds the same run again instead of starting a second one.
    prefix = f"program:{program['id']}:milestone:{index}"
    run = service.project_run_create({"operationId": prefix, "threadId": program["sourceThreadId"],
        "objective": objective_for(program, index), "tokenBudget": max(1, budget), "maxEpisodes": max(1, episodes),
        "maxWorkers": program["maxWorkers"], "reviewPolicy": "independent", "completionPolicy": "local_integrate"})
    if run.get("milestone") is None:
        with service.project_store.transaction() as db:
            current = service.project_store._read(db, run["id"])
            current["milestone"] = {"programId": program["id"], "index": index}
            service.project_store.save(db, current, "milestone_assigned")
        run = service.project_store.get(run["id"])
    if run["state"] == "created":
        run = service.project_control({"operationId": prefix + ":start", "runId": run["id"],
                                       "expectedRevision": run["revision"]}, "start")
    program["currentRunId"] = run["id"]
    _save(service.project_store, program)
    return run


def _ledger(store, run):
    """The run whose ledger holds a milestone's whole usage (its integration run, when there is one)."""
    return store.get(run["integrationRunId"]) if run.get("integrationRunId") else run


def advance(service):
    """Settle finished milestones and start the next; returns whether anything changed."""
    store, changed = service.project_store, False
    for program in list_programs(store):
        if program["state"] != "running" or not program.get("currentRunId"):
            continue
        run = store.get(program["currentRunId"])
        if run["state"] == "cancelled":
            program.update(state="cancelled", reason="The current milestone was cancelled")
            _save(store, program)
            changed = True
            continue
        if not (run["state"] == "integrated" and run.get("workflowStatus") == "completed"):
            continue  # running, or stopped for a person on its own run: resuming that run resumes the program
        ledger = _ledger(store, run)
        index = len(program["milestones"]) + 1
        tasks = "; ".join(task["instructions"][:120] for task in run.get("plan") or [])
        program["milestones"].append({
            "index": index, "runId": run["id"], "complete": bool(run.get("objectiveComplete")),
            "tokens": service.project_budget.report(ledger)["chargedTokens"], "episodes": ledger["episodesUsed"],
            "summary": (run.get("reason", "")[:600] if run.get("objectiveComplete") else
                        f"{len(run.get('plan') or [])} tasks integrated: {tasks}")[:1500]})
        used = sum(item["tokens"] for item in program["milestones"])
        episodes = sum(item["episodes"] for item in program["milestones"])
        if run.get("objectiveComplete"):
            program.update(state="completed", reason=f"Objective reported complete after {index - 1} milestones: "
                                                     + run.get("reason", "")[:1500])
        elif index >= program["maxMilestones"]:
            program.update(state="needs_review", reason=f"Milestone limit reached ({index})")
        elif used >= program["tokenBudget"] or episodes >= program["maxEpisodes"]:
            program.update(state="budget_limited", reason="Program token or episode budget used up")
        else:
            _start_milestone(service, program, index + 1, program["tokenBudget"] - used, program["maxEpisodes"] - episodes)
            changed = True
            continue
        _save(store, program)
        changed = True
    return changed


def for_thread(store, thread_id):
    """The newest program started from a conversation, or None."""
    owned = [item for item in list_programs(store) if item["sourceThreadId"] == thread_id]
    return max(owned, key=lambda item: item.get("createdAt", 0)) if owned else None


def add_guidance(store, program_id, text):
    program = get(store, program_id)
    program["guidance"] = [*program.get("guidance", []), text][-MAX_GUIDANCE:]
    _save(store, program)
