# Storage & Atomic I/O Rules

## Atomic File Exports
1. **Two-Stage Writes**:
   - Always write raw message bytes to a `.part` temporary file (e.g., `<message_id>.eml.part`) first.
   - Atomically rename/replace the temporary file to its target destination (`<message_id>.eml`) only after all bytes are flushed and written.
2. **Integrity Checksums**:
   - Compute and record SHA-256 digests and file byte lengths for every exported message.

## Checkpoint & SQLite State
1. **Idempotent Updates**:
   - Checkpoint operations must use `ON CONFLICT(...) DO UPDATE` upserts to allow safe resumption of interrupted runs without duplicates or errors.
2. **Transaction Scoping**:
   - Wrap multi-statement database operations in context managers (`with connection:`) to ensure ACID atomicity and automatic rollbacks on failure.
3. **Database File Naming**:
   - Checkpoint databases should be named `export.sqlite3` placed directly in the export output directory.
