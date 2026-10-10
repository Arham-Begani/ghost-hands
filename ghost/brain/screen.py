"""Screen capture for the brain: primary monitor, physical pixels.

capture()      -> (png_bytes, scale)   physical = model * scale
screen_size()  -> (w, h)               physical pixels
crop_png(x, y) -> png bytes            360x240 centred on (x, y), clamped
"""
from __future__ import annotations

import ctypes
import io
import os

import mss
from PIL import Image


def _dpi_aware() -> None:
    # main.py / cli.py do this first; this is only a safety net for direct use.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass


_dpi_aware()

_MSS = getattr(mss, "MSS", None) or mss.mss
_size: tuple[int, int] | None = None


def _monitor(sct) -> dict:
    return sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]


# Recording mode (main.py sets these): the overlay is left visible to screen
# recorders and hides itself from capture only while the brain takes a shot.
before_grab = None
after_grab = None


def _grab(region: dict | None = None) -> Image.Image:
    if before_grab:
        before_grab()
    try:
        with _MSS() as sct:                      # mss is not thread-safe: one per call
            shot = sct.grab(region or _monitor(sct))
            return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    finally:
        if after_grab:
            after_grab()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=3)
    return buf.getvalue()


def screen_size() -> tuple[int, int]:
    global _size
    if _size is None:
        with _MSS() as sct:
            m = _monitor(sct)
            _size = (int(m["width"]), int(m["height"]))
    return _size


def capture() -> tuple[bytes, float]:
    """Full primary monitor, downscaled to <= GH_SCREEN_MAX_W wide."""
    img = _grab()
    phys_w, phys_h = img.size
    max_w = int(os.getenv("GH_SCREEN_MAX_W", "1440") or 1440)
    if phys_w > max_w:
        sent_w = max_w
        sent_h = max(1, round(phys_h * max_w / phys_w))
        img = img.resize((sent_w, sent_h), Image.LANCZOS)
    else:
        sent_w = phys_w
    return _png(img), phys_w / sent_w


def crop_png(x: int, y: int, w: int = 360, h: int = 240) -> bytes:
    with _MSS() as sct:
        m = _monitor(sct)
    sw, sh = int(m["width"]), int(m["height"])
    w, h = min(w, sw), min(h, sh)
    left = max(0, min(int(x) - w // 2, sw - w))
    top = max(0, min(int(y) - h // 2, sh - h))
    img = _grab({"left": m["left"] + left, "top": m["top"] + top, "width": w, "height": h})
    return _png(img)


if __name__ == "__main__":
    png, scale = capture()
    root = os.path.join(os.path.dirname(__file__), "..", "..", "runs")
    os.makedirs(root, exist_ok=True)
    out = os.path.abspath(os.path.join(root, "_test_screen.png"))
    with open(out, "wb") as f:
        f.write(png)
    sent = Image.open(io.BytesIO(png)).size
    w, h = screen_size()
    print(f"saved {out}")
    print(f"physical {w}x{h}  sent {sent[0]}x{sent[1]}  scale {scale:.4f}")
    print(f"model (0,0) -> physical ({round(0 * scale)},{round(0 * scale)})")
    mx, my = sent[0] - 1, sent[1] - 1
    print(f"model ({mx},{my}) -> physical ({round(mx * scale)},{round(my * scale)})  [screen max {w - 1},{h - 1}]")
    with open(os.path.join(root, "_test_crop.png"), "wb") as f:
        f.write(crop_png(w - 5, h - 5))
    print("crop at bottom-right corner ok")
