"""What search_workspace_text reads.

A real run spent 170s on one search: the walker read every file in the folder,
~40k of them, mostly .cache, scratch copies and .claude/worktrees (whole copies of
the repository, which also doubled the matches). It now reads what git would show:
tracked files, then untracked files that are not ignored and not in hidden folders.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from app.agent_runtime.workspace_tools import _iter_workspace_files


def _write(root: Path, relative: str, text: str = "needle\n") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _files(root: Path) -> list[str]:
    return [path.relative_to(root).as_posix() for path in _iter_workspace_files(root)]


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_workspace_reads_tracked_then_visible_untracked_files(tmp_path):
    def git(*args, cwd=tmp_path):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    git("init", "-q")
    _write(tmp_path, ".gitignore", "build/\n")
    _write(tmp_path, "src/app.py")
    _write(tmp_path, ".github/workflows/ci.yml")
    git("add", ".")
    _write(tmp_path, "notes.md")                    # untracked, visible
    _write(tmp_path, "build/out.js")                # ignored
    _write(tmp_path, ".cache/copy/src/app.py")      # untracked, hidden folder
    _write(tmp_path, "node_modules/pkg/index.js")
    nested = tmp_path / "vendor" / "lib"            # a nested repository
    nested.mkdir(parents=True)
    git("init", "-q", cwd=nested)
    _write(nested, "lib.py")

    files = _files(tmp_path)

    assert files[:3] == sorted(files[:3]) and set(files[:3]) == {".gitignore", ".github/workflows/ci.yml", "src/app.py"}
    assert files[3:] == ["notes.md"]


def test_without_git_the_walk_skips_hidden_and_nested_repositories(tmp_path, monkeypatch):
    monkeypatch.setattr("app.agent_runtime.workspace_tools._git_list", lambda root, *args: None)
    _write(tmp_path, "src/app.py")
    _write(tmp_path, ".claude/worktrees/x/src/app.py")
    _write(tmp_path, "node_modules/pkg/index.js")
    _write(tmp_path, "copy/.git/HEAD", "ref: refs/heads/main\n")
    _write(tmp_path, "copy/src/app.py")

    assert _files(tmp_path) == ["src/app.py"]
