"""Research judgment in a long goal: what to work on, and an outside view while there is time to act on it.

The charter, the ledger, the lines and the final check made goals reliable. What a real six-hour research goal still
lacked was judgment: it went straight on with the subject an earlier goal had picked although its objective named
three candidates, and nobody outside looked at the work until the claim, when a weak novelty argument and a single
seed are the most expensive to fix (thread 262978b8, 2026-11-10). The guide already said "pilot two or three
candidate directions briefly and commit to one"; as a sentence it was not done. Systems that do this well make it a
stage with an exit: co-scientist ranks hypotheses by pairwise comparison, Jr. AI Scientist starts by writing down the
limits of the work it builds on, and LLM ideas lose more than human ones once executed (Si, Hashimoto & Yang 2025),
so a cheap pilot comes before the choice. Agents4Science found model reviewers sycophantic: the referees here state
the reasons to reject first and run on the checker's model, which the person can make a stronger one.

Two Host-run steps, both for research goals:
- Directions: before changing work the lead names 3 to 5 candidate directions (closest prior work, what is new, a pilot,
  the signal that keeps it and the one that drops it; one that continues existing work lists that work's limits),
  pilots them, and an independent referee ranks them pair by pair. The lead chooses, against the ranking only with a
  reason. Like every gate it asks a few times and then gives way; a claim still needs the choice (or a skip reason).
- Early review: once the work has results (a ledger claim) or a third of the time has gone, a referee with none of the
  conversation reads the work, searches the literature itself and says what a top venue would reject it for. Its
  requests become charter criteria the final check holds the work to, while there is time to meet them.

What each referee says reaches the lead inside its next tool result, or at the start of its next goal turn.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

from app.agent_runtime.contracts import AgentStatus
from app.agent_runtime.tools import ToolResult

from . import goal_charter
from .goal_common import changes, decide, span as _span, time_left as _left

ENABLED = True  # tests of other parts turn it off (tests/conftest.py)
ROLES = ("goal-direction", "goal-review")  # Host-run referees: neither the work nor its lines
MIN_DIRECTIONS, MAX_DIRECTIONS = 3, 5
MIN_TEXT = 12
MAX_HOLDS = 2  # it asks for a decision, it guards nothing: never at the cost of the goal
MAX_REFEREE_TRIES = 2
EARLY_TIME_SHARE = 0.3  # of the person's time, when no result is in the ledger yet
EARLY_TOKENS = 3_000_000  # weighted, for a goal given no time
EARLY_ROOM = 0.25  # of the time (or budget) that must be left for the requests to be met
MAX_REQUESTS = 8
DONE = ("chosen", "skipped", "gave_way")

DIRECTIONS_HELD = (
    "Not executed: this is a research goal and no direction has been chosen. Before work that changes anything, call "
    "weave_directions with 3 to 5 candidate directions: for each, the closest prior work (a link or arXiv id you "
    "found), what would be new, a pilot that tests it in minutes, the result that keeps it and the one that drops it. "
    "When a candidate continues work already in this folder, name that work and at least two of its limits. Reading "
    "and searching first is fine. If the person already fixed the direction, give skip_reason instead.")
PILOT = ("Directions recorded. Pilot each one cheaply now (minutes each, not hours: a small run, a derivation, a "
         "closer look at the closest work), then call weave_directions again with the same candidates and pilot_result "
         "for each; an independent referee then ranks them.")
RANKING = ("Pilots recorded. An independent referee with none of this conversation is ranking the directions pair by "
           "pair; its ranking reaches you in a later tool result. Keep piloting or reading meanwhile; choose with "
           "weave_directions(choose=...) once it arrives.")
CHOOSE_HELD = ("Not executed: the referee's ranking of your directions is in (shown above in an earlier result or in "
               "this turn's context). Choose one with weave_directions(choose=<number>, reason=...) before changing "
               "anything; against the ranking only with a reason.")
NO_DIRECTION = ("No research direction was chosen in this goal: name the candidates with weave_directions, pilot them, "
                "and choose one (or give skip_reason when the person fixed the direction), then claim again.")

DIRECTION_PROMPT = (
    "You are an independent research advisor with none of the conversation that produced these candidates. A research "
    "agent must choose one direction to spend the rest of its time on. Do not trust the candidates' descriptions of "
    "the prior work: search for the closest published work yourself (several queries, from specific to broad) and "
    "read what you need in this workspace; run nothing that changes it. For each candidate first write the strongest "
    "reason a top reviewer would give against pursuing it. Then compare the candidates in pairs (every pair), each "
    "time saying which is better and why, by: importance to the field, real novelty against what you found, what its "
    "pilot showed, and whether it can reach a convincing result in the time left. End with only JSON: "
    '{"summary":"...","against":{"1":"..."},"pairs":[{"a":1,"b":2,"better":1,"why":"..."}],'
    '"ranking":[2,1,3],"advice":"what the chosen direction must do to be convincing"} '
    "(numbers are the candidates' numbers below).\n")
EARLY_PROMPT = (
    "You are a senior referee with none of the conversation that did this work, reading a research project midway, "
    "while there is still time to change it. This is not a pass/fail check. Read the workspace (the draft, the "
    "results, the code) and run what you need to see; do not edit, fix or delete anything. Search the literature "
    "yourself for the closest prior work (several queries, from specific to broad): do not rely on the draft's own "
    "related work. Then write, first, the strongest reasons a top venue in this field would reject the work as it "
    "stands (triviality, prior work that already shows it, unfair or missing baselines, a single seed or sample "
    "too small for the claim, a wrong statement). For each headline claim in the ledger, say how many random seeds, "
    "trained models and samples carry it; where one seed, one model or a small sample carries a claim, ask for the "
    "replication that would settle it (how many seeds, how large a sample), sized to this computer and the time left. "
    "Then the revisions that would most raise it, most valuable first, "
    "each concrete and checkable and doable in the time left. End with only JSON: "
    '{"summary":"...","closest_work":["title or id: how it relates"],"reject":["..."],"requests":["..."]}.\n')
DIRECTION_BACK = ("An independent referee ranked your research directions (best first): {order}.\n{summary}\n"
                  "Against each:\n{against}\nAdvice: {advice}\nChoose with weave_directions(choose=<number>, "
                  "reason=...); a choice against the ranking needs a reason. Write the choice into the charter "
                  "(weave_charter with a reason) when the deliverable changes.")
DIRECTION_FAILED = ("The independent referee could not rank your directions ({why}). Choose one yourself with "
                    "weave_directions(choose=<number>, reason=...), weighing what the pilots showed.")
EARLY_BACK = ("A mid-course referee with none of this conversation read the work. {summary}\nWhat a top venue would "
              "reject it for now:\n{reject}\nIts requests are now criteria the final check holds you to ({ids}). "
              "Address them while there is time, most valuable first, and answer each in a weave_note.")


# --- the machine ----------------------------------------------------------------------------------------------------

def probe(workspace):
    """What this computer offers the work, read once when a goal starts. A real lead learned there was a GPU only
    when the person asked "有GPU吧", and then ran three 2.9 GB downloads at once over the connection the model
    provider shares."""
    if not ENABLED:
        return {}
    found = {"cpus": os.cpu_count() or 0, "gpus": []}
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        done = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=8, stdin=subprocess.DEVNULL, creationflags=flags)
        if done.returncode == 0:
            found["gpus"] = [line.strip() for line in done.stdout.splitlines() if line.strip()][:4]
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        import psutil
        found["memoryGb"] = round(psutil.virtual_memory().total / 2**30)
    except Exception:  # noqa: BLE001 - a missing optional module only leaves the number out
        pass
    try:
        found["diskFreeGb"] = round(shutil.disk_usage(workspace).free / 2**30, 1) if workspace else None
    except OSError:
        pass
    return found


def machine_line(record):
    machine = (record or {}).get("machine")
    if not machine:
        return ""
    gpus = ", ".join(machine.get("gpus") or ()) or "none found (nvidia-smi)"
    parts = [f"{machine.get('cpus')} logical processors"]
    if machine.get("memoryGb"):
        parts.append(f"{machine['memoryGb']} GB memory")
    if machine.get("diskFreeGb") is not None:
        parts.append(f"{machine['diskFreeGb']} GB free on the workspace drive")
    text = f"This computer: {', '.join(parts)}; GPU: {gpus}."
    if machine.get("gpus"):
        text += " Check that the Python environment you run has a GPU build before relying on it."
    return text + (" Run one large download at a time: downloads share the connection the model provider uses, and a "
                   "saturated link fails your own requests.")


# --- the person's time ----------------------------------------------------------------------------------------------

# A duration or a part of the day, in Chinese or English. Hours steer a goal ("go deeper while it lasts", the card's
# time bar, "the 6 h are up, so finish"), so they come from the person's words, never from the model's guess: a real
# lead given "深入研究一下 ai ran" with no time set hours=6 itself, and the goal began to wrap up at a deadline no one set.
_NUMBER = r"(?:[0-9０-９]+(?:\.[0-9]+)?|[一二两三四五六七八九十百半几]+)"
TIME_WORDS = re.compile(
    rf"{_NUMBER}\s*(?:个)?\s*(?:半)?\s*(?:小时|钟头|分钟|天|夜|晚上|晚)"
    r"|一整晚|整晚|今晚|今夜|通宵|整夜|一整夜|一晚上|下午|上午|中午|晚上|明早|明天|天亮|白天|一整天|周末"
    r"|\b\d+(?:\.\d+)?\s*(?:h|hrs?|hours?|mins?|minutes?|days?)\b"
    r"|\b(?:overnight|tonight|all night|the night|this afternoon|this evening|this morning|all day|the weekend"
    r"|an hour|half an hour|a few hours|couple of hours|until morning|till morning)\b", re.I)
PERSON_TURNS = 4  # the person's latest messages read for a time: the request and what they added around it
HOURS_DROPPED = ("hours was not kept: the person named no time, so this goal is paced by its token budget (you are "
                 "told when to wrap up). Never invent a time.")


def persons_words(service, thread_id):
    """The person's latest messages in the conversation: their own words, not the Host's or the mode's context."""
    try:
        messages = service.store.load(thread_id).messages
    except (OSError, ValueError, KeyError):
        return []
    said = [str(message.content or "") for message in messages
            if getattr(message.role, "value", message.role) == "user" and not getattr(message, "name", None)]
    return said[-PERSON_TURNS:]


