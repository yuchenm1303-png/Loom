"""Lines: the independent parts of a long goal, run at the same time by sub-agents the Host owns.

Asking the lead to delegate did not work: a prompt leaves "no" as the silent default, and in a real two-hour
goal it never split anything. So the Host asks for a decision (lines, or why not) before the work starts, and
does the rest itself: starts the sub-agents, holds the lead while they run, retries what the provider failed,
stops what overruns, closes them, and hands the reports back.

The reason to split is cost as much as speed: a conversation's history is re-sent on every step, so what a goal
costs grows with the square of its steps in one conversation (docs/goal-lines-design-2026-11-07.md). The lead
does not run while lines run, and only their reports enter its history.

Whether two lines depend on each other only the lead can know, so the batch is its statement that they do not.
Lines that declare the same files or the same resource are refused, not queued: serial work stays with the lead,
or goes in a later round. Declared files are not a sandbox; what the Host saw changed is compared afterwards.

Splitting pays only on a goal one agent cannot finish soon: on goals a single agent finished in about an hour, a
forced split at the start cost 2 to 5 times the tokens for the same grade (docs/goal-lines-design-2026-11-07.md).
So the lead starts alone, and the Host asks for the decision only once the goal has cost it SPLIT_AFTER; by then
it also knows the work well enough to write good briefs. A goal that is deepened is asked again at once.

A line that writes gives a check: a command that proves its part works. The Host does not run it (that would act
outside the line's permissions); it reads the line's own runs, and a line that ends without its check passing is
sent back once. What the lead reads says whether each check passed.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

from app.agent_runtime.contracts import AgentEventKind, AgentStatus
from app.agent_runtime.tools import ToolResult

from .goal_common import CONTROL_TOOLS, decide, changes, charged, delay, span, time_left, transient

MODE_ENV = "LOOM_GOAL_LINES"
MODES = ("lines", "prompt", "off")
MAX_LINES = 4
MAX_HOLDS = 2
MIN_BRIEF = 80
CHECK_CHARS = 500
SPLIT_AFTER = 500_000  # weighted tokens the lead spends alone before the Host asks whether to split
BUDGET_SHARE = 0.6  # of what is left, for the lines together; the rest is for the lead and the checker
MIN_BUDGET_LEFT = 0.1  # below this share of the budget a round would only starve the lead and the checker
TIME_SHARE = 0.7  # of the person's time left; the rest is for integrating
UNTIMED_SECONDS = 4 * 3600
OVERRUN = 1.25  # past this much of its cap after being asked to report, a line is closed as it is
REPORT_GRACE = 600  # seconds a line has to report after being asked
MAX_LOST = 2  # restarts of the Host a line is carried through
REPORT_CHARS = 6000
OBJECTIVE_CHARS = 2000
LIVE = {"running", "waiting", "reporting"}
MAX_WAIT_HOLDS = 2
CPU_RESERVED = 2  # logical processors kept for the lead, the Host and the person's own use
FAILURE_KIND = "goal_lines_required"

GUIDE_LINES = (
    "Lines: you are the lead of this goal. Start the work yourself. A goal you can finish in an hour or so is "
    "cheaper done alone: splitting it measured 2 to 5 times the tokens for the same result. When the goal is large "
    "(the Host asks once it has cost you a while), run what is independent at the same time with weave_lines: up "
    "to four lines that do not depend on each other's results, each worth many steps of its own (a survey, an "
    "experiment, a module, a data set). A line sees none of this conversation, so its brief says the objective, "
    "the files to read and where to write, what to hand back, and its boundaries; a line that writes gives check, "
    "the argument list of a command that proves its part works (its tests, or a script that checks its results), "
    "and a line of heavy computation gives cpus. After weave_lines you "
    "end the turn: no waiting, no checking on them, no work of your own meanwhile, and that time costs you "
    "nothing. Work that feeds each other in order, and runs that compete for one GPU or one set of files, go "
    "before the lines or in a later round; integrating and the final result are yours. Do not read what a line "
    "produced except what your answer relies on. When nothing is worth splitting, say why with solo_reason.")
GUIDE_PROMPT = (
    "Sub-agents: in a long goal you are the lead, and delegating is the default. Split what is left into lines "
    "that do not depend on each other, and give each line worth many steps of its own (a survey, an experiment, "
    "building a data set, a review of a finished part) to a sub-agent with spawn_agent, up to four at once, "
    "history_mode none and a self-contained brief: what to do, where, what to report. Your history is re-sent on "
    "every step for the rest of the goal, so reading handed to a sub-agent is paid once instead of all night. "
    "Meanwhile you integrate, decide and keep the list; collect reports with wait_agent (a timeout lets you "
    "keep working), then close_agent. Keep for yourself only steps that feed each other in order and runs that "
    "compete for one GPU or one set of files.")
GUIDE_OFF = "Sub-agents: do this goal yourself, in this conversation; do not use spawn_agent."

LINE_PROMPT = (
    "You are one line of a long goal; other lines are working at the same time on other parts, and a lead will "
    "integrate your report. You see none of the lead's conversation.\n"
    "The whole goal, for context only:\n{objective}\n\n"
    "Your line: {title}\n{brief}\n\n"
    "{writes}\n{check}"
    "Limits: about {tokens} tokens and {time}. Do this line yourself, without sub-agents. Run what you make and "
    "look at the result; report what you saw, not what you expect.\n"
    "Put details and data in files. End with a report of at most 300 words: what you did, the files, the key "
    "results with their numbers, and anything not done, not verified or surprising.")
WRITES_ONLY = "Write only under: {paths}. Anything else in this folder belongs to someone else: read it if you need to, do not change it."
WRITES_NONE = "This line only reads: do not change anything in this folder."
CHECK_LINE = ("Done when this command passes. Run it exactly as written, as your last step before the report, and "
              "fix your part until it does:\n{check}\n")
CHECK_AGAIN = ("Not done yet: {what}. Run exactly this, fix your part until it passes, then give your report again:\n"
               "{check}")
HELD_LATER = ("Not executed: this long goal has grown large, and the Host asks once whether to split what is left. "
              "Call weave_lines now with the parts that can run at the same time, or with solo_reason if nothing is "
              "worth splitting. Reading files first is fine if you need to before deciding.")
REPORT_NOW = ("Your limit for this line is reached. Do not start anything new: give your report now from what you "
              "have, and say what you did not get to.")
RESUME_LINE = ("The previous attempt at this line stopped because of the model provider or a restart, not because of "
               "your work. Carry on from where it stopped; do not redo what is done.")
WAIT = ("Not executed: your lines are running. Every step you take now re-sends your whole history, and in a real run "
        "a lead that stayed and checked on its lines cost 20 times one that stopped. End this turn now with a sentence, and "
        "no tool call: the Host brings you back with their reports and you spend nothing until then.")
HELD = ("Not executed: this is a long goal and you have not decided how to split it. Call weave_lines now, with "
        "the lines that can run at the same time, or with solo_reason if nothing is worth splitting. Reading files "
        "first is fine if you need to before deciding.")


def mode():
    value = os.environ.get(MODE_ENV, "").strip().lower()
    return value if value in MODES else "lines"


def guide():
    return {"lines": GUIDE_LINES, "prompt": GUIDE_PROMPT, "off": GUIDE_OFF}[mode()]


def live(record):
    return [line for line in record.get("lines") or () if line["state"] in LIVE]


def _normal(path):
    return str(path).replace("\\", "/").strip().strip("/").lower().removeprefix("./")


def _overlap(left, right):
    a, b = _normal(left), _normal(right)
    return a == b or not a or not b or a.startswith(b + "/") or b.startswith(a + "/")


def _validated(raw, workspace=None):
    """The batch as the Host will run it; raises with what the lead has to change."""
    if len(raw) > MAX_LINES:
        raise ValueError(f"At most {MAX_LINES} lines at once; merge the smallest ones, or keep some for a later round.")
    lines = []
    for item in raw:
        title, brief = str(item.get("title") or "").strip(), str(item.get("brief") or "").strip()
        if not title or len(brief) < MIN_BRIEF:
            raise ValueError("Each line needs a title and a self-contained brief: it sees none of this conversation, "
                             "so say the objective, the files to read and where to write, what to hand back, and its "
                             "boundaries.")
        writes = [str(path).strip() for path in item.get("writes") or () if str(path).strip()]
        argv = _check_argv(title, item.get("check"), workspace)
        if writes and not argv:
            raise ValueError(f"\"{title}\" writes files but has no check: give a command that proves its part works, "
                             "such as its tests or a script that checks its results.")
        resource = str(item.get("resource") or "").strip().lower()
        cpus = item.get("cpus")
        if cpus is not None and (type(cpus) is not int or cpus < 1):
            raise ValueError(f"cpus of \"{title}\" is the number of logical processors it keeps busy, a whole number.")
        if resource == "cpu":  # an older way of saying heavy computation, with no size: ask for the size
            if cpus is None:
                raise ValueError(f"\"{title}\" needs the CPU: give cpus, how many logical processors it keeps busy "
                                 "(its threads or worker processes), instead of resource.")
            resource = ""
        lines.append({"title": title[:120], "brief": brief, "writes": writes, "check": _display(argv),
                      "checkArgv": argv, "resource": resource, "cpus": cpus or 0})
    for at, left in enumerate(lines):
        for right in lines[at + 1:]:
            shared = [a for a in left["writes"] for b in right["writes"] if _overlap(a, b)]
            if shared:
                raise ValueError(f"\"{left['title']}\" and \"{right['title']}\" both write {shared[0]}. Lines run at the "
                                 "same time in one folder: give them separate paths, or do that part yourself.")
            if left["resource"] and left["resource"] == right["resource"]:
                raise ValueError(f"\"{left['title']}\" and \"{right['title']}\" both need {left['resource']}. Only one "
                                 "can use it at a time: start one now and the other in a later round, or run them yourself.")
    # Each line says what it uses (Ray's num_cpus, Slurm's cpus-per-task); the Host packs them into the machine.
    # A fixed "one CPU-bound line at a time" refused a real goal two 8-thread trainings on 20 logical processors.
    room = max(1, (os.cpu_count() or 1) - CPU_RESERVED)
    wanted = sum(line["cpus"] for line in lines)
    if wanted > room:
        raise ValueError(f"These lines want {wanted} logical processors and this machine has {room} to give "
                         f"({os.cpu_count() or 1} in all, {CPU_RESERVED} kept for the lead and the Host). Give fewer "
                         "threads to some, or start the rest in a later round.")
    return lines


def _check_argv(title, check, workspace):
    """A line's check as the argument list it must run, its program one that exists here.

    It was a string, and a real lead wrote a description ("运行 `python experiments/analyze.py` 退出码 0，且断言 ...")
    that the Host, which finds the check among the line's own runs, recorded as never run for both lines. An
    argument list whose first item is a program on this computer cannot be a description; a string is split as a
    command line, for leads that still send one."""
    if not check:
        return []
    if isinstance(check, str):
        try:
            argv = shlex.split(check, posix=os.name != "nt")
        except ValueError:
            argv = check.split()
        argv = [part[1:-1] if len(part) > 1 and part[0] == part[-1] == '"' else part for part in argv]
    elif isinstance(check, (list, tuple)) and all(isinstance(part, str) for part in check):
        argv = [part for part in check if part != ""]
    else:
        raise ValueError(f"The check of \"{title}\" is a list of arguments, such as [\"python\", \"check.py\"].")
    if len(" ".join(argv)) > CHECK_CHARS:
        raise ValueError(f"The check of \"{title}\" is too long: give one command, at most {CHECK_CHARS} characters.")
    if argv and not _program(argv[0], workspace):
        raise ValueError(f"The check of \"{title}\" starts with {argv[0]!r}, which is not a program on this computer "
                         "(not on PATH, not a file in the workspace). Give the exact command the line will run, as a "
                         "list of arguments starting with the program (the full path of the interpreter when it is not "
                         "on PATH), such as [\"python\", \"scripts/check_results.py\"]; put what it asserts inside that script.")
    return argv


def _program(name, workspace):
    if shutil.which(name):
        return True
    path = Path(name)
    if not path.is_absolute() and workspace:
        path = Path(workspace) / path
    return path.is_file()


def _display(argv):
    """The check as one command line the line can type exactly (paths with spaces quoted)."""
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def start(service, thread_id, arguments):
    """The lead's decision: start a round of lines, or record why there is none. Called from its turn."""
    with service.project_runner.guard:  # the scheduler writes the same record
        record = service.weave_goals.get(thread_id)
        if record is None or record.get("phase") == "done":
            raise ValueError("weave_lines works inside a running long goal; start one with weave_goal first.")
        if live(record):
            raise ValueError("A round of lines is still running. End this turn; you come back with their reports.")
        if record.get("phase") == "checking":
            raise ValueError("An independent check is running; lines would change what it is reading.")
        raw, reason = arguments.get("lines") or [], str(arguments.get("solo_reason") or "").strip()
        if not raw:
            if not reason:
                raise ValueError("Give lines, or solo_reason saying why nothing here is worth splitting.")
            record["linesDecision"] = "solo"
            record.setdefault("soloReasons", []).append(reason[:300])
            service.weave_goals.put(thread_id, record)
            return ToolResult(ok=True, content="Noted: you work on this yourself. You can still call weave_lines later.", data={"lines": 0})
        lines = _validated(raw, getattr(service.store.load(thread_id), "workspace_dir", None))
        left = record["tokenBudget"] - record["tokens"]
        if left < MIN_BUDGET_LEFT * record["tokenBudget"]:
            raise ValueError("Too little of the budget is left to start lines; finish this yourself.")
        cap = int(left * BUDGET_SHARE / len(lines))
        now = time.time()
        seconds = time_left(record) * TIME_SHARE if record.get("hours") else UNTIMED_SECONDS
        if seconds < 60:
            raise ValueError("Too little of the person's time is left to start lines; finish and report.")
        objective = service.runtime.get_goal(thread_id).objective[:OBJECTIVE_CHARS]
        control = service.runtime.agent_control
        record["lines"] = []
        stats = record.setdefault("lineStats", {"rounds": 0, "started": 0, "done": 0, "failed": 0, "tokens": 0})
        stats["rounds"] += 1
        record.update(linesDecision="lines", linesTurn=service.store.load(thread_id).current_turn_id, linesWaitHolds=0)
        for line in lines:
            writes = WRITES_ONLY.format(paths=", ".join(line["writes"])) if line["writes"] else WRITES_NONE
            check = CHECK_LINE.format(check=line["check"]) if line["check"] else ""
            prompt = LINE_PROMPT.format(objective=objective, title=line["title"], brief=line["brief"], writes=writes,
                                        check=check, tokens=f"{cap:,}", time=span(seconds))
            line.update(state="running", agentId="", prompt=prompt, cap=cap, startedAt=now, deadline=now + seconds,
                        attempts=0, lost=0, retryAt=0, pressedAt=0, tokens=0, report="", why="", changed=[],
                        checkResult="", sentBack=False)
            try:
                line["agentId"] = control.spawn(thread_id, prompt, role="goal-line", history_mode="none", background=True).node.session_id
                stats["started"] += 1
            except (OSError, ValueError, RuntimeError) as exc:
                line.update(state="failed", why=f"could not start: {exc}")
                stats["failed"] += 1
            record["lines"].append(line)
        if not live(record):
            record["reports"] = _digest(record["lines"])
        service.weave_goals.put(thread_id, record)
        titles = "\n".join(f"- {line['title']}: {'started' if line['agentId'] else line['why']}" for line in record["lines"])
    return ToolResult(ok=True, content=f"Lines started (about {cap:,} tokens and {span(seconds)} each):\n{titles}\n"
                      "End this turn now, in one sentence and with no tool call: you are brought back with their "
                      "reports, and spend nothing until then.",
                      data={"lines": len(record["lines"]), "cap": cap})


