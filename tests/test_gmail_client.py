import base64
from unittest.mock import MagicMock

import pytest

from mailbox_rescue.gmail.client import GmailClient, decode_raw_message

SAMPLE_RFC822_EMAIL = (
    b"From: sender@example.com\r\n"
    b"To: recipient@example.com\r\n"
    b"Subject: Test Subject with Special Chars: \xc3\xa9\xc3\xa0\xc3\xb1 & <symbols>\r\n"
    b"Date: Wed, 27 Aug 2026 12:00:00 -0400\r\n"
    b"Message-ID: <unique-id-12345@example.com>\r\n"
    b"MIME-Version: 1.0\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Transfer-Encoding: 8bit\r\n"
    b"\r\n"
    b"Hello,\r\n"
    b"\r\n"
    b"This is a multi-line RFC 822 test message body.\r\n"
    b"Line 2 contains unicode: \xe2\x9c\x93 \xe2\x98\x85 \xc2\xa9.\r\n"
    b"Line 3 has binary bytes: \x00\x01\xfe\xff\r\n"
    b"\r\n"
    b"-- End of Message --\r\n"
)


def test_decode_raw_message_normal_base64url() -> None:
    # Contains bytes that map to '-' and '_' in URL-safe Base64:
    # 0xfb -> 11111011 (starts with 111110 = 62 -> '-' in urlsafe)
    # 0xff -> 11111111 (starts with 111111 = 63 -> '_' in urlsafe)
    raw = b"\xfb\xff\xfe\x00\x01\x02\x03\x04"
    encoded_padded = base64.urlsafe_b64encode(raw).decode("ascii")

    assert "-" in encoded_padded or "_" in encoded_padded
    assert decode_raw_message(encoded_padded) == raw


@pytest.mark.parametrize(
    "payload",
    [
        b"",  # 0 bytes -> 0 chars (len % 4 == 0)
        b"a",  # 1 byte  -> 2 chars, needs 2 '=' (len % 4 == 2)
        b"ab",  # 2 bytes -> 3 chars, needs 1 '=' (len % 4 == 3)
        b"abc",  # 3 bytes -> 4 chars, needs 0 '=' (len % 4 == 0)
        b"abcd",  # 4 bytes -> 6 chars, needs 2 '=' (len % 4 == 2)
        b"abcde",  # 5 bytes -> 7 chars, needs 1 '=' (len % 4 == 3)
        b"abcdef",  # 6 bytes -> 8 chars, needs 0 '=' (len % 4 == 0)
    ],
)
def test_decode_raw_message_omitted_padding(payload: bytes) -> None:
    unpadded_b64url = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    assert "=" not in unpadded_b64url
    assert decode_raw_message(unpadded_b64url) == payload


def test_decode_raw_message_preserves_representative_rfc822_email() -> None:
    # Gmail API returns unpadded Base64URL string in the 'raw' field
    encoded_unpadded = (
        base64.urlsafe_b64encode(SAMPLE_RFC822_EMAIL).decode("ascii").rstrip("=")
    )

    decoded = decode_raw_message(encoded_unpadded)
    assert decoded == SAMPLE_RFC822_EMAIL


def test_gmail_client_get_raw_message_delegates_to_decoder() -> None:
    mock_service = MagicMock()
    encoded_unpadded = (
        base64.urlsafe_b64encode(SAMPLE_RFC822_EMAIL).decode("ascii").rstrip("=")
    )

    messages_resource = mock_service.users.return_value.messages.return_value
    messages_resource.get.return_value.execute.return_value = {
        "id": "msg_xyz_789",
        "raw": encoded_unpadded,
    }

    client = object.__new__(GmailClient)
    client._service = mock_service

    result = client.get_raw_message("msg_xyz_789")

    assert result == SAMPLE_RFC822_EMAIL
    messages_resource.get.assert_called_once_with(
        userId="me",
        id="msg_xyz_789",
        format="raw",
    )


def test_gmail_client_get_export_message_single_fetch_captures_all_fields() -> None:
    mock_service = MagicMock()
    encoded_unpadded = (
        base64.urlsafe_b64encode(SAMPLE_RFC822_EMAIL).decode("ascii").rstrip("=")
    )

    messages_resource = mock_service.users.return_value.messages.return_value
    messages_resource.get.return_value.execute.return_value = {
        "id": "msg_12345",
        "threadId": "thread_67890",
        "labelIds": ["INBOX", "UNREAD", "Label_1"],
        "raw": encoded_unpadded,
    }

    client = object.__new__(GmailClient)
    client._service = mock_service

    export_msg = client.get_export_message("msg_12345")

    assert export_msg.message_id == "msg_12345"
    assert export_msg.thread_id == "thread_67890"
    assert export_msg.label_ids == ("INBOX", "UNREAD", "Label_1")
    assert export_msg.raw_bytes == SAMPLE_RFC822_EMAIL

    # Verifies only a single format='raw' call was made (no second metadata call)
    messages_resource.get.assert_called_once_with(
        userId="me",
        id="msg_12345",
        format="raw",
    )


def test_gmail_client_get_export_message_defaults_missing_optional_fields() -> None:
    mock_service = MagicMock()
    encoded_unpadded = base64.urlsafe_b64encode(b"simple payload").decode("ascii")

    messages_resource = mock_service.users.return_value.messages.return_value
    messages_resource.get.return_value.execute.return_value = {
        "id": "msg_only_id",
        "raw": encoded_unpadded,
    }

    client = object.__new__(GmailClient)
    client._service = mock_service

    export_msg = client.get_export_message("msg_only_id")

    assert export_msg.message_id == "msg_only_id"
    assert export_msg.thread_id == ""
    assert export_msg.label_ids == ()
    assert export_msg.raw_bytes == b"simple payload"


def test_gmail_client_list_labels_system_and_unicode_user_labels() -> None:
    mock_service = MagicMock()
    labels_resource = mock_service.users.return_value.labels.return_value
    labels_resource.list.return_value.execute.return_value = {
        "labels": [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "SENT", "name": "SENT", "type": "system"},
            {"id": "Label_100", "name": "Projects / 集中", "type": "user"},
        ]
    }

    client = object.__new__(GmailClient)
    client._service = mock_service

    labels = client.list_labels()

    assert len(labels) == 3
    assert labels[0].id == "INBOX"
    assert labels[0].name == "INBOX"
    assert labels[0].type == "system"
    assert labels[1].id == "SENT"
    assert labels[2].id == "Label_100"
    assert labels[2].name == "Projects / 集中"
    assert labels[2].type == "user"

    labels_resource.list.assert_called_once_with(userId="me")
