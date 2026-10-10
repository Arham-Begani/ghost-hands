"""
Ghost Hands - shared contract between BRAIN (Codex) and OVERLAY (Claude).

FROZEN. Neither agent edits this file. If a change is needed, the agent says so
and Arham edits it by hand, then tells the other agent.

Coordinates everywhere = PHYSICAL pixels on the PRIMARY monitor.
main.py makes the process per-monitor DPI aware BEFORE importing Qt / pyautogui / mss.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Literal

State = Literal[
    "idle", "listening", "transcribing", "thinking", "acting",
    "handover", "done", "error", "aborted", "coach", "replay",
]
ActionKind = Literal[
    "click", "double_click", "right_click", "drag", "move",
    "scroll", "type", "keypress", "wait",
]

# --------------------------------------------------------------------------
# BRAIN -> OVERLAY   (put on `to_overlay`)
# --------------------------------------------------------------------------

@dataclass
class Status:
    state: State
    text: str = ""        # short HUD text: "Thinking", "Step 3", "Listening"
    step: int = 0
    total: int = 0        # 0 = unknown


@dataclass
class TaskHeard:
    text: str             # transcribed task; overlay shows it big for ~2 s


@dataclass
class GhostAction:
    """Overlay glides the ghost cursor to (x, y), shows the caption, plays the
    action cue, then calls ack.set(). Brain waits ack (timeout 3 s) and only
    THEN performs the real input. This is what makes it look like the ghost
    is doing the clicking."""
    kind: ActionKind
    x: int
    y: int
    caption: str                       # <= 80 chars, starts with a verb
    target_name: str = ""              # UI Automation name under the point, may be ""
    text: str = ""                     # kind == "type": the text being typed
    keys: list[str] = field(default_factory=list)   # kind == "keypress": ["CTRL","C"]
    end_x: int = 0                     # kind == "drag"
    end_y: int = 0
    scroll_y: int = 0                  # kind == "scroll": +down / -up (wheel clicks)
    step: int = 0
    duration_ms: int = 700
    ack: threading.Event = field(default_factory=threading.Event, repr=False)


@dataclass
class HandOver:
    """Hard stop. Ghost parks at (x, y) and refuses: Send / Pay / Delete / Submit..."""
    x: int
    y: int
    reason: str           # 'Stopped before "Send". That one is yours.'


@dataclass
class RunFinished:
    run_dir: str          # runs/<run_id>/  (contains steps.json)
    ok: bool
    summary: str          # one line, shown + spoken


@dataclass
class Error:
    text: str


@dataclass
class MicLevel:            # voice.py while recording, ~30 Hz; overlay mic bar
    level: float          # 0..1 (RMS, already smoothed / normalised)


# --------------------------------------------------------------------------
# OVERLAY -> BRAIN   (put on `to_brain`)
# --------------------------------------------------------------------------

@dataclass
class StartListening:      # F8 pressed (hold to talk)
    pass


@dataclass
class StopListening:       # F8 released -> brain transcribes, emits TaskHeard, runs
    pass


@dataclass
class RunTask:             # typed fallback (F7 box)
    text: str


@dataclass
class ReplayRun:           # F10 - fallback: re-execute a logged run with NO model calls
    run_dir: str


@dataclass
class Abort:               # Esc, or overlay-side reasons
    reason: str = "user"


to_overlay: "queue.Queue[object]" = queue.Queue()
to_brain: "queue.Queue[object]" = queue.Queue()

# --------------------------------------------------------------------------
# RUN LOG  (brain writes, overlay's coach mode + brain's replay read)
# --------------------------------------------------------------------------
# runs/<run_id>/steps.json
# {
#   "task": "freeze the top row",
#   "created": "2026-10-10T12:41:03+05:30",
#   "screen": [1920, 1080],
#   "ok": true,
#   "steps": [
#     {"index": 1, "kind": "click", "x": 812, "y": 344,
#      "end_x": 0, "end_y": 0, "scroll_y": 0, "text": "", "keys": [],
#      "caption": "Open the View menu", "target_name": "View",
#      "crop": "step01.png", "t": 1.82}
#   ]
# }
# crop = 360x240 PNG centred on (x, y), captured BEFORE the action.
RUNS_DIR = "runs"
LAST_RUN_FILE = "runs/LAST"   # text file containing the latest run_dir
