"""Request-boundary token admission and an exactly-once project usage ledger.

Every model call of a project session passes ``BudgetedModelExecutor``: generation,
retries the turn issues, in-turn compaction summaries. Before a request is sent the
ledger owner (the parallel team root, else the run) must still have room for it;
afterwards the outcome is recorded. Usage is keyed by the runtime event that reports
it, so a live listener, episode settlement and a restart can all offer the same
event and it is charged once.

What this is not: a provider billing cap. A request already in flight finishes and
is charged; it can cost more than the largest request observed so far, which is
what admission predicts with. A request that failed or was cancelled after it was
sent has unknown usage; its admission reserve is assumed spent, and it is counted
separately from known tokens.
"""
from __future__ import annotations

import time
import uuid

from app.agent_runtime.contracts import AgentEventKind as E
from app.ai.errors import AITransportError
from app.ai.execution_control import ModelAdmissionRefused
from app.agent_runtime.context_budget import estimate_tokens

USAGE_FIELDS = {E.MODEL_RESPONSE: "usage", E.MODEL_RESPONSE_REJECTED: "usage", E.TURN_STOP_CHECKED: "usage",
                E.CONTEXT_CHECKPOINTED: "summary_usage"}
ACTIVE_EPISODE = ("prepared", "running", "waiting_approval")

SCHEMA = """
    CREATE TABLE IF NOT EXISTS project_model_requests(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES project_runs(id),
        ledger_run_id TEXT NOT NULL, episode_id TEXT NOT NULL, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
        state TEXT NOT NULL, reserved INTEGER NOT NULL, estimate INTEGER NOT NULL, tokens INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS project_model_requests_ledger ON project_model_requests(ledger_run_id, state);
    CREATE INDEX IF NOT EXISTS project_model_requests_turn ON project_model_requests(turn_id, state);
    CREATE TABLE IF NOT EXISTS project_usage(key TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES project_runs(id),
        ledger_run_id TEXT NOT NULL, episode_id TEXT NOT NULL, turn_id TEXT NOT NULL, tokens INTEGER NOT NULL,
        applied INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS project_usage_pending ON project_usage(applied, run_id);
    INSERT OR IGNORE INTO project_runner_schema_versions VALUES(3);
"""


def event_tokens(event):
    field = USAGE_FIELDS.get(event.kind)
    if field is None:
        return None
    usage = event.data.get(field) or {}
    return max(0, int(usage.get("total_tokens") or 0)) if isinstance(usage, dict) else 0


def ledger_id(run):
    return run.get("teamOwnerId") or run["id"]


def _read(db, run_id):
    import json
    row = db.execute("SELECT data FROM project_runs WHERE id=?", (run_id,)).fetchone()
    return json.loads(row[0]) if row else None


def position(db, owner):
    """Owner budget position. ``committed`` is known usage, settled or not yet."""
    ledger = owner["id"]
    unapplied = db.execute("SELECT COALESCE(SUM(tokens),0) FROM project_usage WHERE ledger_run_id=? AND applied=0",
                           (ledger,)).fetchone()[0]
    rows = {state: (count, reserved, tokens) for state, count, reserved, tokens in db.execute(
        "SELECT state,COUNT(*),COALESCE(SUM(reserved),0),COALESCE(SUM(tokens),0) FROM project_model_requests "
        "WHERE ledger_run_id=? GROUP BY state", (ledger,))}
    largest = _largest(db, ledger)
    in_flight = rows.get("open", (0, 0, 0))[1] + rows.get("returned", (0, 0, 0))[2]
    assumed = rows.get("unknown", (0, 0, 0))[1]
    committed = owner["tokensUsed"] + unapplied
    return {"enforcement": "request_boundary", "hardBillingCap": False, "tokenBudget": owner["tokenBudget"],
            "knownTokens": committed, "inFlightTokens": in_flight, "assumedUnknownTokens": assumed,
            "unknownRequests": rows.get("unknown", (0, 0, 0))[0], "refusedRequests": rows.get("refused", (0, 0, 0))[0],
            "largestRequestTokens": largest, "chargedTokens": committed + in_flight + assumed}


def _largest(db, ledger):
    return max(db.execute("SELECT COALESCE(MAX(tokens),0) FROM project_usage WHERE ledger_run_id=?", (ledger,)).fetchone()[0],
               db.execute("SELECT COALESCE(MAX(tokens),0) FROM project_model_requests WHERE ledger_run_id=? AND state='returned'",
                          (ledger,)).fetchone()[0])


def _largest_known(db, ledger):
    # A new ledger (an integration child) has seen nothing yet; the runs before
    # it on this Host are the better guess than an input estimate.
    return _largest(db, ledger) or db.execute("SELECT COALESCE(MAX(tokens),0) FROM project_usage").fetchone()[0]


