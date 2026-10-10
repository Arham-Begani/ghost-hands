"""Ghost Hands entry point.

    python main.py            real brain if it imports, else the scripted fake brain
    python main.py --fake     force the fake brain (loops the scripted demo)
    python main.py --selftest out_dir   prove capture exclusion + click-through, then exit
    python main.py --task "open notepad and type hi"   submit a task as if typed in the F7 box
"""
from __future__ import annotations

import ctypes
import logging
import os
import signal
import sys

# 1. DPI awareness before anything touches the screen (Qt, mss, pyautogui).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)        # per-monitor aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
# Qt then draws in physical pixels, matching mss / pyautogui coordinates 1:1.
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
os.environ.setdefault("QT_LOGGING_RULES", "qt.qpa.window=false")

sys.setswitchinterval(0.001)       # keep the Qt thread responsive while brain/voice threads work
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("ghost.main")

# 2. .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    log.warning("python-dotenv not installed; skipping .env")

from ghost.contracts import to_brain, to_overlay  # noqa: E402


def pick_brain(force_fake: bool):
    """3. Real brain if it imports, else the fake one."""
    if not force_fake and os.getenv("GH_FAKE") != "1":
        try:
            from ghost.brain.worker import start_brain_thread
            log.info("brain: ghost.brain.worker")
            return start_brain_thread, "real"
        except Exception as e:
            log.warning("brain: real worker unavailable (%s: %s); using fake_brain", type(e).__name__, e)
    from ghost.overlay.fake_brain import start_fake_brain
    return start_fake_brain, "fake"


def main() -> int:
    args = sys.argv[1:]
    selftest = None
    if "--selftest" in args:
        i = args.index("--selftest")
        selftest = args[i + 1] if i + 1 < len(args) else "runs/selftest"
    start_brain, kind = pick_brain("--fake" in args or selftest is not None)

    # 4. Qt + overlay
    from PyQt6.QtWidgets import QApplication

    from ghost.overlay.app import Overlay
    # Hang watchdog: a C thread (works even if the GIL is stuck) dumps every
    # thread's stack to runs/hang.log if the Qt loop stalls for > 6 s.
    import faulthandler
    os.makedirs("runs", exist_ok=True)
    hang_log = open("runs/hang.log", "a", buffering=1)
    faulthandler.enable(file=hang_log, all_threads=True)    # native crashes too

    def pet() -> None:
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(6, repeat=False, file=hang_log)
    signal.signal(signal.SIGINT, signal.SIG_DFL)          # Ctrl+C kills the overlay instantly
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    overlay = Overlay(to_overlay, to_brain)
    overlay.show()
    from PyQt6.QtCore import QTimer as _QTimer
    _pet = _QTimer(overlay)
    _pet.timeout.connect(pet)
    _pet.start(1000)

    if kind == "real":
        # Importing openai takes ~5 s here: warm the brain + voice off the Qt thread
        # so the overlay is alive from the first frame and the first F8 is fast.
        import threading

        def boot() -> None:
            try:
                start_brain()
                from ghost.brain import voice  # noqa: F401
            except Exception:
                log.exception("brain failed to start")
        threading.Thread(target=boot, name="brain-boot", daemon=True).start()
    else:
        start_brain()

    # 5. hotkeys (lands with the next milestone)
    try:
        from ghost.overlay.hotkeys import start_hotkeys
        start_hotkeys(overlay)
    except ImportError:
        log.info("hotkeys: not built yet")

    if "--task" in args:            # scripted run, same path as the F7 box (for testing)
        i = args.index("--task")
        text = args[i + 1] if i + 1 < len(args) else ""
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2000, lambda: overlay.submit_task(text))
    if "--replay" in args:          # same as pressing F10
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(2500, lambda: overlay.commands.put("f10"))
    if selftest:
        from ghost.overlay.selftest import run_selftest
        run_selftest(overlay, selftest)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
