from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from .memory_store import redact_secrets
from .skills import SkillDefinition, SkillError


_MAX_RESOURCE_BYTES = 256 * 1024
_MAX_STAGE_BYTES = 64 * 1024 * 1024
_MAX_STAGE_FILES = 2048
_INSTALL_MANIFEST = ".loom-skill.json"
_STAGE_MARKER = ".loom-staged-skill.json"
_RESERVED_METADATA_FILES = frozenset({_INSTALL_MANIFEST, _STAGE_MARKER})


def list_skill_files(skill: SkillDefinition, *, limit: int = 200) -> tuple[str, ...]:
    root = _bundle_root(skill)
    rows: list[str] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        directories[:] = [
            item
            for item in directories
            if item != ".git" and not (current_path / item).is_symlink()
        ]
        for filename in sorted(files, key=str.casefold):
            if filename in _RESERVED_METADATA_FILES:
                continue
            path = current_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            rows.append(path.relative_to(root).as_posix())
            if len(rows) >= max(1, int(limit)):
                return tuple(rows)
    return tuple(rows)


def read_skill_resource(
    skill: SkillDefinition,
    relative_path: str,
    *,
    max_bytes: int = _MAX_RESOURCE_BYTES,
) -> str:
    root = _bundle_root(skill)
    raw = str(relative_path or "").strip().replace("\\", "/")
    if not raw:
        raise SkillError("skill resource path must not be empty")
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise SkillError("skill resource path must remain inside the skill bundle")
    if relative.name in _RESERVED_METADATA_FILES:
        raise SkillError("Loom internal skill metadata is not a readable skill resource")
    resolved = (root / relative).resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SkillError("skill resource path escapes the skill bundle") from exc
    if not resolved.is_file():
        raise SkillError("skill resource is not a regular file")
    if resolved.stat().st_size > max(1024, int(max_bytes)):
        raise SkillError("skill resource exceeds the configured text size limit")
    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise SkillError(
            "skill resource is not UTF-8 text; stage the bundle to use binary assets"
        ) from exc
    return redact_secrets(text)


def stage_skill_bundle(
    skill: SkillDefinition,
    workspace: str | Path,
    *,
    max_bytes: int = _MAX_STAGE_BYTES,
    max_files: int = _MAX_STAGE_FILES,
) -> Path:
    source = _bundle_root(skill)
    workspace_root = Path(workspace).expanduser().resolve(strict=True)
    if not workspace_root.is_dir():
        raise SkillError("workspace is not a directory")

    stage_parent = workspace_root / ".loom" / "skill-runs"
    stage_parent.mkdir(parents=True, exist_ok=True)
    target = stage_parent / skill.name
    temporary = stage_parent / f".{skill.name}.stage-{uuid.uuid4().hex}"

    if target.exists():
        marker = target / _STAGE_MARKER
        if not marker.is_file():
            raise SkillError(
                f"refusing to replace unrecognized staging directory: {target}"
            )

    temporary.mkdir(parents=True, exist_ok=False)
    try:
        total_bytes = 0
        total_files = 0
        for current, directories, files in os.walk(source, topdown=True, followlinks=False):
            current_path = Path(current)
            safe_directories: list[str] = []
            for directory in directories:
                child = current_path / directory
                if child.is_symlink() or directory == ".git":
                    continue
                safe_directories.append(directory)
            directories[:] = safe_directories

            relative_dir = current_path.relative_to(source)
            output_dir = temporary / relative_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            for filename in files:
                if filename in _RESERVED_METADATA_FILES:
                    continue
                src = current_path / filename
                if src.is_symlink():
                    raise SkillError(f"skill bundle contains a symlink: {src}")
                if not src.is_file():
                    continue
                size = src.stat().st_size
                total_files += 1
                total_bytes += size
                if total_files > max(1, int(max_files)):
                    raise SkillError("skill bundle contains too many files to stage")
                if total_bytes > max(1024, int(max_bytes)):
                    raise SkillError("skill bundle is too large to stage")
                shutil.copy2(src, output_dir / filename)

        (temporary / _STAGE_MARKER).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "name": skill.name,
                    "source": str(skill.path),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if target.exists():
            shutil.rmtree(target)
        temporary.replace(target)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return target


def _bundle_root(skill: SkillDefinition) -> Path:
    root = skill.root.resolve(strict=True)
    bundle = skill.path.resolve(strict=True).parent
    try:
        bundle.relative_to(root)
    except ValueError as exc:
        raise SkillError("skill bundle escapes its discovery root") from exc
    return bundle


__all__ = [
    "list_skill_files",
    "read_skill_resource",
    "stage_skill_bundle",
]
