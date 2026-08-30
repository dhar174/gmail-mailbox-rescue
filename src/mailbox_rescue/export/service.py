from __future__ import annotations

import http.client
import json
import socket
import sqlite3
import ssl
import threading
import urllib.error
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import google.auth.exceptions
from googleapiclient.errors import Error as GoogleApiError
from googleapiclient.errors import HttpError

from mailbox_rescue.export.eml import write_eml
from mailbox_rescue.export.manifest import write_manifest
from mailbox_rescue.export.mbox import write_mbox
from mailbox_rescue.export.metadata import (
    is_metadata_complete,
    write_portable_metadata,
)
from mailbox_rescue.export.models import (
    ExportFailure,
    ExportPhase,
    ExportProgress,
    ExportResult,
    ExportScope,
    FatalStorageError,
    RetryPolicy,
    ScanError,
)
from mailbox_rescue.export.report import generate_html_report
from mailbox_rescue.export.retry import is_transient_error
from mailbox_rescue.export.verify import verify_archive, verify_completed_message
from mailbox_rescue.gmail.client import GmailExportMessage, GmailLabel
from mailbox_rescue.storage.checkpoint import (
    CheckpointStore,
    CompletedMessage,
    FailedMessage,
    MessageMetadata,
)

if TYPE_CHECKING:
    from mailbox_rescue.gmail.client import GmailClient

_GMAIL_API_EXCEPTIONS = (
    GoogleApiError,
    HttpError,
    google.auth.exceptions.GoogleAuthError,
    urllib.error.URLError,
    http.client.HTTPException,
    ConnectionError,
    TimeoutError,
    socket.error,
    ssl.SSLError,
    ValueError,
    KeyError,
)


def invalidate_derived_archive(output_root: Path) -> None:
    """
    Remove derived archive artifacts when canonical state is mutated during export.
    Preserves canonical messages/*.eml, export.sqlite3, metadata/account.json, and metadata/labels.json.
    """
    derived_files = [
        output_root / "mailbox.mbox",
        output_root / "mailbox.mbox.part",
        output_root / "checksums.sha256",
        output_root / "checksums.sha256.part",
        output_root / "export-report.html",
        output_root / "export-report.html.part",
        output_root / "metadata" / "messages.jsonl",
        output_root / "metadata" / "messages.jsonl.part",
    ]
    for path in derived_files:
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
        except OSError as exc:
            raise FatalStorageError(
                f"Failed to invalidate derived archive artifact '{path}': {exc}"
            ) from exc


