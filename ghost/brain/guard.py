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

import atexit
import ctypes
import ctypes.wintypes
import json
import logging
import math
import os
import subprocess
import sys
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
# UI Automation runs in a helper SUBPROCESS (ghost/brain/uia_host.py):
# uiautomation/comtypes can segfault inside some apps (UWP Settings), and a
# native crash in-process would take the whole app down. The rules themselves
# live in uia_rules.py (shared with the host).
from ghost.brain.uia_rules import (  # noqa: E402,F401  (re-exported)
    _ANCESTOR_TYPES, _CAPTION_RE, _DANGER, _DANGER_RE, _MAX_NAME, _SKIP_SELF_TYPES,
    _blocked, _clean, is_dangerous_text,
)

_UIA_TIMEOUT = 1.0
_STARTUP_TIMEOUT = 3.0             # spawn + import uiautomation + COM init (~0.5-1.5 s)
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class _HostDied(Exception):
    pass


class _UIAHost:
    """Client for one long-lived uia_host process; respawns it when it dies."""

    def __init__(self) -> None:
        self._lock = threading.Lock()          # spawn / write
        self._plock = threading.Lock()         # pending table
        self._proc = None
        self._ready = threading.Event()
        self._pending: dict = {}
        self._next = 1
        self._timeouts = 0

    # -- process management -------------------------------------------------
    def _spawn(self) -> None:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        self._ready = threading.Event()
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "ghost.brain.uia_host"],
            cwd=_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, creationflags=flags, bufsize=0,
        )
        self._timeouts = 0
        log.info("uia_host spawned pid=%s", self._proc.pid)
        threading.Thread(target=self._reader, args=(self._proc, self._ready),
                         daemon=True, name="guard-uia-reader").start()

    def _ensure(self):
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                if self._proc is not None:
                    log.warning("uia_host pid=%s gone (rc=%s); respawning",
                                self._proc.pid, self._proc.returncode)
                self._spawn()
            return self._proc, self._ready

    def _reader(self, proc, ready) -> None:
        try:
            for line in proc.stdout:
                try:
                    m = json.loads(line.decode("utf-8"))
                except Exception:              # noqa: BLE001
                    continue
                if m.get("ready"):
                    ready.set()
                    continue
                with self._plock:
                    ent = self._pending.pop(m.get("id"), None)
                if ent:
                    ent[1]["m"] = m
                    ent[0].set()
        except Exception:                      # noqa: BLE001
            pass
        # end of stream: the host exited or crashed -> fail its calls still waiting
        proc._gh_dead = True
        ready.set()
        self._discard(proc)
        with self._plock:
            dead = [k for k, v in self._pending.items() if v[1].get("proc") is proc]
            ents = [self._pending.pop(k) for k in dead]
        for ev, box in ents:
            box["dead"] = True
            ev.set()

    def _discard(self, proc) -> None:
        """proc is known dead/broken: forget it so the next call respawns
        (poll() can still say 'running' for a moment after the pipe breaks)."""
        with self._lock:
            if self._proc is proc:
                self._proc = None
        try:
            proc.kill()
        except Exception:                      # noqa: BLE001
            pass

    def kill(self) -> None:
        with self._lock:
            p, self._proc = self._proc, None
        if p is not None and p.poll() is None:
            try:
                p.stdin.close()
            except Exception:                  # noqa: BLE001
                pass
            try:
                p.wait(0.3)
            except Exception:                  # noqa: BLE001
                p.kill()

    # -- calls ----------------------------------------------------------------
    def call(self, op: str, timeout: float = _UIA_TIMEOUT, **kw):
        """Returns the host's result. Raises _HostDied, TimeoutError or RuntimeError."""
        proc, ready = self._ensure()
        if not ready.wait(_STARTUP_TIMEOUT):
            if proc.poll() is not None:
                self._discard(proc)
                raise _HostDied(f"uia_host exited during start-up (rc={proc.returncode})")
            raise TimeoutError("uia_host start-up timed out")
        if getattr(proc, "_gh_dead", False):
            raise _HostDied("uia_host exited during start-up")
        ev, box = threading.Event(), {"proc": proc}
        with self._plock:
            rid = self._next
            self._next += 1
            self._pending[rid] = (ev, box)
        msg = (json.dumps({"id": rid, "op": op, **kw}, ensure_ascii=True) + "\n").encode("utf-8")
        try:
            with self._lock:
                proc.stdin.write(msg)
                proc.stdin.flush()
        except (OSError, ValueError) as e:
            with self._plock:
                self._pending.pop(rid, None)
            self._discard(proc)
            raise _HostDied(f"uia_host write failed: {e}") from None
        if not ev.wait(timeout):
            with self._plock:
                self._pending.pop(rid, None)
            if proc.poll() is not None or getattr(proc, "_gh_dead", False):
                self._discard(proc)
                raise _HostDied(f"uia_host died (rc={proc.returncode})")
            self._timeouts += 1
            if self._timeouts >= 2:            # wedged inside some app: start fresh
                log.warning("uia_host timed out twice in a row; restarting it")
                self.kill()
            raise TimeoutError("UIA timed out")
        self._timeouts = 0
        if box.get("dead"):
            raise _HostDied(f"uia_host died (rc={proc.poll()})")
        m = box["m"]
        if not m.get("ok"):
            raise RuntimeError(m.get("e", "uia error"))
        return m.get("r")


