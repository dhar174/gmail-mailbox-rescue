# Testing & Quality Assurance Rules

## Pytest Strategy
1. **Isolated Filesystem Tests**:
   - Use pytest's built-in `tmp_path` fixture for all tests involving local file creation, SQLite databases, or path resolution. Never write to the repository root or real user data directories in tests.
2. **API & Network Isolation**:
   - Unit tests must never make real network calls or connect to live Google endpoints.
   - Mock `googleapiclient.discovery.build`, OAuth flows, and HTTP transports using standard `unittest.mock` or pytest monkeypatching.
3. **Edge Case Coverage**:
   - Test invalid message IDs, directory traversal attempts, empty streams, network timeouts, and database conflict scenarios.
