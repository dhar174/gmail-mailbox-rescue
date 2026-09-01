from __future__ import annotations

import os
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
SCOPES = (GMAIL_READONLY_SCOPE,)


class OAuthConfigurationError(RuntimeError):
    """Raised when the local Google OAuth client configuration is missing."""


class GoogleOAuth:
    def __init__(self, client_secrets_file: Path, token_file: Path) -> None:
        self.client_secrets_file = client_secrets_file
        self.token_file = token_file

    def authorize(self) -> Credentials:
        credentials = self._load_saved_credentials()

        if credentials and credentials.valid:
            return credentials

        if credentials and credentials.expired and credentials.refresh_token:
            try:
                credentials.refresh(Request())
                self._save(credentials)
                return credentials
            except RefreshError:
                self.token_file.unlink(missing_ok=True)

        if not self.client_secrets_file.is_file():
            raise OAuthConfigurationError(
                "Google sign-in configuration was not found.\n\n"
                f"Expected:\n{self.client_secrets_file}\n\n"
                "Place the approved Google OAuth configuration file at the path shown above.\n\n"
                "If MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS is set, update or remove that "
                "environment variable if it points to the wrong location.\n\n"
                "If this copy of Mailbox Rescue was provided to you by someone else, "
                "contact them for the approved configuration file."
            )

        flow = InstalledAppFlow.from_client_secrets_file(
            str(self.client_secrets_file),
            scopes=list(SCOPES),
        )
        credentials = flow.run_local_server(
            host="127.0.0.1",
            port=0,
            open_browser=True,
            authorization_prompt_message="Opening Google sign-in in your browser...",
        )
        self._save(credentials)
        return credentials

    def sign_out(self) -> None:
        self.token_file.unlink(missing_ok=True)

    def _load_saved_credentials(self) -> Credentials | None:
        if not self.token_file.is_file():
            return None

        try:
            return Credentials.from_authorized_user_file(str(self.token_file), list(SCOPES))
        except (ValueError, OSError):
            self.token_file.unlink(missing_ok=True)
            return None

    def _save(self, credentials: Credentials) -> None:
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = self.token_file.with_suffix(f"{self.token_file.suffix}.tmp")
        try:
            temp_file.write_text(credentials.to_json(), encoding="utf-8")
            if os.name == "posix":
                try:
                    temp_file.chmod(0o600)
                except OSError:
                    pass
            temp_file.replace(self.token_file)
        except Exception:
            temp_file.unlink(missing_ok=True)
            raise
