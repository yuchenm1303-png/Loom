from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence

import yaml

from .memory_store import redact_secrets


_SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SEARCH_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_MAX_SKILL_FILE_BYTES = 128 * 1024
_MAX_SKILL_INSTRUCTIONS_CHARS = 64_000
_MAX_DISCOVERED_SKILLS = 512


class SkillError(ValueError):
    pass


class SkillScope(str, Enum):
    REPO = "repo"
    USER = "user"


@dataclass(frozen=True, slots=True)
class SkillRoot:
    path: Path
    scope: SkillScope

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", Path(self.path).expanduser())
        object.__setattr__(self, "scope", SkillScope(self.scope))


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    name: str
    description: str
    short_description: str
    path: Path
    root: Path
    scope: SkillScope

    def __post_init__(self) -> None:
        name = str(self.name or "").strip()
        description = " ".join(str(self.description or "").split())
        short_description = " ".join(str(self.short_description or "").split())
        if not _SKILL_NAME_RE.fullmatch(name):
            raise SkillError(f"invalid skill name: {name!r}")
        if not description:
            raise SkillError(f"skill {name!r} must define a description")
        if len(description) > 2048:
            raise SkillError(f"skill {name!r} description is too long")
        if len(short_description) > 512:
            raise SkillError(f"skill {name!r} short_description is too long")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "short_description", short_description)
        object.__setattr__(self, "path", Path(self.path))
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "scope", SkillScope(self.scope))

    @property
    def search_text(self) -> str:
        return " ".join(part for part in (self.name, self.short_description, self.description) if part)


@dataclass(frozen=True, slots=True)
class SkillCatalogSnapshot:
    skills: tuple[SkillDefinition, ...]
    errors: tuple[str, ...] = ()

    def get(self, name: str) -> SkillDefinition | None:
        wanted = str(name or "").strip().casefold()
        if not wanted:
            return None
        for skill in self.skills:
            if skill.name.casefold() == wanted:
                return skill
        return None

    def search(self, query: str, *, limit: int = 5) -> tuple[SkillDefinition, ...]:
        raw_query = str(query or "").strip()
        if not raw_query:
            raise SkillError("skill search query must not be empty")
        resolved_limit = max(1, min(20, int(limit)))
        folded = raw_query.casefold()
        query_tokens = _tokens(raw_query)
        scored: list[tuple[int, str, SkillDefinition]] = []
        for skill in self.skills:
            name = skill.name.casefold()
            short = skill.short_description.casefold()
            description = skill.description.casefold()
            name_tokens = set(_tokens(skill.name))
            text_tokens = set(_tokens(skill.search_text))
            score = 0
            if folded == name:
                score += 10_000
            elif name.startswith(folded):
                score += 7_000
            elif folded in name:
                score += 5_000
            elif folded and folded in short:
                score += 2_000
            elif folded and folded in description:
                score += 1_500
            for token in query_tokens:
                if token in name_tokens:
                    score += 700
                elif any(part.startswith(token) or token.startswith(part) for part in name_tokens):
                    score += 350
                if token in text_tokens:
                    score += 100
            if score > 0:
                scored.append((score, skill.name, skill))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in scored[:resolved_limit])


@dataclass(frozen=True, slots=True)
class ParsedSkillDocument:
    name: str
    description: str
    short_description: str
    instructions: str


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(_SEARCH_TOKEN_RE.findall(str(value or "").casefold()))


