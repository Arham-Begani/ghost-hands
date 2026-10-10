"""`python main.py --selftest DIR`: proves, with real screenshots, that the
overlay is excluded from capture and is click-through. Writes:
  room_view.png   what the room sees (capture exclusion briefly OFF)
  model_view.png  what mss / the model sees (exclusion ON): no ghost
  bare.png        the desktop with the overlay hidden (reference)
  report.json
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
from ctypes import wintypes

import mss
import mss.tools

_MSS = getattr(mss, "MSS", None) or mss.mss
from PyQt6.QtCore import QRectF, QTimer
from PyQt6.QtWidgets import QApplication

from .app import WS_EX_LAYERED, WS_EX_NOACTIVATE, WS_EX_TRANSPARENT, clock
from .theme import s

log = logging.getLogger("ghost.selftest")
u32 = ctypes.windll.user32
u32.WindowFromPoint.restype = wintypes.HWND
u32.WindowFromPoint.argtypes = [wintypes.POINT]
u32.GetAncestor.restype = wintypes.HWND
u32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
u32.GetForegroundWindow.restype = wintypes.HWND


def _grab(sct, mon):
    img = sct.grab(mon)
    return img, bytes(img.bgra)


def _diff(a: bytes, b: bytes, W: int, rect: QRectF, thresh: int = 24) -> tuple[int, int]:
    """(# pixels whose max channel delta > thresh, # pixels) inside rect."""
    x0, y0 = max(0, int(rect.left())), max(0, int(rect.top()))
    x1, y1 = int(rect.right()), int(rect.bottom())
    n = changed = 0
    for y in range(y0, y1, 2):
        row = (y * W) * 4
        for x in range(x0, x1, 2):
            i = row + x * 4
            if max(abs(a[i] - b[i]), abs(a[i + 1] - b[i + 1]), abs(a[i + 2] - b[i + 2])) > thresh:
                changed += 1
            n += 1
    return changed, n


def run_selftest(ov, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    fg_start = u32.GetForegroundWindow()
    state: dict = {"fg_start": fg_start, "fg_changed_to_overlay": False}

    def watch_fg() -> None:
        if u32.GetForegroundWindow() == ov.hwnd:
            state["fg_changed_to_overlay"] = True

    fgw = QTimer(ov)
    fgw.timeout.connect(watch_fg)
    fgw.start(50)

    def ready() -> bool:
        c = ov.cursor
        return (ov.hud.step >= 4 and c.glide is None and c.vis > 0.95 and ov.captions.current is not None
                and clock() - c.arrive_t > 0.5)

    def poll() -> None:
        if ready():
            start()
        else:
            QTimer.singleShot(50, poll)

    def start() -> None:
        ov.frozen_at = clock()
        c = ov.cursor
        tip = c.pos
        state["ghost_at"] = [round(tip.x()), round(tip.y())]
        state["ghost_rect"] = QRectF(tip.x() - s(30), tip.y() - s(20), s(70), s(70))
        cap = ov.captions.current.rect()
        state["caption_rect"] = cap
        state["hud_rect"] = ov.hud.rect
        ov.set_capture_excluded(False)
        ov.repaint()
        QTimer.singleShot(350, shot_room)

    def shot_room() -> None:
        with _MSS() as sct:
            mon = sct.monitors[1]
            img, state["A"] = _grab(sct, mon)
            mss.tools.to_png(img.rgb, img.size, output=os.path.join(out_dir, "room_view.png"))
            state["W"] = img.width
        state["excl_ok"] = ov.set_capture_excluded(True)
        QTimer.singleShot(350, shot_model)

    def shot_model() -> None:
        with _MSS() as sct:
            img, state["B"] = _grab(sct, sct.monitors[1])
            mss.tools.to_png(img.rgb, img.size, output=os.path.join(out_dir, "model_view.png"))
        # click-through: what window is hit-tested under the ghost?
        gx, gy = state["ghost_at"]
        hit = u32.WindowFromPoint(wintypes.POINT(gx + 4, gy + 6))
        root = u32.GetAncestor(hit, 2) if hit else None
        state["hit_is_overlay"] = bool(hit) and (hit == ov.hwnd or root == ov.hwnd)
        ov.hide()
        QTimer.singleShot(350, shot_bare)

    def shot_bare() -> None:
        with _MSS() as sct:
            img, C = _grab(sct, sct.monitors[1])
            mss.tools.to_png(img.rgb, img.size, output=os.path.join(out_dir, "bare.png"))
        ov.show()
        ov.frozen_at = None
        A, B, W = state["A"], state["B"], state["W"]
        ex = u32.GetWindowLongW(wintypes.HWND(ov.hwnd), -20) & 0xFFFFFFFF
        rep = {"ghost_at": state["ghost_at"], "capture_excluded_flag": ov.capture_excluded and state["excl_ok"],
               "exstyle": hex(ex),
               "exstyle_ok": all(ex & f for f in (WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_NOACTIVATE)),
               "hit_test_passes_through": not state["hit_is_overlay"],
               "overlay_ever_foreground": state["fg_changed_to_overlay"]}
        for name in ("ghost_rect", "caption_rect", "hud_rect"):
            r = state[name]
            ra, n = _diff(A, C, W, r)        # room view vs bare desktop: ghost present
            rb, _ = _diff(B, C, W, r)        # model view vs bare desktop: should be ~0
            rep[name] = {"room_vs_bare_changed_px": ra, "model_vs_bare_changed_px": rb, "sampled_px": n}
        g = rep["ghost_rect"]
        rep["PASS_capture_exclusion"] = (g["room_vs_bare_changed_px"] > 0.15 * g["sampled_px"]
                                         and g["model_vs_bare_changed_px"] <= 0.01 * g["sampled_px"])
        rep["PASS_click_through"] = rep["hit_test_passes_through"] and rep["exstyle_ok"] \
            and not rep["overlay_ever_foreground"]
        with open(os.path.join(out_dir, "report.json"), "w") as f:
            json.dump(rep, f, indent=2)
        print("SELFTEST", json.dumps(rep, indent=2), flush=True)
        stay = float(os.getenv("GH_SELFTEST_STAY", "0"))
        QTimer.singleShot(int(stay * 1000) + 200, QApplication.quit)

    QTimer.singleShot(500, poll)
