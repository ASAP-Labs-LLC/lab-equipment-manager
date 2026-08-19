#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Application entry point for the Lab Manager console.
"""

from __future__ import annotations

import sys

from PyQt5.QtWidgets import QApplication

from main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Lab Manager Console")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