class ExportService:
    def __init__(
        self,
        gmail_client: GmailClient,
        checkpoint_store: CheckpointStore,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self._gmail_client = gmail_client
        self._checkpoint_store = checkpoint_store
        self._retry_policy = retry_policy or RetryPolicy()

    def run(
        self,
        output_root: Path,
        scope: ExportScope = ExportScope.ALL_MAIL,
        cancel_event: threading.Event | None = None,
        progress_callback: Callable[[ExportProgress], None] | None = None,
    ) -> ExportResult:
        if cancel_event and cancel_event.is_set():
            if progress_callback:
                progress_callback(ExportProgress(phase=ExportPhase.CANCELLED))
            return ExportResult(
                total_scanned=0,
                completed_this_run=0,
                skipped_completed=0,
                failed=0,
                cancelled=True,
            )

        derived_invalidated = False

        def _mark_mutated() -> None:
            nonlocal derived_invalidated
            if not derived_invalidated:
                invalidate_derived_archive(output_root)
                derived_invalidated = True

        # Phase 1: Scanning
        if progress_callback:
            progress_callback(ExportProgress(phase=ExportPhase.SCANNING))

        scanned_ids = self._scan_message_ids(
            scope=scope,
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )

        if scanned_ids is None:
            # Cancelled during scan
            if progress_callback:
                progress_callback(ExportProgress(phase=ExportPhase.CANCELLED))
            return ExportResult(
                total_scanned=0,
                completed_this_run=0,
                skipped_completed=0,
                failed=0,
                cancelled=True,
            )

        # Fetch label list once per run
        labels_list, labels_map, metadata_warnings, cancelled = self._fetch_labels_with_retry(
            cancel_event=cancel_event,
            progress_callback=progress_callback,
        )
        if cancelled:
            if progress_callback:
                progress_callback(ExportProgress(phase=ExportPhase.CANCELLED))
            return ExportResult(
                total_scanned=len(scanned_ids),
                completed_this_run=0,
                skipped_completed=0,
                failed=0,
                cancelled=True,
            )

        total_scanned = len(scanned_ids)
        if progress_callback:
            progress_callback(
                ExportProgress(
                    phase=ExportPhase.SCAN_COMPLETE,
                    total_messages=total_scanned,
                )
            )
            progress_callback(
                ExportProgress(
                    phase=ExportPhase.EXPORTING,
                    total_messages=total_scanned,
                )
            )

        # Phase 2: Sequential Message Export & Verification
        completed_this_run = 0
        skipped_completed = 0
        failures: list[ExportFailure] = []

        for idx, msg_id in enumerate(scanned_ids, start=1):
            if cancel_event and cancel_event.is_set():
                if progress_callback:
                    progress_callback(
                        ExportProgress(
                            phase=ExportPhase.CANCELLED,
                            total_messages=total_scanned,
                            current_index=idx,
                            completed_this_run=completed_this_run,
                            skipped_completed=skipped_completed,
                            failed_this_run=len(failures),
                        )
                    )
                return ExportResult(
                    total_scanned=total_scanned,
                    completed_this_run=completed_this_run,
                    skipped_completed=skipped_completed,
                    failed=len(failures),
                    cancelled=True,
                    failures=failures,
                    metadata_warnings=metadata_warnings,
                )

            try:
                completed_record = self._checkpoint_store.get_completed(msg_id)
            except sqlite3.Error as db_exc:
                raise FatalStorageError(
                    f"Fatal database error reading checkpoint for message '{msg_id}': {db_exc}"
                ) from db_exc

            if completed_record is not None:
                # Verify existing completed message before skipping
                is_valid_eml, _ = verify_completed_message(output_root, completed_record)

                if is_valid_eml:
                    try:
                        existing_meta = self._checkpoint_store.get_message_metadata(msg_id)
                    except sqlite3.Error as db_exc:
                        raise FatalStorageError(
                            f"Fatal database error reading message metadata for '{msg_id}': {db_exc}"
                        ) from db_exc

                    if is_metadata_complete(existing_meta):
                        # Message is valid and has complete metadata -> Skip
                        skipped_completed += 1
                        if progress_callback:
                            progress_callback(
                                ExportProgress(
                                    phase=ExportPhase.MESSAGE_SKIPPED,
                                    total_messages=total_scanned,
                                    current_index=idx,
                                    message_id=msg_id,
                                    completed_this_run=completed_this_run,
                                    skipped_completed=skipped_completed,
                                    failed_this_run=len(failures),
                                )
                            )
                        continue

                    # Pre-#5 or incomplete metadata: EML is intact but metadata needs backfilling.
                    # Attempt to backfill metadata without rewriting the already-verified EML.
                    export_msg, fetch_error, attempts_made, cancelled = (
                        self._fetch_export_message_with_retry(
                            message_id=msg_id,
                            current_index=idx,
                            total_messages=total_scanned,
                            completed_this_run=completed_this_run,
                            skipped_completed=skipped_completed,
                            failed_this_run=len(failures),
                            cancel_event=cancel_event,
                            progress_callback=progress_callback,
                        )
                    )
                    if cancelled:
                        if progress_callback:
                            progress_callback(
                                ExportProgress(
                                    phase=ExportPhase.CANCELLED,
                                    total_messages=total_scanned,
                                    current_index=idx,
                                    message_id=msg_id,
                                    completed_this_run=completed_this_run,
                                    skipped_completed=skipped_completed,
                                    failed_this_run=len(failures),
                                )
                            )
                        return ExportResult(
                            total_scanned=total_scanned,
                            completed_this_run=completed_this_run,
                            skipped_completed=skipped_completed,
                            failed=len(failures),
                            cancelled=True,
                            failures=failures,
                            metadata_warnings=metadata_warnings,
                        )

                    if fetch_error is not None or export_msg is None:
                        # Backfill fetch failed: record metadata warning and preserve verified EML
                        metadata_warnings.append(
                            f"Could not backfill metadata for message '{msg_id}' ({fetch_error or 'missing'}). "
                            "Message content remains verified."
                        )
                        skipped_completed += 1
                        if progress_callback:
                            progress_callback(
                                ExportProgress(
                                    phase=ExportPhase.MESSAGE_SKIPPED,
                                    total_messages=total_scanned,
                                    current_index=idx,
                                    message_id=msg_id,
                                    completed_this_run=completed_this_run,
                                    skipped_completed=skipped_completed,
                                    failed_this_run=len(failures),
                                )
                            )
                        continue

                    # Persist backfilled metadata
                    _mark_mutated()
                    if export_msg.message_id and export_msg.message_id != msg_id:
                        metadata_warnings.append(
                            f"Inconsistent Gmail API message ID during metadata backfill: requested '{msg_id}', received '{export_msg.message_id}'"
                        )
                    meta_record = self._build_message_metadata(msg_id, export_msg, labels_map)
                    try:
                        self._checkpoint_store.set_message_metadata(meta_record)
                    except sqlite3.Error as db_exc:
                        raise FatalStorageError(
                            f"Fatal database error backfilling metadata for '{msg_id}': {db_exc}"
                        ) from db_exc

                    skipped_completed += 1
                    if progress_callback:
                        progress_callback(
                            ExportProgress(
                                phase=ExportPhase.MESSAGE_SKIPPED,
                                total_messages=total_scanned,
                                current_index=idx,
                                message_id=msg_id,
                                completed_this_run=completed_this_run,
                                skipped_completed=skipped_completed,
                                failed_this_run=len(failures),
                            )
                        )
                    continue

                # If existing EML failed verification (missing, wrong size/hash, unsafe path):
                # Fall through to self-repair by fetching again from Gmail!

            # Fetch single-call export message (id, threadId, labelIds, raw_bytes)
            export_msg, fetch_error, attempts_made, cancelled = (
                self._fetch_export_message_with_retry(
                    message_id=msg_id,
                    current_index=idx,
                    total_messages=total_scanned,
                    completed_this_run=completed_this_run,
                    skipped_completed=skipped_completed,
                    failed_this_run=len(failures),
                    cancel_event=cancel_event,
                    progress_callback=progress_callback,
                )
            )

            if cancelled:
                if progress_callback:
                    progress_callback(
                        ExportProgress(
                            phase=ExportPhase.CANCELLED,
                            total_messages=total_scanned,
                            current_index=idx,
                            message_id=msg_id,
                            completed_this_run=completed_this_run,
                            skipped_completed=skipped_completed,
                            failed_this_run=len(failures),
                        )
                    )
                return ExportResult(
                    total_scanned=total_scanned,
                    completed_this_run=completed_this_run,
                    skipped_completed=skipped_completed,
                    failed=len(failures),
                    cancelled=True,
                    failures=failures,
                    metadata_warnings=metadata_warnings,
                )

            if fetch_error is not None or export_msg is None:
                _mark_mutated()
                error_type = type(fetch_error).__name__
                error_message = str(fetch_error)
                failure = ExportFailure(
                    message_id=msg_id,
                    error_type=error_type,
                    error_message=error_message,
                    attempt_count=attempts_made,
                )
                failures.append(failure)

                try:
                    self._checkpoint_store.mark_failed(
                        FailedMessage(
                            message_id=msg_id,
                            error_type=error_type,
                            error_message=error_message,
                            attempt_count=attempts_made,
                            last_failed_at=datetime.now(UTC).isoformat(),
                        )
                    )
                except sqlite3.Error as db_exc:
                    raise FatalStorageError(
                        f"Failed to record message failure in checkpoint database: {db_exc}"
                    ) from db_exc

                if progress_callback:
                    progress_callback(
                        ExportProgress(
                            phase=ExportPhase.MESSAGE_FAILED,
                            total_messages=total_scanned,
                            current_index=idx,
                            message_id=msg_id,
                            completed_this_run=completed_this_run,
                            skipped_completed=skipped_completed,
                            failed_this_run=len(failures),
                            attempt=attempts_made,
                            error_message=error_message,
                        )
                    )
                continue

            # Write .eml atomically to disk
            _mark_mutated()
            try:
                written = write_eml(output_root, msg_id, export_msg.raw_bytes)
            except OSError as fs_exc:
                raise FatalStorageError(
                    f"Fatal filesystem error writing message '{msg_id}': {fs_exc}"
                ) from fs_exc

            # Checkpoint completed message + message metadata atomically
            relative_path = written.path.relative_to(output_root).as_posix()
            if export_msg.message_id and export_msg.message_id != msg_id:
                metadata_warnings.append(
                    f"Inconsistent Gmail API message ID during export: requested '{msg_id}', received '{export_msg.message_id}'"
                )
            meta_record = self._build_message_metadata(msg_id, export_msg, labels_map)
            try:
                self._checkpoint_store.mark_completed(
                    message=CompletedMessage(
                        message_id=msg_id,
                        relative_path=relative_path,
                        sha256=written.sha256,
                        size_bytes=written.size_bytes,
                    ),
                    message_metadata=meta_record,
                )
            except sqlite3.Error as db_exc:
                raise FatalStorageError(
                    f"Fatal database error checkpointing message '{msg_id}': {db_exc}"
                ) from db_exc

            completed_this_run += 1
            if progress_callback:
                progress_callback(
                    ExportProgress(
                        phase=ExportPhase.MESSAGE_COMPLETED,
                        total_messages=total_scanned,
                        current_index=idx,
                        message_id=msg_id,
                        completed_this_run=completed_this_run,
                        skipped_completed=skipped_completed,
                        failed_this_run=len(failures),
                        attempt=attempts_made,
                    )
                )

        # Phase 3: Archive Verification and Derived Artifacts Generation
        if cancel_event and cancel_event.is_set():
            if progress_callback:
                progress_callback(
                    ExportProgress(
                        phase=ExportPhase.CANCELLED,
                        total_messages=total_scanned,
                        current_index=total_scanned,
                        completed_this_run=completed_this_run,
                        skipped_completed=skipped_completed,
                        failed_this_run=len(failures),
                    )
                )
            return ExportResult(
                total_scanned=total_scanned,
                completed_this_run=completed_this_run,
                skipped_completed=skipped_completed,
                failed=len(failures),
                cancelled=True,
                failures=failures,
                metadata_warnings=metadata_warnings,
            )

        verification_result = verify_archive(
            output_root,
            self._checkpoint_store,
            cancel_event=cancel_event,
        )
        if verification_result.cancelled or (cancel_event and cancel_event.is_set()):
            if progress_callback:
                progress_callback(
                    ExportProgress(
                        phase=ExportPhase.CANCELLED,
                        total_messages=total_scanned,
                        current_index=total_scanned,
                        completed_this_run=completed_this_run,
                        skipped_completed=skipped_completed,
                        failed_this_run=len(failures),
                    )
                )
            return ExportResult(
                total_scanned=total_scanned,
                completed_this_run=completed_this_run,
                skipped_completed=skipped_completed,
                failed=len(failures),
                cancelled=True,
                failures=failures,
                metadata_warnings=metadata_warnings,
            )

        all_completed = self._checkpoint_store.list_completed()
        verified_messages = verification_result.verified_messages

        try:
            write_portable_metadata(
                output_root,
                self._checkpoint_store,
                labels_list,
                completed_messages=verified_messages,
                warnings_collector=metadata_warnings,
            )
            if cancel_event and cancel_event.is_set():
                if progress_callback:
                    progress_callback(
                        ExportProgress(
                            phase=ExportPhase.CANCELLED,
                            total_messages=total_scanned,
                            current_index=total_scanned,
                            completed_this_run=completed_this_run,
                            skipped_completed=skipped_completed,
                            failed_this_run=len(failures),
                        )
                    )
                return ExportResult(
                    total_scanned=total_scanned,
                    completed_this_run=completed_this_run,
                    skipped_completed=skipped_completed,
                    failed=len(failures),
                    cancelled=True,
                    failures=failures,
                    metadata_warnings=metadata_warnings,
                )

            write_manifest(output_root, verified_messages)
            if cancel_event and cancel_event.is_set():
                if progress_callback:
                    progress_callback(
                        ExportProgress(
                            phase=ExportPhase.CANCELLED,
                            total_messages=total_scanned,
                            current_index=total_scanned,
                            completed_this_run=completed_this_run,
                            skipped_completed=skipped_completed,
                            failed_this_run=len(failures),
                        )
                    )
                return ExportResult(
                    total_scanned=total_scanned,
                    completed_this_run=completed_this_run,
                    skipped_completed=skipped_completed,
                    failed=len(failures),
                    cancelled=True,
                    failures=failures,
                    metadata_warnings=metadata_warnings,
                )

            mbox_path = write_mbox(
                output_root,
                verified_messages,
                cancel_event=cancel_event,
            )
            if mbox_path is None or (cancel_event and cancel_event.is_set()):
                if progress_callback:
                    progress_callback(
                        ExportProgress(
                            phase=ExportPhase.CANCELLED,
                            total_messages=total_scanned,
                            current_index=total_scanned,
                            completed_this_run=completed_this_run,
                            skipped_completed=skipped_completed,
                            failed_this_run=len(failures),
                        )
                    )
                return ExportResult(
                    total_scanned=total_scanned,
                    completed_this_run=completed_this_run,
                    skipped_completed=skipped_completed,
                    failed=len(failures),
                    cancelled=True,
                    failures=failures,
                    metadata_warnings=metadata_warnings,
                )

            export_metadata = self._checkpoint_store.get_metadata()

            intermediate_result = ExportResult(
                total_scanned=total_scanned,
                completed_this_run=completed_this_run,
                skipped_completed=skipped_completed,
                failed=len(failures),
                cancelled=False,
                failures=failures,
                archive_verified=verification_result.is_valid,
                verified_files=verification_result.verified_count,
                verification_failures=verification_result.failures,
                metadata_warnings=metadata_warnings,
            )

            report_path = generate_html_report(
                output_root=output_root,
                result=intermediate_result,
                metadata=export_metadata,
                total_canonical_emls=len(all_completed),
            )
        except OSError as exc:
            raise FatalStorageError(f"Fatal error generating archive artifacts: {exc}") from exc

        if progress_callback:
            progress_callback(
                ExportProgress(
                    phase=ExportPhase.COMPLETED,
                    total_messages=total_scanned,
                    current_index=total_scanned,
                    completed_this_run=completed_this_run,
                    skipped_completed=skipped_completed,
                    failed_this_run=len(failures),
                )
            )

        return ExportResult(
            total_scanned=total_scanned,
            completed_this_run=completed_this_run,
            skipped_completed=skipped_completed,
            failed=len(failures),
            cancelled=False,
            failures=failures,
            archive_verified=verification_result.is_valid,
            verified_files=verification_result.verified_count,
            verification_failures=verification_result.failures,
            metadata_warnings=metadata_warnings,
            report_path=report_path,
        )

    def _build_message_metadata(
        self,
        message_id: str,
        export_msg: GmailExportMessage,
        labels_map: dict[str, GmailLabel] | None,
    ) -> MessageMetadata:
        labels_data: list[dict[str, str]] = []
        for lid in export_msg.label_ids:
            if labels_map and lid in labels_map:
                labels_data.append({"id": lid, "name": labels_map[lid].name})
            else:
                labels_data.append({"id": lid})

        return MessageMetadata(
            message_id=message_id,
            thread_id=export_msg.thread_id,
            labels_json=json.dumps(labels_data, ensure_ascii=False),
            captured_at=datetime.now(UTC).isoformat(),
        )

    def _fetch_labels_with_retry(
        self,
        cancel_event: threading.Event | None,
        progress_callback: Callable[[ExportProgress], None] | None,
    ) -> tuple[list[GmailLabel] | None, dict[str, GmailLabel] | None, list[str], bool]:
        attempt = 1
        while True:
            if cancel_event and cancel_event.is_set():
                return None, None, [], True

            try:
                labels = self._gmail_client.list_labels()
                labels_map = {lbl.id: lbl for lbl in labels}
                return labels, labels_map, [], False
            except _GMAIL_API_EXCEPTIONS as exc:
                if is_transient_error(exc) and attempt < self._retry_policy.max_attempts:
                    delay = self._retry_policy.compute_delay(attempt)
                    if progress_callback:
                        progress_callback(
                            ExportProgress(
                                phase=ExportPhase.RETRYING,
                                attempt=attempt + 1,
                                error_message=str(exc),
                            )
                        )
                    if cancel_event and cancel_event.is_set():
                        return None, None, [], True
                    self._retry_policy.sleep_fn(delay)
                    if cancel_event and cancel_event.is_set():
                        return None, None, [], True
                    attempt += 1
                else:
                    warning = (
                        f"Failed to fetch Gmail label names ({exc}). "
                        "Label IDs will be preserved without names."
                    )
                    return None, None, [warning], False

    def _scan_message_ids(
        self,
        scope: ExportScope,
        cancel_event: threading.Event | None,
        progress_callback: Callable[[ExportProgress], None] | None,
    ) -> list[str] | None:
        attempt = 1
        label_ids = ["INBOX"] if scope == ExportScope.INBOX else None

        while True:
            if cancel_event and cancel_event.is_set():
                return None

            try:
                ids: list[str] = []
                for msg_id in self._gmail_client.iter_message_ids(
                    label_ids=label_ids,
                    include_spam_trash=False,
                ):
                    if cancel_event and cancel_event.is_set():
                        return None
                    ids.append(msg_id)
                return ids
            except _GMAIL_API_EXCEPTIONS as exc:
                if is_transient_error(exc) and attempt < self._retry_policy.max_attempts:
                    delay = self._retry_policy.compute_delay(attempt)
                    if progress_callback:
                        progress_callback(
                            ExportProgress(
                                phase=ExportPhase.RETRYING,
                                attempt=attempt + 1,
                                error_message=str(exc),
                            )
                        )
                    if cancel_event and cancel_event.is_set():
                        return None
                    self._retry_policy.sleep_fn(delay)
                    if cancel_event and cancel_event.is_set():
                        return None
                    attempt += 1
                else:
                    raise ScanError(f"Scanning Gmail mailbox failed: {exc}") from exc

    def _fetch_export_message_with_retry(
        self,
        message_id: str,
        current_index: int,
        total_messages: int,
        completed_this_run: int,
        skipped_completed: int,
        failed_this_run: int,
        cancel_event: threading.Event | None,
        progress_callback: Callable[[ExportProgress], None] | None,
    ) -> tuple[GmailExportMessage | None, Exception | None, int, bool]:
        attempt = 1
        while True:
            if cancel_event and cancel_event.is_set():
                return None, None, attempt - 1, True

            try:
                export_msg = self._gmail_client.get_export_message(message_id)
                return export_msg, None, attempt, False
            except _GMAIL_API_EXCEPTIONS as exc:
                if is_transient_error(exc) and attempt < self._retry_policy.max_attempts:
                    delay = self._retry_policy.compute_delay(attempt)
                    if progress_callback:
                        progress_callback(
                            ExportProgress(
                                phase=ExportPhase.RETRYING,
                                total_messages=total_messages,
                                current_index=current_index,
                                message_id=message_id,
                                completed_this_run=completed_this_run,
                                skipped_completed=skipped_completed,
                                failed_this_run=failed_this_run,
                                attempt=attempt + 1,
                                error_message=str(exc),
                            )
                        )
                    if cancel_event and cancel_event.is_set():
                        return None, None, attempt, True
                    self._retry_policy.sleep_fn(delay)
                    if cancel_event and cancel_event.is_set():
                        return None, None, attempt, True
                    attempt += 1
                else:
                    return None, exc, attempt, False
