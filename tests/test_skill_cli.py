from __future__ import annotations

from pathlib import Path

import loom_entry
import loom_skill_cli


def _write_skill(root: Path) -> None:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        "---\n"
        "name: cli-demo\n"
        "description: CLI demo skill\n"
        "---\n"
        "Do the task.\n",
        encoding="utf-8",
    )


def test_loom_entry_dispatches_skill_subcommand(monkeypatch):
    seen = []

    def fake_skill_main(argv):
        seen.append(list(argv))
        return 7

    monkeypatch.setattr(loom_entry, "skill_main", fake_skill_main)
    assert loom_entry.main(["skill", "list"]) == 7
    assert seen == [["list"]]


def test_loom_entry_preserves_existing_agent_cli(monkeypatch):
    seen = []

    def fake_agent_main(argv):
        seen.append(list(argv))
        return 3

    monkeypatch.setattr(loom_entry, "agent_main", fake_agent_main)
    assert loom_entry.main(["hello", "world"]) == 3
    assert seen == [["hello", "world"]]


def test_skill_cli_installs_local_bundle_without_model_configuration(tmp_path: Path, capsys):
    source = tmp_path / "source"
    _write_skill(source)
    root = tmp_path / "skills"

    code = loom_skill_cli.main(["--root", str(root), "install", str(source)])

    assert code == 0
    assert (root / "cli-demo" / "SKILL.md").is_file()
    output = capsys.readouterr().out
    assert "Installed cli-demo" in output
    assert "inert" in output
