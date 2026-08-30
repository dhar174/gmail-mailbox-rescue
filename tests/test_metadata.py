import json
from pathlib import Path

from mailbox_rescue.export.metadata import (
    write_account_metadata,
    write_labels_metadata,
    write_messages_metadata,
    write_portable_metadata,
)
from mailbox_rescue.gmail.client import GmailLabel
from mailbox_rescue.storage.checkpoint import (
    CheckpointStore,
    CompletedMessage,
    MessageMetadata,
)


def test_write_account_metadata_creates_valid_json(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = CheckpointStore(root / "export.sqlite3")
    store.set_metadata(account_email="alice@example.com", export_scope="all_mail")

    target = write_account_metadata(root, store)
    assert target.exists()
    assert not (root / "metadata" / "account.json.part").exists()

    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["account_email"] == "alice@example.com"
    assert data["export_scope"] == "all_mail"
    assert "archive_created_at" in data
    assert "archive_updated_at" in data
    assert "metadata_generated_at" in data


def test_write_labels_metadata_preserves_unicode_and_types(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    labels = [
        GmailLabel(id="SENT", name="SENT", type="system"),
        GmailLabel(id="INBOX", name="INBOX", type="system"),
        GmailLabel(id="Label_99", name="Projects / 集中", type="user"),
    ]

    target = write_labels_metadata(root, labels)
    assert target.exists()
    assert not (root / "metadata" / "labels.json.part").exists()

    raw_text = target.read_text(encoding="utf-8")
    assert "集中" in raw_text  # ensure_ascii=False preservation

    data = json.loads(raw_text)
    assert len(data) == 3
    # Sorted deterministically by id
    assert data[0]["id"] == "INBOX"
    assert data[1]["id"] == "Label_99"
    assert data[1]["name"] == "Projects / 集中"
    assert data[1]["type"] == "user"
    assert data[2]["id"] == "SENT"


def test_write_messages_metadata_jsonl_structure_and_ordering(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = CheckpointStore(root / "export.sqlite3")

    msg_b = CompletedMessage(
        message_id="msg_b",
        relative_path="messages/msg_b.eml",
        sha256="b" * 64,
        size_bytes=200,
    )
    meta_b = MessageMetadata(
        message_id="msg_b",
        thread_id="th_b",
        labels_json='[{"id": "INBOX", "name": "INBOX"}]',
        captured_at="2026-08-28T12:00:00+00:00",
    )

    msg_a = CompletedMessage(
        message_id="msg_a",
        relative_path="messages/msg_a.eml",
        sha256="a" * 64,
        size_bytes=100,
    )
    meta_a = MessageMetadata(
        message_id="msg_a",
        thread_id="th_a",
        labels_json='[{"id": "UNREAD", "name": "UNREAD"}, {"id": "L1", "name": "Work"}]',
        captured_at="2026-08-28T12:05:00+00:00",
    )

    store.mark_completed(msg_b, meta_b)
    store.mark_completed(msg_a, meta_a)

    target = write_messages_metadata(root, store)
    assert target.exists()

    lines = [line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2

    # Deterministic order by message_id
    rec_a = json.loads(lines[0])
    assert rec_a["gmail_message_id"] == "msg_a"
    assert rec_a["thread_id"] == "th_a"
    assert rec_a["relative_path"] == "messages/msg_a.eml"
    assert rec_a["sha256"] == "a" * 64
    assert rec_a["size_bytes"] == 100
    assert len(rec_a["labels"]) == 2
    assert rec_a["labels"][0] == {"id": "UNREAD", "name": "UNREAD"}
    assert rec_a["labels"][1] == {"id": "L1", "name": "Work"}

    rec_b = json.loads(lines[1])
    assert rec_b["gmail_message_id"] == "msg_b"
    assert rec_b["thread_id"] == "th_b"

    # Verify no raw body is included
    assert "body" not in rec_a
    assert "raw" not in rec_a


def test_write_portable_metadata_bundle(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = CheckpointStore(root / "export.sqlite3")
    store.set_metadata(account_email="bob@example.com", export_scope="inbox")

    write_portable_metadata(root, store, [GmailLabel(id="INBOX", name="INBOX")])

    assert (root / "metadata" / "account.json").is_file()
    assert (root / "metadata" / "labels.json").is_file()
    assert (root / "metadata" / "messages.jsonl").is_file()
