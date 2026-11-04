"""Where a project's time goes: the waits the scheduler imposes, and episode time by phase.

The 2026-11-04 parallelism study (docs/project-runner-parallelism-study-2026-11-04.md) had
to reconstruct this from old stores: real runs kept two workers busy together only 10-38%
of the time, and review, not implementation, took most of the tokens. Whether more agents
would help depends on numbers the runner did not keep, so it keeps them now:

- waits: wall time a run had work it could start and the scheduler held it back, by why.
    workerCap   a team task was ready, but every worker slot was taken
    fileScope   ready team tasks all conflicted with running ones (files or shared resources)
    admission   the run could dispatch, but other projects' episodes held every Host slot
    reviewerCap a review shard was ready, but every reviewer slot was taken
    disk        a ready team task needed a new checkout the drive could not hold (admission.py)
  One open wait per (run, channel); switching or closing it adds the elapsed time. Nothing
  is written while a wait continues, so a tick costs a dictionary lookup.
- summarize: episode seconds and tokens by phase, and how many episodes were out at once,
  from the timeline spans the overview already derives from project events.
"""
from __future__ import annotations

import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS project_waits(wait_key TEXT NOT NULL, reason TEXT NOT NULL, seconds REAL NOT NULL,
    PRIMARY KEY(wait_key, reason));
CREATE TABLE IF NOT EXISTS project_wait_open(wait_key TEXT PRIMARY KEY, reason TEXT NOT NULL, since REAL NOT NULL);
"""


def _open(store):
    cache = getattr(store, "_open_waits", None)
    if cache is None:
        with store.lock:
            cache = {key: (reason, since) for key, reason, since in
                     store.db.execute("SELECT wait_key, reason, since FROM project_wait_open")}
        store._open_waits = cache
    return cache


def note_wait(store, run_id, channel, reason, *, now=None):
    """Open, switch or close the wait of one run on one channel (``reason`` None closes it)."""
    key = f"{run_id}:{channel}"
    cache = _open(store)
    current = cache.get(key)
    if (current[0] if current else None) == reason:
        return
    now = time.time() if now is None else now
    with store.transaction() as db:
        if current:
            db.execute("INSERT INTO project_waits VALUES(?,?,?) ON CONFLICT(wait_key, reason) "
                       "DO UPDATE SET seconds=seconds+excluded.seconds", (key, current[0], max(0.0, now - current[1])))
            db.execute("DELETE FROM project_wait_open WHERE wait_key=?", (key,))
        if reason:
            db.execute("INSERT OR REPLACE INTO project_wait_open VALUES(?,?,?)", (key, reason, now))
    if reason:
        cache[key] = (reason, now)
    else:
        cache.pop(key, None)


def waits(store, run_ids, *, now=None):
    """Seconds waited per reason over these runs, the open waits counted up to now."""
    now = time.time() if now is None else now
    ids = list(run_ids)
    if not ids:
        return {}
    totals = {}
    pattern = " OR ".join("wait_key LIKE ?" for _ in ids)
    args = [f"{run_id}:%" for run_id in ids]
    with store.lock:
        for reason, seconds in store.db.execute(
                f"SELECT reason, SUM(seconds) FROM project_waits WHERE {pattern} GROUP BY reason", args):
            totals[reason] = totals.get(reason, 0.0) + float(seconds or 0)
        for reason, since in store.db.execute(f"SELECT reason, since FROM project_wait_open WHERE {pattern}", args):
            totals[reason] = totals.get(reason, 0.0) + max(0.0, now - float(since))
    return {reason: round(seconds, 1) for reason, seconds in totals.items()}


def phase_of(span, kinds):
    """planning / working / taskReview / projectReview / shardReview / aggregateReview, from a timeline span."""
    phase = span.get("phase") or ""
    if phase != "verifying":
        return phase or "other"
    return {"team": "taskReview", "shard": "shardReview"}.get(kinds.get(span["runId"]), span.get("reviewKind") or "projectReview")


def summarize(spans, kinds, *, now=None):
    """Episode seconds, tokens and counts per phase; wall-time share with k episodes out at once."""
    now = time.time() if now is None else now
    timed = [(span, float(span["start"]), float(span["end"] if span.get("end") is not None else now))
             for span in spans if span.get("start") is not None]
    phases = {}
    for span, start, end in timed:
        item = phases.setdefault(phase_of(span, kinds), {"seconds": 0.0, "tokens": 0, "episodes": 0})
        item["seconds"] += max(0.0, end - start)
        item["tokens"] += int(span.get("tokens") or 0)
        item["episodes"] += 1
    for item in phases.values():
        item["seconds"] = round(item["seconds"], 1)
    if not timed:
        return {"span": 0.0, "phases": phases, "concurrency": {}, "peak": 0}
    first = min(start for _, start, _ in timed)
    last = max(end for _, _, end in timed)
    points = sorted([(start, 1) for _, start, _ in timed] + [(end, -1) for _, _, end in timed])
    shares, level, previous, peak = {}, 0, first, 0
    for at, delta in points:
        shares[level] = shares.get(level, 0.0) + (at - previous)
        level += delta
        peak = max(peak, level)
        previous = at
    return {"span": round(last - first, 1), "phases": phases, "peak": peak,
            "concurrency": {str(level): round(seconds, 1) for level, seconds in sorted(shares.items()) if seconds > 0}}
