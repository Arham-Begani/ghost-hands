"""Ghost Hands brain: the OpenAI computer-use loop (Responses API, GA `computer` tool).

run_task(task) drives gpt-6.1-sol on the user's real screen. For every action the model
returns it: maps coords to physical px, hard-stops dangerous targets (guard), emits a
GhostAction for the overlay, waits the ack, logs + narrates, executes, then paces.
See OPENAI_NOTES.md for the confirmed API shapes.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import threading
import time

from ghost.contracts import Error, GhostAction, HandOver, RunFinished, Status, to_overlay

# --------------------------------------------------------------------------- logging
log = logging.getLogger("ghost.brain")


def _setup_log() -> None:
    if any(isinstance(h, logging.FileHandler) for h in log.handlers):
        return
    try:
        os.makedirs("runs", exist_ok=True)
        h = logging.FileHandler(os.path.join("runs", "brain.log"), encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        log.addHandler(h)
        log.setLevel(logging.INFO)
    except Exception:
        pass


_setup_log()

# --------------------------------------------------------------------------- prompt
SYSTEM_PROMPT = """You are Ghost Hands, a patient teacher. You operate the user's REAL Windows PC to SHOW them how to do a task in the app that is open, so they can repeat it themselves afterwards. Everything you do is watched live and narrated aloud.

Which app:
- Act in the app the task names ("in Windows" / "in Settings" means Windows Settings; open it from Start). If no app is named, act in the app that is visibly in front.
- Never operate terminals, code editors (VS Code), or AI assistant windows (Claude, ChatGPT, Codex): they belong to the presenter. If one is in front and the task names no app, use the app behind it (e.g. the Google Sheet in Chrome).

How to act:
- Prefer the visible path a human would learn: menu bar -> submenu -> item, toolbar buttons, right-click menus, dialog fields. Use keyboard shortcuts or typed formulas only when no visible menu path exists.
- Take the smallest correct sequence of steps. Do not explore, do not hover around, do not undo and redo. Do not open new apps or tabs unless the task needs it. Never close windows.
- Prefer ONE action per computer call, so each step can be narrated on its own.
- You already get a fresh screenshot with the task and after every call; do not request a screenshot before your first action. Only take one if the screen is not what you expected.

Narration (required):
- Before EVERY computer call, write exactly one short line of text (max 80 characters) that starts with a verb and says WHAT you are doing and WHY, e.g. "Open the View menu to find the freeze options". If one call has several actions, write one line per action, in order.
- No markdown, no quotes around the line, no step numbers.

Safety:
- Never click Send, Share-confirm, Pay, Buy, Purchase, Delete, Remove, Submit, Post, Checkout or anything that sends, spends, publishes or deletes. When the task's last step would be such a button, MOVE the pointer onto that button (a move action, no click) and stop. The user makes that final click. (The app enforces this anyway.)
- If the task needs an email address you do not know, use manager@company.com.

Finishing:
- When the task is done, reply with ONE line of at most 70 characters saying what changed on screen, e.g. "Row 1 is now frozen". If you stopped before a send-type button, say e.g. "Ready to send: you click Send".

