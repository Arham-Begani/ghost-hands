"""Scripted stand-in for the brain so the overlay runs with no API.

Loops: "freeze the top row" in Google Sheets (6 steps, every cue type), then a
short "send this sheet to my manager" run that ends in a HandOver on Send.
Also writes runs/demo-fake/steps.json + runs/LAST for coach mode."""
from __future__ import annotations

import ctypes
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime

from ghost.contracts import (
    LAST_RUN_FILE, RUNS_DIR, Abort, GhostAction, HandOver, RunFinished, RunTask, Status, TaskHeard,
    to_brain, to_overlay,
)

log = logging.getLogger("ghost.fake_brain")

# Coordinates designed on 1920x1080 Chrome + Google Sheets, scaled to the real screen.
FREEZE = [
    dict(kind="click", x=96, y=262, caption="Click a cell so the sheet is active", target_name="A1"),
    dict(kind="click", x=196, y=137, caption="Open the View menu", target_name="View"),
    dict(kind="move", x=262, y=232, caption="Hover Freeze to see the options", target_name="Freeze"),
    dict(kind="click", x=486, y=232, caption="Choose 1 row to lock the header in place", target_name="1 row"),
    dict(kind="scroll", x=900, y=620, scroll_y=6, caption="Scroll down: the header row stays put"),
    dict(kind="keypress", x=900, y=620, keys=["CTRL", "HOME"], caption="Press Ctrl + Home to jump back to the top"),
]
SHARE = [
    dict(kind="click", x=1786, y=106, caption="Open Share to send the sheet", target_name="Share"),
    dict(kind="type", x=960, y=452, text="priya.manager@company.com",
         caption="Type your manager's email address", target_name="Add people"),
]
SEND_AT = (1206, 700)


def _screen() -> tuple[int, int]:
    try:
        u = ctypes.windll.user32
        return u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    except Exception:
        return 1920, 1080


class FakeBrain:
    def __init__(self, loop: bool = True, pace: float = 1.0) -> None:
        self.loop = loop
        self.pace = pace
        w, h = _screen()
        self.sx, self.sy = w / 1920, h / 1080
        self.aborted = threading.Event()
        self.pending_task: str | None = None

    # ------------------------------------------------------------ helpers
    def _xy(self, x: int, y: int) -> tuple[int, int]:
        return round(x * self.sx), round(y * self.sy)

    def emit(self, ev: object) -> None:
        to_overlay.put(ev)

    def poll_inbox(self) -> None:
        while True:
            try:
                ev = to_brain.get_nowait()
            except queue.Empty:
                return
            if isinstance(ev, Abort):
                self.aborted.set()
            elif isinstance(ev, RunTask):
                self.pending_task = ev.text
            else:
                log.info("fake brain ignores %r", ev)

    def sleep(self, secs: float) -> bool:
        """Sleep while watching to_brain; False if the run was aborted."""
        end = time.monotonic() + secs
        while time.monotonic() < end:
            self.poll_inbox()
            if self.aborted.is_set():
                return False
            time.sleep(0.03)
        return True

    def act(self, spec: dict, step: int, total: int) -> bool:
        x, y = self._xy(spec["x"], spec["y"])
        self.emit(Status("acting", "", step, total))
        a = GhostAction(kind=spec["kind"], x=x, y=y, caption=spec["caption"],
                        target_name=spec.get("target_name", ""), text=spec.get("text", ""),
                        keys=spec.get("keys", []), scroll_y=spec.get("scroll_y", 0), step=step)
        self.emit(a)
        if not a.ack.wait(3.0):
            log.warning("ack timeout on step %d", step)
        # the executor would act here; typing takes ~30 ms/char
        return self.sleep(self.pace + 0.03 * len(a.text))

    # ------------------------------------------------------------ runs
    def intro(self, task: str) -> bool:
        self.emit(Status("idle"))
        if not self.sleep(2.0):
            return False
        self.emit(Status("listening"))
        if not self.sleep(1.8):
            return False
        self.emit(Status("transcribing"))
        if not self.sleep(0.6):
            return False
        self.emit(TaskHeard(task))
        if not self.sleep(2.6):
            return False
        self.emit(Status("thinking"))
        return self.sleep(1.8)

    def run_freeze(self, task: str) -> bool:
        if not self.intro(task):
            return False
        total = len(FREEZE)
        for i, spec in enumerate(FREEZE, 1):
            if i == 4:                                  # a second model turn mid-run
                self.emit(Status("thinking", "", i - 1, total))
                if not self.sleep(1.4):
                    return False
            if not self.act(spec, i, total):
                return False
        self.emit(RunFinished("runs/demo-fake", True, "Top row frozen: your header now stays put"))
        return self.sleep(6.0)

    def run_share(self) -> bool:
        if not self.intro("send this sheet to my manager"):
            return False
        for i, spec in enumerate(SHARE, 1):
            if not self.act(spec, i, 0):
                return False
        self.emit(Status("thinking", "", len(SHARE), 0))
        if not self.sleep(1.2):
            return False
        x, y = self._xy(*SEND_AT)
        self.emit(HandOver(x, y, 'Stopped before "Send". That one is yours.'))
        if not self.sleep(1.2):
            return False
        self.emit(RunFinished("runs/demo-fake", False, "Paused at Send: you make the call"))
        return self.sleep(6.5)

    def run(self) -> None:
        write_fake_run(self)
        while True:
            self.aborted.clear()
            task = self.pending_task or "freeze the top row"
            self.pending_task = None
            ok = self.run_freeze(task)
            if ok:
                ok = self.run_share()
            if not ok:
                self.emit(Status("aborted", "You've got it"))
                self.aborted.clear()
                self.sleep(3.0)
            if not self.loop:
                return


def write_fake_run(fb: FakeBrain) -> str:
    run_dir = os.path.join(RUNS_DIR, "demo-fake")
    os.makedirs(run_dir, exist_ok=True)
    steps = []
    for i, spec in enumerate(FREEZE, 1):
        x, y = fb._xy(spec["x"], spec["y"])
        steps.append({
            "index": i, "kind": spec["kind"], "x": x, "y": y, "end_x": 0, "end_y": 0,
            "scroll_y": spec.get("scroll_y", 0), "text": spec.get("text", ""), "keys": spec.get("keys", []),
            "caption": spec["caption"], "target_name": spec.get("target_name", ""),
            "crop": "", "t": round(1.8 * i, 2),
        })
    w, h = _screen()
    doc = {"task": "freeze the top row", "created": datetime.now().astimezone().isoformat(timespec="seconds"),
           "screen": [w, h], "ok": True, "steps": steps}
    with open(os.path.join(run_dir, "steps.json"), "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
    with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
        f.write(run_dir.replace("\\", "/") + "/")
    return run_dir


def start_fake_brain(loop: bool = True) -> threading.Thread:
    pace = float(os.getenv("GH_TEACH_PACE", "1.0"))
    t = threading.Thread(target=FakeBrain(loop=loop, pace=pace).run, name="fake-brain", daemon=True)
    t.start()
    log.info("fake brain running (loop=%s)", loop)
    return t
