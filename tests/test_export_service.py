import hashlib
import json
import mailbox
import sqlite3
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from googleapiclient.errors import HttpError

import mailbox_rescue.export.service as export_service_module
from mailbox_rescue.export.models import (
    ExportPhase,
    ExportProgress,
    ExportScope,
    FatalStorageError,
    RetryPolicy,
    ScanError,
)
from mailbox_rescue.export.service import ExportService
from mailbox_rescue.gmail.client import GmailExportMessage, GmailLabel
from mailbox_rescue.storage.checkpoint import (
    CheckpointStore,
    CompletedMessage,
    FailedMessage,
    MessageMetadata,
)


def _make_http_error(status: int, reason: str = "", error_dict: dict | None = None) -> HttpError:
    resp = SimpleNamespace(status=status, reason=reason)
    if error_dict is not None:
        content = json.dumps(error_dict).encode("utf-8")
    else:
        content = b""
    return HttpError(resp=resp, content=content)


class FakeGmailClient:
    def __init__(
        self,
        message_map: dict[str, bytes] | None = None,
        message_ids: list[str] | None = None,
        labels: list[GmailLabel] | None = None,
    ) -> None:
        self.message_map = message_map or {}
        self.message_ids = message_ids if message_ids is not None else list(self.message_map.keys())
        self.labels = (
            labels
            if labels is not None
            else [
                GmailLabel(id="INBOX", name="INBOX", type="system"),
                GmailLabel(id="Label_1", name="Projects", type="user"),
            ]
        )
        self.scan_calls: list[dict[str, Any]] = []
        self.get_export_calls: list[str] = []
        self.get_raw_calls = self.get_export_calls
        self.scan_side_effects: list[Any] = []
        self.list_labels_side_effects: list[Any] = []
        self.get_export_side_effects: dict[str, list[Any]] = {}
        self.get_raw_side_effects = self.get_export_side_effects

    def list_labels(self) -> list[GmailLabel]:
        if self.list_labels_side_effects:
            effect = self.list_labels_side_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            if callable(effect):
                return effect()
        return self.labels

    def iter_message_ids(
        self,
        *,
        label_ids: list[str] | None = None,
        query: str | None = None,
        include_spam_trash: bool = False,
    ) -> Iterator[str]:
        self.scan_calls.append(
            {
                "label_ids": label_ids,
                "query": query,
                "include_spam_trash": include_spam_trash,
            }
        )
        if self.scan_side_effects:
            effect = self.scan_side_effects.pop(0)
            if isinstance(effect, BaseException):
                raise effect
            if callable(effect):
                effect()
        yield from self.message_ids

    def get_export_message(self, message_id: str) -> GmailExportMessage:
        self.get_export_calls.append(message_id)
        if self.get_export_side_effects.get(message_id):
            effect = self.get_export_side_effects[message_id].pop(0)
            if isinstance(effect, BaseException):
                raise effect
            if callable(effect):
                return effect()
            return effect
        if message_id not in self.message_map:
            raise _make_http_error(404, reason="Not Found")
        return GmailExportMessage(
            message_id=message_id,
            thread_id=f"th_{message_id}",
            label_ids=("INBOX", "Label_1"),
            raw_bytes=self.message_map[message_id],
        )

    def get_raw_message(self, message_id: str) -> bytes:
        return self.get_export_message(message_id).raw_bytes


def _make_test_policy(max_attempts: int = 4) -> tuple[RetryPolicy, list[float]]:
    delays: list[float] = []
    policy = RetryPolicy(
        max_attempts=max_attempts,
        base_delay=1.0,
        max_delay=16.0,
        jitter=0.0,
        sleep_fn=delays.append,
        jitter_fn=lambda j: 0.0,
    )
    return policy, delays


def test_successful_export_end_to_end(tmp_path: Path) -> None:
    output_root = tmp_path / "export_dir"
    db_path = output_root / "checkpoint.sqlite3"
    store = CheckpointStore(db_path)

    raw_msg_1 = b"From: a@example.com\r\nSubject: Test 1\r\n\r\nBody 1\r\n"
    raw_msg_2 = b"From: b@example.com\r\nSubject: Test 2\r\n\r\nBody 2\r\n"

    fake_client = FakeGmailClient(
        message_map={
            "msg_1": raw_msg_1,
            "msg_2": raw_msg_2,
        }
    )

    policy, _ = _make_test_policy()
    service = ExportService(
        gmail_client=fake_client,  # type: ignore[arg-type]
        checkpoint_store=store,
        retry_policy=policy,
    )

    progress_events: list[ExportProgress] = []
    result = service.run(
        output_root=output_root,
        scope=ExportScope.ALL_MAIL,
        progress_callback=progress_events.append,
    )

    # Validate result
    assert result.total_scanned == 2
    assert result.completed_this_run == 2
    assert result.skipped_completed == 0
    assert result.failed == 0
    assert result.cancelled is False
    assert result.failures == []

    # Validate files on disk
    eml_1 = output_root / "messages" / "msg_1.eml"
    eml_2 = output_root / "messages" / "msg_2.eml"
    assert eml_1.exists()
    assert eml_1.read_bytes() == raw_msg_1
    assert eml_2.exists()
    assert eml_2.read_bytes() == raw_msg_2

    # Validate checkpoint store
    assert store.completed_count() == 2
    rec1 = store.get_completed("msg_1")
    assert rec1 is not None
    assert rec1.relative_path == "messages/msg_1.eml"
    assert rec1.size_bytes == len(raw_msg_1)
    assert len(rec1.sha256) == 64

    # Validate progress phases
    phases = [p.phase for p in progress_events]
    assert ExportPhase.SCANNING in phases
    assert ExportPhase.SCAN_COMPLETE in phases
    assert ExportPhase.EXPORTING in phases
    assert ExportPhase.MESSAGE_COMPLETED in phases
    assert ExportPhase.COMPLETED in phases


