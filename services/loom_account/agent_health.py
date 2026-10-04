"""Metadata-only Host health persistence for Loom Agent operations."""
from __future__ import annotations

from typing import Any

HEALTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_device_health (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    health_schema INTEGER NOT NULL DEFAULT 0,
    os_release TEXT NOT NULL DEFAULT '',
    os_version TEXT NOT NULL DEFAULT '',
    arch TEXT NOT NULL DEFAULT '',
    system_uptime_seconds INTEGER NOT NULL DEFAULT 0,
    cpu_percent REAL,
    memory_total_bytes INTEGER NOT NULL DEFAULT 0,
    memory_used_bytes INTEGER NOT NULL DEFAULT 0,
    memory_percent REAL,
    disk_total_bytes INTEGER NOT NULL DEFAULT 0,
    disk_free_bytes INTEGER NOT NULL DEFAULT 0,
    disk_percent REAL,
    host_rss_bytes INTEGER NOT NULL DEFAULT 0,
    host_heap_used_bytes INTEGER NOT NULL DEFAULT 0,
    host_cpu_percent REAL,
    relay_rtt_ms REAL,
    capability_browser INTEGER NOT NULL DEFAULT 0,
    capability_computer_use INTEGER NOT NULL DEFAULT 0,
    capability_terminal INTEGER NOT NULL DEFAULT 0,
    capability_files INTEGER NOT NULL DEFAULT 0,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(user_id, device_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_device_health_updated ON agent_device_health(updated_at DESC);
CREATE TABLE IF NOT EXISTS agent_device_health_samples (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id TEXT NOT NULL,
    bucket_at INTEGER NOT NULL,
    cpu_percent REAL,
    memory_percent REAL,
    disk_percent REAL,
    host_cpu_percent REAL,
    relay_rtt_ms REAL,
    PRIMARY KEY(user_id, device_id, bucket_at)
);
CREATE INDEX IF NOT EXISTS idx_agent_device_health_samples_time ON agent_device_health_samples(bucket_at DESC);
"""


def initialize(db: Any) -> None:
    db.executescript(HEALTH_SCHEMA)


def _text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _number(value: Any, maximum: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0:
        return None
    return min(maximum, number)


def _integer(value: Any, maximum: int = 1_000_000_000_000_000) -> int:
    number = _number(value, float(maximum))
    return int(number or 0)


def store(db: Any, user_id: int, device_id: str, value: Any, now: int) -> None:
    health = value if isinstance(value, dict) else {}
    if not health:
        return
    capabilities = health.get("capabilities") if isinstance(health.get("capabilities"), dict) else {}
    cpu = _number(health.get("cpu_percent"), 100)
    memory = _number(health.get("memory_percent"), 100)
    disk = _number(health.get("disk_percent"), 100)
    host_cpu = _number(health.get("host_cpu_percent"), 100)
    relay_rtt = _number(health.get("relay_rtt_ms"), 60_000)
    values = (
        user_id, device_id, _integer(health.get("schema"), 100), _text(health.get("os_release"), 120),
        _text(health.get("os_version"), 160), _text(health.get("arch"), 32),
        _integer(health.get("system_uptime_seconds"), 10_000_000_000), cpu,
        _integer(health.get("memory_total_bytes")), _integer(health.get("memory_used_bytes")), memory,
        _integer(health.get("disk_total_bytes")), _integer(health.get("disk_free_bytes")), disk,
        _integer(health.get("host_rss_bytes")), _integer(health.get("host_heap_used_bytes")), host_cpu, relay_rtt,
        1 if capabilities.get("browser") else 0, 1 if capabilities.get("computer_use") else 0,
        1 if capabilities.get("terminal") else 0, 1 if capabilities.get("files") else 0, now,
    )
    db.execute("""INSERT INTO agent_device_health(
        user_id,device_id,health_schema,os_release,os_version,arch,system_uptime_seconds,cpu_percent,memory_total_bytes,memory_used_bytes,memory_percent,
        disk_total_bytes,disk_free_bytes,disk_percent,host_rss_bytes,host_heap_used_bytes,host_cpu_percent,relay_rtt_ms,
        capability_browser,capability_computer_use,capability_terminal,capability_files,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id,device_id) DO UPDATE SET
        health_schema=excluded.health_schema,os_release=excluded.os_release,os_version=excluded.os_version,arch=excluded.arch,
        system_uptime_seconds=excluded.system_uptime_seconds,cpu_percent=COALESCE(excluded.cpu_percent,agent_device_health.cpu_percent),
        memory_total_bytes=excluded.memory_total_bytes,memory_used_bytes=excluded.memory_used_bytes,memory_percent=COALESCE(excluded.memory_percent,agent_device_health.memory_percent),
        disk_total_bytes=excluded.disk_total_bytes,disk_free_bytes=excluded.disk_free_bytes,disk_percent=COALESCE(excluded.disk_percent,agent_device_health.disk_percent),
        host_rss_bytes=excluded.host_rss_bytes,host_heap_used_bytes=excluded.host_heap_used_bytes,host_cpu_percent=COALESCE(excluded.host_cpu_percent,agent_device_health.host_cpu_percent),
        relay_rtt_ms=COALESCE(excluded.relay_rtt_ms,agent_device_health.relay_rtt_ms),capability_browser=excluded.capability_browser,
        capability_computer_use=excluded.capability_computer_use,capability_terminal=excluded.capability_terminal,capability_files=excluded.capability_files,
        updated_at=excluded.updated_at""", values)
    bucket = now - (now % 300)
    db.execute("""INSERT INTO agent_device_health_samples(user_id,device_id,bucket_at,cpu_percent,memory_percent,disk_percent,host_cpu_percent,relay_rtt_ms)
        VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(user_id,device_id,bucket_at) DO UPDATE SET
        cpu_percent=COALESCE(excluded.cpu_percent,agent_device_health_samples.cpu_percent),
        memory_percent=COALESCE(excluded.memory_percent,agent_device_health_samples.memory_percent),
        disk_percent=COALESCE(excluded.disk_percent,agent_device_health_samples.disk_percent),
        host_cpu_percent=COALESCE(excluded.host_cpu_percent,agent_device_health_samples.host_cpu_percent),
        relay_rtt_ms=COALESCE(excluded.relay_rtt_ms,agent_device_health_samples.relay_rtt_ms)""",
        (user_id, device_id, bucket, cpu, memory, disk, host_cpu, relay_rtt))
    db.execute("DELETE FROM agent_device_health_samples WHERE bucket_at < ?", (now - 30 * 86400,))


def latest_by_device(db: Any) -> dict[tuple[int, str], dict[str, Any]]:
    rows = db.execute("SELECT * FROM agent_device_health").fetchall()
    result: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        item["capabilities"] = {
            "browser": bool(item.pop("capability_browser", 0)),
            "computer_use": bool(item.pop("capability_computer_use", 0)),
            "terminal": bool(item.pop("capability_terminal", 0)),
            "files": bool(item.pop("capability_files", 0)),
        }
        result[(int(item["user_id"]), str(item["device_id"]))] = item
    return result


def hourly_history(db: Any, since: int) -> dict[tuple[int, str], list[dict[str, Any]]]:
    rows = db.execute("""SELECT user_id,device_id,MIN(23,MAX(0,CAST((bucket_at-?)/3600 AS INTEGER))) AS bucket,
        AVG(cpu_percent) AS cpu_percent,AVG(memory_percent) AS memory_percent,AVG(disk_percent) AS disk_percent,
        AVG(host_cpu_percent) AS host_cpu_percent,AVG(relay_rtt_ms) AS relay_rtt_ms
        FROM agent_device_health_samples WHERE bucket_at>=?
        GROUP BY user_id,device_id,bucket ORDER BY user_id,device_id,bucket""", (since, since)).fetchall()
    result: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (int(row["user_id"]), str(row["device_id"]))
        result.setdefault(key, []).append({
            "bucket": int(row["bucket"] or 0),
            "cpu_percent": None if row["cpu_percent"] is None else round(float(row["cpu_percent"]), 1),
            "memory_percent": None if row["memory_percent"] is None else round(float(row["memory_percent"]), 1),
            "disk_percent": None if row["disk_percent"] is None else round(float(row["disk_percent"]), 1),
            "host_cpu_percent": None if row["host_cpu_percent"] is None else round(float(row["host_cpu_percent"]), 1),
            "relay_rtt_ms": None if row["relay_rtt_ms"] is None else round(float(row["relay_rtt_ms"]), 1),
        })
    return result