def named_time(texts):
    """Whether any of these words gives a time: a duration or a part of the day."""
    return any(TIME_WORDS.search(text or "") for text in texts)


# --- directions -----------------------------------------------------------------------------------------------------

def _research(record):
    charter = goal_charter.charter_of(record)
    return bool(charter) and charter.get("kind") == "research"


def _state(record):
    return ((record or {}).get("directions") or {}).get("state", "")


def needed(record):
    """Whether this goal still owes a direction decision."""
    return ENABLED and _research(record) and record.get("phase") == "working" and _state(record) not in DONE


def _clean(value, limit=600):
    return " ".join(str(value or "").split())[:limit]


def _candidates(raw):
    if not isinstance(raw, list) or not MIN_DIRECTIONS <= len(raw) <= MAX_DIRECTIONS:
        raise ValueError(f"Give {MIN_DIRECTIONS} to {MAX_DIRECTIONS} candidate directions: one choice is not a choice.")
    kept = []
    for at, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError("Each direction is an object.")
        value = {key: _clean(item.get(key)) for key in ("title", "question", "new", "pilot", "signal", "kill", "pilot_result")}
        value["closest_work"] = [_clean(text, 300) for text in item.get("closest_work") or () if _clean(text)][:6]
        value["existing"] = [_clean(text, 200) for text in item.get("existing") or () if _clean(text)][:8]
        value["limitations"] = [_clean(text, 300) for text in item.get("limitations") or () if _clean(text)][:6]
        missing = [key for key in ("title", "question", "new", "pilot", "signal", "kill") if len(value[key]) < (3 if key == "title" else MIN_TEXT)]
        if missing:
            raise ValueError(f"Direction {at} needs {', '.join(missing)}: each says what it asks, what would be new, "
                             "the pilot, and the results that keep it or drop it.")
        if not value["closest_work"]:
            raise ValueError(f"Direction {at} needs closest_work: the nearest prior work you found (a link or arXiv id). "
                             "Search first; novelty is judged against it.")
        if value["existing"] and len(value["limitations"]) < 2:
            raise ValueError(f"Direction {at} continues existing work: give at least two limitations of that work, "
                             "as a reviewer of it would.")
        kept.append(value)
    if len({item["title"].lower() for item in kept}) != len(kept):
        raise ValueError("Each direction must be different.")
    return kept


