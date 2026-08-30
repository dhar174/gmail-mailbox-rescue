from __future__ import annotations

import html
from datetime import UTC, datetime
from pathlib import Path

from mailbox_rescue.export.models import ExportResult
from mailbox_rescue.storage.checkpoint import ExportMetadata


def generate_html_report(
    output_root: Path,
    result: ExportResult,
    metadata: ExportMetadata | None = None,
    total_canonical_emls: int = 0,
) -> Path:
    """
    Generate a standalone self-describing HTML report for the export run.
    Writes atomically via temporary sibling .part file.
    """
    report_file = output_root / "export-report.html"
    part_file = output_root / "export-report.html.part"

    generation_time = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    account_email = metadata.account_email if metadata else "Unknown"
    if metadata is None:
        export_scope_display = "Unknown"
    elif metadata.export_scope == "all_mail":
        export_scope_display = "All Mail"
    elif metadata.export_scope == "inbox":
        export_scope_display = "Inbox only"
    else:
        export_scope_display = metadata.export_scope
    # Determine archive verification and overall status
    if not result.archive_verified or len(result.verification_failures) > 0:
        status_text = "VERIFICATION FAILED"
        status_bg = "#fce8e6"
        status_fg = "#c5221f"
        status_summary = (
            "One or more saved archive files failed integrity verification. "
            "Inspect the verification failures below."
        )
    elif result.failed > 0:
        status_text = "PARTIAL EXPORT"
        status_bg = "#fef7e0"
        status_fg = "#b06000"
        status_summary = (
            f"Export completed with {result.failed:,} message(s) that could not be retrieved from Gmail. "
            "All rescued messages were verified successfully."
        )
    else:
        status_text = "VERIFIED COMPLETE"
        status_bg = "#e6f4ea"
        status_fg = "#137333"
        status_summary = (
            "All discovered messages were successfully exported and verified with SHA-256 integrity checks."
        )

    # Render warnings section if present
    warnings_html = ""
    if result.metadata_warnings:
        warning_items = "".join(f"<li>{html.escape(w)}</li>" for w in result.metadata_warnings)
        warnings_html = f"""
        <div class="card warning-card">
            <h3>Metadata Warnings</h3>
            <ul>{warning_items}</ul>
        </div>
        """

    # Render Gmail message failures table if present
    failures_html = ""
    if result.failures:
        failure_rows = "".join(
            f"<tr><td><code>{html.escape(f.message_id)}</code></td>"
            f"<td>{html.escape(f.error_type)}</td>"
            f"<td>{html.escape(f.error_message)}</td></tr>"
            for f in result.failures
        )
        failures_html = f"""
        <div class="card">
            <h3>Failed Gmail Messages ({len(result.failures):,})</h3>
            <table>
                <thead>
                    <tr><th>Message ID</th><th>Error Type</th><th>Error Details</th></tr>
                </thead>
                <tbody>{failure_rows}</tbody>
            </table>
        </div>
        """

    # Render Verification failures table if present
    verification_failures_html = ""
    if result.verification_failures:
        v_failure_rows = "".join(
            f"<tr><td><code>{html.escape(vf.message_id)}</code></td>"
            f"<td><code>{html.escape(vf.relative_path)}</code></td>"
            f"<td>{html.escape(vf.reason)}</td></tr>"
            for vf in result.verification_failures
        )
        verification_failures_html = f"""
        <div class="card error-card">
            <h3>Integrity Verification Failures ({len(result.verification_failures):,})</h3>
            <table>
                <thead>
                    <tr><th>Message ID</th><th>Relative Path</th><th>Failure Reason</th></tr>
                </thead>
                <tbody>{v_failure_rows}</tbody>
            </table>
        </div>
        """

    verification_badge = (
        '<span class="badge badge-success">PASSED</span>'
        if (result.archive_verified and not result.verification_failures)
        else '<span class="badge badge-danger">FAILED</span>'
    )

    content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mailbox Rescue - Export Report</title>
    <style>
        :root {{
            --bg: #f8f9fa;
            --surface: #ffffff;
            --text: #202124;
            --text-secondary: #5f6368;
            --border: #dadce0;
            --font: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        }}
        body {{
            font-family: var(--font);
            background-color: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 32px 16px;
            line-height: 1.5;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
        }}
        .header {{
            margin-bottom: 24px;
        }}
        h1 {{
            margin: 0 0 8px 0;
            font-size: 24px;
        }}
        .status-banner {{
            padding: 16px 20px;
            border-radius: 8px;
            background-color: {status_bg};
            color: {status_fg};
            margin-bottom: 24px;
            border: 1px solid {status_fg}33;
        }}
        .status-title {{
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 4px;
        }}
        .card {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 20px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }}
        .warning-card {{
            border-left: 4px solid #b06000;
        }}
        .error-card {{
            border-left: 4px solid #c5221f;
        }}
        h3 {{
            margin-top: 0;
            margin-bottom: 16px;
            font-size: 16px;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
        }}
        .stat-box {{
            background: var(--bg);
            padding: 12px 16px;
            border-radius: 6px;
        }}
        .stat-label {{
            font-size: 12px;
            color: var(--text-secondary);
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .stat-value {{
            font-size: 20px;
            font-weight: 600;
            margin-top: 4px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 14px;
        }}
        th, td {{
            text-align: left;
            padding: 10px 12px;
            border-bottom: 1px solid var(--border);
        }}
        th {{
            background-color: var(--bg);
            color: var(--text-secondary);
            font-weight: 600;
        }}
        code {{
            font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
            background-color: var(--bg);
            padding: 2px 4px;
            border-radius: 4px;
            font-size: 12px;
        }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: bold;
        }}
        .badge-success {{ background-color: #e6f4ea; color: #137333; }}
        .badge-danger {{ background-color: #fce8e6; color: #c5221f; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Mailbox Rescue — Export Report</h1>
            <div style="color: var(--text-secondary); font-size: 14px;">Generated on {html.escape(generation_time)}</div>
        </div>

        <div class="status-banner">
            <div class="status-title">{html.escape(status_text)}</div>
            <div>{html.escape(status_summary)}</div>
        </div>

        <div class="card">
            <h3>Archive Summary</h3>
            <table>
                <tr><td style="width: 220px; font-weight: 600;">Account</td><td>{html.escape(account_email)}</td></tr>
                <tr><td style="font-weight: 600;">Export Scope</td><td>{html.escape(export_scope_display)}</td></tr>
                <tr><td style="font-weight: 600;">Destination Directory</td><td><code>{html.escape(str(output_root))}</code></td></tr>
                <tr><td style="font-weight: 600;">Verification Status</td><td>{verification_badge}</td></tr>
            </table>
        </div>

        <div class="card">
            <h3>Export Statistics</h3>
            <div class="grid">
                <div class="stat-box">
                    <div class="stat-label">Discovered</div>
                    <div class="stat-value">{result.total_scanned:,}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Saved This Run</div>
                    <div class="stat-value">{result.completed_this_run:,}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Already Saved (Skipped)</div>
                    <div class="stat-value">{result.skipped_completed:,}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Failed Messages</div>
                    <div class="stat-value">{result.failed:,}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Total Preserved EMLs</div>
                    <div class="stat-value">{total_canonical_emls:,}</div>
                </div>
                <div class="stat-box">
                    <div class="stat-label">Verified EML Files</div>
                    <div class="stat-value">{result.verified_files:,}</div>
                </div>
            </div>
        </div>

        {warnings_html}
        {failures_html}
        {verification_failures_html}
    </div>
</body>
</html>
"""

    part_file.write_text(content, encoding="utf-8")
    part_file.replace(report_file)
    return report_file
