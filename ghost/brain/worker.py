"""Brain worker thread: drains contracts.to_brain and dispatches.

Runs (task / replay / transcription) execute on their own thread so this loop keeps
draining to_brain and Abort is instant."""
from __future__ import annotations

import logging
import threading
import time

from ghost.contracts import (Abort, Error, ReplayRun, RunTask, StartListening, Status, StopListening,
                             TaskHeard, to_brain, to_overlay)

log = logging.getLogger("ghost.brain")

SPLASH_S = 2.2                      # overlay TaskHeard splash; first GhostAction waits this long
_busy = threading.Event()           # a run / replay / transcription is in progress
_listening = threading.Event()


def _emit(ev) -> None:
    to_overlay.put(ev)


def _spawn(fn, *args) -> None:
    def body():
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - never kill the brain
            log.exception("run thread failed")
            _emit(Error("Something went wrong. Try again."))
            _emit(Status("idle"))
        finally:
            _busy.clear()
    _busy.set()
    threading.Thread(target=body, name="ghost-run", daemon=True).start()


def _run_task(text: str) -> None:
    from ghost.brain.agent import run_task
    _emit(TaskHeard(text))
    run_task(text, not_before=time.time() + SPLASH_S)


def _transcribe_then_run() -> None:
    try:
        from ghost.brain import voice
    except Exception as e:
        log.warning("voice missing: %s", e)
        _emit(Error("Voice isn't available. Press F7 to type the task."))
        _emit(Status("idle"))
        return
    _emit(Status("transcribing", "Transcribing"))
    try:
        text = (voice.stop_and_transcribe() or "").strip()
    except Exception as e:
        log.exception("transcribe failed: %s", e)
        text = ""
    if not text:
        _emit(Error("Didn't catch that. Hold F8 and try again."))
        _emit(Status("idle"))
        return
    log.info("heard: %r", text)
    _run_task(text)


def _replay(run_dir: str) -> None:
    try:
        from ghost.brain import replay
    except Exception as e:
        log.warning("replay missing: %s", e)
        _emit(Error("Replay isn't available yet."))
        _emit(Status("idle"))
        return
    try:
        from ghost.brain import guard
        guard.ABORT.clear()
    except Exception:
        pass
    replay.replay(run_dir)


def _abort() -> None:
    try:
        from ghost.brain import guard
        guard.ABORT.set()
    except Exception as e:
        log.warning("guard missing on abort: %s", e)
    try:
        from ghost.brain import voice
        voice.stop_speaking()
    except Exception:
        pass
    if _busy.is_set():
        _emit(Status("aborted", "You've got it"))


def _handle(ev) -> None:
    if isinstance(ev, Abort):
        _abort()
    elif isinstance(ev, StartListening):
        if _busy.is_set() or _listening.is_set():
            return
        try:
            from ghost.brain import voice
            voice.start_recording()
            _listening.set()
            _emit(Status("listening", "Listening"))
        except Exception as e:
            log.warning("start_recording failed: %s", e)
            _emit(Error("Mic isn't available. Press F7 to type the task."))
            _emit(Status("idle"))
    elif isinstance(ev, StopListening):
        if not _listening.is_set():
            return
        _listening.clear()
        if _busy.is_set():
            return
        _spawn(_transcribe_then_run)
    elif isinstance(ev, RunTask):
        text = (ev.text or "").strip()
        if text and not _busy.is_set():
            _spawn(_run_task, text)
    elif isinstance(ev, ReplayRun):
        if not _busy.is_set():
            _spawn(_replay, (ev.run_dir or "").strip())
    else:
        log.info("unknown brain event: %r", ev)


def _loop() -> None:
    while True:
        ev = to_brain.get()
        try:
            _handle(ev)
        except Exception:  # noqa: BLE001
            log.exception("worker dispatch failed for %r", ev)


def start_brain_thread() -> threading.Thread:
    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except Exception:
        pass
    import ghost.brain.agent  # noqa: F401  (sets up runs/brain.log)
    t = threading.Thread(target=_loop, name="ghost-brain", daemon=True)
    t.start()
    log.info("brain worker started")
    return t
