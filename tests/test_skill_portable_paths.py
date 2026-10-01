from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.agent_runtime.skill_bundle import stage_skill_bundle
from app.agent_runtime.skill_installer import SkillInstallError, SkillInstaller
from app.agent_runtime.skills import SkillDefinition, SkillError, SkillScope


def test_zip_rejects_windows_reserved_path_component(tmp_path: Path):
    archive = tmp_path / "reserved.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "bundle/CON/SKILL.md",
            "---\nname: demo\ndescription: Demo skill\n---\nDo it.\n",
        )

    with pytest.raises(SkillInstallError, match="Windows-reserved"):
        SkillInstaller(tmp_path / "skills").install(archive)


def test_staging_requires_valid_loom_marker_before_replacement(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = root / "demo"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\nname: demo\ndescription: Demo skill\n---\nDo it.\n",
        encoding="utf-8",
    )
    skill = SkillDefinition(
        name="demo",
        description="Demo skill",
        short_description="",
        path=skill_file.resolve(),
        root=root.resolve(),
        scope=SkillScope.USER,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    staged = stage_skill_bundle(skill, workspace)
    marker = staged / ".loom-staged-skill.json"
    marker.write_text(json.dumps({"schema_version": 1, "name": "demo"}), encoding="utf-8")

    with pytest.raises(SkillError, match="unrecognized staging directory"):
        stage_skill_bundle(skill, workspace)
