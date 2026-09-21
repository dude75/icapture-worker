"""SQLite persistence for capture tasks."""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.schemas import ErrorDetail, TaskStatus


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class TaskRecord:
    task_id: str
    status: TaskStatus
    connector: str
    meeting_host: str
    meeting_room: str
    display_name: str
    pin: str
    jwt: str | None
    started_at: str
    stopped_at: str | None = None
    duration_sec: float | None = None
    artifact_path: str | None = None
    artifact_size_bytes: int | None = None
    error: dict | None = None


class TaskStore:
    def __init__(self, sqlite_path: str) -> None:
        self._path = sqlite_path
        Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS capture_tasks (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                connector TEXT NOT NULL,
                meeting_host TEXT NOT NULL,
                meeting_room TEXT NOT NULL,
                display_name TEXT NOT NULL,
                pin TEXT NOT NULL,
                jwt TEXT,
                started_at TEXT NOT NULL,
                stopped_at TEXT,
                duration_sec REAL,
                artifact_path TEXT,
                artifact_size_bytes INTEGER,
                error_json TEXT
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create(
        self,
        *,
        task_id: str,
        connector: str,
        meeting_host: str,
        meeting_room: str,
        display_name: str,
        pin: str,
        jwt: str | None,
    ) -> TaskRecord:
        started_at = _utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO capture_tasks (
                    task_id, status, connector, meeting_host, meeting_room,
                    display_name, pin, jwt, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    TaskStatus.queued.value,
                    connector,
                    meeting_host,
                    meeting_room,
                    display_name,
                    pin,
                    jwt,
                    started_at,
                ),
            )
            self._conn.commit()
        return self.get(task_id)  # type: ignore[return-value]

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM capture_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def list_tasks(self, status: TaskStatus | None = None) -> list[TaskRecord]:
        with self._lock:
            if status is None:
                rows = self._conn.execute(
                    "SELECT * FROM capture_tasks ORDER BY started_at DESC"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM capture_tasks WHERE status = ? ORDER BY started_at DESC",
                    (status.value,),
                ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def count_active(self) -> int:
        active = {
            TaskStatus.queued.value,
            TaskStatus.joining.value,
            TaskStatus.capturing.value,
            TaskStatus.finalizing.value,
        }
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) FROM capture_tasks
                WHERE status IN ({",".join("?" for _ in active)})
                """,
                tuple(active),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def count_queued(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM capture_tasks WHERE status = ?",
                (TaskStatus.queued.value,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def list_queued_fifo(self) -> list[TaskRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM capture_tasks
                WHERE status = ?
                ORDER BY started_at ASC
                """,
                (TaskStatus.queued.value,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def update_status(self, task_id: str, status: TaskStatus) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE capture_tasks SET status = ? WHERE task_id = ?",
                (status.value, task_id),
            )
            self._conn.commit()

    def mark_success(
        self,
        task_id: str,
        *,
        stopped_at: str,
        duration_sec: float,
        artifact_path: str,
        artifact_size_bytes: int,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE capture_tasks
                SET status = ?, stopped_at = ?, duration_sec = ?,
                    artifact_path = ?, artifact_size_bytes = ?,
                    error_json = NULL
                WHERE task_id = ?
                """,
                (
                    TaskStatus.success.value,
                    stopped_at,
                    duration_sec,
                    artifact_path,
                    artifact_size_bytes,
                    task_id,
                ),
            )
            self._conn.commit()

    def mark_error(self, task_id: str, error: ErrorDetail) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE capture_tasks
                SET status = ?, error_json = ?, stopped_at = COALESCE(stopped_at, ?)
                WHERE task_id = ?
                """,
                (
                    TaskStatus.error.value,
                    json.dumps(error.model_dump(mode="json")),
                    _utc_now(),
                    task_id,
                ),
            )
            self._conn.commit()

    def mark_canceled(self, task_id: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE capture_tasks
                SET status = ?, stopped_at = ?, artifact_path = NULL,
                    artifact_size_bytes = NULL, error_json = NULL
                WHERE task_id = ?
                """,
                (TaskStatus.canceled.value, _utc_now(), task_id),
            )
            self._conn.commit()

    def purge_expired(self, ttl_sec: int) -> list[str]:
        if ttl_sec <= 0:
            return []
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=ttl_sec)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        terminal = (
            TaskStatus.success.value,
            TaskStatus.error.value,
            TaskStatus.canceled.value,
        )
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT task_id FROM capture_tasks
                WHERE status IN ({",".join("?" for _ in terminal)})
                  AND COALESCE(stopped_at, started_at) <= ?
                """,
                (*terminal, cutoff),
            ).fetchall()
            task_ids = [str(row[0]) for row in rows]
            if not task_ids:
                return []
            self._conn.execute(
                f"""
                DELETE FROM capture_tasks
                WHERE task_id IN ({",".join("?" for _ in task_ids)})
                """,
                tuple(task_ids),
            )
            self._conn.commit()
        return task_ids

    def list_unfinished(self) -> list[TaskRecord]:
        active = {
            TaskStatus.queued.value,
            TaskStatus.joining.value,
            TaskStatus.capturing.value,
            TaskStatus.finalizing.value,
        }
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT * FROM capture_tasks
                WHERE status IN ({",".join("?" for _ in active)})
                """,
                tuple(active),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> TaskRecord:
        error = json.loads(row["error_json"]) if row["error_json"] else None
        return TaskRecord(
            task_id=row["task_id"],
            status=TaskStatus(row["status"]),
            connector=row["connector"],
            meeting_host=row["meeting_host"],
            meeting_room=row["meeting_room"],
            display_name=row["display_name"],
            pin=row["pin"],
            jwt=row["jwt"],
            started_at=row["started_at"],
            stopped_at=row["stopped_at"],
            duration_sec=row["duration_sec"],
            artifact_path=row["artifact_path"],
            artifact_size_bytes=row["artifact_size_bytes"],
            error=error,
        )
