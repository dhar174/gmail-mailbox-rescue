from __future__ import annotations

import email
import email.errors
import email.policy
import hashlib
import mailbox
from pathlib import Path

from mailbox_rescue.export.models import FatalStorageError
from mailbox_rescue.export.verify import resolve_safe_relative_path
from mailbox_rescue.storage.checkpoint import CompletedMessage


def write_mbox(
    output_root: Path,
    completed_messages: list[CompletedMessage],
) -> Path:
    """
    Regenerate mailbox.mbox from canonical EML files present on disk.
    Writes atomically via temporary sibling .part file, strictly revalidating every input file.
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
                    raise FatalStorageError(
                        f"MBOX generation failed: message file '{completed.relative_path}' is missing or unsafe"
                    )

                try:
                    raw_bytes = resolved.read_bytes()
                except OSError as err:
                    raise FatalStorageError(
                        f"MBOX generation failed: could not read message file '{completed.relative_path}': {err}"
                    ) from err

                if len(raw_bytes) != completed.size_bytes:
                    raise FatalStorageError(
                        f"MBOX generation failed: message '{completed.message_id}' size mismatch "
                        f"(expected {completed.size_bytes}, found {len(raw_bytes)})"
                    )

                digest = hashlib.sha256(raw_bytes).hexdigest()
                if digest.lower() != completed.sha256.lower():
                    raise FatalStorageError(
                        f"MBOX generation failed: message '{completed.message_id}' SHA-256 mismatch "
                        f"(expected {completed.sha256}, calculated {digest})"
                    )

                try:
                    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)
                except (email.errors.MessageError, ValueError, LookupError):
                    try:
                        msg = email.message_from_bytes(raw_bytes, policy=email.policy.compat32)
                    except Exception as parse_err:
                        raise FatalStorageError(
                            f"MBOX generation failed: could not parse message '{completed.message_id}': {parse_err}"
                        ) from parse_err
                mbox.add(msg)
            mbox.flush()
        finally:
            mbox.unlock()
    except Exception:
        mbox.close()
        if part_file.exists():
            try:
                part_file.unlink()
            except OSError:
                pass
        raise
    else:
        mbox.close()

    part_file.replace(mbox_file)
    return mbox_file
