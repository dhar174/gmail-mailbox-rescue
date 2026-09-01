from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from google.oauth2.credentials import Credentials

from mailbox_rescue.auth.google_oauth import GoogleOAuth, OAuthConfigurationError


def test_save_creates_final_token_and_removes_temp(tmp_path: Path) -> None:
    token_file = tmp_path / "google_token.json"
    client_secrets = tmp_path / "client_secret.json"
    oauth = GoogleOAuth(client_secrets_file=client_secrets, token_file=token_file)

    mock_credentials = MagicMock(spec=Credentials)
    mock_credentials.to_json.return_value = '{"token": "sample_secret_token_123"}'

    oauth._save(mock_credentials)

    assert token_file.is_file()
    assert token_file.read_text(encoding="utf-8") == '{"token": "sample_secret_token_123"}'

    temp_file = token_file.with_suffix(f"{token_file.suffix}.tmp")
    assert not temp_file.exists()


def test_save_atomically_replaces_existing_token(tmp_path: Path) -> None:
    token_file = tmp_path / "google_token.json"
    client_secrets = tmp_path / "client_secret.json"
    token_file.write_text('{"token": "old_token"}', encoding="utf-8")

    oauth = GoogleOAuth(client_secrets_file=client_secrets, token_file=token_file)

    mock_credentials = MagicMock(spec=Credentials)
    mock_credentials.to_json.return_value = '{"token": "new_refreshed_token"}'

    oauth._save(mock_credentials)

    assert token_file.read_text(encoding="utf-8") == '{"token": "new_refreshed_token"}'
    temp_file = token_file.with_suffix(f"{token_file.suffix}.tmp")
    assert not temp_file.exists()


def test_save_cleans_up_temporary_file_on_write_failure(tmp_path: Path) -> None:
    token_file = tmp_path / "google_token.json"
    client_secrets = tmp_path / "client_secret.json"
    oauth = GoogleOAuth(client_secrets_file=client_secrets, token_file=token_file)

    mock_credentials = MagicMock(spec=Credentials)
    mock_credentials.to_json.return_value = '{"token": "payload"}'

    with (
        patch.object(Path, "replace", side_effect=OSError("Disk write error")),
        pytest.raises(OSError, match="Disk write error"),
    ):
        oauth._save(mock_credentials)

    temp_file = token_file.with_suffix(f"{token_file.suffix}.tmp")
    assert not temp_file.exists()


def test_save_posix_permission_hardening(tmp_path: Path) -> None:
    token_file = tmp_path / "google_token.json"
    client_secrets = tmp_path / "client_secret.json"
    oauth = GoogleOAuth(client_secrets_file=client_secrets, token_file=token_file)

    mock_credentials = MagicMock(spec=Credentials)
    mock_credentials.to_json.return_value = '{"token": "posix_test_token"}'

    with (
        patch("mailbox_rescue.auth.google_oauth.os.name", "posix"),
        patch.object(Path, "chmod") as mock_chmod,
    ):
        oauth._save(mock_credentials)
        mock_chmod.assert_called_once_with(0o600)

    assert token_file.is_file()


def test_save_posix_permission_failure_does_not_abort_save(tmp_path: Path) -> None:
    token_file = tmp_path / "google_token.json"
    client_secrets = tmp_path / "client_secret.json"
    oauth = GoogleOAuth(client_secrets_file=client_secrets, token_file=token_file)

    mock_credentials = MagicMock(spec=Credentials)
    mock_credentials.to_json.return_value = '{"token": "posix_test_token"}'

    with (
        patch("mailbox_rescue.auth.google_oauth.os.name", "posix"),
        patch.object(Path, "chmod", side_effect=OSError("Operation not permitted")),
    ):
        oauth._save(mock_credentials)

    assert token_file.is_file()
    assert token_file.read_text(encoding="utf-8") == '{"token": "posix_test_token"}'


def test_authorize_missing_client_secrets_raises_friendly_error(tmp_path: Path) -> None:
    token_file = tmp_path / "google_token.json"
    client_secrets = tmp_path / "client_secret.json"
    oauth = GoogleOAuth(client_secrets_file=client_secrets, token_file=token_file)

    with pytest.raises(OAuthConfigurationError) as exc_info:
        oauth.authorize()

    message = str(exc_info.value)
    assert "Google sign-in configuration was not found" in message
    assert str(client_secrets) in message
    assert "Place the approved Google OAuth configuration file at the path shown above" in message
    assert "MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS" in message
    assert "contact them for the approved configuration file" in message


def test_authorize_missing_client_secrets_with_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailbox_rescue.config import AppPaths

    custom_secrets = tmp_path / "custom_dir" / "custom_client_secret.json"
    monkeypatch.setenv("MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS", str(custom_secrets))

    paths = AppPaths.discover()
    oauth = GoogleOAuth(client_secrets_file=paths.client_secrets_file, token_file=paths.token_file)

    with pytest.raises(OAuthConfigurationError) as exc_info:
        oauth.authorize()

    message = str(exc_info.value)
    assert "Google sign-in configuration was not found" in message
    assert str(custom_secrets.resolve()) in message
    assert "MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS" in message


