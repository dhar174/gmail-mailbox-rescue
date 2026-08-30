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


@dataclass(frozen=True, slots=True)
class GmailLabel:
    id: str
    name: str
    type: str | None = None


@dataclass(frozen=True, slots=True)
class GmailExportMessage:
    message_id: str
    thread_id: str
    label_ids: tuple[str, ...]
    raw_bytes: bytes


def decode_raw_message(encoded: str) -> bytes:
    """Decode Gmail API base64url-encoded message payload into raw bytes."""
    padding = "=" * (-len(encoded) % 4)
    return base64.urlsafe_b64decode(encoded + padding)


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

    def list_labels(self) -> list[GmailLabel]:
        response = self._service.users().labels().list(userId="me").execute()
        labels_raw = response.get("labels", [])
        return [
            GmailLabel(
                id=label["id"],
                name=label.get("name", label["id"]),
                type=label.get("type"),
            )
            for label in labels_raw
            if "id" in label
        ]

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

    def get_export_message(self, message_id: str) -> GmailExportMessage:
        message = (
            self._service.users()
            .messages()
            .get(userId="me", id=message_id, format="raw")
            .execute()
        )
        raw_encoded = message.get("raw", "")
        return GmailExportMessage(
            message_id=message.get("id", message_id),
            thread_id=message.get("threadId", ""),
            label_ids=tuple(message.get("labelIds") or ()),
            raw_bytes=decode_raw_message(raw_encoded),
        )

    def get_raw_message(self, message_id: str) -> bytes:
        return self.get_export_message(message_id).raw_bytes