def set_directions(service, thread_id, arguments):
    """The lead's weave_directions: candidates (with pilot results to have them ranked), a choice, or a skip."""
    with service.project_runner.guard:
        record = service.weave_goals.get(thread_id)
        if record is None or record.get("phase") == "done":
            raise ValueError("weave_directions belongs to a running long goal.")
        state = dict(record.get("directions") or {})
        if arguments.get("skip_reason") is not None:
            reason = _clean(arguments.get("skip_reason"))
            if len(reason) < 40:
                raise ValueError("Say why no choice is needed (for example: the person fixed the direction, in their words).")
            state.update(state="skipped", reason=reason, at=time.time())
            decide(record, "directions_skipped")
            text = "Direction stage skipped; the reason is shown to the person and to the final check."
        elif arguments.get("choose") is not None and not arguments.get("candidates"):
            chosen, candidates = arguments.get("choose"), state.get("candidates") or []
            if type(chosen) is not int or not 1 <= chosen <= len(candidates):
                raise ValueError("choose is the number of one of your candidates" + (f" (1 to {len(candidates)})." if candidates
                                 else "; name them first."))
            if state.get("state") == "ranking":
                raise ValueError("The referee is still ranking; its ranking reaches you in a later tool result.")
            if not state.get("pilotsDone"):
                raise ValueError("Pilot the candidates first and report pilot_result for each.")
            reason = _clean(arguments.get("reason"))
            top = ((state.get("ranking") or {}).get("ranking") or [None])[0]
            if top and chosen != top and len(reason) < 40:
                raise ValueError(f"The referee ranked {top} first: choosing {chosen} needs a reason (what you know that it did not).")
            state.update(state="chosen", chosen=chosen, reason=reason, at=time.time())
            decide(record, "direction_chosen", chosen=chosen, top=top)
            text = (f"Direction {chosen} chosen: {candidates[chosen - 1]['title']}. Write it into the charter with "
                    "weave_charter (with a reason) if the deliverable changes, and say why in a weave_note.")
        else:
            candidates = _candidates(arguments.get("candidates"))
            piloted = all(len(item["pilot_result"]) >= MIN_TEXT for item in candidates)
            state.update(candidates=candidates, at=time.time(), pilotsDone=piloted,
                         state="ranking" if piloted else "declared", ranking=None, refereeId="", tries=0)
            decide(record, "directions_named", count=len(candidates), piloted=piloted)
            text = RANKING if piloted else PILOT
            if arguments.get("choose") is not None:  # a real lead named and chose in one call
                text += " Your choose was not taken: choose once the referee's ranking has reached you."
        record["directions"] = state
        service.weave_goals.put(thread_id, record)
    return ToolResult(ok=True, content=text, data={"state": state["state"]})