def test_all_mail_scope_parameters(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"m1": b"body"})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    service.run(output_root=tmp_path, scope=ExportScope.ALL_MAIL)

    assert len(fake_client.scan_calls) == 1
    assert fake_client.scan_calls[0] == {
        "label_ids": None,
        "query": None,
        "include_spam_trash": False,
    }


def test_inbox_scope_parameters(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"m1": b"body"})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    service.run(output_root=tmp_path, scope=ExportScope.INBOX)

    assert len(fake_client.scan_calls) == 1
    assert fake_client.scan_calls[0] == {
        "label_ids": ["INBOX"],
        "query": None,
        "include_spam_trash": False,
    }


def test_resume_skips_already_completed_messages(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    raw_1 = b"raw 1"
    h1 = hashlib.sha256(raw_1).hexdigest()

    # Pre-populate msg_1 on disk with metadata
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir(parents=True)
    (messages_dir / "msg_1.eml").write_bytes(raw_1)

    store.mark_completed(
        CompletedMessage(
            message_id="msg_1",
            relative_path="messages/msg_1.eml",
            sha256=h1,
            size_bytes=len(raw_1),
        ),
        message_metadata=MessageMetadata(
            message_id="msg_1",
            thread_id="th_msg_1",
            labels_json='[{"id": "INBOX", "name": "INBOX"}]',
            captured_at="2026-08-28T12:00:00+00:00",
        ),
    )

    fake_client = FakeGmailClient(
        message_map={
            "msg_1": raw_1,
            "msg_2": b"raw 2",
        }
    )
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    progress_events: list[ExportProgress] = []
    result = service.run(
        output_root=tmp_path,
        scope=ExportScope.ALL_MAIL,
        progress_callback=progress_events.append,
    )

    assert result.total_scanned == 2
    assert result.completed_this_run == 1
    assert result.skipped_completed == 1
    assert result.failed == 0
    assert result.cancelled is False

    # Ensure get_export_message was only called for msg_2, never for msg_1
    assert fake_client.get_export_calls == ["msg_2"]

    # Ensure progress recorded message skipped
    skipped_events = [p for p in progress_events if p.phase == ExportPhase.MESSAGE_SKIPPED]
    assert len(skipped_events) == 1
    assert skipped_events[0].message_id == "msg_1"


def test_cancellation_before_work(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"msg_1": b"body 1"})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    cancel_event = threading.Event()
    cancel_event.set()

    progress_events: list[ExportProgress] = []
    result = service.run(
        output_root=tmp_path,
        cancel_event=cancel_event,
        progress_callback=progress_events.append,
    )

    assert result.cancelled is True
    assert result.total_scanned == 0
    assert result.completed_this_run == 0
    assert store.completed_count() == 0
    assert len(fake_client.scan_calls) == 0
    assert len(fake_client.get_raw_calls) == 0
    assert progress_events[-1].phase == ExportPhase.CANCELLED


def test_cancellation_mid_export_and_safe_resume(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(
        message_map={
            "msg_1": b"raw 1",
            "msg_2": b"raw 2",
            "msg_3": b"raw 3",
        }
    )
    cancel_event = threading.Event()

    def on_progress(p: ExportProgress) -> None:
        # Cancel as soon as msg_1 completes
        if p.phase == ExportPhase.MESSAGE_COMPLETED and p.message_id == "msg_1":
            cancel_event.set()

    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(
        output_root=tmp_path,
        cancel_event=cancel_event,
        progress_callback=on_progress,
    )

    assert result.cancelled is True
    assert result.completed_this_run == 1
    assert store.completed_count() == 1
    assert store.is_completed("msg_1")
    assert not store.is_completed("msg_2")
    assert not store.is_completed("msg_3")
    # msg_2 and msg_3 should not have been fetched
    assert fake_client.get_raw_calls == ["msg_1"]

    # Resume run without cancellation
    resume_result = service.run(output_root=tmp_path)
    assert resume_result.cancelled is False
    assert resume_result.total_scanned == 3
    assert resume_result.completed_this_run == 2
    assert resume_result.skipped_completed == 1
    assert store.completed_count() == 3
    assert fake_client.get_raw_calls == ["msg_1", "msg_2", "msg_3"]


def test_transient_429_then_success(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"msg_1": b"raw 1"})
    err429 = _make_http_error(429, reason="Too Many Requests")
    fake_client.get_raw_side_effects["msg_1"] = [err429, err429]

    policy, delays = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    progress_events: list[ExportProgress] = []
    result = service.run(
        output_root=tmp_path,
        progress_callback=progress_events.append,
    )

    assert result.completed_this_run == 1
    assert result.failed == 0
    assert len(fake_client.get_raw_calls) == 3
    assert len(delays) == 2
    assert delays == [1.0, 2.0]

    retry_events = [p for p in progress_events if p.phase == ExportPhase.RETRYING]
    assert len(retry_events) == 2
    assert retry_events[0].attempt == 2
    assert retry_events[1].attempt == 3


def test_transient_503_then_success(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"msg_1": b"raw 1"})
    err503 = _make_http_error(503, reason="Service Unavailable")
    fake_client.get_raw_side_effects["msg_1"] = [err503]

    policy, delays = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)
    assert result.completed_this_run == 1
    assert result.failed == 0
    assert len(fake_client.get_raw_calls) == 2
    assert len(delays) == 1


