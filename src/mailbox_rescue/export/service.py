from __future__ import annotations

import http.client
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
from mailbox_rescue.export.retry import is_transient_error
from mailbox_rescue.storage.checkpoint import CheckpointStore, CompletedMessage, FailedMessage

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

        # Phase 2: Sequential Export
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
                )

            try:
                already_completed = self._checkpoint_store.is_completed(msg_id)
            except sqlite3.Error as db_exc:
                raise FatalStorageError(
                    f"Fatal database error reading checkpoint for message '{msg_id}': {db_exc}"
                ) from db_exc
            if already_completed:
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

            # Fetch raw RFC 822 bytes with bounded retries
            raw_bytes, fetch_error, attempts_made, cancelled = self._fetch_raw_message_with_retry(
                message_id=msg_id,
                current_index=idx,
                total_messages=total_scanned,
                completed_this_run=completed_this_run,
                skipped_completed=skipped_completed,
                failed_this_run=len(failures),
                cancel_event=cancel_event,
                progress_callback=progress_callback,
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
                )

            if fetch_error is not None or raw_bytes is None:
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
            try:
                written = write_eml(output_root, msg_id, raw_bytes)
            except OSError as fs_exc:
                raise FatalStorageError(
                    f"Fatal filesystem error writing message '{msg_id}': {fs_exc}"
                ) from fs_exc

            # Checkpoint completed message (which atomically clears any prior failure)
            relative_path = written.path.relative_to(output_root).as_posix()
            try:
                self._checkpoint_store.mark_completed(
                    CompletedMessage(
                        message_id=msg_id,
                        relative_path=relative_path,
                        sha256=written.sha256,
                        size_bytes=written.size_bytes,
                    )
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
        )

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

    def _fetch_raw_message_with_retry(
        self,
        message_id: str,
        current_index: int,
        total_messages: int,
        completed_this_run: int,
        skipped_completed: int,
        failed_this_run: int,
        cancel_event: threading.Event | None,
        progress_callback: Callable[[ExportProgress], None] | None,
    ) -> tuple[bytes | None, Exception | None, int, bool]:
        attempt = 1
        while True:
            if cancel_event and cancel_event.is_set():
                return None, None, attempt - 1, True

            try:
                raw_bytes = self._gmail_client.get_raw_message(message_id)
                return raw_bytes, None, attempt, False
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
                    self._retry_policy.sleep_fn(delay)
                    if cancel_event and cancel_event.is_set():
                        return None, None, attempt, True
                    attempt += 1
                else:
                    return None, exc, attempt, False
