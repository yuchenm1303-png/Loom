from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.agent_runtime.instructions import InstructionLoader, PROJECT_DOC_SEPARATOR


def test_nested_agents_walk_root_to_cwd_and_deeper_rules_come_later(tmp_path: Path):
    root = tmp_path / "repo"
    cwd = root / "packages" / "worker"
    cwd.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "AGENTS.md").write_text("root rule", encoding="utf-8")
    (root / "packages" / "AGENTS.md").write_text("package rule", encoding="utf-8")
    (cwd / "AGENTS.md").write_text("worker rule", encoding="utf-8")

    loaded = InstructionLoader().load(cwd)

    assert loaded.startswith(PROJECT_DOC_SEPARATOR)
    assert loaded.index("root rule") < loaded.index("package rule") < loaded.index("worker rule")


def test_override_wins_over_agents_and_conflicting_deeper_override_is_last(tmp_path: Path):
    root = tmp_path / "repo"
    cwd = root / "sub"
    cwd.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "AGENTS.md").write_text("root says use A", encoding="utf-8")
    (cwd / "AGENTS.md").write_text("sub regular says use B", encoding="utf-8")
    (cwd / "AGENTS.override.md").write_text("sub override says use C", encoding="utf-8")

    entries = InstructionLoader().load_entries(cwd)

    assert [entry.path.name for entry in entries] == ["AGENTS.md", "AGENTS.override.md"]
    assert "sub regular" not in InstructionLoader().load(cwd)
    assert entries[-1].text == "sub override says use C"


def test_configured_fallback_is_used_only_when_primary_names_are_absent(tmp_path: Path):
    root = tmp_path / "repo"
    child = root / "child"
    child.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "TEAM.md").write_text("fallback root", encoding="utf-8")
    (child / "TEAM.md").write_text("fallback child", encoding="utf-8")
    (child / "AGENTS.md").write_text("primary child", encoding="utf-8")

    entries = InstructionLoader(fallback_names=("TEAM.md",)).load_entries(child)

    assert [entry.path.name for entry in entries] == ["TEAM.md", "AGENTS.md"]
    assert entries[0].text == "fallback root"
    assert entries[1].text == "primary child"


def test_empty_root_markers_disable_parent_walk(tmp_path: Path):
    root = tmp_path / "repo"
    cwd = root / "child"
    cwd.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "AGENTS.md").write_text("root rule", encoding="utf-8")
    (cwd / "AGENTS.md").write_text("cwd rule", encoding="utf-8")

    loaded = InstructionLoader(root_markers=()).load(cwd)

    assert "cwd rule" in loaded
    assert "root rule" not in loaded


def test_instruction_byte_budget_is_shared_across_scope_chain(tmp_path: Path):
    root = tmp_path / "repo"
    cwd = root / "child"
    cwd.mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "AGENTS.md").write_text("A" * 8, encoding="utf-8")
    (cwd / "AGENTS.md").write_text("B" * 8, encoding="utf-8")

    entries = InstructionLoader(max_bytes=10).load_entries(cwd)

    assert entries[0].text == "A" * 8
    assert entries[1].text == "B" * 2
    assert entries[1].truncated is True


def test_discovered_symlink_is_not_rejected_only_because_target_is_outside_root(tmp_path: Path):
    if os.name == "nt":
        pytest.skip("symlink creation is not reliably available on Windows CI")
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".git").mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("linked instructions", encoding="utf-8")
    try:
        (root / "AGENTS.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    loaded = InstructionLoader().load(root)

    assert "linked instructions" in loaded
