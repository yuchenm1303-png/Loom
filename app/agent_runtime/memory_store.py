from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable

from .storage import utc_now


class MemoryScope(str, Enum):
    GLOBAL = "global"
    WORKSPACE = "workspace"


class MemoryCategory(str, Enum):
    PREFERENCE = "preference"
    FACT = "fact"
    PROJECT = "project"
    DECISION = "decision"
    CONSTRAINT = "constraint"


class MemoryCandidateState(str, Enum):
    PENDING = "pending"
    CONSOLIDATED = "consolidated"


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    text: str
    scope: MemoryScope
    category: MemoryCategory
    importance: int = 3

    def __post_init__(self) -> None:
        text = redact_secrets(str(self.text or "").strip())
        if not text:
            raise ValueError("memory candidate text must not be empty")
        if len(text) > 4000:
            raise ValueError("memory candidate text exceeds 4,000 characters")
        importance = int(self.importance)
        if not 1 <= importance <= 5:
            raise ValueError("memory importance must be within 1..5")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "scope", MemoryScope(self.scope))
        object.__setattr__(self, "category", MemoryCategory(self.category))
        object.__setattr__(self, "importance", importance)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    memory_id: str
    scope: MemoryScope
    scope_key: str
    category: MemoryCategory
    text: str
    importance: int
    source_count: int
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "scope": self.scope.value,
            "category": self.category.value,
            "text": self.text,
            "importance": self.importance,
            "source_count": self.source_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class MemoryExtraction:
    extraction_id: str
    source_session_id: str
    source_turn_id: str
    summary: str
    candidate_count: int
    usage_total_tokens: int
    created_at: str


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE_KEY)[A-Z0-9_]*)"
    r"\s*([:=])\s*([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
_PEM_RE = re.compile(
    r"-----BEGIN [^-\n]*PRIVATE KEY-----.*?-----END [^-\n]*PRIVATE KEY-----",
    flags=re.DOTALL,
)
_TERM_RE = re.compile(r"[A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]")


def redact_secrets(text: str) -> str:
    value = str(text or "")
    value = _PEM_RE.sub("[REDACTED_PRIVATE_KEY]", value)
    value = _OPENAI_KEY_RE.sub("[REDACTED_API_KEY]", value)
    value = _BEARER_RE.sub("Bearer [REDACTED_TOKEN]", value)
    value = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", value)
    return value


def workspace_memory_key(workspace: str | Path) -> str:
    normalized = str(Path(workspace).expanduser().resolve())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


