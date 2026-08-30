from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox

from mailbox_rescue.auth.google_oauth import OAuthConfigurationError
from mailbox_rescue.config import AppPaths
from mailbox_rescue.export.models import ExportPhase, ExportProgress, ExportResult, ExportScope
from mailbox_rescue.gmail.client import GmailClient, MailboxProfile
from mailbox_rescue.storage.checkpoint import CheckpointStore
from mailbox_rescue.ui.main_window import MainWindow
from mailbox_rescue.ui.worker import ExportWorker


def _create_test_window(tmp_path: Path) -> MainWindow:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(
        data_dir=data_dir,
        token_file=data_dir / "token.json",
        client_secrets_file=tmp_path / "secrets.json",
    )
    return MainWindow(paths)


def test_main_window_initial_state(qapp: object, tmp_path: Path) -> None:
    window = _create_test_window(tmp_path)

    assert window.account_status_label.text() == "Not connected"
    assert window.connect_button.text() == "Connect Google Account"
    assert window.connect_button.isEnabled() is True
    assert window.radio_all_mail.isChecked() is True
    assert window.radio_inbox.isChecked() is False
    assert window.get_selected_scope() == ExportScope.ALL_MAIL
    assert window.destination_path == ""
    assert window.start_button.isEnabled() is False
    assert window.cancel_button.isEnabled() is False
    assert window.open_folder_button.isEnabled() is False
    assert window.progress_status_label.text() == "Ready"


def test_scope_selection_mapping(qapp: object, tmp_path: Path) -> None:
    window = _create_test_window(tmp_path)

    assert window.get_selected_scope() == ExportScope.ALL_MAIL

    window.radio_inbox.setChecked(True)
    assert window.get_selected_scope() == ExportScope.INBOX

    window.set_scope(ExportScope.ALL_MAIL)
    assert window.radio_all_mail.isChecked() is True
    assert window.get_selected_scope() == ExportScope.ALL_MAIL

    window.set_scope(ExportScope.INBOX)
    assert window.radio_inbox.isChecked() is True
    assert window.get_selected_scope() == ExportScope.INBOX


def test_destination_selection_and_control_enabling(qapp: object, tmp_path: Path) -> None:
    window = _create_test_window(tmp_path)
    export_dir = tmp_path / "export_output"

    # Destination set but no account connected -> Start disabled, Open Folder enabled if dir exists
    export_dir.mkdir(parents=True, exist_ok=True)
    window.set_destination(str(export_dir))
    assert window.destination_path == str(export_dir)
    assert window.destination_edit.text() == str(export_dir)
    assert window.start_button.isEnabled() is False
    assert window.open_folder_button.isEnabled() is True

    # Account connected but destination empty -> Start disabled
    window.set_destination("")
    window.gmail_client = MagicMock(spec=GmailClient)
    window.mailbox_profile = MailboxProfile("user@example.com", 100, 10)
    window._update_control_states()
    assert window.start_button.isEnabled() is False

    # Both connected and destination set -> Start enabled
    window.set_destination(str(export_dir))
    assert window.start_button.isEnabled() is True


