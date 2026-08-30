from __future__ import annotations

import argparse
import fnmatch
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
    "checksums.sha256",
    "export-report.html",
    "messages.jsonl",
]

PROHIBITED_DEV_AND_CACHE_PATTERNS = [
    "__pycache__",
    ".pytest_cache",
    ".coverage*",
    ".git*",
    ".venv*",
]

CLIENT_SECRET_PATTERNS = [
    "client_secret*.json",
    "client_secrets*.json",
]


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
    parser.add_argument("target", type=Path, help="Path to release folder or ZIP file")
    parser.add_argument(
        "--allow-oauth-client",
        action="store_true",
        help="Allow approved client_secret.json if intentionally staged for pilot release",
    )

    args = parser.parse_args(argv)
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
