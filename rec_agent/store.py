from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "rec_agent.db"


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

    def save_task(self, task_id: str, tenant_id: str, user_id: str, key: str, status: str, payload: dict) -> None:
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
            row = conn.execute("SELECT payload_json FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
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

    def add_feedback(self, task_id: str, user_id: str, merchant_id: str | None, event_type: str, event_time: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO feedback(task_id, user_id, merchant_id, event_type, event_time) VALUES(?,?,?,?,?)",
                (task_id, user_id, merchant_id, event_type, event_time),
            )
