from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from mailbox_rescue.export.models import VerificationFailure
from mailbox_rescue.storage.checkpoint import CheckpointStore, CompletedMessage


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verified_messages: list[CompletedMessage] = field(default_factory=list)
    failures: list[VerificationFailure] = field(default_factory=list)

    @property
    def verified_count(self) -> int:
        return len(self.verified_messages)

    @property
    def is_valid(self) -> bool:
        return not self.failures


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Calculate SHA-256 hex digest of a file by streaming chunks."""
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def resolve_safe_relative_path(output_root: Path, relative_path: str) -> Path | None:
    """
    Resolve a recorded relative path against output_root.
    Returns None if the path attempts to traverse outside output_root or is invalid.
    """
    if not relative_path or not relative_path.strip():
        return None
    try:
        resolved_root = output_root.resolve()
        candidate = (output_root / relative_path).resolve()
        if not candidate.is_relative_to(resolved_root):
            return None
        return candidate
    except (ValueError, RuntimeError, OSError):
        return None


def verify_completed_message(
    output_root: Path,
    completed: CompletedMessage,
) -> tuple[bool, str | None]:
    """
    Verify that the recorded CompletedMessage corresponds to an existing, intact regular file.
    Returns (True, None) if intact, or (False, reason) if corrupted, missing, or unsafe.
    """
    resolved = resolve_safe_relative_path(output_root, completed.relative_path)
    if resolved is None:
        return False, "unsafe_relative_path"

    try:
        if not resolved.exists() or not resolved.is_file():
            return False, "file_missing_or_not_regular"

        stat_result = resolved.stat()
        if stat_result.st_size != completed.size_bytes:
            return (
                False,
                f"size_mismatch: expected {completed.size_bytes} bytes, found {stat_result.st_size} bytes",
            )

        actual_digest = sha256_file(resolved)
        if actual_digest.lower() != completed.sha256.lower():
            return (
                False,
                f"sha256_mismatch: expected {completed.sha256}, calculated {actual_digest}",
            )
    except OSError as exc:
        return False, f"filesystem_error: {exc}"

    return True, None


def verify_archive(
    output_root: Path,
    checkpoint_store: CheckpointStore,
) -> VerificationResult:
    """
    Perform a complete verification pass across all completed messages in checkpoint_store.
    """
    completed_messages = checkpoint_store.list_completed()
    verified_messages: list[CompletedMessage] = []
    failures: list[VerificationFailure] = []

    for completed in completed_messages:
        valid, reason = verify_completed_message(output_root, completed)
        if valid:
            verified_messages.append(completed)
        else:
            failures.append(
                VerificationFailure(
                    message_id=completed.message_id,
                    relative_path=completed.relative_path,
                    reason=reason or "unknown_verification_failure",
                )
            )

    return VerificationResult(
        verified_messages=verified_messages,
        failures=failures,
    )
