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
    monkeypatch.setattr(sys, "platform", "win32")

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


@pytest.mark.parametrize(
    "relative_executable",
    [
        "Mailbox Rescue.app/Contents/MacOS/Mailbox Rescue",
        "Mailbox Rescue.app/Contents/Helpers/nested/Mailbox Rescue",
        "Mailbox Rescue.APP/Contents/MacOS/Mailbox Rescue",
    ],
)
def test_frozen_macos_discovers_app_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative_executable: str
) -> None:
    distribution = tmp_path / "Folder With Spaces"
    monkeypatch.delenv("MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(distribution / relative_executable))
    monkeypatch.chdir(tmp_path)
    # Missing sidecar must still resolve here, never to a development credential.
    (tmp_path / "client_secret.json").write_text("not a packaged credential")
    assert resolve_client_secrets_path() == distribution / "client_secret.json"


def test_frozen_macos_without_app_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "app.backup" / "exporter"))
    assert resolve_client_secrets_path() == tmp_path / "app.backup" / "client_secret.json"


def test_macos_override_and_token_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from platformdirs.macos import MacOS

    from mailbox_rescue import config

    monkeypatch.setenv("HOME", str(tmp_path / "Home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "Home"))
    data_dir = MacOS(APP_NAME, APP_AUTHOR).user_data_path
    monkeypatch.setattr(config, "user_data_path", lambda *args: data_dir)
    distribution = tmp_path / "Distribution"
    export_dir = tmp_path / "Backup"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        sys, "executable",
        str(distribution / "Mailbox Rescue.app/Contents/MacOS/Mailbox Rescue"),
    )
    override = tmp_path / "Secure" / "missing-client.json"
    monkeypatch.setenv("MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS", str(override))
    paths = AppPaths.discover()
    assert paths.client_secrets_file == override.resolve()
    assert paths.data_dir == data_dir
    assert paths.token_file == data_dir / "google_token.json"
    assert not paths.token_file.is_relative_to(distribution)
    assert not paths.token_file.is_relative_to(export_dir)
    assert not paths.token_file.is_relative_to(override.parent)
