from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

# Load verify_release_hygiene module directly without modifying global sys.path
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "verify_release_hygiene.py"
_spec = importlib.util.spec_from_file_location("verify_release_hygiene", SCRIPT_PATH)
assert _spec is not None
assert _spec.loader is not None
hygiene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hygiene)


def _sample_installed_oauth_dict() -> dict[str, object]:
    return {
        "installed": {
            "client_id": "test-client-id.apps.googleusercontent.com",
            "client_secret": "test-client-secret-value",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost", "http://127.0.0.1"],
        }
    }


def test_hygiene_passes_on_clean_directory(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    bundle_dir.mkdir()
    (bundle_dir / "Mailbox Rescue.exe").write_bytes(b"MZ dummy exe")
    (bundle_dir / "START HERE.txt").write_text("Hello coworker", encoding="utf-8")
    (bundle_dir / "PySide6").mkdir()
    (bundle_dir / "PySide6" / "QtCore.pyd").write_bytes(b"dummy pyd")

    violations = hygiene.inspect_path(bundle_dir, allow_oauth_client=False)
    assert violations == []


@pytest.mark.parametrize(
    "token_name",
    ["google_token.json", "token.json", "credentials.json", "my_token.json"],
)
def test_hygiene_rejects_token_files(tmp_path: Path, token_name: str) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    bundle_dir.mkdir()
    (bundle_dir / token_name).write_text("sensitive token payload", encoding="utf-8")

    violations = hygiene.inspect_path(bundle_dir, allow_oauth_client=False)
    assert len(violations) == 1
    assert "Prohibited token file detected" in violations[0]

    # Even with allow_oauth_client=True, token files are STILL prohibited
    violations_allowed = hygiene.inspect_path(bundle_dir, allow_oauth_client=True)
    assert any("Prohibited token file detected" in v for v in violations_allowed)


@pytest.mark.parametrize(
    "data_file",
    [
        "export.sqlite3",
        "export.sqlite3-wal",
        "export.sqlite3-shm",
        "export.sqlite3-journal",
        "msg_123.eml",
        "partial.eml.part",
        "mailbox.mbox",
        "mailbox.mbox.part",
        "checksums.sha256",
        "checksums.sha256.part",
        "export-report.html",
        "export-report.html.part",
        "account.json",
        "account.json.part",
        "labels.json",
        "labels.json.part",
        "messages.jsonl",
        "messages.jsonl.part",
    ],
)
def test_hygiene_rejects_mail_and_checkpoint_artifacts(tmp_path: Path, data_file: str) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    bundle_dir.mkdir()
    nested_dir = bundle_dir / "nested"
    nested_dir.mkdir()
    (nested_dir / data_file).write_text("mail or db content", encoding="utf-8")

    violations = hygiene.inspect_path(bundle_dir, allow_oauth_client=False)
    assert len(violations) == 1
    assert "Prohibited export/data file detected" in violations[0]


def test_hygiene_rejects_dev_cache_artifacts(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    pycache = bundle_dir / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "module.cpython-312.pyc").write_bytes(b"pyc")

    violations = hygiene.inspect_path(bundle_dir, allow_oauth_client=False)
    assert len(violations) >= 1
    assert any("Prohibited development/cache artifact detected" in v for v in violations)


def test_hygiene_configured_directory_requires_exactly_one_sidecar(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    bundle_dir.mkdir()
    (bundle_dir / "Mailbox Rescue.exe").write_bytes(b"MZ dummy")

    # 1. Configured directory with ZERO sidecars -> fails
    violations_zero = hygiene.inspect_path(bundle_dir, allow_oauth_client=True)
    assert len(violations_zero) == 1
    assert "Expected exactly one OAuth client configuration" in violations_zero[0]

    # 2. Configured directory with EXACTLY ONE root sidecar -> passes
    (bundle_dir / "client_secret.json").write_text(
        json.dumps(_sample_installed_oauth_dict()), encoding="utf-8"
    )
    violations_one = hygiene.inspect_path(bundle_dir, allow_oauth_client=True)
    assert violations_one == []

    # 3. Generic directory with sidecar -> fails
    violations_generic = hygiene.inspect_path(bundle_dir, allow_oauth_client=False)
    assert len(violations_generic) == 1
    assert "Unexpected OAuth client secrets file detected" in violations_generic[0]


def test_hygiene_rejects_second_root_credential(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    bundle_dir.mkdir()
    (bundle_dir / "client_secret.json").write_text(
        json.dumps(_sample_installed_oauth_dict()), encoding="utf-8"
    )
    (bundle_dir / "client_secret_old.json").write_text(
        json.dumps(_sample_installed_oauth_dict()), encoding="utf-8"
    )

    violations = hygiene.inspect_path(bundle_dir, allow_oauth_client=True)
    assert len(violations) >= 1
    assert any("Expected exactly one OAuth client configuration" in v for v in violations)


def test_hygiene_rejects_nested_credential(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    bundle_dir.mkdir()
    (bundle_dir / "client_secret.json").write_text(
        json.dumps(_sample_installed_oauth_dict()), encoding="utf-8"
    )
    nested = bundle_dir / "nested"
    nested.mkdir()
    (nested / "client_secret_dev.json").write_text(
        json.dumps(_sample_installed_oauth_dict()), encoding="utf-8"
    )

    violations = hygiene.inspect_path(bundle_dir, allow_oauth_client=True)
    assert len(violations) >= 1
    assert any("Expected exactly one OAuth client configuration" in v for v in violations)


def test_hygiene_rejects_only_nested_credential(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    nested = bundle_dir / "nested"
    nested.mkdir(parents=True)
    (nested / "client_secret.json").write_text(
        json.dumps(_sample_installed_oauth_dict()), encoding="utf-8"
    )

    violations = hygiene.inspect_path(bundle_dir, allow_oauth_client=True)
    assert len(violations) == 1
    assert "Unexpected or misplaced OAuth client secrets file detected" in violations[0]


def test_hygiene_rejects_malformed_root_config(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    bundle_dir.mkdir()
    (bundle_dir / "client_secret.json").write_text(
        json.dumps({"web": {}}), encoding="utf-8"
    )

    violations = hygiene.inspect_path(bundle_dir, allow_oauth_client=True)
    assert len(violations) >= 1
    assert any("Invalid staged OAuth client config" in v for v in violations)


def test_hygiene_zip_configured_requires_exactly_one_sidecar(tmp_path: Path) -> None:
    valid_content = json.dumps(_sample_installed_oauth_dict()).encode("utf-8")

    # 1. Configured ZIP with ZERO sidecars -> fails
    zip_empty = tmp_path / "release_empty.zip"
    with zipfile.ZipFile(zip_empty, "w") as zf:
        zf.writestr("Mailbox Rescue/Mailbox Rescue.exe", b"MZ dummy")

    violations_empty = hygiene.inspect_path(zip_empty, allow_oauth_client=True)
    assert len(violations_empty) == 1
    assert "Expected exactly one OAuth client configuration" in violations_empty[0]

    # 2. Configured ZIP with EXACTLY ONE sidecar at expected path -> passes
    zip_ok = tmp_path / "release_ok.zip"
    with zipfile.ZipFile(zip_ok, "w") as zf:
        zf.writestr("Mailbox Rescue/Mailbox Rescue.exe", b"MZ dummy")
        zf.writestr("Mailbox Rescue/client_secret.json", valid_content)

    violations_ok = hygiene.inspect_path(zip_ok, allow_oauth_client=True)
    assert violations_ok == []


def test_hygiene_zip_rejects_sidecar_at_zip_root(tmp_path: Path) -> None:
    zip_path = tmp_path / "release_root.zip"
    valid_content = json.dumps(_sample_installed_oauth_dict()).encode("utf-8")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("client_secret.json", valid_content)

    violations = hygiene.inspect_path(zip_path, allow_oauth_client=True)
    assert len(violations) == 1
    assert "Unexpected or misplaced OAuth client secrets file detected" in violations[0]


def test_hygiene_zip_rejects_multiple_app_folders_or_extra_secrets(tmp_path: Path) -> None:
    zip_path = tmp_path / "release_multi.zip"
    valid_content = json.dumps(_sample_installed_oauth_dict()).encode("utf-8")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Mailbox Rescue/Mailbox Rescue.exe", b"MZ dummy")
        zf.writestr("Mailbox Rescue/client_secret.json", valid_content)
        zf.writestr("Other Folder/client_secret.json", valid_content)

    violations = hygiene.inspect_path(zip_path, allow_oauth_client=True)
    assert len(violations) >= 1
    assert any("Expected exactly one OAuth client configuration" in v for v in violations)


def test_hygiene_zip_rejects_duplicate_entries(tmp_path: Path) -> None:
    zip_path = tmp_path / "release_dup.zip"
    valid_content = json.dumps(_sample_installed_oauth_dict()).encode("utf-8")
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Mailbox Rescue/Mailbox Rescue.exe", b"MZ dummy")
        zf.writestr("Mailbox Rescue/client_secret.json", valid_content)
        zf.writestr("Mailbox Rescue/client_secret.json", valid_content)

    violations = hygiene.inspect_path(zip_path, allow_oauth_client=True)
    assert len(violations) >= 1
    assert any("Expected exactly one OAuth client configuration" in v for v in violations)


def test_hygiene_zip_rejects_malformed_sidecar(tmp_path: Path) -> None:
    zip_path = tmp_path / "release_bad.zip"
    bad_content = b'{"web": {}}'
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Mailbox Rescue/Mailbox Rescue.exe", b"MZ dummy")
        zf.writestr("Mailbox Rescue/client_secret.json", bad_content)

    violations = hygiene.inspect_path(zip_path, allow_oauth_client=True)
    assert len(violations) >= 1
    assert any("Invalid staged OAuth client config" in v for v in violations)


def test_hygiene_scans_zip_archive_prohibited_tokens(tmp_path: Path) -> None:
    zip_path = tmp_path / "release.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("Mailbox Rescue/Mailbox Rescue.exe", b"MZ dummy")
        zf.writestr("Mailbox Rescue/google_token.json", b"token payload")

    violations = hygiene.inspect_path(zip_path, allow_oauth_client=False)
    assert len(violations) == 1
    assert "Prohibited token file detected" in violations[0]
    assert "Mailbox Rescue/google_token.json" in violations[0]


def test_hygiene_cli_main(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    bundle_dir.mkdir()
    (bundle_dir / "Mailbox Rescue.exe").write_bytes(b"MZ dummy")

    # Nonexistent target
    ret_nonexistent = hygiene.main([str(tmp_path / "does_not_exist")])
    assert ret_nonexistent == 1

    # Clean target
    ret_clean = hygiene.main([str(bundle_dir)])
    assert ret_clean == 0
    captured = capsys.readouterr()
    assert "Release hygiene check passed" in captured.out

    # Dirty target
    (bundle_dir / "google_token.json").write_text("token", encoding="utf-8")
    ret_dirty = hygiene.main([str(bundle_dir)])
    assert ret_dirty == 1
    captured_err = capsys.readouterr()
    assert "RELEASE HYGIENE VIOLATIONS FOUND" in captured_err.err


# --------------------------------------------------------------------------
# OAuth Client Configuration Validation Tests
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "client_secret.json",
        "client_secret_test.json",
        "client_secrets_prod.json",
        "CLIENT_SECRET.JSON",
    ],
)
def test_validate_oauth_client_config_valid(tmp_path: Path, filename: str) -> None:
    config_file = tmp_path / filename
    config_file.write_text(json.dumps(_sample_installed_oauth_dict()), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert errors == []


@pytest.mark.parametrize(
    "invalid_name",
    [
        "random.json",
        "notes.json",
        "foo.json",
        "google_auth.json",
    ],
)
def test_validate_oauth_client_config_invalid_filename(
    tmp_path: Path, invalid_name: str
) -> None:
    config_file = tmp_path / invalid_name
    config_file.write_text(json.dumps(_sample_installed_oauth_dict()), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert len(errors) == 1
    assert "Invalid OAuth configuration filename" in errors[0]


@pytest.mark.parametrize(
    "token_name",
    [
        "google_token.json",
        "token.json",
        "credentials.json",
        "my_token.json",
    ],
)
def test_validate_oauth_client_config_rejects_token_filenames(
    tmp_path: Path, token_name: str
) -> None:
    config_file = tmp_path / token_name
    config_file.write_text(json.dumps(_sample_installed_oauth_dict()), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert len(errors) == 1
    assert "Rejected filename" in errors[0]
    assert "User tokens must never be used" in errors[0]


def test_validate_oauth_client_config_nonexistent_or_directory(tmp_path: Path) -> None:
    missing_file = tmp_path / "client_secret.json"
    errors_missing = hygiene.validate_oauth_client_config(missing_file)
    assert len(errors_missing) == 1
    assert "does not exist or is not a regular file" in errors_missing[0]

    dir_target = tmp_path / "client_secret_dir.json"
    dir_target.mkdir()
    errors_dir = hygiene.validate_oauth_client_config(dir_target)
    assert len(errors_dir) == 1
    assert "does not exist or is not a regular file" in errors_dir[0]


def test_validate_oauth_client_config_invalid_json(tmp_path: Path) -> None:
    config_file = tmp_path / "client_secret.json"
    config_file.write_text("{ unclosed json structure", encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert len(errors) == 1
    assert "Invalid JSON syntax" in errors[0]


def test_validate_oauth_client_config_non_dict_top_level(tmp_path: Path) -> None:
    config_file = tmp_path / "client_secret.json"
    config_file.write_text('["array", "not", "dict"]', encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert len(errors) == 1
    assert "Top-level JSON must be an object/dictionary" in errors[0]


def test_validate_oauth_client_config_rejects_web_client(tmp_path: Path) -> None:
    config_file = tmp_path / "client_secret.json"
    web_config = {
        "web": {
            "client_id": "web-client.apps.googleusercontent.com",
            "client_secret": "web-secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["https://mywebapp.com/oauth2callback"],
        }
    }
    config_file.write_text(json.dumps(web_config), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert len(errors) == 1
    assert "Found 'web' client application type" in errors[0]


def test_validate_oauth_client_config_missing_installed_key(tmp_path: Path) -> None:
    config_file = tmp_path / "client_secret.json"
    config_file.write_text(json.dumps({"unknown_service": {}}), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert len(errors) == 1
    assert "Missing top-level 'installed' key" in errors[0]


@pytest.mark.parametrize(
    "missing_key",
    ["client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris"],
)
def test_validate_oauth_client_config_missing_required_keys(
    tmp_path: Path, missing_key: str
) -> None:
    config = _sample_installed_oauth_dict()
    installed = config["installed"]
    assert isinstance(installed, dict)
    del installed[missing_key]

    config_file = tmp_path / "client_secret.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert len(errors) == 1
    assert f"Missing required key '{missing_key}'" in errors[0]


def test_validate_oauth_client_config_auth_uri_validation(tmp_path: Path) -> None:
    config = _sample_installed_oauth_dict()
    installed = config["installed"]
    assert isinstance(installed, dict)

    # Attacker-controlled auth_uri
    installed["auth_uri"] = "https://evil.example/oauth"
    bad_file = tmp_path / "client_secret_bad.json"
    bad_file.write_text(json.dumps(config), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(bad_file)
    assert len(errors) == 1
    assert "Invalid 'auth_uri'" in errors[0]
    assert "Expected official Google authorization endpoint" in errors[0]

    # Valid v2 auth_uri
    installed["auth_uri"] = "https://accounts.google.com/o/oauth2/v2/auth"
    good_file = tmp_path / "client_secret.json"
    good_file.write_text(json.dumps(config), encoding="utf-8")

    assert hygiene.validate_oauth_client_config(good_file) == []


def test_validate_oauth_client_config_token_uri_validation(tmp_path: Path) -> None:
    config = _sample_installed_oauth_dict()
    installed = config["installed"]
    assert isinstance(installed, dict)

    # Attacker-controlled token_uri
    installed["token_uri"] = "https://evil.example/token"
    bad_file = tmp_path / "client_secret_bad.json"
    bad_file.write_text(json.dumps(config), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(bad_file)
    assert len(errors) == 1
    assert "Invalid 'token_uri'" in errors[0]
    assert "Expected official Google token endpoint" in errors[0]

    # Alternate valid Google token_uri
    installed["token_uri"] = "https://accounts.google.com/o/oauth2/token"
    good_file = tmp_path / "client_secret.json"
    good_file.write_text(json.dumps(config), encoding="utf-8")

    assert hygiene.validate_oauth_client_config(good_file) == []


@pytest.mark.parametrize(
    "bad_client_id",
    [
        "my-client-id",
        "client-12345.example.com",
        ".apps.googleusercontent.com",
        "   ",
    ],
)
def test_validate_oauth_client_config_rejects_non_google_client_id(
    tmp_path: Path, bad_client_id: str
) -> None:
    config = _sample_installed_oauth_dict()
    installed = config["installed"]
    assert isinstance(installed, dict)
    installed["client_id"] = bad_client_id

    config_file = tmp_path / "client_secret.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert len(errors) == 1
    assert "client_id" in errors[0]


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("client_secret", 12345),
        ("client_secret", "   "),
        ("auth_uri", None),
        ("token_uri", ""),
    ],
)
def test_validate_oauth_client_config_invalid_field_types(
    tmp_path: Path, field: str, bad_value: object
) -> None:
    config = _sample_installed_oauth_dict()
    installed = config["installed"]
    assert isinstance(installed, dict)
    installed[field] = bad_value

    config_file = tmp_path / "client_secret.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert len(errors) == 1
    assert f"Invalid value for '{field}'" in errors[0]


@pytest.mark.parametrize(
    "bad_redirect_uris",
    [
        "http://localhost",                                # string instead of list
        [],                                                # empty list
        [""],                                              # list with empty string
        ["http://localhost", "  "],                        # whitespace item
        [123],                                             # non-string in list
        ["https://evil.example/callback"],                 # remote URL
        ["https://my-web-app.example/oauth"],
        ["http://localhost.evil.example"],                 # prefix spoofing
        ["http://localhost.evil.example/callback"],
        ["http://127.0.0.1.evil.example/"],                # prefix spoofing
        ["https://localhost"],                             # https loopback rejected
        ["https://127.0.0.1:8080/"],
        ["ftp://localhost"],                               # non-http scheme
        ["urn:ietf:wg:oauth:2.0:oob"],                     # OOB no longer counts as loopback
    ],
)
def test_validate_oauth_client_config_invalid_redirect_uris(
    tmp_path: Path, bad_redirect_uris: object
) -> None:
    config = _sample_installed_oauth_dict()
    installed = config["installed"]
    assert isinstance(installed, dict)
    installed["redirect_uris"] = bad_redirect_uris

    config_file = tmp_path / "client_secret.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert len(errors) == 1
    assert "Invalid 'redirect_uris'" in errors[0]


@pytest.mark.parametrize(
    "valid_redirect_uris",
    [
        ["http://localhost"],
        ["http://localhost:12345/"],
        ["http://127.0.0.1"],
        ["http://127.0.0.1:54321/"],
        ["http://[::1]/"],
        ["http://[::1]:8765/"],
        ["http://localhost", "http://127.0.0.1"],
    ],
)
def test_validate_oauth_client_config_valid_redirect_uris(
    tmp_path: Path, valid_redirect_uris: list[str]
) -> None:
    config = _sample_installed_oauth_dict()
    installed = config["installed"]
    assert isinstance(installed, dict)
    installed["redirect_uris"] = valid_redirect_uris

    config_file = tmp_path / "client_secret.json"
    config_file.write_text(json.dumps(config), encoding="utf-8")

    errors = hygiene.validate_oauth_client_config(config_file)
    assert errors == []


def test_validate_oauth_client_config_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    valid_file = tmp_path / "client_secret.json"
    valid_file.write_text(json.dumps(_sample_installed_oauth_dict()), encoding="utf-8")

    ret_ok = hygiene.main(["--validate-oauth-client", str(valid_file)])
    assert ret_ok == 0
    captured_ok = capsys.readouterr()
    assert "OAuth client configuration valid" in captured_ok.out

    invalid_file = tmp_path / "client_secret_bad.json"
    invalid_file.write_text(json.dumps({"web": {}}), encoding="utf-8")

    ret_bad = hygiene.main(["--validate-oauth-client", str(invalid_file)])
    assert ret_bad == 1
    captured_bad = capsys.readouterr()
    assert "OAUTH CLIENT VALIDATION FAILED" in captured_bad.err
