from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from mailbox_rescue.export.verify import resolve_safe_relative_path
from mailbox_rescue.storage.checkpoint import CompletedMessage


@dataclass(frozen=True, slots=True)
class ManifestEntryFailure:
    relative_path: str
    expected_sha256: str
    actual_sha256: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class ManifestVerificationResult:
    total_entries: int
    verified_entries: int
    failures: list[ManifestEntryFailure] = field(default_factory=list)
    is_valid: bool = True


def write_manifest(
    output_root: Path,
    completed_messages: list[CompletedMessage],
) -> Path:
    """
    Generate checksums.sha256 from verified completed messages in deterministic order.
    Writes atomically via temporary sibling .part file.
    """
    manifest_file = output_root / "checksums.sha256"
    part_file = output_root / "checksums.sha256.part"

    # Sort deterministically by relative_path
    sorted_messages = sorted(completed_messages, key=lambda m: m.relative_path)
    lines: list[str] = []
    for msg in sorted_messages:
        # Standard sha256sum format: "<hash>  <relative_path>\n"
        # Ensure forward slashes for cross-platform portability
        rel_posix = Path(msg.relative_path).as_posix()
        # Refuse traversal/control-character paths so the manifest cannot reference files outside output_root
        if "\n" in rel_posix or "\r" in rel_posix or resolve_safe_relative_path(output_root, rel_posix) is None:
            continue
        lines.append(f"{msg.sha256.lower()}  {rel_posix}\n")

    part_file.write_text("".join(lines), encoding="utf-8")
    part_file.replace(manifest_file)
    return manifest_file


def verify_manifest(
    output_root: Path,
    manifest_path: Path | None = None,
) -> ManifestVerificationResult:
    """
    Verify recorded checksums.sha256 against actual files in output_root.
    """
    target = manifest_path or (output_root / "checksums.sha256")
    if not target.exists() or not target.is_file():
        return ManifestVerificationResult(
            total_entries=0,
            verified_entries=0,
            failures=[
                ManifestEntryFailure(
                    relative_path=str(target),
                    expected_sha256="",
                    actual_sha256=None,
                    reason="manifest_file_missing",
                )
            ],
            is_valid=False,
        )

    content = target.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines() if line.strip()]

    total_entries = len(lines)
    verified_entries = 0
    failures: list[ManifestEntryFailure] = []

    for line in lines:
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            failures.append(
                ManifestEntryFailure(
                    relative_path=line,
                    expected_sha256="",
                    actual_sha256=None,
                    reason="malformed_manifest_line",
                )
            )
            continue

        expected_hash, rel_path = parts[0].strip().lower(), parts[1].strip()
        resolved = resolve_safe_relative_path(output_root, rel_path)
        if resolved is None:
            failures.append(
                ManifestEntryFailure(
                    relative_path=rel_path,
                    expected_sha256=expected_hash,
                    actual_sha256=None,
                    reason="unsafe_relative_path",
                )
            )
            continue

        if not resolved.exists() or not resolved.is_file():
            failures.append(
                ManifestEntryFailure(
                    relative_path=rel_path,
                    expected_sha256=expected_hash,
                    actual_sha256=None,
                    reason="file_missing_or_not_regular",
                )
            )
            continue

        try:
            hasher = hashlib.sha256()
            with resolved.open("rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    hasher.update(chunk)
            actual_digest = hasher.hexdigest().lower()
            if actual_digest != expected_hash:
                failures.append(
                    ManifestEntryFailure(
                        relative_path=rel_path,
                        expected_sha256=expected_hash,
                        actual_sha256=actual_digest,
                        reason="sha256_mismatch",
                    )
                )
            else:
                verified_entries += 1
        except OSError as exc:
            failures.append(
                ManifestEntryFailure(
                    relative_path=rel_path,
                    expected_sha256=expected_hash,
                    actual_sha256=None,
                    reason=f"filesystem_error: {exc}",
                )
            )

    return ManifestVerificationResult(
        total_entries=total_entries,
        verified_entries=verified_entries,
        failures=failures,
        is_valid=(len(failures) == 0),
    )