_host = _UIAHost()
atexit.register(_host.kill)


def check(x: int, y: int) -> tuple[bool, str]:
    """(ok, name). ok=False means a hard stop: name is the offending label.
    Never hangs (UIA runs in a helper process, 1 s budget); fails open.
    If the helper crashes during the check it is respawned and the check retried once."""
    err: Exception | None = None
    for attempt in (0, 1):
        try:
            ok, name = _host.call("check", x=int(x), y=int(y))
            return bool(ok), str(name or "")
        except _HostDied as e:
            err = e
            if attempt == 0:
                log.warning("guard.check(%s,%s): %s; respawning and retrying", x, y, e)
                continue
        except Exception as e:                 # noqa: BLE001
            err = e
        break
    log.warning("guard.check(%s,%s) failed open: %s", x, y, err)
    return True, ""


def focused_name() -> str:
    """Name of the focused UIA element ("" on any problem). Thread-safe."""
    try:
        return str(_host.call("focused_name") or "")
    except Exception:                          # noqa: BLE001
        return ""


def focused_point() -> tuple[int, int] | None:
    """Where the caret lives: a point inside the focused element, if it's a
    sensible size (a text box, not a whole window). None otherwise."""
    try:
        r = _host.call("focused_point")
    except Exception:                          # noqa: BLE001
        return None
    return (int(r[0]), int(r[1])) if r else None


def warm() -> None:
    """Spawn the UIA helper and pay its start-up cost off the critical path."""
    def _w():
        try:
            _host.call("ping", timeout=_STARTUP_TIMEOUT)
        except Exception as e:                 # noqa: BLE001
            log.warning("guard.warm: %s", e)
    threading.Thread(target=_w, daemon=True, name="guard-warm").start()


# --------------------------------------------------------------------------
# Mouse takeover
# --------------------------------------------------------------------------
_MOVE_PX = 60
_WINDOW_S = 0.4
_TELEPORT_PX = 150
_POLL_PX = 30
_POLL_GRACE_S = 0.35

_lock = threading.Lock()
_armed = False
_fired = False
_last = (0, 0)                     # last known cursor position (ours or the user's)
_anchor = (0, 0)                   # where WE last put the cursor (arm / expect / injected)
_ANCHOR_PX = 40                    # a takeover must actually move the cursor this far
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
    global _last, _exp_t, _anchor
    with _lock:
        _last = (int(x), int(y))
        _anchor = _last
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
    global _last, _anchor
    if injected:
        with _lock:
            _last = (x, y)
            _anchor = (x, y)
        return
    with _lock:
        if not _armed or _fired:
            _last = (x, y)
            return
        now = time.monotonic()
        d = math.hypot(x - _last[0], y - _last[1])
        _last = (x, y)
        if d > _TELEPORT_PX:            # a hand makes many small deltas; a jump is a resync
            return
        _recent.append((now, d))
        while _recent and now - _recent[0][0] > _WINDOW_S:
            _recent.popleft()
        total = sum(px for _, px in _recent)
        n = len(_recent)
    if total > _MOVE_PX and n >= 3:
        # Raw hook points can be unclipped (mouse pushed against a screen edge) or
        # spurious; only a cursor that really left where we put it is a takeover.
        cx, cy = _cursor()
        if math.hypot(cx - _anchor[0], cy - _anchor[1]) < _ANCHOR_PX:
            with _lock:
                _recent.clear()
            return
        _takeover(f"moved {total:.0f}px in {n} events, now at ({x},{y})")


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
    global _armed, _fired, _last, _exp_t, _anchor
    _ensure_watch()
    warm()
    with _lock:
        _last = _cursor()
        _anchor = _last
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
