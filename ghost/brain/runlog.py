"""Run log: runs/<YYYYmmdd-HHMMSS>-<slug>/steps.json (+ stepNN.png crops).

    log = RunLog("freeze the top row")
    log.record({"index": 1, "kind": "click", "x": .., "y": .., "caption": .., ...}, crop_png)
    log.audio_path(1)          # where voice writes step01.mp3
    log.record_handover(x, y, reason)
    log.finish(ok, summary)    # writes steps.json + runs/LAST

steps.json is rewritten after every record, so a crash still leaves a usable log.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
import time

from ghost import contracts

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

_STEP_KEYS = ("index", "kind", "x", "y", "end_x", "end_y", "scroll_y", "text", "keys",
              "caption", "target_name")
_DEFAULTS = {"index": 0, "kind": "", "x": 0, "y": 0, "end_x": 0, "end_y": 0, "scroll_y": 0,
             "text": "", "keys": [], "caption": "", "target_name": ""}


def _abs(run_dir: str) -> str:
    return run_dir if os.path.isabs(run_dir) else os.path.join(ROOT, run_dir)


def _slug(task: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (task or "").lower()).strip("-")
    return (s[:40].rstrip("-") or "run")


def _screen() -> list[int]:
    try:
        from ghost.brain import screen
        return list(screen.screen_size())
    except Exception:
        return [0, 0]


class RunLog:
    def __init__(self, task: str):
        self.task = task or ""
        stamp = time.strftime("%Y%m%d-%H%M%S")
        base = f"{contracts.RUNS_DIR}/{stamp}-{_slug(self.task)}"
        rid, n = base, 2
        while os.path.exists(_abs(rid)):
            rid, n = f"{base}-{n}", n + 1
        self.run_dir = rid + "/"
        self._abs = _abs(self.run_dir)
        os.makedirs(self._abs, exist_ok=True)
        self._t0 = time.monotonic()
        self._lock = threading.Lock()
        self.data = {
            "task": self.task,
            "created": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "screen": _screen(),
            "ok": False,
            "summary": "",
            "steps": [],
        }
        self._write()

    # ------------------------------------------------------------------
    def audio_path(self, index: int) -> str:
        return os.path.join(self._abs, f"step{int(index):02d}.mp3")

    def record(self, step: dict, crop_png: bytes | None = None) -> dict:
        with self._lock:
            idx = int(step.get("index") or len(self.data["steps"]) + 1)
            rec = {k: step.get(k, _DEFAULTS[k]) for k in _STEP_KEYS}
            rec["index"] = idx
            rec["keys"] = list(rec["keys"] or [])
            for k in ("x", "y", "end_x", "end_y", "scroll_y"):
                rec[k] = int(rec[k] or 0)
            rec["crop"] = ""
            if crop_png:
                name = f"step{idx:02d}.png"
                with open(os.path.join(self._abs, name), "wb") as f:
                    f.write(crop_png)
                rec["crop"] = name
            rec["t"] = round(time.monotonic() - self._t0, 2)
            rec["audio"] = ""
            self.data["steps"].append(rec)
            self._write()
            return rec

    def record_handover(self, x: int, y: int, reason: str, target_name: str = "",
                        crop_png: bytes | None = None) -> dict:
        idx = len(self.data["steps"]) + 1
        return self.record({"index": idx, "kind": "handover", "x": x, "y": y,
                            "caption": reason, "target_name": target_name}, crop_png)

    def finish(self, ok: bool, summary: str) -> None:
        with self._lock:
            for s in self.data["steps"]:
                name = f"step{int(s['index']):02d}.mp3"
                s["audio"] = name if os.path.exists(os.path.join(self._abs, name)) else ""
            self.data["ok"] = bool(ok)
            self.data["summary"] = summary or ""
            self._write()
            if not self.data["steps"]:      # an empty run must never replace the F9/F10 run
                return
            with open(os.path.join(ROOT, contracts.LAST_RUN_FILE), "w", encoding="utf-8") as f:
                f.write(self.run_dir)

    # ------------------------------------------------------------------
    def _write(self) -> None:
        path = os.path.join(self._abs, "steps.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)


def load(run_dir: str) -> dict:
    run_dir = (run_dir or "").strip()
    with open(os.path.join(_abs(run_dir), "steps.json"), encoding="utf-8") as f:
        return json.load(f)


def last_run_dir() -> str:
    with open(os.path.join(ROOT, contracts.LAST_RUN_FILE), encoding="utf-8") as f:
        return f.read().strip()
