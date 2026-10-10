"""Screen-level effects: TaskHeard splash, HandOver lock ring, particle bursts
(done / dissolve), toast."""
from __future__ import annotations

import math
import random

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QPainter, QPen, QRadialGradient

from . import theme as T
from .caption import paint_lock
from .theme import s


class Particles:
    def __init__(self) -> None:
        self.ps: list[list] = []   # [x, y, vx, vy, t0, life, r, color]

    def burst(self, c: QPointF, now: float, colors: list[QColor], n: int = 40, speed: float = 260,
              life: float = 0.9, up: float = 0.0, spread: float = 0.0) -> None:
        for _ in range(n):
            a = random.uniform(0, math.tau)
            v = s(speed) * random.uniform(0.35, 1.0)
            ox = random.uniform(-spread, spread)
            oy = random.uniform(-spread, spread)
            self.ps.append([c.x() + ox, c.y() + oy, math.cos(a) * v, math.sin(a) * v - s(up), now,
                            life * random.uniform(0.6, 1.0), s(random.uniform(1.6, 4.2)), random.choice(colors)])

    def update(self, now: float, dt: float) -> None:
        keep = []
        for p in self.ps:
            if now - p[4] > p[5]:
                continue
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[2] *= 1 - 2.2 * dt
            p[3] = p[3] * (1 - 2.2 * dt) + s(40) * dt
            keep.append(p)
        self.ps = keep

    def rects(self) -> list[QRectF]:
        if not self.ps:
            return []
        xs = [p[0] for p in self.ps]
        ys = [p[1] for p in self.ps]
        m = s(10)
        return [QRectF(min(xs) - m, min(ys) - m, max(xs) - min(xs) + 2 * m, max(ys) - min(ys) + 2 * m)]

    def paint(self, p: QPainter, now: float) -> None:
        if not self.ps:
            return
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, _, _, t0, life, r, col in self.ps:
            k = (now - t0) / life
            p.setBrush(T.alpha(col, (1 - k) ** 1.3))
            rr = r * (1 - 0.5 * k)
            p.drawEllipse(QPointF(x, y), rr, rr)
        p.restore()


