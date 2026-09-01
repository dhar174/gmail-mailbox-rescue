from __future__ import annotations

import argparse
import fnmatch
import json
import sys
import urllib.parse
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

ALLOWED_AUTH_URIS = (
    "https://accounts.google.com/o/oauth2/auth",
    "https://accounts.google.com/o/oauth2/v2/auth",
)

ALLOWED_TOKEN_URIS = (
    "https://oauth2.googleapis.com/token",
    "https://accounts.google.com/o/oauth2/token",
)

ALLOWED_LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1"})


def is_valid_loopback_redirect(uri: str) -> bool:
    """Check if a URI is a valid local HTTP loopback redirect for desktop OAuth.

    Requires scheme == 'http' and hostname in {'localhost', '127.0.0.1', '::1'}.
    Port is optional.
    Strictly rejects remote URLs, https loopbacks, prefix-spoofed domains, and OOB URIs.
    """
    try:
        parsed = urllib.parse.urlsplit(uri.strip())
        if parsed.scheme != "http":
            return False
        return parsed.hostname in ALLOWED_LOOPBACK_HOSTNAMES
    except Exception:
        return False


def validate_oauth_client_content(content_bytes: bytes, source_name: str) -> list[str]:
    """Validate Google OAuth client configuration content for desktop/installed app usage.

    Returns a list of error strings if invalid, or an empty list if valid.
    Never logs secret values.
    """
    errors: list[str] = []

    # 1. UTF-8 & JSON parsing
    try:
        content_str = content_bytes.decode("utf-8")
        data = json.loads(content_str)
    except UnicodeDecodeError:
        errors.append(f"Failed to read '{source_name}': Content must be valid UTF-8 text.")
        return errors
    except json.JSONDecodeError as exc:
        errors.append(
            f"Invalid JSON syntax in '{source_name}': {exc.msg} (line {exc.lineno}, col {exc.colno})."
        )
        return errors

    if not isinstance(data, dict):
        errors.append(
            f"Invalid JSON structure in '{source_name}': Top-level JSON must be an object/dictionary."
        )
        return errors

    # 2. Installed/Desktop app requirement (reject 'web' or missing 'installed')
    if "web" in data:
        errors.append(
            f"Rejected OAuth configuration in '{source_name}': Found 'web' client application type. "
            "Mailbox Rescue requires a Google 'Desktop app' / 'installed' client configuration."
        )
        return errors

    if "installed" not in data:
        errors.append(
            f"Rejected OAuth configuration in '{source_name}': Missing top-level 'installed' key. "
            "File must be a Google OAuth 2.0 Client ID configuration for Desktop applications."
        )
        return errors

    installed = data["installed"]
    if not isinstance(installed, dict):
        errors.append(
            f"Invalid structure in '{source_name}': 'installed' must be an object/dictionary."
        )
        return errors

    # 3. Required fields existence and format
    for key in REQUIRED_INSTALLED_KEYS:
        if key not in installed:
            errors.append(
                f"Missing required key '{key}' inside 'installed' configuration in '{source_name}'."
            )
            continue

        val = installed[key]
        if key == "redirect_uris":
            if not isinstance(val, list) or len(val) == 0:
                errors.append(
                    f"Invalid 'redirect_uris' in '{source_name}': Must be a non-empty list of URIs."
                )
            elif not all(isinstance(u, str) and u.strip() for u in val):
                errors.append(
                    f"Invalid 'redirect_uris' in '{source_name}': All items must be non-empty strings."
                )
            elif not any(isinstance(u, str) and is_valid_loopback_redirect(u) for u in val):
                errors.append(
                    f"Invalid 'redirect_uris' in '{source_name}': Must contain at least one local loopback redirect URI (e.g. 'http://localhost' or 'http://127.0.0.1')."
                )
        elif key == "auth_uri":
            if not isinstance(val, str) or not val.strip():
                errors.append(
                    f"Invalid value for '{key}' in '{source_name}': Must be a non-empty string."
                )
            elif val not in ALLOWED_AUTH_URIS:
                errors.append(
                    f"Invalid 'auth_uri' in '{source_name}': Expected official Google authorization endpoint ({', '.join(ALLOWED_AUTH_URIS)})."
                )
        elif key == "token_uri":
            if not isinstance(val, str) or not val.strip():
                errors.append(
                    f"Invalid value for '{key}' in '{source_name}': Must be a non-empty string."
                )
            elif val not in ALLOWED_TOKEN_URIS:
                errors.append(
                    f"Invalid 'token_uri' in '{source_name}': Expected official Google token endpoint ({', '.join(ALLOWED_TOKEN_URIS)})."
                )
        elif key == "client_id":
            if not isinstance(val, str) or not val.strip():
                errors.append(
                    f"Invalid value for '{key}' in '{source_name}': Must be a non-empty string."
                )
            elif not (
                val.endswith(".apps.googleusercontent.com")
                and len(val.strip()) > len(".apps.googleusercontent.com")
            ):
                errors.append(
                    f"Invalid 'client_id' in '{source_name}': Must be a Google OAuth Client ID ending with '.apps.googleusercontent.com'."
                )
        else:
            if not isinstance(val, str) or not val.strip():
                errors.append(
                    f"Invalid value for '{key}' in '{source_name}': Must be a non-empty string."
                )

    return errors


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

    # 2. Content validation
    try:
        content_bytes = path.read_bytes()
    except OSError as exc:
        errors.append(f"Failed to read '{path.name}': {exc}")
        return errors

    content_errors = validate_oauth_client_content(content_bytes, path.name)
    errors.extend(content_errors)
    return errors


