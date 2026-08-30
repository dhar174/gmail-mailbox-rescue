from __future__ import annotations

import email
import email.policy
import mailbox
from pathlib import Path

from mailbox_rescue.export.verify import resolve_safe_relative_path
from mailbox_rescue.storage.checkpoint import CompletedMessage


def write_mbox(
    output_root: Path,
    completed_messages: list[CompletedMessage],
) -> Path:
    """
    Regenerate mailbox.mbox from canonical EML files present on disk.
    Writes atomically via temporary sibling .part file.
    """
    mbox_file = output_root / "mailbox.mbox"
    part_file = output_root / "mailbox.mbox.part"

    if part_file.exists():
        try:
            part_file.unlink()
        except OSError:
            pass

    # Sort deterministically by message_id
    sorted_messages = sorted(completed_messages, key=lambda m: m.message_id)

    mbox = mailbox.mbox(str(part_file))
    try:
        mbox.lock()
        try:
            for completed in sorted_messages:
                resolved = resolve_safe_relative_path(output_root, completed.relative_path)
                if resolved is None or not resolved.is_file():
                    continue

                raw_bytes = resolved.read_bytes()
                try:
                    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
                except Exception:
                    msg = email.message_from_bytes(raw_bytes, policy=email.policy.compat32)
                mbox.add(msg)
            mbox.flush()
        finally:
            mbox.unlock()
    finally:
        mbox.close()

    part_file.replace(mbox_file)
    return mbox_file
