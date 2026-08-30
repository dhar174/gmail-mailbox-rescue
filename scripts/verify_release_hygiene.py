from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import zipfile
from pathlib import Path

PROHIBITED_TOKEN_PATTERNS = [
    "*token*.json",
    "google_token.json",
    "token.json",
    "credentials.json",
]

PROHIBITED_MAIL_AND_DATA_PATTERNS = [
    "*.sqlite3*",
    "*.eml",
    "*.eml.part",
    "*.mbox",
    "*.mbox.part",
    "checksums.sha256",
    "checksums.sha256.part",
    "export-report.html",
    "export-report.html.part",
    "account.json",
    "account.json.part",
    "labels.json",
    "labels.json.part",
    "messages.jsonl",
    "messages.jsonl.part",
]

PROHIBITED_DEV_AND_CACHE_PATTERNS = [
    "__pycache__",
    ".pytest_cache",
    ".coverage*",
    ".git*",
    ".venv*",
]

VALID_CLIENT_SECRET_PATTERNS = [
    "client_secret.json",
    "client_secret_*.json",
    "client_secrets*.json",
]

CLIENT_SECRET_PATTERNS = [
    "client_secret*.json",
    "client_secrets*.json",
]

REQUIRED_INSTALLED_KEYS = (
    "client_id",
    "client_secret",
    "auth_uri",
    "token_uri",
    "redirect_uris",
)


def validate_oauth_client_config(path: Path) -> list[str]:
    """Validate a Google OAuth client configuration file for desktop/installed app usage.

    Returns a list of error strings if invalid, or an empty list if valid.
    Never logs secret values.
    """
    errors: list[str] = []

    if not path.is_file():
        errors.append(f"OAuth configuration file does not exist or is not a regular file: {path}")
        return errors

    # 1. Filename validation
    name_lower = path.name.lower()
    for token_pat in PROHIBITED_TOKEN_PATTERNS:
        if fnmatch.fnmatch(name_lower, token_pat.lower()):
            errors.append(
                f"Rejected filename '{path.name}': User tokens must never be used as OAuth client config."
            )
            return errors

    matches_valid_pattern = any(
        fnmatch.fnmatch(name_lower, pat.lower()) for pat in VALID_CLIENT_SECRET_PATTERNS
    )
    if not matches_valid_pattern:
        errors.append(
            f"Invalid OAuth configuration filename '{path.name}'. "
            "Filename must match 'client_secret.json', 'client_secret_*.json', or 'client_secrets*.json'."
        )
        return errors

    # 2. JSON syntax & encoding validation
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
    except UnicodeDecodeError:
        errors.append(f"Failed to read '{path.name}': File must be valid UTF-8 text.")
        return errors
    except json.JSONDecodeError as exc:
        errors.append(
            f"Invalid JSON syntax in '{path.name}': {exc.msg} (line {exc.lineno}, col {exc.colno})."
        )
        return errors

    if not isinstance(data, dict):
        errors.append(
            f"Invalid JSON structure in '{path.name}': Top-level JSON must be an object/dictionary."
        )
        return errors

    # 3. Installed/Desktop app requirement (reject 'web' or missing 'installed')
    if "web" in data:
        errors.append(
            f"Rejected OAuth configuration in '{path.name}': Found 'web' client application type. "
            "Mailbox Rescue requires a Google 'Desktop app' / 'installed' client configuration."
        )
        return errors

    if "installed" not in data:
        errors.append(
            f"Rejected OAuth configuration in '{path.name}': Missing top-level 'installed' key. "
            "File must be a Google OAuth 2.0 Client ID configuration for Desktop applications."
        )
        return errors

    installed = data["installed"]
    if not isinstance(installed, dict):
        errors.append(
            f"Invalid structure in '{path.name}': 'installed' must be an object/dictionary."
        )
        return errors

    # 4. Required fields validation
    for key in REQUIRED_INSTALLED_KEYS:
        if key not in installed:
            errors.append(
                f"Missing required key '{key}' inside 'installed' configuration in '{path.name}'."
            )
            continue

        val = installed[key]
        if key == "redirect_uris":
            if not isinstance(val, list) or len(val) == 0:
                errors.append(
                    f"Invalid 'redirect_uris' in '{path.name}': Must be a non-empty list of URIs."
                )
            elif not all(isinstance(u, str) and u.strip() for u in val):
                errors.append(
                    f"Invalid 'redirect_uris' in '{path.name}': All items must be non-empty strings."
                )
        else:
            if not isinstance(val, str) or not val.strip():
                errors.append(
                    f"Invalid value for '{key}' in '{path.name}': Must be a non-empty string."
                )

    return errors


