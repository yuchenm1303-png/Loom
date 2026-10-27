"""Project control state and dispatch intent commit in one SQLite transaction."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
import uuid


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class ProjectStore:
    def __init__(self, home: Path):
        home.mkdir(parents=True, exist_ok=True)
        self.home = home
        self.lock = threading.RLock()
        self.db = sqlite3.connect(home / "projects.sqlite3", isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS project_runner_schema_versions(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO project_runner_schema_versions VALUES(1);
            CREATE TABLE IF NOT EXISTS project_runs(id TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_commands(operation TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES project_runs(id));
            CREATE TABLE IF NOT EXISTS project_episodes(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES project_runs(id), session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL, data TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS project_active_session ON project_episodes(session_id)
                WHERE state IN ('prepared','running','waiting_approval','outcome_unknown');
            CREATE TABLE IF NOT EXISTS project_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL REFERENCES project_runs(id), kind TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_preparation_receipts(episode_id TEXT PRIMARY KEY REFERENCES project_episodes(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_candidates(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES project_runs(id), data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS project_assessments(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES project_runs(id),
                candidate_id TEXT NOT NULL REFERENCES project_candidates(id), episode_id TEXT NOT NULL UNIQUE REFERENCES project_episodes(id), data TEXT NOT NULL);
            INSERT OR IGNORE INTO project_runner_schema_versions VALUES(2);
        """)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield self.db
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def close(self):
        with self.lock:
            self.db.close()

    def _read(self, db, run_id):
        row = db.execute("SELECT data FROM project_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("unknown project run")
        return json.loads(row[0])

    def get(self, run_id):
        with self.lock:
            return self._read(self.db, run_id)

    def preparation_receipt(self, episode_id, result=None):
        with self.transaction() as db:
            if result is not None:
                db.execute("INSERT OR IGNORE INTO project_preparation_receipts VALUES(?,?)", (episode_id, encoded(result)))
            row = db.execute("SELECT data FROM project_preparation_receipts WHERE episode_id=?", (episode_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def session_run(self, session_id):
        with self.lock:
            row = self.db.execute("SELECT run_id FROM project_episodes WHERE session_id=? ORDER BY rowid DESC LIMIT 1",
                                  (session_id,)).fetchone()
            return self._read(self.db, row[0]) if row else None

    def put_candidate(self, db, run, snapshot):
        db.execute("INSERT INTO project_candidates VALUES(?,?,?)", (snapshot["id"], run["id"], encoded(snapshot)))
        run["reviewCandidate"] = {key: value for key, value in snapshot.items() if key != "files"}

    def candidate(self, run):
        with self.lock:
            row = self.db.execute("SELECT data FROM project_candidates WHERE id=? AND run_id=?",
                                  (run["reviewCandidate"]["id"], run["id"])).fetchone()
            if row is None:
                raise ValueError("Missing project candidate snapshot")
            return json.loads(row[0])

    def put_assessment(self, db, run, episode, assessment):
        assessment = {**assessment, "id": str(uuid.uuid4())}
        db.execute("INSERT INTO project_assessments VALUES(?,?,?,?,?)", (assessment["id"], run["id"],
                   assessment["candidateId"], episode["id"], encoded(assessment)))
        run["lastAssessment"] = assessment

    def assessments(self, run_id):
        self.get(run_id)
        with self.lock:
            return [json.loads(row[0]) for row in self.db.execute(
                "SELECT data FROM project_assessments WHERE run_id=? ORDER BY rowid DESC LIMIT 100", (run_id,))]

    def list(self):
        with self.lock:
            return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM project_runs ORDER BY rowid")]

    def save(self, db, run, kind):
        run["revision"] += 1
        run["updatedAt"] = time.time()
        db.execute("UPDATE project_runs SET revision=?,data=? WHERE id=?", (run["revision"], encoded(run), run["id"]))
        db.execute("INSERT INTO project_events(run_id,kind,data) VALUES(?,?,?)", (run["id"], kind, encoded(run)))

    def command(self, operation, request, apply):
        if not isinstance(operation, str) or not operation.strip() or len(operation) > 128:
            raise ValueError("operationId must be a nonempty string of at most 128 characters")
        fingerprint = hashlib.sha256(encoded(request).encode()).hexdigest()
        with self.transaction() as db:
            old = db.execute("SELECT fingerprint,run_id FROM project_commands WHERE operation=?", (operation,)).fetchone()
            if old:
                if old[0] != fingerprint:
                    raise ValueError("operationId already used for different arguments")
                return self._read(db, old[1])
            run = apply(db)
            db.execute("INSERT INTO project_commands VALUES(?,?,?)", (operation, fingerprint, run["id"]))
            return run

    def lookup(self, operation, request):
        if not isinstance(operation, str) or not operation.strip() or len(operation) > 128:
            raise ValueError("operationId must be a nonempty string of at most 128 characters")
        fingerprint = hashlib.sha256(encoded(request).encode()).hexdigest()
        with self.lock:
            row = self.db.execute("SELECT fingerprint,run_id FROM project_commands WHERE operation=?", (operation,)).fetchone()
            if row:
                if row[0] != fingerprint:
                    raise ValueError("operationId already used for different arguments")
                return self._read(self.db, row[1])
        return None

    def create(self, operation, values, *, request=None):
        def apply(db):
            run = {**values, "id": str(uuid.uuid4()), "revision": 0, "state": "created", "control": "paused",
                   "phase": "planning", "plan": [], "taskIndex": 0, "sessions": {}, "activeEpisode": None,
                   "episodesUsed": 0, "tokensUsed": 0, "reason": "", "createdAt": time.time()}
            if values.get("reviewPolicy") == "independent":
                run.update(reviewScope="task", reviewFailures={}, feedback=[], reviewCandidate=None)
            db.execute("INSERT INTO project_runs VALUES(?,0,?)", (run["id"], encoded(run)))
            self.save(db, run, "created")
            return run
        return self.command(operation, {"action": "create", **(request if request is not None else values)}, apply)

    def control(self, operation, run_id, expected_revision, action):
        if type(expected_revision) is not int or expected_revision < 1:
            raise ValueError("expectedRevision must be a positive integer")
        def apply(db):
            run = self._read(db, run_id)
            if run["revision"] != expected_revision:
                raise ValueError("stale project revision; read the run before retrying")
            if action not in {"start", "pause", "resume", "cancel"}:
                raise ValueError("unknown project control action")
            if run["state"] in {"cancelled", "ready_for_review", "delivered", "integrated"}:
                raise ValueError("project run is terminal")
            if action in {"start", "resume"}:
                if run["state"] == "needs_review":
                    raise ValueError("unknown execution outcome requires reconciliation; cannot automatically resume")
                if run["episodesUsed"] >= run["maxEpisodes"] or run["tokensUsed"] >= run["tokenBudget"]:
                    raise ValueError("project budget exhausted")
                run.update(state="running", control="active", reason="")
            elif action == "pause":
                run["control"] = "paused"
            else:
                run.update(state="cancelled", control="paused")
            self.save(db, run, action)
            return run
        return self.command(operation, {"action": action, "id": run_id, "expectedRevision": expected_revision}, apply)

    def begin_review(self, operation, request, snapshot, policy):
        def apply(db):
            run = self._read(db, request["id"])
            if run["revision"] != request["expectedRevision"]:
                raise ValueError("stale project revision; read the run before retrying")
            if not self.can_review(run):
                raise ValueError("Only a settled candidate can enter an explicit review; unknown execution must be reconciled")
            if policy["tokenBudget"] <= run["tokensUsed"] or policy["maxEpisodes"] <= run["episodesUsed"]:
                raise ValueError("project budget exhausted; provide explicit review budget")
            run.update(policy, reviewPolicy="independent", state="running", control="active", phase="verifying",
                       reviewScope="project", taskIndex=len(run["plan"]), reviewFailures={}, feedback=[],
                       verificationStatus="pending", reason="")
            run.pop("verifiedCandidate", None)
            self.put_candidate(db, run, snapshot)
            self.save(db, run, "explicit_review_started")
            return run
        return self.command(operation, request, apply)

    @staticmethod
    def can_review(run):
        return bool(not run["activeEpisode"] and run["plan"] and (
            run["state"] in {"ready_for_review", "needs_review"} or (
                run["state"] == "running" and run["control"] == "budget_limited" and run["phase"] == "verifying")))

    def events(self, run_id, after=0, limit=100):
        self.get(run_id)
        if not 1 <= limit <= 200 or after < 0:
            raise ValueError("invalid event page")
        with self.lock:
            return [{"sequence": r[0], "kind": r[1], "run": json.loads(r[2])} for r in self.db.execute(
                "SELECT sequence,kind,data FROM project_events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (run_id, after, limit))]
