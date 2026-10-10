"""Full-screen, click-through, capture-excluded overlay window + event pump.

Everything is drawn in physical pixels (main.py turns Qt high-DPI scaling off),
so brain coordinates map 1:1 onto the widget."""
from __future__ import annotations

import ctypes
import logging
import os
import queue
import time
from ctypes import wintypes

from PyQt6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PyQt6.QtGui import QCursor, QGuiApplication, QPainter, QRegion, QSurfaceFormat
from PyQt6.QtWidgets import QWidget

from ghost.contracts import Error, GhostAction, HandOver, RunFinished, Status, TaskHeard

from . import theme as T
from .theme import s

log = logging.getLogger("ghost.overlay")

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
WDA_EXCLUDEFROMCAPTURE = 0x11
HWND_TOPMOST = wintypes.HWND(-1)
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE | NOMOVE | NOACTIVATE | NOOWNERZORDER

_user32 = ctypes.windll.user32
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
_user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int, ctypes.c_uint]

clock = time.perf_counter

# The overlay composites through OpenGL (see Overlay.__init__). Don't let the GL
# flush block on vsync: DWM composes the window anyway, so there's no tearing,
# and a blocking swap halves the frame rate against the 16 ms timer.
_fmt = QSurfaceFormat.defaultFormat()
_fmt.setSwapInterval(0)
_fmt.setAlphaBufferSize(8)
QSurfaceFormat.setDefaultFormat(_fmt)


def system_ui_scale() -> float:
    try:
        return max(1.0, _user32.GetDpiForSystem() / 96.0)
    except Exception:
        return 1.0


