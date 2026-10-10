"""Safety for the brain.

* Hard stops: check(x, y) reads the UI Automation element under the point and
  refuses Send / Pay / Delete / Submit / ... buttons.
* Mouse takeover: while armed, real (non-injected) mouse movement > 25 px in
  300 ms, or a real click, sets ABORT and tells the overlay "You've got it".

Programmatic cursor moves (SetCursorPos, used by pyautogui) never reach the
low-level hook, and mouse_event clicks arrive flagged `injected`, so only the
human's hand counts. executor calls expect(x, y) before every move so the
distance of the next real move is measured from where WE put the cursor.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import math
import queue
import re
import threading
import time
from collections import deque

from ghost import contracts

log = logging.getLogger(__name__)

ABORT = threading.Event()


class Aborted(Exception):
    pass


# --------------------------------------------------------------------------
# Hard stops
# --------------------------------------------------------------------------
_DANGER = (r"send|pay|pay now|purchase|buy|checkout|check out|delete|remove|submit|transfer|"
           r"confirm order|place order|sign out|log ?out|unsubscribe|post")
_DANGER_RE = re.compile(rf"\b(?:{_DANGER})\b", re.I)
# caption text: an imperative that starts with the word, or "click/press ... <word>"
_CAPTION_RE = re.compile(
    rf"(?:^|[.;:!]\s*)(?:now\s+|then\s+|and\s+)?(?:{_DANGER})\b"
    rf"|\b(?:click|press|tap|hit|select|choose|push|activate)\s+(?:on\s+)?(?:the\s+)?"
    rf"[\"'“‘]?(?:{_DANGER})\b",
    re.I,
)
_MAX_NAME = 40
_ANCESTOR_TYPES = {"ButtonControl", "MenuItemControl", "HyperlinkControl", "SplitButtonControl"}
_SKIP_SELF_TYPES = {"WindowControl", "PaneControl", "DocumentControl", "TitleBarControl",
                    "EditControl", "DataItemControl", "TableControl", "ListControl"}
_UIA_TIMEOUT = 1.0


def is_dangerous_text(s: str) -> bool:
    """True for per-step captions like 'Click Send', 'Press Submit', 'Delete the row'.
    Use on captions / target names only, never on the user's task text."""
    return bool(s) and bool(_CAPTION_RE.search(s.strip()))


def _blocked(name: str) -> bool:
    return bool(name) and len(name) <= _MAX_NAME and bool(_DANGER_RE.search(name))


def _clean(s) -> str:
    s = str(s or "")
    s = re.sub(r"[‎‏‪-‮⁦-⁩]", "", s)     # bidi marks (Gmail)
    return " ".join(s.split())


def _inspect(auto, x: int, y: int) -> tuple[bool, str]:
    c = auto.ControlFromPoint(int(x), int(y))
    if c and c.ControlTypeName == "PaneControl" and not c.Name:
        # Chromium/Electron switch on their accessibility tree on the first
        # UIA query and answer with a bare pane; ask again once.
        time.sleep(0.15)
        c = auto.ControlFromPoint(int(x), int(y))
    if not c:
        return True, ""
    ctype = c.ControlTypeName
    name = _clean(c.Name)
    cands: list[str] = []                      # names that may block
    if ctype not in _SKIP_SELF_TYPES:
        cands.append(name)
        try:
            la = c.GetLegacyIAccessiblePattern()
            cands.append(_clean(la.Name))
            if ctype in _ANCESTOR_TYPES:
                cands.append(_clean(la.Value))
        except Exception:
            pass
    best = name if 0 < len(name) <= _MAX_NAME else ""
    p = c
    for _ in range(2):
        try:
            p = p.GetParentControl()
        except Exception:
            p = None
        if not p:
            break
        if p.ControlTypeName in _ANCESTOR_TYPES:
            pn = _clean(p.Name)
            cands.append(pn)
            if not best and 0 < len(pn) <= _MAX_NAME:
                best = pn
    for n in cands:
        if _blocked(n):
            return False, n
    return True, best


# All UIA work runs on ONE long-lived thread. uiautomation keeps a process-wide
# COM singleton; creating it on a short-lived thread that then uninitialises
# COM leaves dangling pointers and the next call segfaults.
_uia_q: "queue.Queue" = queue.Queue()
_uia_thread = None
_uia_lock = threading.Lock()


def _uia_loop() -> None:
    import uiautomation as auto
    auto.InitializeUIAutomationInCurrentThread()       # never uninitialised
    try:
        auto.GetRootControl()                          # create the singleton here
    except Exception as e:                             # noqa: BLE001
        log.warning("uia init: %s", e)
    while True:
        fn, box, done = _uia_q.get()
        try:
            box["r"] = fn(auto)
        except Exception as e:                         # noqa: BLE001
            box["e"] = e
        done.set()


