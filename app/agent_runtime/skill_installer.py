from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .skills import SkillError, parse_skill_document


_MANIFEST_NAME = ".loom-skill.json"
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_BUNDLE_BYTES = 64 * 1024 * 1024
_MAX_BUNDLE_FILES = 2048
_ALLOWED_REMOTE_SCHEMES = frozenset({"https"})


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


class SkillInstaller:
    """Install and manage reusable Agent Skills bundles under one user root.

    Installation is intentionally inert: Loom copies files but never executes
    install hooks, dependency managers, or bundled scripts during installation.
    Remote acquisition is intentionally conservative: only HTTPS sources are
    accepted by default, and Git is run with prompts disabled.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        max_archive_bytes: int = _MAX_ARCHIVE_BYTES,
        max_bundle_bytes: int = _MAX_BUNDLE_BYTES,
        max_bundle_files: int = _MAX_BUNDLE_FILES,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.max_archive_bytes = max(1024, int(max_archive_bytes))
        self.max_bundle_bytes = max(1024, int(max_bundle_bytes))
        self.max_bundle_files = max(1, int(max_bundle_files))

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
            installed: list[InstalledSkill] = []
            for candidate in selected:
                installed.append(
                    self._install_candidate(
                        candidate,
                        source=tree.source,
                        source_kind=tree.source_kind,
                        force=force,
                    )
                )
            return tuple(installed)

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
            try:
                updated.append(self.update(skill.name))
            except SkillInstallError as exc:
                errors.append(f"{skill.name}: {exc}")
        if errors:
            detail = "; ".join(errors)
            raise SkillInstallError(f"some skills could not be updated: {detail}")
        return tuple(updated)

    def remove(self, name: str) -> bool:
        skill = self.get(name, required=False)
        if skill is None:
            return False
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
            if not child.is_dir():
                continue
            skill_file = child / "SKILL.md"
            if not skill_file.is_file():
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
            haystack = " ".join(
                (skill.name, skill.short_description, skill.description)
            ).casefold()
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
        if target.exists() and not force:
            raise SkillInstallError(
                f"skill {candidate.name!r} is already installed; use --force to replace it"
            )

        temporary = self.root / f".{candidate.name}.install-{uuid.uuid4().hex}"
        backup = self.root / f".{candidate.name}.backup-{uuid.uuid4().hex}"
        installed_at = datetime.now(timezone.utc).isoformat()
        try:
            self._copy_bundle(candidate.skill_file.parent, temporary)
            manifest = {
                "schema_version": 1,
                "name": candidate.name,
                "source": source,
                "source_kind": source_kind,
                "source_skill_dir": candidate.relative_dir,
                "installed_at": installed_at,
            }
            (temporary / _MANIFEST_NAME).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            if target.exists():
                target.replace(backup)
            temporary.replace(target)
            if backup.exists():
                shutil.rmtree(backup)
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
                    continue
                if directory == ".git":
                    continue
                safe_directories.append(directory)
            directories[:] = safe_directories

            relative_dir = current_path.relative_to(source_root)
            output_dir = destination / relative_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            for filename in files:
                src = current_path / filename
                if src.is_symlink():
                    raise SkillInstallError(f"skill bundle contains a symlink: {src}")
                if not src.is_file():
                    continue
                size = src.stat().st_size
                total_files += 1
                total_bytes += size
                if total_files > self.max_bundle_files:
                    raise SkillInstallError(
                        f"skill bundle exceeds {self.max_bundle_files} files"
                    )
                if total_bytes > self.max_bundle_bytes:
                    raise SkillInstallError(
                        f"skill bundle exceeds {self.max_bundle_bytes} bytes"
                    )
                dst = output_dir / filename
                shutil.copy2(src, dst)

    def _discover_candidates(self, root: Path) -> tuple[_Candidate, ...]:
        root = root.resolve(strict=True)
        paths = sorted(root.rglob("SKILL.md"), key=lambda path: str(path).casefold())
        candidates: list[_Candidate] = []
        seen: set[str] = set()
        for path in paths:
            if any(part in {".git", "__MACOSX"} for part in path.parts):
                continue
            if path.is_symlink():
                continue
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(root)
            except (OSError, ValueError):
                continue
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

        local = Path(raw).expanduser()
        if local.exists():
            resolved = local.resolve()
            if resolved.is_dir():
                return _SourceTree(resolved, str(resolved), "directory")
            if resolved.is_file() and zipfile.is_zipfile(resolved):
                extracted = temp_root / "archive"
                self._extract_zip(resolved, extracted)
                return _SourceTree(extracted, str(resolved), "zip")
            raise SkillInstallError(f"unsupported local skill source: {resolved}")

        parsed = urllib.parse.urlparse(raw)
        if not parsed.scheme:
            raise SkillInstallError(f"skill source does not exist: {raw}")
        if parsed.scheme.casefold() not in _ALLOWED_REMOTE_SCHEMES:
            raise SkillInstallError(
                "remote skill sources must use https:// URLs. "
                "For ssh://, git://, or http:// sources, clone/download them yourself and install the local directory or zip."
            )

        if self._looks_like_zip_url(parsed):
            archive = temp_root / "download.zip"
            self._download(raw, archive)
            extracted = temp_root / "archive"
            self._extract_zip(archive, extracted)
            return _SourceTree(extracted, raw, "remote-zip")

        clone_root = temp_root / "repo"
        repo_url, branch, subpath = self._normalize_git_source(raw)
        self._git_clone(repo_url, clone_root, branch=branch)
        root = clone_root / subpath if subpath else clone_root
        if not root.is_dir():
            raise SkillInstallError(
                f"requested skill path does not exist in cloned repository: {subpath}"
            )
        return _SourceTree(root.resolve(), raw, "git")

    @staticmethod
    def _looks_like_zip_url(parsed: urllib.parse.ParseResult) -> bool:
        return parsed.path.casefold().endswith(".zip")

    def _download(self, url: str, destination: Path) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "Loom-Skill-Installer/1"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > self.max_archive_bytes:
                    raise SkillInstallError("remote skill archive exceeds the configured size limit")
                total = 0
                with destination.open("wb") as handle:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.max_archive_bytes:
                            raise SkillInstallError(
                                "remote skill archive exceeds the configured size limit"
                            )
                        handle.write(chunk)
        except SkillInstallError:
            raise
        except Exception as exc:
            raise SkillInstallError(f"failed to download skill archive: {exc}") from exc

    def _extract_zip(self, archive: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=False)
        total_bytes = 0
        total_files = 0
        root = destination.resolve()
        try:
            with zipfile.ZipFile(archive) as bundle:
                for info in bundle.infolist():
                    name = info.filename.replace("\\", "/")
                    if not name or name.endswith("/"):
                        continue
                    relative = Path(name)
                    if relative.is_absolute() or ".." in relative.parts:
                        raise SkillInstallError(f"unsafe path in skill archive: {name}")
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == 0o120000:
                        raise SkillInstallError(f"skill archive contains a symlink: {name}")
                    total_files += 1
                    total_bytes += int(info.file_size)
                    if total_files > self.max_bundle_files:
                        raise SkillInstallError(
                            f"skill archive exceeds {self.max_bundle_files} files"
                        )
                    if total_bytes > self.max_archive_bytes:
                        raise SkillInstallError(
                            "skill archive exceeds the configured size limit"
                        )
                    output = (destination / relative).resolve()
                    try:
                        output.relative_to(root)
                    except ValueError as exc:
                        raise SkillInstallError(f"unsafe path in skill archive: {name}") from exc
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with bundle.open(info) as src, output.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile as exc:
            raise SkillInstallError(f"invalid skill zip archive: {archive}") from exc

    @staticmethod
    def _normalize_git_source(source: str) -> tuple[str, str, str]:
        parsed = urllib.parse.urlparse(source)
        if parsed.scheme.casefold() != "https":
            raise SkillInstallError("remote Git skill sources must use https:// URLs")
        if parsed.netloc.casefold() != "github.com":
            return source, "", ""

        pieces = [piece for piece in parsed.path.split("/") if piece]
        if len(pieces) < 2:
            raise SkillInstallError("GitHub skill URL must include owner and repository")
        owner, repo = pieces[0], pieces[1]
        if repo.endswith(".git"):
            repo = repo[:-4]
        repo_url = f"https://github.com/{owner}/{repo}.git"
        branch = ""
        subpath = ""
        if len(pieces) >= 4 and pieces[2] == "tree":
            branch = urllib.parse.unquote(pieces[3])
            subpath = "/".join(urllib.parse.unquote(piece) for piece in pieces[4:])
        elif len(pieces) > 2:
            raise SkillInstallError(
                "unsupported GitHub URL; use the repository root or a /tree/<branch>/<path> URL"
            )
        return repo_url, branch, subpath

    @staticmethod
    def _git_clone(repo_url: str, destination: Path, *, branch: str = "") -> None:
        command = [
            "git",
            "-c",
            "protocol.file.allow=never",
            "-c",
            "protocol.ext.allow=never",
            "clone",
            "--depth",
            "1",
            "--no-tags",
        ]
        if branch:
            command.extend(["--branch", branch])
        command.extend(["--", repo_url, str(destination)])
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = ""
        env["SSH_ASKPASS"] = ""
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                env=env,
            )
        except FileNotFoundError as exc:
            raise SkillInstallError(
                "git is required for repository skill sources but was not found on PATH"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SkillInstallError("git clone timed out") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "git clone failed"
            raise SkillInstallError(detail[-2000:])

    @staticmethod
    def _read_manifest(path: Path, *, required: bool = True) -> dict[str, object]:
        manifest_path = path / _MANIFEST_NAME
        if not manifest_path.is_file():
            if required:
                raise SkillInstallError(f"installed skill is missing {_MANIFEST_NAME}: {path}")
            return {}
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            if required:
                raise SkillInstallError(f"invalid skill manifest: {manifest_path}") from exc
            return {}
        return data if isinstance(data, dict) else {}


__all__ = [
    "InstalledSkill",
    "SkillInstallError",
    "SkillInstaller",
]