def _hold_while_running(service, session, calls):
    """The turn that started a round stops there. A prompt asking for that was not enough: one lead did, and spent 0.03M
    while its lines ran; another stayed, polled and re-checked, and spent 0.64M. A turn the person started is theirs."""
    waiting = tuple(call for call in calls if not (call.name in CONTROL_TOOLS or call.name == "weave_status"))
    if not waiting:
        return None
    with service.project_runner.guard:
        record = service.weave_goals.get(session.session_id)
        record["linesWaitHolds"] = record.get("linesWaitHolds", 0) + 1
        service.weave_goals.put(session.session_id, record)
        if record["linesWaitHolds"] > MAX_WAIT_HOLDS:  # never at the cost of the turn: five refused batches would fail it
            return None
    refusal = ToolResult(False, WAIT, {"failure_kind": FAILURE_KIND, "execution_status": "not_executed", "retry_policy": "end_the_turn"})
    return tuple((call, refusal) for call in waiting)


def hold(service, session, step, calls):
    """Before a batch runs: the refusal for each of its calls, or None when it may run."""
    if getattr(session, "weave", False) is not True or mode() != "lines":
        return None
    record = service.weave_goals.get(session.session_id)
    if record is None:
        return None
    if live(record) and record.get("linesTurn") == session.current_turn_id:
        return _hold_while_running(service, session, calls)
    if record.get("linesDecision") == "later":
        if charged(session.usage) - record.get("baseCharged", 0) < SPLIT_AFTER:
            return None
        with service.project_runner.guard:
            record = service.weave_goals.get(session.session_id)
            if record is None or record.get("linesDecision") != "later":
                return None
            record.update(linesDecision="pending", linesHolds=0, linesGrown=True)
            decide(record, "split_asked", after=SPLIT_AFTER)
            service.weave_goals.put(session.session_id, record)
    if record.get("linesDecision") != "pending":
        return None

    waiting = tuple(call for call in calls if changes(step, call))
    if not waiting:
        return None
    with service.project_runner.guard:
        record = service.weave_goals.get(session.session_id)
        if record is None or record.get("linesDecision") != "pending":
            return None
        record["linesHolds"] = record.get("linesHolds", 0) + 1
        if record["linesHolds"] > MAX_HOLDS:  # it asks for a decision, it guards nothing: never at the cost of the turn
            record["linesDecision"] = "gave_way"
            service.weave_goals.put(session.session_id, record)
            return None
        service.weave_goals.put(session.session_id, record)
    refusal = ToolResult(False, HELD_LATER if record.get("linesGrown") else HELD,
                         {"failure_kind": FAILURE_KIND, "execution_status": "not_executed",
                                       "retry_policy": "decide_with_weave_lines"})
    return tuple((call, refusal) for call in waiting)


