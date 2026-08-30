from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QThread, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from mailbox_rescue.auth.google_oauth import GoogleOAuth, OAuthConfigurationError
from mailbox_rescue.config import AppPaths
from mailbox_rescue.export.models import ExportPhase, ExportProgress, ExportResult, ExportScope
from mailbox_rescue.gmail.client import GmailClient, MailboxProfile
from mailbox_rescue.storage.checkpoint import CheckpointStore, check_resume_compatibility
from mailbox_rescue.ui.worker import ExportWorker


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        self.gmail_client: GmailClient | None = None
        self.mailbox_profile: MailboxProfile | None = None
        self.destination_path: str = ""
        self.cancel_event: threading.Event | None = None
        self._export_thread: QThread | None = None
        self._export_worker: ExportWorker | None = None

        self.setWindowTitle("Mailbox Rescue")
        self.resize(620, 480)

        self._init_ui()

    def _init_ui(self) -> None:
        main_layout = QVBoxLayout()
        main_layout.setSpacing(12)

        # 1. Header & Account Section
        account_group = QGroupBox("Google Account")
        account_layout = QVBoxLayout()
        account_layout.setSpacing(8)

        self.account_status_label = QLabel("Not connected")
        self.account_status_label.setWordWrap(True)
        self.connect_button = QPushButton("Connect Google Account")
        self.connect_button.clicked.connect(self.connect_google)

        account_layout.addWidget(self.account_status_label)
        account_layout.addWidget(self.connect_button)
        account_group.setLayout(account_layout)
        main_layout.addWidget(account_group)

        # 2. Scope Selection Section
        scope_group = QGroupBox("What should be preserved?")
        scope_layout = QVBoxLayout()
        scope_layout.setSpacing(4)

        self.scope_button_group = QButtonGroup(self)
        self.radio_all_mail = QRadioButton("All Mail")
        self.radio_inbox = QRadioButton("Inbox only")
        self.radio_all_mail.setChecked(True)

        self.scope_button_group.addButton(self.radio_all_mail)
        self.scope_button_group.addButton(self.radio_inbox)

        self.scope_helper_label = QLabel("Note: All Mail excludes Spam and Trash.")
        self.scope_helper_label.setStyleSheet("color: gray; font-size: 11px;")

        scope_layout.addWidget(self.radio_all_mail)
        scope_layout.addWidget(self.radio_inbox)
        scope_layout.addWidget(self.scope_helper_label)
        scope_group.setLayout(scope_layout)
        main_layout.addWidget(scope_group)

        # 3. Destination Folder Section
        dest_group = QGroupBox("Destination Folder")
        dest_layout = QVBoxLayout()
        dest_layout.setSpacing(6)

        dest_row_layout = QHBoxLayout()
        self.destination_edit = QLineEdit()
        self.destination_edit.setPlaceholderText("Select a folder to store exported messages...")
        self.destination_edit.textChanged.connect(self._on_destination_text_changed)

        self.browse_button = QPushButton("Browse...")
        self.browse_button.clicked.connect(self.browse_destination)

        dest_row_layout.addWidget(self.destination_edit)
        dest_row_layout.addWidget(self.browse_button)
        dest_layout.addLayout(dest_row_layout)
        dest_group.setLayout(dest_layout)
        main_layout.addWidget(dest_group)

        # 4. Progress and Status Section
        progress_group = QGroupBox("Export Progress")
        progress_layout = QVBoxLayout()
        progress_layout.setSpacing(6)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)

        self.progress_status_label = QLabel("Ready")
        self.progress_status_label.setWordWrap(True)

        self.progress_detail_label = QLabel("")
        self.progress_detail_label.setStyleSheet("color: gray; font-size: 11px;")

        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_status_label)
        progress_layout.addWidget(self.progress_detail_label)
        progress_group.setLayout(progress_layout)
        main_layout.addWidget(progress_group)

        # 5. Action Buttons Row
        actions_layout = QHBoxLayout()
        self.start_button = QPushButton("Start Export")
        self.start_button.clicked.connect(self.start_export)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_export)

        self.open_folder_button = QPushButton("Open Folder")
        self.open_folder_button.clicked.connect(self.open_export_folder)

        actions_layout.addWidget(self.start_button)
        actions_layout.addWidget(self.cancel_button)
        actions_layout.addWidget(self.open_folder_button)
        main_layout.addLayout(actions_layout)

        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)

        self._update_control_states()

    def connect_google(self) -> None:
        self.connect_button.setEnabled(False)
        self.account_status_label.setText("Opening Google sign-in...")

        try:
            credentials = GoogleOAuth(
                client_secrets_file=self.paths.client_secrets_file,
                token_file=self.paths.token_file,
            ).authorize()
            client = GmailClient(credentials)
            profile = client.profile()
        except OAuthConfigurationError as exc:
            QMessageBox.warning(self, "OAuth configuration required", str(exc))
            self.account_status_label.setText("Google OAuth configuration required")
        except Exception as exc:  # noqa: BLE001 - Outermost GUI boundary
            QMessageBox.critical(self, "Could not connect", str(exc))
            self.account_status_label.setText("Connection failed")
        else:
            self.gmail_client = client
            self.mailbox_profile = profile
            self.account_status_label.setText(
                f"Connected as: {profile.email_address}\n"
                f"Gmail reports approximately {profile.messages_total:,} messages "
                f"({profile.threads_total:,} threads)."
            )
            self.connect_button.setText("Refresh Account")
        finally:
            self.connect_button.setEnabled(True)
            self._update_control_states()

    def browse_destination(self) -> None:
        initial_dir = self.destination_path or str(Path.home())
        chosen_dir = QFileDialog.getExistingDirectory(
            self,
            "Select Export Destination Folder",
            initial_dir,
        )
        if chosen_dir:
            self.set_destination(chosen_dir)

    def set_destination(self, path_str: str) -> None:
        self.destination_path = path_str.strip()
        self.destination_edit.setText(self.destination_path)
        self._update_control_states()

    def _on_destination_text_changed(self, text: str) -> None:
        self.destination_path = text.strip()
        self._update_control_states()

    def get_selected_scope(self) -> ExportScope:
        if self.radio_inbox.isChecked():
            return ExportScope.INBOX
        return ExportScope.ALL_MAIL

    def set_scope(self, scope: ExportScope) -> None:
        if scope == ExportScope.INBOX:
            self.radio_inbox.setChecked(True)
        else:
            self.radio_all_mail.setChecked(True)

    def start_export(self) -> None:
        if self.gmail_client is None or self.mailbox_profile is None:
            QMessageBox.warning(
                self,
                "Not Connected",
                "Please connect a Google account before exporting.",
            )
            return

        if not self.destination_path:
            QMessageBox.warning(
                self,
                "Destination Required",
                "Please choose a destination folder for the export.",
            )
            return

        output_root = Path(self.destination_path).expanduser().resolve()
        selected_scope = self.get_selected_scope()

        # Initialize checkpoint database and check resume compatibility
        metadata_dir = output_root / "metadata"
        checkpoint_path = metadata_dir / "checkpoint.sqlite3"
        try:
            metadata_dir.mkdir(parents=True, exist_ok=True)
            checkpoint_store = CheckpointStore(checkpoint_path)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self,
                "Storage Error",
                f"Could not initialize export checkpoint database:\n{exc}",
            )
            return

        compatible, reason = check_resume_compatibility(
            checkpoint_store=checkpoint_store,
            account_email=self.mailbox_profile.email_address,
            export_scope=selected_scope.value,
        )
        if not compatible:
            QMessageBox.warning(
                self,
                "Resume Incompatible",
                reason or "This destination folder is incompatible with the current account or scope.",
            )
            return

        try:
            checkpoint_store.set_metadata(
                account_email=self.mailbox_profile.email_address,
                export_scope=selected_scope.value,
            )
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(
                self,
                "Storage Error",
                f"Failed to record archive identity in checkpoint database:\n{exc}",
            )
            return

        self.cancel_event = threading.Event()
        self._export_thread = QThread()
        self._export_worker = ExportWorker(
            gmail_client=self.gmail_client,
            checkpoint_store=checkpoint_store,
            output_root=output_root,
            scope=selected_scope,
            cancel_event=self.cancel_event,
        )
        self._export_worker.moveToThread(self._export_thread)

        # Wire worker lifecycle and signals
        self._export_thread.started.connect(self._export_worker.run)
        self._export_worker.progress.connect(self._on_export_progress)
        self._export_worker.completed.connect(self._on_export_completed)
        self._export_worker.failed.connect(self._on_export_failed)

        # Wire cleanup
        self._export_worker.finished.connect(self._export_thread.quit)
        self._export_worker.finished.connect(self._export_worker.deleteLater)
        self._export_thread.finished.connect(self._export_thread.deleteLater)
        self._export_thread.finished.connect(self._on_export_thread_finished)

        self._set_exporting_state()
        self._export_thread.start()

    def cancel_export(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.progress_status_label.setText("Cancelling safely...")

    def open_export_folder(self) -> None:
        if self.destination_path:
            path = Path(self.destination_path).expanduser().resolve()
            if path.exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_export_progress(self, progress: ExportProgress) -> None:
        match progress.phase:
            case ExportPhase.SCANNING:
                self.progress_bar.setRange(0, 0)
                self.progress_status_label.setText("Scanning mailbox...")
                self.progress_detail_label.setText("")
            case ExportPhase.SCAN_COMPLETE:
                total = max(progress.total_messages, 1)
                self.progress_bar.setRange(0, total)
                self.progress_bar.setValue(0)
                self.progress_status_label.setText(f"Found {progress.total_messages:,} messages")
            case ExportPhase.EXPORTING:
                total = max(progress.total_messages, 1)
                self.progress_bar.setRange(0, total)
                self.progress_status_label.setText(
                    f"Exporting {progress.current_index:,} of {progress.total_messages:,}"
                )
            case ExportPhase.RETRYING:
                self.progress_status_label.setText("Temporary Gmail error. Retrying...")
            case (
                ExportPhase.MESSAGE_COMPLETED
                | ExportPhase.MESSAGE_SKIPPED
                | ExportPhase.MESSAGE_FAILED
            ):
                total = max(progress.total_messages, 1)
                self.progress_bar.setRange(0, total)
                current = (
                    progress.completed_this_run
                    + progress.skipped_completed
                    + progress.failed_this_run
                )
                self.progress_bar.setValue(current)
                self.progress_status_label.setText(
                    f"Exporting {current:,} of {progress.total_messages:,}"
                )
                self.progress_detail_label.setText(
                    f"Saved: {progress.completed_this_run:,}  ·  "
                    f"Already saved: {progress.skipped_completed:,}  ·  "
                    f"Failed: {progress.failed_this_run:,}"
                )
            case ExportPhase.CANCELLED:
                self.progress_status_label.setText(
                    "Export cancelled safely. You can resume this folder later."
                )
            case _:
                pass

    def _on_export_completed(self, result: ExportResult) -> None:
        if result.cancelled:
            self.progress_status_label.setText(
                "Export cancelled safely. You can resume this folder later."
            )
        elif result.failed == 0:
            self.progress_status_label.setText("Export complete.")
            self.progress_bar.setRange(0, max(result.total_scanned, 1))
            self.progress_bar.setValue(result.total_scanned)
        else:
            self.progress_status_label.setText(
                f"Export completed with {result.failed:,} message(s) that could not be saved."
            )
            self.progress_bar.setRange(0, max(result.total_scanned, 1))
            self.progress_bar.setValue(
                result.completed_this_run + result.skipped_completed + result.failed
            )

        self.progress_detail_label.setText(
            f"Saved: {result.completed_this_run:,}  ·  "
            f"Already saved: {result.skipped_completed:,}  ·  "
            f"Failed: {result.failed:,}"
        )
        self._set_idle_state()

    def _on_export_failed(self, error_message: str) -> None:
        self.progress_status_label.setText("Export stopped due to an error.")
        QMessageBox.critical(self, "Export Error", f"Export failed:\n{error_message}")
        self._set_idle_state()

    def _on_export_thread_finished(self) -> None:
        self._export_thread = None
        self._export_worker = None
        self._update_control_states()

    def _set_exporting_state(self) -> None:
        self.connect_button.setEnabled(False)
        self.radio_all_mail.setEnabled(False)
        self.radio_inbox.setEnabled(False)
        self.destination_edit.setEnabled(False)
        self.browse_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.open_folder_button.setEnabled(False)

    def _set_idle_state(self) -> None:
        self.connect_button.setEnabled(True)
        self.radio_all_mail.setEnabled(True)
        self.radio_inbox.setEnabled(True)
        self.destination_edit.setEnabled(True)
        self.browse_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._update_control_states()

    def _update_control_states(self) -> None:
        is_running = self._export_thread is not None and self._export_thread.isRunning()
        if is_running:
            return

        is_connected = self.gmail_client is not None
        has_destination = bool(self.destination_path and self.destination_path.strip())
        dest_exists = Path(self.destination_path).exists() if has_destination else False

        self.start_button.setEnabled(is_connected and has_destination)
        self.cancel_button.setEnabled(False)
        self.open_folder_button.setEnabled(dest_exists)
