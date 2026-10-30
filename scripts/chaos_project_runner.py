"""Real-model qualification with the Host killed and restarted at random moments.

A multi-day run meets Host restarts (updates, sleep, crashes) far more often than a
half-hour run does. This compresses them: it starts scripts/smoke_project_runner.py,
kills that Host process at random times, restarts it with --resume on the same state,
and finally lets the run finish. The pass criteria are the qualification's own (the
external oracle), plus what the report shows about each restart: episodes continued,
nothing charged twice, nothing left for a person to reconcile.

Only the Host's own Python process is killed. Sandboxed child processes it started are
left to finish on their own, as they would after a real crash; killing a sandbox helper
mid-run leaves access-control entries behind on this machine. Consumes model quota.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import random
import subprocess
import sys
import time

SMOKE = Path(__file__).resolve().with_name("smoke_project_runner.py")


def last_report(log):
    for line in reversed(log.read_text(encoding="utf-8", errors="replace").splitlines()):
        if line.startswith("{") and '"outcome"' in line:
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, required=True, help="New directory for the qualification")
    parser.add_argument("--stored-model", required=True)
    parser.add_argument("--provider", choices=("openai", "openai-compatible", "opencode-go"))
    parser.add_argument("--scenario", choices=("tally", "ledger"), default="ledger")
    parser.add_argument("--token-budget", type=int, default=8_000_000)
    parser.add_argument("--max-episodes", type=int, default=80)
    parser.add_argument("--max-workers", type=int, choices=(1, 2), default=2)
    parser.add_argument("--kills", type=int, default=6, help="Host kills before the run may finish undisturbed")
    parser.add_argument("--min-seconds", type=float, default=60, help="Shortest Host lifetime before a kill")
    parser.add_argument("--max-seconds", type=float, default=420, help="Longest Host lifetime before a kill")
    parser.add_argument("--seconds", type=int, default=7200, help="Wall-clock limit for the whole run")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if root.exists():
        parser.error("--root must not exist")
    logs = root.parent / (root.name + "-chaos")
    logs.mkdir(parents=True)
    rng = random.Random(args.seed)
    common = ["--root", str(root), "--stored-model", args.stored_model, "--seconds", "7200"]
    if args.provider:
        common += ["--provider", args.provider]
    first = ["--scenario", args.scenario, "--token-budget", str(args.token_budget),
             "--max-episodes", str(args.max_episodes), "--max-workers", str(args.max_workers)]
    started, kills, lives = time.monotonic(), [], []
    final = None
    for life in range(args.kills + 1):
        log = logs / f"host-{life}.log"
        argv_ = [sys.executable, str(SMOKE), *common, *(first if life == 0 else ["--resume"])]
        with log.open("w", encoding="utf-8") as handle:
            child = subprocess.Popen(argv_, stdout=handle, stderr=subprocess.STDOUT)
            born = time.monotonic()
            # The first Host must have created its run before a kill means anything.
            while life == 0 and child.poll() is None and not (root / "run.json").exists():
                time.sleep(1)
            lifetime = rng.uniform(args.min_seconds, args.max_seconds) if life < args.kills else None
            while child.poll() is None:
                if time.monotonic() - started > args.seconds:
                    lifetime = 0  # out of time: stop the Host, the run stays resumable
                if lifetime is not None and time.monotonic() - born >= lifetime:
                    child.kill()  # this Python process only: TerminateProcess, no cleanup runs
                    child.wait()
                    kills.append({"life": life, "afterSeconds": round(time.monotonic() - born, 1),
                                  "atSeconds": round(time.monotonic() - started, 1)})
                    print(json.dumps({"killed": kills[-1]}), flush=True)
                    break
                time.sleep(1)
        report = last_report(log)
        lives.append({"life": life, "exit": child.returncode, "outcome": (report or {}).get("outcome")})
        if report is not None:  # this Host ended the run itself (passed, blocked, budget, ...)
            final = report
            break
        if time.monotonic() - started > args.seconds:
            break
    summary = {"kills": kills, "lives": lives, "outcome": (final or {}).get("outcome", "deadline"),
               "elapsedSeconds": round(time.monotonic() - started, 1), "final": final}
    (logs / "chaos-report.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0 if summary["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
