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
try:
    from theme import theme_manager  # type: ignore[reportMissingImports]
except Exception:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(__file__))
    from theme import theme_manager  # type: ignore[reportMissingImports]

from main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Lab Manager Map")
    w = MainWindow()
    # Apply persisted theme once MainWindow has loaded cfg
    try:
        mode = getattr(w.cfg, 'theme_mode', 'light')
        if mode == 'custom' and getattr(w.cfg, 'custom_qss_path', ''):
            theme_manager().apply_file(app, getattr(w.cfg, 'custom_qss_path', ''))
        else:
            theme_manager().apply(app, mode)
        # Ensure visuals pick up theme colors and title bar
        w._refresh_theme_visuals()
        try:
            w._apply_platform_titlebar_theme()
        except Exception:
            pass
        # Load fonts and apply
        w._apply_font_from_cfg()
    except Exception:
        pass
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
