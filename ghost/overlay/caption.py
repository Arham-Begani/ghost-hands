"""Caption bubble: glass pill next to the ghost, typewriter text, step badge.
A new caption cross-fades over the old one, which freezes where it was."""
from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen

from . import theme as T
from .theme import s

CHAR_S = 0.035
FADE_S = 0.22
MAX_TEXT_W = 460


class Bubble:
    def __init__(self, text: str, step: int, now: float, accent: QColor | None, badge: str) -> None:
        self.text = text.strip()
        self.step = step
        self.t0 = now
        self.dead_t0: float | None = None
        self.accent = accent              # None = violet->cyan gradient
        self.badge = badge                # "num" | "lock" | "check" | "none"
        self.font = T.font(22, T.SEMI)
        self.badge_font = T.font(15, T.BOLD)
        fm = QFontMetricsF(self.font)
        self.line_h = fm.height()
        self.ascent = fm.ascent()
        self.lines = self._wrap(fm, self.text, s(MAX_TEXT_W))
        text_w = max((fm.horizontalAdvance(l) for l in self.lines), default=0)
        self.has_badge = badge != "none" and (badge != "num" or step > 0)
        self.pad_l = s(10) + (s(32) + s(12) if self.has_badge else s(8))
        self.pad_r = s(20)
        self.pad_v = s(13)
        h = max(s(54), len(self.lines) * self.line_h + 2 * self.pad_v)
        self.size = QRectF(0, 0, self.pad_l + text_w + self.pad_r, h)
        self.pos = QPointF(0, 0)
        self.placed = False

    @staticmethod
    def _wrap(fm: QFontMetricsF, text: str, max_w: float) -> list[str]:
        lines, cur = [], ""
        for word in text.split():
            trial = f"{cur} {word}".strip()
            if cur and fm.horizontalAdvance(trial) > max_w:
                lines.append(cur)
                cur = word
            else:
                cur = trial
        if cur:
            lines.append(cur)
        return lines[:3] or [""]

    def alpha(self, now: float) -> float:
        a = T.clamp((now - self.t0) / FADE_S)
        if self.dead_t0 is not None:
            a = min(a, 1 - T.clamp((now - self.dead_t0) / FADE_S))
        return a

    def rect(self) -> QRectF:
        return QRectF(self.pos, self.size.size())

    def paint(self, p: QPainter, now: float) -> None:
        a = self.alpha(now)
        if a <= 0.003:
            return
        r = self.rect()
        p.save()
        # entrance: tiny rise + scale; exit: drift up
        if self.dead_t0 is None:
            k = T.ease_out_cubic(T.clamp((now - self.t0) / 0.28))
            sc = 0.96 + 0.04 * k
            p.translate(r.center() + QPointF(0, s(6) * (1 - k)))
            p.scale(sc, sc)
            p.translate(-r.center())
        else:
            p.translate(0, -s(8) * T.clamp((now - self.dead_t0) / FADE_S))
        T.glass(p, r, s(14), a, edge=self.accent)
        p.setOpacity(a)
        if self.has_badge:
            self._paint_badge(p, r, now)
        # typewriter
        n = int(max(0.0, now - self.t0 - 0.06) / CHAR_S)
        p.setFont(self.font)
        y = r.top() + self.pad_v + self.ascent + (r.height() - 2 * self.pad_v - len(self.lines) * self.line_h) / 2
        fm = QFontMetricsF(self.font)
        caret = None
        for line in self.lines:
            show = line[:max(0, n)]
            n -= len(line) + 1
            if show:
                p.setPen(T.GHOST)
                p.drawText(QPointF(r.left() + self.pad_l, y), show)
            if 0 <= len(show) < len(line) and caret is None:
                caret = QPointF(r.left() + self.pad_l + fm.horizontalAdvance(show) + s(2), y)
            y += self.line_h
        if caret is not None and self.dead_t0 is None:
            col = self.accent if self.accent is not None else T.CYAN
            p.setPen(QPen(col, s(2.2)))
            p.drawLine(caret + QPointF(0, -self.ascent + s(3)), caret + QPointF(0, s(3)))
        p.restore()

    def _paint_badge(self, p: QPainter, r: QRectF, now: float) -> None:
        d = s(32)
        c = QPointF(r.left() + s(10) + d / 2, r.top() + min(r.height() / 2, s(27)))
        br = QRectF(c.x() - d / 2, c.y() - d / 2, d, d)
        k = T.clamp((now - self.t0) / 0.4)
        sc = T.ease_out_back(k, 2.0)
        p.save()
        p.translate(c)
        p.scale(sc, sc)
        p.translate(-c)
        p.setPen(Qt.PenStyle.NoPen)
        if self.accent is None:
            g = QLinearGradient(br.topLeft(), br.bottomRight())
            g.setColorAt(0, T.VIOLET)
            g.setColorAt(1, T.CYAN)
            p.setBrush(g)
        else:
            p.setBrush(self.accent)
        p.drawEllipse(br)
        p.setBrush(QColor(255, 255, 255, 40))                 # glossy cap
        p.drawChord(br.adjusted(s(2), s(1), -s(2), -s(2)), 30 * 16, 120 * 16)
        ink = QColor(10, 12, 20)
        if self.badge == "num":
            p.setFont(self.badge_font)
            p.setPen(ink)
            p.drawText(br.adjusted(0, -s(0.5), 0, 0), Qt.AlignmentFlag.AlignCenter, str(self.step))
        elif self.badge == "lock":
            paint_lock(p, c, s(16), ink)
        elif self.badge == "check":
            pen = QPen(ink, s(2.6))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            p.drawPolyline([c + QPointF(-s(7), 0), c + QPointF(-s(2), s(5)), c + QPointF(s(7), -s(5))])
        p.restore()


