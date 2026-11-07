"""Weave mode: a conversation whose agent can also run a persistent project.

Weave is chat first. Every message the person sends in Weave mode is an ordinary turn
of the conversation and its agent answers it, a greeting in seconds. What the mode adds
are the weave_* tools: when the person wants something built that takes many sessions,
the agent writes the objective and proposes a project (weave_propose); the person starts
it from the hub (projectWeave/start), and from then on the agent reads it (weave_status)
and steers it (weave_steer, weave_control) while the conversation goes on.

A proposal starts nothing: a project spends millions of tokens, so only the person's
click starts one. Proposals live in a small file beside the project store, one per
conversation; a new proposal replaces the old one.
"""
from __future__ import annotations

import json
import threading
import time
import uuid

from app.agent_runtime.contracts import ToolEffect
from app.agent_runtime.tools import AgentTool, ToolResult
from app.ai import AIMessage, MessageRole

from . import goal, program
from .parallel import MAX_WORKERS
from .workspace import describe as describe_workspace

PROPOSALS_FILE = "weave-proposals.json"
# The budgets the hub's chip offers; a proposal's budget is one of them.
BUDGETS = (2_000_000, 4_000_000, 8_000_000, 16_000_000)
MAX_OBJECTIVE = 20000
CONTEXT_NAME = "loom_weave_mode"
_FINISHED_PROGRAM = {"completed", "cancelled"}
_FINISHED_RUN = {"integrated", "cancelled", "delivered"}

GUIDE = (
    "This conversation is in Weave mode. It is a conversation first: answer greetings, questions and small "
    "requests directly and quickly, exactly as in chat, with your normal tools. Weave adds one ability: a "
    "persistent project that plans milestones, runs many agent episodes in its own isolated checkout of this "
    "repository, has every task independently reviewed and merges the result back; it can run for hours or days.\n"
    "- Propose a project with weave_propose when the person asks for substantial code work in a Git repository that "
    "needs many tasks and sessions, or agrees when you suggest one. Never for a greeting, a question, or work you can "
    "finish in this turn yourself.\n"
    "- First make sure you know what they want; if the goal is unclear, ask in plain conversation. The objective "
    "you propose is the project's whole brief: what to build, constraints, and what done looks like, self-contained. "
    "A proposal starts nothing: the person reviews it in the hub and starts it there.\n"
    "- While a project runs, keep talking normally. Call weave_status before you answer about its progress. "
    "Pass changes of direction into it with weave_steer, and pause or resume it with weave_control. Do not do "
    "its tasks yourself in this conversation unless the person asks you to.\n"
    "Long goal: when the person gives you time (two hours, an afternoon, a night) or says they will be away or not "
    "watching, call weave_goal in your first response, before the work, with hours, even when the task looks small "
    "enough for one turn: only a goal keeps you going, gets the result independently checked and uses the time they "
    "gave. The same for an open-ended objective with hours to run, research included. It needs no repository and "
    "no click. The Host then brings "
    "you back after each turn in this conversation until you call mark_thread_goal, so end a turn freely and keep "
    "going in the next. Use it instead of a proposal when the person will not be there to start one. Mark complete "
    "only when the objective is met in substance: an independent agent then checks your claim and its findings come "
    "back to you. Pass hours when the person gave you a time: a night asks for a night's depth, and with much of "
    "it left a correct result is a reason to go further, not to stop. Mark blocked only when you cannot go on "
    "without the person.\n"
    "Task list: a turn that uses any tool shows the person a task list; a reply that needs no tool needs no list. "
    "Put update_plan first in the same response as your first tool calls, whatever update_plan's own text says about "
    "simple work: two or more short outcome steps in the person's language, one in_progress, the rest pending. Advance "
    "it alongside the calls that begin the next step (a completed step takes an outcome and evidence_refs naming a "
    "call_id), and finish it before your final answer.\n"
    "Sub-agents: in an ordinary turn each one costs many times what the same reading costs you, so do the work "
    "yourself by default, a scan or an investigation of an ordinary repository included. In a long goal the default "
    "flips: you lead and delegate independent lines, because your history is re-sent on every step for hours (each "
    "goal turn says so). Use spawn_agent when the person asks for it, or "
    "when the work has lines that do not depend on each other and can run at the same time, each worth many steps of "
    "its own: subsystems of hundreds of files too large to read well in this turn, a literature survey beside an "
    "experiment, a separate experiment, a fresh review of a finished result. Check this when you write the list. "
    "Steps that feed each other in order, and runs competing for one GPU or one set of files, stay yours. The person "
    "telling you to work on your own means without them, not without sub-agents. When you do: one agent per line, at "
    "most four, each with a narrow brief (which "
    "paths, what to find out, a short report) and history_mode none unless it needs this conversation. Name the agent "
    "in its step of the list, leave what you delegated to them instead of reading it too, collect results with "
    "wait_agent, and check only what your answer relies on. Sub-agents share your checkout, so writers get disjoint "
    "files; overlapping edits are yours."
)