def uia_call(fn, timeout: float = _UIA_TIMEOUT):
    """Run fn(uiautomation_module) on the UIA thread. Returns (ok, result_or_exc)."""
    global _uia_thread
    with _uia_lock:
        if _uia_thread is None:
            _uia_thread = threading.Thread(target=_uia_loop, daemon=True, name="guard-uia")
            _uia_thread.start()
    box: dict = {}
    done = threading.Event()
    _uia_q.put((fn, box, done))
    if not done.wait(timeout):
        return False, TimeoutError("UIA timed out")
    return ("r" in box), box.get("r", box.get("e"))


def check(x: int, y: int) -> tuple[bool, str]:
    """(ok, name). ok=False means a hard stop: name is the offending label.
    Never hangs (UIA runs on a helper thread, 1 s budget); fails open."""
    ok, r = uia_call(lambda auto: _inspect(auto, x, y))
    if ok:
        return r
    log.warning("guard.check(%s,%s) failed open: %s", x, y, r)
    return True, ""


def focused_name() -> str:
    """Name of the focused UIA element ("" on any problem). Thread-safe."""
    def fn(auto):
        c = auto.GetFocusedControl()
        return _clean(c.Name) if c else ""
    ok, r = uia_call(fn)
    return r if ok else ""


def warm() -> None:
    """Start the UIA thread and pay its ~0.5 s init cost off the critical path."""
    threading.Thread(target=lambda: uia_call(lambda auto: None, 3.0), daemon=True).start()


# --------------------------------------------------------------------------
# Mouse takeover
# --------------------------------------------------------------------------
_MOVE_PX = 25
_WINDOW_S = 0.3
_POLL_PX = 30
_POLL_GRACE_S = 0.35

_lock = threading.Lock()
_armed = False
_fired = False
_last = (0, 0)                     # last known cursor position (ours or the user's)
_exp_t = 0.0                       # time of last expect()
_recent: deque = deque()           # (t, px) of real movement
_listener = None
_poller = None


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _cursor() -> tuple[int, int]:
    p = _POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def expect(x: int, y: int) -> None:
    """Executor is about to put the cursor at (x, y) itself."""
    global _last, _exp_t
    with _lock:
        _last = (int(x), int(y))
        _exp_t = time.monotonic()
        _recent.clear()


def _takeover(why: str) -> None:
    global _fired
    with _lock:
        if not _armed or _fired:
            return
        _fired = True
    ABORT.set()
    log.info("takeover: %s", why)
    contracts.to_overlay.put(contracts.Status("aborted", "You've got it"))


def _on_move(x, y, injected=False):
    global _last
    if injected:
        with _lock:
            _last = (x, y)
        return
    with _lock:
        if not _armed or _fired:
            _last = (x, y)
            return
        now = time.monotonic()
        d = math.hypot(x - _last[0], y - _last[1])
        _last = (x, y)
        _recent.append((now, d))
        while _recent and now - _recent[0][0] > _WINDOW_S:
            _recent.popleft()
        total = sum(px for _, px in _recent)
    if total > _MOVE_PX:
        _takeover(f"moved {total:.0f}px")


def _on_click(x, y, button, pressed, injected=False):
    if pressed and not injected and _armed:
        _takeover("clicked")


def _poll_loop():                  # fallback when the low-level hook is unavailable
    while True:
        time.sleep(0.03)
        if not _armed or _fired:
            continue
        with _lock:
            if time.monotonic() - _exp_t < _POLL_GRACE_S:
                continue
            ex, ey = _last
        cx, cy = _cursor()
        if math.hypot(cx - ex, cy - ey) > _POLL_PX:
            _takeover("cursor left expected position")


def _ensure_watch() -> None:
    global _listener, _poller
    if _listener is not None or _poller is not None:
        return
    try:
        from pynput import mouse
        _listener = mouse.Listener(on_move=_on_move, on_click=_on_click)
        _listener.daemon = True
        _listener.start()
        _listener.wait()
    except Exception as e:         # noqa: BLE001
        log.warning("pynput listener failed (%s); polling GetCursorPos", e)
        _listener = None
        _poller = threading.Thread(target=_poll_loop, daemon=True, name="guard-poll")
        _poller.start()


def arm() -> None:
    """Start watching for the user grabbing the mouse (call at run start)."""
    global _armed, _fired, _last, _exp_t
    _ensure_watch()
    warm()
    with _lock:
        _last = _cursor()
        _exp_t = time.monotonic()
        _recent.clear()
        _fired = False
        _armed = True


def disarm() -> None:
    global _armed
    with _lock:
        _armed = False


def raise_if_aborted() -> None:
    if ABORT.is_set():
        raise Aborted()


if __name__ == "__main__":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    print("guard self-test: hover things for 6 s")
    for _ in range(12):
        x, y = _cursor()
        t0 = time.perf_counter()
        r = check(x, y)
        print(f"({x},{y}) -> {r}   {1000 * (time.perf_counter() - t0):.0f} ms")
        time.sleep(0.5)
