"""Read-only profile of project-runner stores: time, tokens and concurrency by phase.

Usage: python -I scripts/profile_project_runs.py ".cache/*/state/project-runner/projects.sqlite3"
Episode start/end come from run snapshots in project_events (updatedAt when activeEpisode
appears/clears); tokens from project_usage. "in-flight" is the share of wall time with k
episodes out. Older stores without project_usage are skipped.
"""
import collections, glob, json, sqlite3, sys

def profile(path):
    db = sqlite3.connect("file:" + path + "?mode=ro", uri=True)
    eps = {}
    for eid, rid, data in db.execute("SELECT id,run_id,data FROM project_episodes"):
        d = json.loads(data)
        eps[eid] = {"run": rid, "phase": d.get("phase"), "tokens": 0, "start": None, "end": None}
    for eid, tok in db.execute("SELECT episode_id, SUM(tokens) FROM project_usage GROUP BY episode_id"):
        if eid in eps:
            eps[eid]["tokens"] = tok or 0
    runs = {rid: json.loads(d) for rid, d in db.execute("SELECT id,data FROM project_runs")}
    open_by_run = {}
    first = last = None
    for rid, kind, data in db.execute("SELECT run_id,kind,data FROM project_events ORDER BY sequence"):
        d = json.loads(data)
        t = d.get("updatedAt")
        if t is None:
            continue
        first = first if first is not None else t
        last = t
        ae = d.get("activeEpisode")
        cur = open_by_run.get(rid)
        if ae and ae.get("id") != cur:
            if cur and eps.get(cur) and eps[cur]["end"] is None:
                eps[cur]["end"] = t
            open_by_run[rid] = ae["id"]
            if ae["id"] in eps and eps[ae["id"]]["start"] is None:
                eps[ae["id"]]["start"] = t
        elif not ae and cur:
            if eps.get(cur) and eps[cur]["end"] is None:
                eps[cur]["end"] = t
            open_by_run[rid] = None
    timed = [e for e in eps.values() if e["start"] and e["end"]]
    if not timed:
        return None
    # label review scope: review episodes on a root with project scope vs task
    for e in timed:
        r = runs.get(e["run"], {})
        e["team"] = bool(r.get("teamOwnerId"))
        ph = e["phase"] or "?"
        if ph in ("verifying", "review", "reviewing"):
            ph = "review(task)" if e["team"] else "review(root)"
        e["label"] = ph
    span = last - first
    by_phase = collections.defaultdict(lambda: [0.0, 0, 0])
    for e in timed:
        b = by_phase[e["label"]]
        b[0] += e["end"] - e["start"]; b[1] += e["tokens"]; b[2] += 1
    # concurrency histogram
    pts = sorted([(e["start"], 1) for e in timed] + [(e["end"], -1) for e in timed])
    hist = collections.Counter(); k = 0; prev = first
    for t, dlt in pts:
        hist[k] += t - prev; k += dlt; prev = t
    hist[k] += last - prev
    workers = max((r.get("maxWorkers", 1) for r in runs.values()), default=1)
    return span, by_phase, hist, workers, sum(e["tokens"] for e in eps.values()), len(timed)

for path in sorted(glob.glob(sys.argv[1])):
    try:
        res = profile(path)
    except sqlite3.OperationalError as exc:
        print(path, "skip", exc); continue
    name = path.split("\\")[-4] if "\\" in path else path.split("/")[-4]
    if not res:
        print(name, "no timed episodes"); continue
    span, by_phase, hist, workers, tokens, n = res
    print(f"\n== {name}  maxWorkers={workers}  span={span/60:.0f}min  episodes={n}  tokens={tokens/1e6:.2f}M")
    for ph, (sec, tok, cnt) in sorted(by_phase.items(), key=lambda x: -x[1][0]):
        print(f"   {ph:14s} n={cnt:3d}  {sec/60:7.1f} ep-min  {tok/1e6:6.2f}M tok ({100*tok/max(tokens,1):4.1f}%)")
    print("   in-flight:", "  ".join(f"{k}:{100*v/span:.0f}%" for k, v in sorted(hist.items())))