class Proposals:
    """The waiting proposal of each conversation, kept on disk so a Host restart keeps it."""

    def __init__(self, home):
        self.path = home / PROPOSALS_FILE
        self.lock = threading.Lock()

    def _read(self):
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.path)

    def get(self, thread_id):
        with self.lock:
            item = self._read().get(thread_id)
        return item if isinstance(item, dict) else None

    def put(self, thread_id, proposal):
        with self.lock:
            value = self._read()
            value[thread_id] = proposal
            self._write(value)
        return proposal

    def take(self, thread_id, proposal_id=None):
        """Remove the conversation's proposal (only `proposal_id`, when given); returns what was removed."""
        with self.lock:
            value = self._read()
            item = value.get(thread_id)
            if not isinstance(item, dict) or (proposal_id and item.get("id") != proposal_id):
                return None
            del value[thread_id]
            self._write(value)
        return item


def current_project(service, thread_id):
    """The conversation's project that has not finished: (program or None, root run or None)."""
    owned = program.for_thread(service.project_store, thread_id)
    if owned is not None:
        if owned.get("state") in _FINISHED_PROGRAM:
            return None, None
        run = service.project_store.get(owned["currentRunId"]) if owned.get("currentRunId") else None
        return owned, run
    roots = [run for run in service.project_store.list() if run.get("sourceThreadId") == thread_id
             and not run.get("teamOwnerId") and not run.get("integrationTarget")]
    run = max(roots, key=lambda item: item.get("createdAt", 0)) if roots else None
    return (None, run) if run and run.get("state") not in _FINISHED_RUN else (None, None)


def snap_budget(value):
    if type(value) is not int:
        return BUDGETS[2]
    return min(BUDGETS, key=lambda budget: abs(budget - value))


def status(service, thread_id):
    """What the agent needs to talk about the project: compact, never the raw overview."""
    overview = service.project_overview({"threadId": thread_id})
    run, owned = overview.get("run"), overview.get("program")
    result = {"proposal": service.weave_proposals.get(thread_id), "project": None}
    if run is None:
        return result
    plan = [{"id": task["id"], "state": task.get("state", "pending"), "instructions": task["instructions"][:300]}
            for task in run.get("plan") or []]
    position = run.get("ledgerPosition") or {}
    result["project"] = {
        "objective": (owned or {}).get("objective") or run["objective"].split("\n\nThis is milestone")[0],
        "programState": (owned or {}).get("state"),
        "runState": run.get("state"), "control": run.get("control"), "phase": run.get("phase"),
        "reason": (run.get("reason") or (owned or {}).get("reason") or "")[:600],
        "milestone": (run.get("milestone") or {}).get("index", 1),
        "plan": plan,
        "episodes": run.get("ledgerEpisodes", run.get("episodesUsed")),
        "tokens": position.get("chargedTokens", run.get("tokensUsed")), "tokenBudget": run.get("tokenBudget"),
        "agents": [{"role": agent.get("role"), "activity": str(agent.get("activity") or "")[:200]}
                   for agent in overview.get("agents") or []],
        "recentEvents": [event["kind"] for event in (overview.get("events") or [])[:8]],
        "guidance": list(run.get("guidance") or [])[-5:],
        "finished": (owned or {}).get("state") in _FINISHED_PROGRAM or run.get("state") in _FINISHED_RUN,
    }
    return result


def start(service, params):
    """The person starts the conversation's proposal from the hub; the program is created as the hub would."""
    thread_id = service._required_text(params, "threadId")
    proposal = service.weave_proposals.get(thread_id)
    if proposal is None or proposal.get("id") != params.get("proposalId"):
        raise ValueError("This proposal is no longer waiting; ask the agent for a new one")
    created = program.create(service, {
        "operationId": params.get("operationId") or f"weave:{proposal['id']}", "threadId": thread_id,
        "objective": proposal["objective"], "tokenBudget": params.get("tokenBudget", proposal["tokenBudget"]),
        "maxEpisodes": params.get("maxEpisodes", 400), "maxWorkers": params.get("maxWorkers", proposal["maxWorkers"]),
        "maxMilestones": params.get("maxMilestones", 20), "attachments": params.get("attachments"),
        "permissionMode": params.get("permissionMode", "workspace")})
    service.weave_proposals.take(thread_id, proposal["id"])
    return created


