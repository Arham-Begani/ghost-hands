"""Top-centre HUD pill: pulsing state dot (+ live mic bars while listening),
state label, step counter, task text."""
from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPen

from . import theme as T
from .theme import s

STATE_STYLE: dict[str, tuple[str, QColor, float]] = {
    # state: (label, dot colour, pulse period s; 0 = steady)
    "idle": ("Ready", QColor(245, 247, 255, 150), 0),
    "listening": ("Listening", T.CYAN, 1.1),
    "transcribing": ("Hearing you", T.CYAN, 0.8),
    "thinking": ("Thinking", T.VIOLET, 0.9),
    "acting": ("Acting", T.CYAN, 1.6),
    "handover": ("Your call", T.AMBER, 1.2),
    "done": ("Done", T.MINT, 0),
    "error": ("Error", T.CORAL, 0.7),
    "aborted": ("You've got it", T.MINT, 0),
    "coach": ("Your turn", T.CYAN, 1.4),
    "replay": ("Replay", T.VIOLET, 1.4),
}


class Hud:
    def __init__(self, screen_w: int) -> None:
        self.screen_w = screen_w
        self.state = "idle"
        self.label = "Ready"
        self.step = 0
        self.total = 0
        self.task = ""
        self.note = ""                 # overrides the task slot until note_until
        self.note_until = 0.0
        self.after_note = ""
        self.hint = "Hold F8 to ask  ·  F7 to type"
        self.change_t0 = 0.0
        self.w = s(320)
        self.mic = [0.0] * 5
        self.f_label = T.font(15, T.SEMI)
        self.f_body = T.font(15)
        self.f_step = T.font(15, T.SEMI)
        self._rect = QRectF()
        self._last_w = 0.0

    # ------------------------------------------------------------ control
    def set_state(self, state: str, text: str, now: float, step: int | None = None, total: int | None = None) -> None:
        label = text or STATE_STYLE.get(state, (state.title(),))[0]
        if state != self.state or label != self.label:
            self.change_t0 = now
        self.state, self.label = state, label
        if step is not None and (step or state in ("idle", "thinking", "listening")):
            self.step = step
        if total is not None and total:
            self.total = total
        if state == "idle":
            self.step = self.total = 0

    def set_step(self, step: int) -> None:
        if step:
            self.step = step

    def set_task(self, text: str, now: float) -> None:
        self.task = text
        self.change_t0 = now
        self.step = self.total = 0

    def flash(self, text: str, now: float, secs: float, then: str = "") -> None:
        self.note, self.note_until, self.after_note = text, now + secs, then
        self.change_t0 = now

    @property
    def rect(self) -> QRectF:
        return self._rect

    def task_anchor(self) -> QPointF:
        return self._rect.center()

    # ------------------------------------------------------------ frame
    def _segments(self, now: float) -> tuple[str, str, str]:
        steps = ""
        if self.step:
            steps = f"Step {self.step} / {self.total}" if self.total else f"Step {self.step}"
        if self.note and now >= self.note_until:
            self.note, self.task = "", (self.after_note or self.task)
            self.change_t0 = now
        body = self.note or self.task or (self.hint if self.state == "idle" else "")
        return self.label, steps, body

    def update(self, now: float, dt: float) -> None:
        label, steps, body = self._segments(now)
        fl, fb, fs = QFontMetricsF(self.f_label), QFontMetricsF(self.f_body), QFontMetricsF(self.f_step)
        w = s(18) + self._dot_w() + s(10) + fl.horizontalAdvance(label)
        if steps:
            w += s(26) + fs.horizontalAdvance(steps)
        if body:
            w += s(26) + min(fb.horizontalAdvance(body), s(420))
        w += s(22)
        self.w += (w - self.w) * min(1.0, dt * 12)
        h = s(46)
        self._rect = QRectF((self.screen_w - self.w) / 2, s(16), self.w, h)
        if self.state == "listening":
            for i in range(len(self.mic)):
                target = random.random() ** 1.6 * (0.6 + 0.4 * math.sin(now * 7 + i))
                self.mic[i] += (target - self.mic[i]) * min(1.0, dt * 18)

    def _dot_w(self) -> float:
        return s(12) + (s(38) if self.state == "listening" else 0)

    def rects(self) -> list[QRectF]:
        return [self._rect.adjusted(-s(20), -s(16), s(20), s(24))]

    def paint(self, p: QPainter, now: float) -> None:
        r = self._rect
        T.glass(p, r, r.height() / 2)
        label, steps, body = self._segments(now)
        _, color, period = STATE_STYLE.get(self.state, ("", T.CYAN, 1.2))
        p.save()
        p.setClipRect(r.adjusted(1, 1, -1, -1))
        cy = r.center().y()
        x = r.left() + s(18)
        # state dot with pulsing halo
        dc = QPointF(x + s(6), cy)
        p.setPen(Qt.PenStyle.NoPen)
        if period:
            k = (now % period) / period
            p.setBrush(T.alpha(color, 0.45 * (1 - k)))
            rr = s(5) + s(9) * T.ease_out_cubic(k)
            p.drawEllipse(dc, rr, rr)
        p.setBrush(T.alpha(color, 0.25))
        p.drawEllipse(dc, s(8), s(8))
        p.setBrush(color)
        core = s(5) * (1 + (0.12 * math.sin(now * math.tau / period) if period else 0))
        p.drawEllipse(dc, core, core)
        x += s(12)
        if self.state == "listening":
            bx = x + s(8)
            for i, v in enumerate(self.mic):
                bh = s(4) + s(16) * v
                p.setBrush(T.mix(T.CYAN, T.VIOLET, i / 4))
                p.drawRoundedRect(QRectF(bx + i * s(6), cy - bh / 2, s(3), bh), s(1.5), s(1.5))
            x += s(38)
        x += s(10)
        fade = T.ease_out_cubic(T.clamp((now - self.change_t0) / 0.25))
        p.setOpacity(0.4 + 0.6 * fade)
        fl = QFontMetricsF(self.f_label)
        base = cy + (fl.ascent() - fl.descent()) / 2
        p.setFont(self.f_label)
        p.setPen(T.GHOST)
        p.drawText(QPointF(x, base), label)
        x += fl.horizontalAdvance(label)
        if steps:
            x = self._divider(p, x, cy)
            p.setFont(self.f_step)
            p.setPen(T.alpha(T.GHOST, 0.72))
            p.drawText(QPointF(x, base), steps)
            x += QFontMetricsF(self.f_step).horizontalAdvance(steps)
        if body:
            x = self._divider(p, x, cy)
            fb = QFontMetricsF(self.f_body)
            avail = max(s(10), r.right() - s(22) - x)
            txt = fb.elidedText(body, Qt.TextElideMode.ElideRight, avail)
            p.setFont(self.f_body)
            dim = 0.55 if (body == self.hint and not self.note) else 0.88
            p.setPen(T.alpha(T.GHOST, dim))
            p.drawText(QPointF(x, base), txt)
        p.restore()

    def _divider(self, p: QPainter, x: float, cy: float) -> float:
        x += s(13)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.alpha(T.GHOST, 0.28))
        p.drawEllipse(QPointF(x, cy), s(1.8), s(1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        return x + s(13)
