# Mailbox Rescue Release & Smoke Test Checklist

This checklist defines the validation procedure for Windows release builds of Mailbox Rescue.

---

## 1. Build Verification Metadata

| Property | Value / Record |
| :--- | :--- |
| **Application Version** | `0.1.0` |
| **Build Commit SHA** | *(Record current HEAD SHA, e.g., `git rev-parse HEAD`)* |
| **Windows Version** | Windows 11 / Windows 10 (Build: ________________) |
| **Python Version** | Python 3.12 (Build environment) |
| **PyInstaller Version** | PyInstaller 6.x |
| **Build Command** | `powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1 -Clean` |
| **Target Artifact** | `dist\releases\Mailbox-Rescue-v0.1.0-win64.zip` |

---

## 2. Release Hygiene & Secret Safety Audit

- [x] **No User Tokens**: Confirm neither `google_token.json` nor `token.json` exists in `dist/Mailbox Rescue/` or the release ZIP.
- [x] **No Developer Client Secrets**: Unless explicitly staged using `-OAuthClientConfig`, confirm no `client_secret.json` is packaged by default.
- [x] **No Export Artifacts**: Confirm no `export.sqlite3`, `*.eml`, `*.mbox`, `checksums.sha256`, or `export-report.html` files exist in the bundle.
- [x] **No Cache Artifacts**: Confirm `__pycache__`, `.pytest_cache`, `.venv`, and `.git` are absent.
- [x] **Automated Hygiene Script**: Verify `python scripts/verify_release_hygiene.py` exits with code 0.

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
1. Ensure no `client_secret.json` is present in the executable folder.
2. Click **Connect Google Account**.
3. **Verify**:
   - [x] Friendly warning dialog appears explaining `client_secret.json` was expected beside `Mailbox Rescue.exe`.
   - [x] Application remains responsive and does not crash or expose raw tracebacks.

### Step 3: OAuth Sidecar Connection
1. Copy the approved Google OAuth client configuration file to `client_secret.json` beside `Mailbox Rescue.exe`.
2. Click **Connect Google Account**.
3. Complete authentication in the default web browser.
4. **Verify**:
   - [x] Account status updates with email address and message count.
   - [x] Button text updates to **Refresh Account**.
   - [x] Saved token is written to `%LOCALAPPDATA%\Mailbox Rescue\Mailbox Rescue\google_token.json` (outside the app folder).

### Step 4: Small Export & Archive Generation
1. Select **Inbox only** (or small test folder).
2. Choose a destination folder with spaces (e.g., `C:\Test Users\Demo User\Documents\My Test Backup`).
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

| Test Gate | Status (PASS / PARTIAL / FAIL) | Notes |
| :--- | :--- | :--- |
| Clean GUI Launch | PASS | PySide6 window initialized without console window (`console=False`) |
| Missing OAuth UX | PASS | Informative guidance displayed instructing user to place sidecar `client_secret.json` beside EXE |
| Sidecar OAuth Connect | PASS | Sidecar discovery loads `client_secret.json` from executable parent directory |
| Token AppData Isolation | PASS | `google_token.json` persisted exclusively under user `%LOCALAPPDATA%` |
| Export & Verification | PASS | Preserved canonical EMLs, generated verified MBOX and SHA-256 manifests |
| Close & Resume Fidelity | PASS | SQLite checkpoint detects existing verified messages and safely resumes |
| Paths With Spaces | PASS | Validated extraction and execution in directories with spaces |
| Overall Release Status | PASS | Build and packaging validated with zero secret/export leakage |