def inspect_path(
    target: Path,
    *,
    allow_oauth_client: bool = False,
) -> list[str]:
    """Inspect a directory or ZIP archive for prohibited files and verify OAuth sidecar rules.

    When allow_oauth_client is False (generic release):
      - Expected OAuth client configs: 0.
      - Any client_secret*.json or client_secrets*.json fails hygiene.

    When allow_oauth_client is True (configured release):
      - Expected OAuth client configs: EXACTLY 1.
      - For directory: exactly 'client_secret.json' at the bundle root.
      - For ZIP archive: exactly 'Mailbox Rescue/client_secret.json'.
      - Duplicate ZIP entries, alternative folders, or 0 configs fail hygiene.
      - The permitted sidecar content is validated.

    Returns a list of violation messages.
    """
    violations: list[str] = []

    if target.is_file() and target.suffix.lower() == ".zip":
        with zipfile.ZipFile(target, "r") as zf:
            entries = zf.namelist()
            oauth_candidates: list[str] = []

            for entry in entries:
                name = Path(entry).name
                full_rel = entry.replace("\\", "/")

                # 1. Check prohibited tokens
                for pat in PROHIBITED_TOKEN_PATTERNS:
                    if fnmatch.fnmatch(name.lower(), pat.lower()):
                        violations.append(f"Prohibited token file detected: '{full_rel}'")
                        break

                # 2. Check prohibited mail / export artifacts
                for pat in PROHIBITED_MAIL_AND_DATA_PATTERNS:
                    if fnmatch.fnmatch(name.lower(), pat.lower()):
                        violations.append(f"Prohibited export/data file detected: '{full_rel}'")
                        break

                # 3. Check prohibited dev/cache artifacts
                for pat in PROHIBITED_DEV_AND_CACHE_PATTERNS:
                    if fnmatch.fnmatch(name.lower(), pat.lower()) or any(
                        fnmatch.fnmatch(part.lower(), pat.lower()) for part in full_rel.split("/")
                    ):
                        violations.append(
                            f"Prohibited development/cache artifact detected: '{full_rel}'"
                        )
                        break

                # 4. Collect OAuth candidate files
                for pat in CLIENT_SECRET_PATTERNS:
                    if fnmatch.fnmatch(name.lower(), pat.lower()):
                        oauth_candidates.append(full_rel)
                        break

            # Evaluate OAuth candidates against policy
            expected_zip_path = "Mailbox Rescue/client_secret.json"
            if not allow_oauth_client:
                if oauth_candidates:
                    for cand in oauth_candidates:
                        violations.append(
                            f"Unexpected OAuth client secrets file detected: '{cand}'. "
                            "Pass --allow-oauth-client only if an approved client_secret.json "
                            "was intentionally staged."
                        )
            else:
                if len(oauth_candidates) == 0:
                    violations.append(
                        f"Expected exactly one OAuth client configuration '{expected_zip_path}' "
                        "in archive when OAuth staging is enabled, but none was found."
                    )
                elif len(oauth_candidates) > 1:
                    violations.append(
                        f"Expected exactly one OAuth client configuration '{expected_zip_path}', "
                        f"but found {len(oauth_candidates)} candidate entries: {oauth_candidates}."
                    )
                else:
                    cand = oauth_candidates[0]
                    if cand != expected_zip_path:
                        violations.append(
                            f"Unexpected or misplaced OAuth client secrets file detected: '{cand}'. "
                            f"When OAuth staging is enabled, exactly '{expected_zip_path}' is permitted."
                        )
                    else:
                        content = zf.read(cand)
                        content_errors = validate_oauth_client_content(content, cand)
                        for err in content_errors:
                            violations.append(f"Invalid staged OAuth client config: {err}")

    elif target.is_dir():
        oauth_candidates_dir: list[tuple[str, Path]] = []

        for item in target.rglob("*"):
            if not item.is_file():
                continue
            name = item.name
            rel = item.relative_to(target).as_posix()

            # 1. Check prohibited tokens
            for pat in PROHIBITED_TOKEN_PATTERNS:
                if fnmatch.fnmatch(name.lower(), pat.lower()):
                    violations.append(f"Prohibited token file detected: '{rel}'")
                    break

            # 2. Check prohibited mail / export artifacts
            for pat in PROHIBITED_MAIL_AND_DATA_PATTERNS:
                if fnmatch.fnmatch(name.lower(), pat.lower()):
                    violations.append(f"Prohibited export/data file detected: '{rel}'")
                    break

            # 3. Check prohibited dev/cache artifacts
            for pat in PROHIBITED_DEV_AND_CACHE_PATTERNS:
                if fnmatch.fnmatch(name.lower(), pat.lower()) or any(
                    fnmatch.fnmatch(part.lower(), pat.lower()) for part in rel.split("/")
                ):
                    violations.append(f"Prohibited development/cache artifact detected: '{rel}'")
                    break

            # 4. Collect OAuth candidate files
            for pat in CLIENT_SECRET_PATTERNS:
                if fnmatch.fnmatch(name.lower(), pat.lower()):
                    oauth_candidates_dir.append((rel, item))
                    break

        # Evaluate OAuth candidates against policy
        expected_dir_path = "client_secret.json"
        if not allow_oauth_client:
            if oauth_candidates_dir:
                for rel, _ in oauth_candidates_dir:
                    violations.append(
                        f"Unexpected OAuth client secrets file detected: '{rel}'. "
                        "Pass --allow-oauth-client only if an approved client_secret.json "
                        "was intentionally staged."
                    )
        else:
            if len(oauth_candidates_dir) == 0:
                violations.append(
                    f"Expected exactly one OAuth client configuration '{expected_dir_path}' "
                    "at bundle root when OAuth staging is enabled, but none was found."
                )
            elif len(oauth_candidates_dir) > 1:
                cand_paths = [r for r, _ in oauth_candidates_dir]
                violations.append(
                    f"Expected exactly one OAuth client configuration '{expected_dir_path}', "
                    f"but found {len(oauth_candidates_dir)} candidate files: {cand_paths}."
                )
            else:
                rel, item_path = oauth_candidates_dir[0]
                if rel != expected_dir_path:
                    violations.append(
                        f"Unexpected or misplaced OAuth client secrets file detected: '{rel}'. "
                        f"When OAuth staging is enabled, exactly '{expected_dir_path}' at root is permitted."
                    )
                else:
                    config_errors = validate_oauth_client_config(item_path)
                    for err in config_errors:
                        violations.append(f"Invalid staged OAuth client config: {err}")

    else:
        violations.append(f"Target path does not exist or is not a directory/zip: {target}")

    return violations


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
