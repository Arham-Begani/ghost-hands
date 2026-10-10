"""The ghost cursor: hand-drawn arrow, prebaked glow, comet trail, arc glide,
idle bob, thinking orbit, and every per-action cue (ripple, typing chip,
key caps, scroll chevrons, drag path, context-menu glyph)."""
from __future__ import annotations

import math
from collections import deque

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPen, QPixmap

from . import theme as T
from .theme import s

# Windows arrow at 100 % (about 12 x 19 px). Tip is the hotspot at (0, 0).
_ARROW = [(0, 0), (0, 16.6), (4.0, 12.9), (6.9, 19.3), (9.7, 18.1), (6.9, 11.9), (11.9, 11.9)]
_ARROW_R = [0.7, 1.1, 0.5, 1.0, 1.0, 0.5, 1.1]

TRAIL_LEN = 22
RIPPLE_S = 0.55
CUE_FADE = 0.2

_KEY_NAMES = {
    "CTRL": "Ctrl", "CONTROL": "Ctrl", "SHIFT": "Shift", "ALT": "Alt", "META": "Win", "WIN": "Win",
    "CMD": "Win", "SUPER": "Win", "ENTER": "Enter", "RETURN": "Enter", "ESC": "Esc", "ESCAPE": "Esc",
    "TAB": "Tab", "SPACE": "Space", "BACKSPACE": "⌫", "DELETE": "Del", "DEL": "Del",
    "ARROWUP": "↑", "UP": "↑", "ARROWDOWN": "↓", "DOWN": "↓", "ARROWLEFT": "←", "LEFT": "←",
    "ARROWRIGHT": "→", "RIGHT": "→", "HOME": "Home", "END": "End", "PAGEUP": "PgUp", "PAGEDOWN": "PgDn",
}


def key_label(k: str) -> str:
    u = k.strip().upper()
    if u in _KEY_NAMES:
        return _KEY_NAMES[u]
    return u if len(u) <= 1 else u.capitalize()