def dismiss(service, params):
    thread_id = service._required_text(params, "threadId")
    service.weave_proposals.take(thread_id, params.get("proposalId"))
    return {"dismissed": True}


def context_messages(service, session, step):
    """The mode's guide and a one-line state, for Weave turns of the person's own conversations.

    The line changes only with the project's coarse state, so the frames it appends stay rare.
    """
    if not getattr(session, "weave", False) or session.session_id in service._project_session_ids:
        return ()
    try:
        owned, run = current_project(service, session.session_id)
        proposal = service.weave_proposals.get(session.session_id)
    except (OSError, ValueError):
        owned, run, proposal = None, None, None
    long_goal = None
    try:
        long_goal = goal.view(service, session.session_id)
    except (OSError, ValueError, RuntimeError):
        pass
    if long_goal is not None and long_goal["status"] == "active":
        line = f"A long goal is running in this conversation: {long_goal['objective']}"
    elif run is not None:
        state = "paused" if run.get("control") != "active" else "running"
        line = (f"Current project: {state}, milestone {(run.get('milestone') or {}).get('index', 1)}. "
                f"Objective: {((owned or {}).get('objective') or run['objective'])[:300]}")
    elif proposal is not None:
        line = f"A proposal is waiting for the person to start it in the hub: {proposal['objective'][:300]}"
    else:
        line = "No project is running in this conversation."
    return (AIMessage(role=MessageRole.USER, name=CONTEXT_NAME, content=f"{GUIDE}\n{line}"),)


