"""Remove derived checkouts a project no longer needs, so long runs do not fill the disk.

Every review, delivery and team task gets its own Git worktree of the user's repository:
a full copy of its tracked files, registered in the user's `git worktree list`. A small
real project already left 17. Over days on a real repository that is gigabytes, on a
machine whose system drive has been nearly full.

Only copies that can be recreated are removed, and never while the run has an episode in
flight: review checkouts once their candidate is superseded, and every checkout of a run
that finished (integrated, or a team task merged into its aggregate). A cancelled or
stopped run keeps its workspace for inspection; only its derived copies go.
"""
from __future__ import annotations

from pathlib import Path
import shutil
import time

from .review import git

SWEEP_SECONDS = 60


class Janitor:
    def __init__(self):
        self.last = 0.0

    def sweep(self, store, *, now=None):
        now = time.time() if now is None else now
        if now - self.last < SWEEP_SECONDS:
            return 0
        self.last = now
        runs = {run["id"]: run for run in store.list()}
        merged = {item["runId"] for run in runs.values() for item in run.get("teamChildren", []) if item["state"] == "merged"}
        removed = 0
        for run in runs.values():
            if run["activeEpisode"]:
                continue
            finished = run["state"] == "integrated" or run["id"] in merged
            current = (run.get("reviewCandidate") or {}).get("id")
            for folder, candidate in _reviews(store.home / "reviews", run["id"]):
                if finished or not current or not current.startswith(candidate):
                    removed += _remove(run, folder)
            for folder in _children(store.home / "deliveries" / run["id"]):
                removed += _remove(run, folder)  # only built a commit; the commit stays in the repository
            if finished:
                for folder in _children(store.home / "delivery-checks" / run["id"]):
                    removed += _remove(run, folder)
                removed += _remove(run, store.home / "workspaces" / run["id"])
        if removed:
            for repository in {run["repository"] for run in runs.values() if run.get("repository")}:
                try:
                    git(repository, "worktree", "prune")
                except ValueError:
                    pass
        return removed


# Git for Windows refuses a worktree whose path is much over 200 characters ("'$GIT_DIR' too
# big", core.longpaths does not lift it), so the folders checkouts live in keep short leaf names.
SHORT = 12


def review_folder(run_id: str, candidate_id: str) -> str:
    """A review checkout's folder name under reviews/: one level, so its parents exist up front."""
    return f"{run_id}--{candidate_id[:SHORT]}"


def _reviews(root: Path, run_id: str):
    """A run's review checkouts with their candidate: reviews/<run>--<candidate>, and the older reviews/<run>/<candidate>."""
    flat = [(item, item.name[len(run_id) + 2:]) for item in _children(root) if item.name.startswith(f"{run_id}--")]
    return flat + [(item, item.name) for item in _children(root / run_id)]


def _children(folder: Path):
    return [item for item in folder.iterdir() if item.is_dir()] if folder.is_dir() else []


def _remove(run, folder: Path) -> int:
    if not folder.exists():
        return 0
    try:
        git(run["repository"], "worktree", "remove", "--force", str(folder))
    except ValueError:
        shutil.rmtree(folder, ignore_errors=True)  # not a registered worktree (or already unregistered)
    return 0 if folder.exists() else 1
