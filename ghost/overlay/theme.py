"""Design tokens + small drawing helpers shared by every overlay layer.

All overlay drawing happens in PHYSICAL pixels (main.py disables Qt high-DPI
scaling), and every size below is a 100%-scale value multiplied by `UI`.
"""
from __future__ import annotations

import math
from collections import OrderedDict

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

# ---------------------------------------------------------------- palette
GHOST = QColor("#F5F7FF")
VIOLET = QColor("#8B7CFF")
CYAN = QColor("#3EE7FF")
INK = QColor(10, 12, 20, round(0.82 * 255))
AMBER = QColor("#FFB547")
MINT = QColor("#4BE3A1")
CORAL = QColor("#FF5C7A")
WHITE = QColor(255, 255, 255)

UI = 1.0  # set once by app.Overlay from the system DPI


def s(v: float) -> float:
    return v * UI


# ---------------------------------------------------------------- type
_FAMILY: str | None = None


def family() -> str:
    global _FAMILY
    if _FAMILY is None:
        have = set(QFontDatabase.families())
        for name in ("Segoe UI Variable Display", "Segoe UI Variable Text", "Segoe UI"):
            if name in have:
                _FAMILY = name
                break
        else:
            _FAMILY = QFont().defaultFamily()
    return _FAMILY


def font(px: float, weight: QFont.Weight = QFont.Weight.Normal, spacing: float = 0.0) -> QFont:
    f = QFont(family())
    f.setPixelSize(max(1, round(s(px))))
    f.setWeight(weight)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, s(spacing))
    return f


SEMI = QFont.Weight.DemiBold
BOLD = QFont.Weight.Bold


# ---------------------------------------------------------------- math
def clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if v < lo else hi if v > hi else v


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_in_out_cubic(t: float) -> float:
    return 4 * t * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_cubic(t: float) -> float:
    return 1 - (1 - t) ** 3


def ease_out_back(t: float, k: float = 1.70158) -> float:
    t -= 1
    return 1 + (k + 1) * t * t * t + k * t * t


def pulse(now: float, period: float, phase: float = 0.0) -> float:
    """0..1 sine wave."""
    return 0.5 + 0.5 * math.sin((now / period + phase) * math.tau)


def alpha(c: QColor, a: float) -> QColor:
    out = QColor(c)
    out.setAlphaF(clamp(c.alphaF() * a))
    return out


def mix(a: QColor, b: QColor, t: float) -> QColor:
    t = clamp(t)
    return QColor.fromRgbF(
        lerp(a.redF(), b.redF(), t), lerp(a.greenF(), b.greenF(), t),
        lerp(a.blueF(), b.blueF(), t), lerp(a.alphaF(), b.alphaF(), t),
    )


def glow_gradient(r: QRectF, a: float = 1.0) -> QLinearGradient:
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0.0, alpha(VIOLET, a))
    g.setColorAt(1.0, alpha(CYAN, a))
    return g


# ---------------------------------------------------------------- glass
_GLASS: "OrderedDict[tuple, QPixmap]" = OrderedDict()


def glass(p: QPainter, r: QRectF, radius: float, opacity: float = 1.0,
          fill: QColor = INK, edge: QColor | None = None) -> None:
    """Dark glass panel: soft drop shadow, ink fill, top sheen, hairline edge.
    `edge` tints the hairline (default: violet->cyan glow gradient).
    Prebaked per size into a pixmap, so a frame costs one blit."""
    if opacity <= 0.003:
        return
    w, h = max(1, round(r.width())), max(1, round(r.height()))
    key = (w, h, round(radius, 1), fill.rgba(), edge.rgba() if edge is not None else None)
    pm = _GLASS.get(key)
    m = round(s(18))
    if pm is None:
        pm = QPixmap(w + 2 * m, h + 2 * m)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        _glass_draw(q, QRectF(m, m, w, h), radius, fill, edge)
        q.end()
        _GLASS[key] = pm
        if len(_GLASS) > 160:
            _GLASS.popitem(last=False)
    else:
        _GLASS.move_to_end(key)
    p.save()
    p.setOpacity(p.opacity() * opacity)
    p.drawPixmap(QPointF(r.left() - m, r.top() - m), pm)
    p.restore()


def _glass_draw(p: QPainter, r: QRectF, radius: float, fill: QColor, edge: QColor | None) -> None:
    opacity = 1.0
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    # shadow: a few stacked translucent rounded rects (cheap, no blur)
    for i, a in enumerate((0.10, 0.07, 0.05, 0.03)):
        g = s(2 + i * 3)
        p.setBrush(QColor(0, 0, 0, round(255 * a * opacity)))
        p.drawRoundedRect(r.adjusted(-g, -g + s(4), g, g + s(6)), radius + g, radius + g)
    p.setBrush(alpha(fill, opacity))
    p.drawRoundedRect(r, radius, radius)
    # sheen on the upper half
    sheen = QLinearGradient(r.topLeft(), QPointF(r.left(), r.top() + r.height() * 0.6))
    sheen.setColorAt(0.0, QColor(255, 255, 255, round(22 * opacity)))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    p.setBrush(sheen)
    p.drawRoundedRect(r, radius, radius)
    # hairline edge
    p.setBrush(Qt.BrushStyle.NoBrush)
    if edge is None:
        pen = QPen(glow_gradient(r, 0.55 * opacity), max(1.0, s(1.1)))
    else:
        pen = QPen(alpha(edge, 0.85 * opacity), max(1.0, s(1.3)))
    p.setPen(pen)
    half = pen.widthF() / 2
    p.drawRoundedRect(r.adjusted(half, half, -half, -half), radius - half, radius - half)
    p.restore()


def rounded_polygon(pts: list[tuple[float, float]], radii: list[float]) -> QPainterPath:
    """Closed polygon with each corner rounded by a quadratic curve."""
    n = len(pts)
    path = QPainterPath()
    for i in range(n):
        px, py = pts[i - 1]
        cx, cy = pts[i]
        nx, ny = pts[(i + 1) % n]
        r = radii[i]
        d1 = math.hypot(px - cx, py - cy) or 1
        d2 = math.hypot(nx - cx, ny - cy) or 1
        r1, r2 = min(r, d1 / 2), min(r, d2 / 2)
        a = QPointF(cx + (px - cx) / d1 * r1, cy + (py - cy) / d1 * r1)
        b = QPointF(cx + (nx - cx) / d2 * r2, cy + (ny - cy) / d2 * r2)
        if i == 0:
            path.moveTo(a)
        else:
            path.lineTo(a)
        path.quadTo(QPointF(cx, cy), b)
    path.closeSubpath()
    return path
