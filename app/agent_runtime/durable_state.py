from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .storage import utc_now


class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    COMPLETE = "complete"
    BUDGET_LIMITED = "budget_limited"
    USAGE_LIMITED = "usage_limited"


class QueueItemState(str, Enum):
    PENDING = "pending"
    DISPATCHED = "dispatched"


@dataclass(frozen=True, slots=True)
class ThreadGoal:
    session_id: str
    objective: str
    status: GoalStatus
    token_budget: int | None
    tokens_used: int
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        session_id = str(self.session_id or "").strip()
        objective = str(self.objective or "").strip()
        if not session_id or not objective:
            raise ValueError("thread goal requires session_id and objective")
        budget = self.token_budget
        if budget is not None and int(budget) < 1:
            raise ValueError("thread goal token_budget must be positive")
        if int(self.tokens_used) < 0:
            raise ValueError("thread goal tokens_used must be non-negative")
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "objective", objective)
        object.__setattr__(self, "status", GoalStatus(self.status))
        object.__setattr__(self, "token_budget", None if budget is None else int(budget))
        object.__setattr__(self, "tokens_used", int(self.tokens_used))


@dataclass(frozen=True, slots=True)
class QueuedTurn:
    queue_id: str
    session_id: str
    text: str
    state: QueueItemState
    turn_id: str
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        queue_id = str(self.queue_id or "").strip()
        session_id = str(self.session_id or "").strip()
        text = str(self.text or "").strip()
        if not queue_id or not session_id or not text:
            raise ValueError("queued turn requires queue_id, session_id and text")
        object.__setattr__(self, "queue_id", queue_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "state", QueueItemState(self.state))
        object.__setattr__(self, "turn_id", str(self.turn_id or "").strip())


