from pathlib import Path

import pytest

from mailbox_rescue.export.eml import safe_message_id, write_eml


def test_safe_message_id_replaces_path_characters() -> None:
    assert safe_message_id("abc/../123") == "abc_.._123"


def test_safe_message_id_rejects_empty_value() -> None:
    with pytest.raises(ValueError):
        safe_message_id("///")


def test_write_eml_preserves_original_bytes(tmp_path: Path) -> None:
    raw = b"From: test@example.com\r\nSubject: hello\r\n\r\nBody\r\n"

    result = write_eml(tmp_path, "abc123", raw)

    assert result.path.read_bytes() == raw
    assert result.size_bytes == len(raw)
    assert len(result.sha256) == 64
    assert not result.path.with_suffix(".eml.part").exists()
