from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import tempfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from .skills import SkillError, parse_skill_document


_METADATA_FILE = ".loom-skill.json"
_MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_FILES = 1024
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_SKILL_FILE_BYTES = 128 * 1024
_ALLOWED_REMOTE_HOSTS = {"github.com", "www.github.com", "codeload.github.com"}
_BLOCKED_SUFFIXES = {
    ".app",
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
_IGNORED_DIRS = {".git", ".hg", ".svn", "__pycache__", "node_modules"}


class SkillInstallError(SkillError):
    pass


@dataclass(frozen=True, slots=True)
class InstalledSkill:
    name: str
    path: Path
    source: str
    content_sha256: str
    managed: bool


@dataclass(frozen=True, slots=True)
class SkillInstallResult:
    name: str
    path: Path
    source: str
    content_sha256: str
    file_count: int
    total_bytes: int
    replaced: bool


@dataclass(frozen=True, slots=True)
class _GitHubArchiveSpec:
    archive_url: str
    subpath: str
    canonical_source: str


DownloadFunc = Callable[[str], bytes]


def default_skill_install_root(home: str | Path | None = None) -> Path:
    if home is None:
        configured = str(os.environ.get("LOOM_HOME") or "").strip()
        base = Path(configured).expanduser() if configured else Path.home() / ".loom"
    else:
        base = Path(home).expanduser()
    return base.resolve(strict=False) / "skills"


class SkillInstaller:
    """Install inert Agent Skills bundles without executing package content."""

    def __init__(
        self,
        root: str | Path,
        *,
        downloader: DownloadFunc | None = None,
        max_download_bytes: int = _MAX_DOWNLOAD_BYTES,
        max_archive_bytes: int = _MAX_ARCHIVE_BYTES,
        max_files: int = _MAX_FILES,
        max_file_bytes: int = _MAX_FILE_BYTES,
    ) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.downloader = downloader or self._download_https
        self.max_download_bytes = max(1024, int(max_download_bytes))
        self.max_archive_bytes = max(1024, int(max_archive_bytes))
        self.max_files = max(1, int(max_files))
        self.max_file_bytes = max(1024, int(max_file_bytes))

    def install(self, source: str | Path, *, force: bool = False) -> SkillInstallResult:
        raw_source = str(source).strip()
        if not raw_source:
            raise SkillInstallError("skill source must not be empty")

        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".loom-skill-stage-", dir=self.root) as temp_name:
            temp_root = Path(temp_name)
            skill_source, canonical_source = self._materialize_source(raw_source, temp_root)
            parsed = self._validate_skill_root(skill_source)
            destination = (self.root / parsed.name).resolve(strict=False)
            self._ensure_inside_root(destination)
            replaced = destination.exists() or destination.is_symlink()
            if replaced and not force:
                raise SkillInstallError(
                    f"skill {parsed.name!r} already exists; use --force or 'loom skill update {parsed.name}'"
                )
            if replaced and not self._is_managed_destination(destination):
                raise SkillInstallError(
                    f"refusing to replace unmanaged skill {parsed.name!r}; remove or relocate it manually"
                )

            staged = temp_root / "installed"
            file_count, total_bytes = self._copy_bundle(skill_source, staged)
            digest = self._tree_digest(staged)
            metadata = {
                "format_version": 1,
                "name": parsed.name,
                "source": canonical_source,
                "content_sha256": digest,
                "installed_at": datetime.now(timezone.utc).isoformat(),
                "file_count": file_count,
                "total_bytes": total_bytes,
                "execution_policy": "inert-on-install",
            }
            self._write_text_file(
                staged / _METADATA_FILE,
                json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
            )
            self._atomic_publish(staged, destination, replace=replaced)
            return SkillInstallResult(
                name=parsed.name,
                path=destination,
                source=canonical_source,
                content_sha256=digest,
                file_count=file_count,
                total_bytes=total_bytes,
                replaced=replaced,
            )

    def update(self, name: str) -> SkillInstallResult:
        destination = self._destination_for_name(name)
        metadata = self._read_metadata(destination)
        source = str(metadata.get("source") or "").strip()
        if not source:
            raise SkillInstallError(f"skill {name!r} does not record an update source")
        return self.install(source, force=True)

    def remove(self, name: str) -> Path:
        destination = self._destination_for_name(name)
        if not destination.exists() and not destination.is_symlink():
            raise SkillInstallError(f"skill not found: {name}")
        if not self._is_managed_destination(destination):
            raise SkillInstallError(f"refusing to remove unmanaged skill: {name}")
        if destination.is_symlink():
            raise SkillInstallError(f"refusing to remove symlinked skill: {name}")
        shutil.rmtree(destination)
        return destination

    def list_installed(self) -> tuple[InstalledSkill, ...]:
        if not self.root.is_dir():
            return ()
        records: list[InstalledSkill] = []
        for directory in sorted(self.root.iterdir(), key=lambda item: item.name.casefold()):
            if directory.name.startswith(".") or not directory.is_dir() or directory.is_symlink():
                continue
            skill_file = directory / "SKILL.md"
            if not skill_file.is_file():
                continue
            try:
                parsed = parse_skill_document(skill_file.read_text(encoding="utf-8"), default_name=directory.name)
            except (OSError, UnicodeError, SkillError):
                continue
            metadata = self._try_read_metadata(directory)
            records.append(
                InstalledSkill(
                    name=parsed.name,
                    path=directory.resolve(),
                    source=str(metadata.get("source") or "manual") if metadata else "manual",
                    content_sha256=str(metadata.get("content_sha256") or "") if metadata else "",
                    managed=metadata is not None,
                )
            )
        return tuple(records)

    def _materialize_source(self, source: str, temp_root: Path) -> tuple[Path, str]:
        parsed_url = urllib.parse.urlparse(source)
        if parsed_url.scheme:
            if parsed_url.scheme.casefold() != "https":
                raise SkillInstallError("remote skill sources must use HTTPS")
            spec = self._parse_github_url(source)
            archive = self.downloader(spec.archive_url)
            extracted = temp_root / "archive"
            self._safe_extract_zip(archive, extracted)
            archive_root = self._unwrap_single_directory(extracted)
            skill_root = self._locate_skill_root(archive_root, spec.subpath)
            return skill_root, spec.canonical_source

        local = Path(source).expanduser().resolve(strict=True)
        if local.is_symlink():
            raise SkillInstallError("skill source must not be a symlink")
        if local.is_dir():
            skill_root = self._locate_skill_root(local, "")
            return skill_root, str(local)
        if local.is_file() and local.suffix.casefold() == ".zip":
            if local.stat().st_size > self.max_download_bytes:
                raise SkillInstallError("skill ZIP exceeds the configured size limit")
            extracted = temp_root / "archive"
            self._safe_extract_zip(local.read_bytes(), extracted)
            skill_root = self._locate_skill_root(self._unwrap_single_directory(extracted), "")
            return skill_root, str(local)
        raise SkillInstallError("skill source must be a directory, .zip file, or public GitHub HTTPS URL")

    def _parse_github_url(self, source: str) -> _GitHubArchiveSpec:
        parsed = urllib.parse.urlparse(source)
        host = (parsed.hostname or "").casefold()
        if host not in {"github.com", "www.github.com"}:
            raise SkillInstallError("remote skill installation currently supports public github.com URLs only")
        parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise SkillInstallError("GitHub skill URL must include owner/repository")
        owner, repo = parts[0], parts[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not owner or not repo or any(value in {".", ".."} for value in (owner, repo)):
            raise SkillInstallError("invalid GitHub owner/repository")

        ref = "HEAD"
        subpath = ""
        if len(parts) > 2:
            mode = parts[2]
            if mode not in {"tree", "blob"} or len(parts) < 4:
                raise SkillInstallError("use a repository URL or a GitHub /tree/<ref>/<path> skill URL")
            ref = parts[3]
            tail = parts[4:]
            if mode == "blob" and tail and tail[-1].casefold() == "skill.md":
                tail = tail[:-1]
            subpath = "/".join(tail)
        quoted_owner = urllib.parse.quote(owner, safe="")
        quoted_repo = urllib.parse.quote(repo, safe="")
        quoted_ref = urllib.parse.quote(ref, safe="")
        archive_url = f"https://github.com/{quoted_owner}/{quoted_repo}/archive/{quoted_ref}.zip"
        canonical_source = source.split("#", 1)[0]
        return _GitHubArchiveSpec(archive_url, subpath, canonical_source)

    def _download_https(self, url: str) -> bytes:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.casefold() != "https" or (parsed.hostname or "").casefold() not in _ALLOWED_REMOTE_HOSTS:
            raise SkillInstallError("download host is not allowed")
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Loom-Skill-Installer/1",
                "Accept": "application/zip, application/octet-stream;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                final = urllib.parse.urlparse(response.geturl())
                if final.scheme.casefold() != "https" or (final.hostname or "").casefold() not in _ALLOWED_REMOTE_HOSTS:
                    raise SkillInstallError("GitHub download redirected to an untrusted host")
                header = response.headers.get("Content-Length")
                if header and int(header) > self.max_download_bytes:
                    raise SkillInstallError("skill download exceeds the configured size limit")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(1024 * 1024, self.max_download_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_download_bytes:
                        raise SkillInstallError("skill download exceeds the configured size limit")
                    chunks.append(chunk)
                return b"".join(chunks)
        except SkillInstallError:
            raise
        except Exception as exc:
            raise SkillInstallError(f"failed to download skill: {type(exc).__name__}: {exc}") from exc

    def _safe_extract_zip(self, payload: bytes, destination: Path) -> None:
        if len(payload) > self.max_download_bytes:
            raise SkillInstallError("skill ZIP exceeds the configured compressed size limit")
        destination.mkdir(parents=True, exist_ok=True)
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise SkillInstallError("skill source is not a valid ZIP archive") from exc

        total = 0
        file_count = 0
        with archive:
            for info in archive.infolist():
                raw_name = info.filename.replace("\\", "/")
                relative = PurePosixPath(raw_name)
                if not raw_name or relative.is_absolute() or ".." in relative.parts:
                    raise SkillInstallError(f"unsafe ZIP path: {info.filename!r}")
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise SkillInstallError(f"symlinks are not allowed in skill ZIPs: {info.filename}")
                if info.is_dir():
                    continue
                file_count += 1
                total += int(info.file_size)
                if file_count > self.max_files:
                    raise SkillInstallError("skill archive contains too many files")
                if info.file_size > self.max_file_bytes:
                    raise SkillInstallError(f"skill file is too large: {info.filename}")
                if total > self.max_archive_bytes:
                    raise SkillInstallError("skill archive expands beyond the configured size limit")
                self._reject_blocked_file(relative.name)
                target = destination.joinpath(*relative.parts)
                resolved_parent = target.parent.resolve(strict=False)
                try:
                    resolved_parent.relative_to(destination.resolve(strict=False))
                except ValueError as exc:
                    raise SkillInstallError(f"unsafe ZIP path: {info.filename!r}") from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source_handle, target.open("wb") as target_handle:
                    shutil.copyfileobj(source_handle, target_handle, length=1024 * 1024)
                self._harden_file(target)

    def _locate_skill_root(self, base: Path, subpath: str) -> Path:
        base = base.resolve(strict=True)
        if subpath:
            relative = PurePosixPath(subpath.replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise SkillInstallError("GitHub skill subpath escapes the repository")
            candidate = base.joinpath(*relative.parts).resolve(strict=True)
            try:
                candidate.relative_to(base)
            except ValueError as exc:
                raise SkillInstallError("GitHub skill subpath escapes the repository") from exc
            if candidate.is_file() and candidate.name.casefold() == "skill.md":
                candidate = candidate.parent
            if not (candidate / "SKILL.md").is_file():
                raise SkillInstallError(f"no SKILL.md found at requested path: {subpath}")
            return candidate

        direct = base / "SKILL.md"
        if direct.is_file():
            return base
        matches = [
            path
            for path in base.rglob("SKILL.md")
            if not any(part in _IGNORED_DIRS or part.startswith(".") for part in path.relative_to(base).parts[:-1])
        ]
        if not matches:
            raise SkillInstallError("no SKILL.md found in source")
        if len(matches) > 1:
            raise SkillInstallError(
                "source contains multiple skills; install a specific GitHub /tree/<ref>/<skill-path> URL"
            )
        return matches[0].parent.resolve()

    @staticmethod
    def _unwrap_single_directory(root: Path) -> Path:
        entries = [item for item in root.iterdir() if item.name not in {"__MACOSX", ".DS_Store"}]
        if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
            return entries[0]
        return root

    def _validate_skill_root(self, skill_root: Path):
        if skill_root.is_symlink() or not skill_root.is_dir():
            raise SkillInstallError("skill root must be a real directory")
        skill_file = skill_root / "SKILL.md"
        if not skill_file.is_file() or skill_file.is_symlink():
            raise SkillInstallError("skill root must contain a regular SKILL.md")
        if skill_file.stat().st_size > _MAX_SKILL_FILE_BYTES:
            raise SkillInstallError("SKILL.md exceeds Loom's size limit")
        try:
            return parse_skill_document(skill_file.read_text(encoding="utf-8"), default_name=skill_root.name)
        except (OSError, UnicodeError, SkillError) as exc:
            raise SkillInstallError(f"invalid SKILL.md: {exc}") from exc

    def _copy_bundle(self, source: Path, destination: Path) -> tuple[int, int]:
        destination.mkdir(parents=True, exist_ok=False)
        file_count = 0
        total_bytes = 0
        for current, dirs, files in os.walk(source, topdown=True, followlinks=False):
            current_path = Path(current)
            retained_dirs: list[str] = []
            for directory_name in dirs:
                child = current_path / directory_name
                if directory_name in _IGNORED_DIRS:
                    continue
                if child.is_symlink():
                    raise SkillInstallError(f"symlinks are not allowed in skill bundles: {child}")
                retained_dirs.append(directory_name)
            dirs[:] = retained_dirs
            relative_dir = current_path.relative_to(source)
            target_dir = destination / relative_dir
            target_dir.mkdir(parents=True, exist_ok=True)
            for filename in files:
                if filename == _METADATA_FILE:
                    continue
                source_file = current_path / filename
                if source_file.is_symlink() or not source_file.is_file():
                    raise SkillInstallError(f"only regular files are allowed in skill bundles: {source_file}")
                self._reject_blocked_file(filename)
                size = source_file.stat().st_size
                file_count += 1
                total_bytes += size
                if file_count > self.max_files:
                    raise SkillInstallError("skill bundle contains too many files")
                if size > self.max_file_bytes:
                    raise SkillInstallError(f"skill file is too large: {source_file}")
                if total_bytes > self.max_archive_bytes:
                    raise SkillInstallError("skill bundle exceeds the configured size limit")
                target_file = target_dir / filename
                shutil.copyfile(source_file, target_file)
                self._harden_file(target_file)
        return file_count, total_bytes

    @staticmethod
    def _reject_blocked_file(filename: str) -> None:
        if Path(filename).suffix.casefold() in _BLOCKED_SUFFIXES:
            raise SkillInstallError(f"native executable content is not allowed in skill bundles: {filename}")

    @staticmethod
    def _harden_file(path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _tree_digest(self, root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if not path.is_file() or path.name == _METADATA_FILE:
                continue
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        return digest.hexdigest()

    def _atomic_publish(self, staged: Path, destination: Path, *, replace: bool) -> None:
        backup: Path | None = None
        if replace:
            backup = destination.with_name(f".{destination.name}.loom-backup-{os.getpid()}")
            if backup.exists() or backup.is_symlink():
                raise SkillInstallError(f"temporary backup path already exists: {backup}")
            os.replace(destination, backup)
        try:
            os.replace(staged, destination)
        except Exception:
            if backup is not None and backup.exists() and not destination.exists():
                os.replace(backup, destination)
            raise
        else:
            if backup is not None:
                shutil.rmtree(backup, ignore_errors=True)

    def _destination_for_name(self, name: str) -> Path:
        value = str(name or "").strip()
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise SkillInstallError("invalid skill name")
        destination = (self.root / value).resolve(strict=False)
        self._ensure_inside_root(destination)
        return destination

    def _ensure_inside_root(self, path: Path) -> None:
        root = self.root.resolve(strict=False)
        try:
            path.resolve(strict=False).relative_to(root)
        except ValueError as exc:
            raise SkillInstallError("skill path escapes the install root") from exc

    def _is_managed_destination(self, destination: Path) -> bool:
        return destination.is_dir() and not destination.is_symlink() and (destination / _METADATA_FILE).is_file()

    def _try_read_metadata(self, destination: Path) -> dict[str, object] | None:
        try:
            return self._read_metadata(destination)
        except SkillInstallError:
            return None

    def _read_metadata(self, destination: Path) -> dict[str, object]:
        self._ensure_inside_root(destination)
        metadata_file = destination / _METADATA_FILE
        if not metadata_file.is_file() or metadata_file.is_symlink():
            raise SkillInstallError(f"skill is not managed by Loom: {destination.name}")
        try:
            payload = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SkillInstallError(f"invalid skill metadata for {destination.name}") from exc
        if not isinstance(payload, dict) or int(payload.get("format_version", 0)) != 1:
            raise SkillInstallError(f"unsupported skill metadata for {destination.name}")
        return payload

    @staticmethod
    def _write_text_file(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass


__all__ = [
    "InstalledSkill",
    "SkillInstallError",
    "SkillInstallResult",
    "SkillInstaller",
    "default_skill_install_root",
]