def hold(service, session, step, calls):
    """Before a batch runs: hold changing work while a research goal has no direction, a few times."""
    if getattr(session, "weave", False) is not True:
        return None
    record = service.weave_goals.get(session.session_id)
    if not record or not needed(record) or _state(record) in {"declared", "ranking"}:
        return None  # piloting is changing work, and it is what the step asks for
    waiting = tuple(call for call in calls if changes(step, call))
    if not waiting:
        return None
    ranked = _state(record) == "ranked"
    with service.project_runner.guard:
        record = service.weave_goals.get(session.session_id)
        if not record or not needed(record):
            return None
        state = record.setdefault("directions", {})
        key = "chooseHolds" if ranked else "holds"
        state[key] = state.get(key, 0) + 1
        if state[key] > (1 if ranked else MAX_HOLDS):
            if not ranked:
                state["state"] = "gave_way"
            decide(record, "directions_gave_way", ranked=ranked)
            service.weave_goals.put(session.session_id, record)
            return None
        service.weave_goals.put(session.session_id, record)
    refusal = ToolResult(False, CHOOSE_HELD if ranked else DIRECTIONS_HELD, {
        "failure_kind": "goal_direction_required", "execution_status": "not_executed", "retry_policy": "choose_a_direction"})
    return tuple((call, refusal) for call in waiting)


def claim_gap(record):
    """What a claim of completion lacks on this side; goal.claim_check hands it back like the other gaps."""
    return NO_DIRECTION if ENABLED and _research(record) and _state(record) not in {"chosen", "skipped"} else ""


# --- referees -------------------------------------------------------------------------------------------------------

def _spawn(service, thread_id, record, task, role):
    from . import goal  # the goal module owns the checker's model choice
    service._prepare_turn(service.store.load(thread_id))
    snapshot = service.runtime.agent_control.spawn(thread_id, task, role=role, history_mode="none", background=True,
                                                   model=goal._checker_model(service, record))
    return snapshot.node.session_id


