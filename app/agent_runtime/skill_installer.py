from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from .skills import SkillError, parse_skill_document


_MANIFEST_NAME = ".loom-skill.json"
_STAGE_MARKER_NAME = ".loom-staged-skill.json"
_RESERVED_METADATA_FILES = frozenset({_MANIFEST_NAME, _STAGE_MARKER_NAME})
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_BUNDLE_BYTES = 64 * 1024 * 1024
_MAX_BUNDLE_FILES = 2048
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_SKILL_FILE_BYTES = 128 * 1024
_IGNORED_DIRS = frozenset({".git", ".hg", ".svn", "__pycache__", "node_modules"})
_BLOCKED_BINARY_SUFFIXES = frozenset(
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
_GITHUB_SOURCE_HOSTS = frozenset({"github.com", "www.github.com"})
_GITHUB_DOWNLOAD_HOSTS = frozenset({"github.com", "www.github.com", "codeload.github.com"})
_GITHUB_PART_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
)
_PORTABLE_FORBIDDEN_CHARS = frozenset('< >"|?*'.replace(" ", ""))


class SkillInstallError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InstalledSkill:
    name: str
    path: Path
    description: str
    short_description: str
    source: str = ""
    source_kind: str = ""
    installed_at: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "path": str(self.path),
            "description": self.description,
            "short_description": self.short_description,
            "source": self.source,
            "source_kind": self.source_kind,
            "installed_at": self.installed_at,
        }


@dataclass(frozen=True, slots=True)
class _SourceTree:
    root: Path
    source: str
    source_kind: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    name: str
    description: str
    short_description: str
    skill_file: Path
    relative_dir: str


@dataclass(frozen=True, slots=True)
class _GitHubArchive:
    archive_url: str
    subpath: str
    source: str


DownloadFunc = Callable[[str], bytes]


