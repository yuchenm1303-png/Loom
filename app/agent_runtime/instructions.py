"""Deterministic project instruction discovery, bounded and source-labelled."""
from __future__ import annotations

from pathlib import Path


class InstructionLoader:
    def __init__(self, max_bytes: int = 32_768) -> None:
        self.max_bytes = max_bytes

    def load(self, workspace: str) -> str:
        cwd = Path(workspace).resolve()
        root = next((p for p in (cwd, *cwd.parents) if (p / ".git").exists()), cwd)
        directories = [cwd]
        while directories[-1] != root:
            directories.append(directories[-1].parent)
        remaining = self.max_bytes
        sections = []
        for directory in reversed(directories):
            for name in ("AGENTS.override.md", "AGENTS.md"):
                path = directory / name
                if not path.is_file():
                    continue
                # Do not let a project symlink import instructions outside its root.
                if not path.resolve().is_relative_to(root):
                    continue
                with path.open("rb") as handle:
                    raw = handle.read(remaining + 1)
                truncated = len(raw) > remaining
                text = raw[:remaining].decode("utf-8", errors="replace")
                remaining -= min(len(raw), remaining)
                sections.append(f"Project instructions from {path}:\n{text}" + ("\n[Instruction budget exhausted]" if truncated else ""))
                break
            if remaining <= 0:
                break
        return "\n\n".join(sections)
