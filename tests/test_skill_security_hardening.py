from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import pytest

from app.agent_runtime.skill_bundle import read_skill_resource, stage_skill_bundle
from app.agent_runtime.skill_installer import SkillInstallError, SkillInstaller
from app.agent_runtime.skills import SkillDefinition, SkillError, SkillScope


def _write_skill(root: Path, name: str = "demo") -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: Security test workflow for {name}\n"
        "---\n"
        "Follow the workflow.\n",
        encoding="utf-8",
    )
    return directory


def _definition(skill_dir: Path, root: Path, name: str = "demo") -> SkillDefinition:
    return SkillDefinition(
        name=name,
        description=f"Security test workflow for {name}",
        short_description="",
        path=(skill_dir / "SKILL.md").resolve(),
        root=root.resolve(),
        scope=SkillScope.USER,
    )


def test_remote_install_rejects_non_https_and_non_github_sources(tmp_path: Path):
    installer = SkillInstaller(tmp_path / "skills", downloader=lambda _url: b"")

    for source in (
        "http://github.com/acme/skills",
        "git://github.com/acme/skills.git",
        "ssh://git@github.com/acme/skills.git",
        "https://example.com/skills.zip",
    ):
        with pytest.raises(SkillInstallError):
            installer.install(source)


def test_github_tree_install_uses_archive_download_without_git(tmp_path: Path):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "skills-main/pdf/SKILL.md",
            "---\nname: pdf\ndescription: Work with PDF documents\n---\nRead references first.\n",
        )
        bundle.writestr("skills-main/pdf/references/guide.md", "safe reference\n")
        bundle.writestr(
            "skills-main/other/SKILL.md",
            "---\nname: other\ndescription: Another skill\n---\nOther.\n",
        )
    payload = archive.getvalue()
    requested: list[str] = []

    def download(url: str) -> bytes:
        requested.append(url)
        return payload

    installer = SkillInstaller(tmp_path / "skills", downloader=download)
    rows = installer.install("https://github.com/acme/skills/tree/main/pdf")

    assert [row.name for row in rows] == ["pdf"]
    assert requested == ["https://github.com/acme/skills/archive/main.zip"]
    assert (rows[0].path / "references" / "guide.md").read_text(encoding="utf-8") == "safe reference\n"
    manifest = json.loads((rows[0].path / ".loom-skill.json").read_text(encoding="utf-8"))
    assert manifest["source_kind"] == "github"
    assert manifest["execution_policy"] == "inert-on-install"
    assert len(manifest["content_sha256"]) == 64


def test_force_and_remove_refuse_unmanaged_skills(tmp_path: Path):
    root = tmp_path / "skills"
    _write_skill(root, "manual")
    source_root = tmp_path / "source"
    _write_skill(source_root, "manual")
    installer = SkillInstaller(root)

    with pytest.raises(SkillInstallError, match="unmanaged"):
        installer.install(source_root, force=True)
    with pytest.raises(SkillInstallError, match="unmanaged"):
        installer.remove("manual")
    assert (root / "manual" / "SKILL.md").is_file()


def test_update_all_skips_unmanaged_skills(tmp_path: Path):
    root = tmp_path / "skills"
    _write_skill(root, "manual")
    installer = SkillInstaller(root)

    assert installer.update_all() == ()
    assert (root / "manual" / "SKILL.md").is_file()


def test_installer_rejects_native_executable_payload(tmp_path: Path):
    source_root = tmp_path / "source"
    skill_dir = _write_skill(source_root)
    (skill_dir / "payload.exe").write_bytes(b"MZ-not-executed")

    with pytest.raises(SkillInstallError, match="native executable"):
        SkillInstaller(tmp_path / "skills").install(source_root)


def test_installer_rejects_symlinked_bundle_content_when_supported(tmp_path: Path):
    source_root = tmp_path / "source"
    skill_dir = _write_skill(source_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("outside", encoding="utf-8")
    link = skill_dir / "references"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not supported on this platform")

    with pytest.raises(SkillInstallError, match="symlink"):
        SkillInstaller(tmp_path / "skills").install(source_root)


def test_installer_strips_executable_bits_on_copied_scripts(tmp_path: Path):
    if os.name == "nt":
        pytest.skip("Unix executable bits do not apply on Windows")
    source_root = tmp_path / "source"
    skill_dir = _write_skill(source_root)
    script = skill_dir / "scripts" / "run.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho safe\n", encoding="utf-8")
    script.chmod(0o755)

    row = SkillInstaller(tmp_path / "skills").install(source_root)[0]

    assert (row.path / "scripts" / "run.sh").stat().st_mode & 0o111 == 0


def test_skill_resource_rejects_symlink_even_when_target_is_inside_bundle(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _write_skill(root)
    real = skill_dir / "reference.md"
    real.write_text("reference", encoding="utf-8")
    link = skill_dir / "alias.md"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation is not supported on this platform")
    skill = _definition(skill_dir, root)

    with pytest.raises(SkillError, match="symlink"):
        read_skill_resource(skill, "alias.md")


def test_skill_resource_blocks_internal_metadata_at_any_depth(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _write_skill(root)
    nested = skill_dir / "references"
    nested.mkdir()
    (nested / ".loom-skill.json").write_text("{}", encoding="utf-8")
    skill = _definition(skill_dir, root)

    with pytest.raises(SkillError, match="internal skill metadata"):
        read_skill_resource(skill, "references/.loom-skill.json")


def test_staging_rejects_workspace_loom_symlink_escape_when_supported(tmp_path: Path):
    root = tmp_path / "skills"
    skill_dir = _write_skill(root)
    skill = _definition(skill_dir, root)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (workspace / ".loom").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is not supported on this platform")

    with pytest.raises(SkillError, match="symlink"):
        stage_skill_bundle(skill, workspace)
    assert not (outside / "skill-runs").exists()


def test_staging_strips_executable_bits_and_does_not_copy_install_manifest(tmp_path: Path):
    if os.name == "nt":
        pytest.skip("Unix executable bits do not apply on Windows")
    source_root = tmp_path / "source"
    skill_dir = _write_skill(source_root)
    script = skill_dir / "scripts" / "run.py"
    script.parent.mkdir()
    script.write_text("print('safe')\n", encoding="utf-8")
    script.chmod(0o755)
    installed = SkillInstaller(tmp_path / "installed").install(source_root)[0]
    skill = _definition(installed.path, tmp_path / "installed")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    staged = stage_skill_bundle(skill, workspace)

    assert (staged / "scripts" / "run.py").stat().st_mode & 0o111 == 0
    assert not (staged / ".loom-skill.json").exists()
    marker = json.loads((staged / ".loom-staged-skill.json").read_text(encoding="utf-8"))
    assert marker["execution_policy"] == "inert-staging"