def is_line(service, session_id):
    """Whether this session is a line: its limits only hold if it has no sub-agents of its own."""
    graph = getattr(service.runtime, "agent_graph", None)
    if graph is None:
        return False
    node = graph.get(session_id)
    return node is not None and node.role == "goal-line"


def _failure(service, session_id):
    session = service.store.load(session_id)
    for event in reversed(service.store.turn_events(session_id, session.current_turn_id) if session.current_turn_id else ()):
        if event.kind is AgentEventKind.TURN_FAILED:
            return event.data
    return {}


_CHECK_WORDS = {"failed": "your check did not pass the last time you ran it",
                "not_run": "your check was not run"}


def _command(data):
    argv = data.get("argv")
    if isinstance(argv, (list, tuple)):
        return " ".join(str(part) for part in argv)
    return str(data.get("command") or data.get("cmd") or "")


def _program_name(value):
    name = Path(str(value)).name.lower()
    return name[:-4] if name.endswith(".exe") else name


def _ran(check, data):
    """Whether a run is the check: the same program (wherever it lives: a line put the runtime's Python first when
    `python` was not on PATH) with the same arguments, or the check inside a wrapper such as cmd /c or bash -lc."""
    run = data.get("argv")
    if isinstance(run, (list, tuple)) and run and _program_name(run[0]) == _program_name(check[0]) and \
            [str(part) for part in run[1:]] == check[1:]:
        return True
    text = " ".join(_command(data).split()).lower()
    rest = " ".join(" ".join(check[1:]).split()).lower()
    return _program_name(check[0]) in text and (not rest or rest in text)