def test_retry_exhaustion_records_failure_and_continues(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(
        message_map={
            "msg_fail": b"raw fail",
            "msg_ok": b"raw ok",
        }
    )
    err500 = _make_http_error(500, reason="Internal Server Error")
    # 4 consecutive failures for msg_fail
    fake_client.get_raw_side_effects["msg_fail"] = [err500, err500, err500, err500]

    policy, delays = _make_test_policy(max_attempts=4)
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    assert result.total_scanned == 2
    assert result.completed_this_run == 1
    assert result.failed == 1
    assert len(result.failures) == 1
    assert result.failures[0].message_id == "msg_fail"
    assert result.failures[0].attempt_count == 4

    # Checkpoint store state
    assert store.is_completed("msg_ok")
    assert not store.is_completed("msg_fail")
    failure_record = store.get_failure("msg_fail")
    assert failure_record is not None
    assert failure_record.attempt_count == 4
    assert failure_record.error_type == "HttpError"

    # Exactly 3 backoff waits were performed for msg_fail (before attempts 2, 3, 4)
    assert len(delays) == 3


def test_rate_limit_403_retried_and_permission_403_not_retried(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    rate_limit_err = _make_http_error(
        403,
        error_dict={
            "error": {
                "code": 403,
                "message": "User Rate Limit Exceeded",
                "errors": [{"reason": "userRateLimitExceeded"}],
            }
        },
    )
    perm_err = _make_http_error(
        403,
        error_dict={
            "error": {
                "code": 403,
                "message": "Access Denied",
                "errors": [{"reason": "insufficientPermissions"}],
            }
        },
    )

    fake_client = FakeGmailClient(
        message_map={
            "msg_rate_limit": b"raw rate limit",
            "msg_perm": b"raw perm",
        }
    )
    fake_client.get_raw_side_effects["msg_rate_limit"] = [
        rate_limit_err
    ]  # 1 rate limit err then success
    fake_client.get_raw_side_effects["msg_perm"] = [perm_err]  # Permanent 403

    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    assert result.completed_this_run == 1
    assert result.failed == 1
    assert store.is_completed("msg_rate_limit")
    assert not store.is_completed("msg_perm")

    # msg_rate_limit was retried (2 calls total)
    assert fake_client.get_raw_calls.count("msg_rate_limit") == 2
    # msg_perm failed immediately on 1st attempt (1 call total, no retry)
    assert fake_client.get_raw_calls.count("msg_perm") == 1


def test_permanent_message_failure_404(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(
        message_map={
            "msg_404": b"not used",
            "msg_2": b"raw 2",
        }
    )
    fake_client.get_raw_side_effects["msg_404"] = [_make_http_error(404, reason="Not Found")]

    policy, delays = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    assert result.total_scanned == 2
    assert result.completed_this_run == 1
    assert result.failed == 1
    assert not store.is_completed("msg_404")
    assert store.is_completed("msg_2")
    assert store.get_failure("msg_404") is not None
    assert len(delays) == 0  # No retry for 404


def test_failure_recovery_on_subsequent_export(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    # Pre-populate a failure record
    store.mark_failed(
        FailedMessage(
            message_id="msg_1",
            error_type="HttpError",
            error_message="503 Service Unavailable",
            attempt_count=4,
            last_failed_at="2026-08-27T00:00:00+00:00",
        )
    )
    assert store.failed_count() == 1

    fake_client = FakeGmailClient(message_map={"msg_1": b"recovered raw"})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    assert result.completed_this_run == 1
    assert result.failed == 0
    assert store.is_completed("msg_1")
    # Failure record must be cleared after successful export
    assert store.failed_count() == 0
    assert store.get_failure("msg_1") is None


def test_checkpoint_database_failure_raises_fatal_storage_error(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"msg_1": b"raw 1"})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    with (
        patch.object(store, "mark_completed", side_effect=sqlite3.DatabaseError("DB write failed")),
        pytest.raises(FatalStorageError, match="Fatal database error"),
    ):
        service.run(output_root=tmp_path)


def test_checkpoint_read_failure_raises_fatal_storage_error(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"msg_1": b"raw 1"})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    with (
        patch.object(store, "get_completed", side_effect=sqlite3.DatabaseError("DB read failed")),
        pytest.raises(FatalStorageError, match="Fatal database error reading checkpoint"),
    ):
        service.run(output_root=tmp_path)


def test_file_write_failure_raises_fatal_storage_error(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"msg_1": b"raw 1"})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    with (
        patch("mailbox_rescue.export.service.write_eml", side_effect=OSError("Disk write failed")),
        pytest.raises(FatalStorageError, match="Fatal filesystem error"),
    ):
        service.run(output_root=tmp_path)

    assert not store.is_completed("msg_1")


def test_scan_retry_recovers_from_transient_error(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"msg_1": b"raw 1"})
    err503 = _make_http_error(503, reason="Service Unavailable")
    fake_client.scan_side_effects = [err503]

    policy, delays = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    assert result.completed_this_run == 1
    assert len(fake_client.scan_calls) == 2
    assert len(delays) == 1


def test_scan_retry_exhaustion_raises_scan_error(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"msg_1": b"raw 1"})
    err503 = _make_http_error(503, reason="Service Unavailable")
    fake_client.scan_side_effects = [err503, err503, err503, err503]

    policy, delays = _make_test_policy(max_attempts=4)
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    with pytest.raises(ScanError, match="Scanning Gmail mailbox failed"):
        service.run(output_root=tmp_path)

    assert len(delays) == 3
    assert len(fake_client.scan_calls) == 4


