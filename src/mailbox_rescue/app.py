from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from mailbox_rescue.config import AppPaths
from mailbox_rescue.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Mailbox Rescue")

    window = MainWindow(AppPaths.discover())
    window.show()

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
