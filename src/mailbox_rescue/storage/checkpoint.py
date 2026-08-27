from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CompletedMessage:
    message_id: str
    relative_path: str
    sha256: str
    size_bytes: int


class CheckpointStore:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self._initialize()

    def is_completed(self, message_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM completed_messages WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return row is not None

    def get_completed(self, message_id: str) -> CompletedMessage | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT message_id, relative_path, sha256, size_bytes
                FROM completed_messages
                WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
        if row is None:
            return None
        return CompletedMessage(
            message_id=row[0],
            relative_path=row[1],
            sha256=row[2],
            size_bytes=int(row[3]),
        )

    def mark_completed(self, message: CompletedMessage) -> None:
        completed_at = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO completed_messages (
                    message_id, relative_path, sha256, size_bytes, completed_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    sha256 = excluded.sha256,
                    size_bytes = excluded.size_bytes,
                    completed_at = excluded.completed_at
                """,
                (
                    message.message_id,
                    message.relative_path,
                    message.sha256,
                    message.size_bytes,
                    completed_at,
                ),
            )

    def completed_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM completed_messages").fetchone()
        return int(row[0])

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.database_path)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS completed_messages (
                    message_id TEXT PRIMARY KEY,
                    relative_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    completed_at TEXT NOT NULL
                )
                """
            )
