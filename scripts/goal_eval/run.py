"""Run one long-goal evaluation with a real model: one task, one way of running it, in a new isolated root.

    python scripts/goal_eval/run.py <root> <task> <lines|prompt|off> --stored-model <id> [--provider opencode-go] [--hours-cap 2] [--budget 8000000]
    python scripts/goal_eval/run.py <root> --regrade
    python scripts/goal_eval/run.py --table <root> [<root> ...]

The Host's own scheduler drives the goal, as in the app. Writes <root>/result.json: the grade, tokens by session and
what the lead decided. Consumes model quota. Never prints or stores a secret.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time

LOOM = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(LOOM), str(LOOM / "scripts"), str(Path(__file__).resolve().parent)]

from tasks import TASKS  # noqa: E402


def ledger(service, thread):
    from app.project_runner.goal_common import charged
    rows = [("lead", service.store.load(thread).usage)]
    for snapshot in service.runtime.agent_control.list_tree(thread):
        rows.append((snapshot.node.role, service.store.load(snapshot.node.session_id).usage))
    return [{"role": role, "weighted": int(charged(usage)), "raw": usage.total_tokens} for role, usage in rows]


def grade(task, workspace):
    try:
        cases = TASKS[task]["grade"](workspace)
    except Exception as exc:  # noqa: BLE001 - a grader's crash is a result to look at, not a lost run
        cases = [{"case": "grader", "passed": False, "why": f"{type(exc).__name__}: {exc}"}]
    return {"passed": sum(1 for case in cases if case["passed"]), "of": len(cases), "cases": cases}


def run(args):
    root = args.root.resolve()
    root.mkdir(parents=True)
    workspace = root / "workspace"
    workspace.mkdir()
    os.environ["LOOM_GOAL_LINES"] = args.mode
    task = TASKS[args.task]
    if task["prepare"]:
        task["prepare"](workspace)

    from app.agent_runtime.contracts import AgentLimits
    from app.project_runner import goal
    from weave_live import build_service
    service, runtime, _ = build_service(root, args.stored_model, args.provider)
    runtime.limits = AgentLimits()  # the Host's own: no step limit per turn
    service.weave_budgets = {}
    thread = service.thread_start({"workspaceDir": str(workspace), "permissionMode": "full-access"})["thread"]["id"]
    service.weave_budgets[thread] = args.budget
    (root / "thread.txt").write_text(thread, encoding="utf-8")
    meta = {"task": args.task, "mode": args.mode, "budget": args.budget}
    (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    service.turn_start({"threadId": thread, "input": task["prompt"].replace("{python}", sys.executable), "weave": True})
    started = time.time()
    log = (root / "progress.jsonl").open("a", encoding="utf-8")
    while True:
        time.sleep(30)
        view = goal.view(service, thread)
        session = service.store.load(thread)
        log.write(json.dumps({"t": round(time.time() - started), "goal": view, "session": session.status.value,
                              "errors": service.__dict__.get("_goal_errors")}, ensure_ascii=False, default=str) + "\n")
        log.flush()
        if view and view["status"] != "active":
            break
        if not view and not service._is_active(thread) and not service.weave_retries.get(thread):
            break  # finished in its turn with no goal
        if time.time() - started > args.hours_cap * 3600:
            break
    record = service.weave_goals.get(thread) or {}
    result = {**meta, "seconds": round(time.time() - started), "goal": goal.view(service, thread),
              "ledger": ledger(service, thread), "decision": record.get("linesDecision"),
              "soloReasons": record.get("soloReasons"), "lineStats": record.get("lineStats"),
              "holds": record.get("linesHolds"), "checks": record.get("checks"), "deepens": record.get("deepens"),
              "grade": grade(args.task, workspace)}
    (root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("task", "mode", "seconds", "decision")}, ensure_ascii=False), result["grade"]["passed"], "/", result["grade"]["of"], flush=True)
    runtime.close()


def table(roots):
    rows = []
    for root in roots:
        try:
            result = json.loads((root / "result.json").read_text("utf-8"))
        except OSError:
            rows.append(f"{root.name}: not finished")
            continue
        weighted = sum(item["weighted"] for item in result["ledger"])
        lead = result["ledger"][0]["weighted"]
        stats = result.get("lineStats") or {}
        goal = result.get("goal") or {}
        rows.append(f"{result['task']:9s} {result['mode']:7s} grade {result['grade']['passed']:3d}/{result['grade']['of']:<3d} "
                    f"{weighted / 1e6:5.2f}M lead {lead / 1e6:5.2f}M {result['seconds'] / 60:5.1f} min "
                    f"lines {stats.get('started', 0)} ({stats.get('failed', 0)} failed) agents {len(result['ledger']) - 1} "
                    f"checks-rejected {result.get('checks')} {goal.get('status')} checked={goal.get('checked')} decision={result.get('decision')}")
    print("\n".join(rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", type=Path, nargs="?")
    parser.add_argument("task", nargs="?", choices=tuple(TASKS))
    parser.add_argument("mode", nargs="?", choices=("lines", "prompt", "off"))
    parser.add_argument("--stored-model")
    parser.add_argument("--provider")
    parser.add_argument("--hours-cap", type=float, default=2.0)
    parser.add_argument("--budget", type=int, default=8_000_000)
    parser.add_argument("--regrade", action="store_true")
    parser.add_argument("--table", nargs="+", type=Path)
    args = parser.parse_args()
    if args.table:
        return table(args.table)
    if args.regrade:
        result = json.loads((args.root / "result.json").read_text("utf-8"))
        result["grade"] = grade(result["task"], args.root / "workspace")
        (args.root / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        return print(result["grade"]["passed"], "/", result["grade"]["of"], [case["case"] for case in result["grade"]["cases"] if not case["passed"]])
    if not (args.root and args.task and args.mode and args.stored_model):
        parser.error("root, task, mode and --stored-model are required")
    return run(args)


if __name__ == "__main__":
    main()
