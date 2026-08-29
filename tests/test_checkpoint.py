import sqlite3
from pathlib import Path

import pytest

from mailbox_rescue.storage.checkpoint import (
    CheckpointStore,
    CompletedMessage,
    FailedMessage,
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