Reference paths (Google Sheets in Chrome, sheet columns Name | Month | Region | Sales, 20 rows):
- Freeze the top row: View -> Freeze -> 1 row.
- Bar chart of sales by month: select the Month column, Ctrl-click the Sales column header, Insert -> Chart, then pick a column/bar chart type in the chart editor.
- Highlight sales above 50,000 in green: select the Sales column, Format -> Conditional formatting, condition "Greater than", value 50000, choose green fill, Done.
- Split names into first and last: select the Name column, Data -> Split text to columns, separator Space.
- Send this sheet to my manager: Share button, type the email, then move onto Send and stop.
Windows Settings "Turn on dark mode" (Windows 10): Start -> Settings -> Personalization -> Colors -> "Choose your color" dropdown -> Dark.
"""

# --------------------------------------------------------------------------- config
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


MODEL = lambda: os.getenv("GH_MODEL", "gpt-6.1-sol")  # noqa: E731
_CLICKY = {"click", "double_click", "right_click", "drag", "move"}
_MOD_MAP = {"CTRL": "ctrl", "CONTROL": "ctrl", "SHIFT": "shift", "ALT": "alt", "OPTION": "alt",
            "META": "win", "CMD": "win", "COMMAND": "win", "WIN": "win", "SUPER": "win"}
_KEY_PRETTY = {"CTRL": "Ctrl", "CONTROL": "Ctrl", "ESC": "Esc", "ESCAPE": "Esc", "ENTER": "Enter",
               "RETURN": "Enter", "ARROWLEFT": "Left", "ARROWRIGHT": "Right", "ARROWUP": "Up",
               "ARROWDOWN": "Down", "PAGEDOWN": "Page Down", "PAGEUP": "Page Up", "META": "Win",
               "CMD": "Win", "SUPER": "Win", "BACKSPACE": "Backspace", "TAB": "Tab", "SPACE": "Space"}
_running_lock = threading.Lock()


class _Stop(Exception):
    """Run ended early (hand-over / abort); already reported."""


# --------------------------------------------------------------------------- helpers
def _emit(ev) -> None:
    to_overlay.put(ev)


def _g(obj, name, default=None):
    """Field access for SDK models or plain dicts."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _clean_line(s: str, limit: int) -> str:
    s = re.sub(r"[*_`#>]+", "", s or "").strip().strip('"').strip("'").strip()
    s = re.sub(r"^\s*(step\s*\d+[:.)-]?|\d+[.)])\s*", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s)
    if len(s) > limit:
        cut = s[: limit - 1].rsplit(" ", 1)[0]
        s = cut.rstrip(",;:") + "…"
    return s


def _texts(response) -> tuple[list[str], str]:
    """(commentary lines, final text) from a response's output items."""
    commentary, final = [], []
    for item in _g(response, "output", []) or []:
        if _g(item, "type") != "message":
            continue
        parts = [_g(c, "text", "") for c in (_g(item, "content", []) or [])
                 if _g(c, "type") in ("output_text", "text")]
        text = "\n".join(p for p in parts if p).strip()
        if not text:
            continue
        (final if _g(item, "phase") == "final_answer" else commentary).append(text)
    lines = [ln for t in commentary for ln in t.splitlines() if ln.strip()]
    return lines, "\n".join(final).strip()


def _pretty_keys(keys) -> str:
    return " + ".join(_KEY_PRETTY.get(k.upper(), k.capitalize() if len(k) > 1 else k.upper())
                      for k in keys)


def _template(kind: str, name: str, text: str, keys, scroll_y: int) -> str:
    q = f' "{name}"' if name else ""
    if kind == "click":
        return f"Click{q or ' here'}"
    if kind == "double_click":
        return f"Double-click{q or ' here'}"
    if kind == "right_click":
        return f"Right-click{q or ' here'} for more options"
    if kind == "type":
        return _clean_line(f'Type "{text}"', 80)
    if kind == "keypress":
        return f"Press {_pretty_keys(keys)}"
    if kind == "scroll":
        return "Scroll down" if scroll_y >= 0 else "Scroll up"
    if kind == "drag":
        return "Drag to select"
    if kind == "move":
        return f"Point at{q or ' the target'}"
    if kind == "wait":
        return "Wait for the app to catch up"
    return kind.replace("_", " ").capitalize()


