from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path

APP_NAME = "Mailbox Rescue"
APP_AUTHOR = "Mailbox Rescue"


def resolve_client_secrets_path() -> Path:
    """Resolve the Google OAuth client secrets file location with deterministic precedence.

    Precedence:
    1. MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS environment variable
    2. Beside the frozen macOS .app bundle, or beside the frozen executable
    3. Development working directory / client_secret.json
    """
    configured_client = os.getenv("MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS")
    if configured_client:
        return Path(configured_client).expanduser().resolve()

    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        if sys.platform == "darwin":
            for ancestor in executable.parents:
                if ancestor.suffix.lower() == ".app":
                    return ancestor.parent / "client_secret.json"
        # Also supports a frozen command-line executable without an app bundle.
        # Never fall back to the working directory for a missing packaged sidecar.
        return executable.parent / "client_secret.json"

    return Path.cwd() / "client_secret.json"


@dataclass(frozen=True, slots=True)
class AppPaths:
    data_dir: Path
    token_file: Path
    client_secrets_file: Path

    @classmethod
    def discover(cls) -> AppPaths:
        data_dir = Path(user_data_path(APP_NAME, APP_AUTHOR))
        data_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            data_dir=data_dir,
            token_file=data_dir / "google_token.json",
            client_secrets_file=resolve_client_secrets_path(),
        )
