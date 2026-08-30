import hashlib
from pathlib import Path

from mailbox_rescue.export.verify import (
    resolve_safe_relative_path,
    verify_archive,
    verify_completed_message,
)
from mailbox_rescue.storage.checkpoint import CheckpointStore, CompletedMessage


def test_resolve_safe_relative_path_valid_and_traversal(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()

    # Valid relative paths
    res = resolve_safe_relative_path(root, "messages/msg1.eml")
    assert res is not None
    assert res == (root / "messages" / "msg1.eml").resolve()

    res_nested = resolve_safe_relative_path(root, "subdir/nested/file.txt")
    assert res_nested is not None
    assert res_nested == (root / "subdir" / "nested" / "file.txt").resolve()

    # Traversal attempts outside root
    assert resolve_safe_relative_path(root, "../outside.txt") is None
    assert resolve_safe_relative_path(root, "../../etc/passwd") is None
    assert resolve_safe_relative_path(root, "messages/../../outside.txt") is None

    # POSIX absolute path attempts
    assert resolve_safe_relative_path(root, "/absolute/messages/a.eml") is None
    assert resolve_safe_relative_path(root, "/messages/a.eml") is None

    # Windows drive and UNC absolute path attempts (must be rejected on all platforms)
    assert resolve_safe_relative_path(root, r"C:\archive\messages\a.eml") is None
    assert resolve_safe_relative_path(root, "C:/archive/messages/a.eml") is None
    assert resolve_safe_relative_path(root, r"\\server\share\a.eml") is None
    assert resolve_safe_relative_path(root, "//server/share/a.eml") is None
    assert resolve_safe_relative_path(root, "C:messages/a.eml") is None
    assert resolve_safe_relative_path(root, r"\messages\a.eml") is None

    # Control characters
    assert resolve_safe_relative_path(root, "messages/bad\nname.eml") is None
    assert resolve_safe_relative_path(root, "messages/bad\rname.eml") is None
    assert resolve_safe_relative_path(root, "messages/bad\0name.eml") is None

    # Absolute path attempts with real outside dir
    outside_dir = tmp_path / "other"
    outside_dir.mkdir()
    assert resolve_safe_relative_path(root, str(outside_dir / "file.txt")) is None

    # Empty / whitespace
    assert resolve_safe_relative_path(root, "") is None
    assert resolve_safe_relative_path(root, "   ") is None


def test_verify_completed_message_checks_integrity(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    messages_dir = root / "messages"
    messages_dir.mkdir(parents=True)

    raw_bytes = b"From: sender@example.com\r\nSubject: Test\r\n\r\nHello World\r\n"
    digest = hashlib.sha256(raw_bytes).hexdigest()
    eml_file = messages_dir / "msg_1.eml"
    eml_file.write_bytes(raw_bytes)

    valid_record = CompletedMessage(
        message_id="msg_1",
        relative_path="messages/msg_1.eml",
        sha256=digest,
        size_bytes=len(raw_bytes),
    )

    # 1. Valid record
    valid, reason = verify_completed_message(root, valid_record)
    assert valid is True
    assert reason is None

    # 2. Missing file
    missing_record = CompletedMessage(
        message_id="msg_missing",
        relative_path="messages/msg_missing.eml",
        sha256=digest,
        size_bytes=len(raw_bytes),
    )
    valid, reason = verify_completed_message(root, missing_record)
    assert valid is False
    assert reason == "file_missing_or_not_regular"

    # 3. Directory instead of regular file
    dir_record = CompletedMessage(
        message_id="msg_dir",
        relative_path="messages",
        sha256=digest,
        size_bytes=len(raw_bytes),
    )
    valid, reason = verify_completed_message(root, dir_record)
    assert valid is False
    assert reason == "file_missing_or_not_regular"

    # 4. Size mismatch
    wrong_size_record = CompletedMessage(
        message_id="msg_1",
        relative_path="messages/msg_1.eml",
        sha256=digest,
        size_bytes=len(raw_bytes) + 10,
    )
    valid, reason = verify_completed_message(root, wrong_size_record)
    assert valid is False
    assert reason is not None
    assert "size_mismatch" in reason

    # 5. SHA-256 mismatch (tampered content)
    wrong_hash_record = CompletedMessage(
        message_id="msg_1",
        relative_path="messages/msg_1.eml",
        sha256="0" * 64,
        size_bytes=len(raw_bytes),
    )
    valid, reason = verify_completed_message(root, wrong_hash_record)
    assert valid is False
    assert reason is not None
    assert "sha256_mismatch" in reason

    # 6. Unsafe path in record
    unsafe_record = CompletedMessage(
        message_id="msg_unsafe",
        relative_path="../outside.eml",
        sha256=digest,
        size_bytes=len(raw_bytes),
    )
    valid, reason = verify_completed_message(root, unsafe_record)
    assert valid is False
    assert reason == "unsafe_relative_path"


def test_verify_archive_full_pass(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    messages_dir = root / "messages"
    messages_dir.mkdir(parents=True)
    store = CheckpointStore(root / "export.sqlite3")

    # Empty archive verifies cleanly
    empty_result = verify_archive(root, store)
    assert empty_result.is_valid is True
    assert empty_result.verified_count == 0
    assert empty_result.failures == []

    # Write 2 valid messages
    payload1 = b"Message 1 content"
    payload2 = b"Message 2 content"
    (messages_dir / "m1.eml").write_bytes(payload1)
    (messages_dir / "m2.eml").write_bytes(payload2)

    store.mark_completed(
        CompletedMessage(
            message_id="m1",
            relative_path="messages/m1.eml",
            sha256=hashlib.sha256(payload1).hexdigest(),
            size_bytes=len(payload1),
        )
    )
    store.mark_completed(
        CompletedMessage(
            message_id="m2",
            relative_path="messages/m2.eml",
            sha256=hashlib.sha256(payload2).hexdigest(),
            size_bytes=len(payload2),
        )
    )

    valid_result = verify_archive(root, store)
    assert valid_result.is_valid is True
    assert valid_result.verified_count == 2
    assert valid_result.failures == []

    # Tamper with m2.eml with same length content
    (messages_dir / "m2.eml").write_bytes(b"Tampered content!")  # 17 bytes

    tampered_result = verify_archive(root, store)
    assert tampered_result.is_valid is False
    assert tampered_result.verified_count == 1
    assert len(tampered_result.verified_messages) == 1
    assert tampered_result.verified_messages[0].message_id == "m1"
    assert len(tampered_result.failures) == 1
    assert tampered_result.failures[0].message_id == "m2"
    assert "sha256_mismatch" in tampered_result.failures[0].reason
