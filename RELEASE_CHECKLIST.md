# Mailbox Rescue Release & Smoke Test Checklist

This checklist defines the validation procedure and release audit record for Windows release builds of Mailbox Rescue.

---

## 1. Build Verification Metadata

| Property | Value / Record |
| :--- | :--- |
| **Application Version** | `0.1.0` |
| **Build Commit SHA** | `05e35c7caea9d5a92a0b5c77ff032182f3235722` |
| **Windows Build Environment** | Windows 11 (Build 26340, AMD64) |
| **Python Version** | Python 3.12.2 (64-bit, MSC v.1937) |
| **PyInstaller Version** | PyInstaller 6.22.2 |
| **Build Command** | `.\scripts\build-windows.ps1 -Clean -OAuthClientConfig <external Desktop OAuth JSON>` |
| **Target Artifact** | `dist\releases\Mailbox-Rescue-v0.1.0-win64.zip` (77,220,379 bytes / 73.64 MiB) |
| **Live Smoke Test Date** | 2026-09-01 |

---

## 2. Release Hygiene & Secret Safety Audit

- [x] **No User Tokens**: Neither `google_token.json` nor `token.json` exists in `dist/Mailbox Rescue/` or the release ZIP.
- [x] **No Developer Client Secrets**: Unless explicitly staged using `-OAuthClientConfig`, no `client_secret.json` is packaged by default.
- [x] **Exact-One Sidecar Rule**: When OAuth staging is enabled, exactly one validated `client_secret.json` at the root is allowed; any extra or nested secrets fail hygiene.
- [x] **No Export Artifacts**: No `export.sqlite3`, `*.eml`, `*.mbox`, `checksums.sha256`, `export-report.html`, `account.json`, `labels.json`, `messages.jsonl`, or any `.part` files exist in the bundle.
- [x] **No Cache Artifacts**: `__pycache__`, `.pytest_cache`, `.venv`, and `.git` are absent.
- [x] **Automated Hygiene Script**: `python scripts/verify_release_hygiene.py` exits with code 0.

---

## 3. Packaged Application Smoke Test Procedure

Perform these steps on a clean Windows machine, Windows Sandbox, or an isolated Windows user session:

### Step 1: Clean Extraction & Launch
1. Extract `Mailbox-Rescue-v0.1.0-win64.zip` to a test directory (e.g. `C:\Users\Test User\Desktop\Mailbox Rescue`).
2. Double-click `Mailbox Rescue.exe`.
3. **Verify**:
   - [x] Desktop GUI window opens promptly.
   - [x] No background console window is displayed (`--windowed` / `console=False` active).
   - [x] No missing DLL or `qwindows` Qt platform plugin errors occur.

### Step 2: Missing OAuth Configuration Warning
1. Ensure no `client_secret.json` is present in the executable folder and `MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS` is unset.
2. Click **Connect Google Account**.
3. **Verify**:
   - [x] Friendly warning dialog appears explaining `client_secret.json` was expected at the displayed path.
   - [x] Application remains responsive and does not crash or expose raw tracebacks.

### Step 3: OAuth Sidecar Connection
1. Copy the approved Google OAuth client configuration file to `client_secret.json` beside `Mailbox Rescue.exe`.
2. Click **Connect Google Account**.
3. Complete authentication in the default web browser.
4. **Verify**:
   - [x] Sidecar configuration discovery beside executable confirmed.
   - [x] Local HTTP loopback listener (`127.0.0.1`) and OAuth flow URL generated correctly.
   - [x] Saved token written to `%LOCALAPPDATA%\Mailbox Rescue\Mailbox Rescue\google_token.json` and confirmed outside the application, release ZIP, and export destination.

### Step 4: Small Export & Archive Generation
1. Select **Inbox only** (or small test folder).
2. Choose a destination folder with spaces (e.g., `C:\Users\Test User\Documents\Mailbox Rescue Test Backup`).
3. Click **Start Export**.
4. **Verify**:
   - [x] Progress bar updates accurately through scanning and download phases.
   - [x] Export completes with green status and verification passed.
   - [x] Clicking **Open Folder** opens the generated backup folder.
   - [x] Output contains `messages/`, `mailbox.mbox`, `export.sqlite3`, `checksums.sha256`, and `export-report.html`.

### Step 5: Close, Reopen, and Safe Resume
1. Close `Mailbox Rescue.exe`.
2. Re-launch `Mailbox Rescue.exe`.
3. Select the same account, scope, and destination directory.
4. Click **Start Export**.
5. **Verify**:
   - [x] Resume dialog confirms prior checkpoint.
   - [x] Already verified messages are skipped without duplicate download.
   - [x] Archive integrity remains verified.

---

## 4. Smoke Test Results Record

| Test Gate | Status (PASS / PENDING) | Notes |
| :--- | :--- | :--- |
| Clean GUI Launch | PASS | Tested packaged `Mailbox Rescue.exe` from extracted release ZIP in path with spaces; launched windowed without console or missing DLLs |
| Missing OAuth UX | PASS | Informative guidance displayed instructing user where to place configuration |
| Sidecar OAuth Connect | PASS | Validated the configured packaged app with a Google Desktop OAuth client, loopback consent flow, and successful live connection using a development test user. Nectar production approval remains tracked separately in Issue #7. |
| Token AppData Isolation | PASS | Live packaged authorization created the AppData token outside the application directory, release ZIP, and export destination; token contents were not inspected. |
| Export & Verification | PASS | Real Inbox-only export completed on a path with spaces: 9,133 EML files, 9,133 checkpoint completions, 0 failures, all required portable artifacts present, 9,133/9,133 manifest hashes verified, and 0 remaining `.part` files. |
| Close & Resume Fidelity | PASS | Normal close and packaged relaunch succeeded; saved authorization was reusable without a new consent flow. Resume recognized the prior checkpoint, skipped all 9,133 completed messages, downloaded 0 duplicates, retained 9,133 EML and MBOX messages, and passed verification again. The canonical EML manifest remained byte-stable; regenerated MBOX envelope timestamps are non-canonical and may change the MBOX file hash without changing message count or content. |
| Paths With Spaces | PASS | Validated extraction and execution in directories with spaces |
| Overall Release Status | PASS | Reproducible packaging, release hygiene, packaged Desktop OAuth, AppData token isolation, real Inbox export, close/reopen, saved authorization, resume, and post-resume verification all passed. |
