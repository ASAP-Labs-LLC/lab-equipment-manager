#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
theme.py — Lightweight theming manager to apply QSS and provide named colors.
"""

from __future__ import annotations

import json
import os
from typing import Dict, Tuple

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication


class ThemeManager:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.mode = "light"
        self.colors: Dict[str, str] = {}

    def _paths(self, mode: str) -> Tuple[str, str]:
        md = mode.lower()
        qss = os.path.join(self.base_dir, f"{md}.qss")
        jsn = os.path.join(self.base_dir, f"{md}.json")
        return qss, jsn

    def apply(self, app: QApplication, mode: str) -> None:
        self.mode = (mode or "light").lower()
        qss_path, json_path = self._paths(self.mode)
        # Load QSS
        try:
            with open(qss_path, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
        except Exception:
            app.setStyleSheet("")
        # Load colors
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                self.colors = json.load(f) or {}
        except Exception:
            self.colors = {}

    def apply_file(self, app: QApplication, qss_path: str) -> None:
        self.mode = "custom"
        try:
            with open(qss_path, "r", encoding="utf-8") as f:
                app.setStyleSheet(f.read())
        except Exception:
            app.setStyleSheet("")
        # Keep previous colors; custom QSS typically doesn't provide JSON palette

    def color(self, key: str, fallback: str | None = None) -> QColor:
        val = self.colors.get(key)
        if not val:
            val = fallback or "#cccccc"
        try:
            return QColor(val)
        except Exception:
            return QColor("#cccccc")


_manager: ThemeManager | None = None


def theme_manager() -> ThemeManager:
    global _manager
    if _manager is None:
        base = os.path.join(os.path.dirname(__file__), "themes")
        _manager = ThemeManager(base)
    return _manager
