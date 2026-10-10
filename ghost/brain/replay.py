"""Stage fallback: re-run a logged run with NO model calls.

replay(run_dir) -> None. Same events, pacing, guard and TTS as a live run.
Never raises.
"""
from __future__ import annotations

import logging
import os
import time

from ghost import contracts
from ghost.brain import executor, guard, runlog

log = logging.getLogger(__name__)

_POINTER = {"click", "double_click", "right_click", "drag"}


def _put(ev) -> None:
    contracts.to_overlay.put(ev)


def _sleep(s: float) -> None:
    end = time.monotonic() + s
    while time.monotonic() < end:
        if guard.ABORT.is_set():
            raise guard.Aborted()
        time.sleep(0.03)


def _speak(caption: str, cache_path: str) -> None:
    if not caption:
        return
    try:
        from ghost.brain import voice
    except Exception:
        return
    try:
        try:
            voice.speak(caption, cache_path=cache_path)
        except TypeError:
            voice.speak(caption)
    except Exception as e:                       # noqa: BLE001
        log.warning("replay speak failed: %s", e)


def replay(run_dir: str) -> None:
    run_dir = (run_dir or "").strip()
    try:
        _replay(run_dir)
    except guard.Aborted:
        log.info("replay aborted")
    except Exception as e:                       # noqa: BLE001
        log.exception("replay failed")
        _put(contracts.Error(f"Replay failed: {e}"))
        _put(contracts.Status("idle"))
    finally:
        guard.disarm()


def _replay(run_dir: str) -> None:
    guard.ABORT.clear()
    data = runlog.load(run_dir)
    abs_dir = runlog._abs(run_dir)
    steps = data.get("steps", [])
    total = sum(1 for s in steps if s.get("kind") != "handover")
    task = data.get("task", "")
    summary = data.get("summary") or f"Replayed: {task}"
    pace = float(os.getenv("GH_TEACH_PACE", "1.0") or 1.0)

    _put(contracts.Status("replay", "Replay", 0, total))
    _put(contracts.TaskHeard(task))
    guard.arm()
    _sleep(2.2)

    i = 0
    for s in steps:
        guard.raise_if_aborted()
        kind = s.get("kind", "")
        x, y = int(s.get("x", 0)), int(s.get("y", 0))
        caption = s.get("caption", "")
        if kind == "handover":
            _put(contracts.HandOver(x, y, caption))
            _put(contracts.RunFinished(run_dir, False, summary))
            return
        i += 1
        target = s.get("target_name", "")
        if kind in _POINTER:
            ok, name = guard.check(x, y)
            if not ok:
                reason = f'Stopped before "{name}". That one is yours.'
                _put(contracts.HandOver(x, y, reason))
                _put(contracts.RunFinished(run_dir, False, reason))
                return
        _put(contracts.Status("acting", "", i, total))
        ev = contracts.GhostAction(
            kind=kind, x=x, y=y, caption=caption, target_name=target,
            text=s.get("text", ""), keys=list(s.get("keys") or []),
            end_x=int(s.get("end_x", 0)), end_y=int(s.get("end_y", 0)),
            scroll_y=int(s.get("scroll_y", 0)), step=i,
        )
        _put(ev)
        ev.ack.wait(3.0)
        guard.raise_if_aborted()
        audio = s.get("audio") or f"step{int(s.get('index', i)):02d}.mp3"
        _speak(caption, os.path.join(abs_dir, audio))
        executor.do(kind, x, y, text=ev.text, keys=ev.keys, end_x=ev.end_x, end_y=ev.end_y,
                    scroll_y=ev.scroll_y)
        _sleep(pace)

    _put(contracts.RunFinished(run_dir, bool(data.get("ok", True)), summary))
