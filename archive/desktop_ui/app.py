#!/usr/bin/env python3
"""LocalFun — desktop anime & manga tracker."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from localfun.ui.main_window import MainWindow, make_app_icon
from localfun.ui.styles import STYLESHEET


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("LocalFun")
    app.setOrganizationName("LocalFun")
    app.setWindowIcon(make_app_icon())
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
