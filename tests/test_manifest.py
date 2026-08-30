import hashlib
from pathlib import Path

import pytest

from mailbox_rescue.export.manifest import verify_manifest, write_manifest
from mailbox_rescue.storage.checkpoint import CompletedMessage


def test_write_manifest_deterministic_and_atomic_replace(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    messages_dir = root / "messages"
    messages_dir.mkdir(parents=True)

    p1 = b"Message one"
    p2 = b"Message two"
    (messages_dir / "b.eml").write_bytes(p1)
    (messages_dir / "a.eml").write_bytes(p2)

    h1 = hashlib.sha256(p1).hexdigest()
    h2 = hashlib.sha256(p2).hexdigest()

    m1 = CompletedMessage(
        message_id="b",
        relative_path="messages/b.eml",
        sha256=h1,
        size_bytes=len(p1),
    )
    m2 = CompletedMessage(
        message_id="a",
        relative_path="messages/a.eml",
        sha256=h2,
        size_bytes=len(p2),
    )

    # Pass in reverse order; write_manifest should sort deterministically
    manifest_path = write_manifest(root, [m1, m2])
    assert manifest_path.exists()
    assert not (root / "checksums.sha256.part").exists()

    content = manifest_path.read_text(encoding="utf-8")
    expected_lines = [
        f"{h2}  messages/a.eml",
        f"{h1}  messages/b.eml",
    ]
    assert content == "\n".join(expected_lines) + "\n"

    # Re-writing replaces rather than appends
    write_manifest(root, [m2])
    content_updated = manifest_path.read_text(encoding="utf-8")
    assert content_updated == f"{h2}  messages/a.eml\n"


def test_verify_manifest_integrity_and_tamper_detection(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    messages_dir = root / "messages"
    messages_dir.mkdir(parents=True)

    p1 = b"Payload 1"
    p2 = b"Payload 2"
    (messages_dir / "m1.eml").write_bytes(p1)
    (messages_dir / "m2.eml").write_bytes(p2)

    h1 = hashlib.sha256(p1).hexdigest()
    h2 = hashlib.sha256(p2).hexdigest()

    m1 = CompletedMessage(message_id="m1", relative_path="messages/m1.eml", sha256=h1, size_bytes=len(p1))
    m2 = CompletedMessage(message_id="m2", relative_path="messages/m2.eml", sha256=h2, size_bytes=len(p2))

    write_manifest(root, [m1, m2])

    # 1. Valid manifest verification
    result = verify_manifest(root)
    assert result.is_valid is True
    assert result.total_entries == 2
    assert result.verified_entries == 2
    assert result.failures == []

    # 2. Tampered file
    (messages_dir / "m1.eml").write_bytes(b"Payload X")
    tampered_res = verify_manifest(root)
    assert tampered_res.is_valid is False
    assert tampered_res.verified_entries == 1
    assert len(tampered_res.failures) == 1
    assert tampered_res.failures[0].reason == "sha256_mismatch"

    # 3. Missing file
    (messages_dir / "m2.eml").unlink()
    missing_res = verify_manifest(root)
    assert missing_res.is_valid is False
    assert any(f.reason == "file_missing_or_not_regular" for f in missing_res.failures)

    # 4. Missing manifest file
    (root / "checksums.sha256").unlink()
    no_manifest_res = verify_manifest(root)
    assert no_manifest_res.is_valid is False
    assert no_manifest_res.failures[0].reason == "manifest_file_missing"


def test_write_manifest_rejects_unsafe_paths(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()

    unsafe_cases = [
        "../outside.eml",
        "../../secret.txt",
        "messages/bad\nname.eml",
        "messages/bad\rname.eml",
    ]

    for bad_path in unsafe_cases:
        msg = CompletedMessage(
            message_id="bad_msg",
            relative_path=bad_path,
            sha256="0" * 64,
            size_bytes=100,
        )
        with pytest.raises(ValueError, match="Unsafe relative path"):
            write_manifest(root, [msg])
