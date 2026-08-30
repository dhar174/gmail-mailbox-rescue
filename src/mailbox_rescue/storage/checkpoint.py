from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class MessageMetadata:
    message_id: str
    thread_id: str
    labels_json: str
    captured_at: str


@dataclass(frozen=True, slots=True)
class CompletedMessage:
    message_id: str
    relative_path: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class FailedMessage:
    message_id: str
    error_type: str
    error_message: str
    attempt_count: int
    last_failed_at: str


@dataclass(frozen=True, slots=True)
class ExportMetadata:
    account_email: str
    export_scope: str
    created_at: str
    last_updated_at: str


def check_resume_compatibility(
    checkpoint_store: CheckpointStore,
    account_email: str,
    export_scope: str,
) -> tuple[bool, str | None]:
    metadata = checkpoint_store.get_metadata()
    if metadata is None:
        if checkpoint_store.completed_count() > 0 or checkpoint_store.failed_count() > 0:
            return (
                False,
                (
                    "This export folder contains an older checkpoint that is not bound to a Google account. "
                    "Choose a new folder for this export."
                ),
            )
        return True, None
    if metadata.account_email.strip().lower() != account_email.strip().lower():
        return (
            False,
            f"This export folder belongs to {metadata.account_email}. Choose a different folder.",
        )
    if metadata.export_scope != export_scope:
        existing_scope_display = "All Mail" if metadata.export_scope == "all_mail" else "Inbox only"
        return (
            False,
            (
                f"This folder contains an existing {existing_scope_display} export. "
                f"Choose '{existing_scope_display}' to resume it or select a different destination."
            ),
        )
    return True, None


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

    def list_completed(self) -> list[CompletedMessage]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_id, relative_path, sha256, size_bytes
                FROM completed_messages
                ORDER BY message_id ASC
                """
            ).fetchall()
        return [
            CompletedMessage(
                message_id=row[0],
                relative_path=row[1],
                sha256=row[2],
                size_bytes=int(row[3]),
            )
            for row in rows
        ]

    def mark_completed(
        self,
        message: CompletedMessage,
        message_metadata: MessageMetadata | None = None,
    ) -> None:
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
            if message_metadata is not None:
                connection.execute(
                    """
                    INSERT INTO message_metadata (
                        message_id, thread_id, labels_json, captured_at
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        thread_id = excluded.thread_id,
                        labels_json = excluded.labels_json,
                        captured_at = excluded.captured_at
                    """,
                    (
                        message.message_id,
                        message_metadata.thread_id,
                        message_metadata.labels_json,
                        message_metadata.captured_at,
                    ),
                )
            connection.execute(
                "DELETE FROM failed_messages WHERE message_id = ?",
                (message.message_id,),
            )

    def get_message_metadata(self, message_id: str) -> MessageMetadata | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT message_id, thread_id, labels_json, captured_at
                FROM message_metadata
                WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
        if row is None:
            return None
        return MessageMetadata(
            message_id=row[0],
            thread_id=row[1],
            labels_json=row[2],
            captured_at=row[3],
        )

    def set_message_metadata(self, metadata: MessageMetadata) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO message_metadata (
                    message_id, thread_id, labels_json, captured_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    labels_json = excluded.labels_json,
                    captured_at = excluded.captured_at
                """,
                (
                    metadata.message_id,
                    metadata.thread_id,
                    metadata.labels_json,
                    metadata.captured_at,
                ),
            )

    def get_all_message_metadata(self) -> dict[str, MessageMetadata]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_id, thread_id, labels_json, captured_at
                FROM message_metadata
                """
            ).fetchall()
        return {
            row[0]: MessageMetadata(
                message_id=row[0],
                thread_id=row[1],
                labels_json=row[2],
                captured_at=row[3],
            )
            for row in rows
        }

    def completed_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM completed_messages").fetchone()
        return int(row[0])

    def mark_failed(self, failure: FailedMessage) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO failed_messages (
                    message_id, error_type, error_message, attempt_count, last_failed_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    error_type = excluded.error_type,
                    error_message = excluded.error_message,
                    attempt_count = excluded.attempt_count,
                    last_failed_at = excluded.last_failed_at
                """,
                (
                    failure.message_id,
                    failure.error_type,
                    failure.error_message,
                    failure.attempt_count,
                    failure.last_failed_at,
                ),
            )

    def clear_failure(self, message_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM failed_messages WHERE message_id = ?",
                (message_id,),
            )

    def get_failure(self, message_id: str) -> FailedMessage | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT message_id, error_type, error_message, attempt_count, last_failed_at
                FROM failed_messages
                WHERE message_id = ?
                """,
                (message_id,),
            ).fetchone()
        if row is None:
            return None
        return FailedMessage(
            message_id=row[0],
            error_type=row[1],
            error_message=row[2],
            attempt_count=int(row[3]),
            last_failed_at=row[4],
        )

    def list_failures(self) -> list[FailedMessage]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_id, error_type, error_message, attempt_count, last_failed_at
                FROM failed_messages
                ORDER BY last_failed_at ASC
                """
            ).fetchall()
        return [
            FailedMessage(
                message_id=row[0],
                error_type=row[1],
                error_message=row[2],
                attempt_count=int(row[3]),
                last_failed_at=row[4],
            )
            for row in rows
        ]

    def failed_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM failed_messages").fetchone()
        return int(row[0])

    def get_metadata(self) -> ExportMetadata | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT account_email, export_scope, created_at, last_updated_at
                FROM export_metadata
                WHERE id = 1
                """
            ).fetchone()
        if row is None:
            return None
        return ExportMetadata(
            account_email=row[0],
            export_scope=row[1],
            created_at=row[2],
            last_updated_at=row[3],
        )

    def set_metadata(self, account_email: str, export_scope: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO export_metadata (
                    id, account_email, export_scope, created_at, last_updated_at
                )
                VALUES (1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    account_email = excluded.account_email,
                    export_scope = excluded.export_scope,
                    last_updated_at = excluded.last_updated_at
                """,
                (account_email, export_scope, now, now),
            )

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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS message_metadata (
                    message_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    labels_json TEXT NOT NULL,
                    captured_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS failed_messages (
                    message_id TEXT PRIMARY KEY,
                    error_type TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    last_failed_at TEXT NOT NULL
                )
                """
            )
            columns = [
                row[1]
                for row in connection.execute("PRAGMA table_info(export_metadata)").fetchall()
            ]
            if columns and "id" not in columns:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT account_email, export_scope, created_at, last_updated_at
                    FROM export_metadata
                    ORDER BY rowid ASC
                    LIMIT 1
                    """
                ).fetchone()
                connection.execute("ALTER TABLE export_metadata RENAME TO _legacy_export_metadata")
                try:
                    connection.execute(
                        """
                        CREATE TABLE export_metadata (
                            id INTEGER PRIMARY KEY CHECK (id = 1),
                            account_email TEXT NOT NULL,
                            export_scope TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            last_updated_at TEXT NOT NULL
                        )
                        """
                    )
                    if row is not None:
                        connection.execute(
                            """
                            INSERT INTO export_metadata (
                                id, account_email, export_scope, created_at, last_updated_at
                            )
                            VALUES (1, ?, ?, ?, ?)
                            """,
                            row,
                        )
                    connection.execute("DROP TABLE _legacy_export_metadata")
                except Exception:
                    connection.execute("DROP TABLE IF EXISTS export_metadata")
                    connection.execute(
                        "ALTER TABLE _legacy_export_metadata RENAME TO export_metadata"
                    )
                    raise
            else:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS export_metadata (
                        id INTEGER PRIMARY KEY CHECK (id = 1),
                        account_email TEXT NOT NULL,
                        export_scope TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        last_updated_at TEXT NOT NULL
                    )
                    """
                )
