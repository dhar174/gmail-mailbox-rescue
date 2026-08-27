from pathlib import Path

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

