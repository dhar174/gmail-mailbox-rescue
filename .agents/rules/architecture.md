# Architecture & Local-First Rules

## Core Principles
1. **Local-First & Zero Telemetry**:
   - Mailbox data, OAuth tokens, and SQLite checkpoints must reside entirely on the user's local machine.
   - Do not add analytics, remote logging, telemetry, or external tracking services.
   - Never introduce hosted intermediary servers or third-party cloud bridges.

2. **Decoupled Engine**:
   - The export engine (`mailbox_rescue.export`, `mailbox_rescue.gmail`, `mailbox_rescue.storage`) must remain independent of the UI layer (`mailbox_rescue.ui`).
   - Core export logic must be callable headlessly or via CLI.

3. **User Paths**:
   - Local state (credentials, tokens) must use standard platform directories via `platformdirs.user_data_path` (roaming on Windows).
   - Export destination paths are always explicitly user-specified.
