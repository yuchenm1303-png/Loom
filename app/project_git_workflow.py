"""Repository association and explicit project Git workflow operations."""
from __future__ import annotations

import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from app.project_git_commit import _run_git, _validate_project_repo, _root_or_rpc_error, _project_busy

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def execute(service, operation: str, params: dict, error):
    project_id = service._required_text(params, "projectId")
    _, root = _root_or_rpc_error(service, project_id)
    common = _run_git(root, "rev-parse", "--git-common-dir")
    directory = Path(common.stdout.strip()) if common and common.returncode == 0 else root
    if not directory.is_absolute():
        directory = root / directory
    key = str(directory.resolve()).casefold()
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    if not lock.acquire(blocking=False):
        raise error(-32036, "仓库正在执行其他 Git 操作，请稍后重试")
    try:
        return _execute(service, operation, params, error)
    finally:
        lock.release()


def github_slug(remote: str) -> str:
    match = re.fullmatch(r"git@github\.com:([\w.-]+/[\w.-]+?)(?:\.git)?", remote)
    if match:
        slug = match.group(1)
        return slug if all(part not in {".", ".."} for part in slug.split("/")) else ""
    try:
        parsed = urlsplit(remote)
    except ValueError:
        return ""
    if parsed.scheme in {"https", "ssh"} and parsed.hostname == "github.com":
        path = parsed.path.strip("/").removesuffix(".git")
        if re.fullmatch(r"[\w.-]+/[\w.-]+", path) and all(part not in {".", ".."} for part in path.split("/")):
            return path
    return ""


