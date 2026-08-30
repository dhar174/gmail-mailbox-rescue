import hashlib
import mailbox
from pathlib import Path

from mailbox_rescue.export.mbox import write_mbox
from mailbox_rescue.storage.checkpoint import CompletedMessage


def test_mbox_generation_and_reopen_with_mailbox_module(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    messages_dir = root / "messages"
    messages_dir.mkdir(parents=True)

    eml1_bytes = (
        b"From: alice@example.com\r\n"
        b"To: bob@example.com\r\n"
        b"Subject: Test Message 1\r\n"
        b"\r\n"
        b"Hello Bob,\r\n"
        b"This is the first test message body.\r\n"
    )
    eml2_bytes = (
        b"From: charlie@example.com\r\n"
        b"To: dave@example.com\r\n"
        b"Subject: Test Message 2\r\n"
        b"\r\n"
        b"Hello Dave,\r\n"
        b"This is the second test message body.\r\n"
    )

    (messages_dir / "msg1.eml").write_bytes(eml1_bytes)
    (messages_dir / "msg2.eml").write_bytes(eml2_bytes)

    m1 = CompletedMessage(
        message_id="msg1",
        relative_path="messages/msg1.eml",
        sha256=hashlib.sha256(eml1_bytes).hexdigest(),
        size_bytes=len(eml1_bytes),
    )
    m2 = CompletedMessage(
        message_id="msg2",
        relative_path="messages/msg2.eml",
        sha256=hashlib.sha256(eml2_bytes).hexdigest(),
        size_bytes=len(eml2_bytes),
    )

    mbox_path = write_mbox(root, [m1, m2])
    assert mbox_path.exists()
    assert not (root / "mailbox.mbox.part").exists()

    # Re-open with Python's mailbox module
    read_mbox = mailbox.mbox(str(mbox_path))
    try:
        assert len(read_mbox) == 2
        subjects = [msg["subject"] for msg in read_mbox]
        assert "Test Message 1" in subjects
        assert "Test Message 2" in subjects
    finally:
        read_mbox.close()

    # Repeat generation does not duplicate messages (remains 2, not 4)
    write_mbox(root, [m1, m2])
    read_mbox2 = mailbox.mbox(str(mbox_path))
    try:
        assert len(read_mbox2) == 2
    finally:
        read_mbox2.close()


def test_mbox_escapes_from_lines_in_body(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    messages_dir = root / "messages"
    messages_dir.mkdir(parents=True)

    eml_with_from_line = (
        b"From: sender@example.com\r\n"
        b"To: recipient@example.com\r\n"
        b"Subject: From Line in Body Test\r\n"
        b"\r\n"
        b"Header line\r\n"
        b"From this line begins with From\r\n"
        b"Trailing line\r\n"
    )

    (messages_dir / "msg_from.eml").write_bytes(eml_with_from_line)

    m = CompletedMessage(
        message_id="msg_from",
        relative_path="messages/msg_from.eml",
        sha256=hashlib.sha256(eml_with_from_line).hexdigest(),
        size_bytes=len(eml_with_from_line),
    )

    mbox_path = write_mbox(root, [m])
    read_mbox = mailbox.mbox(str(mbox_path))
    try:
        assert len(read_mbox) == 1
        msg = read_mbox[0]
        assert msg["subject"] == "From Line in Body Test"
        payload = str(msg.get_payload())
        assert "From this line begins with From" in payload
    finally:
        read_mbox.close()


def test_mbox_resilient_to_malformed_headers(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    messages_dir = root / "messages"
    messages_dir.mkdir(parents=True)

    malformed_eml = b"Subject: \xff\xfe non-ascii raw header\r\n\r\nRaw body text\r\n"
    (messages_dir / "bad_hdr.eml").write_bytes(malformed_eml)

    m = CompletedMessage(
        message_id="bad_hdr",
        relative_path="messages/bad_hdr.eml",
        sha256=hashlib.sha256(malformed_eml).hexdigest(),
        size_bytes=len(malformed_eml),
    )

    mbox_path = write_mbox(root, [m])
    read_mbox = mailbox.mbox(str(mbox_path))
    try:
        assert len(read_mbox) == 1
    finally:
        read_mbox.close()
