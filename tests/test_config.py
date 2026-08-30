from __future__ import annotations

import sys
from pathlib import Path

import pytest
from platformdirs import user_data_path

from mailbox_rescue.config import APP_AUTHOR, APP_NAME, AppPaths, resolve_client_secrets_path


def test_resolve_client_secrets_path_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    custom_secret = tmp_path / "custom_client.json"
    monkeypatch.setenv("MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS", str(custom_secret))

    # Even if frozen is simulated, env var takes precedence
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app" / "Mailbox Rescue.exe"))

    resolved = resolve_client_secrets_path()
    assert resolved == custom_secret.resolve()


def test_resolve_client_secrets_path_frozen_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS", raising=False)
    app_dir = tmp_path / "Packaged App"
    app_dir.mkdir(parents=True)
    exe_path = app_dir / "Mailbox Rescue.exe"

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_path))

    resolved = resolve_client_secrets_path()
    assert resolved == app_dir / "client_secret.json"


def test_resolve_client_secrets_path_dev_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS", raising=False)
    if hasattr(sys, "frozen"):
        monkeypatch.delattr(sys, "frozen")

    monkeypatch.chdir(tmp_path)
    resolved = resolve_client_secrets_path()
    assert resolved == tmp_path / "client_secret.json"


def test_app_paths_discover_preserves_user_data_directory_when_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Simulate running in frozen mode from a portable directory with spaces
    portable_dir = tmp_path / "Portable App Folder"
    portable_dir.mkdir(parents=True)
    exe_path = portable_dir / "Mailbox Rescue.exe"

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_path))
    monkeypatch.delenv("MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS", raising=False)

    paths = AppPaths.discover()

    expected_data_dir = Path(user_data_path(APP_NAME, APP_AUTHOR))
    assert paths.data_dir == expected_data_dir
    assert paths.token_file == expected_data_dir / "google_token.json"
    # Token file must NEVER be located in the application executable directory
    assert paths.token_file.parent != portable_dir
    assert paths.client_secrets_file == portable_dir / "client_secret.json"
