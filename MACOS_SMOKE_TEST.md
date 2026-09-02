# macOS packaged pilot qualification

REAL MACOS PACKAGED SMOKE TEST: NOT PERFORMED

Issue #20 remains open. CI can establish source test compatibility and produce a
generic arm64 ZIP; it does not demonstrate a coworker's interactive workflow.
The existing Windows qualification in RELEASE_CHECKLIST.md is separate.

## Tester record

Record the Mac model/architecture, macOS version, source SHA, ZIP filename and
SHA-256, Python/PySide6/PyInstaller build versions, tester, and date. Record only
counts, statuses, and paths; never attach tokens, OAuth JSON, email, or backup
contents to GitHub. Mark each gate PASS, FAIL, NOT TESTED, or BLOCKED with evidence.

| Gate | Status | Evidence required |
| --- | --- | --- |
| Source-mode tests and Ruff | NOT TESTED here | CI run URL and exact results |
| Source GUI launch | NOT TESTED interactively | Native window and small development export; CI offscreen initialization is separate |
| PyInstaller app and ZIP | NOT TESTED here | Build log, architecture, ZIP, hygiene and extraction results |
| Finder launch | NOT TESTED | Extract downloaded ZIP in a clean location with spaces; open app without Python or Terminal; normal GUI, folder picker, Open Folder |
| Packaged live OAuth | NOT TESTED | Approved test user, external sidecar discovered, browser opens, localhost callback succeeds, account displayed, gmail.readonly only |
| Token isolation | NOT TESTED live | Observe path selected by platformdirs (normally ~/Library/Application Support/Mailbox Rescue/google_token.json), outside app/distribution/export; mode 0600; no contents |
| Inbox export | NOT TESTED | Small real Inbox, destination with spaces, zero unexpected failures, archive verified |
| Archive and attachments | NOT TESTED | EML attachments preserved; MBOX, metadata/account.json, labels.json, messages.jsonl, checksums.sha256, export-report.html, export.sqlite3 present |
| Cancellation and close | NOT TESTED | Cancel during export; close while exporting; safe completion/cancellation and no corrupt checkpoint |
| Reopen and resume | NOT TESTED | Saved authorization reused, same account/scope/destination, checkpoint recognized, completed messages skipped, no duplicates, verification passes again |
| All Mail | NOT TESTED | Controlled All Mail run, shared archive format and verification |

## Coworker-style procedure

1. A maintainer builds on a native Mac with `./scripts/build-macos.sh --clean --oauth-client-config "/secure/path/client_secret.json"`. The Desktop OAuth client stays outside Git. Alternatively, place the approved sidecar beside the app in an extracted generic CI distribution; rerun hygiene before private redistribution.
2. Download/copy the private ZIP to a clean Mac location and extract it. Keep the app, instructions, and sidecar together. Follow START HERE.txt. For an unverified-developer warning, use the normal per-app Open Anyway approval; if unavailable under organization policy, record BLOCKED and contact IT.
3. Connect an approved Google test user. Confirm read-only consent, account display, and token location without reading token contents.
4. Export a small Inbox to a folder with spaces. Open the folder and HTML report. Verify all archive components, attachments, and successful checksums.
5. Test cancellation/close during a controlled export. Reopen, reuse authorization, select the same account, scope, and destination, and accept resume. Compare message counts and skipped/completed totals; ensure no blind duplication and successful archive verification.
6. Sanity-test All Mail in its own destination. Confirm account/scope mismatches remain rejected when attempting to reuse an incompatible destination.
7. Record results above. Keep Issue #20 open and use `Refs #20` until all required physical-Mac criteria have actually passed.
