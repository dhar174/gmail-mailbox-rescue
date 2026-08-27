# Agent Guidelines & Skill Activation Guide

## Core Directive: Check Skills on Every Turn
Before executing tasks or writing code, **always check whether one or more installed skills in `.agents/skills/` apply**. Proactively read and follow the relevant `SKILL.md` file whenever the task touches that capability area.

---

## Installed Skills & Activation Triggers

| Skill | When to Activate for this Repository |
| :--- | :--- |
| **`auth-implementation-patterns`** | Modifying OAuth 2.0 flows (`google_oauth.py`), token storage, token refresh lifecycles, or credentials management. |
| **`backend-security-coder`** | Implementing API error handling, input validation, defensive boundary checks, or handling sensitive credentials/tokens. |
| **`code-review-checklist`** | Conducting pre-commit, PR, or diff reviews to verify functionality, security, performance, and maintainability. |
| **`database`** | Modifying SQLite schemas, `CheckpointStore` (`checkpoint.py`), query optimizations, database migrations, or transaction integrity. |
| **`file-path-traversal`** | Working on local file writing, `safe_message_id` (`eml.py`), path resolution, or preventing directory traversal during file exports. |
| **`git-workflow-and-versioning`** | Creating branches, structuring commits with conventional commit messages, handling merges, or resolving conflicts. |
| **`gmail-automation`** | Implementing or updating Gmail API queries, message enumeration, raw message retrieval, and API pagination (`client.py`). |
| **`pytest-skill`** | Writing or refactoring pytest tests, creating fixtures, configuring parametrization, or setting up `conftest.py`. |
| **`python-packaging`** | Editing `pyproject.toml`, managing Hatchling build configuration, project entry points (`mailbox-rescue`), or packaging distributions. |
| **`python-patterns`** | Making architectural decisions, designing type hierarchies, structured dataclasses, and organizing component boundaries. |
| **`python-pro`** | Writing Python 3.11+ code, enforcing Ruff linting/formatting rules, optimizing performance, and applying modern Python idioms. |
| **`python-testing-patterns`** | Designing TDD workflows, test architecture, mocking external Google APIs and filesystem operations, and measuring test coverage. |
| **`repo-maintainer`** | Performing repository hygiene audits, dependency updates, pre-release checks, and CI configuration. |

---

## Architectural Principles
1. **Local-First & Zero Telemetry**: Mailbox data, tokens, and checkpoints must remain entirely on the user's machine.
2. **Atomic Writes**: Always write partial files (e.g., `.eml.part`) before atomically renaming to target destinations.
3. **Least Privilege**: Request only `https://www.googleapis.com/auth/gmail.readonly`.
