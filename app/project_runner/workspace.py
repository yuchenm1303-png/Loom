"""Explicit source-folder preparation; never commit a person's existing files."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.project_git_workflow import repository_operation_lock
from .review import git


def describe(root):
    root = Path(root).resolve()
    base = {"path": str(root), "git": False, "changes": [], "changeCount": 0}
    if not root.is_dir():
        return {**base, "status": "unavailable"}
    # Side by side: each git costs ~75 ms to start on Windows, and the hub cannot be drawn before this answers.
    with ThreadPoolExecutor(max_workers=4) as pool:
        asked = [pool.submit(git, root, "rev-parse", "--show-toplevel"),
                 pool.submit(git, root, "symbolic-ref", "--quiet", "--short", "HEAD"),
                 pool.submit(git, root, "rev-parse", "--verify", "HEAD"),
                 pool.submit(git, root, "--no-optional-locks", "-c", "core.quotepath=false", "status", "--porcelain=v1", raw=True)]
    answer = lambda ask: "" if isinstance(ask.exception(), ValueError) else ask.result()
    try:
        top = Path(asked[0].result()).resolve()
    except ValueError:
        return {**base, "status": "invalid" if (root / ".git").exists() else "not_git"}
    if top != root:
        return {**base, "status": "nested", "repositoryRoot": str(top)}
    branch, head = answer(asked[1]), answer(asked[2])
    lines = asked[3].result().splitlines()
    lines = [line for line in lines if line.strip()]
    return {**base, "git": True, "branch": branch or "detached HEAD", "head": head[:12],
            "status": "unborn" if not head else "ready" if branch else "detached",
            "changeCount": len(lines), "changes": [{"status": line[:2].strip() or "?", "path": line[3:].strip().strip('"')}
                                                   for line in lines[:40]]}


class WorkspaceMixin:
    def _assert_workspace_editable(self, thread_id):
        if self.project_store.session_run(thread_id):
            raise ValueError("Internal agent folders cannot be changed; open the original conversation")
        session = self._load(thread_id)
        if self._is_active(thread_id) or session.status.value in {"running", "waiting_approval"}:
            raise ValueError("Finish the current answer before changing the project folder")
        if any(run.get("sourceThreadId") == thread_id and run["state"] not in {"integrated", "cancelled"}
               for run in self.project_store.list()):
            raise ValueError("This project still owns its source folder; finish or cancel it before moving this conversation")
        return session

    def thread_move_project(self, params):
        with self.project_runner.guard:
            self._assert_workspace_editable(self._required_text(params, "threadId"))
            return super().thread_move_project(params)

    def project_prepare_workspace(self, params):
        with self.project_runner.guard:
            session = self._assert_workspace_editable(self._required_text(params, "threadId"))
            root = Path(session.workspace_dir).resolve()
            if params.get("confirmed") is not True or str(root) != params.get("expectedPath"):
                raise ValueError("Confirm the current folder before preparing Git")
            with repository_operation_lock(root):
                state = describe(root)
                action = params.get("action")
                if state["status"] == "ready":
                    return {"workspace": state}  # safe retry after an uncertain response
                if action == "initialize" and state["status"] in {"not_git", "unborn"}:
                    if state["status"] == "not_git":
                        git(root, "init", "-b", "main")
                    ref = git(root, "symbolic-ref", "HEAD")
                    tree = git(root, "mktree")  # stdin is closed: an empty tree, never the user's index
                    commit = git(root, "-c", "user.name=Loom", "-c", "user.email=loom@localhost", "commit-tree", tree,
                                 "-m", "Initialize project baseline (existing files excluded)")
                    git(root, "update-ref", ref, commit, "0" * len(tree))
                elif action == "branch" and state["status"] == "detached":
                    branch = self._required_text(params, "branch")
                    git(root, "check-ref-format", "--branch", branch)
                    git(root, "switch", "-c", branch)
                else:
                    raise ValueError("This folder cannot be prepared here; select the repository root and read its current state")
                return {"workspace": describe(root)}
