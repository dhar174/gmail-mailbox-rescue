# Gmail Mailbox Rescue

A small, local-first desktop application for preserving a Google/Gmail mailbox before an account is deactivated.

The core promise is intentionally boring and important:

**Google → your computer. Nothing in between.**

Mailbox Rescue does not need a hosted backend and is designed so OAuth tokens and exported email stay on the user's own machine.

---

## For Coworkers & End Users

No Python or command-line tools are required to use Mailbox Rescue.

### 1. Quick Start
1. **Download & Extract**: Download the Windows ZIP (`Mailbox-Rescue-v0.1.0-win64.zip`) or the Mac ZIP for your processor (`Mailbox-Rescue-v0.1.0-macos-arm64.zip` for Apple Silicon). Extract it to a convenient folder.
2. **Verify Configuration**: Keep the provided `client_secret.json` directly beside `Mailbox Rescue.exe` (Windows) or `Mailbox Rescue.app` (Mac). Keep the whole extracted folder together; do not move only the Mac app to Applications. If the JSON file is missing, ask the person providing the app for the approved configuration.
3. **Launch the App**: Double-click `Mailbox Rescue.exe` or `Mailbox Rescue.app`.
   > *Note on Windows SmartScreen*: Since this MVP build is currently unsigned, Windows may display a "Windows protected your PC" notification. Click **More info** and then **Run anyway**.
   > *Mac pilot*: The app is not Apple-notarized. If a trusted copy is blocked as an unverified developer, open **System Settings → Privacy & Security → Open Anyway**, then confirm **Open**. If your organization blocks this, contact IT. See [Apple's opening instructions](https://support.apple.com/en-gb/102445).
4. **Connect Google Account**: Click **Connect Google Account**. Your default web browser will open to Google's sign-in page. Log in with your work account and grant read-only access.
5. **Choose Scope & Folder**:
   - Select **All Mail** (recommended) or **Inbox only**.
   - Click **Browse...** to select an empty destination folder. Ensure the chosen drive has sufficient free disk space for your mailbox.
6. **Start Export**: Click **Start Export**, keep your computer awake, and leave the application running until it reports completion and integrity verification. Keep the entire backup folder, including its checkpoint and metadata.

### 2. Resuming Interrupted Exports
If your computer sleeps, restarts, or you close the app during export:
1. Reopen `Mailbox Rescue.exe` (Windows) or `Mailbox Rescue.app` (Mac).
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

The application stores user tokens via `platformdirs.user_data_path()`: normally `%LOCALAPPDATA%\Mailbox Rescue\Mailbox Rescue\google_token.json` on Windows and `~/Library/Application Support/Mailbox Rescue/google_token.json` on macOS. Tokens remain outside the app, distribution, and backup destination; POSIX token files retain mode `0600` where supported. Do not choose the application-data folder as an export destination.

OAuth configuration precedence is the `MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS` override, then the packaged sidecar location, or the development working directory when not frozen. Frozen macOS discovery walks executable ancestors to the nearest `.app` and uses its parent; frozen Windows and executables without an `.app` use the executable directory. A missing packaged sidecar does not fall back to a working-directory credential. The only Gmail scope remains `gmail.readonly`.

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

See [RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for full pre-release smoke testing steps.

### Building the macOS Pilot

On a native Mac, first validate the source application:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -v
python -m ruff check .
mailbox-rescue
```

Confirm the GUI and a small development export before investigating packaging problems.
Use an approved Desktop OAuth client stored outside the repository.

For Intel builds, install the current `cryptography` dependency with static OpenSSL
linking before packaging. Its upstream macOS wheels now target Apple Silicon;
an ordinary Intel source install can conflict with Python's OpenSSL in the bundle.
With Xcode command-line tools, Homebrew OpenSSL, and Rust available, run this in
the activated virtual environment (also used by the Intel CI job):

```bash
env OPENSSL_STATIC=1 OPENSSL_DIR="$(brew --prefix openssl@3)" \
  python -m pip install --force-reinstall --no-cache-dir --no-binary cryptography cryptography
```

See the [cryptography macOS build instructions](https://cryptography.io/en/latest/installation/#building-cryptography-on-macos).
These tools are only needed on the build machine; the tester needs only the ZIP.

```bash
# Generic build: zero OAuth client files
./scripts/build-macos.sh --clean

# Private configured pilot: exactly one validated sidecar
./scripts/build-macos.sh --clean \
  --oauth-client-config "/secure/path/client_secret.json"
```

The script checks macOS, native Python 3.11+, PyInstaller, and architecture; rejects Rosetta; builds the same exporter through `packaging/mailbox-rescue-macos.spec`; stages instructions; validates the optional Desktop OAuth sidecar; and checks release hygiene in the staging folder, ZIP, and a fresh extraction. `ditto` preserves the bundle's framework symlinks. It also verifies the extracted executable's permissions and ad-hoc code signature. An ad-hoc signature does **not** mean Apple Developer signing or notarization.

Outputs live under `dist/macos/`, separate from Windows artifacts. `--clean` only removes `build/macos/` and `dist/macos/`. The script prints version, source SHA, macOS version, architecture, ZIP path, and byte size. A configured build produces:

```text
dist/macos/releases/Mailbox-Rescue-v0.1.0-macos-arm64.zip
└── Mailbox Rescue/
    ├── Mailbox Rescue.app
    ├── client_secret.json
    └── START HERE.txt
```

Intel native builds use `macos-x86_64` in the name. CI builds Apple Silicon and Intel separately on `macos-15` and `macos-15-intel`; a physical-Mac field test is still required for the recipient's machine and account. Generic ZIPs omit `client_secret.json`. Keep approved configured ZIPs private. Tokens, mail, and export/checkpoint data are never release inputs.

The macOS CI jobs run the full suite and Ruff on both architectures, initialize the source Qt UI offscreen, build the `.app`, and upload generic versioned ZIPs as the `Mailbox-Rescue-macos-arm64` and `Mailbox-Rescue-macos-x86_64` workflow artifacts. Each job also extracts its ZIP into a path with spaces, opens the actual app through macOS Launch Services with the native UI, captures its onscreen window, and checks normal quit. The disconnected window screenshots are uploaded separately for inspection. Download and extract the ZIP artifact wrapper to obtain the distribution ZIP. A maintainer must provide the approved sidecar separately or create a private configured build before coworker sign-in.

**REAL MACOS PACKAGED SMOKE TEST: NOT PERFORMED.** CI does not qualify Finder launch, live OAuth, real export, cancellation, or resume. Follow [MACOS_SMOKE_TEST.md](MACOS_SMOKE_TEST.md) on a physical Mac; Issue #20 remains open until those checks pass. No DMG, installer, Universal2, Developer ID signing, or notarization is required for this pilot.
