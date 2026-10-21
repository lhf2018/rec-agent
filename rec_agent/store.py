from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from rec_agent.timeutil import to_utc, to_utc_iso, utc_now

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "rec_agent.db"


@dataclass
class ExposureItem:
    merchant_id: str
    event_type: str
    event_time: str


@dataclass
class DeliverySummary:
    last_success_at: str | None
    merchant_ids: list[str]
    categories: list[str]


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  task_id TEXT PRIMARY KEY,
                  tenant_id TEXT,
                  user_id TEXT,
                  idempotency_key TEXT UNIQUE,
                  status TEXT,
                  payload_json TEXT,
                  created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS steps (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_id TEXT,
                  step_index INTEGER,
                  event_json TEXT
                );
                CREATE TABLE IF NOT EXISTS feedback (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  task_id TEXT,
                  user_id TEXT,
                  merchant_id TEXT,
                  event_type TEXT,
                  event_time TEXT
                );
                CREATE TABLE IF NOT EXISTS not_interested (
                  user_id TEXT NOT NULL,
                  merchant_id TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL,
                  PRIMARY KEY (user_id, merchant_id)
                );
                CREATE TABLE IF NOT EXISTS deliveries (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id TEXT NOT NULL,
                  task_id TEXT,
                  delivered_at TEXT NOT NULL,
                  merchant_ids_json TEXT NOT NULL,
                  categories_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS scene_seen (
                  user_id TEXT NOT NULL,
                  scene_key TEXT NOT NULL,
                  seen_at TEXT NOT NULL,
                  PRIMARY KEY (user_id, scene_key)
                );
                CREATE TABLE IF NOT EXISTS exposures (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id TEXT NOT NULL,
                  merchant_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  event_time TEXT NOT NULL,
                  task_id TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_deliveries_user_time
                  ON deliveries(user_id, delivered_at);
                CREATE INDEX IF NOT EXISTS idx_exposures_user_time
                  ON exposures(user_id, event_time);
                """
            )

    def new_task_id(self) -> str:
        return f"task_{uuid.uuid4().hex[:10]}"

    def get_by_idempotency(self, key: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload_json FROM tasks WHERE idempotency_key = ?", (key,)
            ).fetchone()
            if not row:
                return None
            return json.loads(row["payload_json"])

    def save_task(
        self, task_id: str, tenant_id: str, user_id: str, key: str, status: str, payload: dict
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO tasks(task_id, tenant_id, user_id, idempotency_key, status, payload_json)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET status=excluded.status, payload_json=excluded.payload_json
                """,
                (task_id, tenant_id, user_id, key, status, json.dumps(payload, ensure_ascii=False)),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT payload_json FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if not row:
                return None
            return json.loads(row["payload_json"])

    def save_steps(self, task_id: str, steps: list[dict]) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM steps WHERE task_id = ?", (task_id,))
            for s in steps:
                conn.execute(
                    "INSERT INTO steps(task_id, step_index, event_json) VALUES(?,?,?)",
                    (task_id, s.get("step_index", 0), json.dumps(s, ensure_ascii=False)),
                )

    def get_steps(self, task_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT event_json FROM steps WHERE task_id = ? ORDER BY step_index", (task_id,)
            ).fetchall()
            return [json.loads(r["event_json"]) for r in rows]

    def add_feedback(
        self,
        task_id: str,
        user_id: str,
        merchant_id: str | None,
        event_type: str,
        event_time: str,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO feedback(task_id, user_id, merchant_id, event_type, event_time) VALUES(?,?,?,?,?)",
                (task_id, user_id, merchant_id, event_type, event_time),
            )

    # --- dynamic user state ---

    def upsert_not_interested(
        self, user_id: str, merchant_id: str, *, expires_at: str, created_at: str | None = None
    ) -> None:
        created = to_utc_iso(created_at)
        exp = to_utc_iso(expires_at)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO not_interested(user_id, merchant_id, created_at, expires_at)
                VALUES(?,?,?,?)
                ON CONFLICT(user_id, merchant_id) DO UPDATE SET
                  expires_at=excluded.expires_at,
                  created_at=excluded.created_at
                """,
                (user_id, merchant_id, created, exp),
            )

    def list_not_interested(self, user_id: str, *, now: datetime | None = None) -> list[str]:
        clock = now or utc_now()
        clock_iso = to_utc_iso(clock)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT merchant_id FROM not_interested
                WHERE user_id = ? AND expires_at > ?
                """,
                (user_id, clock_iso),
            ).fetchall()
            return [r["merchant_id"] for r in rows]

    def record_delivery(
        self,
        user_id: str,
        *,
        task_id: str,
        merchant_ids: list[str],
        categories: list[str],
        delivered_at: str | None = None,
    ) -> None:
        at = to_utc_iso(delivered_at)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO deliveries(user_id, task_id, delivered_at, merchant_ids_json, categories_json)
                VALUES(?,?,?,?,?)
                """,
                (
                    user_id,
                    task_id,
                    at,
                    json.dumps(merchant_ids, ensure_ascii=False),
                    json.dumps(categories, ensure_ascii=False),
                ),
            )
            for mid in merchant_ids:
                conn.execute(
                    """
                    INSERT INTO exposures(user_id, merchant_id, event_type, event_time, task_id)
                    VALUES(?,?,?,?,?)
                    """,
                    (user_id, mid, "impression", at, task_id),
                )

    def count_deliveries_since(self, user_id: str, since: datetime) -> int:
        since_iso = to_utc_iso(since)
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS c FROM deliveries
                WHERE user_id = ? AND delivered_at >= ?
                """,
                (user_id, since_iso),
            ).fetchone()
            return int(row["c"] if row else 0)

    def get_delivery_summary(self, user_id: str) -> DeliverySummary:
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT delivered_at, merchant_ids_json, categories_json FROM deliveries
                WHERE user_id = ?
                ORDER BY delivered_at DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()
            if not row:
                return DeliverySummary(None, [], [])
            return DeliverySummary(
                last_success_at=row["delivered_at"],
                merchant_ids=json.loads(row["merchant_ids_json"]),
                categories=json.loads(row["categories_json"]),
            )

    def record_scene_seen(self, user_id: str, scene_key: str, *, seen_at: str | None = None) -> None:
        at = to_utc_iso(seen_at)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO scene_seen(user_id, scene_key, seen_at)
                VALUES(?,?,?)
                ON CONFLICT(user_id, scene_key) DO UPDATE SET seen_at=excluded.seen_at
                """,
                (user_id, scene_key, at),
            )

    def scene_seen_within(
        self, user_id: str, scene_key: str, *, window: timedelta, now: datetime | None = None
    ) -> bool:
        clock = now or utc_now()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT seen_at FROM scene_seen WHERE user_id = ? AND scene_key = ?",
                (user_id, scene_key),
            ).fetchone()
            if not row:
                return False
            seen = to_utc(row["seen_at"])
            if seen is None:
                return False
            return clock - seen < window

    def record_exposure(
        self,
        user_id: str,
        merchant_id: str,
        event_type: str,
        *,
        event_time: str | None = None,
        task_id: str | None = None,
    ) -> None:
        at = to_utc_iso(event_time)
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO exposures(user_id, merchant_id, event_type, event_time, task_id)
                VALUES(?,?,?,?,?)
                """,
                (user_id, merchant_id, event_type, at, task_id),
            )

    def list_exposure_merchants(
        self,
        user_id: str,
        *,
        since: datetime,
        limit: int = 20,
        event_types: tuple[str, ...] = ("impression", "click", "not_interested"),
    ) -> list[ExposureItem]:
        since_iso = to_utc_iso(since)
        placeholders = ",".join("?" * len(event_types))
        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT merchant_id, event_type, event_time FROM exposures
                WHERE user_id = ? AND event_time >= ? AND event_type IN ({placeholders})
                  AND merchant_id IS NOT NULL AND merchant_id != ''
                ORDER BY event_time DESC
                LIMIT ?
                """,
                (user_id, since_iso, *event_types, limit),
            ).fetchall()
            return [
                ExposureItem(
                    merchant_id=r["merchant_id"],
                    event_type=r["event_type"],
                    event_time=r["event_time"],
                )
                for r in rows
            ]
