from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite


class ExportScope(StrEnum):
    ALL_MAIL = "all_mail"
    INBOX = "inbox"


class ExportPhase(StrEnum):
    SCANNING = "scanning"
    SCAN_COMPLETE = "scan_complete"
    EXPORTING = "exporting"
    RETRYING = "retrying"
    MESSAGE_COMPLETED = "message_completed"
    MESSAGE_SKIPPED = "message_skipped"
    MESSAGE_FAILED = "message_failed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ExportProgress:
    phase: ExportPhase
    total_messages: int = 0
    current_index: int = 0
    message_id: str | None = None
    completed_this_run: int = 0
    skipped_completed: int = 0
    failed_this_run: int = 0
    attempt: int = 1
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class ExportFailure:
    message_id: str
    error_type: str
    error_message: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ExportResult:
    total_scanned: int
    completed_this_run: int
    skipped_completed: int
    failed: int
    cancelled: bool
    failures: list[ExportFailure] = field(default_factory=list)
    error_message: str | None = None


def _default_jitter(max_jitter: float) -> float:
    return random.uniform(0, max_jitter) if max_jitter > 0 else 0.0


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 1.0
    max_delay: float = 16.0
    jitter: float = 1.0
    sleep_fn: Callable[[float], None] = field(default=time.sleep)
    jitter_fn: Callable[[float], float] = field(default=_default_jitter)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        for name, value in (
            ("base_delay", self.base_delay),
            ("max_delay", self.max_delay),
            ("jitter", self.jitter),
        ):
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
            if not isfinite(value):
                raise ValueError(f"{name} must be finite")

    def compute_delay(self, attempt: int) -> float:
        """
        Compute backoff delay before attempt `attempt + 1`.
        `attempt` is 1-based (i.e. attempt=1 after 1st failure).
        """
        attempt = max(attempt, 1)
        exponential = self.base_delay * (2 ** (attempt - 1))
        jitter_amount = self.jitter_fn(self.jitter) if self.jitter > 0 else 0.0
        return min(exponential + jitter_amount, self.max_delay)


class ExportError(Exception):
    """Base exception for export engine errors."""


class ScanError(ExportError):
    """Raised when scanning message IDs fails fatally."""


class FatalStorageError(ExportError):
    """Raised when local filesystem or database operations fail fatally."""
