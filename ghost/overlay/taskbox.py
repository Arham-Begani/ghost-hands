"""F7 typed-task box: a small glass input. Remembers the window the user was
in, and gives focus straight back to it after Enter / Esc."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPen
from PyQt6.QtWidgets import QLineEdit, QWidget

from . import theme as T
from .theme import s

log = logging.getLogger("ghost.taskbox")
u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
u32.GetForegroundWindow.restype = wintypes.HWND
u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetWindowThreadProcessId.restype = wintypes.DWORD
u32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
u32.SetForegroundWindow.argtypes = [wintypes.HWND]
u32.BringWindowToTop.argtypes = [wintypes.HWND]
u32.IsWindow.argtypes = [wintypes.HWND]


def force_foreground(hwnd: int) -> bool:
    """SetForegroundWindow that survives Windows' foreground lock: briefly
    attach our input queue to the current foreground thread."""
    if not hwnd or not u32.IsWindow(hwnd):
        return False
    fg = u32.GetForegroundWindow()
    cur = k32.GetCurrentThreadId()
    fg_tid = u32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = bool(fg_tid and fg_tid != cur and u32.AttachThreadInput(cur, fg_tid, True))
    try:
        u32.BringWindowToTop(hwnd)
        ok = bool(u32.SetForegroundWindow(hwnd))
    finally:
        if attached:
            u32.AttachThreadInput(cur, fg_tid, False)
    return ok


class _Edit(QLineEdit):
    def __init__(self, box: "TaskBox") -> None:
        super().__init__(box)
        self.box = box

    def keyPressEvent(self, e) -> None:
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.box.submit()
        elif e.key() == Qt.Key.Key_Escape:
            self.box.close_box()
        else:
            super().keyPressEvent(e)

    def focusOutEvent(self, e) -> None:
        super().focusOutEvent(e)
        QTimer.singleShot(120, self.box.on_focus_lost)


class TaskBox(QWidget):
    W, H = 640, 76

    def __init__(self, on_submit) -> None:
        super().__init__(None)
        self.on_submit = on_submit
        self.saved_hwnd = 0
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        m = round(s(20))                       # room for the glass shadow
        self.m = m
        self.resize(round(s(self.W)) + 2 * m, round(s(self.H)) + 2 * m)
        self.edit = _Edit(self)
        self.edit.setFrame(False)
        self.edit.setFont(T.font(22, T.SEMI))
        self.edit.setPlaceholderText("What should the ghost show you?")
        self.edit.setStyleSheet(
            "QLineEdit { background: transparent; color: #F5F7FF; border: none;"
            " selection-background-color: rgba(139,124,255,0.55); }"
        )
        pal = self.edit.palette()
        pal.setColor(pal.ColorRole.PlaceholderText, QColor(245, 247, 255, 110))
        self.edit.setPalette(pal)
        self.edit.setGeometry(m + round(s(58)), m, round(s(self.W - 58 - 92)), round(s(self.H)))

    def open(self, screen_w: int, top: float) -> None:
        if self.isVisible():
            return
        fg = u32.GetForegroundWindow()
        self.saved_hwnd = int(fg) if fg else 0
        self.edit.clear()
        self.move(round((screen_w - self.width()) / 2), round(top))
        self.show()
        self.raise_()
        self.activateWindow()
        force_foreground(int(self.winId()))
        self.edit.setFocus()

    def submit(self) -> None:
        text = self.edit.text().strip()
        self.close_box()
        if text:
            # let focus settle back in the user's app before the first screenshot
            QTimer.singleShot(200, lambda: self.on_submit(text))

    def close_box(self) -> None:
        if not self.isVisible():
            return
        self.hide()
        force_foreground(self.saved_hwnd)

    def on_focus_lost(self) -> None:
        # clicked elsewhere: just disappear, don't steal focus back
        if self.isVisible() and not self.isActiveWindow():
            self.hide()

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.m, self.m, self.width() - 2 * self.m, self.height() - 2 * self.m)
        T.glass(p, r, r.height() / 2)
        # ghost glyph: small gradient orb
        c = QPointF(r.left() + s(32), r.center().y())
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.alpha(T.VIOLET, 0.25))
        p.drawEllipse(c, s(13), s(13))
        g = T.glow_gradient(QRectF(c.x() - s(7), c.y() - s(7), s(14), s(14)))
        p.setBrush(g)
        p.drawEllipse(c, s(7), s(7))
        # enter hint
        f = T.font(13, T.SEMI)
        fm = QFontMetricsF(f)
        label = "Enter ↵"
        w = fm.horizontalAdvance(label) + s(20)
        hr = QRectF(r.right() - s(20) - w, r.center().y() - s(14), w, s(28))
        p.setPen(QPen(QColor(245, 247, 255, 60), 1))
        p.setBrush(QColor(255, 255, 255, 14))
        p.drawRoundedRect(hr, s(8), s(8))
        p.setFont(f)
        p.setPen(QColor(245, 247, 255, 150))
        p.drawText(hr, Qt.AlignmentFlag.AlignCenter, label)
        p.end()
