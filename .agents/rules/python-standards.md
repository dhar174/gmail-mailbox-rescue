# Python & Code Quality Standards

## Python 3.11+ Idioms
1. **Typing & Annotations**:
   - Use `from __future__ import annotations` at the top of every module.
   - Use built-in generics (e.g., `list[str]`, `dict[str, Any]`, `str | None`) rather than `typing.List`, `typing.Dict`, or `typing.Optional`.
   - Explicitly annotate function arguments and return types.
2. **Data Modeling**:
   - Prefer immutable dataclasses with `@dataclass(frozen=True, slots=True)` for domain models, configuration, and transfer objects.
3. **Tooling & Formatting**:
   - Adhere to Ruff linting and formatting configuration (`line-length = 100`, `target-version = "py311"`).
   - Ensure clean imports and zero unreferenced variables or debug statements.
