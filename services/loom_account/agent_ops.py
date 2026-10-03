"""Agent operations extension for Loom Account Service.

Keeps device/run/tool telemetry and control-plane endpoints isolated from the
core authentication service. Only operational metadata is stored here; user
prompts, assistant text, tool arguments/results, files, and screens are never
accepted by these APIs.
"""
from __future__ import annotations

import hmac
import json
import os
from http import HTTPStatus
from typing import Any, Collection
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from . import server as base

AccountError = base.AccountError
_now = base._now
_AGENT_SCHEMA = r"CREATE TABLE IF NOT EXISTS agent_devices (\n    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n    device_id TEXT NOT NULL,\n    name TEXT NOT NULL DEFAULT '',\n    platform TEXT NOT NULL DEFAULT '',\n    app_version TEXT NOT NULL DEFAULT '',\n    host_version TEXT NOT NULL DEFAULT '',\n    host_mode TEXT NOT NULL DEFAULT '',\n    host_protocol INTEGER NOT NULL DEFAULT 0,\n    bootstrap_protocol INTEGER NOT NULL DEFAULT 0,\n    connected_at INTEGER NOT NULL,\n    last_seen_at INTEGER NOT NULL,\n    disconnected_at INTEGER,\n    PRIMARY KEY(user_id, device_id)\n);\nCREATE INDEX IF NOT EXISTS idx_agent_devices_last_seen ON agent_devices(last_seen_at DESC);\n\nCREATE TABLE IF NOT EXISTS agent_threads (\n    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n    thread_id TEXT NOT NULL,\n    model TEXT NOT NULL DEFAULT '',\n    provider TEXT NOT NULL DEFAULT '',\n    updated_at INTEGER NOT NULL,\n    PRIMARY KEY(user_id, thread_id)\n);\n\nCREATE TABLE IF NOT EXISTS agent_thread_usage (\n    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n    thread_id TEXT NOT NULL,\n    input_tokens INTEGER NOT NULL DEFAULT 0,\n    output_tokens INTEGER NOT NULL DEFAULT 0,\n    total_tokens INTEGER NOT NULL DEFAULT 0,\n    updated_at INTEGER NOT NULL,\n    PRIMARY KEY(user_id, thread_id)\n);\n\nCREATE TABLE IF NOT EXISTS agent_runs (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n    device_id TEXT NOT NULL DEFAULT '',\n    thread_id TEXT NOT NULL,\n    turn_id TEXT NOT NULL,\n    model TEXT NOT NULL DEFAULT '',\n    provider TEXT NOT NULL DEFAULT '',\n    status TEXT NOT NULL DEFAULT 'running',\n    started_at INTEGER NOT NULL,\n    updated_at INTEGER NOT NULL,\n    completed_at INTEGER,\n    tool_count INTEGER NOT NULL DEFAULT 0,\n    approval_count INTEGER NOT NULL DEFAULT 0,\n    input_tokens INTEGER NOT NULL DEFAULT 0,\n    output_tokens INTEGER NOT NULL DEFAULT 0,\n    total_tokens INTEGER NOT NULL DEFAULT 0,\n    error_present INTEGER NOT NULL DEFAULT 0,\n    UNIQUE(user_id, turn_id)\n);\nCREATE INDEX IF NOT EXISTS idx_agent_runs_updated ON agent_runs(updated_at DESC);\nCREATE INDEX IF NOT EXISTS idx_agent_runs_user ON agent_runs(user_id, updated_at DESC);\n\nCREATE TABLE IF NOT EXISTS agent_tool_events (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n    run_id INTEGER REFERENCES agent_runs(id) ON DELETE CASCADE,\n    event_key TEXT NOT NULL,\n    tool_name TEXT NOT NULL DEFAULT '',\n    kind TEXT NOT NULL,\n    created_at INTEGER NOT NULL,\n    UNIQUE(user_id, event_key)\n);\nCREATE INDEX IF NOT EXISTS idx_agent_tool_events_created ON agent_tool_events(created_at DESC);\n\nCREATE TABLE IF NOT EXISTS agent_commands (\n    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,\n    device_id TEXT NOT NULL DEFAULT '',\n    kind TEXT NOT NULL,\n    payload_json TEXT NOT NULL DEFAULT '{}',\n    status TEXT NOT NULL DEFAULT 'pending',\n    requested_by INTEGER REFERENCES users(id) ON DELETE SET NULL,\n    created_at INTEGER NOT NULL,\n    delivered_at INTEGER,\n    completed_at INTEGER,\n    result_json TEXT NOT NULL DEFAULT '{}'\n);\nCREATE INDEX IF NOT EXISTS idx_agent_commands_pending ON agent_commands(user_id, status, created_at);".replace("\\n", "\n")