class GhostCursor:
    def __init__(self) -> None:
        k = 1.6 * T.UI
        self.path = T.rounded_polygon([(x * k, y * k) for x, y in _ARROW], [r * k for r in _ARROW_R])
        self.body_rect = self.path.boundingRect()
        self._bake_glow()

        self.pos = QPointF(-1000, -1000)       # hotspot, without idle bob
        self.vis = 0.0
        self.vis_target = 0.0
        self.appear_t0 = -10.0
        self.glide: dict | None = None
        self.arrive_t = 0.0
        self.trail: deque[QPointF] = deque(maxlen=TRAIL_LEN)
        self.thinking = False
        self.think_amt = 0.0
        self.idle_amt = 0.0
        self.tilt = 0.0
        self.squash_t0 = -10.0
        self.ripples: list[tuple[float, QPointF, QColor | None]] = []
        self.cues: list[dict] = []
        self.chip_pos = QPointF(0, 0)          # set by the overlay's layout pass
        self._prev = QPointF(self.pos)
        self.font_chip = T.font(18, T.SEMI)
        self.font_key = T.font(16, T.SEMI)
        self.font_key_plus = T.font(20, T.BOLD)

    # ------------------------------------------------------------ prebake
    def _bake_glow(self) -> None:
        pad = s(28)
        br = self.body_rect
        pm = QPixmap(int(br.width() + 2 * pad), int(br.height() + 2 * pad))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.glow_origin = QPointF(pad - br.x(), pad - br.y())
        p.translate(self.glow_origin)
        layers = 13
        for i in range(layers):
            w = s(2 * pad / T.UI) * (1 - i / layers) + s(2)
            a = 0.05 if i < layers - 2 else (0.35 if i == layers - 2 else 0.9)
            pen = QPen(T.glow_gradient(br, a), w)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.strokePath(self.path, pen)
        p.end()
        self.glow = pm

    # ------------------------------------------------------------ control
    @property
    def visible(self) -> bool:
        return self.vis_target > 0 or self.vis > 0.01

    def show(self, at: QPointF, now: float) -> None:
        if self.vis < 0.05:
            self.pos = QPointF(at)
            self._prev = QPointF(at)
            self.trail.clear()
            self.appear_t0 = now
            self.arrive_t = now
        self.vis_target = 1.0

    def hide(self) -> None:
        self.vis_target = 0.0

    def kill(self) -> None:
        """Vanish instantly (the dissolve particles stand in for it)."""
        self.vis = self.vis_target = 0.0
        self.glide = None
        self.trail.clear()
        self.thinking = False
        self.clear_cues(0.0)

    def glide_to(self, x: float, y: float, dur: float, now: float, on_arrive=None, arc: bool = True) -> None:
        a, b = QPointF(self.pos), QPointF(x, y)
        d = b - a
        dist = math.hypot(d.x(), d.y())
        if dist < 2 or dur <= 0:
            self.pos = b
            self.glide = None
            self.arrive_t = now
            if on_arrive:
                on_arrive()
            return
        perp = QPointF(-d.y() / dist, d.x() / dist)
        if perp.y() > 0:                       # always bow upward: reads as a lifted hand
            perp = -perp
        ctrl = (a + b) / 2 + perp * (0.12 * dist if arc else 0.0)
        self.glide = {"a": a, "b": b, "c": ctrl, "t0": now, "dur": dur, "cb": on_arrive}

    def click(self, now: float, double: bool = False, color: QColor | None = None) -> None:
        self.squash_t0 = now
        self.ripples.append((now, QPointF(self.pos), color))
        if double:
            self.ripples.append((now + 0.16, QPointF(self.pos), color))

    def ripple_at(self, pt: QPointF, now: float, color: QColor | None = None) -> None:
        self.ripples.append((now, QPointF(pt), color))

    def clear_cues(self, now: float) -> None:
        for c in self.cues:
            if c["end"] is None:
                c["end"] = now

    def cue(self, kind: str, now: float, **data) -> None:
        self.clear_cues(now)
        self.cues.append({"kind": kind, "t0": now, "end": None, **data})

    def active_cue(self, kind: str) -> dict | None:
        for c in self.cues:
            if c["kind"] == kind and c["end"] is None:
                return c
        return None

    # ------------------------------------------------------------ chip (type)
    def chip_text(self, c: dict, now: float) -> str:
        n = int(max(0.0, now - c["start"]) / 0.03)
        return c["text"][:n]

    def chip_size(self, now: float) -> QRectF | None:
        c = self.active_cue("type")
        if not c:
            return None
        fm = QFontMetricsF(self.font_chip)
        shown = self.chip_text(c, now)
        w = max(s(120), fm.horizontalAdvance(shown) + s(48))
        return QRectF(0, 0, min(w, s(620)), s(44))

    # ------------------------------------------------------------ frame
    def update(self, now: float, dt: float) -> None:
        self.vis += (self.vis_target - self.vis) * min(1.0, dt * 9)
        if self.vis_target == 0 and self.vis < 0.01:
            self.vis = 0.0

        if self.glide:
            g = self.glide
            k = T.clamp((now - g["t0"]) / g["dur"])
            e = T.ease_in_out_cubic(k)
            u = 1 - e
            self.pos = g["a"] * (u * u) + g["c"] * (2 * u * e) + g["b"] * (e * e)
            if k >= 1:
                self.pos = QPointF(g["b"])
                self.glide = None
                self.arrive_t = now
                if g["cb"]:
                    g["cb"]()

        vel = (self.pos - self._prev) / max(dt, 1e-3)
        self._prev = QPointF(self.pos)
        target_tilt = T.clamp(vel.x() * 0.006, -7, 7)
        self.tilt += (target_tilt - self.tilt) * min(1.0, dt * 10)

        moving = self.glide is not None
        idle_goal = 0.0 if moving else T.clamp((now - self.arrive_t - 0.25) / 0.6)
        self.idle_amt += (idle_goal - self.idle_amt) * min(1.0, dt * 6)
        self.think_amt += ((1.0 if self.thinking else 0.0) - self.think_amt) * min(1.0, dt * 5)

        if self.vis > 0.05:
            self.trail.append(self.pos + QPointF(s(3), s(6)))
        elif self.trail:
            self.trail.popleft()

        self.ripples = [r for r in self.ripples if now - r[0] < RIPPLE_S]
        self.cues = [c for c in self.cues if c["end"] is None or now - c["end"] < CUE_FADE]

    def tip(self, now: float) -> QPointF:
        bob = s(2) * math.sin(now * math.tau / 2.2) * self.idle_amt
        return QPointF(self.pos.x(), self.pos.y() + bob)

    def rects(self, now: float) -> list[QRectF]:
        out: list[QRectF] = []
        if self.vis > 0 or self.trail:
            t = self.pos
            m = s(120)
            out.append(QRectF(t.x() - m, t.y() - m, 2 * m, 2 * m))
        if len(self.trail) > 1:
            xs = [p.x() for p in self.trail]
            ys = [p.y() for p in self.trail]
            m = s(16)
            out.append(QRectF(min(xs) - m, min(ys) - m, max(xs) - min(xs) + 2 * m, max(ys) - min(ys) + 2 * m))
        for t0, c, _ in self.ripples:
            m = s(64)
            out.append(QRectF(c.x() - m, c.y() - m, 2 * m, 2 * m))
        for c in self.cues:
            if c["kind"] == "type":
                r = self.chip_size(now) or QRectF(0, 0, s(620), s(44))
                out.append(QRectF(self.chip_pos, r.size()).adjusted(-s(20), -s(20), s(20), s(24)))
            elif c["kind"] == "drag":
                a, b = c["a"], c["b"]
                out.append(QRectF(a, b).normalized().adjusted(-s(30), -s(30), s(30), s(30)))
        return out

    # ------------------------------------------------------------ paint
    def paint(self, p: QPainter, now: float) -> None:
        for t0, c, col in self.ripples:
            self._paint_ripple(p, now, t0, c, col)
        for c in self.cues:
            if c["kind"] == "drag":
                self._paint_drag(p, now, c)
        self._paint_trail(p)
        if self.vis <= 0.004:
            for c in self.cues:
                if c["kind"] == "type":
                    self._paint_chip(p, now, c)
            return

        tip = self.tip(now)
        k = T.clamp((now - self.appear_t0) / 0.45)
        appear = 0.55 + 0.45 * T.ease_out_back(k)
        sx = sy = appear
        q = (now - self.squash_t0) / 0.18
        if 0 <= q < 1:
            b = math.sin(math.pi * q)
            sx *= 1 + 0.10 * b
            sy *= 1 - 0.20 * b

        p.save()
        p.translate(tip)
        p.rotate(self.tilt)
        p.scale(sx, sy)
        breath = 0.72 + 0.28 * T.pulse(now, 2.6)
        p.setOpacity(self.vis * breath)
        p.drawPixmap(-self.glow_origin, self.glow)
        p.setOpacity(self.vis)
        p.setPen(Qt.PenStyle.NoPen)                     # contact shadow: definition on white apps
        p.setBrush(QColor(10, 12, 20, 70))
        p.drawPath(self.path.translated(s(0.6), s(1.6)))
        fill = QLinearGradient(QPointF(0, 0), QPointF(0, self.body_rect.bottom()))
        fill.setColorAt(0, T.GHOST)
        fill.setColorAt(1, QColor("#E3E6FF"))
        p.setBrush(fill)
        pen = QPen(T.WHITE, 1.0)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPath(self.path)
        p.restore()

        if self.think_amt > 0.01:
            self._paint_orbit(p, now, tip)
        for c in self.cues:
            fn = {"type": self._paint_chip, "keys": self._paint_keys, "scroll": self._paint_scroll,
                  "menu": self._paint_menu}.get(c["kind"])
            if fn:
                fn(p, now, c)

    def _cue_alpha(self, now: float, c: dict, fade_in: float = 0.15) -> float:
        a = T.clamp((now - c["t0"]) / fade_in)
        if c["end"] is not None:
            a *= 1 - T.clamp((now - c["end"]) / CUE_FADE)
        return a

    def _paint_trail(self, p: QPainter) -> None:
        pts = list(self.trail)
        n = len(pts)
        if n < 2:
            return
        p.save()
        for soft in (True, False):
            for i in range(1, n):
                a, b = pts[i - 1], pts[i]
                if abs(a.x() - b.x()) + abs(a.y() - b.y()) < 0.6:
                    continue
                f = i / (n - 1)                     # 1 = newest (at the cursor)
                if f > 0.5:
                    col = T.mix(T.CYAN, T.VIOLET, (f - 0.5) * 2)
                else:
                    col = T.alpha(T.CYAN, (f * 2) ** 1.4)
                w = s(1.5) + s(8.5) * f ** 1.3
                if soft:
                    col = T.alpha(col, 0.16 * self.vis)
                    w *= 2.8
                else:
                    col = T.alpha(col, 0.9 * self.vis)
                pen = QPen(col, w)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawLine(a, b)
        p.restore()

    def _paint_ripple(self, p: QPainter, now: float, t0: float, c: QPointF, col: QColor | None) -> None:
        k = (now - t0) / RIPPLE_S
        if k < 0:
            return
        e = T.ease_out_cubic(k)
        r = s(56) * e
        fade = (1 - k) ** 1.6
        ring = col if col is not None else T.mix(T.VIOLET, T.CYAN, k)
        p.save()
        p.setBrush(T.alpha(ring, 0.16 * fade))
        p.setPen(QPen(T.alpha(ring, 0.95 * fade), s(1) + s(3) * (1 - k)))
        p.drawEllipse(c, r, r)
        if k < 0.35:                                  # bright contact dot
            d = s(7) * (1 - k / 0.35)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(T.alpha(T.WHITE, 0.9 * (1 - k / 0.35)))
            p.drawEllipse(c, d, d)
        p.restore()

    def _paint_orbit(self, p: QPainter, now: float, tip: QPointF) -> None:
        center = tip + QPointF(s(8), s(13))
        rad = s(27)
        ang = now * math.tau / 1.5
        p.save()
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(6):
            a = ang - i * 0.22
            pt = center + QPointF(math.cos(a) * rad, math.sin(a) * rad)
            f = 1 - i / 6
            p.setBrush(T.alpha(T.mix(T.CYAN, T.VIOLET, i / 5), self.think_amt * self.vis * f))
            r = s(1.2) + s(2.6) * f
            p.drawEllipse(pt, r, r)
        p.restore()

    def _paint_chip(self, p: QPainter, now: float, c: dict) -> None:
        r = self.chip_size(now)
        if r is None:
            r = QRectF(0, 0, c.get("last_w", s(120)), s(44))
        c["last_w"] = r.width()
        a = self._cue_alpha(now, c)
        r = QRectF(self.chip_pos, r.size())
        T.glass(p, r, s(10), a)
        p.save()
        p.setOpacity(a)
        # left accent: a tiny "text cursor" glyph
        p.setPen(QPen(T.CYAN, s(2)))
        cx, cy = r.left() + s(17), r.center().y()
        p.drawLine(QPointF(cx, cy - s(8)), QPointF(cx, cy + s(8)))
        p.drawLine(QPointF(cx - s(3), cy - s(8)), QPointF(cx + s(3), cy - s(8)))
        p.drawLine(QPointF(cx - s(3), cy + s(8)), QPointF(cx + s(3), cy + s(8)))
        p.setFont(self.font_chip)
        fm = QFontMetricsF(self.font_chip)
        txt = self.chip_text(c, now) if c["end"] is None else c["text"]
        tx = r.left() + s(32)
        avail = r.width() - s(48)
        txt = fm.elidedText(txt, Qt.TextElideMode.ElideLeft, avail)
        p.setPen(T.GHOST)
        base = r.center().y() + (fm.ascent() - fm.descent()) / 2
        p.drawText(QPointF(tx, base), txt)
        if T.pulse(now, 0.9) > 0.35:                 # blinking caret
            x = tx + fm.horizontalAdvance(txt) + s(2)
            p.setPen(QPen(T.CYAN, s(2)))
            p.drawLine(QPointF(x, r.center().y() - s(11)), QPointF(x, r.center().y() + s(11)))
        p.restore()

    def _paint_keys(self, p: QPainter, now: float, c: dict) -> None:
        labels = [key_label(k) for k in c["keys"]] or ["?"]
        fm = QFontMetricsF(self.font_key)
        h = s(38)
        widths = [max(h, fm.horizontalAdvance(t) + s(24)) for t in labels]
        plus_w = s(22)
        total = sum(widths) + plus_w * (len(labels) - 1)
        tip = self.tip(now)
        x = tip.x() + s(6) - total / 2
        y = tip.y() - s(62)
        base_a = self._cue_alpha(now, c, 0.01)
        pressed = 0.0
        pt = now - c["t0"] - 0.45 - 0.06 * len(labels)
        if 0 <= pt < 0.22:
            pressed = math.sin(math.pi * pt / 0.22)
        for i, (t, w) in enumerate(zip(labels, widths)):
            k = T.clamp((now - c["t0"] - i * 0.07) / 0.38)
            if k <= 0:
                x += w + plus_w
                continue
            sc = T.ease_out_back(k, 2.4)
            a = base_a * T.clamp(k * 3)
            r = QRectF(x, y + s(3) * pressed, w, h)
            p.save()
            p.translate(r.center())
            p.scale(sc, sc)
            p.translate(-r.center())
            edge = T.mix(T.WHITE, T.CYAN, pressed) if pressed else None
            T.glass(p, r, s(8), a, edge=edge)
            # key-cap lip
            p.setOpacity(a)
            p.setPen(QPen(QColor(255, 255, 255, 40), s(1)))
            p.drawLine(QPointF(r.left() + s(7), r.bottom() - s(4)), QPointF(r.right() - s(7), r.bottom() - s(4)))
            p.setFont(self.font_key)
            p.setPen(T.GHOST)
            p.drawText(r.adjusted(0, -s(2), 0, -s(2)), Qt.AlignmentFlag.AlignCenter, t)
            p.restore()
            x += w
            if i < len(labels) - 1:
                p.save()
                p.setOpacity(a)
                p.setFont(self.font_key_plus)
                p.setPen(T.VIOLET)
                p.drawText(QRectF(x, y, plus_w, h), Qt.AlignmentFlag.AlignCenter, "+")
                p.restore()
                x += plus_w

    def _paint_scroll(self, p: QPainter, now: float, c: dict) -> None:
        a = self._cue_alpha(now, c) * (1 - T.clamp((now - c["t0"] - 1.8) / 0.4))
        if a <= 0:
            return
        d = 1 if c["dy"] >= 0 else -1
        tip = self.tip(now)
        cx = tip.x() - s(30)
        cy = tip.y() + s(10)
        p.save()
        for i in range(3):
            ph = ((now - c["t0"]) * 1.6 + i / 3) % 1.0
            yy = cy + d * (ph - 0.5) * s(40)
            f = math.sin(math.pi * ph)
            col = T.mix(T.VIOLET, T.CYAN, ph)
            pen = QPen(T.alpha(col, a * f), s(3))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            w = s(9)
            p.drawPolyline([QPointF(cx - w, yy - d * s(5)), QPointF(cx, yy + d * s(4)), QPointF(cx + w, yy - d * s(5))])
        p.restore()

    def _paint_drag(self, p: QPainter, now: float, c: dict) -> None:
        a = self._cue_alpha(now, c)
        p.save()
        pen = QPen(T.alpha(T.CYAN, 0.9 * a), s(2.5))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setDashPattern([0.1, 2.6])
        pen.setDashOffset(-(now - c["t0"]) * 9)
        p.setPen(pen)
        p.drawLine(c["a"], c["b"])
        p.setPen(QPen(T.alpha(T.VIOLET, a), s(2)))
        p.setBrush(T.alpha(T.VIOLET, 0.18 * a))
        r = s(10) + s(3) * T.pulse(now, 1.0)
        p.drawEllipse(c["b"], r, r)
        p.restore()

    def _paint_menu(self, p: QPainter, now: float, c: dict) -> None:
        k = T.clamp((now - c["t0"]) / 0.3)
        a = self._cue_alpha(now, c)
        tip = self.tip(now)
        sz = s(30)
        r = QRectF(tip.x() + s(16), tip.y() - s(40), sz, sz)
        p.save()
        p.translate(r.center())
        sc = T.ease_out_back(k, 2.2)
        p.scale(sc, sc)
        p.translate(-r.center())
        T.glass(p, r, s(7), a)
        p.setOpacity(a)
        pen = QPen(T.GHOST, s(2))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        for i in (-1, 0, 1):
            y = r.center().y() + i * s(6)
            p.drawLine(QPointF(r.left() + s(8), y), QPointF(r.right() - s(8), y))
        p.restore()
