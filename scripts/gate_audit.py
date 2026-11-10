"""How build mode's gates and long-goal thresholds behave in real sessions.

    python scripts/gate_audit.py [--since 2026-10-26] [--home ~/.loom]

Per gate: refused calls, the model steps and turns they hit, steps wasted entirely on refusals and their weighted
tokens, refusals per turn (a gate gives way after its holds), and the tool the model called next. Then the
thresholds' recorded decisions (deepen or finish, claim held, note due, split asked) with the numbers they were
made on, from weave-goals.json. Reads logs only.
"""
import argparse
import collections
import glob
import json
import os

parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
parser.add_argument("--since", default="2026-10-26")
parser.add_argument("--home", default=os.path.expanduser(os.path.join("~", ".loom")))
args = parser.parse_args()
SINCE = args.since
ROOT = os.path.join(args.home, "agent_runtime", "sessions")
GATES = {"turn_plan_required", "goal_scope_required", "goal_charter_required", "goal_lines_required",
         "goal_note_required", "investigate_suggested"}

refusals = collections.Counter()          # kind -> refused calls
batches = collections.Counter()           # kind -> model steps with a refusal of that kind
wasted_steps = collections.Counter()      # kind -> steps whose every call was refused by a gate
wasted_tokens = collections.Counter()     # kind -> weighted tokens of those steps
turns_hit = collections.defaultdict(set)  # kind -> {(session, turn)}
holds_per_turn = collections.defaultdict(collections.Counter)  # kind -> {n refusal steps in a turn: turns}
next_tools = collections.defaultdict(collections.Counter)      # kind -> tool the model called next (first success)
other_fail = collections.Counter()        # other not-executed kinds / ValueErrors
sessions = weave_sessions = 0
turns_total = weave_turns = 0
steps_total = collections.Counter()
tokens_total = collections.Counter()
notice = collections.Counter()
examples = collections.defaultdict(list)


def weighted(usage):
    usage = usage or {}
    inp, cached = usage.get("input_tokens") or 0, usage.get("cached_input_tokens") or 0
    return (inp - cached) + 0.1 * cached + (usage.get("output_tokens") or 0)


for path in glob.glob(os.path.join(ROOT, "*", "events.jsonl")):
    sid = path.split(os.sep)[-2]
    try:
        meta = open(os.path.join(ROOT, sid, "session.json"), encoding="utf-8").read(200000)
    except OSError:
        meta = ""
    weave = '"weave":true' in meta.replace(" ", "")
    rows = []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("created_at", "") >= SINCE:
            rows.append(row)
    if not rows:
        continue
    sessions += 1
    weave_sessions += weave
    space = "weave" if weave else "chat"
    # Walk steps: a model_response, then the tool results of its calls.
    step, step_usage, step_calls, step_refused, step_kinds = None, None, 0, 0, set()
    turn = None
    pending = {}  # kind -> waiting for the next successful tool

    def close_step():
        if step is None:
            return
        if step_calls and step_refused == step_calls and step_kinds:
            for kind in step_kinds:
                wasted_steps[(space, kind)] += 1
                wasted_tokens[(space, kind)] += weighted(step_usage) / len(step_kinds)

    turn_holds = collections.Counter()
    for row in rows:
        kind, data = row["kind"], row.get("data") or {}
        if kind == "turn_started":
            for gate, n in turn_holds.items():
                holds_per_turn[(space, gate)][n] += 1
            turn_holds = collections.Counter()
            turn = row.get("turn_id")
            turns_total += 1
            weave_turns += weave
            pending = {}
        elif kind == "model_response":
            close_step()
            step, step_usage = data.get("step_id"), data.get("usage")
            step_calls, step_refused, step_kinds = len(data.get("tool_calls") or ()), 0, set()
            steps_total[space] += 1
            tokens_total[space] += weighted(step_usage)
        elif kind == "tool_failed":
            info = data.get("data") if isinstance(data.get("data"), dict) else {}
            failure = info.get("failure_kind")
            if failure in GATES:
                refusals[(space, failure)] += 1
                step_refused += 1
                if failure not in step_kinds:
                    batches[(space, failure)] += 1
                    turn_holds[failure] += 1
                step_kinds.add(failure)
                turns_hit[(space, failure)].add((sid, turn))
                pending[failure] = True
                if len(examples[failure]) < 3:
                    examples[failure].append(f"{sid[:8]} {row['created_at'][:16]} {data.get('tool')}")
            else:
                content = str(data.get("content") or "")
                label = failure or (content.split(":")[0][:60] if content.startswith(("ValueError", "Invalid tool request")) else None)
                if failure in {"invalid_tool_request", "duplicate_call", "tool_not_in_step", "dependency_not_executed"} or (
                        not failure and content.startswith("ValueError")):
                    other_fail[(space, (failure or content[:90]))] += 1
        elif kind == "tool_completed":
            for gate in list(pending):
                next_tools[(space, gate)][data.get("tool")] += 1
                del pending[gate]
        elif kind == "turn_completed" and data.get("verification_notice"):
            notice[space] += 1
    close_step()
    for gate, n in turn_holds.items():
        holds_per_turn[(space, gate)][n] += 1

print(f"since {SINCE}: sessions {sessions} (weave {weave_sessions}), turns {turns_total} (weave {weave_turns})")
print("model steps:", dict(steps_total), "weighted tokens (M):", {k: round(v / 1e6, 2) for k, v in tokens_total.items()})
print("turns ending with a runtime verification notice:", dict(notice))
print("\n== gate refusals ==")
for key in sorted(refusals, key=lambda k: -refusals[k]):
    space, gate = key
    print(f"{space:5s} {gate:24s} calls {refusals[key]:4d}  steps {batches[key]:4d}  turns {len(turns_hit[key]):4d}  "
          f"wasted steps {wasted_steps[key]:4d}  wasted {wasted_tokens[key] / 1e6:5.2f}M  "
          f"holds/turn {dict(sorted(holds_per_turn[key].items()))}")
    print("      next successful tool:", next_tools[key].most_common(6))
print("\n== other not-executed / ValueError ==")
for key, n in other_fail.most_common(20):
    print(f"{key[0]:5s} {n:4d}  {key[1]}")
print("\nexamples:", dict(examples))

print("\n== threshold decisions (weave-goals.json) ==")
try:
    goals = json.load(open(os.path.join(args.home, "project-runner", "weave-goals.json"), encoding="utf-8"))
except (OSError, ValueError):
    goals = {}
decided = collections.defaultdict(list)
for thread, record in goals.items():
    for entry in record.get("decisions") or ():
        decided[entry.get("what")].append(entry)
for what, entries in sorted(decided.items()):
    print(f"{what:20s} {len(entries):4d}  last: {json.dumps(entries[-1], ensure_ascii=False)[:200]}")
if not decided:
    print("none recorded yet")