class MemoryStore:
    """SQLite-backed candidate and consolidated long-term memory store.

    Extraction and consolidation are deliberately separate. A model may propose
    candidates, but those candidates are first persisted as pending rows and are
    only promoted through the consolidation boundary. Exact normalized duplicates
    collapse into one canonical record with an incrementing source count.
    """

    def __init__(self, runtime_dir: str | Path) -> None:
        self.root = Path(runtime_dir).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.db"
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_extractions (
                    extraction_id TEXT PRIMARY KEY,
                    source_session_id TEXT NOT NULL,
                    source_turn_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    candidate_count INTEGER NOT NULL,
                    usage_total_tokens INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    extraction_id TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    source_turn_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    text TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_candidates_state
                ON memory_candidates(state, created_at, candidate_id);

                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    scope_key TEXT NOT NULL,
                    category TEXT NOT NULL,
                    text TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    source_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memories_scope
                ON memories(scope, scope_key, updated_at);
                """
            )

    def add_extraction(
        self,
        *,
        source_session_id: str,
        source_turn_id: str,
        workspace: str | Path,
        summary: str,
        candidates: Iterable[MemoryCandidate],
        usage_total_tokens: int = 0,
    ) -> MemoryExtraction:
        session_id = _key(source_session_id, "source_session_id")
        turn_id = str(source_turn_id or "").strip()
        candidate_values = tuple(candidates)
        if len(candidate_values) > 64:
            raise ValueError("memory extraction exceeds 64 candidates")
        extraction_id = str(uuid.uuid4())
        created_at = utc_now()
        workspace_key = workspace_memory_key(workspace)
        clean_summary = redact_secrets(str(summary or "").strip())[:20_000]

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO memory_extractions(
                        extraction_id, source_session_id, source_turn_id, summary,
                        candidate_count, usage_total_tokens, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        extraction_id,
                        session_id,
                        turn_id,
                        clean_summary,
                        len(candidate_values),
                        max(0, int(usage_total_tokens)),
                        created_at,
                    ),
                )
                for candidate in candidate_values:
                    scope_key = "global" if candidate.scope is MemoryScope.GLOBAL else workspace_key
                    fingerprint = _fingerprint(
                        candidate.scope,
                        scope_key,
                        candidate.category,
                        candidate.text,
                    )
                    connection.execute(
                        """
                        INSERT INTO memory_candidates(
                            candidate_id, extraction_id, source_session_id, source_turn_id,
                            scope, scope_key, category, text, importance, fingerprint, state, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            extraction_id,
                            session_id,
                            turn_id,
                            candidate.scope.value,
                            scope_key,
                            candidate.category.value,
                            candidate.text,
                            candidate.importance,
                            fingerprint,
                            MemoryCandidateState.PENDING.value,
                            created_at,
                        ),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return MemoryExtraction(
            extraction_id=extraction_id,
            source_session_id=session_id,
            source_turn_id=turn_id,
            summary=clean_summary,
            candidate_count=len(candidate_values),
            usage_total_tokens=max(0, int(usage_total_tokens)),
            created_at=created_at,
        )

    def consolidate_pending(self, *, limit: int = 256) -> tuple[MemoryRecord, ...]:
        cap = max(1, min(2048, int(limit)))
        touched_ids: list[str] = []
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                rows = connection.execute(
                    """
                    SELECT * FROM memory_candidates
                    WHERE state = ?
                    ORDER BY created_at ASC, candidate_id ASC
                    LIMIT ?
                    """,
                    (MemoryCandidateState.PENDING.value, cap),
                ).fetchall()
                for row in rows:
                    existing = connection.execute(
                        "SELECT * FROM memories WHERE fingerprint = ?",
                        (str(row["fingerprint"]),),
                    ).fetchone()
                    if existing is None:
                        memory_id = str(uuid.uuid4())
                        connection.execute(
                            """
                            INSERT INTO memories(
                                memory_id, scope, scope_key, category, text, importance,
                                fingerprint, source_count, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                            """,
                            (
                                memory_id,
                                str(row["scope"]),
                                str(row["scope_key"]),
                                str(row["category"]),
                                str(row["text"]),
                                int(row["importance"]),
                                str(row["fingerprint"]),
                                now,
                                now,
                            ),
                        )
                    else:
                        memory_id = str(existing["memory_id"])
                        connection.execute(
                            """
                            UPDATE memories
                            SET source_count = source_count + 1,
                                importance = CASE WHEN importance < ? THEN ? ELSE importance END,
                                updated_at = ?
                            WHERE memory_id = ?
                            """,
                            (int(row["importance"]), int(row["importance"]), now, memory_id),
                        )
                    connection.execute(
                        "UPDATE memory_candidates SET state = ? WHERE candidate_id = ?",
                        (MemoryCandidateState.CONSOLIDATED.value, str(row["candidate_id"])),
                    )
                    touched_ids.append(memory_id)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        if not touched_ids:
            return ()
        unique_ids = tuple(dict.fromkeys(touched_ids))
        return tuple(self.get(memory_id) for memory_id in unique_ids if self.get(memory_id) is not None)

    def get(self, memory_id: str) -> MemoryRecord | None:
        key = _key(memory_id, "memory_id")
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM memories WHERE memory_id = ?", (key,)).fetchone()
        return _record_from_row(row) if row is not None else None

    def list_records(
        self,
        *,
        workspace: str | Path | None = None,
        include_global: bool = True,
        limit: int = 100,
    ) -> tuple[MemoryRecord, ...]:
        cap = max(1, min(1000, int(limit)))
        with self._lock, self._connect() as connection:
            if workspace is None:
                rows = connection.execute(
                    "SELECT * FROM memories ORDER BY updated_at DESC, memory_id ASC LIMIT ?",
                    (cap,),
                ).fetchall()
            else:
                workspace_key = workspace_memory_key(workspace)
                if include_global:
                    rows = connection.execute(
                        """
                        SELECT * FROM memories
                        WHERE scope = ? OR (scope = ? AND scope_key = ?)
                        ORDER BY updated_at DESC, memory_id ASC LIMIT ?
                        """,
                        (MemoryScope.GLOBAL.value, MemoryScope.WORKSPACE.value, workspace_key, cap),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT * FROM memories
                        WHERE scope = ? AND scope_key = ?
                        ORDER BY updated_at DESC, memory_id ASC LIMIT ?
                        """,
                        (MemoryScope.WORKSPACE.value, workspace_key, cap),
                    ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def search(
        self,
        query: str,
        *,
        workspace: str | Path,
        limit: int = 8,
    ) -> tuple[MemoryRecord, ...]:
        text = str(query or "").strip()
        if not text:
            return ()
        records = self.list_records(workspace=workspace, include_global=True, limit=500)
        if not records:
            return ()
        query_norm = _normalize(text)
        query_terms = _terms(text)
        scored: list[tuple[float, MemoryRecord]] = []
        for record in records:
            body_norm = _normalize(record.text)
            body_terms = _terms(record.text)
            overlap = len(query_terms.intersection(body_terms))
            score = float(overlap * 3)
            if query_norm and query_norm in body_norm:
                score += 12.0
            elif body_norm and body_norm in query_norm:
                score += 5.0
            if record.category is MemoryCategory.PREFERENCE:
                score += 0.75
            score += record.importance * 0.35
            score += math.log2(max(1, record.source_count)) * 0.25
            if record.scope is MemoryScope.WORKSPACE:
                score += 0.5
            if score > 0.0:
                scored.append((score, record))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return tuple(record for _, record in scored[: max(1, min(32, int(limit)))])

    def counts(self, *, workspace: str | Path | None = None) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
            pending = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memory_candidates WHERE state = ?",
                    (MemoryCandidateState.PENDING.value,),
                ).fetchone()[0]
            )
            if workspace is None:
                visible = total
            else:
                key = workspace_memory_key(workspace)
                visible = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM memories
                        WHERE scope = ? OR (scope = ? AND scope_key = ?)
                        """,
                        (MemoryScope.GLOBAL.value, MemoryScope.WORKSPACE.value, key),
                    ).fetchone()[0]
                )
        return {"total": total, "visible": visible, "pending": pending}


def _key(value: str, name: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise ValueError(f"{name} must not be empty")
    return key


def _normalize(text: str) -> str:
    return " ".join(str(text or "").casefold().split())


def _terms(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _TERM_RE.finditer(str(text or ""))}


def _fingerprint(scope: MemoryScope, scope_key: str, category: MemoryCategory, text: str) -> str:
    canonical = "\n".join((scope.value, scope_key, category.value, _normalize(text)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record_from_row(row: sqlite3.Row) -> MemoryRecord:
    return MemoryRecord(
        memory_id=str(row["memory_id"]),
        scope=MemoryScope(str(row["scope"])),
        scope_key=str(row["scope_key"]),
        category=MemoryCategory(str(row["category"])),
        text=str(row["text"]),
        importance=int(row["importance"]),
        source_count=int(row["source_count"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


__all__ = [
    "MemoryCandidate",
    "MemoryCandidateState",
    "MemoryCategory",
    "MemoryExtraction",
    "MemoryRecord",
    "MemoryScope",
    "MemoryStore",
    "redact_secrets",
    "workspace_memory_key",
]