def _execute(service, operation: str, params: dict, error):
    project_id = service._required_text(params, "projectId")
    if operation in {"repository", "init"}:
        project, root = _root_or_rpc_error(service, project_id)
        if operation == "init" and (not root.is_dir() or _project_busy(service, project_id)):
            raise error(-32036, "目录不可用或项目内有任务正在运行")
    else:
        project, root, project_id = _validate_project_repo(service, params)

    def git(*args, timeout=15):
        result = _run_git(root, *args, timeout=timeout)
        if result is None or result.returncode:
            raise error(-32041, "Git 操作失败" if result is None else (result.stderr or result.stdout).strip())
        return result.stdout.strip()

    def branch(value):
        name = str(value or "").strip()
        if not name or name.startswith("-"):
            raise error(-32602, "请输入有效分支名称")
        return git("check-ref-format", "--branch", name)

    def snapshot():
        check = _run_git(root, "rev-parse", "--is-inside-work-tree")
        if not check or check.returncode:
            return {"isRepo": False, "branches": [], "worktrees": [], "repository": ""}
        remote = _run_git(root, "remote", "get-url", "origin")
        slug = github_slug(remote.stdout.strip()) if remote and remote.returncode == 0 else ""
        upstream = _run_git(root, "rev-parse", "--abbrev-ref", "@{upstream}")
        counts = _run_git(root, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
        ahead, behind = (map(int, counts.stdout.split()) if counts and counts.returncode == 0 else (0, 0))
        trees = []
        for block in git("worktree", "list", "--porcelain").split("\n\n"):
            item = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
            if item.get("worktree"):
                path = Path(item["worktree"]).resolve()
                managed = root.parent / f"{root.name}-worktrees"
                trees.append({"path": item["worktree"], "branch": item.get("branch", "").removeprefix("refs/heads/"),
                              "removable": path != root and path.parent == managed.resolve()})
        totals = _run_git(root, "diff", "HEAD", "--numstat")
        additions = deletions = 0
        if totals and totals.returncode == 0:
            for line in totals.stdout.splitlines():
                fields = line.split("\t")
                if len(fields) >= 3 and fields[0].isdigit() and fields[1].isdigit():
                    additions += int(fields[0])
                    deletions += int(fields[1])
        git_dir = git("rev-parse", "--absolute-git-dir")
        common_dir = Path(git("rev-parse", "--git-common-dir"))
        if not common_dir.is_absolute():
            common_dir = root / common_dir
        return {"isRepo": True, "repository": slug, "url": f"https://github.com/{slug}" if slug else "",
                "hasRemote": bool(remote and remote.returncode == 0),
                "branch": git("branch", "--show-current"), "branches": git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines(),
                "upstream": upstream.stdout.strip() if upstream and upstream.returncode == 0 else "",
                "ahead": ahead, "behind": behind, "worktrees": trees,
                "isWorktree": Path(git_dir).resolve() != common_dir.resolve(),
                "additions": additions, "deletions": deletions,
                "githubConnected": bool(getattr(service, "connectors", None) and service.connectors.github_status().get("connected"))}

    result = {}
    if operation == "init":
        git("init", "-b", "main")
    elif operation == "bind":
        slug = str(params.get("repository") or "").strip()
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", slug) or any(part in {".", ".."} for part in slug.split("/")):
            raise error(-32602, "仓库格式应为 owner/repository")
        existing = _run_git(root, "remote", "get-url", "origin")
        git("remote", "set-url" if existing and existing.returncode == 0 else "add", "origin", f"https://github.com/{slug}.git")
    elif operation == "switch_branch":
        name = branch(params.get("branch"))
        git("switch", *( ["-c", name] if params.get("create") else [name]))
    elif operation == "delete_branch":
        git("branch", "-d", branch(params.get("branch")))
    elif operation == "fetch":
        git("fetch", "origin", timeout=60)
    elif operation == "pull":
        git("pull", "--ff-only", timeout=60)
    elif operation == "push":
        name = branch(git("branch", "--show-current"))
        git("push", "--set-upstream", "origin", name, timeout=60)
    elif operation == "create_worktree":
        name = branch(params.get("branch"))
        directory = root.parent / f"{root.name}-worktrees"
        directory.mkdir(exist_ok=True)
        target = directory / name.replace("/", "-")
        if target.exists():
            raise error(-32602, "工作目录已存在，请选择其他分支名称")
        git("worktree", "add", "-b", name, str(target), "HEAD", timeout=60)
        registered = service.project_create({"root": str(target), "name": f"{project.name} · {name}"})
        result["project"] = registered["project"]
    elif operation == "remove_worktree":
        target = Path(str(params.get("path") or "")).resolve()
        listed = snapshot()["worktrees"]
        if not any(Path(tree["path"]).resolve() == target and tree["removable"] for tree in listed):
            raise error(-32602, "只能移除此项目创建的独立工作区")
        registered = next((item for item in service.projects.list() if Path(item.root).resolve() == target), None)
        if registered and _project_busy(service, registered.project_id):
            raise error(-32036, "独立工作区内有任务正在运行")
        git("worktree", "remove", str(target), timeout=30)
        if registered:
            service.project_remove({"projectId": registered.project_id})
    elif operation == "create_pr":
        state = snapshot()
        if not state["repository"]:
            raise error(-32602, "请先关联 GitHub 仓库")
        manager = getattr(service, "connectors", None)
        client = manager._client_for_bound(manager._github) if manager else None
        if client is None:
            raise error(-32041, "请先在设置中连接 GitHub")
        title = str(params.get("title") or "").strip()
        base = branch(params.get("base"))
        head = branch(state["branch"])
        if not title or len(title) > 256 or base == head:
            raise error(-32602, "请填写 PR 标题，并选择不同的目标分支")
        # Check the actual remote ref, rather than assuming the upstream setting
        # means the latest local commits have been published.
        published = git("ls-remote", "--heads", "origin", f"refs/heads/{head}", timeout=60)
        if not published or published.split()[0] != git("rev-parse", "HEAD"):
            raise error(-32602, "当前提交尚未推送，请先推送分支")
        existing, _ = client.request("GET", f"/repos/{state['repository']}/pulls", query={"state": "open", "head": f"{state['repository'].split('/')[0]}:{head}", "base": base})
        if existing:
            return {"projectId": project_id, **state, "pullRequestUrl": existing[0].get("html_url", "")}
        body = str(params.get("body") or "")
        if len(body) > 100_000:
            raise error(-32602, "PR 说明不能超过 100000 个字符")
        payload, _ = client.request("POST", f"/repos/{state['repository']}/pulls", body={"title": title, "body": body, "head": head, "base": base, "draft": bool(params.get("draft", True))})
        result["pullRequestUrl"] = payload.get("html_url", "")
    elif operation != "repository":
        raise error(-32601, "未知 Git 操作")
    if operation != "repository":
        service._notify("project/updated", {"projectId": project_id, "reason": operation})
    return {"projectId": project_id, **result, **snapshot()}
