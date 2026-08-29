import json
import math
import socket
import sqlite3
import ssl
import urllib.error
from types import SimpleNamespace

import google.auth.exceptions
import pytest
from googleapiclient.errors import HttpError

from mailbox_rescue.export.models import RetryPolicy
from mailbox_rescue.export.retry import extract_error_reasons, is_transient_error


def _make_http_error(status: int, reason: str = "", content_dict: dict | None = None) -> HttpError:
    resp = SimpleNamespace(status=status, reason=reason)
    if content_dict is not None:
        content = json.dumps(content_dict).encode("utf-8")
    else:
        content = b""
    return HttpError(resp=resp, content=content)


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_is_transient_error_standard_transient_statuses(status: int) -> None:
    err = _make_http_error(status=status)
    assert is_transient_error(err) is True


@pytest.mark.parametrize("status", [400, 401, 404, 409, 410])
def test_is_transient_error_permanent_http_statuses(status: int) -> None:
    err = _make_http_error(status=status)
    assert is_transient_error(err) is False


@pytest.mark.parametrize(
    "reason_str",
    [
        "rateLimitExceeded",
        "userRateLimitExceeded",
    ],
)
def test_is_transient_error_403_rate_limits(reason_str: str) -> None:
    # Test with errors array format
    content_errors = {
        "error": {
            "code": 403,
            "message": f"Rate limit: {reason_str}",
            "errors": [{"reason": reason_str, "message": "Too fast"}],
        }
    }
    err = _make_http_error(403, content_dict=content_errors)
    assert is_transient_error(err) is True

    # Test with status / details format
    content_status = {
        "error": {
            "code": 403,
            "message": "Limit hit",
            "status": reason_str,
        }
    }
    err2 = _make_http_error(403, content_dict=content_status)
    assert is_transient_error(err2) is True


@pytest.mark.parametrize(
    "reason_str",
    [
        "quotaExceeded",
        "dailyLimitExceeded",
        "resource_exhausted",
        "RESOURCE_EXHAUSTED",
        "forbidden",
        "insufficientPermissions",
        "accessNotConfigured",
        "domainPolicy",
        "accountDisabled",
    ],
)
def test_is_transient_error_403_non_retryable(reason_str: str) -> None:
    content = {
        "error": {
            "code": 403,
            "message": f"Denied: {reason_str}",
            "errors": [{"reason": reason_str, "message": "Not allowed"}],
        }
    }
    err = _make_http_error(403, content_dict=content)
    assert is_transient_error(err) is False


def test_extract_error_reasons_malformed_content() -> None:
    resp = SimpleNamespace(status=403, reason="Forbidden")
    err_bad_json = HttpError(resp=resp, content=b"<!DOCTYPE html><html>Not JSON</html>")
    assert is_transient_error(err_bad_json) is False
    reasons = extract_error_reasons(err_bad_json)
    assert "Forbidden" in reasons
    assert "<!DOCTYPE html><html>Not JSON</html>" in reasons


def test_is_transient_error_network_and_transport() -> None:
    assert is_transient_error(ConnectionResetError("Connection reset by peer")) is True
    assert is_transient_error(TimeoutError("Operation timed out")) is True
    assert is_transient_error(socket.gaierror("getaddrinfo failed")) is True
    assert is_transient_error(ssl.SSLError("SSL handshake failed")) is True
    assert is_transient_error(google.auth.exceptions.TransportError("Transport error")) is True
    assert is_transient_error(urllib.error.URLError("Connection refused")) is True
    assert (
        is_transient_error(urllib.error.URLError(TimeoutError("Connection timed out")))
        is True
    )


def test_is_transient_error_rejects_filesystem_and_database_errors() -> None:
    assert is_transient_error(OSError("Disk full")) is False
    assert is_transient_error(PermissionError("Access denied")) is False
    assert is_transient_error(FileNotFoundError("No such file")) is False
    assert is_transient_error(sqlite3.OperationalError("database is locked")) is False
    assert is_transient_error(ValueError("Invalid argument")) is False
    assert is_transient_error(KeyError("missing_key")) is False


def test_retry_policy_delay_calculation() -> None:
    policy = RetryPolicy(
        max_attempts=4,
        base_delay=1.0,
        max_delay=16.0,
        jitter=0.5,
        jitter_fn=lambda max_j: 0.25,  # Fixed deterministic jitter
    )

    # Attempt 1: 1.0 * (2^0) + 0.25 = 1.25
    assert policy.compute_delay(1) == 1.25
    # Attempt 2: 1.0 * (2^1) + 0.25 = 2.25
    assert policy.compute_delay(2) == 2.25
    # Attempt 3: 1.0 * (2^2) + 0.25 = 4.25
    assert policy.compute_delay(3) == 4.25
    # Attempt 4: 1.0 * (2^3) + 0.25 = 8.25
    assert policy.compute_delay(4) == 8.25
    # Attempt 5 (clamped to max_delay 16.0): 1.0 * (2^4) + 0.25 = 16.25 -> clamped to 16.0
    assert policy.compute_delay(5) == 16.0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_attempts", 0),
        ("max_attempts", -1),
        ("base_delay", -1.0),
        ("max_delay", -1.0),
        ("jitter", -1.0),
        ("base_delay", math.inf),
        ("base_delay", -math.inf),
        ("base_delay", math.nan),
        ("max_delay", math.inf),
        ("max_delay", math.nan),
        ("jitter", math.inf),
        ("jitter", math.nan),
    ],
)
def test_retry_policy_rejects_invalid_settings(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        RetryPolicy(**{field: value})  # type: ignore[arg-type]
