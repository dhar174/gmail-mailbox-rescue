# Gmail Mailbox Rescue

A small, local-first desktop application for preserving a Google/Gmail mailbox before an account is deactivated.

The core promise is intentionally boring and important:

**Google → your computer. Nothing in between.**

Mailbox Rescue does not need a hosted backend and is designed so OAuth tokens and exported email stay on the user's own machine.

## Project status

Early MVP development. See [Issue #1](https://github.com/dhar174/gmail-mailbox-rescue/issues/1) for the MVP roadmap.

The initial target is:

1. Authenticate with Google using the installed-app OAuth flow.
2. Read Gmail with the minimum read-only scope needed for export.
3. Enumerate messages with pagination.
4. Fetch the original RFC email source.
5. Save each message locally as an `.eml` file.
6. Record completed exports in SQLite so an interrupted run can resume.
7. Add MBOX, labels, reporting, packaging, and UI polish after the core path is reliable.

## Development setup

Python 3.11+ is required.

```powershell
git clone https://github.com/dhar174/gmail-mailbox-rescue.git
cd gmail-mailbox-rescue

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

mailbox-rescue
```

## Development OAuth configuration

Create a Google Cloud OAuth **Desktop app** client with the Gmail API enabled and download its client configuration JSON.

Do **not** commit that file.

Either place it in the repository root as:

```text
client_secret.json
```

or point the application at it:

```powershell
$env:MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS = "C:\path\to\client_secret.json"
mailbox-rescue
```

The application stores its token in the platform-specific user data directory, not in the repository.

For development, this client can be replaced later with an OAuth client approved by a Google Workspace administrator. The export engine is deliberately independent of that deployment decision.

## Security model

- Gmail access is read-only.
- User credentials are entered only into Google's OAuth pages.
- No mailbox data is uploaded to a Mailbox Rescue server.
- OAuth client configuration and user tokens are excluded from Git.
- Exported messages remain local unless the user moves them elsewhere.

## Planned archive layout

```text
Mailbox-Backup/
├── messages/
│   ├── <gmail-message-id>.eml
│   └── ...
├── export.sqlite3
├── mailbox.mbox          # later milestone
├── metadata/             # later milestone
└── export-report.html    # later milestone
```

## Running tests

```powershell
pytest
```
