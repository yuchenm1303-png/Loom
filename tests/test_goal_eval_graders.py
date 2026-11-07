"""The eval's graders have to be right before they judge anything: a good solution passes, an empty or naive one does not."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "goal_eval"))

from tasks import BUG_MODULES, TASKS, _config_theory, _er_theory, grade_bugs, grade_phase, grade_pipeline, grade_toolkit, prepare_bugs  # noqa: E402


def failed(results):
    return [item["case"] for item in results if not item["passed"]]


def test_the_seeded_bugs_are_all_caught_and_the_correct_library_passes(tmp_path):
    prepare_bugs(tmp_path)
    seeded = grade_bugs(tmp_path)
    checks = sum(len(checks) for _, checks in BUG_MODULES.values())
    guards = {"paging: past end", "orders: cancel from paid"}  # right already: they catch a fix that breaks them
    assert {item["case"] for item in seeded if item["passed"]} == guards
    assert checks >= 36
    fixed = Path(__file__).with_name("goal_eval_solutions") / "inventory"
    for path in fixed.glob("*.py"):
        (tmp_path / "inventory" / path.name).write_text(path.read_text("utf-8"), "utf-8")
    (tmp_path / "FIXES.md").write_text(" ".join(BUG_MODULES), "utf-8")
    assert failed(grade_bugs(tmp_path)) == []


def test_the_toolkit_grader_passes_a_correct_toolkit_and_fails_an_empty_folder(tmp_path):
    assert len(failed(grade_toolkit(tmp_path))) >= 25
    source = Path(__file__).with_name("goal_eval_solutions") / "toolkit"
    (tmp_path / "toolkit").mkdir()
    for path in source.glob("*.py"):
        (tmp_path / "toolkit" / path.name).write_text(path.read_text("utf-8"), "utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "__init__.py").write_text("", "utf-8")
    (tmp_path / "tests" / "test_a.py").write_text("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n", "utf-8")
    (tmp_path / "README.md").write_text("expr csvq cron diffs", "utf-8")
    assert failed(grade_toolkit(tmp_path)) == []


def test_the_phase_answers_are_the_known_ones(tmp_path):
    assert abs(_er_theory(2.0) - 0.7968) < 1e-3 and _er_theory(0.5) == 0.0
    assert abs(_config_theory(0.8) - 0.0) < 1e-6, "above the critical p there is no giant component"
    assert _config_theory(0.2) > 0.5
    assert failed(grade_phase(tmp_path)) == ["results.json"]
    data = {"er": {c: {"1000": _er_theory(float(c)) + 0.01, "100000": _er_theory(float(c))} for c in ("0.5", "1.5", "2.0", "3.0")},
            "er_theory": {c: _er_theory(float(c)) for c in ("0.5", "1.5", "2.0", "3.0")},
            "config": {p: _config_theory(float(p)) for p in ("0.2", "0.5", "0.8")},
            "config_theory": {p: _config_theory(float(p)) for p in ("0.2", "0.5", "0.8")}, "critical_p": 0.75}
    (tmp_path / "results.json").write_text(json.dumps(data), "utf-8")
    (tmp_path / "report.md").write_text("临界 p = 0.75，c=2 时 0.79 " + "x" * 600, "utf-8")
    (tmp_path / "run_all.py").write_text("", "utf-8")
    assert failed(grade_phase(tmp_path)) == []
    data["critical_p"] = 0.5
    data["er"]["2.0"]["100000"] = 0.5
    (tmp_path / "results.json").write_text(json.dumps(data), "utf-8")
    assert set(failed(grade_phase(tmp_path))) == {"critical p", "er n=100000 c=2.0"}


PIPELINE = '''import csv, json, random
rng = random.Random(2026)
rows = [(i, f"c{rng.randrange(50)}", rng.randint(1, 500)) for i in range(2000)]
total = sum(r[2] for r in rows)
with open("orders.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["id", "customer", "amount"]); w.writerows(rows)
kept = [r for r in rows if r[2] % 7]
with open("clean.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["id", "customer", "amount"]); w.writerows(kept)
clean_total = sum(r[2] for r in kept)
roll = {}
for r in kept:
    roll[r[1]] = roll.get(r[1], 0) + r[2]
json.dump(roll, open("rollup.json", "w"), sort_keys=True)
top = max(roll, key=roll.get)
scaled = {k: round(v * clean_total / total, 6) for k, v in roll.items()}
json.dump(scaled, open("scaled.json", "w"), sort_keys=True)
json.dump({"total": total, "clean_total": clean_total, "top_customer": top, "top_amount": roll[top], "scaled_sum": sum(scaled.values())}, open("state.json", "w"), sort_keys=True)
'''


def test_the_pipeline_grader_runs_it_from_nothing_and_catches_a_wrong_step(tmp_path):
    assert failed(grade_pipeline(tmp_path)) == ["run_all.py"]
    (tmp_path / "run_all.py").write_text(PIPELINE, "utf-8")
    (tmp_path / "NOTES.md").write_text("n", "utf-8")
    assert failed(grade_pipeline(tmp_path)) == []
    (tmp_path / "run_all.py").write_text(PIPELINE.replace("r[2] % 7]", "r[2] % 5]"), "utf-8")
    assert "clean rows" in failed(grade_pipeline(tmp_path))
    (tmp_path / "run_all.py").write_text(PIPELINE.replace("random.Random(2026)", "random.Random()"), "utf-8")
    assert "deterministic" in failed(grade_pipeline(tmp_path))


def test_every_prompt_gives_time_and_never_mentions_sub_agents():
    for task in TASKS.values():
        assert "一个小时" in task["prompt"] and "子代理" not in task["prompt"] and "{python}" in task["prompt"]
