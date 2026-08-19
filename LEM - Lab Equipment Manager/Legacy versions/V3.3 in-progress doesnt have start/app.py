#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
app.py — Entry point for Lab Manager Map.

Usage:
    python app.py
"""

from __future__ import annotations

import sys
from PyQt5.QtWidgets import QApplication

from main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Lab Manager Map")
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
