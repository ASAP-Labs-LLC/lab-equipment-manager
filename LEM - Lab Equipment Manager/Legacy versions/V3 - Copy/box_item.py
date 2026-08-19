#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
box_item.py â€” QGraphics items for the canvas: machine box + resize handle.
"""

from __future__ import annotations

from typing import List

from PyQt5.QtCore import QRectF, QPointF, Qt
from PyQt5.QtGui import QColor, QBrush, QPen, QFont
from PyQt5.QtWidgets import QGraphicsRectItem, QGraphicsTextItem, QMenu, QMessageBox

from models import BoxConfig, STATUS_GREEN, STATUS_RED, STATUS_YELLOW, STATUS_DEAD, STATUS_SERVICE, STATUS_UNKNOWN

COLOR_GREEN = QColor(46, 204, 113)
COLOR_RED = QColor(231, 76, 60)
COLOR_YELLOW = QColor(241, 196, 15)
COLOR_BLACK = QColor(0, 0, 0)
COLOR_BLACK_TEXT = QColor(220, 20, 60)
COLOR_SERVICE_BG = QColor(230, 230, 230)
COLOR_SERVICE_TEXT = QColor(60, 60, 60)
COLOR_TEXT_DEFAULT = QColor(30, 30, 30)
COLOR_TEXT_WHITE = QColor(255, 255, 255)
COLOR_BORDER = QColor(33, 33, 33)
COLOR_HANDLE = QColor(90, 90, 90)

MIN_W, MIN_H = 20.0, 20.0


class ResizeHandle(QGraphicsRectItem):
    def __init__(self, parent_box: "MachineBoxItem") -> None:
        super().__init__(parent_box)
        self._box = parent_box
        self.setRect(QRectF(0, 0, 14, 14))
        self.setBrush(QBrush(COLOR_HANDLE))
        self.setPen(QPen(COLOR_BORDER, 1))
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
        self.setPen(QPen(COLOR_BORDER, 2))
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
            ell = "â€¦" if raw_lines else ""
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
            self.setBrush(QBrush(COLOR_BLACK))
            self.titleItem.setDefaultTextColor(COLOR_BLACK_TEXT)
            self.subItem.setDefaultTextColor(COLOR_BLACK_TEXT)
            return
        if self.box.manual_override == STATUS_SERVICE:
            self.setBrush(QBrush(COLOR_SERVICE_BG))
            self.titleItem.setDefaultTextColor(COLOR_SERVICE_TEXT)
            self.subItem.setDefaultTextColor(COLOR_SERVICE_TEXT)
            return

        if self._status == STATUS_GREEN:
            self.setBrush(QBrush(COLOR_GREEN))
            self.titleItem.setDefaultTextColor(COLOR_TEXT_WHITE)
            self.subItem.setDefaultTextColor(COLOR_TEXT_WHITE)
        elif self._status == STATUS_RED:
            self.setBrush(QBrush(COLOR_RED))
            self.titleItem.setDefaultTextColor(COLOR_TEXT_WHITE)
            self.subItem.setDefaultTextColor(COLOR_TEXT_WHITE)
        elif self._status == STATUS_YELLOW:
            self.setBrush(QBrush(COLOR_YELLOW))
            self.titleItem.setDefaultTextColor(COLOR_TEXT_DEFAULT)
            self.subItem.setDefaultTextColor(COLOR_TEXT_DEFAULT)
        else:
            self.setBrush(QBrush(QColor(200, 200, 200)))
            self.titleItem.setDefaultTextColor(COLOR_TEXT_DEFAULT)
            self.subItem.setDefaultTextColor(COLOR_TEXT_DEFAULT)
    def contextMenuEvent(self, event):
        menu = QMenu()
        info_act = menu.addAction("Machine Info...")
        edit_act = menu.addAction("Edit Boxâ€¦")
        lock_act = menu.addAction("Lock" if not self.box.locked else "Unlock")
        menu.addSeparator()
        info_act.setText("Machine Info...")
        override_menu = menu.addMenu("Manual Override")
        off_act = override_menu.addAction("Off")
        dead_act = override_menu.addAction("DEAD-LINE")
        serv_act = override_menu.addAction("SERVICE")
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
        elif chosen in (off_act, dead_act, serv_act):
            if chosen == off_act:
                self.box.manual_override = ""
            elif chosen == dead_act:
                self.box.manual_override = STATUS_DEAD
            else:
                self.box.manual_override = STATUS_SERVICE
            self.apply_visuals()
            self.scene().parent_window.save_config()

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




