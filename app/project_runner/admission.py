"""How many project episodes may be out at once, Host-wide, and whether a new checkout fits.

Until 2026-11-04 the scheduler ran one project at a time: a run without a team could only
start an episode when no episode of any project was out, and a team only when every
episode out was its own. Two conversations' projects queued behind each other although
they work in separate checkouts. The parallelism study
(docs/project-runner-parallelism-study-2026-11-04.md) found that lock to be the second
structural limit after serial review.

Admission now has independent bounds, and an episode starts only when all hold:
- Host slots (``slots``): LOOM_PROJECT_MAX_EPISODES, default 4, 1..16. While any project
  waits out a provider or network failure the slots halve: a provider that throttles one
  project throttles the next one too, and more requests only lengthen the outage.
- the run family's own bound (parallel.eligible): a team's maxWorkers, the review shards'
  MAX_REVIEWERS, one episode for a run without children.
- disk, before a new checkout is made (``checkouts_that_fit``): every team task and review
  shard is a full worktree of the repository, on a machine whose system drive has been
  nearly full.

Desktop automation was never inside this scheduler (ordinary conversations use it
alongside projects); its runtime serializes input and invalidates another session's
observation, so concurrent projects cannot drive the desktop blindly.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import time

DEFAULT_SLOTS = 4
MAX_SLOTS = 16
# Free space a new checkout must leave on the drive.
RESERVE_BYTES = 2 * 1024 ** 3
CHECKOUT_OVERHEAD = 1.25          # build outputs, probe files and git metadata on top of the sources
SIZE_CACHE_SECONDS = 600

_sizes: dict[str, tuple[float, int]] = {}


def configured_slots():
    try:
        value = int(os.environ.get("LOOM_PROJECT_MAX_EPISODES", DEFAULT_SLOTS))
    except ValueError:
        value = DEFAULT_SLOTS
    return max(1, min(MAX_SLOTS, value))


def slots(live, *, now=None):
    """Episodes all projects together may have out in this tick."""
    now = time.time() if now is None else now
    value = configured_slots()
    if any(run.get("retryAfter", 0) > now and run.get("transientFailureStreak", 0) > 0 for run in live):
        value = max(1, value // 2)
    return value


def checkout_size(source):
    """Bytes of the files a checkout of ``source`` holds (tracked plus unignored), cached for a while."""
    key = str(Path(source).resolve())
    cached = _sizes.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < SIZE_CACHE_SECONDS:
        return cached[1]
    from .review import git
    total = 0
    try:
        names = git(source, "ls-files", "--cached", "--others", "--exclude-standard", "-z", raw=True).split("\0")
    except (ValueError, OSError):
        names = []
    for name in filter(None, names):
        try:
            total += (Path(source) / name).stat().st_size
        except OSError:
            pass
    _sizes[key] = (now, total)
    return total


def checkouts_that_fit(home, source, wanted, *, free=None):
    """How many of ``wanted`` new checkouts of ``source`` leave the reserve free on the drive."""
    if wanted <= 0:
        return 0
    if free is None:
        try:
            free = shutil.disk_usage(home).free
        except OSError:
            return wanted  # cannot tell: do not stop work on a measurement failure
    need = max(1, int(checkout_size(source) * CHECKOUT_OVERHEAD))
    return max(0, min(wanted, int((free - RESERVE_BYTES) // need)))