def _check_result(service, session_id, check):
    """"passed", "failed" or "not_run", from the last run of the check in the line's own commands."""
    if isinstance(check, str):
        check = check.split()
    if not check:
        return "not_run"
    result = "not_run"
    for event in service.store.events(session_id):
        if event.kind not in {AgentEventKind.TOOL_COMPLETED, AgentEventKind.TOOL_FAILED}:
            continue
        data = event.data.get("data") if isinstance(event.data.get("data"), dict) else {}
        if not _ran(check, data):
            continue
        code = data.get("returncode")
        if data.get("running") or code is None:
            continue
        result = "passed" if code == 0 and event.kind is AgentEventKind.TOOL_COMPLETED else "failed"
    return result


def _changed(service, session_id):
    paths = set()
    for event in service.store.events(session_id):
        if event.kind is AgentEventKind.TURN_DIFF_UPDATED:
            paths.update(str(path) for path in event.data.get("paths") or ())
    return sorted(paths)


def _settle(service, thread_id, record, line, state, why=""):
    """A line is over: take what it reported and close it, so nothing it started keeps running."""
    control = service.runtime.agent_control
    try:
        snapshot = control.snapshot(thread_id, line["agentId"])
        line["report"] = str(snapshot.final_text or "")
        line["tokens"] = int(charged(service.store.load(line["agentId"]).usage))
        line["changed"] = _changed(service, line["agentId"])
        if line.get("check"):
            line["checkResult"] = _check_result(service, line["agentId"], line.get("checkArgv") or line["check"])
        if not snapshot.node.relation_status.value == "closed":
            control.close_agent(thread_id, line["agentId"])
    except (OSError, ValueError, RuntimeError) as exc:
        why = why or f"{type(exc).__name__}: {exc}"
    line.update(state=state, why=why[:500], prompt="", brief="")
    stats = record.setdefault("lineStats", {"rounds": 0, "started": 0, "done": 0, "failed": 0, "tokens": 0})
    stats["done" if state == "done" else "failed"] += 1
    stats["tokens"] += line["tokens"]