def _finished(service, thread_id, agent_id):
    """(done, final text or None) for a referee; a failed or stopped one is done with nothing."""
    control = service.runtime.agent_control
    snapshot = control.snapshot(thread_id, agent_id)
    if snapshot.execution_running or snapshot.session_status in {AgentStatus.RUNNING}:
        return False, None
    text = snapshot.final_text if snapshot.session_status is AgentStatus.COMPLETED else None
    try:
        control.close_agent(thread_id, agent_id)
    except (OSError, ValueError, RuntimeError):
        pass
    return True, text


def _json_with(text, key):
    """The last JSON object in a reply that has `key`."""
    found, at, decoder = None, (text or "").find("{"), json.JSONDecoder()
    while at >= 0:
        try:
            value, end = decoder.raw_decode(text, at)
        except ValueError:
            at = text.find("{", at + 1)
            continue
        if isinstance(value, dict) and key in value:
            found = value
        at = text.find("{", end)
    return found


def _time_note(record):
    if record.get("hours"):
        return f"\nTime left of the {record['hours']:g} h the person gave: {_span(_left(record))}.\n"
    return f"\nToken budget left: {1 - record['tokens'] / max(1, record['tokenBudget']):.0%}.\n"


def _direction_task(record, objective):
    lines = []
    for at, item in enumerate(record["directions"]["candidates"], 1):
        lines.append(f"{at}. {item['title']}\n   Question: {item['question']}\n   Closest work: {'; '.join(item['closest_work'])}"
                     f"\n   New: {item['new']}\n   Pilot: {item['pilot']}\n   Pilot result: {item['pilot_result']}"
                     f"\n   Keep if: {item['signal']}\n   Drop if: {item['kill']}"
                     + (f"\n   Continues: {'; '.join(item['existing'])}\n   Its limitations: {'; '.join(item['limitations'])}"
                        if item["existing"] else ""))
    charter = goal_charter.charter_of(record) or {}
    return (DIRECTION_PROMPT + "Objective:\n" + objective + "\nDeliverable:\n" + charter.get("deliverable", "")
            + _time_note(record) + "Candidates:\n" + "\n".join(lines))


def _ranking(value, count):
    order = [item for item in (value or {}).get("ranking") or () if type(item) is int and 1 <= item <= count]
    order = list(dict.fromkeys(order))
    return order if len(order) == count else None


def _tell(record, text):
    """A referee's finding for the lead: in its next tool result, or its next goal turn."""
    record["depthNotices"] = [*(record.get("depthNotices") or ()), {"at": time.time(), "text": text[:6000]}][-6:]


def _advance_directions(service, thread_id, record, objective):
    state = record.get("directions") or {}
    if state.get("state") != "ranking" or record.get("phase") != "working":
        return False
    if not state.get("refereeId"):
        state["tries"] = state.get("tries", 0) + 1
        state["refereeId"] = _spawn(service, thread_id, record, _direction_task(record, objective), "goal-direction")
        return True
    done, text = _finished(service, thread_id, state["refereeId"])
    if not done:
        return False
    state["refereeId"] = ""
    count = len(state.get("candidates") or ())
    value = _json_with(text, "ranking")
    order = _ranking(value, count)
    if order is None and state["tries"] < MAX_REFEREE_TRIES:
        return True  # spawned again on the next tick
    state["state"] = "ranked"
    if order is None:
        state["ranking"] = None
        _tell(record, DIRECTION_FAILED.format(why="no ranking in its reply" if text else "it did not finish"))
        decide(record, "directions_unranked")
        return True
    against = value.get("against") if isinstance(value.get("against"), dict) else {}
    state["ranking"] = {"ranking": order, "summary": _clean(value.get("summary"), 1200),
                        "advice": _clean(value.get("advice"), 1200),
                        "against": {str(key): _clean(text, 400) for key, text in against.items()},
                        "pairs": [pair for pair in value.get("pairs") or () if isinstance(pair, dict)][:12]}
    titles = [item["title"] for item in state["candidates"]]
    _tell(record, DIRECTION_BACK.format(
        order=", ".join(f"{at}. {titles[at - 1]}" for at in order), summary=state["ranking"]["summary"],
        against="\n".join(f"- {at}: {state['ranking']['against'].get(str(at), '')}" for at in order),
        advice=state["ranking"]["advice"] or "none given"))
    decide(record, "directions_ranked", top=order[0])
    return True