class AgentOpsStore(base.AccountStore):
    def _initialize(self) -> None:
        super()._initialize()
        with self._guard, self._connect() as db:
            db.executescript(_AGENT_SCHEMA)

    def admin_system(self) -> dict[str, Any]:
        result = super().admin_system()
        url = str(os.getenv("LOOM_MODEL_GATEWAY_HEALTH_URL") or "http://loom-model-gateway:8790/healthz").strip()
        status = "unavailable"
        if url:
            try:
                with urlopen(url, timeout=2) as response:
                    if int(response.status) == 200:
                        status = "healthy"
            except (HTTPError, URLError, OSError, TimeoutError):
                pass
        result.update({
            "model_gateway": {"status": status},
            "telemetry": {"status": "configured" if os.getenv("LOOM_TELEMETRY_SECRET", "").strip() else "not_configured"},
            "search": {"status": "configured" if os.getenv("TAVILY_API_KEY", "").strip() else "not_configured"},
        })
        return result

    @staticmethod
    def _telemetry_text(value: Any, limit: int = 160) -> str:
        return str(value or "").strip()[:limit]

    @staticmethod
    def _telemetry_time(value: Any, fallback: int | None = None) -> int:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return int(fallback if fallback is not None else _now())
        if number > 10_000_000_000:
            number /= 1000.0
        if number <= 0:
            return int(fallback if fallback is not None else _now())
        return int(number)

    def telemetry_device(self, body: dict[str, Any]) -> dict[str, Any]:
        user_id = int(body.get("user_id") or 0)
        device_id = self._telemetry_text(body.get("device_id"), 128)
        if user_id <= 0 or not device_id:
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_TELEMETRY_DEVICE", "user_id and device_id are required.")
        event = self._telemetry_text(body.get("event"), 32).casefold() or "heartbeat"
        if event not in {"connected", "heartbeat", "disconnected"}:
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_TELEMETRY_EVENT", "Unsupported device telemetry event.")
        now = self._telemetry_time(body.get("at"))
        with self._guard, self._connect() as db:
            if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                raise AccountError(HTTPStatus.NOT_FOUND, "USER_NOT_FOUND", "User not found.")
            existing = db.execute("SELECT connected_at, last_seen_at FROM agent_devices WHERE user_id = ? AND device_id = ?", (user_id, device_id)).fetchone()
            connected_at = now if event == "connected" or existing is None else int(existing["connected_at"])
            last_seen = int(existing["last_seen_at"]) if existing is not None else now
            if event != "disconnected":
                last_seen = now
            disconnected_at = now if event == "disconnected" else None
            db.execute(
                """INSERT INTO agent_devices(user_id, device_id, name, platform, app_version, host_version, host_mode, host_protocol, bootstrap_protocol, connected_at, last_seen_at, disconnected_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, device_id) DO UPDATE SET name=excluded.name, platform=excluded.platform,
                  app_version=excluded.app_version, host_version=excluded.host_version, host_mode=excluded.host_mode,
                  host_protocol=excluded.host_protocol, bootstrap_protocol=excluded.bootstrap_protocol,
                  connected_at=excluded.connected_at, last_seen_at=excluded.last_seen_at, disconnected_at=excluded.disconnected_at""",
                (user_id, device_id, self._telemetry_text(body.get("name"), 160), self._telemetry_text(body.get("platform"), 64),
                 self._telemetry_text(body.get("app_version"), 64), self._telemetry_text(body.get("host_version"), 64),
                 self._telemetry_text(body.get("host_mode"), 64), max(0, int(body.get("host_protocol") or 0)),
                 max(0, int(body.get("bootstrap_protocol") or 0)), connected_at, last_seen, disconnected_at),
            )
        return {"ok": True}

    def telemetry_agent_event(self, body: dict[str, Any]) -> dict[str, Any]:
        user_id = int(body.get("user_id") or 0)
        event = self._telemetry_text(body.get("event"), 48).casefold()
        if user_id <= 0 or event not in {"thread.started", "turn.started", "tool.completed", "approval.requested", "turn.completed"}:
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_TELEMETRY_EVENT", "Unsupported Agent telemetry event.")
        now = self._telemetry_time(body.get("at"))
        thread_id = self._telemetry_text(body.get("thread_id"), 160)
        turn_id = self._telemetry_text(body.get("turn_id"), 160)
        device_id = self._telemetry_text(body.get("device_id"), 128)
        with self._guard, self._connect() as db:
            if db.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone() is None:
                raise AccountError(HTTPStatus.NOT_FOUND, "USER_NOT_FOUND", "User not found.")
            if event == "thread.started":
                if not thread_id:
                    raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_THREAD_ID", "thread_id is required.")
                db.execute("""INSERT INTO agent_threads(user_id, thread_id, model, provider, updated_at) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, thread_id) DO UPDATE SET model=excluded.model, provider=excluded.provider, updated_at=excluded.updated_at""",
                    (user_id, thread_id, self._telemetry_text(body.get("model"), 160), self._telemetry_text(body.get("provider"), 120), now))
                return {"ok": True}
            if not thread_id or not turn_id:
                raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_RUN_ID", "thread_id and turn_id are required.")
            meta = db.execute("SELECT model, provider FROM agent_threads WHERE user_id=? AND thread_id=?", (user_id, thread_id)).fetchone()
            model = self._telemetry_text(body.get("model"), 160) or (str(meta["model"]) if meta else "")
            provider = self._telemetry_text(body.get("provider"), 120) or (str(meta["provider"]) if meta else "")
            run = db.execute("SELECT * FROM agent_runs WHERE user_id=? AND turn_id=?", (user_id, turn_id)).fetchone()
            if event == "turn.started":
                if run is None:
                    db.execute("""INSERT INTO agent_runs(user_id, device_id, thread_id, turn_id, model, provider, status, started_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)""", (user_id, device_id, thread_id, turn_id, model, provider, now, now))
                else:
                    db.execute("UPDATE agent_runs SET device_id=?, thread_id=?, model=CASE WHEN ?<>'' THEN ? ELSE model END, provider=CASE WHEN ?<>'' THEN ? ELSE provider END, status='running', updated_at=? WHERE id=?",
                        (device_id, thread_id, model, model, provider, provider, now, int(run["id"])))
                return {"ok": True}
            if run is None:
                cursor = db.execute("""INSERT INTO agent_runs(user_id, device_id, thread_id, turn_id, model, provider, status, started_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)""", (user_id, device_id, thread_id, turn_id, model, provider, now, now))
                run_id = int(cursor.lastrowid)
            else:
                run_id = int(run["id"])
            if event in {"tool.completed", "approval.requested"}:
                call_id = self._telemetry_text(body.get("call_id"), 160)
                event_key = self._telemetry_text(body.get("event_key"), 220) or f"{event}:{turn_id}:{call_id or now}"
                inserted = db.execute("INSERT OR IGNORE INTO agent_tool_events(user_id, run_id, event_key, tool_name, kind, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, run_id, event_key, self._telemetry_text(body.get("tool_name"), 160), "approval" if event == "approval.requested" else "tool", now))
                if inserted.rowcount:
                    if event == "approval.requested":
                        db.execute("UPDATE agent_runs SET approval_count=approval_count+1, status='waiting_approval', updated_at=? WHERE id=?", (now, run_id))
                    else:
                        db.execute("UPDATE agent_runs SET tool_count=tool_count+1, status=CASE WHEN status='waiting_approval' THEN 'running' ELSE status END, updated_at=? WHERE id=?", (now, run_id))
                return {"ok": True}
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            ci = max(0, int(usage.get("inputTokens") or usage.get("input_tokens") or 0))
            co = max(0, int(usage.get("outputTokens") or usage.get("output_tokens") or 0))
            ct = max(0, int(usage.get("totalTokens") or usage.get("total_tokens") or ci + co))
            previous = db.execute("SELECT * FROM agent_thread_usage WHERE user_id=? AND thread_id=?", (user_id, thread_id)).fetchone()
            def delta(current: int, key: str) -> int:
                if previous is None:
                    return current
                before = max(0, int(previous[key] or 0))
                return current - before if current >= before else current
            di, do, dt = delta(ci, "input_tokens"), delta(co, "output_tokens"), delta(ct, "total_tokens")
            db.execute("""INSERT INTO agent_thread_usage(user_id, thread_id, input_tokens, output_tokens, total_tokens, updated_at) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, thread_id) DO UPDATE SET input_tokens=excluded.input_tokens, output_tokens=excluded.output_tokens, total_tokens=excluded.total_tokens, updated_at=excluded.updated_at""",
                (user_id, thread_id, ci, co, ct, now))
            status = self._telemetry_text(body.get("status"), 32).casefold()
            if status not in {"completed", "failed", "interrupted", "cancelled"}:
                status = "failed" if bool(body.get("error_present")) else "completed"
            db.execute("""UPDATE agent_runs SET device_id=?, model=CASE WHEN ?<>'' THEN ? ELSE model END, provider=CASE WHEN ?<>'' THEN ? ELSE provider END,
                status=?, updated_at=?, completed_at=?, input_tokens=?, output_tokens=?, total_tokens=?, error_present=? WHERE id=?""",
                (device_id, model, model, provider, provider, status, now, now, di, do, dt, 1 if body.get("error_present") else 0, run_id))
        return {"ok": True}

    def telemetry_poll_commands(self, body: dict[str, Any]) -> dict[str, Any]:
        user_id = int(body.get("user_id") or 0)
        device_id = self._telemetry_text(body.get("device_id"), 128)
        if user_id <= 0 or not device_id:
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_COMMAND_TARGET", "user_id and device_id are required.")
        now = _now()
        with self._guard, self._connect() as db:
            rows = db.execute("""SELECT * FROM agent_commands WHERE user_id=? AND (device_id='' OR device_id=?)
                AND (status='pending' OR (status='delivered' AND COALESCE(delivered_at,0)<?)) ORDER BY id LIMIT 8""", (user_id, device_id, now - 30)).fetchall()
            ids = [int(row["id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                db.execute(f"UPDATE agent_commands SET status='delivered', delivered_at=? WHERE id IN ({placeholders}) AND status IN ('pending','delivered')", (now, *ids))
        commands = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"] or "{}"))
            except json.JSONDecodeError:
                payload = {}
            commands.append({"id": int(row["id"]), "kind": str(row["kind"]), "payload": payload})
        return {"commands": commands}

    def telemetry_complete_command(self, body: dict[str, Any]) -> dict[str, Any]:
        command_id = int(body.get("command_id") or 0)
        if command_id <= 0:
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_COMMAND_ID", "command_id is required.")
        ok = bool(body.get("ok"))
        result = {"ok": ok}
        error = self._telemetry_text(body.get("error"), 240)
        if error:
            result["error"] = error
        with self._guard, self._connect() as db:
            changed = db.execute("UPDATE agent_commands SET status=?, completed_at=?, result_json=? WHERE id=? AND status IN ('pending','delivered')",
                ("completed" if ok else "failed", _now(), json.dumps(result, separators=(",", ":")), command_id))
        return {"ok": True, "changed": bool(changed.rowcount)}

    def admin_agent_overview(self) -> dict[str, Any]:
        now = _now(); day = now - 86400; active_cutoff = now - 7200; online_cutoff = now - 90
        with self._connect() as db:
            return {
                "online_devices": int(db.execute("SELECT COUNT(*) FROM agent_devices WHERE last_seen_at>=? AND (disconnected_at IS NULL OR disconnected_at<last_seen_at)", (online_cutoff,)).fetchone()[0]),
                "known_devices": int(db.execute("SELECT COUNT(*) FROM agent_devices").fetchone()[0]),
                "active_runs": int(db.execute("SELECT COUNT(*) FROM agent_runs WHERE status IN ('running','waiting_approval') AND updated_at>=?", (active_cutoff,)).fetchone()[0]),
                "waiting_approvals": int(db.execute("SELECT COUNT(*) FROM agent_runs WHERE status='waiting_approval' AND updated_at>=?", (active_cutoff,)).fetchone()[0]),
                "runs_24h": int(db.execute("SELECT COUNT(*) FROM agent_runs WHERE started_at>=?", (day,)).fetchone()[0]),
                "failed_runs_24h": int(db.execute("SELECT COUNT(*) FROM agent_runs WHERE completed_at>=? AND status IN ('failed','interrupted','cancelled')", (day,)).fetchone()[0]),
                "tokens_24h": int(db.execute("SELECT COALESCE(SUM(total_tokens),0) FROM agent_runs WHERE completed_at>=?", (day,)).fetchone()[0]),
                "tool_calls_24h": int(db.execute("SELECT COUNT(*) FROM agent_tool_events WHERE kind='tool' AND created_at>=?", (day,)).fetchone()[0]),
                "pending_commands": int(db.execute("SELECT COUNT(*) FROM agent_commands WHERE status IN ('pending','delivered')").fetchone()[0]),
                "generated_at": now,
            }

    def admin_devices(self, limit: int = 250) -> list[dict[str, Any]]:
        now = _now(); cutoff = now - 90
        with self._connect() as db:
            rows = db.execute("""SELECT d.*, u.email, u.status AS user_status,
                CASE WHEN d.last_seen_at>=? AND (d.disconnected_at IS NULL OR d.disconnected_at<d.last_seen_at) THEN 1 ELSE 0 END AS online
                FROM agent_devices d JOIN users u ON u.id=d.user_id ORDER BY d.last_seen_at DESC LIMIT ?""", (cutoff, max(1, min(int(limit), 1000)))).fetchall()
        return [{**dict(row), "online": bool(row["online"])} for row in rows]

    def admin_runs(self, limit: int = 300) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT r.*, u.email FROM agent_runs r JOIN users u ON u.id=r.user_id ORDER BY r.updated_at DESC, r.id DESC LIMIT ?", (max(1, min(int(limit), 1000)),)).fetchall()
        return [{**dict(row), "error_present": bool(row["error_present"])} for row in rows]

    def admin_usage(self) -> dict[str, Any]:
        now = _now(); ranges = {"24h": 86400, "7d": 7*86400, "30d": 30*86400}
        with self._connect() as db:
            summaries = {}
            for key, seconds in ranges.items():
                row = db.execute("""SELECT COUNT(*) AS runs, COALESCE(SUM(input_tokens),0) AS input_tokens,
                    COALESCE(SUM(output_tokens),0) AS output_tokens, COALESCE(SUM(total_tokens),0) AS total_tokens,
                    COALESCE(AVG(CASE WHEN completed_at IS NOT NULL THEN completed_at-started_at END),0) AS avg_duration
                    FROM agent_runs WHERE completed_at>=?""", (now-seconds,)).fetchone()
                summaries[key] = {k: int(row[k] or 0) for k in ("runs", "input_tokens", "output_tokens", "total_tokens", "avg_duration")}
            models = db.execute("SELECT provider, model, COUNT(*) AS runs, COALESCE(SUM(total_tokens),0) AS total_tokens, COALESCE(SUM(input_tokens),0) AS input_tokens, COALESCE(SUM(output_tokens),0) AS output_tokens FROM agent_runs WHERE completed_at>=? GROUP BY provider, model ORDER BY total_tokens DESC, runs DESC LIMIT 50", (now-30*86400,)).fetchall()
            users = db.execute("SELECT u.email, r.user_id, COUNT(*) AS runs, COALESCE(SUM(r.total_tokens),0) AS total_tokens FROM agent_runs r JOIN users u ON u.id=r.user_id WHERE r.completed_at>=? GROUP BY r.user_id ORDER BY total_tokens DESC, runs DESC LIMIT 50", (now-30*86400,)).fetchall()
            daily = db.execute("SELECT date(completed_at, 'unixepoch') AS day, COUNT(*) AS runs, COALESCE(SUM(total_tokens),0) AS total_tokens FROM agent_runs WHERE completed_at>=? GROUP BY day ORDER BY day", (now-30*86400,)).fetchall()
        return {"ranges": summaries, "models": [dict(r) for r in models], "users": [dict(r) for r in users], "daily": [dict(r) for r in daily], "generated_at": now}

    def admin_tools(self) -> dict[str, Any]:
        cutoff = _now() - 30*86400
        with self._connect() as db:
            tools = db.execute("SELECT tool_name, COUNT(*) AS calls, MAX(created_at) AS last_used_at FROM agent_tool_events WHERE kind='tool' AND created_at>=? AND tool_name<>'' GROUP BY tool_name ORDER BY calls DESC, last_used_at DESC LIMIT 80", (cutoff,)).fetchall()
            totals = db.execute("SELECT COALESCE(SUM(tool_count),0) AS tool_calls, COALESCE(SUM(approval_count),0) AS approvals, SUM(CASE WHEN status='waiting_approval' THEN 1 ELSE 0 END) AS waiting FROM agent_runs WHERE updated_at>=?", (cutoff,)).fetchone()
        return {"tools": [dict(r) for r in tools], "tool_calls": int(totals["tool_calls"] or 0), "approvals": int(totals["approvals"] or 0), "waiting": int(totals["waiting"] or 0)}

    def admin_models(self) -> dict[str, Any]:
        cutoff = _now() - 30*86400
        with self._connect() as db:
            observed = db.execute("SELECT provider, model, COUNT(*) AS runs, COALESCE(SUM(total_tokens),0) AS total_tokens, MAX(updated_at) AS last_used_at FROM agent_runs WHERE updated_at>=? AND (model<>'' OR provider<>'') GROUP BY provider, model ORDER BY total_tokens DESC, runs DESC", (cutoff,)).fetchall()
            overrides = int(db.execute("SELECT COUNT(*) FROM model_entitlements").fetchone()[0])
        return {"managed_models": self._default_model_ids(), "entitlement_overrides": overrides, "observed": [dict(r) for r in observed]}

    def admin_user_operations(self, user_id: int, limit: int = 120) -> dict[str, Any]:
        uid = int(user_id)
        now = _now(); online_cutoff = now - 90; active_cutoff = now - 7200; month = now - 30*86400
        with self._connect() as db:
            user = db.execute("SELECT id, email FROM users WHERE id=?", (uid,)).fetchone()
            if user is None:
                raise AccountError(HTTPStatus.NOT_FOUND, "USER_NOT_FOUND", "User not found.")
            devices = db.execute("""SELECT d.*, CASE WHEN d.last_seen_at>=? AND (d.disconnected_at IS NULL OR d.disconnected_at<d.last_seen_at) THEN 1 ELSE 0 END AS online
                FROM agent_devices d WHERE d.user_id=? ORDER BY d.last_seen_at DESC LIMIT 100""", (online_cutoff, uid)).fetchall()
            runs = db.execute("SELECT * FROM agent_runs WHERE user_id=? ORDER BY updated_at DESC, id DESC LIMIT ?", (uid, max(1, min(int(limit), 500)))).fetchall()
            run_summary = db.execute("""SELECT COUNT(*) AS runs_30d, COALESCE(SUM(total_tokens),0) AS tokens_30d,
                COALESCE(SUM(tool_count),0) AS tool_calls_30d, COALESCE(SUM(approval_count),0) AS approvals_30d
                FROM agent_runs WHERE user_id=? AND updated_at>=?""", (uid, month)).fetchone()
            active_runs = int(db.execute("SELECT COUNT(*) FROM agent_runs WHERE user_id=? AND status IN ('running','waiting_approval') AND updated_at>=?", (uid, active_cutoff)).fetchone()[0])
            models = db.execute("""SELECT provider, model, COUNT(*) AS runs, COALESCE(SUM(total_tokens),0) AS total_tokens
                FROM agent_runs WHERE user_id=? AND completed_at>=? GROUP BY provider, model ORDER BY total_tokens DESC, runs DESC LIMIT 30""", (uid, month)).fetchall()
        device_items=[{**dict(row), "online": bool(row["online"])} for row in devices]
        run_items=[{**dict(row), "error_present": bool(row["error_present"])} for row in runs]
        return {
            "user_id": uid, "email": str(user["email"]), "generated_at": now,
            "summary": {
                "known_devices": len(device_items), "online_devices": sum(1 for item in device_items if item["online"]),
                "active_runs": active_runs, "runs_30d": int(run_summary["runs_30d"] or 0),
                "tokens_30d": int(run_summary["tokens_30d"] or 0), "tool_calls_30d": int(run_summary["tool_calls_30d"] or 0),
                "approvals_30d": int(run_summary["approvals_30d"] or 0),
            },
            "devices": device_items, "runs": run_items, "models": [dict(row) for row in models],
        }

    def admin_queue_interrupt(self, actor: dict[str, Any], run_id: int) -> dict[str, Any]:
        with self._guard, self._connect() as db:
            run = db.execute("SELECT * FROM agent_runs WHERE id=?", (int(run_id),)).fetchone()
            if run is None:
                raise AccountError(HTTPStatus.NOT_FOUND, "RUN_NOT_FOUND", "Agent run not found.")
            if str(run["status"]) not in {"running", "waiting_approval"}:
                raise AccountError(HTTPStatus.CONFLICT, "RUN_NOT_ACTIVE", "This Agent run is no longer active.")
            payload = {"runId": int(run_id), "threadId": str(run["thread_id"]), "turnId": str(run["turn_id"])}
            now = _now()
            cursor = db.execute("INSERT INTO agent_commands(user_id, device_id, kind, payload_json, status, requested_by, created_at) VALUES (?, ?, 'turn.interrupt', ?, 'pending', ?, ?)",
                (int(run["user_id"]), str(run["device_id"] or ""), json.dumps(payload, separators=(",", ":")), int(actor["id"]), now))
            command_id = int(cursor.lastrowid)
            self._audit(db, int(actor["id"]), "agent.run.interrupt", target_type="agent_run", target_id=str(run_id), metadata={"command_id": command_id, "user_id": int(run["user_id"])})
        return {"id": command_id, "status": "pending", "kind": "turn.interrupt"}


class AgentOpsApplication(base.AccountApplication):
    def admin_agent_overview(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return self.store.admin_agent_overview()
    def admin_devices(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return {"devices": self.store.admin_devices()}
    def admin_runs(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return {"runs": self.store.admin_runs()}
    def admin_usage(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return self.store.admin_usage()
    def admin_tools(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return self.store.admin_tools()
    def admin_models(self, authorization: str) -> dict[str, Any]: self._admin(authorization); return self.store.admin_models()

    def admin_user_operations(self, user_id: int, authorization: str) -> dict[str, Any]: self._admin(authorization); return self.store.admin_user_operations(user_id)

    @staticmethod
    def _require_telemetry_secret(supplied: str) -> None:
        expected = os.getenv("LOOM_TELEMETRY_SECRET", "").strip()
        if not expected:
            raise AccountError(HTTPStatus.SERVICE_UNAVAILABLE, "TELEMETRY_NOT_CONFIGURED", "Agent telemetry is not configured.")
        if not hmac.compare_digest(expected, str(supplied or "")):
            raise AccountError(HTTPStatus.UNAUTHORIZED, "TELEMETRY_UNAUTHORIZED", "Agent telemetry authentication failed.")

    def telemetry_device(self, body: dict[str, Any], secret: str) -> dict[str, Any]: self._require_telemetry_secret(secret); return self.store.telemetry_device(body)
    def telemetry_agent_event(self, body: dict[str, Any], secret: str) -> dict[str, Any]: self._require_telemetry_secret(secret); return self.store.telemetry_agent_event(body)
    def telemetry_poll_commands(self, body: dict[str, Any], secret: str) -> dict[str, Any]: self._require_telemetry_secret(secret); return self.store.telemetry_poll_commands(body)
    def telemetry_complete_command(self, body: dict[str, Any], secret: str) -> dict[str, Any]: self._require_telemetry_secret(secret); return self.store.telemetry_complete_command(body)
    def admin_interrupt_run(self, body: dict[str, Any], authorization: str) -> dict[str, Any]:
        actor = self._admin(authorization)
        try: run_id = int(body.get("run_id") or 0)
        except (TypeError, ValueError): run_id = 0
        if run_id <= 0:
            raise AccountError(HTTPStatus.BAD_REQUEST, "INVALID_RUN_ID", "run_id is required.")
        return {"command": self.store.admin_queue_interrupt(actor, run_id)}


class AgentOpsRequestHandler(base.AccountRequestHandler):
    def _dispatch(self) -> dict[str, Any]:
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        authorization = self.headers.get("Authorization") or ""
        if self.command == "GET":
            routes = {"/v1/admin/agent-overview": self.application.admin_agent_overview, "/v1/admin/devices": self.application.admin_devices,
                "/v1/admin/runs": self.application.admin_runs, "/v1/admin/usage": self.application.admin_usage,
                "/v1/admin/tools": self.application.admin_tools, "/v1/admin/models": self.application.admin_models}
            handler = routes.get(path)
            if handler is not None:
                return handler(authorization)
            prefix = "/v1/admin/users/"
            suffix = "/agent-ops"
            if path.startswith(prefix) and path.endswith(suffix):
                raw_id = path[len(prefix):-len(suffix)].strip("/")
                if raw_id.isdigit():
                    return self.application.admin_user_operations(int(raw_id), authorization)
        if self.command == "POST" and path in {"/v1/telemetry/device", "/v1/telemetry/agent-event", "/v1/telemetry/commands/poll", "/v1/telemetry/commands/complete", "/v1/admin/runs/interrupt"}:
            body = self._json_body()
            if path == "/v1/admin/runs/interrupt":
                return self.application.admin_interrupt_run(body, authorization)
            secret = self.headers.get("X-Loom-Telemetry-Secret") or ""
            handlers = {"/v1/telemetry/device": self.application.telemetry_device, "/v1/telemetry/agent-event": self.application.telemetry_agent_event,
                "/v1/telemetry/commands/poll": self.application.telemetry_poll_commands, "/v1/telemetry/commands/complete": self.application.telemetry_complete_command}
            return handlers[path](body, secret)
        return super()._dispatch()


class AgentOpsServer(base.LoomAccountServer):
    def __init__(self, address: tuple[str, int], application: AgentOpsApplication, trusted_proxies: Collection[str] | None = None) -> None:
        super().__init__(address, application, trusted_proxies)
        self.RequestHandlerClass = AgentOpsRequestHandler


def main(argv: list[str] | None = None) -> int:
    base.AccountStore = AgentOpsStore
    base.AccountApplication = AgentOpsApplication
    base.LoomAccountServer = AgentOpsServer
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