def _press(service, thread_id, line):
    """Over its limit and still working: stop the turn; the next tick asks for the report."""
    line.update(state="reporting", pressedAt=time.time())
    service.runtime.cancel(line["agentId"])


def _tick_line(service, thread_id, record, line, now):
    """One move of one line; returns whether its record changed."""
    control = service.runtime.agent_control
    snapshot = control.snapshot(thread_id, line["agentId"])
    used = int(charged(service.store.load(line["agentId"]).usage))
    changed = used != line["tokens"]
    line["tokens"] = used
    over = used >= line["cap"] or now >= line["deadline"]
    if snapshot.execution_running:
        if line["state"] == "running" and over:
            _press(service, thread_id, line)
            return True
        if line["state"] == "reporting" and (used >= line["cap"] * OVERRUN or now - line["pressedAt"] > REPORT_GRACE):
            _settle(service, thread_id, record, line, "failed", "it was stopped at its limit before it could report")
            return True
        return changed
    status = snapshot.session_status
    if line["state"] == "waiting":
        if now >= line["deadline"]:
            _settle(service, thread_id, record, line, "failed", "the model provider stayed unavailable until the line's time was up")
            return True
        if now < line["retryAt"]:
            return changed
        line["state"] = "running"
        control.send(thread_id, line["agentId"], RESUME_LINE)
        return True
    if status is AgentStatus.COMPLETED:
        if line.get("check"):
            line["checkResult"] = _check_result(service, line["agentId"], line.get("checkArgv") or line["check"])
            if line["checkResult"] != "passed" and not line.get("sentBack") and line["state"] == "running" and not over:
                line["sentBack"] = True
                control.send(thread_id, line["agentId"], CHECK_AGAIN.format(what=_CHECK_WORDS[line["checkResult"]],
                                                                            check=line["check"]))
                return True
        _settle(service, thread_id, record, line, "done" if snapshot.final_text else "failed",
                "" if snapshot.final_text else "it finished without a report")
        return True
    if status is AgentStatus.WAITING_APPROVAL:
        _settle(service, thread_id, record, line, "failed", "it stopped at a command that needs approval; no one sees a line's approvals")
        return True
    if status is AgentStatus.FAILED:
        if transient(_failure(service, line["agentId"])) and now < line["deadline"]:
            line["attempts"] += 1
            line.update(state="waiting", retryAt=now + delay(line["attempts"]))
            return True
        _settle(service, thread_id, record, line, "failed", str(snapshot.error or "the line failed"))
        return True
    if status in {AgentStatus.LIMIT_REACHED, AgentStatus.CANCELLED}:
        if line["state"] == "reporting" and line.get("asked"):
            _settle(service, thread_id, record, line, "failed", "it was stopped at its limit before it could report")
            return True
        # A step limit, or the stop just above: what it has seen is worth more than a new line starting over.
        line.update(state="reporting", asked=True, pressedAt=line["pressedAt"] or now)
        control.send(thread_id, line["agentId"], REPORT_NOW)
        return True
    # Running on disk with nothing running it, or never begun: the Host was restarted.
    line["lost"] += 1
    if line["lost"] > MAX_LOST:
        _settle(service, thread_id, record, line, "failed", "the Host restarted while it ran, more than once")
        return True
    service.runtime.recover_interrupted(line["agentId"])
    began = bool(service.store.load(line["agentId"]).messages)
    control.send(thread_id, line["agentId"], RESUME_LINE if began else line["prompt"])
    return True