# --- early review ---------------------------------------------------------------------------------------------------

def _review_due(record):
    if not _research(record) or record.get("phase") != "working" or record.get("earlyReview"):
        return False
    if _state(record) not in DONE or record.get("reviews"):
        return False  # before a direction there is nothing to review; after a final review its own rounds follow
    hours = record.get("hours") or 0
    if hours:
        used, left = time.time() - record["startedAt"], _left(record)
        if left < EARLY_ROOM * hours * 3600:
            return False
        return bool(record.get("claims")) or used >= EARLY_TIME_SHARE * hours * 3600
    if record["tokens"] > (1 - EARLY_ROOM) * record["tokenBudget"]:
        return False
    return bool(record.get("claims")) or record["tokens"] >= EARLY_TOKENS


def _review_task(service, thread_id, record, objective):
    from . import goal
    notes = "\n".join(f"- {note['text'][:800]}" for note in (record.get("notes") or [])[-4:])
    machine = machine_line(record)
    return (EARLY_PROMPT + "Objective:\n" + objective + goal_charter.checker_lines(record) + _time_note(record)
            + (machine + "\n" if machine else "")
            + (f"The agent's latest notes:\n{notes}\n" if notes else "") + goal._trace(service, thread_id, record))


def _add_requests(record, requests):
    charter = goal_charter.charter_of(record)
    number = max([int(item["id"][1:]) for item in charter["criteria"] if item["id"][1:].isdigit()] or [0])
    added = []
    for text in requests[:MAX_REQUESTS]:
        text = _clean(text, goal_charter.MAX_TEXT)
        if len(text) < goal_charter.MIN_CRITERION or any(item["text"] == text for item in charter["criteria"]):
            continue
        number += 1
        added.append({"id": f"C{number}", "text": text, "status": "open", "origin": "early review", "note": ""})
    charter["criteria"].extend(added)
    return added


def _advance_review(service, thread_id, record, objective):
    review = record.get("earlyReview")
    if review is None:
        if not _review_due(record):
            return False
        record["earlyReview"] = {"state": "running", "at": time.time(), "tries": 1,
                                 "agentId": _spawn(service, thread_id, record, _review_task(service, thread_id, record, objective), "goal-review")}
        decide(record, "early_review_started", claims=len(record.get("claims") or ()))
        return True
    if review.get("state") != "running":
        return False
    done, text = _finished(service, thread_id, review["agentId"])
    if not done:
        return False
    value = _json_with(text, "requests")
    if value is None and review["tries"] < MAX_REFEREE_TRIES and record.get("phase") == "working":
        review.update(tries=review["tries"] + 1,
                      agentId=_spawn(service, thread_id, record, _review_task(service, thread_id, record, objective), "goal-review"))
        return True
    if value is None:
        review.update(state="failed", agentId="")
        decide(record, "early_review_failed")
        return True
    reject = [_clean(item, 500) for item in value.get("reject") or () if _clean(item)][:8]
    requests = [_clean(item, 500) for item in value.get("requests") or () if _clean(item)][:MAX_REQUESTS]
    added = _add_requests(record, requests)
    summary = _clean(value.get("summary"), 1500)
    review.update(state="done", agentId="", summary=summary, reject=reject, requests=requests,
                  closest=[_clean(item, 300) for item in value.get("closest_work") or () if _clean(item)][:8],
                  added=[item["id"] for item in added], doneAt=time.time())
    # The final check is told what this review asked and checks each was answered (goal._previous_review).
    record["reviews"] = [*(record.get("reviews") or []), {
        "round": 0, "verdict": "early_review", "summary": summary, "major": reject, "minor": [],
        "deeper": requests, "at": time.time(), "unmet": []}][-12:]
    _tell(record, EARLY_BACK.format(summary=summary, reject="\n".join(f"- {item}" for item in reject) or "- none",
                                    ids=", ".join(item["id"] for item in added) or "none new"))
    decide(record, "early_review_done", reject=len(reject), added=len(added))
    return True


def advance(service, thread_id, record, objective):
    """One scheduler move for this goal's referees; returns whether the record changed."""
    if not ENABLED:
        return False
    moved = _advance_directions(service, thread_id, record, objective)
    return _advance_review(service, thread_id, record, objective) or moved