class DurableThreadStateStore:
    """SQLite-backed durable state that is intentionally separate from model history.

    The session snapshot remains the canonical conversation state. This store owns
    cross-turn intent: a long-lived goal and queued future turns. Queue claims are
    transactional so concurrent producers cannot dispatch the same item twice.
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
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS thread_goals (
                    session_id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL,
                    token_budget INTEGER,
                    tokens_used INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS thread_queue (
                    queue_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    state TEXT NOT NULL,
                    turn_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_thread_queue_session_state_created
                ON thread_queue(session_id, state, created_at, queue_id);
                """
            )

    def set_goal(
        self,
        session_id: str,
        objective: str,
        *,
        token_budget: int | None = None,
    ) -> ThreadGoal:
        key = _session_key(session_id)
        text = str(objective or "").strip()
        if not text:
            raise ValueError("goal objective must not be empty")
        if token_budget is not None and int(token_budget) < 1:
            raise ValueError("goal token_budget must be positive")
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO thread_goals(
                    session_id, objective, status, token_budget, tokens_used, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    objective = excluded.objective,
                    status = excluded.status,
                    token_budget = excluded.token_budget,
                    tokens_used = 0,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    text,
                    GoalStatus.ACTIVE.value,
                    None if token_budget is None else int(token_budget),
                    now,
                    now,
                ),
            )
        goal = self.get_goal(key)
        if goal is None:  # pragma: no cover - guarded by the write above
            raise RuntimeError("failed to persist thread goal")
        return goal

    def get_goal(self, session_id: str) -> ThreadGoal | None:
        key = _session_key(session_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM thread_goals WHERE session_id = ?",
                (key,),
            ).fetchone()
        return _goal_from_row(row) if row is not None else None

    def set_goal_status(self, session_id: str, status: GoalStatus | str) -> ThreadGoal:
        key = _session_key(session_id)
        resolved = GoalStatus(status)
        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE thread_goals SET status = ?, updated_at = ? WHERE session_id = ?",
                (resolved.value, now, key),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"thread goal not found: {key}")
        goal = self.get_goal(key)
        if goal is None:  # pragma: no cover
            raise RuntimeError("thread goal disappeared after status update")
        return goal

    def clear_goal(self, session_id: str) -> None:
        key = _session_key(session_id)
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM thread_goals WHERE session_id = ?", (key,))

    def add_goal_usage(self, session_id: str, tokens: int) -> ThreadGoal | None:
        key = _session_key(session_id)
        amount = int(tokens)
        if amount < 0:
            raise ValueError("goal usage increment must be non-negative")
        if amount == 0:
            return self.get_goal(key)
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM thread_goals WHERE session_id = ?",
                (key,),
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            tokens_used = int(row["tokens_used"]) + amount
            status = GoalStatus(str(row["status"]))
            budget = row["token_budget"]
            if (
                budget is not None
                and tokens_used >= int(budget)
                and status is GoalStatus.ACTIVE
            ):
                status = GoalStatus.BUDGET_LIMITED
            connection.execute(
                """
                UPDATE thread_goals
                SET tokens_used = ?, status = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (tokens_used, status.value, now, key),
            )
            connection.execute("COMMIT")
        return self.get_goal(key)

    def enqueue(self, session_id: str, text: str) -> QueuedTurn:
        key = _session_key(session_id)
        body = str(text or "").strip()
        if not body:
            raise ValueError("queued turn text must not be empty")
        if len(body) > 200_000:
            raise ValueError("queued turn text exceeds 200,000 characters")
        now = utc_now()
        queue_id = str(uuid.uuid4())
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO thread_queue(
                    queue_id, session_id, text, state, turn_id, created_at, updated_at
                ) VALUES (?, ?, ?, ?, '', ?, ?)
                """,
                (queue_id, key, body, QueueItemState.PENDING.value, now, now),
            )
        return QueuedTurn(
            queue_id=queue_id,
            session_id=key,
            text=body,
            state=QueueItemState.PENDING,
            turn_id="",
            created_at=now,
            updated_at=now,
        )

    def list_queue(self, session_id: str) -> tuple[QueuedTurn, ...]:
        key = _session_key(session_id)
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM thread_queue
                WHERE session_id = ?
                ORDER BY created_at ASC, queue_id ASC
                """,
                (key,),
            ).fetchall()
        return tuple(_queue_from_row(row) for row in rows)

    def pending_count(self, session_id: str) -> int:
        key = _session_key(session_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM thread_queue
                WHERE session_id = ? AND state = ?
                """,
                (key, QueueItemState.PENDING.value),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def claim_next(self, session_id: str, turn_id: str) -> QueuedTurn | None:
        key = _session_key(session_id)
        turn = str(turn_id or "").strip()
        if not turn:
            raise ValueError("queue claim requires turn_id")
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM thread_queue
                WHERE session_id = ? AND state = ?
                ORDER BY created_at ASC, queue_id ASC
                LIMIT 1
                """,
                (key, QueueItemState.PENDING.value),
            ).fetchone()
            if row is None:
                connection.execute("COMMIT")
                return None
            cursor = connection.execute(
                """
                UPDATE thread_queue
                SET state = ?, turn_id = ?, updated_at = ?
                WHERE queue_id = ? AND state = ?
                """,
                (
                    QueueItemState.DISPATCHED.value,
                    turn,
                    now,
                    str(row["queue_id"]),
                    QueueItemState.PENDING.value,
                ),
            )
            if cursor.rowcount != 1:  # pragma: no cover - serialized by BEGIN IMMEDIATE
                connection.execute("ROLLBACK")
                return None
            connection.execute("COMMIT")
        return QueuedTurn(
            queue_id=str(row["queue_id"]),
            session_id=key,
            text=str(row["text"]),
            state=QueueItemState.DISPATCHED,
            turn_id=turn,
            created_at=str(row["created_at"]),
            updated_at=now,
        )

    def complete_claim(self, queue_id: str, turn_id: str) -> None:
        queue_key = str(queue_id or "").strip()
        turn = str(turn_id or "").strip()
        if not queue_key or not turn:
            raise ValueError("queue completion requires queue_id and turn_id")
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                DELETE FROM thread_queue
                WHERE queue_id = ? AND state = ? AND turn_id = ?
                """,
                (queue_key, QueueItemState.DISPATCHED.value, turn),
            )

    def release_claim(self, queue_id: str, turn_id: str) -> None:
        queue_key = str(queue_id or "").strip()
        turn = str(turn_id or "").strip()
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE thread_queue
                SET state = ?, turn_id = '', updated_at = ?
                WHERE queue_id = ? AND state = ? AND turn_id = ?
                """,
                (
                    QueueItemState.PENDING.value,
                    now,
                    queue_key,
                    QueueItemState.DISPATCHED.value,
                    turn,
                ),
            )

    def delete_queue_item(self, session_id: str, queue_id: str) -> bool:
        key = _session_key(session_id)
        queue_key = str(queue_id or "").strip()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM thread_queue WHERE session_id = ? AND queue_id = ?",
                (key, queue_key),
            )
        return cursor.rowcount == 1

    def reconcile_dispatches(self, session_id: str, current_turn_id: str) -> dict[str, int]:
        """Resolve queue claims left behind by a previous process.

        A claim bound to the session's persisted current turn is considered already
        delivered and is removed to prevent duplicate user input. Other stale claims
        are returned to pending because no durable turn adopted them.
        """

        key = _session_key(session_id)
        current_turn = str(current_turn_id or "").strip()
        completed = 0
        released = 0
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT queue_id, turn_id FROM thread_queue
                WHERE session_id = ? AND state = ?
                """,
                (key, QueueItemState.DISPATCHED.value),
            ).fetchall()
            for row in rows:
                queue_id = str(row["queue_id"])
                turn_id = str(row["turn_id"] or "")
                if current_turn and turn_id == current_turn:
                    connection.execute("DELETE FROM thread_queue WHERE queue_id = ?", (queue_id,))
                    completed += 1
                else:
                    connection.execute(
                        """
                        UPDATE thread_queue
                        SET state = ?, turn_id = '', updated_at = ?
                        WHERE queue_id = ?
                        """,
                        (QueueItemState.PENDING.value, now, queue_id),
                    )
                    released += 1
            connection.execute("COMMIT")
        return {"completed": completed, "released": released}


def _session_key(value: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise ValueError("session_id must not be empty")
    return key


def _goal_from_row(row: sqlite3.Row) -> ThreadGoal:
    budget = row["token_budget"]
    return ThreadGoal(
        session_id=str(row["session_id"]),
        objective=str(row["objective"]),
        status=GoalStatus(str(row["status"])),
        token_budget=None if budget is None else int(budget),
        tokens_used=int(row["tokens_used"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _queue_from_row(row: sqlite3.Row) -> QueuedTurn:
    return QueuedTurn(
        queue_id=str(row["queue_id"]),
        session_id=str(row["session_id"]),
        text=str(row["text"]),
        state=QueueItemState(str(row["state"])),
        turn_id=str(row["turn_id"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


__all__ = [
    "DurableThreadStateStore",
    "GoalStatus",
    "QueueItemState",
    "QueuedTurn",
    "ThreadGoal",
]