def _digest(lines):
    """The reports as the lead reads them: its history keeps every word for the rest of the goal."""
    parts = ["Line reports. These are what the lines said, not yet checked; integrate them and check only what your "
             "answer relies on. A claim of completion made before now was not checked: claim again when the goal is met."]
    owner = {}
    for line in lines:
        text = line["report"].strip()
        if len(text) > REPORT_CHARS:
            text = text[:REPORT_CHARS] + "\n(cut: the rest of this report is not shown; details are in the line's files)"
        head = f"## {line['title']}" + ("" if line["state"] == "done" else f" (did not finish: {line['why']})")
        parts += [head, text or "(no report)"]
        if line.get("check"):
            said = {"passed": "passed in the line's own last run",
                    "failed": "did NOT pass in the line's last run; its part is not done",
                    }.get(line.get("checkResult"), "was NOT run by the line; nothing proves its part works")
            parts.append(f"Check `{line['check']}`: {said}.")
        outside = [path for path in line["changed"] if line["writes"] and not any(_overlap(path, own) for own in line["writes"])]
        if not line["writes"] and line["changed"]:
            outside = line["changed"]
        if line["changed"]:
            parts.append("Files the Host saw it change: " + ", ".join(line["changed"][:30]))
        if outside:
            parts.append("WARNING: outside what it declared: " + ", ".join(outside[:30]))
        for path in line["changed"]:
            if path in owner and owner[path] != line["title"]:
                parts.append(f"WARNING: {path} was changed by both \"{owner[path]}\" and \"{line['title']}\"; look at it.")
            owner[path] = line["title"]
    return "\n".join(parts)