def _assume_unknown(db, where, params):
    # Usage nobody reported: assume the largest request observed (this ledger,
    # else this Host). Before any observation, the request's own input estimate (a provider that
    # never reports usage would otherwise never reach the budget).
    for ticket, ledger in db.execute(f"SELECT id,ledger_run_id FROM project_model_requests WHERE {where}", params).fetchall():
        largest = _largest_known(db, ledger)
        db.execute("UPDATE project_model_requests SET state='unknown',reserved=MAX(reserved,CASE WHEN ?>0 THEN ? ELSE estimate END) "
                   "WHERE id=?", (largest, largest, ticket))


class ProjectBudget:
    def __init__(self, store, project_sessions):
        # ``store`` may be a callable: the Host can reopen its ProjectStore.
        self._store, self.project_sessions = store, project_sessions

    @property
    def store(self):
        return self._store() if callable(self._store) else self._store

    def recover(self):
        # Only one scheduler owns this store (file lease); an open request from a
        # previous process was sent or not, and nobody can tell which.
        with self.store.transaction() as db:
            _assume_unknown(db, "state='open'", ())

    def admit(self, request):
        session_id = getattr(request, "session_id", "") or ""
        if session_id not in self.project_sessions:
            return None
        try:
            estimate = int(estimate_tokens(request.messages, request.tools) + (request.max_output_tokens or 0))
        except Exception:
            estimate = 0
        with self.store.transaction() as db:
            row = db.execute("SELECT id,run_id,turn_id FROM project_episodes WHERE session_id=? AND state IN (?,?,?) "
                             "ORDER BY rowid DESC LIMIT 1", (session_id, *ACTIVE_EPISODE)).fetchone()
            run = _read(db, row[1]) if row else None
            reason = ""
            if run is None:
                reason = "Project session has no admitted episode; its model requests are not allowed"
            elif run["state"] == "cancelled":
                reason = "Project run was cancelled"
            else:
                owner = _read(db, ledger_id(run)) or run
                if owner["state"] == "cancelled":
                    reason = "Project run was cancelled"
                else:
                    now = position(db, owner)
                    reserve = now["largestRequestTokens"]
                    if now["chargedTokens"] >= owner["tokenBudget"]:
                        reason = (f"Project token budget reached: {now['chargedTokens']} of {owner['tokenBudget']} "
                                  "charged before this request")
                    elif now["chargedTokens"] + now["largestRequestTokens"] > owner["tokenBudget"]:
                        reason = (f"Project token budget: {now['chargedTokens']} charged + up to {now['largestRequestTokens']} "
                                  f"for the next request would exceed {owner['tokenBudget']}")
            ticket = str(uuid.uuid4())
            if run is not None:
                db.execute("INSERT INTO project_model_requests VALUES(?,?,?,?,?,?,?,?,?,0,?)",
                           (ticket, run["id"], ledger_id(run), row[0], session_id, row[2],
                            "refused" if reason else "open", 0 if reason else reserve, estimate, time.time()))
        if reason:
            raise ModelAdmissionRefused(reason)
        return ticket

    def finish(self, ticket, state, tokens=0):
        with self.store.transaction() as db:
            if state == "unknown" or (state == "returned" and not tokens):
                _assume_unknown(db, "id=? AND state='open'", (ticket,))
            else:
                db.execute("UPDATE project_model_requests SET state=?,tokens=? WHERE id=? AND state='open'",
                           (state, max(0, int(tokens or 0)), ticket))

    def observe(self, event):
        """Runtime listener: record usage as it is reported, not when the episode ends."""
        tokens = event_tokens(event)
        if tokens is None or event.session_id not in self.project_sessions:
            return
        with self.store.transaction() as db:
            self._record(db, event.event_id, event.turn_id, tokens, rejected=event.kind is E.MODEL_RESPONSE_REJECTED)

    def _record(self, db, event_id, turn_id, tokens, *, rejected=False):
        episode = db.execute("SELECT id,run_id FROM project_episodes WHERE turn_id=?", (turn_id,)).fetchone()
        if episode is None:
            return 0
        run = _read(db, episode[1])
        inserted = db.execute("INSERT OR IGNORE INTO project_usage VALUES(?,?,?,?,?,?,0)",
                              (event_id, run["id"], ledger_id(run), episode[0], turn_id, tokens)).rowcount
        if inserted:
            # The event reports what the requests before it returned.
            db.execute("UPDATE project_model_requests SET state='accounted' WHERE turn_id=? AND state='returned'", (turn_id,))
            if rejected and tokens:
                db.execute("UPDATE project_model_requests SET state='accounted' WHERE id=(SELECT id FROM project_model_requests "
                           "WHERE turn_id=? AND state='unknown' ORDER BY created_at DESC LIMIT 1)", (turn_id,))
        return inserted

    def settle(self, db, run, episode, result, save):
        """Charge an episode inside the scheduler's settlement transaction; returns its tokens."""
        turn = episode["turnId"]
        reported = result.get("usage")
        if reported is None:  # executors without event detail report one total
            reported = [(f"episode:{episode['id']}", int(result.get("tokens") or 0))]
        newly = sum(self._record(db, key, turn, tokens) for key, tokens in reported)
        for ticket, tokens in db.execute("SELECT id,tokens FROM project_model_requests WHERE turn_id=? AND state='returned'",
                                         (turn,)).fetchall():
            if not newly:  # a returned response whose event was never written
                self._record(db, f"request:{ticket}", turn, tokens)
            db.execute("UPDATE project_model_requests SET state='accounted' WHERE id=?", (ticket,))
        _assume_unknown(db, "turn_id=? AND state='open'", (turn,))
        self.apply(db, run, save)
        return db.execute("SELECT COALESCE(SUM(tokens),0) FROM project_usage WHERE episode_id=?", (episode["id"],)).fetchone()[0]

    @staticmethod
    def apply(db, run, save, *, exclude_episode=""):
        """Move recorded usage into tokensUsed exactly once (the applied flag)."""
        tokens = db.execute("SELECT COALESCE(SUM(tokens),0) FROM project_usage WHERE run_id=? AND applied=0 AND episode_id!=?",
                            (run["id"], exclude_episode)).fetchone()[0]
        db.execute("UPDATE project_usage SET applied=1 WHERE run_id=? AND applied=0 AND episode_id!=?", (run["id"], exclude_episode))
        if not tokens:
            return 0
        run["tokensUsed"] += tokens
        if run.get("teamOwnerId"):
            parent = _read(db, run["teamOwnerId"])
            parent["tokensUsed"] += tokens
            save(db, parent, "parallel_usage_settled")
        return tokens

    def apply_late(self):
        """Charge usage that arrived after its episode settled (a late response)."""
        with self.store.lock:
            pending = [row[0] for row in self.store.db.execute("SELECT DISTINCT run_id FROM project_usage WHERE applied=0")]
        for run_id in pending:
            with self.store.transaction() as db:
                run = self.store._read(db, run_id)
                active = (run["activeEpisode"] or {}).get("id", "")
                if self.apply(db, run, self.store.save, exclude_episode=active):
                    self.store.save(db, run, "late_usage_settled")

    def refused(self, db, episode):
        return bool(db.execute("SELECT 1 FROM project_model_requests WHERE turn_id=? AND state='refused'",
                               (episode["turnId"],)).fetchone())

    def report(self, run):
        with self.store.lock:
            owner = self.store._read(self.store.db, ledger_id(run))
            return position(self.store.db, owner)


