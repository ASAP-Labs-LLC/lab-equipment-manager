#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
box_item.py — QGraphics items for the canvas: machine box + resize handle.
"""

from __future__ import annotations

from typing import List

from PyQt5.QtCore import QRectF, QPointF, Qt
from PyQt5.QtGui import QColor, QBrush, QPen, QFont
from PyQt5.QtWidgets import (
    QGraphicsRectItem, QGraphicsTextItem, QMenu, QMessageBox,
    QDialog, QFormLayout, QLineEdit, QTextEdit, QPushButton, QLabel,
    QHBoxLayout, QVBoxLayout, QDialogButtonBox
)

from models import BoxConfig, STATUS_GREEN, STATUS_RED, STATUS_YELLOW, STATUS_DEAD, STATUS_SERVICE, STATUS_UNKNOWN
try:
    from theme import theme_manager  # type: ignore[reportMissingImports]
except Exception:  # pragma: no cover
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(__file__))
    from theme import theme_manager  # type: ignore[reportMissingImports]

def C(key: str, fb: str) -> QColor:
    return theme_manager().color(key, fb)

MIN_W, MIN_H = 20.0, 20.0


class ResizeHandle(QGraphicsRectItem):
    def __init__(self, parent_box: "MachineBoxItem") -> None:
        super().__init__(parent_box)
        self._box = parent_box
        self.setRect(QRectF(0, 0, 14, 14))
        self.setBrush(QBrush(C("handle_color", "#5a5a5a")))
        self.setPen(QPen(C("border_color", "#212121"), 1))
        self.setFlag(QGraphicsRectItem.ItemIsMovable, True)
        self.setFlag(QGraphicsRectItem.ItemSendsScenePositionChanges, True)
        self.setCursor(Qt.SizeFDiagCursor)

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.ItemPositionChange:
            if self._box._resizing or self._box.box.locked or self._box.is_map_locked():
                return self.pos()
            new_pos: QPointF = value
            handle_w = self.rect().width()
            handle_h = self.rect().height()
            raw_w = new_pos.x() + handle_w
            raw_h = new_pos.y() + handle_h
            g = self._box.grid_size()
            new_w = max(MIN_W, round(raw_w / g) * g)
            new_h = max(MIN_H, round(raw_h / g) * g)
            self._box.update_size(new_w, new_h)
            return QPointF(self._box.rect().width() - handle_w, self._box.rect().height() - handle_h)
        return super().itemChange(change, value)


class MachineBoxItem(QGraphicsRectItem):
    def __init__(self, box: BoxConfig):
        super().__init__()
        self.box = box
        self._resizing = False

        self.setRect(QRectF(0, 0, box.size[0], box.size[1]))
        self.setPos(QPointF(box.pos[0], box.pos[1]))
        self.setPen(QPen(C("border_color", "#212121"), 2))
        self.setFlags(QGraphicsRectItem.ItemIsSelectable | QGraphicsRectItem.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)

        self._status = STATUS_UNKNOWN
        self._reason = ""
        self._info_lines: List[str] = []

        self.titleItem = QGraphicsTextItem(self)
        f = QFont(); f.setPointSize(10); f.setBold(True)
        self.titleItem.setFont(f)

        self.subItem = QGraphicsTextItem(self)
        f2 = QFont(); f2.setPointSize(9)
        self.subItem.setFont(f2)
        self.subItem.setTextWidth(-1)

        self.handle = ResizeHandle(self)
        self._apply_handle_visibility()

        self._refresh_text_layout()
        self.apply_visuals()

    def grid_size(self) -> float:
        return getattr(self.scene(), "GRID_SIZE", 20.0)

    def is_map_locked(self) -> bool:
        return bool(getattr(self.scene(), "map_locked", False))

    def set_movable(self, movable: bool) -> None:
        self.setFlag(QGraphicsRectItem.ItemIsMovable, movable and not self.is_map_locked())

    def sync_lock_state(self) -> None:
        """Public hook for MainWindow to refresh handle visibility on map lock toggles."""
        self._apply_handle_visibility()
        self._position_handle()

    def update_size(self, w: float, h: float) -> None:
        if self._resizing:
            return
        self._resizing = True
        try:
            w = max(w, MIN_W); h = max(h, MIN_H)
            self.setRect(QRectF(0, 0, w, h))
            self.box.size = (w, h)
            self._refresh_text_layout()
            self._position_handle()
            if hasattr(self.scene(), "parent_window"):
                self.scene().parent_window.save_config()
        finally:
            self._resizing = False

    def _refresh_text_layout(self) -> None:
        padding = 6
        self.titleItem.setPlainText(self.box.title)
        self.titleItem.setPos(padding, padding)

        raw_lines = self._info_lines[:] if self._info_lines else []
        text = "\n".join(raw_lines) if raw_lines else ""
        self.subItem.setPlainText(text)

        avail_w = max(8.0, self.rect().width() - 2 * padding)
        self.subItem.setTextWidth(avail_w)

        base_pt = 9
        min_pt = 6
        f = self.subItem.font()
        f.setPointSize(base_pt)
        self.subItem.setFont(f)

        avail_h = max(4.0, self.rect().height() - (padding + 20 + padding))
        self.subItem.setPos(padding, padding + 20)

        for pt in range(base_pt, min_pt - 1, -1):
            f.setPointSize(pt)
            self.subItem.setFont(f)
            if self.subItem.boundingRect().height() <= avail_h:
                break

        while self.subItem.boundingRect().height() > avail_h and raw_lines:
            raw_lines = raw_lines[:-1]
            ell = "…" if raw_lines else ""
            self.subItem.setPlainText("\n".join(raw_lines + ([ell] if ell else [])))

    def _position_handle(self) -> None:
        self.handle.setPos(self.rect().width() - self.handle.rect().width(),
                           self.rect().height() - self.handle.rect().height())

    def set_status(self, status: str, reason: str, info_lines: List[str]) -> None:
        self._status = status
        self._reason = reason
        self._info_lines = info_lines
        self._refresh_text_layout()
        self.apply_visuals()

    def _apply_handle_visibility(self) -> None:
        unlocked = (not self.box.locked) and (not self.is_map_locked())
        self.handle.setVisible(unlocked)
        self.set_movable(unlocked)

    def apply_visuals(self) -> None:
        if self.box.manual_override == STATUS_DEAD:
            self.setBrush(QBrush(C("machine_black", "#000000")))
            self.titleItem.setDefaultTextColor(C("machine_black_text", "#dc143c"))
            self.subItem.setDefaultTextColor(C("machine_black_text", "#dc143c"))
            return
        if self.box.manual_override == STATUS_SERVICE:
            self.setBrush(QBrush(C("machine_service_bg", "#e6e6e6")))
            self.titleItem.setDefaultTextColor(C("machine_service_text", "#3c3c3c"))
            self.subItem.setDefaultTextColor(C("machine_service_text", "#3c3c3c"))
            return

        if self._status == STATUS_GREEN:
            self.setBrush(QBrush(C("machine_green", "#2ecc71")))
            self.titleItem.setDefaultTextColor(C("machine_text_white", "#ffffff"))
            self.subItem.setDefaultTextColor(C("machine_text_white", "#ffffff"))
        elif self._status == STATUS_RED:
            self.setBrush(QBrush(C("machine_red", "#e74c3c")))
            self.titleItem.setDefaultTextColor(C("machine_text_white", "#ffffff"))
            self.subItem.setDefaultTextColor(C("machine_text_white", "#ffffff"))
        elif self._status == STATUS_YELLOW:
            self.setBrush(QBrush(C("machine_yellow", "#f1c40f")))
            self.titleItem.setDefaultTextColor(C("machine_text_default", "#1e1e1e"))
            self.subItem.setDefaultTextColor(C("machine_text_default", "#1e1e1e"))
        else:
            self.setBrush(QBrush(C("machine_default_bg", "#c8c8c8")))
            self.titleItem.setDefaultTextColor(C("machine_text_default", "#1e1e1e"))
            self.subItem.setDefaultTextColor(C("machine_text_default", "#1e1e1e"))

    def contextMenuEvent(self, event):
        menu = QMenu()
        info_act = menu.addAction("Info…")
        edit_act = menu.addAction("Edit Box…")
        lock_act = menu.addAction("Lock" if not self.box.locked else "Unlock")
        menu.addSeparator()
        override_menu = menu.addMenu("Manual Override")
        dead_act = override_menu.addAction("DEAD-LINE")
        dead_act.setCheckable(True)
        serv_act = override_menu.addAction("SERVICE")
        serv_act.setCheckable(True)
        # reflect current state
        dead_act.setChecked(self.box.manual_override == STATUS_DEAD)
        serv_act.setChecked(self.box.manual_override == STATUS_SERVICE)
        menu.addSeparator()
        remove_act = menu.addAction("Remove")

        chosen = menu.exec_(event.screenPos())
        if not chosen:
            return

        if chosen == info_act:
            self.scene().parent_window.open_box_info(self.box, self._status, self._reason, self._info_lines)
        elif chosen == edit_act:
            self.scene().parent_window.edit_box(self.box)
        elif chosen == lock_act:
            self.box.locked = not self.box.locked
            self._apply_handle_visibility()
            self.scene().parent_window.save_config()
        elif chosen == remove_act:
            if QMessageBox.question(None, "Remove Box", f"Remove '{self.box.title}'?") == QMessageBox.Yes:
                self.scene().parent_window.remove_box(self.box.uid)
        elif chosen in (dead_act, serv_act):
            # Toggle logic: selecting an already-checked action will uncheck it (turn override off)
            # Build action text for logging
            just_checked = bool(chosen.isChecked())
            action_base = "DEAD-LINE" if chosen == dead_act else "SERVICE"
            action_txt = f"{action_base}: {'ON' if just_checked else 'OFF'}"
            # Prompt for user + note on both check and uncheck
            dlg = QDialog(self.scene().parent_window)
            dlg.setModal(True)
            dlg.setWindowTitle("Manual Override")
            dlg.setMinimumWidth(420)
            form = QFormLayout()
            user_edit = QLineEdit(); note_edit = QTextEdit()
            user_edit.setPlaceholderText("Your name")
            note_edit.setPlaceholderText("Optional comment")
            form.addRow("Action:", QLabel(action_txt))
            form.addRow("Name:", user_edit)
            form.addRow("Comment:", note_edit)
            buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            buttons.button(QDialogButtonBox.Ok).setText("Apply")
            def _on_apply() -> None:
                uname = user_edit.text().strip()
                note = note_edit.toPlainText().strip()
                if not uname or not note:
                    QMessageBox.warning(dlg, "Required", "Please enter your name and a comment.")
                    return
                dlg.accept()
            buttons.accepted.connect(_on_apply)
            buttons.rejected.connect(dlg.reject)
            lay = QVBoxLayout(dlg)
            lay.addLayout(form)
            lay.addWidget(buttons)
            if dlg.exec_() != QDialog.Accepted:
                # Revert UI check state if user cancels
                if chosen == dead_act:
                    dead_act.setChecked(self.box.manual_override == STATUS_DEAD)
                else:
                    serv_act.setChecked(self.box.manual_override == STATUS_SERVICE)
                return
            user = user_edit.text().strip()
            note = note_edit.toPlainText().strip()

            # Apply new override respecting mutual exclusivity
            if chosen == dead_act:
                if just_checked:
                    self.box.manual_override = STATUS_DEAD
                    serv_act.setChecked(False)
                else:
                    self.box.manual_override = ""
            else:  # SERVICE
                if just_checked:
                    self.box.manual_override = STATUS_SERVICE
                    dead_act.setChecked(False)
                else:
                    self.box.manual_override = ""
            self.apply_visuals()
            self.scene().parent_window.save_config()
            try:
                self.scene().parent_window._log_manual_override(self.box, action_txt, user, note)
            except Exception:
                pass

    def itemChange(self, change, value):
        if change == QGraphicsRectItem.ItemPositionChange:
            if self.isSelected() and not self.box.locked and not self.is_map_locked():
                new_pos: QPointF = value
                g = self.grid_size()
                sx = round(new_pos.x() / g) * g
                sy = round(new_pos.y() / g) * g
                return QPointF(sx, sy)
        elif change == QGraphicsRectItem.ItemPositionHasChanged:
            self.box.pos = (self.pos().x(), self.pos().y())
            if hasattr(self.scene(), "parent_window"):
                self.scene().parent_window.save_config()
        elif change == QGraphicsRectItem.ItemSceneHasChanged:
            self._apply_handle_visibility()
            self._position_handle()
        return super().itemChange(change, value)