# --------------------------------------------------------------------------- the run
class _Run:
    def __init__(self, task: str, not_before: float):
        from openai import OpenAI
        from ghost.brain import executor, guard, runlog, screen  # hands agent's modules
        self.task = task
        self.not_before = not_before
        self.screen, self.executor, self.guard = screen, executor, guard
        self.client = OpenAI(timeout=90.0, max_retries=1)
        self.rl = runlog.RunLog(task)
        self.step = 0
        self.t0 = time.time()
        self.scale = 1.0
        self.pace = _env_float("GH_TEACH_PACE", 1.0)
        self.max_turns = int(_env_float("GH_MAX_TURNS", 25))
        self.last_xy = None
        try:
            self.sw, self.sh = screen.screen_size()
        except Exception:
            self.sw, self.sh = 1920, 1080
        try:
            from ghost.brain import voice
            self.voice = voice
        except Exception as e:
            log.warning("voice unavailable: %s", e)
            self.voice = None

    # ---- small utilities
    def aborted(self) -> bool:
        return self.guard.ABORT.is_set()

    def check_abort(self) -> None:
        if self.aborted():
            raise _Stop("aborted")

    def speak(self, text: str, path: str | None = None) -> None:
        if not self.voice or not text:
            return
        try:
            self.voice.speak(text, path)
        except Exception as e:
            log.warning("speak failed: %s", e)

    def shot(self) -> str:
        png, self.scale = self.screen.capture()
        return _data_url(png)

    def phys(self, x, y) -> tuple[int, int]:
        px = int(round(float(x or 0) * self.scale))
        py = int(round(float(y or 0) * self.scale))
        return max(0, min(self.sw - 1, px)), max(0, min(self.sh - 1, py))

    def ask(self, **kw):
        """One model call, HUD shows Thinking meanwhile."""
        self.check_abort()
        _emit(Status("thinking", "Thinking", self.step, 0))
        args = dict(model=MODEL(), tools=[{"type": "computer"}], instructions=SYSTEM_PROMPT, **kw)
        effort = os.getenv("GH_REASONING_EFFORT", "").strip()
        if effort:
            args["reasoning"] = {"effort": effort}
        t = time.time()
        try:
            resp = self.client.responses.create(**args)
        except Exception as e:
            if effort and "reasoning" in str(e).lower():
                args.pop("reasoning", None)
                resp = self.client.responses.create(**args)
            else:
                raise
        log.info("model %.1fs id=%s", time.time() - t, _g(resp, "id"))
        self.check_abort()
        return resp

    def hand_over(self, x: int, y: int, name: str, why: str = "", summary: str = "") -> None:
        label = name or "Send"
        reason = why or f'Stopped before "{label}". That one is yours.'
        log.info("HANDOVER at (%s,%s) name=%r", x, y, name)
        _emit(HandOver(x, y, reason))
        self.speak(reason)
        summary = _clean_line(summary or f"Paused before {label}: you make the call", 70)
        try:
            if hasattr(self.rl, "record_handover"):
                crop = None
                try:
                    crop = self.screen.crop_png(x, y)
                except Exception:
                    pass
                self.rl.record_handover(x, y, reason, name or "", crop)
        except Exception as e:
            log.warning("runlog.record_handover: %s", e)
        try:
            self.rl.finish(False, summary)
        except Exception as e:
            log.warning("runlog.finish: %s", e)
        _emit(RunFinished(self.rl.run_dir, False, summary))
        raise _Stop("handover")

    # ---- the loop
    def run(self) -> None:
        log.info("RUN task=%r model=%s", self.task, MODEL())
        resp = self.ask(input=[{"role": "user", "content": [
            {"type": "input_text", "text": f"Task: {self.task}\nShow me how, step by step, on this screen."},
            {"type": "input_image", "image_url": self.shot(), "detail": "original"},
        ]}])
        for turn in range(self.max_turns):
            calls = [it for it in (_g(resp, "output", []) or []) if _g(it, "type") == "computer_call"]
            lines, final = _texts(resp)
            if not calls:
                return self.finish(final or " ".join(lines))
            call = calls[0]
            if len(calls) > 1:
                log.warning("%d computer_calls in one response; handling the first", len(calls))
            self.safety(call)
            actions = list(_g(call, "actions", None) or [])
            if not actions and _g(call, "action", None) is not None:
                actions = [_g(call, "action")]
            for i, act in enumerate(actions):
                narr = lines[i] if len(lines) == len(actions) else (" ".join(lines) if i == 0 else "")
                self.do_action(act, narr)
            self.check_abort()
            out = {"type": "computer_call_output", "call_id": _g(call, "call_id"),
                   "output": {"type": "computer_screenshot", "image_url": self.shot(), "detail": "original"}}
            if self._acks:
                out["acknowledged_safety_checks"] = self._acks
            resp = self.ask(previous_response_id=_g(resp, "id"), input=[out])
        log.warning("turn cap %d reached", self.max_turns)
        self.finish("", capped=True)

    _acks: list = []

    def safety(self, call) -> None:
        """pending_safety_checks: never auto-ack unless the code is allow-listed (GH_ACK_SAFETY_CODES)."""
        self._acks = []
        checks = list(_g(call, "pending_safety_checks", None) or [])
        if not checks:
            return
        allowed = {c.strip() for c in os.getenv("GH_ACK_SAFETY_CODES", "").split(",") if c.strip()}
        for c in checks:
            code, msg = _g(c, "code", "") or "", _g(c, "message", "") or ""
            log.info("safety check code=%s msg=%s", code, msg)
            if code in allowed:
                self._acks.append({"id": _g(c, "id"), "code": code, "message": msg})
                continue
            acts = list(_g(call, "actions", None) or [])
            x, y = (self.phys(_g(acts[0], "x", 0), _g(acts[0], "y", 0))
                    if acts and _g(acts[0], "x", None) is not None
                    else (self.last_xy or (self.sw // 2, self.sh // 2)))
            self.hand_over(x, y, "", "Safety check: this next step is yours to confirm.",
                           "Paused for a safety check: you make the call")

    def do_action(self, act, narration: str) -> None:
        self.check_abort()
        t = _g(act, "type", "")
        kind, x, y = t, 0, 0
        text, keys, ex, ey, sy = "", [], 0, 0, 0
        mods = [k for k in (_g(act, "keys", None) or []) if t != "keypress"]
        if t == "screenshot":
            return
        if t == "wait":
            _emit(Status("acting", "Waiting", self.step, 0))
            time.sleep(1.5)
            return
        if t in ("click", "double_click", "move"):
            x, y = self.phys(_g(act, "x"), _g(act, "y"))
            if t == "click":
                btn = (_g(act, "button", "left") or "left").lower()
                if btn == "right":
                    kind = "right_click"
                elif btn != "left":
                    log.warning("click button %r unsupported; skipping", btn)
                    return
        elif t == "drag":
            path = list(_g(act, "path", None) or [])
            if len(path) < 2:
                log.warning("drag with <2 points; skipping: %r", act)
                return
            x, y = self.phys(_g(path[0], "x"), _g(path[0], "y"))
            ex, ey = self.phys(_g(path[-1], "x"), _g(path[-1], "y"))
        elif t == "scroll":
            x, y = self.phys(_g(act, "x"), _g(act, "y"))
            px = float(_g(act, "scroll_y", 0) or 0)
            if px == 0:
                log.info("horizontal-only scroll ignored: %r", act)
                return
            sy = int(round(px / 100.0)) or (1 if px > 0 else -1)
        elif t == "type":
            text = _g(act, "text", "") or ""
            try:
                fp = self.guard.focused_point()  # ghost hovers the box being typed into
            except Exception:
                fp = None
            x, y = fp or self.last_xy or (self.sw // 2, self.sh // 2)
        elif t == "keypress":
            keys = [str(k) for k in (_g(act, "keys", None) or [])]
            x, y = self.last_xy or (self.sw // 2, self.sh // 2)
        else:
            log.warning("unknown action %r; skipping", act)
            return

        # --- hard stop BEFORE anything is shown or executed
        name = ""
        if kind in _CLICKY:
            ok, name = self.guard.check(x, y)
            if not ok:
                self.hand_over(x, y, name)
        if kind in _CLICKY and narration and self.guard.is_dangerous_text(narration):
            m = re.search(r"\b(send|pay|purchase|buy|checkout|delete|remove|submit|transfer|post)\b", narration, re.I)
            word = m.group(1).capitalize() if m else ""
            self.hand_over(x, y, name if (name and re.search(word or "^$", name, re.I)) else (word or name))
        if kind == "keypress" and {k.upper() for k in keys} & {"ENTER", "RETURN", "SPACE"}:
            focused = _focused_name()
            if focused and self.guard.is_dangerous_text(focused):
                self.hand_over(x, y, focused)

        caption = _clean_line(narration, 80) if narration else _template(kind, name, text, keys, sy)
        self.step += 1
        n = self.step
        _emit(Status("acting", "", n, 0))
        ga = GhostAction(kind=kind, x=x, y=y, caption=caption, target_name=name or "", text=text,
                         keys=list(keys), end_x=ex, end_y=ey, scroll_y=sy, step=n, duration_ms=700)
        log.info("STEP %d %s (%d,%d) %r name=%r text=%r keys=%r", n, kind, x, y, caption, name, text, keys)
        if n == 1 and self.not_before > time.time():        # let the TaskHeard splash finish
            time.sleep(self.not_before - time.time())
        _emit(ga)
        try:
            audio = self.rl.audio_path(n)
        except Exception:
            audio = None
        self.speak(caption, audio)
        crop = None
        try:
            crop = self.screen.crop_png(x, y)
        except Exception as e:
            log.warning("crop failed: %s", e)
        try:
            self.rl.record({"index": n, "kind": kind, "x": x, "y": y, "end_x": ex, "end_y": ey,
                            "scroll_y": sy, "text": text, "keys": list(keys), "caption": caption,
                            "target_name": name or "", "t": round(time.time() - self.t0, 2)}, crop)
        except Exception as e:
            log.warning("runlog.record failed: %s", e)
        if not ga.ack.wait(3.0):
            log.info("ack timeout step %d", n)
        self.check_abort()

        held = [_MOD_MAP[k.upper()] for k in mods if k.upper() in _MOD_MAP]
        try:
            if held:
                import pyautogui
                for k in held:
                    pyautogui.keyDown(k)
            self.executor.do(kind, x, y, text=text, keys=tuple(keys), end_x=ex, end_y=ey, scroll_y=sy)
        finally:
            if held:
                import pyautogui
                for k in reversed(held):
                    pyautogui.keyUp(k)
        self.last_xy = (ex, ey) if kind == "drag" else (x, y)
        self.check_abort()
        time.sleep(self.pace)

    def finish(self, final: str, capped: bool = False) -> None:
        if capped:
            summary, ok = "Stopped after too many steps", False
        else:
            first = next((ln for ln in (final or "").splitlines() if ln.strip()), "")
            summary, ok = (_clean_line(first, 70) or "Done"), True
        log.info("FINISH ok=%s summary=%r", ok, summary)
        try:
            self.rl.finish(ok, summary)
        except Exception as e:
            log.warning("runlog.finish: %s", e)
        _emit(RunFinished(self.rl.run_dir, ok, summary))
        self.speak(summary)


def _focused_name() -> str:
    """Focused UIA element name via guard's single UIA thread (UIA on short-lived threads segfaults)."""
    try:
        from ghost.brain import guard
        return (guard.focused_name() or "")[:40]
    except Exception:
        return ""


# --------------------------------------------------------------------------- public API
def run_task(task: str, not_before: float = 0.0) -> None:
    """Run one task end to end. Never raises. `not_before`: epoch time before which the
    first GhostAction must not be emitted (TaskHeard splash); the model call overlaps it."""
    if not _running_lock.acquire(blocking=False):
        log.info("run_task ignored (busy): %r", task)
        return
    guard = None
    run = None
    try:
        from ghost.brain import guard as _guard
        guard = _guard
        guard.ABORT.clear()
        run = _Run(task, not_before)
        guard.arm()
        run.run()
    except _Stop as s:
        log.info("run stopped: %s", s)
        if str(s) == "aborted":
            _finish_aborted(run)
    except Exception as e:  # noqa: BLE001
        is_abort = guard is not None and (isinstance(e, getattr(guard, "Aborted", ())) or guard.ABORT.is_set())
        if is_abort:
            log.info("run aborted")
            _finish_aborted(run)
        else:
            log.exception("run failed")
            _emit(Error(_calm(e)))
            _emit(Status("idle"))          # no runlog.finish: keep runs/LAST pointing at a usable run
    finally:
        if guard is not None:
            try:
                guard.disarm()
            except Exception:
                pass
        _running_lock.release()


def _finish_aborted(run) -> None:
    if run is None:
        return
    try:
        run.rl.finish(False, "You took over")
    except Exception as e:
        log.warning("runlog.finish: %s", e)


def _calm(e: Exception) -> str:
    s = f"{type(e).__name__}: {e}"
    low = s.lower()
    if "api_key" in low or "authentication" in low or "401" in low:
        return "OpenAI key missing or invalid. Check .env."
    if "timeout" in low or "connection" in low:
        return "Network hiccup talking to OpenAI. Try again."
    if "modulenotfound" in low or "importerror" in low:
        return "A brain module is missing. Check the console."
    if "ratelimit" in low or "429" in low:
        return "OpenAI is busy (rate limit). Try again in a moment."
    return "Something went wrong. Try again."


def is_running() -> bool:
    return _running_lock.locked()