def inspect_path(
    target: Path,
    *,
    allow_oauth_client: bool = False,
) -> list[str]:
    """Inspect a directory or ZIP archive for prohibited files.

    Returns a list of violation messages.
    """
    violations: list[str] = []

    if target.is_file() and target.suffix.lower() == ".zip":
        with zipfile.ZipFile(target, "r") as zf:
            for entry in zf.namelist():
                name = Path(entry).name
                full_rel = entry.replace("\\", "/")
                _check_item(name, full_rel, allow_oauth_client, violations)
    elif target.is_dir():
        for item in target.rglob("*"):
            rel = item.relative_to(target).as_posix()
            _check_item(item.name, rel, allow_oauth_client, violations)
    else:
        violations.append(f"Target path does not exist or is not a directory/zip: {target}")

    return violations


def _check_item(
    name: str,
    relative_path: str,
    allow_oauth_client: bool,
    violations: list[str],
) -> None:
    # 1. Check prohibited tokens (never allowed under any circumstances)
    for pat in PROHIBITED_TOKEN_PATTERNS:
        if fnmatch.fnmatch(name.lower(), pat.lower()):
            violations.append(f"Prohibited token file detected: '{relative_path}'")
            return

    # 2. Check prohibited mail / export artifacts (never allowed)
    for pat in PROHIBITED_MAIL_AND_DATA_PATTERNS:
        if fnmatch.fnmatch(name.lower(), pat.lower()):
            violations.append(f"Prohibited export/data file detected: '{relative_path}'")
            return

    # 3. Check prohibited dev/cache artifacts
    for pat in PROHIBITED_DEV_AND_CACHE_PATTERNS:
        if fnmatch.fnmatch(name.lower(), pat.lower()) or any(
            fnmatch.fnmatch(part.lower(), pat.lower()) for part in relative_path.split("/")
        ):
            violations.append(f"Prohibited development/cache artifact detected: '{relative_path}'")
            return

    # 4. Check client secrets (allowed only if explicitly enabled)
    for pat in CLIENT_SECRET_PATTERNS:
        if fnmatch.fnmatch(name.lower(), pat.lower()):
            if not allow_oauth_client:
                violations.append(
                    f"Unexpected OAuth client secrets file detected: '{relative_path}'. "
                    "Pass --allow-oauth-client only if an approved client_secret.json "
                    "was intentionally staged."
                )
            return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify release hygiene and ensure no tokens, exports, or secrets leak."
    )
    parser.add_argument("target", nargs="?", type=Path, help="Path to release folder or ZIP file")
    parser.add_argument(
        "--allow-oauth-client",
        action="store_true",
        help="Allow approved client_secret.json if intentionally staged for pilot release",
    )
    parser.add_argument(
        "--validate-oauth-client",
        type=Path,
        metavar="FILE",
        help="Validate that a specified file is a valid Google Desktop/installed client_secret.json",
    )

    args = parser.parse_args(argv)

    if args.validate_oauth_client:
        oauth_file = args.validate_oauth_client.resolve()
        errors = validate_oauth_client_config(oauth_file)
        if errors:
            print(f"OAUTH CLIENT VALIDATION FAILED for {oauth_file}:", file=sys.stderr)
            for err in errors:
                print(f"  - {err}", file=sys.stderr)
            return 1
        print(f"OAuth client configuration valid: {oauth_file}")
        return 0

    if not args.target:
        parser.print_help(sys.stderr)
        return 1

    target: Path = args.target.resolve()

    if not target.exists():
        print(f"ERROR: Target does not exist: {target}", file=sys.stderr)
        return 1

    violations = inspect_path(target, allow_oauth_client=args.allow_oauth_client)
    if violations:
        print(f"RELEASE HYGIENE VIOLATIONS FOUND ({len(violations)}):", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print(f"Release hygiene check passed for {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
