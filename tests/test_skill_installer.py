from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.agent_runtime.skill_bundle import (
    list_skill_files,
    read_skill_resource,
    stage_skill_bundle,
)
from app.agent_runtime.skill_installer import SkillInstallError, SkillInstaller
from app.agent_runtime.skills import SkillManager
from app.skill_dispatch import main as dispatch_main


def _write_skill(root: Path, name: str, body: str = "Do the work.") -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Workflow for {name}\n"
        f"short_description: {name} helper\n"
        "---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return directory


def test_installer_copies_complete_bundle_and_records_provenance(tmp_path: Path):
    source = tmp_path / "source"
    skill_dir = _write_skill(source, "release-check")
    scripts = skill_dir / "scripts"
    scripts.mkdir()
    (scripts / "verify.py").write_text("print('ok')\n", encoding="utf-8")

    installer = SkillInstaller(tmp_path / "home" / "skills")
    rows = installer.install(source)

    assert [row.name for row in rows] == ["release-check"]
    installed = tmp_path / "home" / "skills" / "release-check"
    assert (installed / "scripts" / "verify.py").read_text(encoding="utf-8") == "print('ok')\n"
    manifest = json.loads((installed / ".loom-skill.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "release-check"
    assert manifest["source"] == str(source.resolve())
    assert manifest["source_kind"] == "directory"


def test_installer_requires_selection_for_multi_skill_sources(tmp_path: Path):
    source = tmp_path / "source"
    _write_skill(source, "alpha")
    _write_skill(source, "beta")
    installer = SkillInstaller(tmp_path / "skills")

    with pytest.raises(SkillInstallError, match="multiple skills"):
        installer.install(source)

    rows = installer.install(source, names=("beta",))
    assert [row.name for row in rows] == ["beta"]


def test_installer_update_replaces_bundle_from_recorded_source(tmp_path: Path):
    source = tmp_path / "source"
    skill_dir = _write_skill(source, "mutable-skill", body="VERSION ONE")
    installer = SkillInstaller(tmp_path / "skills")
    installer.install(source)

    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: mutable-skill\n"
        "description: Workflow for mutable-skill\n"
        "---\n"
        "VERSION TWO\n",
        encoding="utf-8",
    )
    updated = installer.update("mutable-skill")

    assert updated.name == "mutable-skill"
    assert "VERSION TWO" in (updated.path / "SKILL.md").read_text(encoding="utf-8")


def test_installer_rejects_zip_path_traversal(tmp_path: Path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../escape.txt", "bad")
        bundle.writestr(
            "safe/SKILL.md",
            "---\nname: safe\ndescription: Safe skill\n---\nDo work.\n",
        )

    installer = SkillInstaller(tmp_path / "skills")
    with pytest.raises(SkillInstallError, match="unsafe path"):
        installer.install(archive)
    assert not (tmp_path / "escape.txt").exists()


def test_bundle_resources_can_be_read_and_staged_into_workspace(tmp_path: Path):
    source = tmp_path / "source"
    skill_dir = _write_skill(source, "bundle-demo")
    (skill_dir / "reference.md").write_text("REFERENCE TEXT\n", encoding="utf-8")
    scripts = skill_dir / "scripts"
    scripts.mkdir()
    (scripts / "run.py").write_text("print('bundle')\n", encoding="utf-8")

    installer = SkillInstaller(tmp_path / "installed")
    installer.install(source)
    manager = SkillManager(user_roots=(tmp_path / "installed",))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = manager.discover(workspace)
    skill = snapshot.get("bundle-demo")
    assert skill is not None

    assert "reference.md" in list_skill_files(skill)
    assert read_skill_resource(skill, "reference.md") == "REFERENCE TEXT\n"
    staged = stage_skill_bundle(skill, workspace)
    assert staged == workspace / ".loom" / "skill-runs" / "bundle-demo"
    assert (staged / "scripts" / "run.py").read_text(encoding="utf-8") == "print('bundle')\n"
    assert (staged / ".loom-staged-skill.json").is_file()


def test_loom_skill_dispatch_does_not_require_model_credentials(tmp_path: Path, capsys):
    source = tmp_path / "source"
    _write_skill(source, "cli-demo")
    home = tmp_path / "loom-home"

    rc = dispatch_main(["skill", "--home", str(home), "install", str(source)])
    out = capsys.readouterr().out

    assert rc == 0
    assert "Installed cli-demo" in out
    assert (home / "skills" / "cli-demo" / "SKILL.md").is_file()
