"""Coach mode, "Your turn" (F9). No AI: replays runs/LAST as pulsing rings the
user clicks for real. pynput listeners feed a queue drained on the Qt thread.

Integration: call update / rects / paint EVERY frame (they are no-ops when idle);
Esc and any new run should call stop()."""
from __future__ import annotations

import json
import logging
import math
import os
import queue

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFontMetricsF, QLinearGradient, QPainter, QPen

from ghost.contracts import LAST_RUN_FILE

from . import theme as T
from .caption import Bubble
from .cursor import key_label
from .theme import s

log = logging.getLogger("ghost.coach")

SKIP = {"wait", "handover", "screenshot"}
HIT_PX = 45
SUCCESS_HOLD = 0.45
BANNER_S = 2.8
MODS = {"ctrl", "shift", "alt", "win"}

_STEP_KEY = {
    "CTRL": "ctrl", "CONTROL": "ctrl", "SHIFT": "shift", "ALT": "alt", "OPTION": "alt",
    "META": "win", "WIN": "win", "CMD": "win", "SUPER": "win", "COMMAND": "win",
    "ENTER": "enter", "RETURN": "enter", "ESC": "esc", "ESCAPE": "esc", "TAB": "tab", "SPACE": "space",
    "BACKSPACE": "backspace", "DELETE": "delete", "DEL": "delete", "HOME": "home", "END": "end",
    "PAGEUP": "page_up", "PGUP": "page_up", "PAGEDOWN": "page_down", "PGDN": "page_down",
    "ARROWUP": "up", "UP": "up", "ARROWDOWN": "down", "DOWN": "down",
    "ARROWLEFT": "left", "LEFT": "left", "ARROWRIGHT": "right", "RIGHT": "right",
}


def norm_step_key(k: str) -> str:
    u = k.strip().upper()
    return _STEP_KEY.get(u, u.lower())


def norm_pynput_key(key) -> str:
    """pynput Key / KeyCode -> the same names as norm_step_key."""
    name = getattr(key, "name", None)
    if name:                                   # keyboard.Key member
        for m in ("ctrl", "shift", "alt", "cmd"):
            if name == m or name.startswith(m + "_"):
                return "win" if m == "cmd" else m
        return {"alt_gr": "alt"}.get(name, name)
    vk = getattr(key, "vk", None)
    if vk is not None and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
        return chr(vk).lower()                 # robust while Ctrl is held (char is a control code)
    ch = getattr(key, "char", None)
    return (ch or "").lower()


