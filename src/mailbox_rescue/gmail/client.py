from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build


@dataclass(frozen=True, slots=True)
class MailboxProfile:
    email_address: str
    messages_total: int
    threads_total: int


class GmailClient:
    def __init__(self, credentials: Credentials) -> None:
        self._service = build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def profile(self) -> MailboxProfile:
        profile: dict[str, Any] = self._service.users().getProfile(userId="me").execute()
        return MailboxProfile(
            email_address=profile["emailAddress"],
            messages_total=int(profile.get("messagesTotal", 0)),
            threads_total=int(profile.get("threadsTotal", 0)),
        )

    def iter_message_ids(
        self,
        *,
        label_ids: list[str] | None = None,
        query: str | None = None,
        include_spam_trash: bool = False,
    ) -> Iterator[str]:
        page_token: str | None = None

        while True:
            response = (
                self._service.users()
                .messages()
                .list(
                    userId="me",
                    labelIds=label_ids,
                    q=query,
                    includeSpamTrash=include_spam_trash,
                    maxResults=500,
                    pageToken=page_token,
                )
                .execute()
            )

            for message in response.get("messages", []):
                yield message["id"]

            page_token = response.get("nextPageToken")
            if not page_token:
                break

    def get_raw_message(self, message_id: str) -> bytes:
        message = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="raw")
            .execute()
        )
        encoded = message["raw"]
        padding = "=" * (-len(encoded) % 4)
        return base64.urlsafe_b64decode(encoded + padding)
