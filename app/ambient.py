"""Code-only durable wakeups. Execution is delegated to the existing App Server."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class AdmissionBusy(RuntimeError):
    """No operation was started; safe to keep the wakeup queued."""


class AmbientScheduler:
    def __init__(self, home: Path, executor: Any, *, clock=time.time):
        home.mkdir(parents=True, exist_ok=True)
        self.executor, self.clock = executor, clock
        self.guard = threading.RLock()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.error = ""
        self.owner = False
        self.lease = (home / "ambient.lock").open("a+b")
        try:
            if os.fstat(self.lease.fileno()).st_size == 0:
                self.lease.write(b"0")
                self.lease.flush()
            self.lease.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.owner = True
        except OSError:
            self.lease.close()
            raise RuntimeError("ambient scheduler already has an owner") from None
        self.db = sqlite3.connect(home / "ambient.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(task TEXT, key TEXT, PRIMARY KEY(task,key));
            CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, task TEXT, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS ambient_run_state ON runs(json_extract(data,'$.status'), task);
            CREATE TABLE IF NOT EXISTS notices(id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS config(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS leases(resource TEXT PRIMARY KEY, operation TEXT NOT NULL, state TEXT NOT NULL);
        """)
        # Claimed operations may have reached external tools. Never replay them.
        with self.guard, self.db:
            self.db.execute("UPDATE leases SET state='needs_review'")
            for run in self._runs("starting", "running", "waiting_approval", "cancelling"):
                if run["status"] in {"starting", "running", "waiting_approval", "cancelling"}:
                    run.update(status="needs_review", error="Host restarted; inspect thread before retrying")
                    self._save("runs", run)
                    self._notice(run)
                    task = self._task(run["task"])
                    if task:
                        task["enabled"] = False
                        self._save("tasks", task)
                        self._cancel_pending(task["id"])

    def _rows(self, table: str, *, limit: int | None = None) -> list[dict]:
        query = f"SELECT data FROM {table} ORDER BY rowid"
        if limit is not None:
            query += " DESC LIMIT ?"
        rows = [json.loads(row["data"]) for row in self.db.execute(query, (limit,) if limit is not None else ())]
        return rows[::-1] if limit is not None else rows

    def _runs(self, *statuses: str, task: str | None = None) -> list[dict]:
        query = "SELECT data FROM runs WHERE json_extract(data,'$.status') IN (" + ",".join("?" for _ in statuses) + ")"
        params = list(statuses)
        if task is not None:
            query += " AND task=?"
            params.append(task)
        return [json.loads(row[0]) for row in self.db.execute(query + " ORDER BY rowid", params)]

    def _save(self, table: str, item: dict) -> None:
        if table == "runs":
            self.db.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?)", (item["id"], item["task"], json.dumps(item)))
        else:
            self.db.execute(f"INSERT OR REPLACE INTO {table} VALUES(?,?)", (item["id"], json.dumps(item)))

    def _task(self, task_id: str) -> dict | None:
        row = self.db.execute("SELECT data FROM tasks WHERE id=?", (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def start(self) -> None:
        with self.guard:
            if self.worker and self.worker.is_alive():
                return
            self.stop_event.clear()
            self.worker = threading.Thread(target=self._loop, name="loom-ambient", daemon=True)
            self.worker.start()

    def close(self) -> None:
        self.stop_event.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join()
        with self.guard:
            if not self.owner:
                return
            self.owner = False
            self.db.close()
            self.lease.close()

    def _loop(self) -> None:
        while not self.stop_event.wait(1):
            try:
                self.tick()
                self.error = ""
            except Exception as exc:
                self.error = str(exc)

    def snapshot(self) -> dict:
        with self.guard:
            return {"online": bool(self.owner and self.worker and self.worker.is_alive()),
                    "maxConcurrent": 2,
                    "paused": self._paused(), "error": self.error,
                    "tasks": self._rows("tasks"), "runs": self._rows("runs", limit=100),
                    "resources": [dict(row) for row in self.db.execute("SELECT * FROM leases")],
                    "notifications": self._rows("notices", limit=100), "externalDelivery": "not_configured"}

    def _paused(self) -> bool:
        row = self.db.execute("SELECT value FROM config WHERE key='paused'").fetchone()
        return bool(row and row[0] == "true")

    def history(self, *, before: int | None = None, limit: int = 100) -> dict:
        if not 1 <= limit <= 500 or (before is not None and before < 1):
            raise ValueError("invalid history cursor or limit")
        with self.guard:
            rows = self.db.execute("SELECT rowid,data FROM runs" + (" WHERE rowid<?" if before else "")
                + " ORDER BY rowid DESC LIMIT ?", (before, limit) if before else (limit,)).fetchall()
            return {"runs": [json.loads(row[1]) for row in rows],
                    "nextCursor": rows[-1][0] if len(rows) == limit else None}

    def acquire_resources(self, operation_id: str, resources: list[str]) -> bool:
        """Shared exclusive leases. Restarted owners require explicit verified release."""
        if not operation_id or not resources or any(not resource for resource in resources):
            raise ValueError("operation id and resource names are required")
        with self.guard, self.db:
            for resource in resources:
                row = self.db.execute("SELECT operation,state FROM leases WHERE resource=?", (resource,)).fetchone()
                if row and (row[0] != operation_id or row[1] != "active"):
                    return False
            for resource in set(resources):
                self.db.execute("INSERT OR REPLACE INTO leases VALUES(?,?,'active')", (resource, operation_id))
            return True

    def release_resources(self, operation_id: str) -> None:
        with self.guard, self.db:
            self.db.execute("DELETE FROM leases WHERE operation=?", (operation_id,))

    def pause(self, paused: bool) -> dict:
        with self.guard, self.db:
            self.db.execute("INSERT OR REPLACE INTO config VALUES('paused',?)", (json.dumps(paused),))
        return self.snapshot()

    def put(self, values: dict) -> dict:
        with self.guard, self.db:
            task_id = str(values.get("id") or uuid.uuid4())
            old = self._task(task_id)
            task = {**(old or {}), **values, "id": task_id}
            if old and task.get("threadId") != old["threadId"] and any(
                run["task"] == task_id and run["status"] in {"queued", "starting", "running", "waiting_approval", "cancelling"}
                for run in self._runs("queued", "starting", "running", "waiting_approval", "cancelling", task=task_id)
            ):
                raise ValueError("stop outstanding task runs before changing its thread")
            for key in ("threadId", "prompt", "name", "kind"):
                if not isinstance(task.get(key), str) or not task[key].strip():
                    raise ValueError(f"{key} is required")
            if task["kind"] not in {"once", "interval", "file", "event"}:
                raise ValueError("unsupported trigger kind")
            # This also validates that the thread exists; never change its permissions.
            self.executor.validate(task)
            for key, default in (("maxRuns", 100), ("maxTokens", 100000)):
                value = task.get(key, default)
                if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                    raise ValueError(f"{key} must be a positive integer")
                task[key] = value
            if task["kind"] in {"once", "interval"}:
                raw_due = task.get("nextAt", self.clock())
                if not (raw_due is None and task["kind"] == "once" and not task.get("enabled", True)):
                    due = float(raw_due)
                    if not math.isfinite(due) or due < 0:
                        raise ValueError("nextAt must be a finite UTC epoch")
                    task["nextAt"] = due
            if task["kind"] == "interval":
                interval = float(task.get("seconds", 0))
                if not math.isfinite(interval) or interval < 1:
                    raise ValueError("interval must be at least one second")
                task["seconds"] = interval
            # Internal counters/cursors cannot be reset by a generic task update.
            for key, default in (("runsUsed", 0), ("tokensUsed", 0), ("failures", 0), ("retryAt", 0), ("lastResult", "")):
                task[key] = (old or {}).get(key, default)
            if task["kind"] == "file":
                task["fingerprint"] = ((old or {}).get("fingerprint")
                    if old and old.get("path") == task.get("path") else self._fingerprint(task["path"]))
            if not isinstance(task.get("enabled", True), bool):
                raise ValueError("enabled must be a boolean")
            task["enabled"] = task.get("enabled", True)
            self._save("tasks", task)
            if not task["enabled"]:
                self._cancel_pending(task_id)
            return task

    @staticmethod
    def _fingerprint(path: str) -> str:
        try:
            stat = Path(path).stat()
            return f"{stat.st_mtime_ns}:{stat.st_size}:{stat.st_ino}"
        except FileNotFoundError:
            return "missing"

    def _cancel_pending(self, task_id: str) -> None:
        for run in self._runs("queued", task=task_id):
            if run["task"] == task_id and run["status"] == "queued":
                run["status"] = "cancelled"
                self._save("runs", run)

    def cancel(self, task_id: str, *, delete: bool = False) -> dict:
        with self.guard, self.db:
            task = self._task(task_id)
            if not task:
                raise ValueError("unknown task")
            task["enabled"] = False
            self._save("tasks", task)
            self._cancel_pending(task_id)
            self.db.commit()  # cancellation failure must never re-enable a trigger
            for run in self._runs("running", "waiting_approval", task=task_id):
                if run["task"] == task_id and run["status"] in {"running", "waiting_approval"}:
                    try:
                        self.executor.cancel(run)
                        run["status"] = "cancelling"
                    except Exception as exc:
                        run.update(status="needs_review", error=f"Cancellation needs inspection: {exc}")
                    self._save("runs", run)
                    self._notice(run)
            if delete:
                self.db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            return {"cancelled": True}

    def event(self, task_id: str, key: str, *, source: str = "adapter") -> bool:
        if not key or len(key) > 512 or len(source) > 100:
            raise ValueError("event key/source is missing or too long")
        with self.guard, self.db:
            task = self._task(task_id)
            if not task or not task["enabled"]:
                return False
            inserted = self.db.execute("INSERT OR IGNORE INTO events VALUES(?,?)", (task_id, key)).rowcount
            if not inserted:
                return False
            # Retain dedup receipts even when changes merge into the pending wakeup.
            pending = next(iter(self._runs("queued", task=task_id)), None)
            if pending:
                pending["merged"] += 1
                self._save("runs", pending)
            else:
                self._save("runs", {"id": str(uuid.uuid4()), "task": task_id, "threadId": task["threadId"],
                    "source": source, "eventKey": key, "status": "queued", "createdAt": self.clock(), "merged": 1})
            return True

    def _notice(self, run: dict) -> None:
        if run["status"] == "completed" and str(run.get("result") or "").strip() == "AMBIENT_NO_CHANGE":
            return
        task = self._task(run["task"])
        signature = hashlib.sha256(json.dumps([run["status"], run.get("result"), run.get("error")]).encode()).hexdigest()
        if task and task.get("lastResult") == signature:
            return
        if task:
            task["lastResult"] = signature
            self._save("tasks", task)
        self._save("notices", {"id": run["id"] + ":" + run["status"], "runId": run["id"],
            "task": run["task"], "status": run["status"], "text": run.get("error") or run.get("result") or run["status"],
            "createdAt": self.clock(), "delivery": "local_only"})

    def tick(self) -> None:
        with self.guard, self.db:
            now = self.clock()
            # Reconcile admitted work even while the scheduler is paused.
            for run in self._runs("running", "waiting_approval", "cancelling"):
                if run["status"] not in {"running", "waiting_approval", "cancelling"}:
                    continue
                try:
                    outcome = self.executor.poll(run)
                except Exception as exc:
                    outcome = {"status": "needs_review", "error": str(exc)}
                if outcome:
                    previous = run["status"]
                    run.update(outcome)
                    if previous == "cancelling":
                        run["status"] = "cancelled"
                    self._save("runs", run)
                    if run["status"] != "running" and run["status"] != previous:
                        task = self._task(run["task"])
                        if task and run["status"] != "waiting_approval":
                            task["tokensUsed"] += run.get("tokens", 0)
                            task["failures"] = task["failures"] + 1 if run["status"] == "failed" else 0
                            task["retryAt"] = now + min(3600, 2 ** min(task["failures"], 11)) if task["failures"] else 0
                            if run["status"] in {"needs_review", "cancelled", "interrupted"}:
                                task["enabled"] = False
                                self._cancel_pending(task["id"])
                            self._save("tasks", task)
                        self._notice(run)
            if self._paused():
                return
            for task in self._rows("tasks"):
                if not task["enabled"]:
                    continue
                if task["kind"] in {"once", "interval"} and task["nextAt"] <= now:
                    due = task["nextAt"]
                    self.event(task["id"], f"time:{due}", source=task["kind"])
                    task["nextAt"] = now + task["seconds"] if task["kind"] == "interval" else None
                    if task["kind"] == "once":
                        task["enabled"] = False  # queued one-shot remains admissible
                    self._save("tasks", task)
                elif task["kind"] == "file":
                    try:
                        fingerprint = self._fingerprint(task["path"])
                    except OSError as exc:
                        task["enabled"] = False
                        self._save("tasks", task)
                        self._cancel_pending(task["id"])
                        failed = {"id": str(uuid.uuid4()), "task": task["id"], "threadId": task["threadId"],
                            "source": "file", "status": "failed", "error": str(exc), "createdAt": now}
                        self._save("runs", failed)
                        self._notice(failed)
                        continue
                    if fingerprint != task["fingerprint"]:
                        self.event(task["id"], f"file:{uuid.uuid4()}", source="file")
                        task["fingerprint"] = fingerprint
                        self._save("tasks", task)
            for run in self._runs("queued"):
                if run["status"] != "queued":
                    continue
                task = self._task(run["task"])
                if not task or task["retryAt"] > now:
                    continue
                if task["runsUsed"] >= task["maxRuns"] or task["tokensUsed"] >= task["maxTokens"]:
                    run.update(status="limited", error="Automatic usage limit reached")
                    self._save("runs", run)
                    task["enabled"] = False
                    self._save("tasks", task)
                    self._notice(run)
                    continue
                active = self._runs("running", "waiting_approval", "cancelling")
                if any(r["threadId"] == run["threadId"] for r in active):
                    continue
                try:
                    if not self.executor.ready(run["threadId"]):
                        continue
                except Exception as exc:
                    run.update(status="needs_review", error=str(exc))
                    task["enabled"] = False
                    self._save("tasks", task)
                    self._cancel_pending(task["id"])
                    self._save("runs", run)
                    self._notice(run)
                    continue
                if len(active) >= 2:
                    continue
                # Commit intent BEFORE runtime admission. Crash in this window is review-only.
                run["status"] = "starting"
                self._save("runs", run)
                self.db.commit()
                try:
                    admission = self.executor.start(task, run)
                except AdmissionBusy:
                    run["status"] = "queued"
                except Exception as exc:
                    run.update(status="needs_review", error=str(exc))
                    task["enabled"] = False
                    self._cancel_pending(task["id"])
                else:
                    run.update(admission, status="running")
                    task["runsUsed"] += 1
                self._save("runs", run)
                self._save("tasks", task)
                if run["status"] == "needs_review":
                    self._notice(run)
