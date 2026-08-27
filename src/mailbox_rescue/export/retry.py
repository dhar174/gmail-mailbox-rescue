from __future__ import annotations

import http.client
import json
import socket
import ssl
import urllib.error
import google.auth.exceptions
from googleapiclient.errors import HttpError

_RATE_LIMIT_REASONS = frozenset(
    {
        "ratelimitexceeded",
        "userratelimitexceeded",
        "quotaexceeded",
        "dailylimitexceeded",
        "resource_exhausted",
    }
)


def extract_error_reasons(error: HttpError) -> set[str]:
    """Extract machine-readable error reasons and statuses from an HttpError."""
    reasons: set[str] = set()

    content = getattr(error, "content", b"")
    if isinstance(content, bytes):
        try:
            content_str = content.decode("utf-8")
        except UnicodeDecodeError:
            content_str = ""
    elif isinstance(content, str):
        content_str = content
    else:
        content_str = ""

    if content_str:
        try:
            data = json.loads(content_str)
            if isinstance(data, dict):
                error_obj = data.get("error")
                if isinstance(error_obj, dict):
                    status = error_obj.get("status")
                    if isinstance(status, str):
                        reasons.add(status)

                    errors_list = error_obj.get("errors")
                    if isinstance(errors_list, list):
                        for item in errors_list:
                            if isinstance(item, dict) and "reason" in item:
                                reasons.add(str(item["reason"]))

                    details = error_obj.get("details")
                    if isinstance(details, list):
                        for item in details:
                            if isinstance(item, dict):
                                if "reason" in item:
                                    reasons.add(str(item["reason"]))
                                if "errorType" in item:
                                    reasons.add(str(item["errorType"]))
        except (ValueError, json.JSONDecodeError, TypeError):
            pass

    details_attr = getattr(error, "error_details", None)
    if isinstance(details_attr, list):
        for item in details_attr:
            if isinstance(item, dict) and "reason" in item:
                reasons.add(str(item["reason"]))
            elif isinstance(item, str):
                reasons.add(item)
    elif isinstance(details_attr, str) and details_attr:
        reasons.add(details_attr)

    reason_attr = getattr(error, "reason", None)
    if isinstance(reason_attr, str) and reason_attr:
        reasons.add(reason_attr)

    return reasons


def is_transient_error(exc: BaseException) -> bool:
    """
    Classify whether an exception encountered during a Gmail operation is transient/retryable.
    """
    if isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", None) or getattr(exc, "status_code", None)
        if status == 429:
            return True
        if status in (500, 502, 503, 504):
            return True
        if status == 403:
            reasons = {r.lower() for r in extract_error_reasons(exc)}
            if any(r in _RATE_LIMIT_REASONS for r in reasons):
                return True
            for r in reasons:
                if "ratelimit" in r or "quota" in r or "resource_exhausted" in r:
                    return True
            return False
        return False

    network_transient_types = (
        ConnectionError,
        TimeoutError,
        socket.timeout,
        socket.gaierror,
        socket.herror,
        http.client.RemoteDisconnected,
        http.client.IncompleteRead,
        http.client.BadStatusLine,
        ssl.SSLError,
        google.auth.exceptions.TransportError,
    )
    if isinstance(exc, network_transient_types):
        return True

    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, BaseException):
            return is_transient_error(reason)
        if isinstance(reason, str):
            lower_reason = reason.lower()
            return any(
                term in lower_reason
                for term in ("timed out", "timeout", "connection", "reset", "refused", "temporary")
            )
        return True

    return False
