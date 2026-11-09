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

The lead writes its own charter, so a charter can ask for little, and a checker that judges the charter's criteria
then passes little. A real research goal chartered "new report sections", ran one seed with no literature search,
and its checker judged exactly those criteria. So the Host adds the standard criteria of the kind of work (origin
"standard"): the bar every deliverable of that kind is held to, which the lead can neither leave out nor drop
(Anthropic's long-running harness: the agent may only mark the fixed list passing, never edit it). A standard
criterion that does not apply passes when the deliverable says why and the checker agrees.
"""
from __future__ import annotations

import re
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
    "in a note. Change the charter only with a reason; never lower a criterion to make it pass. The Host adds the "
    "standard criteria of the kind of work you name (for research: related work and novelty, fair comparison, "
    "uncertainty, reproducibility, negative results checked against bugs, limitations); write your own criteria for "
    "what is specific to this request.\n"
    "Notes: end each goal turn with weave_note: what you did, what you found (with the numbers), what you decided "
    "and why, what comes next, and the phase now under way. Every goal turn shows the latest notes, so they carry "
    "the work when your history is compacted. Answer each point of a review in a note before you claim again.\n"
    "Claims: for research and analysis, record each headline number with weave_claims as you produce it (the claim, "
    "the exact command that reproduces it, the output file); the check re-runs them.")


# The bar of each kind of work, in the person's language. Research follows what audits of AI scientist systems found
# missing (Beel et al. 2025: keyword literature search, false novelty; Luo, Kasirzadeh & Shah 2025: unfair
# benchmarks, undisclosed data changes, metric switching, selection on test results) and what a referee asks for.
STANDARD = {
    "research": (
        ("Related work and novelty: the deliverable cites the closest prior work from sources the checker can open "
         "(recent work included) and says what is new or how it relates; novelty is never claimed without a "
         "literature search.",
         "相关工作与新意：引用审稿人能打开的最相关已有工作（含近年工作），说明本工作新在哪里或与已有工作的关系；没做文献检索不得声称新意。"),
        ("Fair comparison: results are compared with suitable baselines under the same data, budget and metric, "
         "with ablations showing what each component contributes; no data, metric or benchmark change goes "
         "undisclosed.",
         "公平对比：在相同数据、预算和指标下与合适的基线比较，并用消融说明每个组件的作用；任何数据、指标或基准的改动都要写明。"),
        ("Uncertainty: headline numbers come with several random seeds or confidence intervals, and a difference "
         "inside the noise is not reported as a finding.",
         "不确定度：主要数字给出多个随机种子或置信区间；落在噪声范围内的差异不写成结论。"),
        ("Reproducibility: random seeds are fixed, compared methods are evaluated on the same samples, and every "
         "reported number comes from a recorded command (listed with weave_claims) that reproduces it.",
         "可复现：固定随机种子，被比较的方法在相同样本上评估，报告中的每个数字都来自一条记录在案、能复现它的命令（用 weave_claims 登记）。"),
        ("Negative results: every negative or surprising result comes with checks that rule out an implementation "
         "fault (sanity checks, a known case reproduced, gradients or units checked); otherwise it is stated as "
         "inconclusive, not as a finding.",
         "负面结果：每个负面或意外结果都附有排除实现错误的检查（合理性检查、复现已知情形、检查梯度或单元）；否则只能写成未定论，不能写成发现。"),
        ("Limitations: the deliverable states its limits, what was not verified, and where its conclusions apply.",
         "局限：写明局限、未验证的部分，以及结论的适用范围。"),
    ),
    "software": (
        ("Tests: the new or changed behaviour has automated tests, and the whole suite passes in this workspace.",
         "测试：新增或修改的行为有自动化测试，并且整套测试在此工作区跑通。"),
        ("Real use: the main flow was run end to end the way its user would, and the evidence is kept.",
         "真实运行：主流程已按使用者的方式端到端跑过一次，并留有证据。"),
        ("Documentation: how to install, run and verify it is written down.",
         "文档：写明如何安装、运行和验证。"),
    ),
    "analysis": (
        ("Provenance: every conclusion points to the data and the recorded command that produced it (listed with "
         "weave_claims).",
         "溯源：每个结论都指向产生它的数据和记录在案的命令（用 weave_claims 登记）。"),
        ("Limits: the evidence's limits and uncertainty are stated next to the conclusions they affect.",
         "局限：在受影响的结论旁写明证据的局限与不确定度。"),
    ),
}
INHERITED = ("Inherited results: results this goal builds on from the previous goal, which ended without an independent "
             "check, are re-verified (re-run or re-derived) before they are used, and any that fail are corrected.",
             "继承的结果：本目标所依赖的上一个目标的结果未经独立核验，使用前要先复核（重跑或重新推导），不成立的要更正。")
LEDGER_KINDS = ("research", "analysis")  # kinds whose claims must each name the command behind them


def _chinese(text):
    return bool(re.search(r"[一-鿿]", text or ""))


def standard_texts(kind, deliverable, inherited=False):
    """The Host's criteria for a charter of this kind, in the deliverable's language."""
    pick = 1 if _chinese(deliverable) else 0
    items = [pair[pick] for pair in STANDARD.get(kind, ())]
    if inherited:
        items.append(INHERITED[pick])
    return items


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
    earlier = (previous or {}).get("criteria") or ()
    standard = [item for item in earlier if item.get("origin") == "standard"]  # the Host's: never the lead's to drop
    old = {item["text"]: item for item in earlier if item.get("origin") != "standard"}
    number = max([int(item["id"][1:]) for item in earlier if item["id"][1:].isdigit()] or [0])
    criteria = []
    for text in raw:
        if text in old:
            kept = old[text]
            criteria.append({**kept, "status": "open", "note": ""} if kept.get("status") == "dropped" else kept)
        else:
            number += 1
            criteria.append({"id": f"C{number}", "text": text, "status": "open", "origin": "charter", "note": ""})
    dropped = [{**item, "status": "dropped", "note": reason}
               for text, item in old.items() if text not in raw and item.get("status") != "dropped"]
    kept_dropped = [item for item in old.values() if item.get("status") == "dropped" and item["text"] not in raw]
    had = {item["text"] for item in standard} | set(raw)
    for text in standard_texts(kind, deliverable, inherited=_inherits_unchecked(record)):
        if text not in had:
            number += 1
            standard.append({"id": f"C{number}", "text": text, "status": "open", "origin": "standard", "note": ""})
    criteria += standard
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


def _inherits_unchecked(record):
    """This goal follows one in the same conversation that did work and was never independently checked."""
    previous = (record or {}).get("previous") or {}
    return bool(previous) and not previous.get("checked") and bool(previous.get("worked"))


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


MAX_CLAIMS = 40


def set_claims(record, claims):
    """The claims ledger: each headline claim with the command that produced it and the file it is in. Kosmos ties
    every statement of a report to the analysis behind it; an audit of AI scientist systems found reading the paper
    alone catches about half the flaws that the code and logs reveal."""
    if not isinstance(claims, list) or not claims:
        raise ValueError("Give the headline claims, each with the command that produces it and where its output is.")
    kept = []
    for item in claims[:MAX_CLAIMS]:
        if not isinstance(item, dict):
            raise ValueError("Each claim is an object with claim, command and output.")
        claim, command, output = (_clean(item.get(key), 600) for key in ("claim", "command", "output"))
        if len(claim) < 8 or not command or not output:
            raise ValueError("Each claim needs the claim itself, the exact command that reproduces it, and the output "
                             "file or place where the number appears.")
        kept.append({"claim": claim, "command": command, "output": output})
    record["claims"] = kept
    return kept


def finish_phases(record):
    """A claim of completion is the last phase of the plan."""
    charter = charter_of(record)
    if charter:
        charter["current"] = len(charter["phases"]) - 1


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
        if charter["kind"] in LEDGER_KINDS:
            count = len(record.get("claims") or ())
            lines.append(f"Claims ledger: {count} claim(s) recorded. " if count else
                         "Claims ledger: empty. Record each headline number with weave_claims (claim, command, output) "
                         "as you produce it; a claim of completion needs it.")
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
    criteria = "\n".join(f"- {item['id']}{' (standard)' if item.get('origin') == 'standard' else ''}: {item['text']}"
                         for item in charter["criteria"] if item["status"] != "dropped")
    dropped = [item for item in charter["criteria"] if item["status"] == "dropped"]
    gone = ("\nCriteria the agent dropped, with its reason (judge whether the deliverable still holds without them):\n"
            + "\n".join(f"- {item['text']}: {item.get('note') or 'no reason'}" for item in dropped)) if dropped else ""
    standard = ("\nThe criteria marked (standard) are the bar for this kind of work, set by the Host, not by the agent. "
                "One that does not apply is met only when the deliverable says why and you agree.") if any(
                    item.get("origin") == "standard" for item in charter["criteria"]) else ""
    claims = record.get("claims") or []
    ledger = ("\nClaims ledger the agent recorded (claim | command | output). Re-run the commands behind the headline "
              "claims yourself (at least one, from a clean state where you can) and compare with what is reported:\n"
              + "\n".join(f"- {item['claim']} | {item['command']} | {item['output']}" for item in claims[:MAX_CLAIMS])
              ) if claims else ""
    return (f"\nDeliverable the agent committed to:\n{charter['deliverable']}\nAcceptance criteria:\n{criteria}{gone}"
            f"{standard}{ledger}\n")


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
            "reviews": reviews, "notes": len(record.get("notes") or ()), "claims": len(record.get("claims") or ())}
