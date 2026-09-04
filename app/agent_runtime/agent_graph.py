from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .storage import utc_now


class AgentHistoryMode(str, Enum):
    NONE = "none"
    RECENT = "recent"
    ALL = "all"


class AgentRelationStatus(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class AgentNode:
    session_id: str
    parent_session_id: str
    root_session_id: str
    role: str
    history_mode: AgentHistoryMode
    relation_status: AgentRelationStatus
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        session_id = str(self.session_id or "").strip()
        parent = str(self.parent_session_id or "").strip()
        root = str(self.root_session_id or "").strip()
        role = str(self.role or "worker").strip()
        if not session_id or not parent or not root:
            raise ValueError("agent node requires session, parent, and root ids")
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "parent_session_id", parent)
        object.__setattr__(self, "root_session_id", root)
        object.__setattr__(self, "role", role or "worker")
        object.__setattr__(self, "history_mode", AgentHistoryMode(self.history_mode))
        object.__setattr__(self, "relation_status", AgentRelationStatus(self.relation_status))


class AgentGraphStore:
    """SQLite-backed parent/child topology for independent Loom sessions.

    A graph node stores only durable relationship metadata. The child session's
    conversation, goal, queue, permissions, and workspace remain owned by the same
    canonical stores used by every other Loom thread.
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
                CREATE TABLE IF NOT EXISTS agent_graph (
                    session_id TEXT PRIMARY KEY,
                    parent_session_id TEXT NOT NULL,
                    root_session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    history_mode TEXT NOT NULL,
                    relation_status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_agent_graph_parent
                ON agent_graph(parent_session_id, relation_status, created_at, session_id);

                CREATE INDEX IF NOT EXISTS idx_agent_graph_root
                ON agent_graph(root_session_id, relation_status, created_at, session_id);
                """
            )

    def add_child(
        self,
        *,
        parent_session_id: str,
        child_session_id: str,
        role: str = "worker",
        history_mode: AgentHistoryMode | str = AgentHistoryMode.RECENT,
    ) -> AgentNode:
        parent = _key(parent_session_id)
        child = _key(child_session_id)
        if parent == child:
            raise ValueError("an agent cannot be its own parent")
        resolved_history = AgentHistoryMode(history_mode)
        role_name = str(role or "worker").strip() or "worker"
        if len(role_name) > 100:
            raise ValueError("agent role exceeds 100 characters")
        root = self.root_for_session(parent)
        now = utc_now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_graph(
                    session_id, parent_session_id, root_session_id, role,
                    history_mode, relation_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    child,
                    parent,
                    root,
                    role_name,
                    resolved_history.value,
                    AgentRelationStatus.ACTIVE.value,
                    now,
                    now,
                ),
            )
        node = self.get(child)
        if node is None:  # pragma: no cover
            raise RuntimeError("failed to persist agent graph node")
        return node

    def get(self, session_id: str) -> AgentNode | None:
        key = _key(session_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM agent_graph WHERE session_id = ?",
                (key,),
            ).fetchone()
        return _node_from_row(row) if row is not None else None

    def root_for_session(self, session_id: str) -> str:
        key = _key(session_id)
        node = self.get(key)
        return node.root_session_id if node is not None else key

    def depth(self, session_id: str) -> int:
        current = _key(session_id)
        depth = 0
        seen: set[str] = set()
        while True:
            if current in seen:
                raise RuntimeError("agent graph contains a parent cycle")
            seen.add(current)
            node = self.get(current)
            if node is None:
                return depth
            depth += 1
            current = node.parent_session_id
            if depth > 64:
                raise RuntimeError("agent graph depth exceeds safety limit")

    def list_children(
        self,
        parent_session_id: str,
        *,
        include_closed: bool = False,
    ) -> tuple[AgentNode, ...]:
        parent = _key(parent_session_id)
        query = "SELECT * FROM agent_graph WHERE parent_session_id = ?"
        params: list[object] = [parent]
        if not include_closed:
            query += " AND relation_status = ?"
            params.append(AgentRelationStatus.ACTIVE.value)
        query += " ORDER BY created_at ASC, session_id ASC"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return tuple(_node_from_row(row) for row in rows)

    def list_tree(
        self,
        session_id: str,
        *,
        include_closed: bool = True,
    ) -> tuple[AgentNode, ...]:
        root = self.root_for_session(session_id)
        query = "SELECT * FROM agent_graph WHERE root_session_id = ?"
        params: list[object] = [root]
        if not include_closed:
            query += " AND relation_status = ?"
            params.append(AgentRelationStatus.ACTIVE.value)
        query += " ORDER BY created_at ASC, session_id ASC"
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return tuple(_node_from_row(row) for row in rows)

    def active_count(self, session_id: str) -> int:
        root = self.root_for_session(session_id)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM agent_graph
                WHERE root_session_id = ? AND relation_status = ?
                """,
                (root, AgentRelationStatus.ACTIVE.value),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def assert_same_tree(self, caller_session_id: str, target_session_id: str) -> AgentNode:
        caller = _key(caller_session_id)
        target = _key(target_session_id)
        node = self.get(target)
        if node is None:
            raise PermissionError("target is not a child agent")
        if self.root_for_session(caller) != node.root_session_id:
            raise PermissionError("target agent belongs to a different Loom agent tree")
        return node

    def close(self, session_id: str) -> AgentNode:
        key = _key(session_id)
        now = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE agent_graph
                SET relation_status = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (AgentRelationStatus.CLOSED.value, now, key),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"agent node not found: {key}")
        node = self.get(key)
        if node is None:  # pragma: no cover
            raise RuntimeError("agent node disappeared after close")
        return node


def _key(value: str) -> str:
    key = str(value or "").strip()
    if not key:
        raise ValueError("session id must not be empty")
    return key


def _node_from_row(row: sqlite3.Row) -> AgentNode:
    return AgentNode(
        session_id=str(row["session_id"]),
        parent_session_id=str(row["parent_session_id"]),
        root_session_id=str(row["root_session_id"]),
        role=str(row["role"]),
        history_mode=AgentHistoryMode(str(row["history_mode"])),
        relation_status=AgentRelationStatus(str(row["relation_status"])),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


__all__ = [
    "AgentGraphStore",
    "AgentHistoryMode",
    "AgentNode",
    "AgentRelationStatus",
]