def parse_skill_document(text: str, *, default_name: str) -> ParsedSkillDocument:
    value = str(text or "")
    if not value.startswith("---"):
        raise SkillError("SKILL.md must start with YAML frontmatter")
    lines = value.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillError("SKILL.md must start with YAML frontmatter")
    closing = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing = index
            break
    if closing is None:
        raise SkillError("SKILL.md frontmatter is missing the closing --- delimiter")

    frontmatter_text = "\n".join(lines[1:closing])
    try:
        metadata = yaml.safe_load(frontmatter_text) or {}
    except yaml.YAMLError as exc:
        raise SkillError(f"invalid SKILL.md YAML frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise SkillError("SKILL.md frontmatter must be a YAML mapping")

    name = str(metadata.get("name") or default_name or "").strip()
    description = str(metadata.get("description") or "").strip()
    short_description = str(
        metadata.get("short_description")
        or metadata.get("short-description")
        or metadata.get("shortDescription")
        or ""
    ).strip()
    instructions = "\n".join(lines[closing + 1 :]).strip()
    definition_probe = SkillDefinition(
        name=name,
        description=description,
        short_description=short_description,
        path=Path("SKILL.md"),
        root=Path("."),
        scope=SkillScope.USER,
    )
    if len(instructions) > _MAX_SKILL_INSTRUCTIONS_CHARS:
        instructions = instructions[: _MAX_SKILL_INSTRUCTIONS_CHARS - 18] + "\n…[truncated]"
    return ParsedSkillDocument(
        name=definition_probe.name,
        description=definition_probe.description,
        short_description=definition_probe.short_description,
        instructions=redact_secrets(instructions),
    )


class SkillManager:
    """Discovers Codex-compatible SKILL.md files without exposing bodies to the model."""

    def __init__(
        self,
        *,
        user_roots: Sequence[Path] = (),
        max_file_bytes: int = _MAX_SKILL_FILE_BYTES,
        max_skills: int = _MAX_DISCOVERED_SKILLS,
    ) -> None:
        self.user_roots = tuple(Path(root).expanduser() for root in user_roots)
        self.max_file_bytes = max(1024, int(max_file_bytes))
        self.max_skills = max(1, min(4096, int(max_skills)))

    def roots_for_workspace(self, workspace: Path) -> tuple[SkillRoot, ...]:
        roots: list[SkillRoot] = []
        seen: set[Path] = set()
        for directory in _workspace_directories(Path(workspace)):
            candidate = directory / ".agents" / "skills"
            resolved = candidate.expanduser().resolve(strict=False)
            if resolved not in seen:
                roots.append(SkillRoot(candidate, SkillScope.REPO))
                seen.add(resolved)
        for candidate in self.user_roots:
            resolved = candidate.expanduser().resolve(strict=False)
            if resolved not in seen:
                roots.append(SkillRoot(candidate, SkillScope.USER))
                seen.add(resolved)
        return tuple(roots)

    def discover(self, workspace: Path) -> SkillCatalogSnapshot:
        skills: list[SkillDefinition] = []
        errors: list[str] = []
        names: set[str] = set()
        for root in self.roots_for_workspace(workspace):
            root_path = root.path.expanduser()
            if not root_path.is_dir():
                continue
            try:
                resolved_root = root_path.resolve(strict=True)
            except OSError as exc:
                errors.append(f"{root_path}: {type(exc).__name__}: {exc}")
                continue
            try:
                candidates = sorted(root_path.rglob("SKILL.md"), key=lambda path: str(path).casefold())
            except OSError as exc:
                errors.append(f"{root_path}: {type(exc).__name__}: {exc}")
                continue
            for path in candidates:
                if len(skills) >= self.max_skills:
                    errors.append(f"skill discovery stopped after {self.max_skills} skills")
                    return SkillCatalogSnapshot(tuple(skills), tuple(errors))
                try:
                    relative = path.relative_to(root_path)
                except ValueError:
                    continue
                if any(part.startswith(".") for part in relative.parts[:-1]):
                    continue
                try:
                    resolved_path = path.resolve(strict=True)
                    resolved_path.relative_to(resolved_root)
                except (OSError, ValueError):
                    errors.append(f"skipped skill outside root: {path}")
                    continue
                if not resolved_path.is_file():
                    continue
                try:
                    if resolved_path.stat().st_size > self.max_file_bytes:
                        errors.append(f"skill file is too large: {resolved_path}")
                        continue
                    text = resolved_path.read_text(encoding="utf-8")
                    parsed = parse_skill_document(text, default_name=path.parent.name)
                    key = parsed.name.casefold()
                    if key in names:
                        continue
                    names.add(key)
                    skills.append(
                        SkillDefinition(
                            name=parsed.name,
                            description=parsed.description,
                            short_description=parsed.short_description,
                            path=resolved_path,
                            root=resolved_root,
                            scope=root.scope,
                        )
                    )
                except (OSError, UnicodeError, SkillError) as exc:
                    errors.append(f"{path}: {type(exc).__name__}: {exc}")
        return SkillCatalogSnapshot(tuple(skills), tuple(errors))

    def load(self, skill: SkillDefinition) -> str:
        resolved_root = skill.root.resolve(strict=True)
        resolved_path = skill.path.resolve(strict=True)
        try:
            resolved_path.relative_to(resolved_root)
        except ValueError as exc:
            raise SkillError("skill path escapes its discovery root") from exc
        if resolved_path.stat().st_size > self.max_file_bytes:
            raise SkillError("skill file exceeds the configured size limit")
        parsed = parse_skill_document(
            resolved_path.read_text(encoding="utf-8"),
            default_name=skill.name,
        )
        if parsed.name.casefold() != skill.name.casefold():
            raise SkillError("skill identity changed after discovery")
        return parsed.instructions


def _workspace_directories(workspace: Path) -> tuple[Path, ...]:
    current = workspace.expanduser().resolve()
    if current.is_file():
        current = current.parent
    project_root = current
    found_marker = False
    for ancestor in (current, *current.parents):
        if (ancestor / ".git").exists():
            project_root = ancestor
            found_marker = True
            break
    if not found_marker:
        return (current,)

    directories: list[Path] = []
    probe = current
    while True:
        directories.append(probe)
        if probe == project_root:
            break
        parent = probe.parent
        if parent == probe:
            break
        probe = parent
    return tuple(directories)


__all__ = [
    "ParsedSkillDocument",
    "SkillCatalogSnapshot",
    "SkillDefinition",
    "SkillError",
    "SkillManager",
    "SkillRoot",
    "SkillScope",
    "parse_skill_document",
]
