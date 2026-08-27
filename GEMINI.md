# Gemini Assistant Instructions — Gmail Mailbox Rescue

## ⚠️ Mandatory Turn-by-Turn Instruction
At the start of **every turn**, evaluate whether the current task or user request relates to any installed skill in `.agents/skills/`. If a skill is relevant, view its `SKILL.md` file and apply its best practices and workflows before taking action.

---

## Installed Skills Reference & Usage Triggers

- **`auth-implementation-patterns`** (`.agents/skills/auth-implementation-patterns/SKILL.md`): Use when configuring or modifying Google OAuth 2.0 flows, token persistence in user data directories, token refresh cycles, or authentication error handling.
- **`backend-security-coder`** (`.agents/skills/backend-security-coder/SKILL.md`): Use when implementing backend security, error isolation, defensive input validation, or credential handling.
- **`code-review-checklist`** (`.agents/skills/code-review-checklist/SKILL.md`): Use before committing code, submitting PRs, or reviewing changes for security, robustness, performance, and style.
- **`database`** (`.agents/skills/database/SKILL.md`): Use when modifying SQLite schemas, `CheckpointStore` transactions, upsert queries, indexing, or migration plans.
- **`file-path-traversal`** (`.agents/skills/file-path-traversal/SKILL.md`): Use when validating file paths, handling message IDs in filenames (`safe_message_id`), or managing export directory structure.
- **`git-workflow-and-versioning`** (`.agents/skills/git-workflow-and-versioning/SKILL.md`): Use when managing Git branches, authoring conventional commits, staging changes, or resolving merge conflicts.
- **`gmail-automation`** (`.agents/skills/gmail-automation/SKILL.md`): Use when working with Gmail API v1 calls, listing/paginating messages, fetching raw RFC 822 data, or handling API rate limits.
- **`pytest-skill`** (`.agents/skills/pytest-skill/SKILL.md`): Use when authoring unit/integration tests with `pytest`, setting up fixtures, `@pytest.mark.parametrize`, or test structure.
- **`python-packaging`** (`.agents/skills/python-packaging/SKILL.md`): Use when editing `pyproject.toml`, managing dependencies, configuring Hatchling build targets, or CLI script entrypoints.
- **`python-patterns`** (`.agents/skills/python-patterns/SKILL.md`): Use when designing software architecture, module boundaries, type hints, dataclasses, or sync/async abstractions.
- **`python-pro`** (`.agents/skills/python-pro/SKILL.md`): Use when writing Python code to enforce modern Python 3.11+ idioms, Ruff linter/formatter compliance, and high runtime performance.
- **`python-testing-patterns`** (`.agents/skills/python-testing-patterns/SKILL.md`): Use for test-driven development (TDD), mocking Google API services, verifying edge cases, and code coverage.
- **`repo-maintainer`** (`.agents/skills/repo-maintainer/SKILL.md`): Use when auditing repository health, cleaning up temporary files, managing Git hygiene, or reviewing CI readiness.

---

## Core Repository Constraints
- **Local-First**: All data (tokens, sqlite database, exported .eml messages) remains strictly on the user's local disk.
- **Atomic File Operations**: Stage files with `.eml.part` suffix and atomically rename upon completion with SHA-256 verification.
- **Minimal Scopes**: Maintain read-only Gmail access (`https://www.googleapis.com/auth/gmail.readonly`).
