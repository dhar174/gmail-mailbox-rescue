# Mailbox Rescue Release & Smoke Test Checklist

This checklist defines the validation procedure and release audit record for Windows release builds of Mailbox Rescue.

---

## 1. Build Verification Metadata

| Property | Value / Record |
| :--- | :--- |
| **Application Version** | `0.1.0` |
| **Build Commit SHA** | `d0c88e0a9652da79e2550f47f82160b6ceaf18e1` |
| **Windows Build Environment** | Windows 11 (Build 26340, AMD64) |
| **Python Version** | Python 3.12.2 (64-bit, MSC v.1937) |
| **PyInstaller Version** | PyInstaller 6.22.2 |
| **Build Command** | `powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1 -Clean` |
| **Target Artifact** | `dist\releases\Mailbox-Rescue-v0.1.0-win64.zip` |

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
1. Extract `Mailbox-Rescue-v0.1.0-win64.zip` to a test directory (e.g. `C:\Test Users\Demo User\Downloads\Mailbox Rescue`).
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
   - [ ] Account status updates with email address and message count.
   - [ ] Button text updates to **Refresh Account**.
   - [ ] Saved token is written to `%LOCALAPPDATA%\Mailbox Rescue\Mailbox Rescue\google_token.json` (outside the app folder).

### Step 4: Small Export & Archive Generation
1. Select **Inbox only** (or small test folder).
2. Choose a destination folder with spaces (e.g., `C:\Test Users\Demo User\Documents\My Test Backup`).
3. Click **Start Export**.
4. **Verify**:
   - [ ] Progress bar updates accurately through scanning and download phases.
   - [ ] Export completes with green status and verification passed.
   - [ ] Clicking **Open Folder** opens the generated backup folder.
   - [ ] Output contains `messages/`, `mailbox.mbox`, `export.sqlite3`, `checksums.sha256`, and `export-report.html`.

### Step 5: Close, Reopen, and Safe Resume
1. Close `Mailbox Rescue.exe`.
2. Re-launch `Mailbox Rescue.exe`.
3. Select the same account, scope, and destination directory.
4. Click **Start Export**.
5. **Verify**:
   - [ ] Resume dialog confirms prior checkpoint.
   - [ ] Already verified messages are skipped without duplicate download.
   - [ ] Archive integrity remains verified.

---

## 4. Smoke Test Results Record

| Test Gate | Status (PASS / PENDING) | Notes |
| :--- | :--- | :--- |
| Clean GUI Launch | PASS | PySide6 window initialized without console window (`console=False`) in development/build host |
| Missing OAuth UX | PASS | Informative guidance displayed instructing user where to place configuration |
| Sidecar OAuth Connect | PENDING MANUAL SMOKE TEST | Verified via unit tests; pending pre-release live sign-in gate on clean VM |
| Token AppData Isolation | PASS | `google_token.json` persisted exclusively under user `%LOCALAPPDATA%` |
| Export & Verification | PENDING MANUAL SMOKE TEST | Verified via automated test suite; pending live mailbox test on clean VM |
| Close & Resume Fidelity | PENDING MANUAL SMOKE TEST | Verified via automated test suite; pending live mailbox test on clean VM |
| Paths With Spaces | PASS | Validated extraction and execution in directories with spaces |
| Overall Release Status | PENDING MANUAL SMOKE TEST | Packaging and engineering validation PASS; manual clean VM sign-in pending before coworker distribution |