def test_connect_google_success_stores_client_and_updates_ui(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _create_test_window(tmp_path)

    mock_auth = MagicMock()
    mock_creds = MagicMock()
    mock_auth.return_value.authorize.return_value = mock_creds
    monkeypatch.setattr("mailbox_rescue.ui.main_window.GoogleOAuth", mock_auth)

    mock_client = MagicMock(spec=GmailClient)
    mock_client.profile.return_value = MailboxProfile(
        email_address="employee@company.com",
        messages_total=18432,
        threads_total=1200,
    )
    monkeypatch.setattr("mailbox_rescue.ui.main_window.GmailClient", lambda creds: mock_client)

    window.connect_google()

    assert window.gmail_client is mock_client
    assert window.mailbox_profile == MailboxProfile(
        email_address="employee@company.com",
        messages_total=18432,
        threads_total=1200,
    )
    assert "employee@company.com" in window.account_status_label.text()
    assert "18,432 messages" in window.account_status_label.text()
    assert window.connect_button.text() == "Refresh Account"
    assert window.connect_button.isEnabled() is True


def test_connect_google_oauth_config_error(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _create_test_window(tmp_path)

    mock_auth = MagicMock()
    mock_auth.return_value.authorize.side_effect = OAuthConfigurationError("Missing secrets file")
    monkeypatch.setattr("mailbox_rescue.ui.main_window.GoogleOAuth", mock_auth)

    warning_mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "warning", warning_mock)

    window.connect_google()

    assert window.gmail_client is None
    assert window.mailbox_profile is None
    assert "Google OAuth configuration required" in window.account_status_label.text()
    assert window.connect_button.isEnabled() is True
    warning_mock.assert_called_once()


def test_connect_google_general_error(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _create_test_window(tmp_path)

    mock_auth = MagicMock()
    mock_auth.return_value.authorize.side_effect = ConnectionResetError("Connection dropped")
    monkeypatch.setattr("mailbox_rescue.ui.main_window.GoogleOAuth", mock_auth)

    critical_mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "critical", critical_mock)

    window.connect_google()

    assert window.gmail_client is None
    assert window.mailbox_profile is None
    assert "Connection failed" in window.account_status_label.text()
    assert window.connect_button.isEnabled() is True
    critical_mock.assert_called_once()


def test_export_cannot_start_disconnected(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _create_test_window(tmp_path)
    window.set_destination(str(tmp_path / "out"))

    warning_mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "warning", warning_mock)

    window.start_export()

    warning_mock.assert_called_once()
    assert window._export_thread is None


def test_export_cannot_start_without_destination(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _create_test_window(tmp_path)
    window.gmail_client = MagicMock(spec=GmailClient)
    window.mailbox_profile = MailboxProfile("user@example.com", 100, 10)

    warning_mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "warning", warning_mock)

    window.start_export()

    warning_mock.assert_called_once()
    assert window._export_thread is None


def test_resume_identity_validation_incompatible_account(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "export_dest"
    checkpoint_dir = dest / "metadata"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    existing_store = CheckpointStore(checkpoint_dir / "checkpoint.sqlite3")
    existing_store.set_metadata("other-user@company.com", "all_mail")

    window = _create_test_window(tmp_path)
    window.gmail_client = MagicMock(spec=GmailClient)
    window.mailbox_profile = MailboxProfile("employee@company.com", 100, 10)
    window.set_destination(str(dest))

    warning_mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "warning", warning_mock)

    window.start_export()

    warning_mock.assert_called_once()
    args, _ = warning_mock.call_args
    assert "other-user@company.com" in args[2]
    assert window._export_thread is None


def test_resume_identity_validation_incompatible_scope(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "export_dest"
    checkpoint_dir = dest / "metadata"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    existing_store = CheckpointStore(checkpoint_dir / "checkpoint.sqlite3")
    existing_store.set_metadata("employee@company.com", "inbox")

    window = _create_test_window(tmp_path)
    window.gmail_client = MagicMock(spec=GmailClient)
    window.mailbox_profile = MailboxProfile("employee@company.com", 100, 10)
    window.set_destination(str(dest))
    window.set_scope(ExportScope.ALL_MAIL)

    warning_mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "warning", warning_mock)

    window.start_export()

    warning_mock.assert_called_once()
    args, _ = warning_mock.call_args
    assert "Inbox only" in args[2]
    assert window._export_thread is None


def test_export_worker_signals_and_service_execution(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "export_dest"
    store = CheckpointStore(dest / "metadata" / "checkpoint.sqlite3")
    mock_client = MagicMock(spec=GmailClient)
    cancel_event = threading.Event()

    worker = ExportWorker(
        gmail_client=mock_client,
        checkpoint_store=store,
        output_root=dest,
        scope=ExportScope.ALL_MAIL,
        cancel_event=cancel_event,
    )

    emitted_progress: list[ExportProgress] = []
    emitted_completed: list[ExportResult] = []
    emitted_failed: list[str] = []
    emitted_finished: list[bool] = []

    worker.progress.connect(emitted_progress.append)
    worker.completed.connect(emitted_completed.append)
    worker.failed.connect(emitted_failed.append)
    worker.finished.connect(lambda: emitted_finished.append(True))

    expected_result = ExportResult(
        total_scanned=2,
        completed_this_run=2,
        skipped_completed=0,
        failed=0,
        cancelled=False,
    )

    def mock_service_run(self, output_root, scope, cancel_event, progress_callback):
        if progress_callback:
            progress_callback(ExportProgress(phase=ExportPhase.SCANNING))
            progress_callback(
                ExportProgress(
                    phase=ExportPhase.MESSAGE_COMPLETED,
                    total_messages=2,
                    completed_this_run=1,
                )
            )
        return expected_result

    monkeypatch.setattr(
        "mailbox_rescue.ui.worker.ExportService.run",
        mock_service_run,
    )

    worker.run()

    assert len(emitted_progress) == 2
    assert emitted_progress[0].phase == ExportPhase.SCANNING
    assert emitted_progress[1].phase == ExportPhase.MESSAGE_COMPLETED
    assert len(emitted_completed) == 1
    assert emitted_completed[0] == expected_result
    assert len(emitted_failed) == 0
    assert len(emitted_finished) == 1


def test_export_worker_exception_handling(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "export_dest"
    store = CheckpointStore(dest / "metadata" / "checkpoint.sqlite3")
    mock_client = MagicMock(spec=GmailClient)
    cancel_event = threading.Event()

    worker = ExportWorker(
        gmail_client=mock_client,
        checkpoint_store=store,
        output_root=dest,
        scope=ExportScope.ALL_MAIL,
        cancel_event=cancel_event,
    )

    emitted_failed: list[str] = []
    emitted_finished: list[bool] = []

    worker.failed.connect(emitted_failed.append)
    worker.finished.connect(lambda: emitted_finished.append(True))

    def mock_service_crash(*args, **kwargs):
        raise RuntimeError("Fatal scan explosion")

    monkeypatch.setattr(
        "mailbox_rescue.ui.worker.ExportService.run",
        mock_service_crash,
    )

    worker.run()

    assert len(emitted_failed) == 1
    assert "Fatal scan explosion" in emitted_failed[0]
    assert len(emitted_finished) == 1


def test_progress_updates_ui_phases(qapp: object, tmp_path: Path) -> None:
    window = _create_test_window(tmp_path)

    # 1. Scanning phase -> Indeterminate
    window._on_export_progress(ExportProgress(phase=ExportPhase.SCANNING))
    assert window.progress_bar.minimum() == 0
    assert window.progress_bar.maximum() == 0
    assert window.progress_status_label.text() == "Scanning mailbox..."

    # 2. Scan complete
    window._on_export_progress(
        ExportProgress(phase=ExportPhase.SCAN_COMPLETE, total_messages=150)
    )
    assert window.progress_bar.maximum() == 150
    assert window.progress_bar.value() == 0
    assert "150 messages" in window.progress_status_label.text()

    # 3. Exporting
    window._on_export_progress(
        ExportProgress(phase=ExportPhase.EXPORTING, total_messages=150, current_index=1)
    )
    assert "Exporting 1 of 150" in window.progress_status_label.text()

    # 4. Message completed
    window._on_export_progress(
        ExportProgress(
            phase=ExportPhase.MESSAGE_COMPLETED,
            total_messages=150,
            completed_this_run=45,
            skipped_completed=10,
            failed_this_run=2,
        )
    )
    assert window.progress_bar.value() == 57
    assert "Exporting 57 of 150" in window.progress_status_label.text()
    assert "Saved: 45" in window.progress_detail_label.text()
    assert "Already saved: 10" in window.progress_detail_label.text()
    assert "Failed: 2" in window.progress_detail_label.text()

    # 5. Retrying
    window._on_export_progress(
        ExportProgress(phase=ExportPhase.RETRYING, total_messages=150, attempt=2)
    )
    assert "Temporary Gmail error. Retrying..." in window.progress_status_label.text()

    # 6. Cancelled
    window._on_export_progress(ExportProgress(phase=ExportPhase.CANCELLED, total_messages=150))
    assert "cancelled safely" in window.progress_status_label.text()


def test_export_completed_full_success_updates_ui(qapp: object, tmp_path: Path) -> None:
    window = _create_test_window(tmp_path)
    window.destination_path = str(tmp_path)

    result = ExportResult(
        total_scanned=50,
        completed_this_run=50,
        skipped_completed=0,
        failed=0,
        cancelled=False,
    )
    window._on_export_completed(result)

    assert window.progress_status_label.text() == "Export complete."
    assert "Saved: 50" in window.progress_detail_label.text()
    assert window.progress_bar.value() == 50
    assert window.connect_button.isEnabled() is True
    assert window.cancel_button.isEnabled() is False


def test_export_completed_partial_success_updates_ui(qapp: object, tmp_path: Path) -> None:
    window = _create_test_window(tmp_path)
    window.destination_path = str(tmp_path)

    result = ExportResult(
        total_scanned=50,
        completed_this_run=45,
        skipped_completed=0,
        failed=5,
        cancelled=False,
    )
    window._on_export_completed(result)

    assert "5 message(s) that could not be saved" in window.progress_status_label.text()
    assert "Saved: 45" in window.progress_detail_label.text()
    assert "Failed: 5" in window.progress_detail_label.text()
    assert window.connect_button.isEnabled() is True


def test_export_completed_cancelled_updates_ui(qapp: object, tmp_path: Path) -> None:
    window = _create_test_window(tmp_path)
    window.destination_path = str(tmp_path)

    result = ExportResult(
        total_scanned=50,
        completed_this_run=20,
        skipped_completed=0,
        failed=0,
        cancelled=True,
    )
    window._on_export_completed(result)

    assert "Export cancelled safely" in window.progress_status_label.text()
    assert "Saved: 20" in window.progress_detail_label.text()
    assert window.connect_button.isEnabled() is True


def test_export_failed_fatal_error_updates_ui(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _create_test_window(tmp_path)
    window.destination_path = str(tmp_path)

    critical_mock = MagicMock()
    monkeypatch.setattr(QMessageBox, "critical", critical_mock)

    window._on_export_failed("Simulated disk corruption")

    assert window.progress_status_label.text() == "Export stopped due to an error."
    critical_mock.assert_called_once()
    assert window.connect_button.isEnabled() is True


def test_cancel_button_sets_event_and_updates_ui(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _create_test_window(tmp_path)
    window.destination_path = str(tmp_path)
    window.cancel_event = threading.Event()
    window.cancel_button.setEnabled(True)

    window.cancel_export()

    assert window.cancel_event.is_set() is True
    assert window.cancel_button.isEnabled() is False
    assert window.progress_status_label.text() == "Cancelling safely..."


def test_export_lifecycle_and_reusability(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dest = tmp_path / "export_dest"
    window = _create_test_window(tmp_path)
    window.gmail_client = MagicMock(spec=GmailClient)
    window.mailbox_profile = MailboxProfile("user@example.com", 10, 2)
    window.set_destination(str(dest))

    # Mock service run to complete synchronously
    def mock_service_run(*args, **kwargs):
        return ExportResult(
            total_scanned=2,
            completed_this_run=2,
            skipped_completed=0,
            failed=0,
            cancelled=False,
        )

    monkeypatch.setattr("mailbox_rescue.ui.worker.ExportService.run", mock_service_run)

    # 1. Start first export
    window.start_export()
    assert window._export_thread is not None

    # Process events to allow QThread / worker run to finish
    while window._export_thread is not None:
        QCoreApplication.processEvents()

    assert window.progress_status_label.text() == "Export complete."
    assert window.start_button.isEnabled() is True
    assert window.connect_button.isEnabled() is True

    # 2. Start second export in the same app session
    window.start_export()
    assert window._export_thread is not None

    while window._export_thread is not None:
        QCoreApplication.processEvents()

    assert window.progress_status_label.text() == "Export complete."
    assert window.start_button.isEnabled() is True


def test_open_export_folder(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _create_test_window(tmp_path)
    export_dir = tmp_path / "my_backup"
    export_dir.mkdir(parents=True, exist_ok=True)
    window.set_destination(str(export_dir))

    open_url_mock = MagicMock()
    monkeypatch.setattr("mailbox_rescue.ui.main_window.QDesktopServices.openUrl", open_url_mock)

    window.open_export_folder()

    open_url_mock.assert_called_once()
    url_arg = open_url_mock.call_args[0][0]
    assert export_dir.name in url_arg.toString()


def test_full_ui_export_flow_and_resume(qapp: object, tmp_path: Path) -> None:
    dest = tmp_path / "full_export"
    window = _create_test_window(tmp_path)

    mock_client = MagicMock(spec=GmailClient)
    mock_client.iter_message_ids.return_value = iter(["msg_alpha", "msg_beta"])
    mock_client.get_raw_message.side_effect = lambda msg_id: f"From: test@example.com\r\nSubject: {msg_id}\r\n\r\nBody".encode("utf-8")

    window.gmail_client = mock_client
    window.mailbox_profile = MailboxProfile("user@example.com", 2, 1)
    window.set_destination(str(dest))

    # First run: exports both messages
    window.start_export()
    while window._export_thread is not None:
        QCoreApplication.processEvents()

    assert (dest / "messages" / "msg_alpha.eml").exists()
    assert (dest / "messages" / "msg_beta.eml").exists()
    assert (dest / "metadata" / "checkpoint.sqlite3").exists()
    assert window.progress_status_label.text() == "Export complete."
    assert "Saved: 2" in window.progress_detail_label.text()
    assert window.open_folder_button.isEnabled() is True

    # Second run: resumes and skips both messages
    mock_client.iter_message_ids.return_value = iter(["msg_alpha", "msg_beta"])
    window.start_export()
    while window._export_thread is not None:
        QCoreApplication.processEvents()

    assert window.progress_status_label.text() == "Export complete."
    assert "Saved: 0" in window.progress_detail_label.text()
    assert "Already saved: 2" in window.progress_detail_label.text()

