"""A SQLite connection that stays open for the stores that read their small state database at every step.

Those stores (goals and queued turns, the agent graph, memory) opened a connection for each call and closed it
again. On a WAL database the last connection to close checkpoints and deletes the ``-wal`` and ``-shm`` files, and
the next one creates them again, so a call that reads one row cost 8-10 ms on Windows (measured: 0.01 ms on a
connection that stays open). A step makes a dozen such calls.

Every use of these stores is ``with self._lock, self._connect() as connection:``, so a single connection per store
is only ever used by one thread at a time. That is what makes it safe to keep one open: this class relies on its
caller holding the store's lock around ``use()`` and does not lock for them.
"""
from __future__ import annotations

import sqlite3
import threading
import weakref
from contextlib import contextmanager
from pathlib import Path


def _close_quietly(box: list) -> None:
    while box:
        connection = box.pop()
        try:
            connection.close()
        except sqlite3.Error:
            pass


class KeptConnection:
    def __init__(self, path: str | Path, *, foreign_keys: bool = False, timeout: float = 30.0) -> None:
        self._path = path
        self._foreign_keys = foreign_keys
        self._timeout = timeout
        self._box: list[sqlite3.Connection] = []  # shared with the finalizer, which must not hold ``self``
        self._busy = False
        self._finalizer = weakref.finalize(self, _close_quietly, self._box)

    def _open(self) -> sqlite3.Connection:
        # ``check_same_thread=False``: the store's lock, not the thread that opened it, decides who uses it.
        connection = sqlite3.connect(self._path, timeout=self._timeout, isolation_level=None, check_same_thread=False)
        try:
            connection.row_factory = sqlite3.Row
            if self._foreign_keys:
                connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
        except BaseException:
            connection.close()
            raise
        return connection

    @contextmanager
    def use(self):
        """The kept connection, opened on first use, with the commit/rollback ``with connection:`` gives."""
        if self._busy:
            # Entered again from inside a block that is still open (nothing does, but it used to work, because every
            # use had a connection of its own): give this one its own, so that it cannot commit the outer block.
            connection = self._open()
            try:
                with connection:
                    yield connection
            finally:
                connection.close()
            return
        if not self._box:
            self._box.append(self._open())
        connection = self._box[0]
        self._busy = True
        try:
            with connection:
                yield connection
        finally:
            self._busy = False
            if connection.in_transaction:
                connection.rollback()

    def close(self) -> None:
        """Release the file handles (the next use opens the connection again). Hold the store's lock."""
        _close_quietly(self._box)
