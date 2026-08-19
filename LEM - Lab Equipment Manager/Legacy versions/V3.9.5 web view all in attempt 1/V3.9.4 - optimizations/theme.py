#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
theme.py — Lightweight theming manager to apply QSS and provide named colors.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, Tuple

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QApplication


class ThemeManager:
    def __init__(self, base_dir: str) -> None:
        self.base_dir = base_dir
        self.mode = "light"
        self.colors: Dict[str, str] = {}
        self.stylesheet: str = ""
        self.extra_styles: str = ""

    def _paths(self, mode: str) -> Tuple[str, str]:
        md = mode.lower()
        qss = os.path.join(self.base_dir, f"{md}.qss")
        jsn = os.path.join(self.base_dir, f"{md}.json")
        return qss, jsn

    def apply(self, app: QApplication, mode: str) -> None:
        self.mode = (mode or "light").lower()
        qss_path, json_path = self._paths(self.mode)
        qss_text = ""
        try:
            with open(qss_path, "r", encoding="utf-8") as f:
                qss_text = f.read()
        except Exception:
            qss_text = ""
        self._set_stylesheet(app, qss_text)
        colors = self._update_colors_from_qss(qss_text, self.mode)
        if colors:
            self.colors = colors
            self._write_colors(json_path, colors)
        else:
            self.colors = self._load_colors_from_json(json_path)

    def apply_file(self, app: QApplication, qss_path: str) -> None:
        base = os.path.splitext(os.path.basename(qss_path))[0]
        self.mode = base.lower() or "custom"
        qss_text = ""
        try:
            with open(qss_path, "r", encoding="utf-8") as f:
                qss_text = f.read()
        except Exception:
            qss_text = ""
        self._set_stylesheet(app, qss_text)
        colors = self._update_colors_from_qss(qss_text, self.mode)
        json_path = os.path.join(self.base_dir, f"{self.mode}.json")
        if colors:
            self.colors = colors
            self._write_colors(json_path, colors)
        else:
            self.colors = self._load_colors_from_json(json_path)

    def color(self, key: str, fallback: str | None = None) -> QColor:
        val = self.colors.get(key)
        if not val:
            val = fallback or "#cccccc"
        try:
            return QColor(val)
        except Exception:
            return QColor("#cccccc")

    def set_extra_styles(self, app: QApplication, extra: str) -> None:
        self.extra_styles = extra or ""
        self._commit_stylesheet(app)

    # ----- helpers -----
    def _set_stylesheet(self, app: QApplication, qss_text: str) -> None:
        self.stylesheet = qss_text or ""
        self._commit_stylesheet(app)

    def _commit_stylesheet(self, app: QApplication) -> None:
        combined = self.stylesheet
        if self.extra_styles:
            combined = f"{combined}\n{self.extra_styles}" if combined else self.extra_styles
        app.setStyleSheet(combined)

    def _update_colors_from_qss(self, qss_text: str, name: str) -> Dict[str, str]:
        colors = self._extract_colors_from_qss(qss_text)
        if not colors:
            return {}
        return colors

    def _extract_colors_from_qss(self, text: str) -> Dict[str, str]:
        if not text:
            return {}
        colors: Dict[str, str] = {}
        meta_pattern = re.compile(r"/\*\s*THEME_COLORS(?P<body>.*?)\*/", re.IGNORECASE | re.DOTALL)
        match = meta_pattern.search(text)
        if match:
            body = match.group("body")
            for key, value in re.findall(r"([A-Za-z0-9_]+)\s*=\s*(#[0-9A-Fa-f]{3,8})", body):
                colors[key] = value
        if "grid_bg" not in colors:
            bg = re.search(r"background(?:-color)?\s*:\s*(#[0-9A-Fa-f]{3,8})", text)
            if bg:
                colors["grid_bg"] = bg.group(1)
        if "grid_line" not in colors:
            line = re.search(r"border(?:-color)?\s*:\s*(#[0-9A-Fa-f]{3,8})", text)
            if line:
                colors["grid_line"] = line.group(1)
        return colors

    def _write_colors(self, path: str, colors: Dict[str, str]) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(colors, f, indent=2)
        except Exception:
            pass

    def _load_colors_from_json(self, path: str) -> Dict[str, str]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
        except Exception:
            return {}


_manager: ThemeManager | None = None


def theme_manager() -> ThemeManager:
    global _manager
    if _manager is None:
        base = os.path.join(os.path.dirname(__file__), "themes")
        _manager = ThemeManager(base)
    return _manager
