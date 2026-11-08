"""A long goal's charter and notes: what an expert would deliver, how it is judged, and what the work has learned.

A goal used to be one sentence and a loop: work until you claim, a check of the claim, and when time was left a
list of "deeper" items stapled on afterwards. A real night came back as many experiments and no whole: the agent
did its own idea of the work, then was pulled back to do more of it. Systems that produce research-grade results
(Sakana's AI Scientist, Agent Laboratory, Kosmos, Anthropic's long-running harness) share the opposite shape: the
request is first expanded into the deliverable and fixed acceptance criteria, the work follows planned phases,
and depth comes from review and revision against that standard.

So a goal now has a charter, written by the lead before work that changes anything:
- deliverable: what a top expert in the field would hand in for this request (for research a manuscript a
  referee could judge, with code that reproduces every number);
- criteria: concrete, checkable acceptance criteria. They are the checker's standard; changing them takes a
  reason, and a criterion left out stays visible as dropped, never silently gone;
- phases: the planned arc, sized to the person's time.
Review rounds append their requests as new criteria, so a revision is part of the charter, not an afterthought.

Notes are the work's own record (done, found, decided, next), shown in every goal turn so the thread survives a
compacted history; review responses go there too.
"""
from __future__ import annotations

import time

from app.agent_runtime.contracts import ToolEffect
from app.agent_runtime.tools import ToolResult
from app.agent_runtime.turn_plan import PLAN_TOOLS
from app.agent_runtime.tool_selection import WEAVE_PREFIX

MIN_CRITERIA, MAX_CRITERIA = 3, 24
MIN_PHASES, MAX_PHASES = 2, 10
MIN_CRITERION, MAX_TEXT = 12, 600
MAX_DELIVERABLE = 2000
MAX_NOTES, NOTES_SHOWN, MAX_NOTE = 60, 6, 2000
MAX_HOLDS = 2
KINDS = ("research", "software", "analysis", "writing", "other")
FAILURE_KIND = "goal_charter_required"
_FREE = PLAN_TOOLS | {"tool_search", "get_thread_state", "mark_thread_goal", "investigate"}

HELD = ("Not executed: this long goal has no charter yet. Before work that changes anything, call weave_charter: "
        "the deliverable a top expert in this field would hand in for this request, concrete acceptance criteria "
        "the check will hold it to, and the phases the time is spent in. Reading first is fine.")
GUIDE = (
    "Charter: before work that changes anything, write the goal's charter with weave_charter. Name the deliverable a "
    "top expert in the field would hand in for this request, not the least that answers it: for research a "
    "manuscript a referee could judge (the question and why it matters, related work and what is new, method, "
    "experiments against fair baselines with ablations and uncertainty, limitations) with code and data that "
    "reproduce every number; for software, working, tested, documented code; for analysis, conclusions with the "
    "evidence and its limits. Give concrete criteria the check can verify by reading and running, and phases sized "
    "to the time. For research, begin by surveying the literature and checking novelty (a line or investigate can "
    "do it while you set up), then pilot two or three candidate directions briefly and commit to one, saying why "
    "in a note. Change the charter only with a reason; never lower a criterion to make it pass.\n"
    "Notes: end each goal turn with weave_note: what you did, what you found (with the numbers), what you decided "
    "and why, and what comes next. Every goal turn shows the latest notes, so they carry the work when your history "
    "is compacted. Answer each point of a review in a note before you claim again.")


def _clean(text, limit):
    return " ".join(str(text or "").split())[:limit]


def charter_of(record):
    return (record or {}).get("charter")


