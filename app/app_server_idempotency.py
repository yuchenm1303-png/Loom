from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


_DEFAULT_RETENTION_SECONDS = 30 * 24 * 60 * 60
_DEFAULT_MAX_ROWS = 20_000


@dataclass(frozen=True, slots=True)
class AppServerIdempotencyEntry:
    operation: str
    client_input_id: str
    request_hash: str
    object_id: str
    state: str
    result: dict[str, Any] | None
    payload: dict[str, Any] | None
    created_at: float
    updated_at: float


class AppServerIdempotencyStore:
    """Durable replay ledger for App Server write admission.

    The ledger belongs to the App Server control plane rather than the MCP
    adapter. This makes retries survive tunnel / adapter / desktop process
    restarts while keeping the Runtime conversation history free of transport
    metadata.
    """

    def __init__(
        self,
        runtime_home: str | Path,
        *,
        retention_seconds: int = _DEFAULT_RETENTION_SECONDS,
        max_rows: int = _DEFAULT_MAX_ROWS,
    ) -> None:
        self.root = Path(runtime_home).expanduser().resolve() / "app_server"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "idempotency.db"
        self.retention_seconds = max(3600, int(retention_seconds))
        self.max_rows = max(100, int(max_rows))
        self._guard = threading.RLock()
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
        with self._guard, self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS app_server_idempotency (
                    operation TEXT NOT NULL,
                    client_input_id TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    payload_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(operation, client_input_id)
                );

                CREATE INDEX IF NOT EXISTS idx_app_server_idempotency_updated
                ON app_server_idempotency(updated_at);
                """
            )
            self._prune_locked(connection)

    def reserve(
        self,
        operation: str,
        client_input_id: str,
        request_hash: str,
        object_id_factory: Callable[[], str],
    ) -> tuple[AppServerIdempotencyEntry, bool]:
        op = _required(operation, "operation")
        key = _required(client_input_id, "client_input_id")
        digest = _required(request_hash, "request_hash")
        now = time.time()

        with self._guard, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM app_server_idempotency
                WHERE operation = ? AND client_input_id = ?
                """,
                (op, key),
            ).fetchone()
            if row is not None:
                entry = _entry_from_row(row)
                if entry.request_hash != digest:
                    connection.execute("ROLLBACK")
                    raise ValueError(
                        "clientInputId was already used for a different request"
                    )
                connection.execute("COMMIT")
                return entry, True

            object_id = _required(object_id_factory(), "object_id")
            connection.execute(
                """
                INSERT INTO app_server_idempotency(
                    operation, client_input_id, request_hash, object_id, state,
                    result_json, payload_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'reserved', NULL, NULL, ?, ?)
                """,
                (op, key, digest, object_id, now, now),
            )
            connection.execute("COMMIT")
            self._prune()
            entry = self.get(op, key)
            if entry is None:  # pragma: no cover - guarded by insert above
                raise RuntimeError("idempotency reservation disappeared")
            return entry, False

    def get(self, operation: str, client_input_id: str) -> AppServerIdempotencyEntry | None:
        op = _required(operation, "operation")
        key = _required(client_input_id, "client_input_id")
        with self._guard, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM app_server_idempotency
                WHERE operation = ? AND client_input_id = ?
                """,
                (op, key),
            ).fetchone()
        return _entry_from_row(row) if row is not None else None

    def prepare(
        self,
        operation: str,
        client_input_id: str,
        *,
        result: dict[str, Any],
        payload: dict[str, Any] | None = None,
    ) -> AppServerIdempotencyEntry:
        op = _required(operation, "operation")
        key = _required(client_input_id, "client_input_id")
        result_json = _json_object(result, "result")
        payload_json = None if payload is None else _json_object(payload, "payload")
        now = time.time()

        with self._guard, self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE app_server_idempotency
                SET state = 'prepared',
                    result_json = ?,
                    payload_json = ?,
                    updated_at = ?
                WHERE operation = ? AND client_input_id = ?
                """,
                (result_json, payload_json, now, op, key),
            )
            if cursor.rowcount != 1:
                raise KeyError("idempotency reservation not found")
        entry = self.get(op, key)
        if entry is None:  # pragma: no cover
            raise RuntimeError("prepared idempotency entry disappeared")
        return entry

    def complete(
        self,
        operation: str,
        client_input_id: str,
        *,
        result: dict[str, Any] | None = None,
    ) -> AppServerIdempotencyEntry:
        op = _required(operation, "operation")
        key = _required(client_input_id, "client_input_id")
        encoded = None if result is None else _json_object(result, "result")
        now = time.time()

        with self._guard, self._connect() as connection:
            if encoded is None:
                cursor = connection.execute(
                    """
                    UPDATE app_server_idempotency
                    SET state = 'completed', updated_at = ?
                    WHERE operation = ? AND client_input_id = ?
                    """,
                    (now, op, key),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE app_server_idempotency
                    SET state = 'completed', result_json = ?, updated_at = ?
                    WHERE operation = ? AND client_input_id = ?
                    """,
                    (encoded, now, op, key),
                )
            if cursor.rowcount != 1:
                raise KeyError("idempotency reservation not found")
        entry = self.get(op, key)
        if entry is None:  # pragma: no cover
            raise RuntimeError("completed idempotency entry disappeared")
        return entry

    def release_reserved(self, operation: str, client_input_id: str) -> bool:
        """Release only a reservation that never reached prepared state."""

        op = _required(operation, "operation")
        key = _required(client_input_id, "client_input_id")
        with self._guard, self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM app_server_idempotency
                WHERE operation = ? AND client_input_id = ? AND state = 'reserved'
                """,
                (op, key),
            )
        return cursor.rowcount == 1

    def discard(self, operation: str, client_input_id: str) -> bool:
        """Delete a request only when the caller knows no operation was launched."""

        op = _required(operation, "operation")
        key = _required(client_input_id, "client_input_id")
        with self._guard, self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM app_server_idempotency
                WHERE operation = ? AND client_input_id = ?
                """,
                (op, key),
            )
        return cursor.rowcount == 1

    def _prune(self) -> None:
        with self._guard, self._connect() as connection:
            self._prune_locked(connection)

    def _prune_locked(self, connection: sqlite3.Connection) -> None:
        cutoff = time.time() - self.retention_seconds
        connection.execute(
            """
            DELETE FROM app_server_idempotency
            WHERE updated_at < ?
            """,
            (cutoff,),
        )
        count_row = connection.execute(
            "SELECT COUNT(*) AS count FROM app_server_idempotency"
        ).fetchone()
        count = int(count_row["count"] if count_row is not None else 0)
        overflow = count - self.max_rows
        if overflow > 0:
            connection.execute(
                """
                DELETE FROM app_server_idempotency
                WHERE rowid IN (
                    SELECT rowid FROM app_server_idempotency
                    ORDER BY updated_at ASC
                    LIMIT ?
                )
                """,
                (overflow,),
            )


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} must not be empty")
    return text


def _json_object(value: dict[str, Any], name: str) -> str:
    if not isinstance(value, dict):
        raise TypeError(f"{name} must be an object")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_object(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("idempotency JSON payload must be an object")
    return parsed


def _entry_from_row(row: sqlite3.Row) -> AppServerIdempotencyEntry:
    return AppServerIdempotencyEntry(
        operation=str(row["operation"]),
        client_input_id=str(row["client_input_id"]),
        request_hash=str(row["request_hash"]),
        object_id=str(row["object_id"]),
        state=str(row["state"]),
        result=_decode_object(row["result_json"]),
        payload=_decode_object(row["payload_json"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


__all__ = [
    "AppServerIdempotencyEntry",
    "AppServerIdempotencyStore",
]
