import sys
import zipfile
from pathlib import Path

import pytest

# Ensure scripts/ directory is resolvable for importing verify_release_hygiene
SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import verify_release_hygiene as hygiene  # noqa: E402


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
    assert len(violations_allowed) == 1
    assert "Prohibited token file detected" in violations_allowed[0]


@pytest.mark.parametrize(
    "data_file",
    [
        "export.sqlite3",
        "export.sqlite3-wal",
        "msg_123.eml",
        "partial.eml.part",
        "mailbox.mbox",
        "checksums.sha256",
        "export-report.html",
        "messages.jsonl",
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


def test_hygiene_client_secrets_unintended_vs_allowed(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "Mailbox Rescue"
    bundle_dir.mkdir()
    (bundle_dir / "client_secret.json").write_text('{"installed": {}}', encoding="utf-8")

    # Without allow_oauth_client -> rejected
    violations = hygiene.inspect_path(bundle_dir, allow_oauth_client=False)
    assert len(violations) == 1
    assert "Unexpected OAuth client secrets file detected" in violations[0]

    # With allow_oauth_client -> permitted
    violations_ok = hygiene.inspect_path(bundle_dir, allow_oauth_client=True)
    assert violations_ok == []


def test_hygiene_scans_zip_archive(tmp_path: Path) -> None:
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