def set_charter(record, arguments, now=None):
    """Write or revise the charter in the record; returns what changed. Raises with what to fix."""
    now = now or time.time()
    previous = charter_of(record)
    deliverable = _clean(arguments.get("deliverable"), MAX_DELIVERABLE)
    raw = [_clean(item, MAX_TEXT) for item in arguments.get("criteria") or ()]
    phases = [_clean(item, 200) for item in arguments.get("phases") or ()]
    kind = arguments.get("kind") if arguments.get("kind") in KINDS else "other"
    reason = _clean(arguments.get("reason"), 600)
    if len(deliverable) < 40:
        raise ValueError("Describe the deliverable as an expert in the field would hand it in, in a few sentences.")
    if not MIN_CRITERIA <= len(raw) <= MAX_CRITERIA or any(len(item) < MIN_CRITERION for item in raw):
        raise ValueError(f"Give {MIN_CRITERIA} to {MAX_CRITERIA} acceptance criteria, each concrete enough that a "
                         "checker can verify it by reading and running.")
    if len(set(raw)) != len(raw):
        raise ValueError("Each criterion must be different.")
    if not MIN_PHASES <= len(phases) <= MAX_PHASES or any(not phase for phase in phases):
        raise ValueError(f"Give {MIN_PHASES} to {MAX_PHASES} phases, in order, sized to the time.")
    if previous and not reason:
        raise ValueError("Changing the charter takes a reason: say what changed and why.")
    old = {item["text"]: item for item in (previous or {}).get("criteria") or ()}
    criteria, number = [], max([int(item["id"][1:]) for item in old.values() if item["id"][1:].isdigit()] or [0])
    for text in raw:
        if text in old:
            kept = old[text]
            criteria.append({**kept, "status": "open", "note": ""} if kept.get("status") == "dropped" else kept)
        else:
            number += 1
            criteria.append({"id": f"C{number}", "text": text, "status": "open", "origin": "charter", "note": ""})
    dropped = [{**item, "status": "dropped", "note": reason}
               for text, item in old.items() if text not in raw and item.get("status") != "dropped"]
    kept_dropped = [item for item in (previous or {}).get("criteria") or () if item.get("status") == "dropped"
                    and item["text"] not in raw]
    current = arguments.get("current_phase")
    current = current if isinstance(current, int) and 0 <= current < len(phases) else (
        (previous or {}).get("current", 0) if previous and len(phases) == len(previous.get("phases") or ()) else 0)
    charter = {"deliverable": deliverable, "kind": kind, "criteria": criteria + kept_dropped + dropped,
               "phases": phases, "current": min(current, len(phases) - 1),
               "revisions": [*((previous or {}).get("revisions") or ()),
                             *([{"at": now, "reason": reason, "dropped": [item["id"] for item in dropped]}] if previous else [])][-20:],
               "writtenAt": (previous or {}).get("writtenAt") or now}
    record["charter"] = charter
    return {"new": not previous, "dropped": [item["text"] for item in dropped]}


def advance_phase(record, index):
    charter = charter_of(record)
    if charter and isinstance(index, int) and 0 <= index < len(charter["phases"]):
        charter["current"] = index


def add_note(record, text, now=None):
    text = str(text or "").strip()[:MAX_NOTE]
    if len(text) < 20:
        raise ValueError("A note says what you did, found, decided and what comes next.")
    notes = [*(record.get("notes") or ()), {"at": now or time.time(), "text": text}]
    record["notes"] = notes[-MAX_NOTES:]


def add_review_round(record, round_number, requests):
    """A passed review with time left: its requests become this round's criteria, in the charter."""
    charter = charter_of(record)
    if not charter:
        return []
    number = max([int(item["id"][1:]) for item in charter["criteria"] if item["id"][1:].isdigit()] or [0])
    added = []
    for text in requests:
        text = _clean(text, MAX_TEXT)
        if len(text) < MIN_CRITERION or any(item["text"] == text for item in charter["criteria"]):
            continue
        number += 1
        added.append({"id": f"C{number}", "text": text, "status": "open", "origin": f"review {round_number}", "note": ""})
    charter["criteria"].extend(added)
    title = f"Revision round {round_number}"
    if title not in charter["phases"]:
        charter["phases"].append(title)
    charter["current"] = len(charter["phases"]) - 1
    return added


def apply_verdict(record, results):
    """The checker's finding for each criterion, by id."""
    charter = charter_of(record)
    if not charter or not isinstance(results, list):
        return
    by_id = {item["id"]: item for item in charter["criteria"]}
    for result in results:
        if not isinstance(result, dict) or result.get("id") not in by_id or by_id[result["id"]]["status"] == "dropped":
            continue
        by_id[result["id"]].update(status="met" if result.get("met") is True else "unmet",
                                   note=_clean(result.get("evidence"), 400))


