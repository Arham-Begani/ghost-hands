"""Real input. Physical pixel coordinates on the primary monitor.

do(kind, x, y, *, text="", keys=(), end_x=0, end_y=0, scroll_y=0, button="left")

Checks guard.ABORT before every primitive (raises guard.Aborted) and calls
guard.expect() before every pointer move so the takeover detector ignores us.
"""
from __future__ import annotations

import ctypes
import logging
import time

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)     # no-op if main.py already did it
except Exception:
    pass

import pyautogui

from ghost.brain import guard

log = logging.getLogger(__name__)

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

WHEEL_DELTA = 120          # pyautogui on Windows passes `clicks` raw to mouse_event
TYPE_INTERVAL = 0.03
GLIDE_S = 0.15             # the overlay ghost already glided; the real cursor just follows
DRAG_S = 0.5

_KEYS = {
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl", "lctrl": "ctrlleft", "rctrl": "ctrlright",
    "alt": "alt", "option": "alt", "menu": "alt", "altgr": "altright",
    "shift": "shift", "lshift": "shiftleft", "rshift": "shiftright",
    "cmd": "win", "command": "win", "meta": "win", "super": "win", "win": "win",
    "windows": "win", "os": "win", "lwin": "winleft", "rwin": "winright",
    "enter": "enter", "return": "enter", "ret": "enter",
    "esc": "esc", "escape": "esc",
    "tab": "tab", "space": "space", "spacebar": "space", " ": "space",
    "backspace": "backspace", "bksp": "backspace", "back": "backspace",
    "delete": "delete", "del": "delete", "insert": "insert", "ins": "insert",
    "up": "up", "arrowup": "up", "down": "down", "arrowdown": "down",
    "left": "left", "arrowleft": "left", "right": "right", "arrowright": "right",
    "home": "home", "end": "end",
    "pageup": "pageup", "pgup": "pageup", "pagedown": "pagedown", "pgdn": "pagedown",
    "capslock": "capslock", "caps": "capslock", "numlock": "numlock",
    "printscreen": "printscreen", "prtsc": "printscreen", "apps": "apps", "contextmenu": "apps",
    "plus": "+", "minus": "-", "equals": "=", "comma": ",", "period": ".", "slash": "/",
}


def _key(k: str) -> str:
    s = str(k).strip()
    low = s.lower().replace("_", "").replace("-", "") if len(s) > 1 else s.lower()
    if low in _KEYS:
        return _KEYS[low]
    if low.startswith("key") and len(low) == 4:          # "KeyA"
        return low[3]
    if low.startswith("digit") and len(low) == 6:        # "Digit1"
        return low[5]
    if low in pyautogui.KEYBOARD_KEYS:
        return low
    log.warning("unknown key %r, passing through", k)
    return low


def _norm_keys(keys) -> list[str]:
    if isinstance(keys, str):
        keys = [keys]
    out: list[str] = []
    for k in keys or ():
        k = str(k)
        if "+" in k and len(k) > 1:                      # "ctrl+c"
            out += [_key(p) for p in k.split("+") if p]
        else:
            out.append(_key(k))
    return out


def _check() -> None:
    if guard.ABORT.is_set():
        raise guard.Aborted()


def _sleep(s: float) -> None:
    end = time.monotonic() + s
    while True:
        _check()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.03, left))


def _glide(x: int, y: int, seconds: float = GLIDE_S) -> None:
    """Move the real cursor to (x, y) in small abortable steps."""
    x, y = int(x), int(y)
    sx, sy = pyautogui.position()
    n = max(1, int(seconds / 0.015))
    for i in range(1, n + 1):
        _check()
        t = i / n
        t = t * t * (3 - 2 * t)
        px, py = round(sx + (x - sx) * t), round(sy + (y - sy) * t)
        guard.expect(px, py)
        pyautogui.moveTo(px, py, _pause=False)
        if i < n:
            time.sleep(seconds / n)
    guard.expect(x, y)
    pyautogui.moveTo(x, y, _pause=False)


def _type(text: str) -> None:
    if text.isascii():
        for ch in text:
            _check()
            if ch == "\r":
                continue
            pyautogui.write(ch, _pause=False)
            time.sleep(TYPE_INTERVAL)
        return
    _check()
    import pyperclip
    pyperclip.copy(text)
    time.sleep(0.05)
    _check()
    pyautogui.hotkey("ctrl", "v")


def do(kind: str, x: int, y: int, *, text: str = "", keys=(), end_x: int = 0, end_y: int = 0,
       scroll_y: int = 0, button: str = "left") -> None:
    try:
        _do(kind, int(x or 0), int(y or 0), text=text or "", keys=keys or (),
            end_x=int(end_x or 0), end_y=int(end_y or 0), scroll_y=int(scroll_y or 0),
            button=(button or "left").lower())
    except pyautogui.FailSafeException:
        guard.ABORT.set()
        raise guard.Aborted("failsafe corner")


def _do(kind, x, y, *, text, keys, end_x, end_y, scroll_y, button):
    _check()
    kind = (kind or "").lower()
    if kind == "click" and button == "right":
        kind = "right_click"
    if kind in ("click", "double_click", "right_click"):
        _glide(x, y)
        _check()
        if kind == "double_click":
            pyautogui.doubleClick(button=button if button in ("left", "middle", "right") else "left")
        elif kind == "right_click":
            pyautogui.click(button="right")
        else:
            pyautogui.click(button=button if button in ("left", "middle") else "left")
    elif kind == "move":
        _glide(x, y)
    elif kind == "drag":
        _glide(x, y)
        _check()
        pyautogui.mouseDown(button="left")
        try:
            _glide(end_x, end_y, DRAG_S)
        finally:
            pyautogui.mouseUp(button="left")
    elif kind == "scroll":
        if x or y:
            _glide(x, y)
        _check()
        pyautogui.scroll(-scroll_y * WHEEL_DELTA)
    elif kind == "type":
        if x or y:
            _glide(x, y)
        _type(text)
    elif kind == "keypress":
        if x or y:
            _glide(x, y)
        ks = _norm_keys(keys)
        _check()
        if len(ks) == 1:
            pyautogui.press(ks[0])
        elif ks:
            pyautogui.hotkey(*ks)
    elif kind == "wait":
        _sleep(1.0)
    else:
        log.warning("executor: unknown action kind %r ignored", kind)
