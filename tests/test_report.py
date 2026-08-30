from pathlib import Path

from mailbox_rescue.export.models import ExportFailure, ExportResult, VerificationFailure
from mailbox_rescue.export.report import generate_html_report
from mailbox_rescue.storage.checkpoint import ExportMetadata


def test_generate_html_report_verified_complete(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()

    meta = ExportMetadata(
        account_email="alice@example.com",
        export_scope="all_mail",
        created_at="2026-08-28T12:00:00+00:00",
        last_updated_at="2026-08-28T12:00:00+00:00",
    )
    result = ExportResult(
        total_scanned=10,
        completed_this_run=8,
        skipped_completed=2,
        failed=0,
        cancelled=False,
        archive_verified=True,
        verified_files=10,
    )

    report_path = generate_html_report(root, result, meta, total_canonical_emls=10)
    assert report_path.exists()
    assert not (root / "export-report.html.part").exists()

    html_content = report_path.read_text(encoding="utf-8")
    assert "VERIFIED COMPLETE" in html_content
    assert "alice@example.com" in html_content
    assert "All Mail" in html_content
    assert "PASSED" in html_content
    assert "10" in html_content


def test_generate_html_report_partial_export_with_failures(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()

    meta = ExportMetadata(
        account_email="bob@example.com",
        export_scope="inbox",
        created_at="2026-08-28T12:00:00+00:00",
        last_updated_at="2026-08-28T12:00:00+00:00",
    )
    result = ExportResult(
        total_scanned=5,
        completed_this_run=3,
        skipped_completed=0,
        failed=2,
        cancelled=False,
        archive_verified=True,
        verified_files=3,
        failures=[
            ExportFailure(
                message_id="msg_fail_1",
                error_type="HttpError",
                error_message="404 Not Found",
                attempt_count=1,
            ),
            ExportFailure(
                message_id="msg_fail_2",
                error_type="HttpError",
                error_message="404 Not Found",
                attempt_count=1,
            ),
        ],
    )

    report_path = generate_html_report(root, result, meta, total_canonical_emls=3)
    html_content = report_path.read_text(encoding="utf-8")

    assert "PARTIAL EXPORT" in html_content
    assert "msg_fail_1" in html_content
    assert "msg_fail_2" in html_content
    assert "404 Not Found" in html_content


def test_generate_html_report_verification_failed(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()

    meta = ExportMetadata(
        account_email="charlie@example.com",
        export_scope="all_mail",
        created_at="2026-08-28T12:00:00+00:00",
        last_updated_at="2026-08-28T12:00:00+00:00",
    )
    result = ExportResult(
        total_scanned=5,
        completed_this_run=5,
        skipped_completed=0,
        failed=0,
        cancelled=False,
        archive_verified=False,
        verified_files=4,
        verification_failures=[
            VerificationFailure(
                message_id="msg_corrupt",
                relative_path="messages/msg_corrupt.eml",
                reason="sha256_mismatch",
            )
        ],
    )

    report_path = generate_html_report(root, result, meta, total_canonical_emls=5)
    html_content = report_path.read_text(encoding="utf-8")

    assert "VERIFICATION FAILED" in html_content
    assert "VERIFIED COMPLETE" not in html_content
    assert "msg_corrupt" in html_content
    assert "messages/msg_corrupt.eml" in html_content
    assert "sha256_mismatch" in html_content


def test_generate_html_report_escapes_dynamic_html_content(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()

    meta = ExportMetadata(
        account_email="<script>alert('xss')</script>@example.com",
        export_scope="all_mail",
        created_at="2026-08-28T12:00:00+00:00",
        last_updated_at="2026-08-28T12:00:00+00:00",
    )
    result = ExportResult(
        total_scanned=1,
        completed_this_run=0,
        skipped_completed=0,
        failed=1,
        cancelled=False,
        archive_verified=True,
        verified_files=0,
        failures=[
            ExportFailure(
                message_id="<msg_id_special_chars>",
                error_type="Error & Co",
                error_message='Failed with "quoted text" & <tags>',
                attempt_count=1,
            )
        ],
        metadata_warnings=['Warning with <b>bold</b> & "quotes"'],
    )

    report_path = generate_html_report(root, result, meta, total_canonical_emls=0)
    html_content = report_path.read_text(encoding="utf-8")

    assert "<script>" not in html_content
    assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;@example.com" in html_content
    assert "&lt;msg_id_special_chars&gt;" in html_content
    assert "Error &amp; Co" in html_content
    assert "Failed with &quot;quoted text&quot; &amp; &lt;tags&gt;" in html_content
    assert "Warning with &lt;b&gt;bold&lt;/b&gt; &amp; &quot;quotes&quot;" in html_content


def test_generate_html_report_verified_with_metadata_warnings(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()

    meta = ExportMetadata(
        account_email="dan@example.com",
        export_scope="all_mail",
        created_at="2026-08-28T12:00:00+00:00",
        last_updated_at="2026-08-28T12:00:00+00:00",
    )
    result = ExportResult(
        total_scanned=5,
        completed_this_run=5,
        skipped_completed=0,
        failed=0,
        cancelled=False,
        archive_verified=True,
        verified_files=5,
        metadata_warnings=["Could not backfill metadata for message 'msg_1'."],
    )

    report_path = generate_html_report(root, result, meta, total_canonical_emls=5)
    html_content = report_path.read_text(encoding="utf-8")

    assert "VERIFIED WITH METADATA WARNINGS" in html_content
    assert "Could not backfill metadata for message &#x27;msg_1&#x27;." in html_content
