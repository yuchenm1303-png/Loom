from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
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
    evidence: str = ""

    def __post_init__(self) -> None:
        text = redact_secrets(str(self.text or "").strip())
        if not text:
            raise ValueError("memory candidate text must not be empty")
        if len(text) > 4000:
            raise ValueError("memory candidate text exceeds 4,000 characters")
        importance = int(self.importance)
        if not 1 <= importance <= 5:
            raise ValueError("memory importance must be within 1..5")
        evidence = redact_secrets(str(self.evidence or "").strip())[:2000]
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "scope", MemoryScope(self.scope))
        object.__setattr__(self, "category", MemoryCategory(self.category))
        object.__setattr__(self, "importance", importance)
        object.__setattr__(self, "evidence", evidence)


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
    usage_count: int = 0
    last_used_at: str = ""
    confidence: float = 1.0
    status: str = "active"
    last_verified_at: str = ""
    archived_at: str = ""
    lifecycle_note: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "scope": self.scope.value,
            "category": self.category.value,
            "text": self.text,
            "importance": self.importance,
            "source_count": self.source_count,
            "usage_count": self.usage_count,
            "last_used_at": self.last_used_at,
            "confidence": self.confidence,
            "status": self.status,
            "last_verified_at": self.last_verified_at,
            "archived_at": self.archived_at,
            "lifecycle_note": self.lifecycle_note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class MemorySearchHit:
    record: MemoryRecord
    score: float
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            **self.record.to_dict(),
            "score": round(float(self.score), 4),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class MemoryUsageEvent:
    event_id: str
    memory_id: str
    source_session_id: str
    source_turn_id: str
    route: str
    score: float
    reason: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "memory_id": self.memory_id,
            "source_session_id": self.source_session_id,
            "source_turn_id": self.source_turn_id,
            "route": self.route,
            "score": self.score,
            "reason": self.reason,
            "created_at": self.created_at,
        }


@dataclass(frozen=True, slots=True)
class MemoryEvidence:
    evidence_id: str
    memory_id: str
    candidate_id: str
    extraction_id: str
    source_session_id: str
    source_turn_id: str
    excerpt: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "memory_id": self.memory_id,
            "source_session_id": self.source_session_id,
            "source_turn_id": self.source_turn_id,
            "excerpt": self.excerpt,
            "created_at": self.created_at,
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
    source_start_event_id: str = ""
    source_end_event_id: str = ""


@dataclass(frozen=True, slots=True)
class MemoryThreadState:
    session_id: str
    last_event_id: str = ""
    last_turn_id: str = ""
    last_success_at: str = ""
    failure_count: int = 0
    retry_at: float = 0.0
    last_error: str = ""
    updated_at: str = ""


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
    value = _SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]",
        value,
    )
    return value


def workspace_memory_key(workspace: str | Path) -> str:
    normalized = str(Path(workspace).expanduser().resolve())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