def advance(service, thread_id, record):
    """One scheduler tick for a round of lines. Returns whether the record changed.

    When the last line is over, the reports wait in the record for the lead's next turn.
    """
    running = live(record)
    if not running:
        return False
    now, moved = time.time(), False
    for line in running:
        try:
            moved = _tick_line(service, thread_id, record, line, now) or moved
        except (OSError, ValueError, RuntimeError) as exc:
            # A line must not hold the goal: the lead hears of it and decides.
            _settle(service, thread_id, record, line, "failed", f"{type(exc).__name__}: {exc}")
            moved = True
    if moved and not live(record):
        record["reports"] = _digest(record["lines"])
    return moved


def stop_all(service, thread_id, record, reason, keep=""):
    """The goal stopped or finished: nothing it opened goes on spending. Lines keep what they had said."""
    control = getattr(service.runtime, "agent_control", None)
    if control is None:
        return
    running = live(record)
    for line in running:
        _settle(service, thread_id, record, line, "failed", reason)
    if running:
        record["reports"] = _digest(record["lines"])
    for snapshot in control.list_tree(thread_id, include_closed=False):
        if snapshot.node.relation_status.value != "active" or snapshot.node.session_id == keep:
            continue
        try:
            control.close_agent(thread_id, snapshot.node.session_id)
        except (OSError, ValueError, RuntimeError):
            pass


def view(record):
    return [{"title": line["title"], "state": line["state"], "tokens": line.get("tokens", 0), "cap": line.get("cap", 0),
             "retryAt": line.get("retryAt", 0) if line["state"] == "waiting" else 0, "why": line.get("why", ""),
             "check": line.get("checkResult", "") if line.get("check") else "",
             "changed": line.get("changed", [])[:200], "report": line.get("report", "")[:4000],
             "startedAt": line.get("startedAt"), "writes": line.get("writes", [])}
            for line in record.get("lines") or ()]