class Splash:
    """TaskHeard: big centred text, holds, then flies into the HUD pill."""
    IN, HOLD, FLY = 0.45, 1.5, 0.55

    def __init__(self, text: str, now: float, screen: QRectF, target: QPointF, on_land) -> None:
        self.text = text
        self.t0 = now
        self.screen = screen
        self.target = target
        self.on_land = on_land
        self.landed = False
        self.font = T.font(44, T.SEMI, spacing=2.5)
        fm = QFontMetricsF(self.font)
        self.tw = min(fm.horizontalAdvance(text), screen.width() * 0.8)
        self.text = fm.elidedText(text, Qt.TextElideMode.ElideRight, screen.width() * 0.8)
        self.th = fm.height()
        self.ascent = fm.ascent()

    @property
    def done(self) -> bool:
        return self.landed

    def update(self, now: float) -> None:
        if not self.landed and now - self.t0 >= self.IN + self.HOLD + self.FLY:
            self.landed = True
            self.on_land()

    def rects(self) -> list[QRectF]:
        c = self.screen.center()
        w, h = self.tw + s(400), self.th + s(260)
        return [QRectF(c.x() - w / 2, c.y() - h / 2, w, h),
                QRectF(self.target.x() - self.tw / 2 - s(40), self.target.y() - s(60), self.tw + s(80), s(120)),
                QRectF(min(c.x(), self.target.x()) - self.tw / 2 - s(40), self.target.y() - s(40),
                       self.tw + s(80), c.y() - self.target.y() + s(80))]

    def paint(self, p: QPainter, now: float) -> None:
        t = now - self.t0
        c = self.screen.center()
        k_in = T.ease_out_cubic(T.clamp(t / self.IN))
        k_fly = T.clamp((t - self.IN - self.HOLD) / self.FLY)
        e = T.ease_in_out_cubic(k_fly)
        pos = c + (self.target - c) * e
        scale = T.lerp(1.0, 15 / 44, e)
        a = k_in * (1 - T.clamp((k_fly - 0.6) / 0.4))
        if a <= 0:
            return
        p.save()
        # soft vignette behind the words for legibility on any app
        va = a * (1 - e)
        if va > 0.01:
            rw, rh = self.tw + s(320), self.th + s(200)
            # gradient lives in the translated/scaled space, centred on the ellipse
            rg = QRadialGradient(QPointF(0, 0), rw / 2)
            rg.setColorAt(0.0, QColor(10, 12, 20, round(150 * va)))
            rg.setColorAt(0.55, QColor(10, 12, 20, round(90 * va)))
            rg.setColorAt(1.0, QColor(10, 12, 20, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(rg)
            p.save()
            p.translate(c)
            p.scale(1.0, rh / rw)
            p.drawEllipse(QPointF(0, 0), rw / 2, rw / 2)
            p.restore()
        p.translate(pos + QPointF(0, s(14) * (1 - k_in)))
        p.scale(scale, scale)
        p.setOpacity(a)
        f = T.font(44, T.SEMI, spacing=T.lerp(9, 2.5, k_in))
        p.setFont(f)
        fm = QFontMetricsF(f)
        w = fm.horizontalAdvance(self.text)
        base = self.ascent / 2 - fm.descent() / 2
        # glow pass in violet, then the ghost-white face
        p.setPen(T.alpha(T.VIOLET, 0.45))
        for dx, dy in ((0, s(2)), (s(1.5), 0), (-s(1.5), 0)):
            p.drawText(QPointF(-w / 2 + dx, base + dy), self.text)
        p.setPen(T.GHOST)
        p.drawText(QPointF(-w / 2, base), self.text)
        # underline sweep
        uw = w * T.ease_out_cubic(T.clamp((t - 0.15) / 0.6))
        pen = QPen(T.glow_gradient(QRectF(-w / 2, 0, w, 1)), s(3))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(-uw / 2, base + s(16)), QPointF(uw / 2, base + s(16)))
        p.restore()


class HandOverFx:
    def __init__(self, c: QPointF, now: float) -> None:
        self.c = QPointF(c)
        self.t0 = now
        self.end: float | None = None

    def alive(self, now: float) -> bool:
        return self.end is None or now - self.end < 0.3

    def rects(self) -> list[QRectF]:
        m = s(90)
        return [QRectF(self.c.x() - m, self.c.y() - m, 2 * m, 2 * m)]

    def paint(self, p: QPainter, now: float) -> None:
        t = now - self.t0
        a = T.clamp(t / 0.3)
        if self.end is not None:
            a *= 1 - T.clamp((now - self.end) / 0.3)
        if a <= 0:
            return
        p.save()
        p.setBrush(Qt.BrushStyle.NoBrush)
        for i in range(2):
            k = ((t / 1.4) + i * 0.5) % 1.0
            r = s(30) + s(34) * T.ease_out_cubic(k)
            p.setPen(QPen(T.alpha(T.AMBER, a * (1 - k) * 0.9), s(2.5)))
            p.drawEllipse(self.c, r, r)
        p.setPen(QPen(T.alpha(T.AMBER, a), s(3)))
        p.setBrush(T.alpha(T.AMBER, 0.10 * a))
        r0 = s(30) + s(2) * T.pulse(now, 1.4)
        p.drawEllipse(self.c, r0, r0)
        # lock badge at the ring's upper right
        bc = self.c + QPointF(r0 * 0.72, -r0 * 0.72)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(T.alpha(T.AMBER, a))
        p.drawEllipse(bc, s(15), s(15))
        p.setOpacity(a)
        paint_lock(p, bc, s(15), QColor(10, 12, 20))
        p.restore()


class Toast:
    def __init__(self, text: str, c: QPointF, now: float, color: QColor, secs: float = 2.4) -> None:
        self.text, self.c, self.t0, self.color, self.secs = text, QPointF(c), now, color, secs
        self.font = T.font(22, T.SEMI)
        fm = QFontMetricsF(self.font)
        self.w = fm.horizontalAdvance(text) + s(56)
        self.h = s(54)

    def alive(self, now: float) -> bool:
        return now - self.t0 < self.secs

    def _rect(self, now: float) -> QRectF:
        t = now - self.t0
        rise = s(24) * T.ease_out_cubic(T.clamp(t / 0.5))
        return QRectF(self.c.x() - self.w / 2, self.c.y() - self.h / 2 - rise, self.w, self.h)

    def rects(self, now: float) -> list[QRectF]:
        return [self._rect(now).adjusted(-s(20), -s(30), s(20), s(30))]

    def paint(self, p: QPainter, now: float) -> None:
        t = now - self.t0
        a = T.clamp(t / 0.25) * (1 - T.clamp((t - self.secs + 0.4) / 0.4))
        r = self._rect(now)
        T.glass(p, r, r.height() / 2, a, edge=self.color)
        p.save()
        p.setOpacity(a)
        p.setFont(self.font)
        p.setPen(self.color)
        p.drawText(r, Qt.AlignmentFlag.AlignCenter, self.text)
        p.restore()