def paint_lock(p: QPainter, c: QPointF, size: float, col: QColor) -> None:
    """Vector padlock centred on c (no emoji font dependency)."""
    w, h = size * 0.82, size * 0.58
    body = QRectF(c.x() - w / 2, c.y() - h / 2 + size * 0.18, w, h)
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(col)
    p.drawRoundedRect(body, size * 0.12, size * 0.12)
    pen = QPen(col, size * 0.14)
    pen.setCapStyle(Qt.PenCapStyle.FlatCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    sw = w * 0.62
    shackle = QPainterPath()
    shackle.moveTo(c.x() - sw / 2, body.top())
    shackle.lineTo(c.x() - sw / 2, body.top() - size * 0.16)
    shackle.arcTo(QRectF(c.x() - sw / 2, body.top() - size * 0.16 - sw / 2, sw, sw), 180, -180)
    shackle.lineTo(c.x() + sw / 2, body.top())
    p.drawPath(shackle)
    p.restore()


class CaptionLayer:
    def __init__(self) -> None:
        self.current: Bubble | None = None
        self.dying: list[Bubble] = []

    def set(self, text: str, step: int, now: float, accent: QColor | None = None, badge: str = "num") -> None:
        self.clear(now)
        if text:
            self.current = Bubble(text, step, now, accent, badge)

    def clear(self, now: float) -> None:
        if self.current is not None:
            self.current.dead_t0 = now
            self.dying.append(self.current)
            self.current = None

    def place(self, target: QPointF, dt: float) -> None:
        """Ease the live bubble toward its layout target (springy follow)."""
        b = self.current
        if b is None:
            return
        if not b.placed:
            b.pos = QPointF(target)
            b.placed = True
        else:
            d = target - b.pos
            if abs(d.x()) + abs(d.y()) > s(60):           # flipped side: slide over
                b.pos = b.pos + d * min(1.0, dt * 14)
            else:                                         # glued to the ghost
                b.pos = QPointF(target)

    def update(self, now: float) -> None:
        self.dying = [b for b in self.dying if b.alpha(now) > 0.003]

    def rects(self) -> list[QRectF]:
        out = [b.rect().adjusted(-s(18), -s(18), s(18), s(24)) for b in self.dying]
        if self.current is not None:
            out.append(self.current.rect().adjusted(-s(18), -s(18), s(18), s(24)))
        return out

    def paint(self, p: QPainter, now: float) -> None:
        for b in self.dying:
            b.paint(p, now)
        if self.current is not None:
            self.current.paint(p, now)
