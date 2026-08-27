# Security & Authentication Rules

## OAuth & Credential Safety
1. **Least Privilege**:
   - Request strictly `https://www.googleapis.com/auth/gmail.readonly`. Never expand scopes to read/write or admin unless explicitly approved.
2. **Secret Isolation**:
   - Never commit `client_secret.json`, `token.json`, `google_token.json`, or `.sqlite3` files.
   - Maintain appropriate exclusions in `.gitignore`.
3. **Loopback Server**:
   - The desktop OAuth flow (`InstalledAppFlow.run_local_server`) must bind strictly to `127.0.0.1` on port `0` (dynamic port selection).

## Input Validation & Path Traversal Prevention
1. **Message ID Sanitization**:
   - All message IDs or external string identifiers used in file naming must pass through `safe_message_id()`.
   - Disallow path traversal characters (`/`, `\`, `..`) and control characters.
   - Reject empty or whitespace-only sanitized IDs with a descriptive `ValueError`.