class SkillInstaller:
    """Install and manage inert Agent Skills bundles under one user root.

    Remote installation is deliberately narrow: public HTTPS GitHub repository,
    tree, or SKILL.md blob URLs only. Installation never runs package content,
    git, dependency managers, hooks, or bundled scripts.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        max_archive_bytes: int = _MAX_ARCHIVE_BYTES,
        max_bundle_bytes: int = _MAX_BUNDLE_BYTES,
        max_bundle_files: int = _MAX_BUNDLE_FILES,
        max_file_bytes: int = _MAX_FILE_BYTES,
        downloader: DownloadFunc | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.max_archive_bytes = max(1024, int(max_archive_bytes))
        self.max_bundle_bytes = max(1024, int(max_bundle_bytes))
        self.max_bundle_files = max(1, int(max_bundle_files))
        self.max_file_bytes = max(1024, int(max_file_bytes))
        self.downloader = downloader or self._download_github_archive

    def install(
        self,
        source: str | Path,
        *,
        names: Iterable[str] = (),
        install_all: bool = False,
        force: bool = False,
    ) -> tuple[InstalledSkill, ...]:
        requested = tuple(str(name).strip() for name in names if str(name).strip())
        if requested and install_all:
            raise SkillInstallError("choose either explicit skill names or install_all, not both")

        self.root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="loom-skill-source-") as temp_dir:
            tree = self._acquire_source(source, Path(temp_dir))
            candidates = self._discover_candidates(tree.root)
            selected = self._select_candidates(candidates, requested=requested, install_all=install_all)
            return tuple(
                self._install_candidate(
                    candidate,
                    source=tree.source,
                    source_kind=tree.source_kind,
                    force=force,
                )
                for candidate in selected
            )

    def update(self, name: str) -> InstalledSkill:
        current = self.get(name)
        manifest = self._read_manifest(current.path)
        source = str(manifest.get("source") or "").strip()
        if not source:
            raise SkillInstallError(f"skill {name!r} has no recorded source and cannot be updated")
        result = self.install(source, names=(current.name,), force=True)
        if len(result) != 1:
            raise SkillInstallError(f"unexpected update result for {name!r}")
        return result[0]

    def update_all(self) -> tuple[InstalledSkill, ...]:
        updated: list[InstalledSkill] = []
        errors: list[str] = []
        for skill in self.list():
            if not self._is_managed(skill.path):
                continue
            try:
                updated.append(self.update(skill.name))
            except SkillInstallError as exc:
                errors.append(f"{skill.name}: {exc}")
        if errors:
            raise SkillInstallError(f"some skills could not be updated: {'; '.join(errors)}")
        return tuple(updated)

    def remove(self, name: str) -> bool:
        skill = self.get(name, required=False)
        if skill is None:
            return False
        if not self._is_managed(skill.path):
            raise SkillInstallError(
                f"refusing to remove unmanaged skill {skill.name!r}; remove it manually if intended"
            )
        if skill.path.is_symlink():
            raise SkillInstallError(f"refusing to remove symlinked skill: {skill.name}")
        resolved = skill.path.resolve(strict=True)
        try:
            resolved.relative_to(self.root.resolve(strict=True))
        except ValueError as exc:
            raise SkillInstallError("refusing to remove a skill outside the configured root") from exc
        shutil.rmtree(resolved)
        return True

    def list(self) -> tuple[InstalledSkill, ...]:
        if not self.root.is_dir():
            return ()
        rows: list[InstalledSkill] = []
        for child in sorted(self.root.iterdir(), key=lambda path: path.name.casefold()):
            if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
                continue
            skill_file = child / "SKILL.md"
            if skill_file.is_symlink() or not skill_file.is_file():
                continue
            try:
                parsed = parse_skill_document(
                    skill_file.read_text(encoding="utf-8"),
                    default_name=child.name,
                )
            except (OSError, UnicodeError, SkillError):
                continue
            manifest = self._read_manifest(child, required=False)
            rows.append(
                InstalledSkill(
                    name=parsed.name,
                    path=child,
                    description=parsed.description,
                    short_description=parsed.short_description,
                    source=str(manifest.get("source") or ""),
                    source_kind=str(manifest.get("source_kind") or ""),
                    installed_at=str(manifest.get("installed_at") or ""),
                )
            )
        return tuple(rows)

    def search(self, query: str, *, limit: int = 20) -> tuple[InstalledSkill, ...]:
        tokens = tuple(part.casefold() for part in str(query or "").split() if part.strip())
        if not tokens:
            raise SkillInstallError("skill search query must not be empty")
        scored: list[tuple[int, str, InstalledSkill]] = []
        for skill in self.list():
            haystack = " ".join((skill.name, skill.short_description, skill.description)).casefold()
            score = sum(1 for token in tokens if token in haystack)
            if score:
                scored.append((score, skill.name.casefold(), skill))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in scored[: max(1, min(100, int(limit)))])

    def get(self, name: str, *, required: bool = True) -> InstalledSkill | None:
        wanted = str(name or "").strip().casefold()
        for skill in self.list():
            if skill.name.casefold() == wanted:
                return skill
        if required:
            raise SkillInstallError(f"skill not installed: {name}")
        return None

    def _install_candidate(
        self,
        candidate: _Candidate,
        *,
        source: str,
        source_kind: str,
        force: bool,
    ) -> InstalledSkill:
        target = self.root / candidate.name
        if target.is_symlink():
            raise SkillInstallError(f"refusing to replace symlinked skill path: {candidate.name}")
        if target.exists():
            if not force:
                raise SkillInstallError(
                    f"skill {candidate.name!r} is already installed; use --force to replace it"
                )
            if not self._is_managed(target):
                raise SkillInstallError(
                    f"refusing to replace unmanaged skill {candidate.name!r}; relocate it manually first"
                )

        temporary = self.root / f".{candidate.name}.install-{uuid.uuid4().hex}"
        backup = self.root / f".{candidate.name}.backup-{uuid.uuid4().hex}"
        installed_at = datetime.now(timezone.utc).isoformat()
        try:
            self._copy_bundle(candidate.skill_file.parent, temporary)
            digest = self._tree_digest(temporary)
            manifest = {
                "schema_version": 1,
                "name": candidate.name,
                "source": source,
                "source_kind": source_kind,
                "source_skill_dir": candidate.relative_dir,
                "installed_at": installed_at,
                "content_sha256": digest,
                "execution_policy": "inert-on-install",
            }
            self._write_text(
                temporary / _MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
            if target.exists():
                target.replace(backup)
            temporary.replace(target)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary, ignore_errors=True)
            if backup.exists() and not target.exists():
                backup.replace(target)
            raise

        return InstalledSkill(
            name=candidate.name,
            path=target,
            description=candidate.description,
            short_description=candidate.short_description,
            source=source,
            source_kind=source_kind,
            installed_at=installed_at,
        )

    def _copy_bundle(self, source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        total_bytes = 0
        total_files = 0
        source_root = source.resolve(strict=True)
        for current, directories, files in os.walk(source_root, topdown=True, followlinks=False):
            current_path = Path(current)
            safe_directories: list[str] = []
            for directory in directories:
                child = current_path / directory
                if child.is_symlink():
                    raise SkillInstallError(f"skill bundle contains a symlink directory: {child}")
                if directory in _IGNORED_DIRS:
                    continue
                if Path(directory).suffix.casefold() == ".app":
                    raise SkillInstallError(f"native application bundles are not allowed in skills: {child}")
                safe_directories.append(directory)
            directories[:] = safe_directories

            relative_dir = current_path.relative_to(source_root)
            output_dir = destination / relative_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            for filename in files:
                if filename in _RESERVED_METADATA_FILES:
                    continue
                src = current_path / filename
                if src.is_symlink():
                    raise SkillInstallError(f"skill bundle contains a symlink: {src}")
                if not src.is_file():
                    raise SkillInstallError(f"skill bundle contains a non-regular file: {src}")
                self._reject_blocked_binary(filename)
                size = src.stat().st_size
                total_files += 1
                total_bytes += size
                self._enforce_bundle_limits(filename, size, total_files, total_bytes)
                dst = output_dir / filename
                shutil.copyfile(src, dst)
                self._harden_file(dst)

    def _discover_candidates(self, root: Path) -> tuple[_Candidate, ...]:
        root = root.resolve(strict=True)
        paths = sorted(root.rglob("SKILL.md"), key=lambda path: str(path).casefold())
        candidates: list[_Candidate] = []
        seen: set[str] = set()
        for path in paths:
            relative_parts = path.relative_to(root).parts[:-1]
            if any(part in _IGNORED_DIRS or part == "__MACOSX" for part in relative_parts):
                continue
            if path.is_symlink():
                raise SkillInstallError(f"skill definition must not be a symlink: {path}")
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, ValueError) as exc:
                raise SkillInstallError(f"skill definition escapes source root: {path}") from exc
            if resolved.stat().st_size > _MAX_SKILL_FILE_BYTES:
                raise SkillInstallError(f"SKILL.md exceeds Loom's size limit: {path}")
            try:
                parsed = parse_skill_document(
                    resolved.read_text(encoding="utf-8"),
                    default_name=resolved.parent.name,
                )
            except (OSError, UnicodeError, SkillError) as exc:
                raise SkillInstallError(f"invalid skill at {path}: {exc}") from exc
            key = parsed.name.casefold()
            if key in seen:
                raise SkillInstallError(f"source contains duplicate skill name: {parsed.name}")
            seen.add(key)
            candidates.append(
                _Candidate(
                    name=parsed.name,
                    description=parsed.description,
                    short_description=parsed.short_description,
                    skill_file=resolved,
                    relative_dir=resolved.parent.relative_to(root).as_posix(),
                )
            )
        if not candidates:
            raise SkillInstallError("source does not contain any valid SKILL.md")
        return tuple(candidates)

    @staticmethod
    def _select_candidates(
        candidates: tuple[_Candidate, ...],
        *,
        requested: tuple[str, ...],
        install_all: bool,
    ) -> tuple[_Candidate, ...]:
        if requested:
            by_name = {candidate.name.casefold(): candidate for candidate in candidates}
            selected: list[_Candidate] = []
            missing: list[str] = []
            for name in requested:
                candidate = by_name.get(name.casefold())
                if candidate is None:
                    missing.append(name)
                else:
                    selected.append(candidate)
            if missing:
                available = ", ".join(candidate.name for candidate in candidates)
                raise SkillInstallError(
                    f"skill(s) not found in source: {', '.join(missing)}; available: {available}"
                )
            return tuple(selected)
        if len(candidates) == 1:
            return candidates
        if install_all:
            return candidates
        available = ", ".join(candidate.name for candidate in candidates[:30])
        suffix = "" if len(candidates) <= 30 else ", …"
        raise SkillInstallError(
            "source contains multiple skills; pass --all or --name <skill>. "
            f"Available: {available}{suffix}"
        )

    def _acquire_source(self, source: str | Path, temp_root: Path) -> _SourceTree:
        raw = str(source).strip()
        if not raw:
            raise SkillInstallError("skill source must not be empty")

        local_candidate = Path(raw).expanduser()
        if local_candidate.exists() or local_candidate.is_symlink():
            if local_candidate.is_symlink():
                raise SkillInstallError("skill source must not be a symlink")
            resolved = local_candidate.resolve(strict=True)
            if resolved.is_dir():
                return _SourceTree(resolved, str(resolved), "directory")
            if resolved.is_file() and resolved.suffix.casefold() == ".zip":
                if resolved.stat().st_size > self.max_archive_bytes:
                    raise SkillInstallError("skill ZIP exceeds the configured compressed size limit")
                extracted = temp_root / "archive"
                self._extract_zip_bytes(resolved.read_bytes(), extracted)
                return _SourceTree(self._unwrap_single_directory(extracted), str(resolved), "zip")
            raise SkillInstallError(f"unsupported local skill source: {resolved}")

        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme.casefold() != "https":
            raise SkillInstallError(
                "remote skill sources must use HTTPS GitHub URLs; git://, ssh:// and http:// are not accepted"
            )
        spec = self._parse_github_source(raw)
        payload = self.downloader(spec.archive_url)
        extracted = temp_root / "archive"
        self._extract_zip_bytes(payload, extracted)
        archive_root = self._unwrap_single_directory(extracted)
        root = self._resolve_subpath(archive_root, spec.subpath)
        return _SourceTree(root, spec.source, "github")

    def _parse_github_source(self, source: str) -> _GitHubArchive:
        parsed = urllib.parse.urlparse(source)
        if (parsed.hostname or "").casefold() not in _GITHUB_SOURCE_HOSTS:
            raise SkillInstallError("remote skill installation supports public github.com URLs only")
        if parsed.username or parsed.password or (parsed.port not in {None, 443}):
            raise SkillInstallError("GitHub skill URLs must not contain credentials or non-standard ports")
        pieces = [urllib.parse.unquote(piece) for piece in parsed.path.split("/") if piece]
        if len(pieces) < 2:
            raise SkillInstallError("GitHub skill URL must include owner and repository")
        owner, repo = pieces[0], pieces[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not _GITHUB_PART_RE.fullmatch(owner) or not _GITHUB_PART_RE.fullmatch(repo):
            raise SkillInstallError("invalid GitHub owner or repository name")

        ref = "HEAD"
        subpath = ""
        if len(pieces) > 2:
            mode = pieces[2]
            if mode not in {"tree", "blob"} or len(pieces) < 4:
                raise SkillInstallError(
                    "unsupported GitHub URL; use the repository root, /tree/<ref>/<path>, or a SKILL.md blob URL"
                )
            ref = pieces[3]
            tail = pieces[4:]
            if mode == "blob":
                if not tail or tail[-1].casefold() != "skill.md":
                    raise SkillInstallError("GitHub blob skill URLs must point to SKILL.md")
                tail = tail[:-1]
            subpath = "/".join(tail)
        if not ref or ref in {".", ".."}:
            raise SkillInstallError("invalid GitHub ref")
        quoted_owner = urllib.parse.quote(owner, safe="")
        quoted_repo = urllib.parse.quote(repo, safe="")
        quoted_ref = urllib.parse.quote(ref, safe="")
        archive_url = f"https://github.com/{quoted_owner}/{quoted_repo}/archive/{quoted_ref}.zip"
        canonical_path = f"/{owner}/{repo}"
        if len(pieces) > 2:
            canonical_path += "/" + "/".join(
                urllib.parse.quote(piece, safe="@._-") for piece in pieces[2:]
            )
        canonical = urllib.parse.urlunparse(("https", "github.com", canonical_path, "", "", ""))
        return _GitHubArchive(archive_url=archive_url, subpath=subpath, source=canonical)

    def _download_github_archive(self, url: str) -> bytes:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme.casefold() != "https" or (parsed.hostname or "").casefold() not in _GITHUB_DOWNLOAD_HOSTS:
            raise SkillInstallError("download host is not allowed")
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Loom-Skill-Installer/2",
                "Accept": "application/zip, application/octet-stream;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                final = urllib.parse.urlparse(response.geturl())
                if final.scheme.casefold() != "https" or (final.hostname or "").casefold() not in _GITHUB_DOWNLOAD_HOSTS:
                    raise SkillInstallError("GitHub download redirected to an untrusted host")
                length = response.headers.get("Content-Length")
                if length and int(length) > self.max_archive_bytes:
                    raise SkillInstallError("remote skill archive exceeds the configured size limit")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(min(1024 * 1024, self.max_archive_bytes - total + 1))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > self.max_archive_bytes:
                        raise SkillInstallError("remote skill archive exceeds the configured size limit")
                    chunks.append(chunk)
                return b"".join(chunks)
        except SkillInstallError:
            raise
        except Exception as exc:
            raise SkillInstallError(
                f"failed to download GitHub skill archive: {type(exc).__name__}: {exc}"
            ) from exc

    def _extract_zip_bytes(self, payload: bytes, destination: Path) -> None:
        if len(payload) > self.max_archive_bytes:
            raise SkillInstallError("skill ZIP exceeds the configured compressed size limit")
        destination.mkdir(parents=True, exist_ok=False)
        root = destination.resolve(strict=True)
        total_bytes = 0
        total_files = 0
        seen_paths: set[str] = set()
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise SkillInstallError("invalid skill ZIP archive") from exc
        with archive:
            for info in archive.infolist():
                raw_name = info.filename.replace("\\", "/")
                if not raw_name:
                    raise SkillInstallError("skill archive contains an empty path")
                relative = PurePosixPath(raw_name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise SkillInstallError(f"unsafe path in skill archive: {info.filename}")
                self._validate_portable_parts(relative.parts, label=info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise SkillInstallError(f"skill archive contains a symlink: {info.filename}")
                if info.is_dir():
                    continue
                normalized = "/".join(relative.parts)
                key = normalized.casefold()
                if key in seen_paths:
                    raise SkillInstallError(f"skill archive contains a duplicate path: {info.filename}")
                seen_paths.add(key)
                total_files += 1
                total_bytes += int(info.file_size)
                self._enforce_bundle_limits(
                    info.filename,
                    int(info.file_size),
                    total_files,
                    total_bytes,
                )
                output = destination.joinpath(*relative.parts)
                try:
                    output.parent.resolve(strict=False).relative_to(root)
                except ValueError as exc:
                    raise SkillInstallError(f"unsafe path in skill archive: {info.filename}") from exc
                output.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as src, output.open("wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                self._harden_file(output)

    def _enforce_bundle_limits(self, filename: str, size: int, file_count: int, total_bytes: int) -> None:
        if file_count > self.max_bundle_files:
            raise SkillInstallError(f"skill bundle exceeds {self.max_bundle_files} files")
        if size > self.max_file_bytes:
            raise SkillInstallError(f"skill file exceeds {self.max_file_bytes} bytes: {filename}")
        if total_bytes > self.max_bundle_bytes:
            raise SkillInstallError(f"skill bundle exceeds {self.max_bundle_bytes} bytes")

    @staticmethod
    def _unwrap_single_directory(root: Path) -> Path:
        entries = [item for item in root.iterdir() if item.name not in {"__MACOSX", ".DS_Store"}]
        if len(entries) == 1 and entries[0].is_dir() and not entries[0].is_symlink():
            return entries[0].resolve(strict=True)
        return root.resolve(strict=True)

    @staticmethod
    def _resolve_subpath(root: Path, subpath: str) -> Path:
        if not subpath:
            return root.resolve(strict=True)
        relative = PurePosixPath(subpath.replace("\\", "/"))
        if relative.is_absolute() or ".." in relative.parts:
            raise SkillInstallError("GitHub skill subpath escapes the repository")
        SkillInstaller._validate_portable_parts(relative.parts, label=subpath)
        candidate = root.joinpath(*relative.parts)
        current = root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise SkillInstallError("GitHub skill subpath contains a symlink")
        candidate = candidate.resolve(strict=True)
        try:
            candidate.relative_to(root.resolve(strict=True))
        except ValueError as exc:
            raise SkillInstallError("GitHub skill subpath escapes the repository") from exc
        if not candidate.is_dir():
            raise SkillInstallError(f"requested skill path is not a directory: {subpath}")
        return candidate

    @staticmethod
    def _validate_portable_parts(parts: Iterable[str], *, label: str) -> None:
        for part in parts:
            if not part or part in {".", ".."}:
                raise SkillInstallError(f"unsafe path component in skill source: {label}")
            if part.endswith((" ", ".")):
                raise SkillInstallError(f"non-portable path component in skill source: {label}")
            if any(
                ord(char) < 32 or char == ":" or char in _PORTABLE_FORBIDDEN_CHARS
                for char in part
            ):
                raise SkillInstallError(f"non-portable path component in skill source: {label}")
            stem = part.split(".", 1)[0].upper()
            if stem in _WINDOWS_RESERVED_NAMES:
                raise SkillInstallError(f"Windows-reserved path component in skill source: {label}")

    @staticmethod
    def _reject_blocked_binary(filename: str) -> None:
        if Path(filename).suffix.casefold() in _BLOCKED_BINARY_SUFFIXES:
            raise SkillInstallError(
                f"native executable content is not allowed in skill bundles: {filename}"
            )

    @staticmethod
    def _harden_file(path: Path) -> None:
        try:
            path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _write_text(path: Path, text: str) -> None:
        path.write_text(text, encoding="utf-8")
        SkillInstaller._harden_file(path)

    @staticmethod
    def _tree_digest(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if not path.is_file() or path.name in _RESERVED_METADATA_FILES:
                continue
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            with path.open("rb") as handle:
                while chunk := handle.read(1024 * 1024):
                    digest.update(chunk)
        return digest.hexdigest()

    def _is_managed(self, path: Path) -> bool:
        return bool(self._read_manifest(path, required=False))

    @staticmethod
    def _read_manifest(path: Path, *, required: bool = True) -> dict[str, object]:
        manifest_path = path / _MANIFEST_NAME
        if manifest_path.is_symlink() or not manifest_path.is_file():
            if required:
                raise SkillInstallError(f"installed skill is missing {_MANIFEST_NAME}: {path}")
            return {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            if required:
                raise SkillInstallError(f"invalid skill manifest: {manifest_path}") from exc
            return {}
        if not isinstance(data, dict) or int(data.get("schema_version", 0)) != 1:
            if required:
                raise SkillInstallError(f"unsupported skill manifest: {manifest_path}")
            return {}
        return data


__all__ = [
    "InstalledSkill",
    "SkillInstallError",
    "SkillInstaller",
]