def stop(service, thread_id, record):
    """The goal stopped: its referees stop with it, and are started again when it goes on."""
    for agent_id, holder in (((record.get("directions") or {}).get("refereeId"), record.get("directions")),
                             ((record.get("earlyReview") or {}).get("agentId"), record.get("earlyReview"))):
        if not agent_id:
            continue
        try:
            service.runtime.cancel(agent_id)
            service.runtime.agent_control.close_agent(thread_id, agent_id)
        except (OSError, ValueError, RuntimeError):
            pass
        if holder is record.get("directions"):
            holder["refereeId"] = ""
        else:
            record.pop("earlyReview", None)  # due again when the goal resumes


# --- what the lead and the person see -------------------------------------------------------------------------------

def take_notices(service, thread_id):
    """The referees' findings not yet given to the lead, marked given."""
    record = service.weave_goals.get(thread_id)
    if not record or not record.get("depthNotices"):
        return ""
    with service.project_runner.guard:
        record = service.weave_goals.get(thread_id)
        notices = (record or {}).get("depthNotices") or []
        if not notices:
            return ""
        record["depthNotices"] = []
        service.weave_goals.put(thread_id, record)
    return "\n\n".join(item["text"] for item in notices)


def annotate(service, session, result):
    """A tool result of a goal's conversation: a referee's finding reaches a lead that is in the middle of a turn
    (a real lead's turn ran 87 minutes; the next turn would have been too late)."""
    if getattr(session, "weave", False) is not True:
        return result
    text = take_notices(service, session.session_id)
    if not text:
        return result
    return type(result)(result.ok, result.content + "\n\nRuntime note: " + text, result.data)


def context_lines(service, thread_id, record):
    """For each goal turn: the machine, the direction stage, and any referee finding not yet given."""
    lines = []
    machine = machine_line(record)
    if machine:
        lines.append(machine)
    if needed(record):
        state = _state(record)
        if not state:
            lines.append("Direction: not chosen yet. " + DIRECTIONS_HELD.split(": ", 1)[1])
        elif state == "declared":
            lines.append("Direction: candidates named, pilots not reported. " + PILOT.split(". ", 1)[1])
        elif state == "ranking":
            lines.append("Direction: an independent referee is ranking your piloted candidates.")
        elif state == "ranked":
            lines.append("Direction: ranked, not chosen. Choose with weave_directions(choose=..., reason=...).")
    elif _state(record) == "chosen":
        state = record["directions"]
        lines.append(f"Direction chosen: {state['candidates'][state['chosen'] - 1]['title']}.")
    # Called by the scheduler with the record it is about to write: taken from that record, or the write puts them back.
    pending = record.get("depthNotices") or []
    if pending:
        record["depthNotices"] = []
        lines.append("\n\n".join(item["text"] for item in pending))
    return lines


def checker_lines(record):
    """For the final check: how the direction was decided and what the mid-course review asked."""
    state = (record or {}).get("directions") or {}
    text = ""
    if state.get("state") == "chosen":
        chosen = state["candidates"][state["chosen"] - 1]
        top = ((state.get("ranking") or {}).get("ranking") or [None])[0]
        text += (f"\nDirection: the agent named {len(state['candidates'])} candidates, piloted them and chose "
                 f"\"{chosen['title']}\"" + (f" against the referee's first choice ({top}), because: {state.get('reason')}"
                                             if top and top != state["chosen"] else "") + ".\n")
    elif state.get("state") == "skipped":
        text += f"\nDirection: the agent chose no direction among candidates, because: {state.get('reason')}\n"
    elif ENABLED and _research(record):
        text += "\nDirection: no direction was compared and chosen in this goal.\n"
    return text


def view(record):
    state = (record or {}).get("directions") or {}
    review = (record or {}).get("earlyReview") or {}
    candidates = state.get("candidates") or []
    return {"directions": {
                "state": state.get("state", "") if _research(record) else "",
                "candidates": [item["title"] for item in candidates],
                "ranking": (state.get("ranking") or {}).get("ranking") or [],
                "chosen": state.get("chosen") or 0, "reason": state.get("reason", "")[:300]},
            "earlyReview": {"state": review.get("state", ""), "summary": review.get("summary", "")[:400],
                            "reject": len(review.get("reject") or ()), "added": review.get("added") or []}}
