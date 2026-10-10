from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path, PurePosixPath

from .memory_store import redact_secrets
from .skills import SkillDefinition, SkillError


_MAX_RESOURCE_BYTES = 256 * 1024
_MAX_STAGE_BYTES = 64 * 1024 * 1024
_MAX_STAGE_FILES = 2048
_MAX_STAGE_FILE_BYTES = 8 * 1024 * 1024
_INSTALL_MANIFEST = ".loom-skill.json"
_STAGE_MARKER = ".loom-staged-skill.json"
_RESERVED_METADATA_FILES = frozenset({_INSTALL_MANIFEST, _STAGE_MARKER})
_IGNORED_DIRECTORIES = frozenset({".git", ".hg", ".svn", "__pycache__", "node_modules"})
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)
_PORTABLE_FORBIDDEN_CHARS = frozenset('< >"|?*'.replace(" ", ""))
_BLOCKED_SUFFIXES = frozenset(
    {
        ".com",
        ".dll",
        ".dmg",
        ".dylib",
        ".exe",
        ".iso",
        ".msi",
        ".scr",
        ".so",
    }
)


def list_skill_files(skill: SkillDefinition, *, limit: int = 200) -> tuple[str, ...]:
    root = _bundle_root(skill)
    rows: list[str] = []
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for name in directories:
            child = current_path / name
            if name in _IGNORED_DIRECTORIES or child.is_symlink():
                continue
            safe_directories.append(name)
        directories[:] = safe_directories
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
    relative = _normalize_bundle_relative_path(relative_path)
    if not relative.parts:
        raise SkillError("skill resource path must not be empty")
    if any(part in _RESERVED_METADATA_FILES for part in relative.parts):
        raise SkillError("Loom internal skill metadata is not a readable skill resource")
    if any(part in _IGNORED_DIRECTORIES for part in relative.parts):
        raise SkillError("hidden/VCS dependency content is not exposed as a skill resource")

    target = _resolve_without_symlinks(root, relative, label="skill resource")
    if not target.is_file():
        raise SkillError("skill resource is not a regular file")
    size = target.stat().st_size
    if size > max(1024, int(max_bytes)):
        raise SkillError("skill resource exceeds the configured text size limit")
    payload = target.read_bytes()
    if b"\x00" in payload:
        raise SkillError("skill resource appears to be binary; stage the bundle to use binary assets")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
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

    loom_dir = workspace_root / ".loom"
    _ensure_safe_directory(
        loom_dir,
        workspace_root,
        create=True,
        label="workspace .loom directory",
    )
    stage_parent = loom_dir / "skill-runs"
    _ensure_safe_directory(
        stage_parent,
        workspace_root,
        create=True,
        label="skill staging directory",
    )

    target = stage_parent / skill.name
    if target.is_symlink():
        raise SkillError(f"refusing to replace symlinked staging directory: {target}")
    if target.exists():
        if not target.is_dir():
            raise SkillError(f"refusing to replace non-directory staging path: {target}")
        if not _valid_stage_marker(target / _STAGE_MARKER, expected_name=skill.name):
            raise SkillError(
                f"refusing to replace unrecognized staging directory: {target}"
            )

    temporary = stage_parent / f".{skill.name}.stage-{uuid.uuid4().hex}"
    backup = stage_parent / f".{skill.name}.backup-{uuid.uuid4().hex}"
    temporary.mkdir(parents=False, exist_ok=False)
    try:
        _copy_bundle_for_stage(
            source,
            temporary,
            max_bytes=max(1024, int(max_bytes)),
            max_files=max(1, int(max_files)),
        )
        marker_payload = {
            "schema_version": 1,
            "name": skill.name,
            "source": str(skill.path),
            "execution_policy": "inert-staging",
        }
        marker = temporary / _STAGE_MARKER
        marker.write_text(
            json.dumps(marker_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _harden_file(marker)

        if target.exists():
            target.replace(backup)
        temporary.replace(target)
        if backup.exists():
            shutil.rmtree(backup, ignore_errors=True)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    return target


def _copy_bundle_for_stage(
    source: Path,
    destination: Path,
    *,
    max_bytes: int,
    max_files: int,
) -> None:
    total_bytes = 0
    total_files = 0
    source_root = source.resolve(strict=True)
    destination_root = destination.resolve(strict=True)

    for current, directories, files in os.walk(source_root, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for directory in directories:
            child = current_path / directory
            if child.is_symlink():
                raise SkillError(f"skill bundle contains a symlink: {child}")
            if directory in _IGNORED_DIRECTORIES:
                continue
            if child.suffix.casefold() == ".app":
                raise SkillError(
                    f"native application bundles are not allowed in staged skills: {child}"
                )
            safe_directories.append(directory)
        directories[:] = safe_directories

        relative_dir = current_path.relative_to(source_root)
        output_dir = destination_root / relative_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        _ensure_path_inside(output_dir, destination_root, "staged skill path")

        for filename in files:
            if filename in _RESERVED_METADATA_FILES:
                continue
            src = current_path / filename
            if src.is_symlink():
                raise SkillError(f"skill bundle contains a symlink: {src}")
            if not src.is_file():
                continue
            _reject_native_payload(src)
            size = src.stat().st_size
            total_files += 1
            total_bytes += size
            if total_files > max_files:
                raise SkillError("skill bundle contains too many files to stage")
            if size > _MAX_STAGE_FILE_BYTES:
                raise SkillError(f"skill bundle file is too large to stage: {src.name}")
            if total_bytes > max_bytes:
                raise SkillError("skill bundle is too large to stage")
            dst = output_dir / filename
            _ensure_path_inside(dst, destination_root, "staged skill path")
            shutil.copyfile(src, dst)
            _harden_file(dst)


def _normalize_bundle_relative_path(value: str) -> PurePosixPath:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return PurePosixPath()
    relative = PurePosixPath(raw)
    if relative.is_absolute() or ".." in relative.parts:
        raise SkillError("skill resource path must remain inside the skill bundle")
    _validate_portable_parts(relative.parts)
    return relative


def _validate_portable_parts(parts: tuple[str, ...]) -> None:
    for part in parts:
        if not part or part in {".", ".."} or part.endswith((" ", ".")):
            raise SkillError("skill resource path contains a non-portable path component")
        if any(
            ord(char) < 32 or char == ":" or char in _PORTABLE_FORBIDDEN_CHARS
            for char in part
        ):
            raise SkillError("skill resource path contains a non-portable path component")
        stem = part.split(".", 1)[0].upper()
        if stem in _WINDOWS_RESERVED_NAMES:
            raise SkillError("skill resource path contains a Windows-reserved component")


def _valid_stage_marker(path: Path, *, expected_name: str) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return bool(
        isinstance(payload, dict)
        and int(payload.get("schema_version", 0)) == 1
        and str(payload.get("name") or "") == expected_name
        and str(payload.get("execution_policy") or "") == "inert-staging"
    )


def _resolve_without_symlinks(
    root: Path,
    relative: PurePosixPath,
    *,
    label: str,
) -> Path:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SkillError(f"{label} may not traverse symlinks")
    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise SkillError(f"{label} does not exist: {relative.as_posix()}") from exc
    _ensure_path_inside(resolved, root, label)
    return resolved


def _ensure_safe_directory(
    path: Path,
    workspace_root: Path,
    *,
    create: bool,
    label: str,
) -> Path:
    if path.is_symlink():
        raise SkillError(f"{label} must not be a symlink")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not path.is_dir():
        raise SkillError(f"{label} is not a directory")
    resolved = path.resolve(strict=True)
    _ensure_path_inside(resolved, workspace_root, label)
    return resolved


def _ensure_path_inside(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise SkillError(f"{label} escapes its allowed root") from exc


def _reject_native_payload(path: Path) -> None:
    if path.suffix.casefold() in _BLOCKED_SUFFIXES:
        raise SkillError(
            f"native executable content is not allowed in skill bundles: {path.name}"
        )


def _harden_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _bundle_root(skill: SkillDefinition) -> Path:
    root = skill.root.resolve(strict=True)
    skill_path = skill.path
    if skill_path.is_symlink():
        raise SkillError("SKILL.md must not be a symlink")
    bundle = skill_path.resolve(strict=True).parent
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
