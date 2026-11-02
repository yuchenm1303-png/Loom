"""Compare suite logs: per task wall/steps/fails, and totals. usage: compare.py LOG [LOG...]"""
import json
import sys
from collections import defaultdict

runs = {}
for path in sys.argv[1:]:
    rows = [json.loads(line) for line in open(path, encoding="utf-8", errors="replace") if line.startswith("{")]
    runs[path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]] = rows

tasks = []
for rows in runs.values():
    for row in rows:
        if row["task"] not in tasks:
            tasks.append(row["task"])

print(f"{'task':10}" + "".join(f"{name:>28}" for name in runs))
totals = defaultdict(lambda: [0, 0, 0, 0, 0])
for task in tasks:
    line = f"{task:10}"
    for name, rows in runs.items():
        mine = [r for r in rows if r["task"] == task]
        if not mine:
            line += f"{'-':>28}"
            continue
        passed = sum(r["passed"] for r in mine)
        wall = sum(r["wall_s"] for r in mine) / len(mine)
        steps = sum(r["model_steps"] or 0 for r in mine) / len(mine)
        fails = sum(r["failed_calls"] or 0 for r in mine) / len(mine)
        line += f"{f'{passed}/{len(mine)} {wall:5.0f}s {steps:4.1f}st {fails:3.1f}f':>28}"
        t = totals[name]
        t[0] += passed; t[1] += len(mine); t[2] += wall; t[3] += steps; t[4] += fails
    print(line)
print(f"{'TOTAL':10}" + "".join(
    f"{f'{t[0]}/{t[1]} {t[2]:5.0f}s {t[3]:4.0f}st {t[4]:3.0f}f':>28}" for t in (totals[n] for n in runs)))
