from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from app.agent_runtime.skill_installer import SkillInstallError, SkillInstaller


def _write_skill(root: Path, name: str = "demo") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Demo skill used by tests\n"
        "---\n"
        "Follow the workflow.\n",
        encoding="utf-8",
    )
    return root


def test_install_local_bundle_is_inert_and_managed(tmp_path: Path):
    source = _write_skill(tmp_path / "source", "demo")
    scripts = source / "scripts"
    scripts.mkdir()
    script = scripts / "run.py"
    script.write_text("raise SystemExit('must never run during install')\n", encoding="utf-8")
    if os.name != "nt":
        script.chmod(0o755)

    installer = SkillInstaller(tmp_path / "home" / "skills")
    result = installer.install(source)

    assert result.name == "demo"
    assert result.replaced is False
    assert (result.path / "scripts" / "run.py").read_text(encoding="utf-8").startswith("raise SystemExit")
    metadata = json.loads((result.path / ".loom-skill.json").read_text(encoding="utf-8"))
    assert metadata["execution_policy"] == "inert-on-install"
    assert metadata["source"] == str(source.resolve())
    if os.name != "nt":
        assert (result.path / "scripts" / "run.py").stat().st_mode & 0o111 == 0


def test_update_reloads_recorded_local_source_atomically(tmp_path: Path):
    source = _write_skill(tmp_path / "source", "demo")
    payload = source / "reference.txt"
    payload.write_text("v1", encoding="utf-8")
    installer = SkillInstaller(tmp_path / "skills")
    first = installer.install(source)
    payload.write_text("v2", encoding="utf-8")

    second = installer.update("demo")

    assert second.replaced is True
    assert second.content_sha256 != first.content_sha256
    assert (second.path / "reference.txt").read_text(encoding="utf-8") == "v2"


def test_refuses_to_overwrite_or_remove_unmanaged_skill(tmp_path: Path):
    root = tmp_path / "skills"
    unmanaged = _write_skill(root / "demo", "demo")
    source = _write_skill(tmp_path / "source", "demo")
    installer = SkillInstaller(root)

    with pytest.raises(SkillInstallError, match="unmanaged"):
        installer.install(source, force=True)
    with pytest.raises(SkillInstallError, match="unmanaged"):
        installer.remove("demo")
    assert unmanaged.is_dir()


def test_zip_path_traversal_is_rejected(tmp_path: Path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("../escape.txt", "nope")

    with pytest.raises(SkillInstallError, match="unsafe ZIP path"):
        SkillInstaller(tmp_path / "skills").install(archive)
    assert not (tmp_path / "escape.txt").exists()


def test_native_executable_content_is_rejected(tmp_path: Path):
    source = _write_skill(tmp_path / "source", "demo")
    (source / "payload.exe").write_bytes(b"MZ-not-really-an-executable")

    with pytest.raises(SkillInstallError, match="native executable"):
        SkillInstaller(tmp_path / "skills").install(source)


def test_bundle_symlink_is_rejected_when_supported(tmp_path: Path):
    source = _write_skill(tmp_path / "source", "demo")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = source / "reference.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not supported")

    with pytest.raises(SkillInstallError, match="symlinks are not allowed"):
        SkillInstaller(tmp_path / "skills").install(source)


def test_github_tree_url_downloads_archive_and_selects_subdirectory(tmp_path: Path):
    archive = tmp_path / "repo.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(
            "skills-main/pdf/SKILL.md",
            "---\nname: pdf\ndescription: Work with PDF documents\n---\nRead the references.\n",
        )
        handle.writestr("skills-main/pdf/references/guide.md", "safe reference")
        handle.writestr(
            "skills-main/other/SKILL.md",
            "---\nname: other\ndescription: Another skill\n---\nOther.\n",
        )
    payload = archive.read_bytes()
    requested: list[str] = []

    def download(url: str) -> bytes:
        requested.append(url)
        return payload

    installer = SkillInstaller(tmp_path / "skills", downloader=download)
    result = installer.install("https://github.com/acme/skills/tree/main/pdf")

    assert result.name == "pdf"
    assert requested == ["https://github.com/acme/skills/archive/main.zip"]
    assert (result.path / "references" / "guide.md").read_text(encoding="utf-8") == "safe reference"


def test_repo_with_multiple_skills_requires_specific_subdirectory(tmp_path: Path):
    archive = tmp_path / "repo.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr(
            "skills-main/a/SKILL.md",
            "---\nname: a\ndescription: Skill A\n---\nA\n",
        )
        handle.writestr(
            "skills-main/b/SKILL.md",
            "---\nname: b\ndescription: Skill B\n---\nB\n",
        )
    installer = SkillInstaller(tmp_path / "skills", downloader=lambda _url: archive.read_bytes())

    with pytest.raises(SkillInstallError, match="multiple skills"):
        installer.install("https://github.com/acme/skills")