class BudgetedModelExecutor:
    """Wraps the runtime's model executor; non-project sessions pass straight through."""

    def __init__(self, inner, budget):
        self.inner, self.budget = inner, budget

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def execute(self, platform, profile_id, request, token, **kwargs):
        try:
            ticket = self.budget.admit(request)
        except ModelAdmissionRefused:
            raise
        except Exception as exc:  # fail closed: an unreadable ledger admits nothing
            raise ModelAdmissionRefused(f"Project budget ledger unavailable: {type(exc).__name__}") from exc
        if ticket is None:
            return self.inner.execute(platform, profile_id, request, token, **kwargs)
        try:
            response = self.inner.execute(platform, profile_id, request, token, **kwargs)
        except AITransportError as exc:
            # A 4xx other than a timeout is the provider refusing the request.
            refused = exc.status_code is not None and 400 <= exc.status_code < 500 and exc.status_code != 408
            self._finish(ticket, "rejected" if refused else "unknown")
            raise
        except BaseException as exc:
            # A tool-contract error carries the complete response, which the
            # runtime commits (and reports usage for) like any other.
            carried = getattr(getattr(exc, "response", None), "usage", None)
            tokens = getattr(carried, "total_tokens", 0) or 0
            self._finish(ticket, "returned" if tokens else "unknown", tokens)
            raise
        usage = getattr(response, "usage", None)
        self._finish(ticket, "returned", getattr(usage, "total_tokens", 0) or 0)
        return response

    def _finish(self, ticket, state, tokens=0):
        try:
            self.budget.finish(ticket, state, tokens)
        except Exception:
            pass  # the open row stays open and is treated as unknown on recovery
