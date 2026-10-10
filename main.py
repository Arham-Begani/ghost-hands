"""Ghost Hands entry point.

    python main.py            real brain if it imports, else the scripted fake brain
    python main.py --fake     force the fake brain (loops the scripted demo)
    python main.py --selftest out_dir   prove capture exclusion + click-through, then exit
"""
from __future__ import annotations

import ctypes
import logging
import os
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
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    overlay = Overlay(to_overlay, to_brain)
    overlay.show()

    if kind == "real":
        try:
            start_brain()
        except TypeError:
            start_brain(to_brain, to_overlay)
    else:
        start_brain()

    # 5. hotkeys (lands with the next milestone)
    try:
        from ghost.overlay.hotkeys import start_hotkeys
        start_hotkeys(overlay)
    except ImportError:
        log.info("hotkeys: not built yet")

    if selftest:
        from ghost.overlay.selftest import run_selftest
        run_selftest(overlay, selftest)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
