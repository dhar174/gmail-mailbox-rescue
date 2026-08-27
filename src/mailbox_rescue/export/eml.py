from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True, slots=True)
class WrittenMessage:
    message_id: str
    path: Path
    sha256: str
    size_bytes: int


def safe_message_id(message_id: str) -> str:
    cleaned = _SAFE_ID.sub("_", message_id).strip("._")
    if not cleaned:
        raise ValueError("message_id does not contain any safe filename characters")
    return cleaned


def write_eml(output_root: Path, message_id: str, raw_message: bytes) -> WrittenMessage:
    messages_dir = output_root / "messages"
    messages_dir.mkdir(parents=True, exist_ok=True)

    filename = f"{safe_message_id(message_id)}.eml"
    destination = messages_dir / filename
    temporary = destination.with_suffix(".eml.part")

    temporary.write_bytes(raw_message)
    temporary.replace(destination)

    digest = hashlib.sha256(raw_message).hexdigest()
    return WrittenMessage(
        message_id=message_id,
        path=destination,
        sha256=digest,
        size_bytes=len(raw_message),
    )
