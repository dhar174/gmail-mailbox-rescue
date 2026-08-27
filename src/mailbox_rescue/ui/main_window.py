from __future__ import annotations

from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mailbox_rescue.auth.google_oauth import GoogleOAuth, OAuthConfigurationError
from mailbox_rescue.config import AppPaths
from mailbox_rescue.gmail.client import GmailClient


class MainWindow(QMainWindow):
    def __init__(self, paths: AppPaths) -> None:
        super().__init__()
        self.paths = paths
        self.setWindowTitle("Mailbox Rescue")
        self.resize(560, 280)

        self.heading = QLabel("Preserve your Gmail mailbox locally")
        self.status = QLabel("Not connected")
        self.connect_button = QPushButton("Connect Google Account")
        self.connect_button.clicked.connect(self.connect_google)

        layout = QVBoxLayout()
        layout.addWidget(self.heading)
        layout.addWidget(self.status)
        layout.addStretch()
        layout.addWidget(self.connect_button)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

    def connect_google(self) -> None:
        self.connect_button.setEnabled(False)
        self.status.setText("Opening Google sign-in...")

        try:
            credentials = GoogleOAuth(
                client_secrets_file=self.paths.client_secrets_file,
                token_file=self.paths.token_file,
            ).authorize()
            profile = GmailClient(credentials).profile()
        except OAuthConfigurationError as exc:
            QMessageBox.warning(self, "OAuth configuration required", str(exc))
            self.status.setText("Google OAuth configuration required")
        except Exception as exc:  # UI boundary: surface unexpected API/auth failures cleanly.
            QMessageBox.critical(self, "Could not connect", str(exc))
            self.status.setText("Connection failed")
        else:
            self.status.setText(
                f"Connected: {profile.email_address}\n"
                f"{profile.messages_total:,} messages · {profile.threads_total:,} threads"
            )
            self.connect_button.setText("Refresh Account")
        finally:
            self.connect_button.setEnabled(True)