def test_fetch_retry_callback_cancellation_skips_sleep(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"msg_1": b"raw 1"})
    fake_client.get_raw_side_effects["msg_1"] = [_make_http_error(503)]
    cancel_event = threading.Event()

    def on_progress(progress: ExportProgress) -> None:
        if progress.phase == ExportPhase.RETRYING:
            cancel_event.set()

    policy, delays = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(
        output_root=tmp_path,
        cancel_event=cancel_event,
        progress_callback=on_progress,
    )

    assert result.cancelled is True
    assert delays == []


def test_resume_repairs_missing_eml(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    raw_1 = b"raw msg 1 payload"
    h1 = hashlib.sha256(raw_1).hexdigest()

    # Record in checkpoint, but do NOT create file on disk
    store.mark_completed(
        CompletedMessage(
            message_id="msg_1",
            relative_path="messages/msg_1.eml",
            sha256=h1,
            size_bytes=len(raw_1),
        ),
        message_metadata=MessageMetadata(
            message_id="msg_1",
            thread_id="th_1",
            labels_json="[]",
            captured_at="2026-08-28T12:00:00+00:00",
        ),
    )

    fake_client = FakeGmailClient(message_map={"msg_1": raw_1})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    # Verification detected missing file and self-repaired by fetching from Gmail
    assert result.completed_this_run == 1
    assert result.skipped_completed == 0
    assert result.failed == 0
    assert (tmp_path / "messages" / "msg_1.eml").is_file()
    assert (tmp_path / "messages" / "msg_1.eml").read_bytes() == raw_1
    assert fake_client.get_export_calls == ["msg_1"]


def test_resume_repairs_corrupted_hash_eml(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    raw_1 = b"raw msg 1 authentic"
    h1 = hashlib.sha256(raw_1).hexdigest()

    # Write tampered content on disk
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir(parents=True)
    (messages_dir / "msg_1.eml").write_bytes(b"tampered content!!")

    store.mark_completed(
        CompletedMessage(
            message_id="msg_1",
            relative_path="messages/msg_1.eml",
            sha256=h1,
            size_bytes=len(raw_1),
        ),
        message_metadata=MessageMetadata(
            message_id="msg_1",
            thread_id="th_1",
            labels_json="[]",
            captured_at="2026-08-28T12:00:00+00:00",
        ),
    )

    fake_client = FakeGmailClient(message_map={"msg_1": raw_1})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    # Verification detected hash mismatch and self-repaired
    assert result.completed_this_run == 1
    assert result.skipped_completed == 0
    assert (tmp_path / "messages" / "msg_1.eml").read_bytes() == raw_1
    assert fake_client.get_export_calls == ["msg_1"]


def test_resume_repairs_wrong_size_eml(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    raw_1 = b"raw msg 1 authentic"
    h1 = hashlib.sha256(raw_1).hexdigest()

    # Write truncated content on disk
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir(parents=True)
    (messages_dir / "msg_1.eml").write_bytes(b"truncated")

    store.mark_completed(
        CompletedMessage(
            message_id="msg_1",
            relative_path="messages/msg_1.eml",
            sha256=h1,
            size_bytes=len(raw_1),
        ),
        message_metadata=MessageMetadata(
            message_id="msg_1",
            thread_id="th_1",
            labels_json="[]",
            captured_at="2026-08-28T12:00:00+00:00",
        ),
    )

    fake_client = FakeGmailClient(message_map={"msg_1": raw_1})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    assert result.completed_this_run == 1
    assert result.skipped_completed == 0
    assert (tmp_path / "messages" / "msg_1.eml").read_bytes() == raw_1
    assert fake_client.get_export_calls == ["msg_1"]


def test_resume_rejects_unsafe_checkpoint_path_and_repairs_safely(tmp_path: Path) -> None:
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    store = CheckpointStore(archive_dir / "checkpoint.sqlite3")

    # Create an outside file in the parent folder
    outside_file = tmp_path / "sensitive.txt"
    outside_file.write_bytes(b"sensitive parent content")

    raw_1 = b"safe msg 1"
    h_outside = hashlib.sha256(outside_file.read_bytes()).hexdigest()

    # Malicious/corrupted checkpoint record pointing outside archive
    store.mark_completed(
        CompletedMessage(
            message_id="msg_1",
            relative_path="../sensitive.txt",
            sha256=h_outside,
            size_bytes=len(outside_file.read_bytes()),
        )
    )

    fake_client = FakeGmailClient(message_map={"msg_1": raw_1})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=archive_dir)

    # Verification rejected traversal and wrote safe EML inside archive_dir / messages
    assert result.completed_this_run == 1
    assert (archive_dir / "messages" / "msg_1.eml").is_file()
    assert (archive_dir / "messages" / "msg_1.eml").read_bytes() == raw_1

    # Checkpoint updated with safe relative path
    updated_rec = store.get_completed("msg_1")
    assert updated_rec is not None
    assert updated_rec.relative_path == "messages/msg_1.eml"


def test_pre_issue5_metadata_backfill(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    raw_1 = b"existing valid raw message"
    h1 = hashlib.sha256(raw_1).hexdigest()

    # Intact EML exists on disk
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir(parents=True)
    eml_file = messages_dir / "msg_1.eml"
    eml_file.write_bytes(raw_1)

    # Checkpoint record exists WITHOUT metadata
    store.mark_completed(
        CompletedMessage(
            message_id="msg_1",
            relative_path="messages/msg_1.eml",
            sha256=h1,
            size_bytes=len(raw_1),
        )
    )
    assert store.get_message_metadata("msg_1") is None

    fake_client = FakeGmailClient(message_map={"msg_1": raw_1})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    # Track if write_eml was called
    with patch("mailbox_rescue.export.service.write_eml") as mock_write_eml:
        result = service.run(output_root=tmp_path)

        # Metadata was backfilled without re-writing the valid EML
        mock_write_eml.assert_not_called()

    assert result.completed_this_run == 0
    assert result.skipped_completed == 1
    assert fake_client.get_export_calls == ["msg_1"]

    meta = store.get_message_metadata("msg_1")
    assert meta is not None
    assert meta.thread_id == "th_msg_1"
    assert "INBOX" in meta.labels_json


def test_backfill_database_error_raises_fatal_storage_error(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    raw_1 = b"existing valid raw message"
    h1 = hashlib.sha256(raw_1).hexdigest()

    messages_dir = tmp_path / "messages"
    messages_dir.mkdir(parents=True)
    (messages_dir / "msg_1.eml").write_bytes(raw_1)

    store.mark_completed(
        CompletedMessage(
            message_id="msg_1",
            relative_path="messages/msg_1.eml",
            sha256=h1,
            size_bytes=len(raw_1),
        )
    )

    fake_client = FakeGmailClient(message_map={"msg_1": raw_1})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    with (
        patch.object(
            store, "set_message_metadata", side_effect=sqlite3.DatabaseError("DB write failed")
        ),
        pytest.raises(
            FatalStorageError, match="Fatal database error backfilling metadata for 'msg_1'"
        ),
    ):
        service.run(output_root=tmp_path)


def test_new_message_canonicalizes_metadata_to_scanned_id_on_id_mismatch(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    raw_1 = b"test payload"

    fake_client = FakeGmailClient(message_ids=["msg_scanned"])
    fake_client.get_export_side_effects["msg_scanned"] = [
        GmailExportMessage(
            message_id="msg_returned_different",
            thread_id="th_123",
            label_ids=("INBOX",),
            raw_bytes=raw_1,
        )
    ]

    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    assert result.completed_this_run == 1
    assert store.is_completed("msg_scanned") is True
    assert store.is_completed("msg_returned_different") is False

    meta_scanned = store.get_message_metadata("msg_scanned")
    assert meta_scanned is not None
    assert meta_scanned.message_id == "msg_scanned"
    assert meta_scanned.thread_id == "th_123"

    meta_diff = store.get_message_metadata("msg_returned_different")
    assert meta_diff is None

    assert any("Inconsistent Gmail API message ID" in w for w in result.metadata_warnings)


def test_backfill_canonicalizes_metadata_to_scanned_id_on_id_mismatch(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    raw_1 = b"legacy raw payload"
    h1 = hashlib.sha256(raw_1).hexdigest()

    messages_dir = tmp_path / "messages"
    messages_dir.mkdir(parents=True)
    (messages_dir / "msg_legacy.eml").write_bytes(raw_1)

    store.mark_completed(
        CompletedMessage(
            message_id="msg_legacy",
            relative_path="messages/msg_legacy.eml",
            sha256=h1,
            size_bytes=len(raw_1),
        )
    )

    fake_client = FakeGmailClient(message_ids=["msg_legacy"])
    fake_client.get_export_side_effects["msg_legacy"] = [
        GmailExportMessage(
            message_id="msg_diff",
            thread_id="th_legacy",
            label_ids=("INBOX",),
            raw_bytes=raw_1,
        )
    ]

    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    assert result.completed_this_run == 0
    assert result.skipped_completed == 1

    meta_legacy = store.get_message_metadata("msg_legacy")
    assert meta_legacy is not None
    assert meta_legacy.message_id == "msg_legacy"
    assert meta_legacy.thread_id == "th_legacy"

    assert store.get_message_metadata("msg_diff") is None
    assert any("Inconsistent Gmail API message ID" in w for w in result.metadata_warnings)


def test_finalization_builds_derived_artifacts_from_only_verified_messages(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")
    raw_good = b"From: a@example.com\r\nSubject: Good Message\r\n\r\nGood\r\n"
    raw_bad = b"From: b@example.com\r\nSubject: Bad Message\r\n\r\nBad\r\n"

    h_good = hashlib.sha256(raw_good).hexdigest()
    h_bad = hashlib.sha256(raw_bad).hexdigest()

    messages_dir = tmp_path / "messages"
    messages_dir.mkdir(parents=True)
    (messages_dir / "msg_good.eml").write_bytes(raw_good)
    # Write corrupted bytes to msg_bad.eml on disk
    (messages_dir / "msg_bad.eml").write_bytes(b"corrupted bytes on disk")

    store.mark_completed(
        CompletedMessage(
            message_id="msg_good",
            relative_path="messages/msg_good.eml",
            sha256=h_good,
            size_bytes=len(raw_good),
        ),
        MessageMetadata(
            message_id="msg_good",
            thread_id="th_good",
            labels_json='[{"id": "INBOX", "name": "INBOX"}]',
            captured_at="2026-08-28T00:00:00Z",
        ),
    )
    store.mark_completed(
        CompletedMessage(
            message_id="msg_bad",
            relative_path="messages/msg_bad.eml",
            sha256=h_bad,
            size_bytes=len(raw_bad),
        ),
        MessageMetadata(
            message_id="msg_bad",
            thread_id="th_bad",
            labels_json='[{"id": "INBOX", "name": "INBOX"}]',
            captured_at="2026-08-28T00:00:00Z",
        ),
    )

    fake_client = FakeGmailClient(message_ids=["msg_good", "msg_bad"])
    # Make msg_bad fail to refetch so it stays corrupted in the checkpoint
    fake_client.get_export_side_effects["msg_bad"] = [_make_http_error(404, reason="Not Found")]

    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    # Check verification result
    assert result.archive_verified is False
    assert result.verified_files == 1
    assert len(result.verification_failures) == 1
    assert result.verification_failures[0].message_id == "msg_bad"

    # 1. Manifest includes msg_good only
    manifest_content = (tmp_path / "checksums.sha256").read_text(encoding="utf-8")
    assert "messages/msg_good.eml" in manifest_content
    assert "messages/msg_bad.eml" not in manifest_content

    # 2. MBOX includes msg_good only
    mb = mailbox.mbox(str(tmp_path / "mailbox.mbox"))
    try:
        assert len(mb) == 1
        assert mb[0]["subject"] == "Good Message"
    finally:
        mb.close()

    # 3. messages.jsonl includes msg_good only
    jsonl_lines = [
        line.strip()
        for line in (tmp_path / "metadata" / "messages.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(jsonl_lines) == 1
    assert json.loads(jsonl_lines[0])["gmail_message_id"] == "msg_good"

    # 4. Report retains VERIFICATION FAILED and lists msg_bad failure
    report_html = (tmp_path / "export-report.html").read_text(encoding="utf-8")
    assert "VERIFICATION FAILED" in report_html
    assert "msg_bad" in report_html


def test_export_service_generates_all_portable_archive_artifacts(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")
    store.set_metadata(account_email="alice@example.com", export_scope="all_mail")

    raw_1 = b"From: a@example.com\r\nSubject: Test 1\r\n\r\nBody 1\r\n"
    raw_2 = b"From: b@example.com\r\nSubject: Test 2\r\n\r\nBody 2\r\n"

    fake_client = FakeGmailClient(
        message_map={"msg_1": raw_1, "msg_2": raw_2},
        labels=[
            GmailLabel(id="INBOX", name="INBOX", type="system"),
            GmailLabel(id="Label_1", name="Work", type="user"),
        ],
    )
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    # 1. Check result fields
    assert result.archive_verified is True
    assert result.verified_files == 2
    assert result.verification_failures == []
    assert result.metadata_warnings == []
    assert result.report_path is not None
    assert result.report_path == tmp_path / "export-report.html"

    # 2. Check canonical files
    assert (tmp_path / "export.sqlite3").is_file()
    assert (tmp_path / "messages" / "msg_1.eml").is_file()
    assert (tmp_path / "messages" / "msg_2.eml").is_file()

    # 3. Check derived portable artifacts
    assert (tmp_path / "checksums.sha256").is_file()
    assert (tmp_path / "mailbox.mbox").is_file()
    assert (tmp_path / "metadata" / "account.json").is_file()
    assert (tmp_path / "metadata" / "labels.json").is_file()
    assert (tmp_path / "metadata" / "messages.jsonl").is_file()
    assert (tmp_path / "export-report.html").is_file()

    # Verify report content
    report_text = (tmp_path / "export-report.html").read_text(encoding="utf-8")
    assert "VERIFIED COMPLETE" in report_text
    assert "alice@example.com" in report_text


def test_export_service_label_fetch_failure_degrades_gracefully(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(message_map={"msg_1": b"raw 1"})
    # Fail list_labels with permanent 403
    perm_403 = _make_http_error(
        403, reason="Forbidden", error_dict={"error": {"code": 403, "message": "No label access"}}
    )
    fake_client.list_labels_side_effects = [perm_403]

    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    # Export must not fail; raw EML is rescued
    assert result.completed_this_run == 1
    assert result.failed == 0
    assert len(result.metadata_warnings) == 1
    assert "Failed to fetch Gmail label names" in result.metadata_warnings[0]
    assert (tmp_path / "messages" / "msg_1.eml").is_file()

    # Label IDs are preserved in metadata
    meta = store.get_message_metadata("msg_1")
    assert meta is not None
    assert "INBOX" in meta.labels_json


def test_export_service_partial_export_generates_derived_artifacts_with_failures(
    tmp_path: Path,
) -> None:
    store = CheckpointStore(tmp_path / "checkpoint.sqlite3")
    fake_client = FakeGmailClient(
        message_map={
            "msg_fail": b"not used",
            "msg_ok": b"raw ok",
        }
    )
    fake_client.get_export_side_effects["msg_fail"] = [_make_http_error(404, reason="Not Found")]

    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    assert result.completed_this_run == 1
    assert result.failed == 1
    assert result.archive_verified is True
    assert result.verified_files == 1

    # Derived artifacts are generated for the successful message
    assert (tmp_path / "checksums.sha256").is_file()
    assert (tmp_path / "mailbox.mbox").is_file()
    assert (tmp_path / "metadata" / "messages.jsonl").is_file()

    report_html = (tmp_path / "export-report.html").read_text(encoding="utf-8")
    assert "PARTIAL EXPORT" in report_html
    assert "msg_fail" in report_html


def test_cancel_before_canonical_mutation_preserves_derived_artifacts(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")
    store.set_metadata(account_email="alice@example.com", export_scope="all_mail")
    fake_client = FakeGmailClient(message_map={"msg_1": b"raw 1"})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    # Initial full export
    res1 = service.run(output_root=tmp_path)
    assert res1.completed_this_run == 1
    assert (tmp_path / "mailbox.mbox").is_file()
    assert (tmp_path / "checksums.sha256").is_file()
    assert (tmp_path / "metadata" / "messages.jsonl").is_file()
    assert (tmp_path / "export-report.html").is_file()

    # Cancel before scan begins
    cancel_ev = threading.Event()
    cancel_ev.set()
    res2 = service.run(output_root=tmp_path, cancel_event=cancel_ev)
    assert res2.cancelled is True

    # Derived artifacts must still exist
    assert (tmp_path / "mailbox.mbox").is_file()
    assert (tmp_path / "checksums.sha256").is_file()
    assert (tmp_path / "metadata" / "messages.jsonl").is_file()
    assert (tmp_path / "export-report.html").is_file()


def test_cancel_after_canonical_mutation_invalidates_derived_artifacts_and_resumes_cleanly(
    tmp_path: Path,
) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")
    store.set_metadata(account_email="alice@example.com", export_scope="all_mail")

    raw_1 = b"From: a@example.com\r\nSubject: Test 1\r\n\r\nBody 1\r\n"
    raw_2 = b"From: b@example.com\r\nSubject: Test 2\r\n\r\nBody 2\r\n"

    fake_client = FakeGmailClient(message_map={"msg_1": raw_1})
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    # 1. Initial full export of msg_1
    res1 = service.run(output_root=tmp_path)
    assert res1.completed_this_run == 1
    assert (tmp_path / "mailbox.mbox").is_file()
    assert (tmp_path / "checksums.sha256").is_file()
    assert (tmp_path / "metadata" / "messages.jsonl").is_file()
    assert (tmp_path / "export-report.html").is_file()

    # 2. Add msg_2, cancel right after msg_2 is written
    fake_client.message_map["msg_2"] = raw_2
    fake_client.message_ids = ["msg_1", "msg_2"]

    cancel_ev = threading.Event()

    def on_progress(p: ExportProgress) -> None:
        if p.phase == ExportPhase.MESSAGE_COMPLETED and p.message_id == "msg_2":
            cancel_ev.set()

    res2 = service.run(
        output_root=tmp_path,
        cancel_event=cancel_ev,
        progress_callback=on_progress,
    )
    assert res2.cancelled is True
    assert res2.completed_this_run == 1

    # Canonical msg_2 is written and checkpointed
    assert (tmp_path / "messages" / "msg_2.eml").is_file()
    assert store.is_completed("msg_2")

    # Stale derived artifacts are invalidated and removed!
    assert not (tmp_path / "mailbox.mbox").exists()
    assert not (tmp_path / "checksums.sha256").exists()
    assert not (tmp_path / "metadata" / "messages.jsonl").exists()
    assert not (tmp_path / "export-report.html").exists()

    # 3. Resume export to completion
    res3 = service.run(output_root=tmp_path)
    assert res3.cancelled is False
    assert res3.completed_this_run == 0
    assert res3.skipped_completed == 2
    assert res3.archive_verified is True

    # Derived artifacts are generated cleanly
    assert (tmp_path / "mailbox.mbox").is_file()
    assert (tmp_path / "checksums.sha256").is_file()
    assert (tmp_path / "metadata" / "messages.jsonl").is_file()
    assert (tmp_path / "export-report.html").is_file()

    # Check for no duplicate MBOX messages
    read_mbox = mailbox.mbox(str(tmp_path / "mailbox.mbox"))
    try:
        assert len(read_mbox) == 2
    finally:
        read_mbox.close()


def test_resume_failed_label_lookup_preserves_existing_labels_snapshot(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")
    store.set_metadata(account_email="alice@example.com", export_scope="all_mail")

    fake_client = FakeGmailClient(
        message_map={"msg_1": b"raw 1"},
        labels=[GmailLabel(id="INBOX", name="INBOX", type="system")],
    )
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    # Initial export writes labels.json with INBOX
    service.run(output_root=tmp_path)
    labels_file = tmp_path / "metadata" / "labels.json"
    assert labels_file.is_file()
    initial_labels_json = labels_file.read_text(encoding="utf-8")
    assert "INBOX" in initial_labels_json

    # Second run: add msg_2 and fail list_labels
    fake_client.message_map["msg_2"] = b"raw 2"
    fake_client.message_ids = ["msg_1", "msg_2"]
    fake_client.list_labels_side_effects = [_make_http_error(403, reason="Forbidden")]

    res2 = service.run(output_root=tmp_path)
    assert res2.completed_this_run == 1
    assert len(res2.metadata_warnings) == 1
    assert "Failed to fetch Gmail label names" in res2.metadata_warnings[0]

    # labels.json was preserved and not overwritten with empty []
    assert labels_file.read_text(encoding="utf-8") == initial_labels_json


def test_incomplete_metadata_backfill_failure_preserves_verified_eml_and_reports_warning(
    tmp_path: Path,
) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")
    store.set_metadata(account_email="alice@example.com", export_scope="all_mail")

    raw_1 = b"From: a@example.com\r\nSubject: Intact EML\r\n\r\nBody text\r\n"
    eml_file = tmp_path / "messages" / "msg_1.eml"
    eml_file.parent.mkdir(parents=True)
    eml_file.write_bytes(raw_1)

    # Checkpoint has CompletedMessage with missing MessageMetadata (pre-#5 archive)
    store.mark_completed(
        CompletedMessage(
            message_id="msg_1",
            relative_path="messages/msg_1.eml",
            sha256=hashlib.sha256(raw_1).hexdigest(),
            size_bytes=len(raw_1),
        )
    )

    fake_client = FakeGmailClient(
        message_map={},  # msg_1 not in message_map -> 404 Not Found on fetch
        message_ids=["msg_1"],
    )
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    result = service.run(output_root=tmp_path)

    # 1. EML is intact and verified
    assert result.failed == 0
    assert result.skipped_completed == 1
    assert result.archive_verified is True
    assert result.verified_files == 1

    # 2. Metadata warning is recorded
    assert len(result.metadata_warnings) >= 1
    assert any(
        "Could not backfill metadata for message 'msg_1'" in w for w in result.metadata_warnings
    )

    # 3. MBOX and manifest include the verified message
    read_mbox = mailbox.mbox(str(tmp_path / "mailbox.mbox"))
    try:
        assert len(read_mbox) == 1
    finally:
        read_mbox.close()
    assert "messages/msg_1.eml" in (tmp_path / "checksums.sha256").read_text(encoding="utf-8")

    # 4. messages.jsonl excludes msg_1 due to incomplete metadata
    messages_jsonl = tmp_path / "metadata" / "messages.jsonl"
    assert messages_jsonl.read_text(encoding="utf-8").strip() == ""

    # 5. Report shows VERIFIED WITH METADATA WARNINGS
    report_html = (tmp_path / "export-report.html").read_text(encoding="utf-8")
    assert "VERIFIED WITH METADATA WARNINGS" in report_html


def test_export_service_cancellation_during_phase_3_verify_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")
    store.set_metadata(account_email="alice@example.com", export_scope="all_mail")

    msg_map = {
        "m1": b"From: a@example.com\r\nSubject: 1\r\n\r\nBody 1\r\n",
        "m2": b"From: b@example.com\r\nSubject: 2\r\n\r\nBody 2\r\n",
        "m3": b"From: c@example.com\r\nSubject: 3\r\n\r\nBody 3\r\n",
    }
    fake_client = FakeGmailClient(message_map=msg_map)
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    cancel_ev = threading.Event()
    original_verify_archive = export_service_module.verify_archive

    def hook_verify_archive(out_root, chk_store, cancel_event=None):
        # Trigger cancellation right when verify_archive starts
        cancel_ev.set()
        return original_verify_archive(out_root, chk_store, cancel_event=cancel_ev)

    monkeypatch.setattr(export_service_module, "verify_archive", hook_verify_archive)

    result = service.run(output_root=tmp_path, cancel_event=cancel_ev)

    assert result.cancelled is True
    assert result.completed_this_run == 3
    assert result.failed == 0
    assert result.report_path is None
    # Derived artifacts must not exist
    assert not (tmp_path / "mailbox.mbox").exists()
    assert not (tmp_path / "checksums.sha256").exists()
    assert not (tmp_path / "export-report.html").exists()

    # Canonical messages and checkpoint are preserved
    assert (tmp_path / "messages" / "m1.eml").is_file()
    assert (tmp_path / "messages" / "m2.eml").is_file()
    assert (tmp_path / "messages" / "m3.eml").is_file()
    assert store.is_completed("m1")
    assert store.is_completed("m2")
    assert store.is_completed("m3")


def test_export_service_cancellation_during_phase_3_write_mbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")
    store.set_metadata(account_email="alice@example.com", export_scope="all_mail")

    msg_map = {
        "m1": b"From: a@example.com\r\nSubject: 1\r\n\r\nBody 1\r\n",
        "m2": b"From: b@example.com\r\nSubject: 2\r\n\r\nBody 2\r\n",
        "m3": b"From: c@example.com\r\nSubject: 3\r\n\r\nBody 3\r\n",
    }
    fake_client = FakeGmailClient(message_map=msg_map)
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    cancel_ev = threading.Event()
    original_write_mbox = export_service_module.write_mbox

    def hook_write_mbox(out_root, verified_msgs, cancel_event=None):
        # Trigger cancellation during write_mbox
        cancel_ev.set()
        return original_write_mbox(out_root, verified_msgs, cancel_event=cancel_ev)

    monkeypatch.setattr(export_service_module, "write_mbox", hook_write_mbox)

    result = service.run(output_root=tmp_path, cancel_event=cancel_ev)

    assert result.cancelled is True
    assert result.completed_this_run == 3
    assert result.failed == 0
    assert result.report_path is None
    assert not (tmp_path / "mailbox.mbox").exists()
    assert not (tmp_path / "mailbox.mbox.part").exists()
    assert not (tmp_path / "export-report.html").exists()


def test_export_service_resume_after_phase_3_cancellation(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path / "export.sqlite3")
    store.set_metadata(account_email="alice@example.com", export_scope="all_mail")

    msg_map = {
        "m1": b"From: a@example.com\r\nSubject: 1\r\n\r\nBody 1\r\n",
        "m2": b"From: b@example.com\r\nSubject: 2\r\n\r\nBody 2\r\n",
    }
    fake_client = FakeGmailClient(message_map=msg_map)
    policy, _ = _make_test_policy()
    service = ExportService(fake_client, store, policy)  # type: ignore[arg-type]

    # 1. Run and cancel right when Phase 3 starts
    cancel_ev = threading.Event()

    def on_progress(p: ExportProgress) -> None:
        if p.phase == ExportPhase.MESSAGE_COMPLETED and p.message_id == "m2":
            cancel_ev.set()

    res1 = service.run(output_root=tmp_path, cancel_event=cancel_ev, progress_callback=on_progress)
    assert res1.cancelled is True
    assert not (tmp_path / "mailbox.mbox").exists()
    assert not (tmp_path / "export-report.html").exists()

    # 2. Resume without cancellation
    res2 = service.run(output_root=tmp_path)
    assert res2.cancelled is False
    assert res2.archive_verified is True
    assert res2.completed_this_run == 0
    assert res2.skipped_completed == 2
    assert (tmp_path / "mailbox.mbox").is_file()
    assert (tmp_path / "checksums.sha256").is_file()
    assert (tmp_path / "export-report.html").is_file()

    read_mbox = mailbox.mbox(str(tmp_path / "mailbox.mbox"))
    try:
        assert len(read_mbox) == 2
    finally:
        read_mbox.close()

    report_html = (tmp_path / "export-report.html").read_text(encoding="utf-8")
    assert "VERIFIED COMPLETE" in report_html
