import sqlite3
from pathlib import Path

import pytest

from mailbox_rescue.storage.checkpoint import (
    CheckpointStore,
    CompletedMessage,
    ExportMetadata,
    FailedMessage,
    check_resume_compatibility,
)


def test_checkpoint_records_completed_message(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")

    assert not store.is_completed("abc123")

    store.mark_completed(
        CompletedMessage(
            message_id="abc123",
            relative_path="messages/abc123.eml",
            sha256="a" * 64,
            size_bytes=42,
        )
    )

    assert store.is_completed("abc123")
    assert store.completed_count() == 1


def test_checkpoint_upsert_updates_existing_record(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")

    initial = CompletedMessage(
        message_id="abc123",
        relative_path="messages/abc123.eml",
        sha256="a" * 64,
        size_bytes=42,
    )
    store.mark_completed(initial)

    updated = CompletedMessage(
        message_id="abc123",
        relative_path="messages/abc123_updated.eml",
        sha256="b" * 64,
        size_bytes=128,
    )
    store.mark_completed(updated)

    assert store.completed_count() == 1
    assert store.is_completed("abc123")
    assert store.get_completed("abc123") == updated
    assert store.get_completed("nonexistent") is None


def test_checkpoint_tracks_and_clears_failures(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")

    assert store.failed_count() == 0
    assert store.list_failures() == []
    assert store.get_failure("msg_fail_1") is None

    failure1 = FailedMessage(
        message_id="msg_fail_1",
        error_type="HttpError",
        error_message="404 Not Found",
        attempt_count=1,
        last_failed_at="2026-08-27T12:00:00+00:00",
    )
    store.mark_failed(failure1)

    assert store.failed_count() == 1
    assert store.get_failure("msg_fail_1") == failure1
    assert not store.is_completed("msg_fail_1")

    # Upsert on second failure
    failure1_retry = FailedMessage(
        message_id="msg_fail_1",
        error_type="HttpError",
        error_message="503 Service Unavailable",
        attempt_count=4,
        last_failed_at="2026-08-27T12:05:00+00:00",
    )
    store.mark_failed(failure1_retry)
    assert store.failed_count() == 1
    assert store.get_failure("msg_fail_1") == failure1_retry

    # Add second failure
    failure2 = FailedMessage(
        message_id="msg_fail_2",
        error_type="TimeoutError",
        error_message="Connection timed out",
        attempt_count=4,
        last_failed_at="2026-08-27T12:10:00+00:00",
    )
    store.mark_failed(failure2)
    assert store.failed_count() == 2

    failures = store.list_failures()
    assert len(failures) == 2
    assert [f.message_id for f in failures] == ["msg_fail_1", "msg_fail_2"]

    # Clear one failure
    store.clear_failure("msg_fail_1")
    assert store.failed_count() == 1
    assert store.get_failure("msg_fail_1") is None
    assert store.get_failure("msg_fail_2") == failure2


def test_checkpoint_mark_completed_atomically_clears_failure(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")

    failure = FailedMessage(
        message_id="msg_1",
        error_type="HttpError",
        error_message="503 Service Unavailable",
        attempt_count=3,
        last_failed_at="2026-08-27T12:00:00+00:00",
    )
    store.mark_failed(failure)

    assert store.failed_count() == 1
    assert store.get_failure("msg_1") == failure
    assert not store.is_completed("msg_1")

    # Marking completed atomically clears failure row in the same transaction
    completed = CompletedMessage(
        message_id="msg_1",
        relative_path="messages/msg_1.eml",
        sha256="c" * 64,
        size_bytes=256,
    )
    store.mark_completed(completed)

    assert store.is_completed("msg_1") is True
    assert store.get_completed("msg_1") == completed
    assert store.get_failure("msg_1") is None
    assert store.failed_count() == 0


def test_checkpoint_mark_completed_transaction_integrity(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")

    failure = FailedMessage(
        message_id="msg_rollback",
        error_type="HttpError",
        error_message="500 Internal Server Error",
        attempt_count=2,
        last_failed_at="2026-08-27T12:00:00+00:00",
    )
    store.mark_failed(failure)

    # Wrap _connect to inject a failure after the first SQL statement
    original_connect = store._connect

    class FaultyConnection:
        def __init__(self, real_conn) -> None:
            self._real = real_conn

        def __enter__(self):
            self._real.__enter__()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return self._real.__exit__(exc_type, exc_val, exc_tb)

        def execute(self, sql, params=()):
            if "DELETE FROM failed_messages" in sql:
                raise sqlite3.OperationalError("Simulated second statement disk failure")
            return self._real.execute(sql, params)

    def faulty_connect():
        return FaultyConnection(original_connect())

    store._connect = faulty_connect

    completed = CompletedMessage(
        message_id="msg_rollback",
        relative_path="messages/msg_rollback.eml",
        sha256="d" * 64,
        size_bytes=512,
    )

    with pytest.raises(sqlite3.OperationalError, match="Simulated second statement disk failure"):
        store.mark_completed(completed)

    # Restore connection method
    store._connect = original_connect

    # Verify rollback: completed_messages must NOT contain msg_rollback, and failure row must remain intact
    assert not store.is_completed("msg_rollback")
    assert store.get_failure("msg_rollback") == failure
    assert store.failed_count() == 1


def test_checkpoint_export_metadata_lifecycle(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")

    # Initial state is empty
    assert store.get_metadata() is None

    # Set metadata for first time
    store.set_metadata("user@example.com", "all_mail")
    meta = store.get_metadata()
    assert isinstance(meta, ExportMetadata)
    assert meta.account_email == "user@example.com"
    assert meta.export_scope == "all_mail"
    assert meta.created_at is not None
    assert meta.last_updated_at is not None

    # Update metadata (e.g. scope change or refresh)
    initial_created_at = meta.created_at
    store.set_metadata("user@example.com", "inbox")
    updated = store.get_metadata()
    assert updated is not None
    assert updated.account_email == "user@example.com"
    assert updated.export_scope == "inbox"
    assert updated.created_at == initial_created_at


def test_checkpoint_resume_compatibility_rules(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")

    # Fresh store with no metadata and no messages is compatible
    compatible, reason = check_resume_compatibility(store, "user@example.com", "all_mail")
    assert compatible is True
    assert reason is None

    # Store with recorded identity
    store.set_metadata("user@example.com", "all_mail")

    # 1. Same account + same scope -> compatible
    compatible, reason = check_resume_compatibility(store, "user@example.com", "all_mail")
    assert compatible is True
    assert reason is None

    # Case-insensitive email comparison
    compatible, reason = check_resume_compatibility(store, "USER@example.COM", "all_mail")
    assert compatible is True
    assert reason is None

    # 2. Different account -> rejected
    compatible, reason = check_resume_compatibility(store, "other@example.com", "all_mail")
    assert compatible is False
    assert reason is not None
    assert "user@example.com" in reason
    assert "Choose a different folder" in reason

    # 3. Same account + different scope -> rejected
    compatible, reason = check_resume_compatibility(store, "user@example.com", "inbox")
    assert compatible is False
    assert reason is not None
    assert "All Mail" in reason
    assert "Choose 'All Mail' to resume" in reason


def test_checkpoint_legacy_unbound_checkpoints_fail_closed(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    store = CheckpointStore(db_path)

    # 1. Store with completed message but no metadata -> Rejected
    store.mark_completed(
        CompletedMessage(
            message_id="legacy_msg_1",
            relative_path="messages/legacy_msg_1.eml",
            sha256="e" * 64,
            size_bytes=100,
        )
    )
    assert store.get_metadata() is None
    compatible, reason = check_resume_compatibility(store, "user@example.com", "all_mail")
    assert compatible is False
    assert reason is not None
    assert "older checkpoint that is not bound" in reason

    # 2. Store with failed message but no metadata -> Rejected
    db_path2 = tmp_path / "legacy_failed.sqlite3"
    store2 = CheckpointStore(db_path2)
    store2.mark_failed(
        FailedMessage(
            message_id="fail_1",
            error_type="HttpError",
            error_message="503",
            attempt_count=2,
            last_failed_at="2026-08-28T00:00:00+00:00",
        )
    )
    assert store2.get_metadata() is None
    compatible, reason = check_resume_compatibility(store2, "user@example.com", "all_mail")
    assert compatible is False
    assert reason is not None
    assert "older checkpoint that is not bound" in reason


def test_checkpoint_metadata_singleton_and_migration(tmp_path: Path) -> None:
    db_path = tmp_path / "migrated.sqlite3"

    # Simulate older schema without id column
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE export_metadata (
                account_email TEXT NOT NULL,
                export_scope TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO export_metadata (account_email, export_scope, created_at, last_updated_at)
            VALUES ('old@example.com', 'all_mail', '2026-08-20T10:00:00+00:00', '2026-08-20T10:00:00+00:00')
            """
        )

    # Opening with CheckpointStore triggers migration
    store = CheckpointStore(db_path)
    meta = store.get_metadata()
    assert meta is not None
    assert meta.account_email == "old@example.com"
    assert meta.created_at == "2026-08-20T10:00:00+00:00"

    # Updating preserves singleton and created_at
    store.set_metadata("updated@example.com", "inbox")
    updated = store.get_metadata()
    assert updated is not None
    assert updated.account_email == "updated@example.com"
    assert updated.export_scope == "inbox"
    assert updated.created_at == "2026-08-20T10:00:00+00:00"

    with sqlite3.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM export_metadata").fetchone()[0]
        assert count == 1


def test_legacy_metadata_migration_failure_preserves_original_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "rollback_migration.sqlite3"

    # Create legacy unkeyed table with initial row
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE export_metadata (
                account_email TEXT NOT NULL,
                export_scope TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO export_metadata (account_email, export_scope, created_at, last_updated_at)
            VALUES ('legacy_user@example.com', 'all_mail', '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00')
            """
        )

    original_connect = sqlite3.connect

    class FailingConnection:
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn

        def execute(self, sql: str, *args, **kwargs):
            if "INSERT INTO export_metadata" in sql:
                raise sqlite3.OperationalError("Simulated disk I/O failure during migration insert")
            return self._conn.execute(sql, *args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self._conn.__exit__(*args)

    monkeypatch.setattr(
        CheckpointStore,
        "_connect",
        lambda self: FailingConnection(original_connect(self.database_path)),
    )

    with pytest.raises(sqlite3.OperationalError, match="Simulated disk I/O failure"):
        CheckpointStore(db_path)

    # Verify that the original legacy table export_metadata was restored and data was preserved
    with sqlite3.connect(db_path) as conn:
        columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(export_metadata)").fetchall()
        ]
        assert "id" not in columns
        assert "account_email" in columns

        row = conn.execute("SELECT account_email, export_scope, created_at FROM export_metadata").fetchone()
        assert row is not None
        assert row[0] == "legacy_user@example.com"
        assert row[1] == "all_mail"
        assert row[2] == "2026-08-01T00:00:00+00:00"





