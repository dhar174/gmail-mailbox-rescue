# Gmail Mailbox Rescue

A small, local-first desktop application for preserving a Google/Gmail mailbox before an account is deactivated.

The core promise is intentionally boring and important:

**Google → your computer. Nothing in between.**

Mailbox Rescue does not need a hosted backend and is designed so OAuth tokens and exported email stay on the user's own machine.

---

## For Coworkers & End Users

No Python or command-line tools are required to use Mailbox Rescue.

### 1. Quick Start
1. **Download & Extract**: Download `Mailbox-Rescue-v0.1.0-win64.zip` and extract it to any convenient folder (e.g. Desktop or Downloads).
2. **Verify Configuration**: Ensure the provided `client_secret.json` configuration file is placed directly beside `Mailbox Rescue.exe` in the extracted folder.
3. **Launch the App**: Double-click `Mailbox Rescue.exe`.
   > *Note on Windows SmartScreen*: Since this MVP build is currently unsigned, Windows may display a "Windows protected your PC" notification. Click **More info** and then **Run anyway**.
4. **Connect Google Account**: Click **Connect Google Account**. Your default web browser will open to Google's sign-in page. Log in with your work account and grant read-only access.
5. **Choose Scope & Folder**:
   - Select **All Mail** (recommended) or **Inbox only**.
   - Click **Browse...** to select an empty destination folder. Ensure the chosen drive has sufficient free disk space for your mailbox.
6. **Start Export**: Click **Start Export** and leave the application running until it reports completion and integrity verification.

### 2. Resuming Interrupted Exports
If your computer sleeps, restarts, or you close the app during export:
1. Reopen `Mailbox Rescue.exe`.
2. Connect your account.
3. Select the same scope and the **same destination backup folder**.
4. Click **Start Export**. Mailbox Rescue automatically detects the existing `export.sqlite3` checkpoint and resumes without re-downloading already verified messages.

---

## Archive Layout

The completed archive produced by Mailbox Rescue is structured as a portable, self-describing, and verified backup:

```text
Mailbox-Backup/
├── export.sqlite3        # SQLite checkpoint database (required for safe resume)
├── messages/             # Canonical preserved raw RFC 822 email files
│   ├── <safe-id>.eml
│   └── ...
├── mailbox.mbox          # Standard MBOX format (portable convenience)
├── checksums.sha256      # SHA-256 manifest verifying EML file integrity
├── metadata/             # Portable JSON sidecar metadata
│   ├── account.json      # Account and scope identity snapshot
│   ├── labels.json       # Gmail label snapshot (system and user labels)
│   └── messages.jsonl    # Line-delimited message IDs, thread IDs, labels, and hashes
└── export-report.html    # Standalone HTML summary report with verification status
```

### Key Archive Principles
- **Canonical EMLs**: Individual `.eml` files contain the exact, unmodified raw bytes received from Gmail's API.
- **MBOX Portability**: `mailbox.mbox` is regenerated from the canonical verified EML files for compatibility with standard email clients.
- **Integrity Verification**: `checksums.sha256` and automatic post-export verification check that every saved file is intact and unmodified.
- **Preserved Metadata**: The `metadata/` directory preserves Gmail thread relationships, label assignments, and account details in standard JSON formats without bloating message files.
- **Self-Healing Resume**: `export.sqlite3` tracks completed progress, validates existing files on resume, and repairs corrupted or missing messages.

---

## For Developers & Maintainers

### Development Setup
Python 3.11+ is required.

```powershell
git clone https://github.com/dhar174/gmail-mailbox-rescue.git
cd gmail-mailbox-rescue

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

mailbox-rescue
```

### Development OAuth Configuration
Create a Google Cloud OAuth **Desktop app** client with the Gmail API enabled and download its client configuration JSON.

Either place it in the working directory as `client_secret.json` or configure the environment variable:

```powershell
$env:MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS = "C:\path\to\client_secret.json"
mailbox-rescue
```

The application stores user tokens in `%LOCALAPPDATA%\Mailbox Rescue\Mailbox Rescue\google_token.json` via `platformdirs`, completely separate from the application code or repository.

### Running Tests and Linting
```powershell
ruff check .
pytest -v
```

### Building the Windows Release Package
To compile the standalone Windows GUI executable and assemble the release ZIP:

```powershell
# Clean build of standalone one-folder distribution and release ZIP
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1 -Clean
```

To assemble a pilot distribution with an intentionally staged sidecar OAuth client configuration:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build-windows.ps1 -Clean -OAuthClientConfig "C:\secure\client_secret.json"
```

The build script will:
1. Compile `dist\Mailbox Rescue\Mailbox Rescue.exe` using PyInstaller (`packaging\mailbox-rescue.spec`) in windowed GUI mode (`console=False`).
2. Copy `START HERE.txt` into the application directory.
3. Verify release hygiene with `scripts\verify_release_hygiene.py`, ensuring zero token or export data leakage.
4. Compress the folder into `dist\releases\Mailbox-Rescue-v0.1.0-win64.zip`.
5. Run release hygiene checks on the generated ZIP.

See [RELEASE_CHECKLIST.md](file:///d:/projects/gmail-mailbox-rescue/RELEASE_CHECKLIST.md) for full pre-release smoke testing steps.