def context_lines(record):
    """The charter and the latest notes, for every goal turn."""
    lines = []
    charter = charter_of(record)
    if charter:
        phases = " → ".join(f"[{phase}]" if at == charter["current"] else phase for at, phase in enumerate(charter["phases"]))
        lines.append(f"Charter. Deliverable: {charter['deliverable']}\nPhases: {phases}\nCriteria (the check holds you to these):")
        lines += [f"- {item['id']} ({item['status']}{', from ' + item['origin'] if item['origin'] != 'charter' else ''}): "
                  f"{item['text']}" + (f" — {item['note']}" if item.get("note") and item["status"] != "open" else "")
                  for item in charter["criteria"] if item["status"] != "dropped"]
    notes = (record.get("notes") or [])[-NOTES_SHOWN:]
    if notes:
        lines.append("Your latest notes:")
        lines += [f"- {time.strftime('%H:%M', time.localtime(note['at']))} {note['text']}" for note in notes]
    return lines


def checker_lines(record):
    """What the checker judges against: the charter, each criterion by id."""
    charter = charter_of(record)
    if not charter:
        return ""
    criteria = "\n".join(f"- {item['id']}: {item['text']}" for item in charter["criteria"] if item["status"] != "dropped")
    dropped = [item for item in charter["criteria"] if item["status"] == "dropped"]
    gone = ("\nCriteria the agent dropped, with its reason (judge whether the deliverable still holds without them):\n"
            + "\n".join(f"- {item['text']}: {item.get('note') or 'no reason'}" for item in dropped)) if dropped else ""
    return f"\nDeliverable the agent committed to:\n{charter['deliverable']}\nAcceptance criteria:\n{criteria}{gone}\n"


def hold(service, session, step, calls):
    """Before a batch runs in a goal with no charter: hold what changes things, at most MAX_HOLDS times."""
    if getattr(session, "weave", False) is not True:
        return None
    record = service.weave_goals.get(session.session_id)
    if not record or record.get("phase") != "working" or charter_of(record) or record.get("charterGaveWay"):
        return None

    def free(call):
        if call.name in _FREE or call.name.startswith(WEAVE_PREFIX):
            return True
        tool = step.tool_router.get(call.name) if step is not None else None
        return tool is not None and tool.effect is ToolEffect.READ_ONLY and call.name != "spawn_agent"

    waiting = tuple(call for call in calls if not free(call))
    if not waiting:
        return None
    with service.project_runner.guard:
        record = service.weave_goals.get(session.session_id)
        if not record or charter_of(record):
            return None
        record["charterHolds"] = record.get("charterHolds", 0) + 1
        if record["charterHolds"] > MAX_HOLDS:  # it asks for a charter, it guards nothing: never at the cost of the turn
            record["charterGaveWay"] = True
            service.weave_goals.put(session.session_id, record)
            return None
        service.weave_goals.put(session.session_id, record)
    refusal = ToolResult(False, HELD, {"failure_kind": FAILURE_KIND, "execution_status": "not_executed",
                                       "retry_policy": "write_the_charter"})
    return tuple((call, refusal) for call in waiting)


def view(record):
    charter = charter_of(record)
    reviews = [{"round": item.get("round"), "verdict": item.get("verdict"), "summary": item.get("summary", "")[:400],
                "major": len(item.get("major") or ()), "minor": len(item.get("minor") or ())}
               for item in (record or {}).get("reviews") or ()][-6:]
    if not charter:
        return {"charter": None, "reviews": reviews, "notes": len((record or {}).get("notes") or ())}
    return {"charter": {"deliverable": charter["deliverable"][:600], "kind": charter["kind"],
                        "phases": charter["phases"], "current": charter["current"],
                        "criteria": [{"id": item["id"], "text": item["text"][:300], "status": item["status"],
                                      "origin": item["origin"]} for item in charter["criteria"]]},
            "reviews": reviews, "notes": len(record.get("notes") or ())}