class Overlay(QWidget):
    def __init__(self, to_overlay: queue.Queue, to_brain: queue.Queue) -> None:
        super().__init__(None)
        self.to_overlay = to_overlay
        self.to_brain = to_brain
        T.UI = system_ui_scale()

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        geo = QGuiApplication.primaryScreen().geometry()
        self.setGeometry(geo)
        self.W, self.H = geo.width(), geo.height()
        self.hwnd = 0
        self.capture_excluded = False
        # A (clipped-away) GL child switches the window to OpenGL composition, so Qt
        # draws per-pixel alpha through DWM instead of UpdateLayeredWindow. Affinity
        # (WDA_EXCLUDEFROMCAPTURE) fails with error 8 on UpdateLayeredWindow windows.
        from PyQt6.QtOpenGLWidgets import QOpenGLWidget
        self._gl = QOpenGLWidget(self)
        self._gl.setGeometry(-8, -8, 1, 1)

        from .caption import CaptionLayer
        from .cursor import GhostCursor
        from .fx import HandOverFx, Particles, Splash, Toast
        from .hud import Hud
        self._Splash, self._HandOverFx, self._Toast = Splash, HandOverFx, Toast
        self.cursor = GhostCursor()
        self.captions = CaptionLayer()
        self.hud = Hud(self.W)
        self.particles = Particles()
        self.splash = None
        self.handover = None
        self.toasts: list = []
        self.hide_at: float | None = None
        self.frozen_at: float | None = None      # selftest: freeze the clock

        self._t_prev = clock()
        self._last_rects: list[QRectF] = []
        self._fps_dbg = os.getenv("GH_FPS") == "1"
        self._fps_n, self._fps_t0, self._paint_ms = 0, clock(), 0.0

        self.frame = QTimer(self)
        self.frame.setTimerType(Qt.TimerType.PreciseTimer)
        self.frame.timeout.connect(self.tick)
        self.frame.start(16)
        self.pump = QTimer(self)
        self.pump.timeout.connect(self.drain)
        self.pump.start(16)
        self.topmost = QTimer(self)
        self.topmost.timeout.connect(self._raise_topmost)
        self.topmost.start(3000)

    # ------------------------------------------------------------ win32
    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not self.hwnd:
            self.hwnd = int(self.winId())
            h = wintypes.HWND(self.hwnd)
            ex = _user32.GetWindowLongW(h, GWL_EXSTYLE)
            want = ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW
            _user32.SetWindowLongW(h, GWL_EXSTYLE, want)
            self.capture_excluded = bool(_user32.SetWindowDisplayAffinity(h, WDA_EXCLUDEFROMCAPTURE))
            self._raise_topmost()
            log.info("overlay hwnd=%#x exstyle %#x -> %#x, capture-excluded=%s, ui=%.2f, %dx%d",
                     self.hwnd, ex & 0xFFFFFFFF, want & 0xFFFFFFFF, self.capture_excluded, T.UI, self.W, self.H)
            if not self.capture_excluded:
                log.warning("SetWindowDisplayAffinity failed (err %s): ghost WILL appear in screenshots",
                            ctypes.GetLastError())

    def set_capture_excluded(self, on: bool) -> bool:
        return bool(_user32.SetWindowDisplayAffinity(wintypes.HWND(self.hwnd), WDA_EXCLUDEFROMCAPTURE if on else 0))

    def _raise_topmost(self) -> None:
        if self.hwnd:
            _user32.SetWindowPos(wintypes.HWND(self.hwnd), HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)

    # ------------------------------------------------------------ events
    def now(self) -> float:
        return self.frozen_at if self.frozen_at is not None else clock()

    def drain(self) -> None:
        for _ in range(64):
            try:
                ev = self.to_overlay.get_nowait()
            except queue.Empty:
                return
            try:
                self.handle(ev)
            except Exception:
                log.exception("overlay failed on %r", ev)
                ack = getattr(ev, "ack", None)
                if ack is not None:
                    ack.set()

    def handle(self, ev: object) -> None:
        now = self.now()
        if isinstance(ev, GhostAction):
            self.on_action(ev, now)
        elif isinstance(ev, Status):
            self.on_status(ev, now)
        elif isinstance(ev, TaskHeard):
            self.on_task(ev, now)
        elif isinstance(ev, HandOver):
            self.on_handover(ev, now)
        elif isinstance(ev, RunFinished):
            self.on_finished(ev, now)
        elif isinstance(ev, Error):
            self.hud.set_state("error", "Error", now)
            self.hud.flash(ev.text, now, 6.0)
            self.cursor.thinking = False
        else:
            log.warning("overlay: unknown event %r", ev)

    def ensure_ghost(self, now: float) -> None:
        self.hide_at = None
        if not self.cursor.visible:
            p = QCursor.pos()
            self.cursor.show(QPointF(p.x() - self.x() + s(14), p.y() - self.y() + s(10)), now)
        else:
            self.cursor.vis_target = 1.0

    def reset_run(self, now: float) -> None:
        self.captions.clear(now)
        self.cursor.clear_cues(now)
        if self.handover:
            self.handover.end = now
        self.hide_at = None
        self.hud.note = ""

    def on_status(self, ev: Status, now: float) -> None:
        st = ev.state
        self.hud.set_state(st, ev.text, now, ev.step, ev.total)
        self.cursor.thinking = st == "thinking"
        if st == "listening":
            self.reset_run(now)
        elif st in ("thinking", "acting", "replay"):
            self.ensure_ghost(now)
        elif st == "aborted":
            self.dissolve(now)
        elif st == "idle":
            self.cursor.hide()
            self.captions.clear(now)

    def on_task(self, ev: TaskHeard, now: float) -> None:
        self.reset_run(now)
        text = ev.text.strip()
        screen = QRectF(0, 0, self.W, self.H)
        target = QPointF(self.W / 2, s(16) + s(23))
        self.splash = self._Splash(text, now, screen, target, lambda: self.hud.set_task(text, self.now()))

    def on_action(self, a: GhostAction, now: float) -> None:
        self.ensure_ghost(now)
        c = self.cursor
        c.thinking = False
        if self.hud.state in ("idle", "listening", "transcribing", "thinking", "done", "handover"):
            self.hud.set_state("acting", "", now)
        self.hud.set_step(a.step)
        self.captions.set(a.caption, a.step, now)
        c.clear_cues(now)
        positional = not (a.x == 0 and a.y == 0)
        if a.kind == "wait" or not positional:
            self.arrive(a)
        else:
            dur = max(0.15, (a.duration_ms or 700) / 1000.0)
            c.glide_to(a.x - self.x(), a.y - self.y(), dur, now, on_arrive=lambda: self.arrive(a))

    def arrive(self, a: GhostAction) -> None:
        now = self.now()
        c = self.cursor
        try:
            k = a.kind
            if k == "click":
                c.click(now)
            elif k == "double_click":
                c.click(now, double=True)
            elif k == "right_click":
                c.click(now)
                c.cue("menu", now)
            elif k == "type":
                c.cue("type", now, text=a.text, start=now + 0.12)
            elif k == "keypress":
                c.cue("keys", now, keys=list(a.keys))
            elif k == "scroll":
                c.cue("scroll", now, dy=a.scroll_y)
            elif k == "drag":
                c.squash_t0 = now
                c.cue("drag", now, a=QPointF(c.pos), b=QPointF(a.end_x - self.x(), a.end_y - self.y()))
        finally:
            QTimer.singleShot(120, lambda: self.after_ack(a))

    def after_ack(self, a: GhostAction) -> None:
        a.ack.set()
        if a.kind == "drag":
            end = QPointF(a.end_x - self.x(), a.end_y - self.y())
            self.cursor.glide_to(end.x(), end.y(), 0.6, self.now(), arc=False,
                                 on_arrive=lambda: self.cursor.ripple_at(end, self.now()))

    def on_handover(self, ev: HandOver, now: float) -> None:
        self.ensure_ghost(now)
        c = self.cursor
        c.thinking = False
        c.clear_cues(now)
        x, y = ev.x - self.x(), ev.y - self.y()

        def park() -> None:
            self.handover = self._HandOverFx(QPointF(x, y), self.now())
        c.glide_to(x, y, 0.7, now, on_arrive=park)
        self.captions.set(ev.reason, 0, now, accent=T.AMBER, badge="lock")
        self.hud.set_state("handover", "", now)

    def on_finished(self, ev: RunFinished, now: float) -> None:
        c = self.cursor
        c.thinking = False
        if ev.ok:
            if c.visible:
                self.particles.burst(c.pos, now, [T.MINT, T.MINT, T.CYAN, T.GHOST], n=46, speed=300, up=60)
            self.hud.set_state("done", "", now)
            self.hud.flash(ev.summary or "Done", now, 3.0, then="F9: your turn")
            self.captions.clear(now)
            self.hide_at = now + 3.2
        elif self.hud.state == "handover":
            self.hud.flash(ev.summary, now, 4.0)
            self.hide_at = now + 6.0
        elif self.hud.state != "aborted":
            self.hud.set_state("error", "Stopped", now)
            self.hud.flash(ev.summary, now, 5.0)
            self.hide_at = now + 4.0

    def dissolve(self, now: float) -> None:
        c = self.cursor
        if c.visible:
            pos = QPointF(c.pos) + QPointF(s(8), s(14))
            self.particles.burst(pos, now, [T.VIOLET, T.CYAN, T.GHOST], n=60, speed=140, life=1.1, up=90,
                                 spread=s(10))
            self.toasts.append(self._Toast("You've got it ✋", pos + QPointF(0, s(70)), now, T.MINT))
        c.kill()
        self.captions.clear(now)
        if self.handover:
            self.handover.end = now

    # ------------------------------------------------------------ frame
    def tick(self) -> None:
        if self.frozen_at is not None:
            return
        now = clock()
        dt = min(0.05, now - self._t_prev)
        self._t_prev = now
        if self.hide_at is not None and now >= self.hide_at:
            self.hide_at = None
            self.cursor.hide()
            self.captions.clear(now)
            if self.handover:
                self.handover.end = now
        self.cursor.update(now, dt)
        self.captions.update(now)
        self.hud.update(now, dt)
        self.particles.update(now, dt)
        if self.splash:
            self.splash.update(now)
        if self.handover and not self.handover.alive(now):
            self.handover = None
        self.toasts = [t for t in self.toasts if t.alive(now)]
        self.layout(now, dt)

        rects = self.cursor.rects(now) + self.captions.rects() + self.hud.rects() + self.particles.rects()
        if self.splash:
            rects += self.splash.rects()
            if self.splash.done:
                self.splash = None
        if self.handover:
            rects += self.handover.rects()
        for t in self.toasts:
            rects += t.rects(now)
        region = QRegion()
        for r in rects + self._last_rects:
            region = region.united(QRegion(r.toAlignedRect()))
        self._last_rects = rects
        if not region.isEmpty():
            self.update(region)

    def layout(self, now: float, dt: float) -> None:
        """Stack [typing chip, caption] at +28/+28 from the ghost, flipping to stay on screen."""
        cur = self.cursor.pos
        chip = self.cursor.chip_size(now)
        cap = self.captions.current
        sizes = ([chip] if chip else []) + ([cap.size] if cap else [])
        if not sizes:
            return
        gap, off, m = s(8), s(28), s(14)
        total_h = sum(r.height() for r in sizes) + gap * (len(sizes) - 1)
        max_w = max(r.width() for r in sizes)
        right = cur.x() + off + max_w <= self.W - m
        below = cur.y() + off + total_h <= self.H - m
        y = cur.y() + off if below else cur.y() - off - total_h
        y = T.clamp(y, self.hud.rect.bottom() + s(10), self.H - m - total_h)
        for r in sizes:
            x = cur.x() + off if right else cur.x() - off - r.width()
            x = T.clamp(x, m, self.W - m - r.width())
            if chip is not None and r is chip:
                self.cursor.chip_pos = QPointF(x, y)
            else:
                self.captions.place(QPointF(x, y), dt)
            y += r.height() + gap

    def paintEvent(self, e) -> None:
        t0 = clock()
        now = self.now()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        if self.handover:
            self.handover.paint(p, now)
        self.captions.paint(p, now)
        self.cursor.paint(p, now)
        self.particles.paint(p, now)
        for t in self.toasts:
            t.paint(p, now)
        if self.splash:
            self.splash.paint(p, now)
        self.hud.paint(p, now)
        p.end()
        if self._fps_dbg:
            self._fps_n += 1
            self._paint_ms += (clock() - t0) * 1000
            if clock() - self._fps_t0 >= 2.0:
                el = clock() - self._fps_t0
                print(f"[overlay] {self._fps_n / el:5.1f} fps, paint {self._paint_ms / max(1, self._fps_n):.2f} ms",
                      flush=True)
                self._fps_n, self._fps_t0, self._paint_ms = 0, clock(), 0.0
