from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_path


APP_NAME = "Mailbox Rescue"
APP_AUTHOR = "Mailbox Rescue"


@dataclass(frozen=True, slots=True)
class AppPaths:
    data_dir: Path
    token_file: Path
    client_secrets_file: Path

    @classmethod
    def discover(cls) -> "AppPaths":
        data_dir = Path(user_data_path(APP_NAME, APP_AUTHOR, roaming=True))
        data_dir.mkdir(parents=True, exist_ok=True)

        configured_client = os.getenv("MAILBOX_RESCUE_GOOGLE_CLIENT_SECRETS")
        client_secrets_file = (
            Path(configured_client).expanduser().resolve()
            if configured_client
            else Path.cwd() / "client_secret.json"
        )

        return cls(
            data_dir=data_dir,
            token_file=data_dir / "google_token.json",
            client_secrets_file=client_secrets_file,
        )
