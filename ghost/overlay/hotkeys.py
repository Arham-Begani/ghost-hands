"""Global hotkeys via a pynput low-level hook.

F8 hold/release = talk, F7 = typed task box, F9 = your turn, F10 = replay last
run, Esc = stop. F7-F10 are swallowed so the app underneath never sees them
(Chrome's F7 would pop a caret-browsing dialog mid-demo). Injected keys (our
own executor typing) are ignored, so the ghost can never trigger Esc-abort.

The hook thread only enqueues commands; the overlay drains them on the Qt
thread (Overlay.commands)."""
from __future__ import annotations

import logging

from pynput import keyboard

log = logging.getLogger("ghost.hotkeys")

VK = {0x76: "f7", 0x77: "f8", 0x78: "f9", 0x79: "f10", 0x1B: "esc"}
SWALLOW = {0x76, 0x77, 0x78, 0x79}
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
LLKHF_INJECTED = 0x10


def start_hotkeys(overlay) -> keyboard.Listener:
    q = overlay.commands
    held = {"f8": False}
    listener: keyboard.Listener | None = None

    def filt(msg, data):
        name = VK.get(data.vkCode)
        if name is None or data.flags & LLKHF_INJECTED:
            return True
        down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        up = msg in (WM_KEYUP, WM_SYSKEYUP)
        if name == "f8":
            if down and not held["f8"]:
                held["f8"] = True
                q.put("f8_down")
            elif up and held["f8"]:
                held["f8"] = False
                q.put("f8_up")
        elif down:
            q.put(name)
        if data.vkCode in SWALLOW:
            listener.suppress_event()
        return True

    listener = keyboard.Listener(win32_event_filter=filt)
    listener.daemon = True
    listener.start()
    log.info("hotkeys: F8 talk · F7 type · F9 your turn · F10 replay · Esc stop")
    return listener