class Coach:
    def __init__(self, ov) -> None:
        self.ov = ov
        self.active = False
        self.events: queue.Queue = queue.Queue()
        self.steps: list[dict] = []
        self.idx = 0
        self.t_start = -10.0
        self.step_t0 = 0.0
        self.success_t0: float | None = None
        self.misses = 0
        self.shake_t0 = -10.0
        self.hover_since: float | None = None
        self.held: set[str] = set()
        self.bubble: Bubble | None = None
        self.old: list[tuple[QPointF, float, float]] = []      # (pt, success_t0, fade_t0) rings fading out
        self.demo_hide_at: float | None = None
        self.fade_t0: float | None = None                       # stop(): fade everything out
        self.idle_at: float | None = None
        self.toast = None
        self.last_mouse = QPointF(ov.W / 2, ov.H / 2)
        self._ml = self._kl = None
        self.f_banner = T.font(24, T.SEMI)
        self.f_badge = T.font(15, T.BOLD)
        self.f_key = T.font(16, T.SEMI)
        self.f_plus = T.font(20, T.BOLD)

    # ------------------------------------------------------------ control
    def start(self, run_dir: str | None = None) -> bool:
        now = self.ov.now()
        steps = self._load(run_dir)
        if not steps:
            self.ov.hud.set_state("error", "Nothing yet", now)
            self.ov.hud.flash("No run to practise yet", now, 3.0)
            return False
        self.stop(silent=True)
        self.steps = steps
        self.idx = 0
        self.active = True
        self.fade_t0 = None
        self.idle_at = None
        self.t_start = now
        self.held.clear()
        self.old.clear()
        while not self.events.empty():
            self.events.get_nowait()
        self.ov.cursor.hide()
        self.ov.captions.clear(now)
        self.ov.hud.set_state("coach", "Your turn", now)
        self._enter_step(now + 0.35)
        self._start_listeners()
        return True

    def stop(self, silent: bool = False) -> None:
        self._stop_listeners()
        if self.active and not silent:
            now = self.ov.now()
            self.fade_t0 = now
            if self.bubble:
                self.bubble.dead_t0 = now
            self.ov.hud.set_state("idle", "", now)
        self.active = False
        self.demo_hide_at = None

    # ------------------------------------------------------------ data
    def _load(self, run_dir: str | None) -> list[dict]:
        try:
            if not run_dir:
                with open(LAST_RUN_FILE, encoding="utf-8") as f:
                    run_dir = f.read().strip()
            path = run_dir if run_dir.endswith(".json") else os.path.join(run_dir, "steps.json")
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except Exception as e:
            log.warning("coach: no run to load (%s)", e)
            return []
        out = []
        for st in doc.get("steps", []):
            if st.get("kind") in SKIP:
                continue
            out.append(st)
        return out

    def _pt(self, st: dict, end: bool = False) -> QPointF:
        x, y = (st.get("end_x", 0), st.get("end_y", 0)) if end else (st.get("x", 0), st.get("y", 0))
        if not x and not y:
            return QPointF(self.ov.W / 2, self.ov.H * 0.55)
        return QPointF(x - self.ov.x(), y - self.ov.y())

    @property
    def step(self) -> dict | None:
        return self.steps[self.idx] if self.active and self.idx < len(self.steps) else None

    def _enter_step(self, t0: float) -> None:
        st = self.steps[self.idx]
        self.step_t0 = t0
        self.success_t0 = None
        self.misses = 0
        self.hover_since = None
        n = self.idx + 1
        text = st.get("caption", "") or st.get("kind", "").replace("_", " ").title()
        if st.get("kind") == "type" and st.get("text"):
            text = f"Type: {st['text']}"
        self.bubble = Bubble(text, n, t0, None, "num")
        hud = self.ov.hud
        hud.step, hud.total = n, len(self.steps)

    # ------------------------------------------------------------ input (pynput threads)
    def _start_listeners(self) -> None:
        try:
            from pynput import keyboard, mouse
            self._ml = mouse.Listener(on_move=self._on_move, on_click=self._on_click, on_scroll=self._on_scroll)
            self._kl = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
            self._ml.daemon = self._kl.daemon = True
            self._ml.start()
            self._kl.start()
        except Exception:
            log.exception("coach: pynput listeners failed")

    def _stop_listeners(self) -> None:
        for l in (self._ml, self._kl):
            try:
                if l is not None:
                    l.stop()
            except Exception:
                pass
        self._ml = self._kl = None

    def _on_move(self, x, y, injected=False):
        if not injected:
            self.events.put(("move", x, y, None, None))

    def _on_click(self, x, y, button, pressed, injected=False):
        if not injected:
            self.events.put(("click", x, y, getattr(button, "name", str(button)), pressed))

    def _on_scroll(self, x, y, dx, dy, injected=False):
        if not injected:
            self.events.put(("scroll", x, y, None, dy))

    def _on_press(self, key, injected=False):
        if not injected:
            self.events.put(("key", 0, 0, norm_pynput_key(key), True))

    def _on_release(self, key, injected=False):
        if not injected:
            self.events.put(("key", 0, 0, norm_pynput_key(key), False))

    def inject(self, kind: str, x: float = 0, y: float = 0, button=None, pressed=None) -> None:
        """Tests: push a synthetic (kind, x, y, button|key, pressed|dy) event."""
        self.events.put((kind, x, y, button, pressed))

    # ------------------------------------------------------------ logic (Qt thread)
    def _handle(self, ev, now: float) -> None:
        kind, x, y, b, p = ev
        if kind == "key":
            if p:
                self.held.add(b)
            else:
                self.held.discard(b)
        else:
            self.last_mouse = QPointF(x - self.ov.x(), y - self.ov.y())
        st = self.step
        if st is None or self.success_t0 is not None or now < self.step_t0:
            return
        k = st.get("kind", "click")
        target = self._pt(st)
        near = math.hypot(self.last_mouse.x() - target.x(), self.last_mouse.y() - target.y()) <= s(HIT_PX)
        if k in ("click", "double_click", "right_click"):
            if kind == "click" and p:
                want_right = k == "right_click" or st.get("button") == "right"
                if near and (b == "right") == want_right:
                    self._success(now)
                elif not near:
                    self._miss(now)
        elif k == "move":
            if kind == "move":
                if near:
                    self.hover_since = self.hover_since or now
                else:
                    self.hover_since = None
            elif kind == "click" and p:
                if near:
                    self._success(now)
        elif k == "drag":
            end = self._pt(st, end=True)
            if kind == "click" and not p:
                if math.hypot(self.last_mouse.x() - end.x(), self.last_mouse.y() - end.y()) <= s(HIT_PX):
                    self._success(now)
                else:
                    self._miss(now)
        elif k == "scroll":
            if kind == "scroll" and math.hypot(self.last_mouse.x() - target.x(),
                                               self.last_mouse.y() - target.y()) <= s(120) * 4:
                self._success(now)
        elif k == "type":
            if (kind == "key" and p and b == "enter") or (kind == "click" and p):
                self._success(now)
        elif k == "keypress":
            if kind == "key" and p:
                want = [norm_step_key(x) for x in st.get("keys", [])]
                mods = {w for w in want if w in MODS}
                main = [w for w in want if w not in MODS]
                if main:
                    if b == main[-1] and mods <= self.held:
                        self._success(now)
                elif b in mods and mods <= self.held:
                    self._success(now)

    def _success(self, now: float) -> None:
        self.success_t0 = now
        pt = self._pt(self.step)
        self.ov.cursor.ripple_at(pt, now, T.MINT)
        self.ov.particles.burst(pt, now, [T.MINT, T.GHOST], n=18, speed=160, life=0.7)

    def _miss(self, now: float) -> None:
        self.misses += 1
        self.shake_t0 = now
        if self.misses >= 2:
            self.misses = 0
            self._demo(now)

    def _demo(self, now: float) -> None:
        c = self.ov.cursor
        target = self._pt(self.step)
        c.show(self.last_mouse, now)
        c.vis_target = 1.0
        c.glide_to(target.x(), target.y(), 0.9, now, on_arrive=self._demo_arrive)
        self.demo_hide_at = None

    def _demo_arrive(self) -> None:
        now = self.ov.now()
        self.ov.cursor.click(now)
        self.demo_hide_at = now + 0.8

    def _finish(self, now: float) -> None:
        last = self._pt(self.steps[-1])
        self._stop_listeners()
        self.active = False
        if self.bubble:
            self.bubble.dead_t0 = now
        hud = self.ov.hud
        hud.set_state("done", "Done", now)
        hud.flash("You did it yourself", now, 4.0, then="")
        self.ov.particles.burst(last, now, [T.MINT, T.MINT, T.CYAN, T.GHOST], n=60, speed=320, up=60)
        from .fx import Toast
        self.toast = Toast("Done. You did it yourself.", QPointF(self.ov.W / 2, self.ov.H * 0.42), now, T.MINT, 2.8)
        self.toast.font = T.font(26, T.SEMI)
        self.toast.w = QFontMetricsF(self.toast.font).horizontalAdvance(self.toast.text) + s(64)
        self.toast.h = s(62)
        self.idle_at = now + 4.0

    def update(self, now: float, dt: float) -> None:
        for _ in range(256):
            try:
                ev = self.events.get_nowait()
            except queue.Empty:
                break
            if self.active:
                self._handle(ev, now)
        st = self.step
        if st is not None:
            if (st.get("kind") == "move" and self.hover_since is not None and self.success_t0 is None
                    and now - self.hover_since >= 0.25):
                self._success(now)
            if self.success_t0 is not None and now - self.success_t0 >= SUCCESS_HOLD:
                self.old.append((self._pt(st), self.success_t0, now))
                if self.bubble:
                    self.bubble.dead_t0 = now
                self.idx += 1
                if self.idx >= len(self.steps):
                    self._finish(now)
                else:
                    self._enter_step(now + 0.12)
        if self.demo_hide_at is not None and now >= self.demo_hide_at:
            self.demo_hide_at = None
            self.ov.cursor.hide()
        self.old = [o for o in self.old if now - o[2] < 0.35]
        if self.toast and not self.toast.alive(now):
            self.toast = None
        if self.idle_at is not None and now >= self.idle_at:
            self.idle_at = None
            self.ov.hud.set_state("idle", "", now)

    # ------------------------------------------------------------ drawing
    def _alpha(self, now: float) -> float:
        if self.fade_t0 is not None:
            return 1 - T.clamp((now - self.fade_t0) / 0.3)
        return 1.0

    def _visible(self, now: float) -> bool:
        return self.active or (self.fade_t0 is not None and now - self.fade_t0 < 0.3)

    def _banner_rect(self) -> QRectF:
        fm = QFontMetricsF(self.f_banner)
        w = fm.horizontalAdvance("Your turn: follow the rings") + s(80)
        h = s(60)
        return QRectF((self.ov.W - w) / 2, self.ov.H * 0.2 - h / 2, w, h)

    def _bubble_pos(self, pt: QPointF, b: Bubble) -> QPointF:
        w, h = b.size.width(), b.size.height()
        off_x, off_y, m = s(46), s(30), s(14)
        x = pt.x() + off_x if pt.x() + off_x + w <= self.ov.W - m else pt.x() - off_x - w
        y = pt.y() + off_y if pt.y() + off_y + h <= self.ov.H - m else pt.y() - off_y - h
        return QPointF(T.clamp(x, m, self.ov.W - m - w), T.clamp(y, s(76), self.ov.H - m - h))

    def rects(self, now: float) -> list[QRectF]:
        out: list[QRectF] = []
        if self.toast:
            out += self.toast.rects(now)
        if not self._visible(now) and not self.old:
            return out
        if now - self.t_start < BANNER_S + 0.1:
            out.append(self._banner_rect().adjusted(-s(24), -s(24), s(24), s(30)))
        st = self.step if self.active else (self.steps[self.idx] if self.idx < len(self.steps) else None)
        if st is not None:
            pt = self._pt(st)
            m = s(110)
            out.append(QRectF(pt.x() - m, pt.y() - m - s(40), 2 * m, 2 * m + s(40)))
            if st.get("kind") == "drag":
                e = self._pt(st, end=True)
                out.append(QRectF(pt, e).normalized().adjusted(-s(40), -s(40), s(40), s(40)))
            if st.get("kind") == "keypress":
                out.append(QRectF(pt.x() - s(220), pt.y() - s(130), s(440), s(80)))
        if self.bubble:
            out.append(self.bubble.rect().adjusted(-s(20), -s(20), s(20), s(26)))
        for pt, _, _ in self.old:
            m = s(80)
            out.append(QRectF(pt.x() - m, pt.y() - m, 2 * m, 2 * m))
        return out

    def paint(self, p: QPainter, now: float) -> None:
        for pt, t_s, t_f in self.old:
            k = T.clamp((now - t_f) / 0.35)
            self._paint_ring(p, now, pt, success=t_s, alpha=1 - k, scale=1 + 0.25 * T.ease_out_cubic(k),
                             step_no=None)
        if self.toast:
            self.toast.paint(p, now)
        if not self._visible(now):
            return
        a = self._alpha(now)
        st = self.step if self.active else (self.steps[self.idx] if self.idx < len(self.steps) else None)
        if st is not None and now >= self.step_t0:
            pt = self._pt(st)
            k_in = T.clamp((now - self.step_t0) / 0.42)
            sc = T.ease_out_back(k_in, 2.0)
            shake = 0.0
            ks = (now - self.shake_t0) / 0.38
            if 0 <= ks < 1:
                shake = math.sin(ks * math.tau * 3) * s(7) * (1 - ks)
            ptd = QPointF(pt.x() + shake, pt.y())
            if st.get("kind") == "drag":
                self._paint_drag_hint(p, now, pt, self._pt(st, end=True), a * k_in)
            self._paint_ring(p, now, ptd, success=self.success_t0, alpha=a * T.clamp(k_in * 2.5), scale=sc,
                             step_no=self.idx + 1)
            if st.get("kind") == "keypress":
                self._paint_keys(p, now, pt, st.get("keys", []), a * k_in)
            if self.bubble is not None and self.bubble.dead_t0 is None:
                self.bubble.pos = self._bubble_pos(pt, self.bubble)
        if self.bubble is not None:
            p.save()
            p.setOpacity(a)
            self.bubble.paint(p, now)
            p.restore()
        self._paint_banner(p, now, a)

    def _paint_banner(self, p: QPainter, now: float, a: float) -> None:
        t = now - self.t_start
        if t >= BANNER_S:
            return
        k = T.ease_out_cubic(T.clamp(t / 0.35)) * (1 - T.clamp((t - BANNER_S + 0.45) / 0.45))
        if k <= 0:
            return
        r = self._banner_rect().translated(0, s(10) * (1 - T.ease_out_cubic(T.clamp(t / 0.35))))
        T.glass(p, r, r.height() / 2, a * k, edge=T.CYAN)
        p.save()
        p.setOpacity(a * k)
        dc = QPointF(r.left() + s(30), r.center().y())
        p.setPen(Qt.PenStyle.NoPen)
        ph = (now % 1.2) / 1.2
        p.setBrush(T.alpha(T.CYAN, 0.45 * (1 - ph)))
        rr = s(5) + s(9) * T.ease_out_cubic(ph)
        p.drawEllipse(dc, rr, rr)
        p.setBrush(T.CYAN)
        p.drawEllipse(dc, s(5.5), s(5.5))
        p.setFont(self.f_banner)
        p.setPen(T.GHOST)
        p.drawText(r.adjusted(s(48), 0, -s(28), 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   "Your turn: follow the rings")
        p.restore()

    def _paint_ring(self, p: QPainter, now: float, c: QPointF, success: float | None, alpha: float,
                    scale: float, step_no: int | None) -> None:
        if alpha <= 0.003:
            return
        ks = T.clamp((now - success) / 0.25) if success is not None else 0.0
        col = T.mix(T.CYAN, T.MINT, ks)
        r0 = s(30)
        p.save()
        p.translate(c)
        p.scale(scale, scale)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if success is None:
            for i in range(2):                                    # expanding echoes
                k = ((now - self.step_t0) / 1.5 + i * 0.5) % 1.0
                rr = r0 + s(30) * T.ease_out_cubic(k)
                p.setPen(QPen(T.alpha(T.mix(T.CYAN, T.VIOLET, k), alpha * (1 - k) * 0.85), s(2.5)))
                p.drawEllipse(QPointF(0, 0), rr, rr)
        breathe = s(2.5) * T.pulse(now, 1.5) if success is None else s(4) * math.sin(math.pi * ks)
        p.setPen(QPen(T.alpha(col, alpha), s(3.2)))
        p.setBrush(T.alpha(col, (0.12 + 0.14 * ks) * alpha))
        p.drawEllipse(QPointF(0, 0), r0 + breathe, r0 + breathe)
        p.setPen(Qt.PenStyle.NoPen)                               # target dot
        p.setBrush(T.alpha(T.WHITE if success is None else T.MINT, 0.85 * alpha * (1 - ks)))
        p.drawEllipse(QPointF(0, 0), s(3.5), s(3.5))
        if success is not None:                                   # check mark draws in
            kc = T.ease_out_cubic(T.clamp((now - success) / 0.3))
            pts = [QPointF(-s(11), s(1)), QPointF(-s(3), s(9)), QPointF(s(12), -s(8))]
            pen = QPen(T.alpha(T.MINT, alpha), s(4))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            l1 = math.hypot(pts[1].x() - pts[0].x(), pts[1].y() - pts[0].y())
            l2 = math.hypot(pts[2].x() - pts[1].x(), pts[2].y() - pts[1].y())
            d = kc * (l1 + l2)
            if d <= l1:
                p.drawLine(pts[0], pts[0] + (pts[1] - pts[0]) * (d / l1))
            else:
                p.drawPolyline([pts[0], pts[1], pts[1] + (pts[2] - pts[1]) * ((d - l1) / l2)])
        if step_no is not None:                                   # number badge, upper right
            d = s(26)
            bc = QPointF(r0 * 0.78, -r0 * 0.78)
            br = QRectF(bc.x() - d / 2, bc.y() - d / 2, d, d)
            p.setPen(Qt.PenStyle.NoPen)
            if success is None:
                g = QLinearGradient(br.topLeft(), br.bottomRight())
                g.setColorAt(0, T.alpha(T.VIOLET, alpha))
                g.setColorAt(1, T.alpha(T.CYAN, alpha))
                p.setBrush(g)
            else:
                p.setBrush(T.alpha(T.MINT, alpha))
            p.drawEllipse(br)
            p.setFont(self.f_badge)
            p.setPen(T.alpha(QColor(10, 12, 20), alpha))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, str(step_no))
        p.restore()

    def _paint_drag_hint(self, p: QPainter, now: float, a: QPointF, b: QPointF, alpha: float) -> None:
        p.save()
        pen = QPen(T.alpha(T.CYAN, 0.85 * alpha), s(2.5))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setDashPattern([0.1, 2.6])
        pen.setDashOffset(-(now - self.step_t0) * 9)
        p.setPen(pen)
        p.drawLine(a, b)
        p.setPen(QPen(T.alpha(T.VIOLET, alpha), s(2)))
        p.setBrush(T.alpha(T.VIOLET, 0.18 * alpha))
        p.drawEllipse(b, s(12), s(12))
        p.restore()

    def _paint_keys(self, p: QPainter, now: float, c: QPointF, keys: list[str], alpha: float) -> None:
        labels = [key_label(k) for k in keys] or ["?"]
        held = {norm_step_key(k) for k in keys} & self.held
        fm = QFontMetricsF(self.f_key)
        h = s(40)
        widths = [max(h, fm.horizontalAdvance(t) + s(26)) for t in labels]
        plus_w = s(24)
        total = sum(widths) + plus_w * (len(labels) - 1)
        x = c.x() - total / 2
        y = c.y() - s(112)
        for i, (t, w) in enumerate(zip(labels, widths)):
            k = T.clamp((now - self.step_t0 - i * 0.07) / 0.38)
            if k > 0:
                down = norm_step_key(keys[i]) in held if i < len(keys) else False
                r = QRectF(x, y + (s(3) if down else 0), w, h)
                sc = T.ease_out_back(k, 2.4)
                p.save()
                p.translate(r.center())
                p.scale(sc, sc)
                p.translate(-r.center())
                T.glass(p, r, s(8), alpha * T.clamp(k * 3), edge=T.CYAN if down else None)
                p.setOpacity(alpha * T.clamp(k * 3))
                p.setPen(QPen(QColor(255, 255, 255, 40), s(1)))
                p.drawLine(QPointF(r.left() + s(7), r.bottom() - s(4)), QPointF(r.right() - s(7), r.bottom() - s(4)))
                p.setFont(self.f_key)
                p.setPen(T.GHOST)
                p.drawText(r.adjusted(0, -s(2), 0, -s(2)), Qt.AlignmentFlag.AlignCenter, t)
                p.restore()
            x += w
            if i < len(labels) - 1:
                p.save()
                p.setOpacity(alpha * T.clamp(k * 3))
                p.setFont(self.f_plus)
                p.setPen(T.VIOLET)
                p.drawText(QRectF(x, y, plus_w, h), Qt.AlignmentFlag.AlignCenter, "+")
                p.restore()
                x += plus_w
