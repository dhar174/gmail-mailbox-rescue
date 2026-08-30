from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Signal, Slot

from mailbox_rescue.export.models import ExportProgress, ExportScope
from mailbox_rescue.export.service import ExportService
from mailbox_rescue.storage.checkpoint import CheckpointStore

if TYPE_CHECKING:
    from mailbox_rescue.gmail.client import GmailClient


class ExportWorker(QObject):
    """Background Qt worker adapting ExportService execution to Qt signals."""

    progress = Signal(object)
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        gmail_client: GmailClient,
        checkpoint_store: CheckpointStore,
        output_root: Path,
        scope: ExportScope = ExportScope.ALL_MAIL,
        cancel_event: threading.Event | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._gmail_client = gmail_client
        self._checkpoint_store = checkpoint_store
        self._output_root = output_root
        self._scope = scope
        self._cancel_event = cancel_event

    @Slot()
    def run(self) -> None:
        try:
            service = ExportService(
                gmail_client=self._gmail_client,
                checkpoint_store=self._checkpoint_store,
            )
            result = service.run(
                output_root=self._output_root,
                scope=self._scope,
                cancel_event=self._cancel_event,
                progress_callback=self._on_progress,
            )
            self.completed.emit(result)
        except Exception as exc:  # noqa: BLE001 - Worker boundary to surface errors safely to UI
            self.failed.emit(str(exc))
        finally:
            self.finished.emit()

    def _on_progress(self, progress: ExportProgress) -> None:
        self.progress.emit(progress)
