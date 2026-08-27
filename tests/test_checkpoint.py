from pathlib import Path

from mailbox_rescue.storage.checkpoint import CheckpointStore, CompletedMessage


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
