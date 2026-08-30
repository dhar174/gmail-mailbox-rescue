import json
from pathlib import Path

from mailbox_rescue.export.metadata import (
    is_metadata_complete,
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
    assert target is not None
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


def test_write_labels_metadata_handles_empty_list_and_none_preservation(tmp_path: Path) -> None:
    root = tmp_path / "archive"

    # 1. Successful lookup with zero labels creates []
    t1 = write_labels_metadata(root, [])
    assert t1 is not None and t1.is_file()
    assert json.loads(t1.read_text(encoding="utf-8")) == []

    # 2. Write valid labels
    labels = [GmailLabel(id="INBOX", name="INBOX")]
    t2 = write_labels_metadata(root, labels)
    assert t2 is not None
    initial_content = t2.read_text(encoding="utf-8")

    # 3. Subsequent lookup failure (labels=None) preserves existing labels.json
    t3 = write_labels_metadata(root, None)
    assert t3 is not None
    assert t3.read_text(encoding="utf-8") == initial_content

    # 4. Lookup failure on clean directory does not create file
    clean_root = tmp_path / "clean"
    t4 = write_labels_metadata(clean_root, None)
    assert t4 is None
    assert not (clean_root / "metadata" / "labels.json").exists()


def test_is_metadata_complete_validation_rules() -> None:
    # 1. Valid full metadata
    assert is_metadata_complete(
        MessageMetadata(
            message_id="m1",
            thread_id="t1",
            labels_json='[{"id": "INBOX", "name": "INBOX"}]',
            captured_at="2026-08-28T00:00:00Z",
        )
    )

    # 2. Valid with degraded label (id present without name)
    assert is_metadata_complete(
        MessageMetadata(
            message_id="m2",
            thread_id="t2",
            labels_json='[{"id": "Label_123"}]',
            captured_at="2026-08-28T00:00:00Z",
        )
    )

    # 3. Valid with genuine empty label list
    assert is_metadata_complete(
        MessageMetadata(
            message_id="m3",
            thread_id="t3",
            labels_json="[]",
            captured_at="2026-08-28T00:00:00Z",
        )
    )

    # 4. None record
    assert not is_metadata_complete(None)

    # 5. Empty or whitespace thread_id
    assert not is_metadata_complete(
        MessageMetadata(
            message_id="m5", thread_id="", labels_json="[]", captured_at="2026-08-28T00:00:00Z"
        )
    )
    assert not is_metadata_complete(
        MessageMetadata(
            message_id="m5", thread_id="   ", labels_json="[]", captured_at="2026-08-28T00:00:00Z"
        )
    )

    # 6. Malformed JSON
    assert not is_metadata_complete(
        MessageMetadata(
            message_id="m6",
            thread_id="t6",
            labels_json="{invalid json",
            captured_at="2026-08-28T00:00:00Z",
        )
    )

    # 7. Non-list JSON
    assert not is_metadata_complete(
        MessageMetadata(
            message_id="m7",
            thread_id="t7",
            labels_json='{"not": "a list"}',
            captured_at="2026-08-28T00:00:00Z",
        )
    )
    assert not is_metadata_complete(
        MessageMetadata(
            message_id="m7",
            thread_id="t7",
            labels_json='"string"',
            captured_at="2026-08-28T00:00:00Z",
        )
    )

    # 8. Label entry missing id or non-dict
    assert not is_metadata_complete(
        MessageMetadata(
            message_id="m8",
            thread_id="t8",
            labels_json='[{"name": "No ID"}]',
            captured_at="2026-08-28T00:00:00Z",
        )
    )
    assert not is_metadata_complete(
        MessageMetadata(
            message_id="m8",
            thread_id="t8",
            labels_json='["INBOX"]',
            captured_at="2026-08-28T00:00:00Z",
        )
    )


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

    lines = [
        line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
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


def test_write_messages_metadata_excludes_incomplete_records_and_reports_warnings(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    store = CheckpointStore(root / "export.sqlite3")

    msg_good = CompletedMessage(
        message_id="msg_good", relative_path="messages/good.eml", sha256="1" * 64, size_bytes=100
    )
    msg_no_meta = CompletedMessage(
        message_id="msg_no_meta",
        relative_path="messages/no_meta.eml",
        sha256="2" * 64,
        size_bytes=100,
    )
    msg_empty_thread = CompletedMessage(
        message_id="msg_empty_thread",
        relative_path="messages/empty_th.eml",
        sha256="3" * 64,
        size_bytes=100,
    )

    store.mark_completed(
        msg_good,
        MessageMetadata(
            message_id="msg_good",
            thread_id="t1",
            labels_json="[]",
            captured_at="2026-08-28T00:00:00Z",
        ),
    )
    store.mark_completed(msg_no_meta)  # No metadata row
    store.mark_completed(
        msg_empty_thread,
        MessageMetadata(
            message_id="msg_empty_thread",
            thread_id="",
            labels_json="[]",
            captured_at="2026-08-28T00:00:00Z",
        ),
    )

    warnings: list[str] = []
    target = write_messages_metadata(root, store, warnings_collector=warnings)

    lines = [
        line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 1
    assert json.loads(lines[0])["gmail_message_id"] == "msg_good"

    assert len(warnings) == 2
    assert any("msg_no_meta" in w for w in warnings)
    assert any("msg_empty_thread" in w for w in warnings)


def test_write_portable_metadata_bundle(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = CheckpointStore(root / "export.sqlite3")
    store.set_metadata(account_email="bob@example.com", export_scope="inbox")

    write_portable_metadata(root, store, [GmailLabel(id="INBOX", name="INBOX")])

    assert (root / "metadata" / "account.json").is_file()
    assert (root / "metadata" / "labels.json").is_file()
    assert (root / "metadata" / "messages.jsonl").is_file()


def test_write_account_metadata_without_checkpoint_metadata_is_truthful(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = CheckpointStore(root / "export.sqlite3")

    target = write_account_metadata(root, store)
    data = json.loads(target.read_text(encoding="utf-8"))

    assert data["account_email"] is None
    assert data["export_scope"] is None
    assert "archive_created_at" in data
    assert "metadata_generated_at" in data


def test_write_messages_metadata_respects_verified_messages_subset(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    store = CheckpointStore(root / "export.sqlite3")

    msg_a = CompletedMessage(
        message_id="msg_a", relative_path="messages/msg_a.eml", sha256="a" * 64, size_bytes=100
    )
    msg_b = CompletedMessage(
        message_id="msg_b", relative_path="messages/msg_b.eml", sha256="b" * 64, size_bytes=200
    )

    store.mark_completed(
        msg_a,
        MessageMetadata(
            message_id="msg_a",
            thread_id="t_a",
            labels_json="[]",
            captured_at="2026-08-28T00:00:00Z",
        ),
    )
    store.mark_completed(
        msg_b,
        MessageMetadata(
            message_id="msg_b",
            thread_id="t_b",
            labels_json="[]",
            captured_at="2026-08-28T00:00:00Z",
        ),
    )

    # Pass only msg_a as the verified subset
    target = write_messages_metadata(root, store, completed_messages=[msg_a])
    lines = [
        line.strip() for line in target.read_text(encoding="utf-8").splitlines() if line.strip()
    ]

    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["gmail_message_id"] == "msg_a"
