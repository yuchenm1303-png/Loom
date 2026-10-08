from __future__ import annotations

import json
import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .catalog import GROUPS, GROUP_BY_ID, MODEL_BY_ID, MODELS, group_for_selection, model_for_selection, selection_for_model

DEFAULT_MODEL_IDS = tuple(item.id for item in MODELS)


def _now() -> int:
    return int(time.time())


def _normalize_model_id(value: Any) -> str:
    model_id = str(value or "").strip()
    if not model_id or len(model_id) > 240:
        raise ValueError("invalid model_id")
    return model_id


@dataclass(frozen=True)
class Decision:
    model_id: str
    enabled: bool
    source: str
    groups: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "enabled": self.enabled,
            "source": self.source,
            "groups": list(self.groups),
        }


class PolicyStore:
    """SQLite-backed control plane for Loom built-in model access.

    Precedence is intentionally strict and inspectable:
      1. provider/model-group global OFF is a hard deny;
      2. individual model global OFF is a hard deny;
      3. account-wide model disable is a hard deny;
      4. explicit per-account model rule;
      5. legacy Ant Ling account override (compatibility only);
      6. active access-group rules, deny wins across multiple groups;
      7. global allow/default allow.

    Disabled access groups are ignored. Saved/custom API connections are outside
    this service; callers should only submit Loom built-in selections.
    """

    def __init__(self, db_path: Path, model_ids: Iterable[str] | None = None) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_ids = tuple(dict.fromkeys(_normalize_model_id(item) for item in (model_ids or DEFAULT_MODEL_IDS)))
        self._guard = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def _initialize(self) -> None:
        with self._guard, self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS global_model_rules (
                    model_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL,
                    updated_by INTEGER
                );

                CREATE TABLE IF NOT EXISTS provider_catalog (
                    model_id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    name TEXT NOT NULL,
                    available INTEGER NOT NULL DEFAULT 1,
                    source TEXT NOT NULL,
                    last_seen INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS provider_catalog_status (
                    group_id TEXT PRIMARY KEY,
                    last_success INTEGER,
                    last_attempt INTEGER,
                    error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS global_model_group_rules (
                    group_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL,
                    updated_by INTEGER
                );

                CREATE TABLE IF NOT EXISTS model_groups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    updated_by INTEGER
                );

                CREATE TABLE IF NOT EXISTS model_group_members (
                    group_id INTEGER NOT NULL REFERENCES model_groups(id) ON DELETE CASCADE,
                    user_id INTEGER NOT NULL,
                    created_at INTEGER NOT NULL,
                    created_by INTEGER,
                    PRIMARY KEY(group_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_model_group_members_user
                    ON model_group_members(user_id);

                CREATE TABLE IF NOT EXISTS model_group_rules (
                    group_id INTEGER NOT NULL REFERENCES model_groups(id) ON DELETE CASCADE,
                    model_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at INTEGER NOT NULL,
                    updated_by INTEGER,
                    PRIMARY KEY(group_id, model_id)
                );
                CREATE INDEX IF NOT EXISTS idx_model_group_rules_model
                    ON model_group_rules(model_id);

                CREATE TABLE IF NOT EXISTS user_model_rules (
                    user_id INTEGER NOT NULL,
                    model_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    updated_by INTEGER,
                    PRIMARY KEY(user_id, model_id)
                );
                CREATE INDEX IF NOT EXISTS idx_user_model_rules_user
                    ON user_model_rules(user_id);

                CREATE TABLE IF NOT EXISTS model_policy_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id INTEGER,
                    action TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_model_policy_audit_created
                    ON model_policy_audit(created_at DESC);
                """
            )
            now = _now()
            for model_id in self.model_ids:
                db.execute(
                    """INSERT OR IGNORE INTO global_model_rules(model_id, enabled, updated_at, updated_by)
                    VALUES (?, 1, ?, NULL)""",
                    (model_id, now),
                )
            for group in GROUPS:
                db.execute(
                    """INSERT OR IGNORE INTO global_model_group_rules(group_id, enabled, updated_at, updated_by)
                    VALUES (?, 1, ?, NULL)""",
                    (group.id, now),
                )
            # Migrate existing rules, preserving decisions from earlier builds.
            for row in db.execute("SELECT model_id FROM global_model_rules").fetchall():
                selection = str(row["model_id"])
                group_id = group_for_selection(selection)
                if group_id:
                    meta = MODEL_BY_ID.get(selection)
                    db.execute(
                        "INSERT OR IGNORE INTO provider_catalog VALUES (?, ?, ?, ?, 1, 'bundled', ?)",
                        (selection, group_id, model_for_selection(selection), meta.name if meta else model_for_selection(selection), now),
                    )
            # Upgrade the first policy prototype, which stored Ant Ling raw
            # model IDs instead of canonical Loom selections. Preserve switch
            # state and access-group intent if that DB ever existed.
            for meta in MODELS:
                legacy_id = str(meta.legacy_account_id or "").strip()
                if not legacy_id or legacy_id == meta.id:
                    continue
                old = db.execute(
                    "SELECT enabled, updated_at, updated_by FROM global_model_rules WHERE model_id=?",
                    (legacy_id,),
                ).fetchone()
                if old is not None:
                    db.execute(
                        "UPDATE global_model_rules SET enabled=?, updated_at=?, updated_by=? WHERE model_id=?",
                        (int(old["enabled"]), int(old["updated_at"]), old["updated_by"], meta.id),
                    )
                    db.execute("DELETE FROM global_model_rules WHERE model_id=?", (legacy_id,))
                old_group_rules = db.execute(
                    "SELECT group_id, enabled, updated_at, updated_by FROM model_group_rules WHERE model_id=?",
                    (legacy_id,),
                ).fetchall()
                for rule in old_group_rules:
                    db.execute(
                        """INSERT OR REPLACE INTO model_group_rules(group_id, model_id, enabled, updated_at, updated_by)
                        VALUES (?, ?, ?, ?, ?)""",
                        (int(rule["group_id"]), meta.id, int(rule["enabled"]), int(rule["updated_at"]), rule["updated_by"]),
                    )
                if old_group_rules:
                    db.execute("DELETE FROM model_group_rules WHERE model_id=?", (legacy_id,))

    def _audit(
        self,
        db: sqlite3.Connection,
        actor_user_id: int | None,
        action: str,
        target_type: str,
        target_id: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        db.execute(
            """INSERT INTO model_policy_audit(
                actor_user_id, action, target_type, target_id, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                actor_user_id,
                action,
                target_type,
                str(target_id),
                json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                _now(),
            ),
        )

    # ------------------------------------------------------------------
    # Global model + provider/model-group control
    # ------------------------------------------------------------------
    def global_rules(self) -> list[dict[str, Any]]:
        catalog = {item["model_id"]: item for item in self.catalog()["models"]}
        with self._connect() as db:
            rows = db.execute(
                "SELECT model_id, enabled, updated_at, updated_by FROM global_model_rules ORDER BY model_id COLLATE NOCASE"
            ).fetchall()
        result = []
        for row in rows:
            model_id = str(row["model_id"])
            meta = MODEL_BY_ID.get(model_id)
            entry = catalog.get(model_id)
            result.append(
                {
                    "model_id": model_id,
                    "name": entry["name"] if entry else (meta.name if meta else model_id),
                    "group_id": meta.group_id if meta else (group_for_selection(model_id) or "other"),
                    "group_name": meta.group_name if meta else (GROUP_BY_ID[group_for_selection(model_id)].name if group_for_selection(model_id) else "Other"),
                    "enabled": bool(row["enabled"]),
                    "available": entry["available"] if entry else not model_id.startswith(("builtin:", "managed:")),
                    "catalog_source": entry["source"] if entry else "legacy",
                    "updated_at": int(row["updated_at"]),
                    "updated_by": int(row["updated_by"]) if row["updated_by"] is not None else None,
                }
            )
        return result

    def register_catalog(self, model_ids: Iterable[str]) -> None:
        """Trusted migration helper, never exposed to desktop requests."""
        normalized = list(dict.fromkeys(_normalize_model_id(item) for item in model_ids))
        if len(normalized) > 2000 or any(group_for_selection(item) is None for item in normalized):
            raise ValueError("invalid built-in model catalog")
        with self._guard, self._connect() as db:
            db.executemany(
                "INSERT OR IGNORE INTO global_model_rules(model_id, enabled, updated_at, updated_by) VALUES (?, 1, ?, NULL)",
                [(item, _now()) for item in normalized],
            )
            db.executemany(
                "INSERT OR IGNORE INTO provider_catalog VALUES (?, ?, ?, ?, 1, 'imported', ?)",
                [(item, group_for_selection(item), model_for_selection(item), model_for_selection(item), _now()) for item in normalized],
            )

    def catalog(self) -> dict[str, Any]:
        with self._guard, self._connect() as db:
            models = [dict(row) for row in db.execute("SELECT * FROM provider_catalog ORDER BY group_id, model_id")]
            providers = [dict(row) for row in db.execute("SELECT * FROM provider_catalog_status ORDER BY group_id")]
        for row in models:
            row["available"] = bool(row["available"])
            row["group_name"] = GROUP_BY_ID[row["group_id"]].name
        content = {"models": models, "providers": providers}
        versioned = [{key: value for key, value in row.items() if key != "last_seen"} for row in models]
        content["revision"] = hashlib.sha256(json.dumps(versioned, sort_keys=True).encode()).hexdigest()
        return content

    def replace_provider_catalog(self, group_id: str, models: list[dict[str, str]], source: str = "provider") -> None:
        if group_id not in GROUP_BY_ID or not models or len(models) > 2000:
            raise ValueError("invalid or empty provider catalog")
        if any(group_for_selection(item["model_id"]) != group_id
            or selection_for_model(group_id, item["model"]) != item["model_id"] for item in models):
            raise ValueError("model does not belong to provider")
        now = _now()
        with self._guard, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE provider_catalog SET available=0 WHERE group_id=?", (group_id,))
            for item in models:
                db.execute(
                    """INSERT INTO provider_catalog VALUES (?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(model_id) DO UPDATE SET model=excluded.model, name=excluded.name,
                    available=1, source=excluded.source, last_seen=excluded.last_seen""",
                    (item["model_id"], group_id, item["model"], item["name"], source, now),
                )
                db.execute("INSERT OR IGNORE INTO global_model_rules VALUES (?, 1, ?, NULL)", (item["model_id"], now))
            db.execute(
                """INSERT INTO provider_catalog_status VALUES (?, ?, ?, '')
                ON CONFLICT(group_id) DO UPDATE SET last_success=excluded.last_success,
                last_attempt=excluded.last_attempt, error=''""", (group_id, now, now),
            )
            db.execute("COMMIT")

    def catalog_failure(self, group_id: str, message: str) -> None:
        with self._guard, self._connect() as db:
            db.execute(
                """INSERT INTO provider_catalog_status(group_id, last_attempt, error) VALUES (?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET last_attempt=excluded.last_attempt, error=excluded.error""",
                (group_id, _now(), message),
            )

    def global_model_groups(self) -> list[dict[str, Any]]:
        counts: dict[str, int] = {}
        for item in self.global_rules():
            counts[item["group_id"]] = counts.get(item["group_id"], 0) + 1
        with self._connect() as db:
            rows = {
                str(row["group_id"]): row
                for row in db.execute(
                    "SELECT group_id, enabled, updated_at, updated_by FROM global_model_group_rules"
                ).fetchall()
            }
        result = []
        for group in GROUPS:
            row = rows.get(group.id)
            result.append(
                {
                    "id": group.id,
                    "name": group.name,
                    "enabled": True if row is None else bool(row["enabled"]),
                    "updated_at": None if row is None else int(row["updated_at"]),
                    "updated_by": None if row is None or row["updated_by"] is None else int(row["updated_by"]),
                    "model_count": counts.get(group.id, 0),
                }
            )
        return result

    def set_global_rule(self, actor_user_id: int, model_id: str, enabled: bool) -> dict[str, Any]:
        model_id = _normalize_model_id(model_id)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        now = _now()
        with self._guard, self._connect() as db:
            db.execute(
                """INSERT INTO global_model_rules(model_id, enabled, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(model_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by""",
                (model_id, 1 if enabled else 0, now, actor_user_id),
            )
            self._audit(db, actor_user_id, "global_model.update", "model", model_id, {"enabled": enabled})
        meta = MODEL_BY_ID.get(model_id)
        return {
            "model_id": model_id,
            "name": meta.name if meta else model_id,
            "group_id": meta.group_id if meta else (group_for_selection(model_id) or "other"),
            "enabled": enabled,
            "updated_at": now,
            "updated_by": actor_user_id,
        }

    def set_global_bulk(
        self, actor_user_id: int, enabled: bool, model_ids: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        targets = list(model_ids or [row["model_id"] for row in self.global_rules()])
        return [self.set_global_rule(actor_user_id, item, enabled) for item in targets]

    def set_global_model_group(self, actor_user_id: int, group_id: str, enabled: bool) -> dict[str, Any]:
        group_id = str(group_id or "").strip()
        known = next((item for item in GROUPS if item.id == group_id), None)
        if known is None:
            raise KeyError("model group not found")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        now = _now()
        with self._guard, self._connect() as db:
            db.execute(
                """INSERT INTO global_model_group_rules(group_id, enabled, updated_at, updated_by)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by""",
                (group_id, 1 if enabled else 0, now, actor_user_id),
            )
            self._audit(db, actor_user_id, "global_model_group.update", "model_group", group_id, {"enabled": enabled})
        return {"id": group_id, "name": known.name, "enabled": enabled, "updated_at": now, "updated_by": actor_user_id}

    # ------------------------------------------------------------------
    # Access groups (sets of Loom accounts)
    # ------------------------------------------------------------------
    def groups(self) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT g.id, g.name, g.enabled, g.created_at, g.updated_at, g.updated_by,
                          COUNT(DISTINCT m.user_id) AS member_count
                   FROM model_groups g
                   LEFT JOIN model_group_members m ON m.group_id = g.id
                   GROUP BY g.id
                   ORDER BY g.name COLLATE NOCASE"""
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "enabled": bool(row["enabled"]),
                "member_count": int(row["member_count"] or 0),
                "created_at": int(row["created_at"]),
                "updated_at": int(row["updated_at"]),
                "updated_by": int(row["updated_by"]) if row["updated_by"] is not None else None,
            }
            for row in rows
        ]

    def create_group(self, actor_user_id: int, name: str, enabled: bool = True) -> dict[str, Any]:
        clean_name = str(name or "").strip()
        if not clean_name or len(clean_name) > 96:
            raise ValueError("group name is required and must be at most 96 characters")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        now = _now()
        try:
            with self._guard, self._connect() as db:
                cursor = db.execute(
                    """INSERT INTO model_groups(name, enabled, created_at, updated_at, updated_by)
                    VALUES (?, ?, ?, ?, ?)""",
                    (clean_name, 1 if enabled else 0, now, now, actor_user_id),
                )
                group_id = int(cursor.lastrowid)
                for rule in self.global_rules():
                    db.execute(
                        """INSERT INTO model_group_rules(group_id, model_id, enabled, updated_at, updated_by)
                        VALUES (?, ?, ?, ?, ?)""",
                        (group_id, str(rule["model_id"]), 1 if bool(rule["enabled"]) else 0, now, actor_user_id),
                    )
                self._audit(db, actor_user_id, "access_group.create", "access_group", group_id, {"name": clean_name})
        except sqlite3.IntegrityError as exc:
            raise ValueError("a group with this name already exists") from exc
        return self.group_detail(group_id)

    def update_group(
        self,
        actor_user_id: int,
        group_id: int,
        *,
        name: str | None = None,
        enabled: bool | None = None,
    ) -> dict[str, Any]:
        with self._guard, self._connect() as db:
            row = db.execute("SELECT * FROM model_groups WHERE id = ?", (int(group_id),)).fetchone()
            if row is None:
                raise KeyError("group not found")
            next_name = str(row["name"]) if name is None else str(name).strip()
            if not next_name or len(next_name) > 96:
                raise ValueError("group name is required and must be at most 96 characters")
            next_enabled = bool(row["enabled"]) if enabled is None else enabled
            if not isinstance(next_enabled, bool):
                raise ValueError("enabled must be a boolean")
            now = _now()
            try:
                db.execute(
                    "UPDATE model_groups SET name=?, enabled=?, updated_at=?, updated_by=? WHERE id=?",
                    (next_name, 1 if next_enabled else 0, now, actor_user_id, int(group_id)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("a group with this name already exists") from exc
            self._audit(db, actor_user_id, "access_group.update", "access_group", group_id, {"name": next_name, "enabled": next_enabled})
        return self.group_detail(group_id)

    def delete_group(self, actor_user_id: int, group_id: int) -> None:
        with self._guard, self._connect() as db:
            row = db.execute("SELECT name FROM model_groups WHERE id = ?", (int(group_id),)).fetchone()
            if row is None:
                raise KeyError("group not found")
            db.execute("DELETE FROM model_groups WHERE id = ?", (int(group_id),))
            self._audit(db, actor_user_id, "access_group.delete", "access_group", group_id, {"name": str(row["name"])})

    def group_detail(self, group_id: int) -> dict[str, Any]:
        with self._connect() as db:
            row = db.execute("SELECT * FROM model_groups WHERE id = ?", (int(group_id),)).fetchone()
            if row is None:
                raise KeyError("group not found")
            members = [
                int(item["user_id"])
                for item in db.execute(
                    "SELECT user_id FROM model_group_members WHERE group_id=? ORDER BY user_id", (int(group_id),)
                ).fetchall()
            ]
            rules = [
                {
                    "model_id": str(item["model_id"]),
                    "enabled": bool(item["enabled"]),
                    "updated_at": int(item["updated_at"]),
                    "updated_by": int(item["updated_by"]) if item["updated_by"] is not None else None,
                }
                for item in db.execute(
                    """SELECT model_id, enabled, updated_at, updated_by
                    FROM model_group_rules WHERE group_id=? ORDER BY model_id COLLATE NOCASE""",
                    (int(group_id),),
                ).fetchall()
            ]
        return {
            "id": int(row["id"]),
            "name": str(row["name"]),
            "enabled": bool(row["enabled"]),
            "members": members,
            "member_count": len(members),
            "models": rules,
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "updated_by": int(row["updated_by"]) if row["updated_by"] is not None else None,
        }

    def set_membership(self, actor_user_id: int, group_id: int, user_id: int, enabled: bool) -> dict[str, Any]:
        if int(user_id) <= 0:
            raise ValueError("user_id must be positive")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        now = _now()
        with self._guard, self._connect() as db:
            if db.execute("SELECT 1 FROM model_groups WHERE id=?", (int(group_id),)).fetchone() is None:
                raise KeyError("group not found")
            if enabled:
                db.execute(
                    """INSERT OR IGNORE INTO model_group_members(group_id,user_id,created_at,created_by)
                    VALUES (?,?,?,?)""",
                    (int(group_id), int(user_id), now, actor_user_id),
                )
            else:
                db.execute("DELETE FROM model_group_members WHERE group_id=? AND user_id=?", (int(group_id), int(user_id)))
            self._audit(db, actor_user_id, "access_group.member.update", "access_group", group_id, {"user_id": int(user_id), "enabled": enabled})
        return self.group_detail(group_id)

    def set_members_bulk(self, actor_user_id: int, group_id: int, user_ids: Iterable[int]) -> dict[str, Any]:
        normalized = sorted({int(item) for item in user_ids if int(item) > 0})
        now = _now()
        with self._guard, self._connect() as db:
            if db.execute("SELECT 1 FROM model_groups WHERE id=?", (int(group_id),)).fetchone() is None:
                raise KeyError("group not found")
            db.execute("DELETE FROM model_group_members WHERE group_id=?", (int(group_id),))
            db.executemany(
                "INSERT INTO model_group_members(group_id,user_id,created_at,created_by) VALUES (?,?,?,?)",
                [(int(group_id), user_id, now, actor_user_id) for user_id in normalized],
            )
            self._audit(db, actor_user_id, "access_group.members.replace", "access_group", group_id, {"user_ids": normalized})
        return self.group_detail(group_id)

    def set_group_rule(self, actor_user_id: int, group_id: int, model_id: str, enabled: bool) -> dict[str, Any]:
        model_id = _normalize_model_id(model_id)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        now = _now()
        with self._guard, self._connect() as db:
            if db.execute("SELECT 1 FROM model_groups WHERE id=?", (int(group_id),)).fetchone() is None:
                raise KeyError("group not found")
            db.execute(
                """INSERT INTO model_group_rules(group_id, model_id, enabled, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(group_id, model_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                (int(group_id), model_id, 1 if enabled else 0, now, actor_user_id),
            )
            self._audit(db, actor_user_id, "access_group.model.update", "access_group", group_id, {"model_id": model_id, "enabled": enabled})
        return self.group_detail(group_id)

    def set_group_rules_bulk(self, actor_user_id: int, group_id: int, enabled: bool, model_ids: Iterable[str] | None = None) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        targets = list(model_ids or [row["model_id"] for row in self.global_rules()])
        for model_id in targets:
            self.set_group_rule(actor_user_id, group_id, model_id, enabled)
        return self.group_detail(group_id)

    # ------------------------------------------------------------------
    # Per-account model overrides
    # ------------------------------------------------------------------
    def user_rules(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT model_id, enabled, updated_at, updated_by
                FROM user_model_rules WHERE user_id=? ORDER BY model_id COLLATE NOCASE""",
                (int(user_id),),
            ).fetchall()
        return [
            {
                "model_id": str(row["model_id"]),
                "enabled": bool(row["enabled"]),
                "updated_at": int(row["updated_at"]),
                "updated_by": int(row["updated_by"]) if row["updated_by"] is not None else None,
            }
            for row in rows
        ]

    def user_memberships(self, user_id: int) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute(
                """SELECT g.id, g.name, g.enabled,
                          CASE WHEN m.user_id IS NULL THEN 0 ELSE 1 END AS member
                   FROM model_groups g
                   LEFT JOIN model_group_members m
                     ON m.group_id = g.id AND m.user_id = ?
                   ORDER BY g.name COLLATE NOCASE""",
                (int(user_id),),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "name": str(row["name"]),
                "enabled": bool(row["enabled"]),
                "member": bool(row["member"]),
            }
            for row in rows
        ]

    def set_user_rule(self, actor_user_id: int, user_id: int, model_id: str, enabled: bool) -> dict[str, Any]:
        if int(user_id) <= 0:
            raise ValueError("user_id must be positive")
        model_id = _normalize_model_id(model_id)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        now = _now()
        with self._guard, self._connect() as db:
            db.execute(
                """INSERT INTO user_model_rules(user_id, model_id, enabled, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, model_id) DO UPDATE SET enabled=excluded.enabled, updated_at=excluded.updated_at, updated_by=excluded.updated_by""",
                (int(user_id), model_id, 1 if enabled else 0, now, actor_user_id),
            )
            self._audit(db, actor_user_id, "user_model.update", "user", user_id, {"model_id": model_id, "enabled": enabled})
        return {"user_id": int(user_id), "model_id": model_id, "enabled": enabled, "updated_at": now, "updated_by": actor_user_id}

    def set_user_rules_bulk(
        self,
        actor_user_id: int,
        user_id: int,
        enabled: bool,
        model_ids: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        if int(user_id) <= 0:
            raise ValueError("user_id must be positive")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        targets = list(model_ids or [row["model_id"] for row in self.global_rules()])
        normalized = list(dict.fromkeys(_normalize_model_id(item) for item in targets))
        if not normalized:
            return []
        now = _now()
        with self._guard, self._connect() as db:
            db.executemany(
                """INSERT INTO user_model_rules(user_id, model_id, enabled, updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, model_id) DO UPDATE SET
                    enabled=excluded.enabled,
                    updated_at=excluded.updated_at,
                    updated_by=excluded.updated_by""",
                [(int(user_id), model_id, 1 if enabled else 0, now, actor_user_id) for model_id in normalized],
            )
            self._audit(
                db, actor_user_id, "user_model.bulk_update", "user", user_id,
                {"enabled": enabled, "model_ids": normalized, "count": len(normalized)},
            )
        return [
            {"user_id": int(user_id), "model_id": model_id, "enabled": enabled, "updated_at": now, "updated_by": actor_user_id}
            for model_id in normalized
        ]

    def clear_user_rules(self, actor_user_id: int, user_id: int, model_ids: Iterable[str] | None = None) -> None:
        with self._guard, self._connect() as db:
            if model_ids is None:
                db.execute("DELETE FROM user_model_rules WHERE user_id=?", (int(user_id),))
                metadata: dict[str, Any] = {"all": True}
            else:
                normalized = [_normalize_model_id(item) for item in model_ids]
                db.executemany(
                    "DELETE FROM user_model_rules WHERE user_id=? AND model_id=?",
                    [(int(user_id), item) for item in normalized],
                )
                metadata = {"model_ids": normalized}
            self._audit(db, actor_user_id, "user_model.clear", "user", user_id, metadata)

    # ------------------------------------------------------------------
    # Effective policy resolution
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        with self._guard:
            return {
                "models": self.global_rules(),
                "model_groups": self.global_model_groups(),
                "groups": self.groups(),
                "catalog": self.catalog(),
            }

    def _active_group_rows(self, user_id: int) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute(
                """SELECT g.id, g.name
                FROM model_groups g
                JOIN model_group_members m ON m.group_id=g.id
                WHERE m.user_id=? AND g.enabled=1
                ORDER BY g.name COLLATE NOCASE""",
                (int(user_id),),
            ).fetchall()

    def effective_access(
        self,
        user_id: int,
        account_access: dict[str, Any],
        extra_model_ids: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        with self._guard:
            return self._effective_access(user_id, account_access, extra_model_ids)

    def _effective_access(self, user_id: int, account_access: dict[str, Any], extra_model_ids=None) -> dict[str, Any]:
        catalog = self.catalog()
        catalog_models = {item["model_id"]: item for item in catalog["models"]}
        global_rows = self.global_rules()
        global_enabled = {str(row["model_id"]): bool(row["enabled"]) for row in global_rows}
        provider_groups = {str(row["id"]): bool(row["enabled"]) for row in self.global_model_groups()}
        account_enabled = bool(account_access.get("enabled", True))
        legacy_override = str(account_access.get("source") or "") == "override"
        legacy_allowed = {str(item).strip().casefold() for item in account_access.get("models", []) if str(item).strip()}

        user_rule_rows = self.user_rules(int(user_id))
        user_rules = {str(row["model_id"]): bool(row["enabled"]) for row in user_rule_rows}

        groups = self._active_group_rows(int(user_id))
        group_ids = [int(row["id"]) for row in groups]
        group_names = {int(row["id"]): str(row["name"]) for row in groups}
        rules_by_model: dict[str, list[tuple[int, bool]]] = {}
        if group_ids:
            placeholders = ",".join("?" for _ in group_ids)
            with self._connect() as db:
                rows = db.execute(
                    f"SELECT group_id, model_id, enabled FROM model_group_rules WHERE group_id IN ({placeholders})",
                    group_ids,
                ).fetchall()
            for row in rows:
                rules_by_model.setdefault(str(row["model_id"]), []).append((int(row["group_id"]), bool(row["enabled"])))

        targets = list(dict.fromkeys([
            *global_enabled.keys(),
            *user_rules.keys(),
            *(str(item).strip() for item in (extra_model_ids or ()) if str(item).strip()),
        ]))
        decisions: list[Decision] = []
        for model_id in targets:
            provider_group_id = group_for_selection(model_id)
            if (provider_group_id or model_id.startswith(("builtin:", "managed:"))) and (model_id not in catalog_models or not catalog_models[model_id]["available"]):
                decisions.append(Decision(model_id, False, "catalog"))
                continue
            if provider_group_id and not provider_groups.get(provider_group_id, True):
                decisions.append(Decision(model_id, False, "global_group", (provider_group_id,)))
                continue
            if not global_enabled.get(model_id, True):
                decisions.append(Decision(model_id, False, "global"))
                continue
            if not account_enabled:
                decisions.append(Decision(model_id, False, "account"))
                continue
            if model_id in user_rules:
                decisions.append(Decision(model_id, user_rules[model_id], "user"))
                continue

            meta = MODEL_BY_ID.get(model_id)
            if legacy_override and provider_group_id == "ant-ling" and (meta is None or not meta.legacy_account_id):
                decisions.append(Decision(model_id, model_for_selection(model_id).casefold() in legacy_allowed, "user_legacy"))
                continue
            if legacy_override and meta is not None and meta.legacy_account_id:
                decisions.append(Decision(model_id, meta.legacy_account_id.casefold() in legacy_allowed, "user_legacy"))
                continue

            group_rules = rules_by_model.get(model_id, [])
            if group_rules:
                names = tuple(group_names[group_id] for group_id, _ in group_rules)
                decisions.append(Decision(model_id, not any(not enabled for _, enabled in group_rules), "group", names))
                continue
            decisions.append(Decision(model_id, True, "global"))

        allowed = [item.model_id for item in decisions if item.enabled]
        result = {
            "enabled": account_enabled and bool(allowed),
            "models": allowed,
            "source": "policy",
            "user_id": int(user_id),
            "groups": [{"id": int(row["id"]), "name": str(row["name"])} for row in groups],
            "model_groups": self.global_model_groups(),
            "user_rules": user_rule_rows,
            "decisions": [item.payload() for item in decisions],
            "catalog": catalog,
            "schema_version": 2,
        }
        versioned = {"enabled": result["enabled"], "decisions": result["decisions"], "catalog": catalog["revision"]}
        result["revision"] = hashlib.sha256(json.dumps(versioned, sort_keys=True).encode()).hexdigest()
        return result

    def user_detail(self, user_id: int, account_access: dict[str, Any]) -> dict[str, Any]:
        return {
            "user_id": int(user_id),
            "rules": self.user_rules(user_id),
            "memberships": self.user_memberships(user_id),
            "access": self.effective_access(user_id, account_access),
        }

    def audit(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(500, int(limit)))
        with self._connect() as db:
            rows = db.execute(
                """SELECT id,actor_user_id,action,target_type,target_id,metadata_json,created_at
                FROM model_policy_audit ORDER BY id DESC LIMIT ?""",
                (safe_limit,),
            ).fetchall()
        result = []
        for row in rows:
            try:
                metadata = json.loads(str(row["metadata_json"] or "{}"))
            except json.JSONDecodeError:
                metadata = {}
            result.append(
                {
                    "id": int(row["id"]),
                    "actor_user_id": int(row["actor_user_id"]) if row["actor_user_id"] is not None else None,
                    "action": str(row["action"]),
                    "target_type": str(row["target_type"]),
                    "target_id": str(row["target_id"]),
                    "metadata": metadata,
                    "created_at": int(row["created_at"]),
                }
            )
        return result
