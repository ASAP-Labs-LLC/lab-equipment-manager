#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
client_app.py - Entry for client-side GUI connecting to server.py

Usage:
    python client_app.py

Environment:
    LABMAP_SERVER_URL (default http://127.0.0.1:8787)
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

from client_main_window import ClientMainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Lab Manager Map (Client)")
    w = ClientMainWindow()
    # Apply theme based on fetched cfg
    try:
        mode = getattr(w.cfg, 'theme_mode', 'light')
        if mode == 'custom' and getattr(w.cfg, 'custom_qss_path', ''):
            theme_manager().apply_file(app, getattr(w.cfg, 'custom_qss_path', ''))
        else:
            theme_manager().apply(app, mode)
        w._refresh_theme_visuals()
        try:
            w._apply_platform_titlebar_theme()
        except Exception:
            pass
        w._apply_font_from_cfg()
    except Exception:
        pass
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

