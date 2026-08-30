from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from mailbox_rescue.gmail.client import GmailLabel
from mailbox_rescue.storage.checkpoint import (
    CheckpointStore,
    CompletedMessage,
    MessageMetadata,
)


def is_metadata_complete(meta: MessageMetadata | None) -> bool:
    """
    Verify that a MessageMetadata record satisfies required portable archive integrity.
    Requires:
    - non-None record
    - non-empty thread_id string
    - valid JSON labels string parsing into a list of dicts with non-empty 'id'
    """
    if meta is None:
        return False
    if not isinstance(meta.thread_id, str) or not meta.thread_id.strip():
        return False
    if not isinstance(meta.labels_json, str):
        return False
    try:
        parsed = json.loads(meta.labels_json)
    except (json.JSONDecodeError, TypeError):
        return False
    if not isinstance(parsed, list):
        return False
    for item in parsed:
        if not isinstance(item, dict):
            return False
        label_id = item.get("id")
        if not isinstance(label_id, str) or not label_id.strip():
            return False
    return True


def write_account_metadata(
    output_root: Path,
    checkpoint_store: CheckpointStore,
) -> Path:
    meta_dir = output_root / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    target = meta_dir / "account.json"
    part = meta_dir / "account.json.part"

    metadata = checkpoint_store.get_metadata()
    now_iso = datetime.now(UTC).isoformat()

    data = {
        "account_email": metadata.account_email if metadata else None,
        "export_scope": metadata.export_scope if metadata else None,
        "archive_created_at": metadata.created_at if metadata else now_iso,
        "archive_updated_at": metadata.last_updated_at if metadata else now_iso,
        "metadata_generated_at": now_iso,
    }

    part.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    part.replace(target)
    return target


def write_labels_metadata(
    output_root: Path,
    labels: list[GmailLabel] | None,
) -> Path | None:
    meta_dir = output_root / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    target = meta_dir / "labels.json"
    part = meta_dir / "labels.json.part"

    if labels is None:
        # Do not overwrite existing valid snapshot on lookup failure
        if target.is_file():
            return target
        return None

    sorted_labels = sorted(labels, key=lambda lbl: lbl.id)
    data = [
        {
            "id": label.id,
            "name": label.name,
            **({"type": label.type} if label.type is not None else {}),
        }
        for label in sorted_labels
    ]

    part.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    part.replace(target)
    return target


def write_messages_metadata(
    output_root: Path,
    checkpoint_store: CheckpointStore,
    completed_messages: list[CompletedMessage] | None = None,
    warnings_collector: list[str] | None = None,
) -> Path:
    meta_dir = output_root / "metadata"
    meta_dir.mkdir(parents=True, exist_ok=True)
    target = meta_dir / "messages.jsonl"
    part = meta_dir / "messages.jsonl.part"

    if completed_messages is None:
        messages_to_write = checkpoint_store.list_completed()
    else:
        messages_to_write = completed_messages

    all_metadata = checkpoint_store.get_all_message_metadata()

    # Sort deterministically by message_id
    sorted_messages = sorted(messages_to_write, key=lambda m: m.message_id)

    lines: list[str] = []

    for msg in sorted_messages:
        meta = all_metadata.get(msg.message_id)
        if not is_metadata_complete(meta):
            if warnings_collector is not None:
                warnings_collector.append(
                    f"Message '{msg.message_id}' excluded from metadata/messages.jsonl due to incomplete metadata."
                )
            continue

        parsed: list[dict[str, str]] = json.loads(meta.labels_json)  # type: ignore[union-attr]
        rel_posix = Path(msg.relative_path).as_posix()
        record = {
            "gmail_message_id": msg.message_id,
            "thread_id": meta.thread_id,  # type: ignore[union-attr]
            "labels": parsed,
            "relative_path": rel_posix,
            "sha256": msg.sha256.lower(),
            "size_bytes": msg.size_bytes,
        }
        lines.append(json.dumps(record, ensure_ascii=False) + "\n")

    part.write_text("".join(lines), encoding="utf-8")
    part.replace(target)
    return target


def write_portable_metadata(
    output_root: Path,
    checkpoint_store: CheckpointStore,
    labels: list[GmailLabel] | None,
    completed_messages: list[CompletedMessage] | None = None,
    warnings_collector: list[str] | None = None,
) -> None:
    """
    Generate all portable metadata files in metadata/ directory atomically.
    """
    write_account_metadata(output_root, checkpoint_store)
    write_labels_metadata(output_root, labels)
    write_messages_metadata(
        output_root,
        checkpoint_store,
        completed_messages=completed_messages,
        warnings_collector=warnings_collector,
    )