class MemoryStore:
    """SQLite-backed long-term memory store.

    V2-A keeps the original candidate -> consolidation boundary, then adds
    provenance, read-usage accounting, and per-thread extraction checkpoints.
    SQLite remains the canonical source; model-generated memory is never allowed
    to mutate the database directly.
    """

    def __init__(self, runtime_dir: str | Path) -> None:
        self.root = Path(runtime_dir).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "state.db"
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

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
                    created_at TEXT NOT NULL,
                    source_start_event_id TEXT NOT NULL DEFAULT '',
                    source_end_event_id TEXT NOT NULL DEFAULT ''
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
                    created_at TEXT NOT NULL,
                    evidence_excerpt TEXT NOT NULL DEFAULT ''
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
                    updated_at TEXT NOT NULL,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    last_used_at TEXT NOT NULL DEFAULT '',
                    confidence REAL NOT NULL DEFAULT 1.0,
                    status TEXT NOT NULL DEFAULT 'active',
                    last_verified_at TEXT NOT NULL DEFAULT ''
                );

                CREATE INDEX IF NOT EXISTS idx_memories_scope
                ON memories(scope, scope_key, updated_at);

                CREATE TABLE IF NOT EXISTS memory_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL UNIQUE,
                    extraction_id TEXT NOT NULL,
                    source_session_id TEXT NOT NULL,
                    source_turn_id TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_evidence_memory
                ON memory_evidence(memory_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS memory_usage_events (
                    event_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    source_session_id TEXT NOT NULL DEFAULT '',
                    source_turn_id TEXT NOT NULL DEFAULT '',
                    route TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_memory_usage_events_memory
                ON memory_usage_events(memory_id, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_memory_usage_events_session
                ON memory_usage_events(source_session_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS memory_thread_state (
                    session_id TEXT PRIMARY KEY,
                    last_event_id TEXT NOT NULL DEFAULT '',
                    last_turn_id TEXT NOT NULL DEFAULT '',
                    last_success_at TEXT NOT NULL DEFAULT '',
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    retry_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                """
            )
            _ensure_column(
                connection,
                "memory_extractions",
                "source_start_event_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "memory_extractions",
                "source_end_event_id",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "memory_candidates",
                "evidence_excerpt",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "memories",
                "usage_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            _ensure_column(
                connection,
                "memories",
                "last_used_at",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "memories",
                "confidence",
                "REAL NOT NULL DEFAULT 1.0",
            )
            _ensure_column(
                connection,
                "memories",
                "status",
                "TEXT NOT NULL DEFAULT 'active'",
            )
            _ensure_column(
                connection,
                "memories",
                "last_verified_at",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "memories",
                "archived_at",
                "TEXT NOT NULL DEFAULT ''",
            )
            _ensure_column(
                connection,
                "memories",
                "lifecycle_note",
                "TEXT NOT NULL DEFAULT ''",
            )
            # This index must be created only after the in-place v1 migration.
            # Existing databases do not have status/usage_count until the
            # _ensure_column calls above complete.
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_memories_usage
                ON memories(status, usage_count DESC, updated_at DESC)
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
        source_start_event_id: str = "",
        source_end_event_id: str = "",
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
        start_event_id = str(source_start_event_id or "").strip()
        end_event_id = str(source_end_event_id or "").strip()

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT INTO memory_extractions(
                        extraction_id, source_session_id, source_turn_id, summary,
                        candidate_count, usage_total_tokens, created_at,
                        source_start_event_id, source_end_event_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        extraction_id,
                        session_id,
                        turn_id,
                        clean_summary,
                        len(candidate_values),
                        max(0, int(usage_total_tokens)),
                        created_at,
                        start_event_id,
                        end_event_id,
                    ),
                )
                for candidate in candidate_values:
                    scope_key = (
                        "global"
                        if candidate.scope is MemoryScope.GLOBAL
                        else workspace_key
                    )
                    fingerprint = _fingerprint(
                        candidate.scope,
                        scope_key,
                        candidate.category,
                        candidate.text,
                    )
                    connection.execute(
                        """
                        INSERT INTO memory_candidates(
                            candidate_id, extraction_id, source_session_id,
                            source_turn_id, scope, scope_key, category, text,
                            importance, fingerprint, state, created_at,
                            evidence_excerpt
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            candidate.evidence,
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
            source_start_event_id=start_event_id,
            source_end_event_id=end_event_id,
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
                                memory_id, scope, scope_key, category, text,
                                importance, fingerprint, source_count,
                                created_at, updated_at, usage_count,
                                last_used_at, confidence, status,
                                last_verified_at
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, 1, ?, ?,
                                0, '', 1.0, 'active', ''
                            )
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
                                importance = CASE
                                    WHEN importance < ? THEN ?
                                    ELSE importance
                                END,
                                status = 'active',
                                archived_at = '',
                                lifecycle_note = '',
                                updated_at = ?
                            WHERE memory_id = ?
                            """,
                            (
                                int(row["importance"]),
                                int(row["importance"]),
                                now,
                                memory_id,
                            ),
                        )

                    candidate_id = str(row["candidate_id"])
                    excerpt = redact_secrets(
                        str(row["evidence_excerpt"] or row["text"])
                    ).strip()[:2000]
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO memory_evidence(
                            evidence_id, memory_id, candidate_id,
                            extraction_id, source_session_id,
                            source_turn_id, excerpt, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            memory_id,
                            candidate_id,
                            str(row["extraction_id"]),
                            str(row["source_session_id"]),
                            str(row["source_turn_id"]),
                            excerpt,
                            now,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE memory_candidates
                        SET state = ?
                        WHERE candidate_id = ?
                        """,
                        (
                            MemoryCandidateState.CONSOLIDATED.value,
                            candidate_id,
                        ),
                    )
                    touched_ids.append(memory_id)

                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

        if not touched_ids:
            return ()

        records: list[MemoryRecord] = []
        for memory_id in dict.fromkeys(touched_ids):
            record = self.get(memory_id)
            if record is not None:
                records.append(record)
        return tuple(records)

    def get(self, memory_id: str) -> MemoryRecord | None:
        key = _key(memory_id, "memory_id")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE memory_id = ?",
                (key,),
            ).fetchone()
        return _record_from_row(row) if row is not None else None

    def get_visible(
        self,
        memory_id: str,
        *,
        workspace: str | Path,
    ) -> MemoryRecord | None:
        record = self.get(memory_id)
        if record is None or record.status != "active":
            return None
        if record.scope is MemoryScope.GLOBAL:
            return record
        if record.scope_key == workspace_memory_key(workspace):
            return record
        return None

    def evidence(
        self,
        memory_id: str,
        *,
        limit: int = 20,
    ) -> tuple[MemoryEvidence, ...]:
        key = _key(memory_id, "memory_id")
        cap = max(1, min(100, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_evidence
                WHERE memory_id = ?
                ORDER BY created_at DESC, evidence_id ASC
                LIMIT ?
                """,
                (key, cap),
            ).fetchall()
        return tuple(_evidence_from_row(row) for row in rows)

    def record_usage(
        self,
        memory_ids: Iterable[str],
        *,
        source_session_id: str = "",
        source_turn_id: str = "",
        route: str = "memory",
        scores: dict[str, float] | None = None,
        reasons: dict[str, str] | None = None,
    ) -> None:
        identifiers = tuple(
            dict.fromkeys(
                str(memory_id or "").strip()
                for memory_id in memory_ids
                if str(memory_id or "").strip()
            )
        )
        if not identifiers:
            return

        session_id = str(source_session_id or "").strip()
        turn_id = str(source_turn_id or "").strip()
        route_name = str(route or "memory").strip()[:64] or "memory"
        score_map = scores or {}
        reason_map = reasons or {}
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for memory_id in identifiers:
                    updated = connection.execute(
                        """
                        UPDATE memories
                        SET usage_count = usage_count + 1,
                            last_used_at = ?
                        WHERE memory_id = ? AND status = 'active'
                        """,
                        (now, memory_id),
                    )
                    if updated.rowcount <= 0:
                        continue
                    connection.execute(
                        """
                        INSERT INTO memory_usage_events(
                            event_id, memory_id, source_session_id,
                            source_turn_id, route, score, reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            memory_id,
                            session_id,
                            turn_id,
                            route_name,
                            float(score_map.get(memory_id, 0.0)),
                            str(reason_map.get(memory_id, ""))[:1000],
                            now,
                        ),
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def mark_used(self, memory_ids: Iterable[str]) -> None:
        self.record_usage(memory_ids, route="legacy")

    def usage_events(
        self,
        memory_id: str,
        *,
        limit: int = 20,
    ) -> tuple[MemoryUsageEvent, ...]:
        key = _key(memory_id, "memory_id")
        cap = max(1, min(100, int(limit)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memory_usage_events
                WHERE memory_id = ?
                ORDER BY created_at DESC, event_id ASC
                LIMIT ?
                """,
                (key, cap),
            ).fetchall()
        return tuple(_usage_event_from_row(row) for row in rows)

    def archive(
        self,
        memory_id: str,
        *,
        note: str = "manual archive",
    ) -> bool:
        key = _key(memory_id, "memory_id")
        now = utc_now()
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE memories
                SET status = 'archived', archived_at = ?,
                    lifecycle_note = ?, updated_at = ?
                WHERE memory_id = ? AND status = 'active'
                """,
                (now, str(note or "manual archive")[:1000], now, key),
            )
        return updated.rowcount > 0

    def restore(self, memory_id: str) -> bool:
        key = _key(memory_id, "memory_id")
        now = utc_now()
        with self._lock, self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE memories
                SET status = 'active', archived_at = '',
                    lifecycle_note = '', updated_at = ?
                WHERE memory_id = ? AND status = 'archived'
                """,
                (now, key),
            )
        return updated.rowcount > 0

    def maintain_lifecycle(
        self,
        *,
        workspace: str | Path | None = None,
        inactive_days: int = 180,
    ) -> tuple[str, ...]:
        """Archive only low-signal, unused facts/project notes.

        Decisions, constraints, and preferences are never auto-archived. Archive
        is reversible, and repeated evidence for an archived fingerprint
        reactivates it during normal consolidation.
        """

        days = max(30, min(3650, int(inactive_days)))
        params: list[object] = [
            MemoryCategory.FACT.value,
            MemoryCategory.PROJECT.value,
            days,
        ]
        workspace_clause = ""
        if workspace is not None:
            workspace_clause = " AND scope = ? AND scope_key = ?"
            params.extend(
                [
                    MemoryScope.WORKSPACE.value,
                    workspace_memory_key(workspace),
                ]
            )
        now = utc_now()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT memory_id FROM memories
                WHERE status = 'active'
                  AND category IN (?, ?)
                  AND importance <= 2
                  AND source_count <= 1
                  AND usage_count = 0
                  AND last_verified_at = ''
                  AND (julianday('now') - julianday(updated_at)) >= ?
                  {workspace_clause}
                ORDER BY updated_at ASC
                """,
                tuple(params),
            ).fetchall()
            identifiers = tuple(str(row["memory_id"]) for row in rows)
            if identifiers:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for memory_id in identifiers:
                        connection.execute(
                            """
                            UPDATE memories
                            SET status = 'archived', archived_at = ?,
                                lifecycle_note = ?, updated_at = ?
                            WHERE memory_id = ? AND status = 'active'
                            """,
                            (
                                now,
                                f"auto-archived after {days} unused days",
                                now,
                                memory_id,
                            ),
                        )
                    connection.execute("COMMIT")
                except Exception:
                    connection.execute("ROLLBACK")
                    raise
        return identifiers

    def delete(self, memory_id: str) -> bool:
        """Forget one consolidated memory and its candidate/evidence copies."""

        key = _key(memory_id, "memory_id")
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT fingerprint FROM memories WHERE memory_id = ?",
                    (key,),
                ).fetchone()
                if row is None:
                    connection.execute("ROLLBACK")
                    return False

                fingerprint = str(row["fingerprint"])
                connection.execute(
                    "DELETE FROM memory_evidence WHERE memory_id = ?",
                    (key,),
                )
                connection.execute(
                    "DELETE FROM memories WHERE memory_id = ?",
                    (key,),
                )
                connection.execute(
                    "DELETE FROM memory_candidates WHERE fingerprint = ?",
                    (fingerprint,),
                )
                connection.execute("COMMIT")
                return True
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def list_records(
        self,
        *,
        workspace: str | Path | None = None,
        include_global: bool = True,
        limit: int = 100,
        status: str = "active",
    ) -> tuple[MemoryRecord, ...]:
        cap = max(1, min(1000, int(limit)))
        wanted_status = str(status or "active").strip().casefold()
        if wanted_status not in {"active", "archived", "superseded"}:
            raise ValueError("memory status must be active, archived, or superseded")
        with self._lock, self._connect() as connection:
            if workspace is None:
                rows = connection.execute(
                    """
                    SELECT * FROM memories
                    WHERE status = ?
                    ORDER BY updated_at DESC, memory_id ASC
                    LIMIT ?
                    """,
                    (wanted_status, cap),
                ).fetchall()
            else:
                workspace_key = workspace_memory_key(workspace)
                if include_global:
                    rows = connection.execute(
                        """
                        SELECT * FROM memories
                        WHERE status = ?
                          AND (
                            scope = ?
                            OR (scope = ? AND scope_key = ?)
                          )
                        ORDER BY updated_at DESC, memory_id ASC
                        LIMIT ?
                        """,
                        (
                            wanted_status,
                            MemoryScope.GLOBAL.value,
                            MemoryScope.WORKSPACE.value,
                            workspace_key,
                            cap,
                        ),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT * FROM memories
                        WHERE status = ?
                          AND scope = ?
                          AND scope_key = ?
                        ORDER BY updated_at DESC, memory_id ASC
                        LIMIT ?
                        """,
                        (
                            wanted_status,
                            MemoryScope.WORKSPACE.value,
                            workspace_key,
                            cap,
                        ),
                    ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def summary_records(
        self,
        *,
        workspace: str | Path,
        limit: int = 10,
    ) -> tuple[MemoryRecord, ...]:
        cap = max(1, min(32, int(limit)))
        workspace_key = workspace_memory_key(workspace)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM memories
                WHERE status = 'active'
                  AND (
                    scope = ?
                    OR (scope = ? AND scope_key = ?)
                  )
                ORDER BY
                    CASE
                        WHEN scope = ? AND scope_key = ? THEN 1
                        ELSE 0
                    END DESC,
                    importance DESC,
                    usage_count DESC,
                    updated_at DESC,
                    memory_id ASC
                LIMIT ?
                """,
                (
                    MemoryScope.GLOBAL.value,
                    MemoryScope.WORKSPACE.value,
                    workspace_key,
                    MemoryScope.WORKSPACE.value,
                    workspace_key,
                    cap,
                ),
            ).fetchall()
        return tuple(_record_from_row(row) for row in rows)

    def search_hits(
        self,
        query: str,
        *,
        workspace: str | Path,
        limit: int = 8,
        include_global: bool = True,
        require_query_match: bool = False,
    ) -> tuple[MemorySearchHit, ...]:
        text = str(query or "").strip()
        if not text:
            return ()
        records = self.list_records(
            workspace=workspace,
            include_global=include_global,
            limit=500,
        )
        if not records:
            return ()

        query_norm = _normalize(text)
        query_terms = _terms(text)
        scored: list[MemorySearchHit] = []
        for record in records:
            body_norm = _normalize(record.text)
            body_terms = _terms(record.text)
            overlap = len(query_terms.intersection(body_terms))
            contains_query = bool(query_norm and query_norm in body_norm)
            contains_body = bool(body_norm and body_norm in query_norm)
            if require_query_match and not (overlap or contains_query or contains_body):
                continue

            score = float(overlap * 3)
            reasons: list[str] = []
            if overlap:
                reasons.append(f"term_overlap:{overlap}")
            if contains_query:
                score += 12.0
                reasons.append("phrase_match")
            elif contains_body:
                score += 5.0
                reasons.append("query_contains_memory")
            if record.category is MemoryCategory.PREFERENCE:
                score += 0.75
                reasons.append("preference")
            importance_boost = record.importance * 0.35
            score += importance_boost
            if record.importance >= 4:
                reasons.append(f"importance:{record.importance}")
            if record.source_count > 1:
                score += math.log2(max(1, record.source_count)) * 0.25
                reasons.append(f"sources:{record.source_count}")
            if record.usage_count:
                score += math.log2(max(1, record.usage_count + 1)) * 0.2
                reasons.append(f"used:{record.usage_count}")
            if record.scope is MemoryScope.WORKSPACE:
                score += 0.5
                reasons.append("workspace")
            score += max(0.0, min(1.0, record.confidence)) * 0.4
            age_days = _age_days(
                record.last_verified_at
                or record.last_used_at
                or record.updated_at
            )
            freshness = max(0.0, 1.0 - min(age_days, 365.0) / 365.0)
            score += freshness * 0.6
            if age_days <= 30:
                reasons.append("recent")
            if score > 0.0:
                scored.append(
                    MemorySearchHit(
                        record=record,
                        score=score,
                        reasons=tuple(reasons),
                    )
                )

        scored.sort(
            key=lambda item: (item.score, item.record.updated_at),
            reverse=True,
        )
        return tuple(scored[: max(1, min(32, int(limit)))])

    def search(
        self,
        query: str,
        *,
        workspace: str | Path,
        limit: int = 8,
        include_global: bool = True,
        require_query_match: bool = False,
    ) -> tuple[MemoryRecord, ...]:
        return tuple(
            hit.record
            for hit in self.search_hits(
                query,
                workspace=workspace,
                limit=limit,
                include_global=include_global,
                require_query_match=require_query_match,
            )
        )

    def compact_index(
        self,
        *,
        workspace: str | Path,
        include_global: bool = True,
        max_chars: int = 2400,
    ) -> dict[str, object]:
        records = self.list_records(
            workspace=workspace,
            include_global=include_global,
            limit=500,
        )
        archived = self.list_records(
            workspace=workspace,
            include_global=include_global,
            limit=500,
            status="archived",
        )
        grouped: dict[MemoryCategory, list[MemoryRecord]] = {
            category: [] for category in MemoryCategory
        }
        for record in records:
            grouped[record.category].append(record)
        for items in grouped.values():
            items.sort(
                key=lambda record: (
                    record.scope is MemoryScope.WORKSPACE,
                    record.importance,
                    record.usage_count,
                    record.source_count,
                    record.updated_at,
                ),
                reverse=True,
            )

        category_counts = {
            category.value: len(items)
            for category, items in grouped.items()
            if items
        }
        labels = {
            MemoryCategory.DECISION: "decisions",
            MemoryCategory.PROJECT: "project",
            MemoryCategory.FACT: "facts",
            MemoryCategory.CONSTRAINT: "constraints",
            MemoryCategory.PREFERENCE: "preferences",
        }
        lines = [
            "LOOM_MEMORY_INDEX v3",
            f"active={len(records)} archived={len(archived)}",
        ]
        budget = max(800, min(8000, int(max_chars)))
        for category in (
            MemoryCategory.DECISION,
            MemoryCategory.PROJECT,
            MemoryCategory.FACT,
            MemoryCategory.CONSTRAINT,
            MemoryCategory.PREFERENCE,
        ):
            items = grouped[category]
            if not items:
                continue
            previews: list[str] = []
            for record in items[:2]:
                text = " ".join(record.text.split())
                if len(text) > 190:
                    text = text[:187].rstrip() + "..."
                previews.append(text)
            line = f"- {labels[category]} ({len(items)}): " + " | ".join(previews)
            if sum(len(item) for item in lines) + len(line) > budget:
                break
            lines.append(line)
        return {
            "version": 3,
            "active": len(records),
            "archived": len(archived),
            "categories": category_counts,
            "summary": "\n".join(lines),
        }

    def skill_candidates(
        self,
        *,
        workspace: str | Path,
        limit: int = 8,
    ) -> tuple[MemoryRecord, ...]:
        records = self.list_records(
            workspace=workspace,
            include_global=False,
            limit=500,
        )
        candidates = [
            record
            for record in records
            if record.category in {MemoryCategory.PROJECT, MemoryCategory.DECISION}
            and record.importance >= 4
            and (record.source_count >= 2 or record.usage_count >= 2)
        ]
        candidates.sort(
            key=lambda record: (
                record.source_count + record.usage_count,
                record.importance,
                record.updated_at,
            ),
            reverse=True,
        )
        return tuple(candidates[: max(1, min(32, int(limit)))])

    def counts(
        self,
        *,
        workspace: str | Path | None = None,
    ) -> dict[str, int]:
        with self._lock, self._connect() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memories WHERE status = 'active'"
                ).fetchone()[0]
            )
            pending = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM memory_candidates
                    WHERE state = ?
                    """,
                    (MemoryCandidateState.PENDING.value,),
                ).fetchone()[0]
            )
            evidence = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memory_evidence"
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
                        WHERE status = 'active'
                          AND (
                            scope = ?
                            OR (scope = ? AND scope_key = ?)
                          )
                        """,
                        (
                            MemoryScope.GLOBAL.value,
                            MemoryScope.WORKSPACE.value,
                            key,
                        ),
                    ).fetchone()[0]
                )
            archived_total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memories WHERE status = 'archived'"
                ).fetchone()[0]
            )
            if workspace is None:
                archived_visible = archived_total
            else:
                archived_visible = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM memories
                        WHERE status = 'archived'
                          AND (
                            scope = ?
                            OR (scope = ? AND scope_key = ?)
                          )
                        """,
                        (
                            MemoryScope.GLOBAL.value,
                            MemoryScope.WORKSPACE.value,
                            key,
                        ),
                    ).fetchone()[0]
                )
            usage_events = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memory_usage_events"
                ).fetchone()[0]
            )
        return {
            "total": total,
            "visible": visible,
            "archived": archived_total,
            "archived_visible": archived_visible,
            "pending": pending,
            "evidence": evidence,
            "usage_events": usage_events,
        }

    def thread_state(self, session_id: str) -> MemoryThreadState:
        key = _key(session_id, "session_id")
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM memory_thread_state
                WHERE session_id = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return MemoryThreadState(session_id=key)
        return MemoryThreadState(
            session_id=key,
            last_event_id=str(row["last_event_id"] or ""),
            last_turn_id=str(row["last_turn_id"] or ""),
            last_success_at=str(row["last_success_at"] or ""),
            failure_count=int(row["failure_count"] or 0),
            retry_at=float(row["retry_at"] or 0.0),
            last_error=str(row["last_error"] or ""),
            updated_at=str(row["updated_at"] or ""),
        )

    def mark_thread_success(
        self,
        session_id: str,
        *,
        last_event_id: str,
        last_turn_id: str,
    ) -> MemoryThreadState:
        key = _key(session_id, "session_id")
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memory_thread_state(
                    session_id, last_event_id, last_turn_id,
                    last_success_at, failure_count, retry_at,
                    last_error, updated_at
                ) VALUES (?, ?, ?, ?, 0, 0, '', ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    last_event_id = excluded.last_event_id,
                    last_turn_id = excluded.last_turn_id,
                    last_success_at = excluded.last_success_at,
                    failure_count = 0,
                    retry_at = 0,
                    last_error = '',
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    str(last_event_id or "").strip(),
                    str(last_turn_id or "").strip(),
                    now,
                    now,
                ),
            )
        return self.thread_state(key)

    def mark_thread_failure(self, session_id: str, error: str) -> float:
        key = _key(session_id, "session_id")
        now = utc_now()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT failure_count FROM memory_thread_state
                WHERE session_id = ?
                """,
                (key,),
            ).fetchone()
            failure_count = (
                int(row["failure_count"] or 0) + 1
                if row is not None
                else 1
            )
            delay = min(
                300.0,
                5.0 * (2 ** min(6, failure_count - 1)),
            )
            retry_at = time.time() + delay
            connection.execute(
                """
                INSERT INTO memory_thread_state(
                    session_id, failure_count, retry_at,
                    last_error, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    failure_count = excluded.failure_count,
                    retry_at = excluded.retry_at,
                    last_error = excluded.last_error,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    failure_count,
                    retry_at,
                    redact_secrets(str(error or ""))[:2000],
                    now,
                ),
            )
        return delay


def _ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> None:
    names = {
        str(row["name"])
        for row in connection.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }
    if column not in names:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def _key(value: str, name: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise ValueError(f"{name} must not be empty")
    return key


def _normalize(text: str) -> str:
    return " ".join(str(text or "").casefold().split())


def _terms(text: str) -> set[str]:
    return {
        match.group(0).casefold()
        for match in _TERM_RE.finditer(str(text or ""))
    }


def _fingerprint(
    scope: MemoryScope,
    scope_key: str,
    category: MemoryCategory,
    text: str,
) -> str:
    canonical = "\n".join(
        (
            scope.value,
            scope_key,
            category.value,
            _normalize(text),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _record_from_row(row: sqlite3.Row) -> MemoryRecord:
    keys = set(row.keys())
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
        usage_count=(
            int(row["usage_count"] or 0)
            if "usage_count" in keys
            else 0
        ),
        last_used_at=(
            str(row["last_used_at"] or "")
            if "last_used_at" in keys
            else ""
        ),
        confidence=(
            float(row["confidence"] or 1.0)
            if "confidence" in keys
            else 1.0
        ),
        status=(
            str(row["status"] or "active")
            if "status" in keys
            else "active"
        ),
        last_verified_at=(
            str(row["last_verified_at"] or "")
            if "last_verified_at" in keys
            else ""
        ),
        archived_at=(
            str(row["archived_at"] or "")
            if "archived_at" in keys
            else ""
        ),
        lifecycle_note=(
            str(row["lifecycle_note"] or "")
            if "lifecycle_note" in keys
            else ""
        ),
    )


def _age_days(value: str) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 3650.0
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()
            / 86400.0,
        )
    except ValueError:
        return 3650.0


def _usage_event_from_row(row: sqlite3.Row) -> MemoryUsageEvent:
    return MemoryUsageEvent(
        event_id=str(row["event_id"]),
        memory_id=str(row["memory_id"]),
        source_session_id=str(row["source_session_id"] or ""),
        source_turn_id=str(row["source_turn_id"] or ""),
        route=str(row["route"] or ""),
        score=float(row["score"] or 0.0),
        reason=str(row["reason"] or ""),
        created_at=str(row["created_at"]),
    )


def _evidence_from_row(row: sqlite3.Row) -> MemoryEvidence:
    return MemoryEvidence(
        evidence_id=str(row["evidence_id"]),
        memory_id=str(row["memory_id"]),
        candidate_id=str(row["candidate_id"]),
        extraction_id=str(row["extraction_id"]),
        source_session_id=str(row["source_session_id"]),
        source_turn_id=str(row["source_turn_id"]),
        excerpt=str(row["excerpt"]),
        created_at=str(row["created_at"]),
    )


__all__ = [
    "MemoryCandidate",
    "MemoryCandidateState",
    "MemoryCategory",
    "MemoryEvidence",
    "MemoryExtraction",
    "MemoryRecord",
    "MemorySearchHit",
    "MemoryScope",
    "MemoryUsageEvent",
    "MemoryStore",
    "MemoryThreadState",
    "redact_secrets",
    "workspace_memory_key",
]