def tools(service):
    def weave_session(context):
        session = service.store.load(context.session_id)
        if not getattr(session, "weave", False) or context.session_id in service._project_session_ids:
            raise ValueError("weave tools are available only in a conversation in Weave mode")

    def read_status(context, _arguments):
        weave_session(context)
        value = status(service, context.session_id)
        project = value["project"]
        if project is None:
            content = "No project in this conversation." + (" A proposal is waiting for the person." if value["proposal"] else "")
        else:
            done = sum(1 for task in project["plan"] if task["state"] == "integrated")
            content = (f"Project {project['programState'] or project['runState']} ({project['control']}), milestone "
                       f"{project['milestone']}, {done}/{len(project['plan'])} tasks integrated.")
        return ToolResult(ok=True, content=content, data=value)

    def propose(context, arguments):
        weave_session(context)
        objective = str(arguments.get("objective") or "").strip()
        if not objective:
            raise ValueError("objective must not be empty")
        if len(objective) > MAX_OBJECTIVE:
            raise ValueError("objective is too long")
        _, run = current_project(service, context.session_id)
        if run is not None:
            raise ValueError("A project is already running in this conversation; steer it with weave_steer, "
                             "or the person stops it before a new one")
        state = describe_workspace(service.store.load(context.session_id).workspace_dir)["status"]
        if state != "ready":
            # Better said now than at the person's click, which would only fail.
            raise ValueError(f"This folder cannot start a project ({state}: it needs the root of a Git repository on a "
                             "branch with a first commit). Use weave_goal to work here, or tell the person the "
                             "Repository card can prepare it.")
        workers = arguments.get("max_workers", 1)
        proposal = {"id": str(uuid.uuid4()), "objective": objective, "title": str(arguments.get("title") or "").strip()[:120],
                    "tokenBudget": snap_budget(arguments.get("token_budget")),
                    "maxWorkers": workers if type(workers) is int and 1 <= workers <= MAX_WORKERS else 1,
                    "createdAt": time.time()}
        service.weave_proposals.put(context.session_id, proposal)
        return ToolResult(ok=True, content="Proposal shown in the hub; the person starts it there. Nothing runs until then.",
                          data={"proposal": proposal})

    def start_goal(context, arguments):
        weave_session(context)
        objective = str(arguments.get("objective") or "").strip()
        if not objective:
            raise ValueError("objective must not be empty")
        if len(objective) > MAX_OBJECTIVE:
            raise ValueError("objective is too long")
        _, run = current_project(service, context.session_id)
        if run is not None:
            raise ValueError("A project is running in this conversation; steer it with weave_steer")
        budget = snap_budget(getattr(service, "weave_budgets", {}).get(context.session_id, arguments.get("token_budget")))
        try:
            hours = max(0.0, min(float(arguments.get("hours") or 0), goal.MAX_HOURS))
        except (TypeError, ValueError):
            hours = 0.0
        goal.start(service, context.session_id, objective, budget, hours)
        given = f" and {hours:g} h of the person's time" if hours else ""
        return ToolResult(ok=True, content=f"Long goal started with a budget of {budget:,} tokens{given}. Carry on "
                          "working; the Host continues this conversation after each turn until you call "
                          "mark_thread_goal.\n" + goal.DELEGATION,
                          data={"tokenBudget": budget, "hours": hours})

    def steer(context, arguments):
        weave_session(context)
        text = str(arguments.get("guidance") or "").strip()
        if not text:
            raise ValueError("guidance must not be empty")
        _, run = current_project(service, context.session_id)
        if run is None:
            raise ValueError("No project is running in this conversation")
        service.project_steer({"runId": run["id"], "text": text})
        return ToolResult(ok=True, content="Guidance added; the project applies it from its next episode.", data={})

    def control(context, arguments):
        weave_session(context)
        action = arguments.get("action")
        if action not in ("pause", "resume"):
            raise ValueError("action must be pause or resume")
        _, run = current_project(service, context.session_id)
        if run is None:
            raise ValueError("No project is running in this conversation")
        updated = service.project_control({"operationId": str(uuid.uuid4()), "runId": run["id"],
                                           "expectedRevision": run["revision"]}, action)
        return ToolResult(ok=True, content=f"Project {action}d.", data={"control": updated.get("control"),
                                                                       "state": updated.get("state")})

    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    return (
        AgentTool(
            name="weave_status",
            description=("Read this conversation's persistent project: its objective, state, milestone, plan with each "
                         "task's state, agents at work, budget, recent events and guidance, and any waiting proposal."),
            input_schema=empty, handler=read_status, effect=ToolEffect.READ_ONLY),
        AgentTool(
            name="weave_propose",
            description=("Propose a persistent project for work that needs many sessions, or an open-ended goal with "
                         "hours or a night to run unattended. The person sees it in the hub and starts it there; "
                         "nothing runs before that. Replaces an earlier waiting proposal."),
            input_schema={"type": "object", "properties": {
                "objective": {"type": "string", "description": "The project's complete, self-contained brief: what to "
                              "build, constraints, and what done looks like. Written in the person's language."},
                "title": {"type": "string", "description": "A few words naming the project."},
                "token_budget": {"type": "integer", "description": "Suggested total token budget, one of "
                                 + ", ".join(f"{budget:,}" for budget in BUDGETS) + "; the person can change it."},
                "max_workers": {"type": "integer", "minimum": 1, "maximum": MAX_WORKERS,
                                "description": "Upper bound on agents implementing tasks at once. How many really run "
                                "is what the plan makes independent (disjoint files, no shared resources); 1 for a "
                                "small or tightly sequential objective."},
            }, "required": ["objective"], "additionalProperties": False},
            handler=propose, effect=ToolEffect.READ_ONLY),
        AgentTool(
            name="weave_goal",
            description=("Start a long goal in this conversation: the Host keeps it going turn after turn until you "
                         "call mark_thread_goal and an independent check agrees, or the budget runs out. No repository "
                         "or click needed. Call it first whenever the person gives you time or will not be watching, "
                         "and for research or open-ended work with hours or a night to run."),
            input_schema={"type": "object", "properties": {
                "objective": {"type": "string", "description": "The complete goal and what done looks like, in the "
                              "person's language; an agent with no history will check your work against it."},
                "token_budget": {"type": "integer", "description": "Suggested budget, one of "
                                 + ", ".join(f"{budget:,}" for budget in BUDGETS) + "; the hub's setting wins."},
                "hours": {"type": "number", "description": "How long the person gave you, from their words "
                          "('一整晚' or 'overnight' is about 8, 'this afternoon' about 4); omit when they gave no time. "
                          "The work is sized to it and is not finished early only because it is correct."},
            }, "required": ["objective"], "additionalProperties": False},
            handler=start_goal, effect=ToolEffect.READ_ONLY),
        AgentTool(
            name="weave_steer",
            description=("Give the running project guidance from the person: a change of direction or an added "
                         "requirement. It reaches every later episode and outranks the plan where they conflict."),
            input_schema={"type": "object", "properties": {"guidance": {"type": "string"}},
                          "required": ["guidance"], "additionalProperties": False},
            handler=steer, effect=ToolEffect.READ_ONLY),
        AgentTool(
            name="weave_control",
            description="Pause the running project, or resume a paused one, when the person asks.",
            input_schema={"type": "object", "properties": {"action": {"type": "string", "enum": ["pause", "resume"]}},
                          "required": ["action"], "additionalProperties": False},
            handler=control, effect=ToolEffect.READ_ONLY),
    )
