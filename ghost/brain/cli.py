"""Headless brain test:  python -m ghost.brain.cli "open notepad and type hello"

Prints every overlay event and auto-acks GhostActions. Wrap real-screen runs in
tools/screenlock.py when other agents share the desktop."""
import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import logging  # noqa: E402
import sys  # noqa: E402
import threading  # noqa: E402

from dotenv import find_dotenv, load_dotenv  # noqa: E402

load_dotenv(find_dotenv(usecwd=True))

from ghost.contracts import GhostAction, to_overlay  # noqa: E402


def _drain() -> None:
    while True:
        ev = to_overlay.get()
        if isinstance(ev, GhostAction):
            d = {k: v for k, v in vars(ev).items() if k != "ack" and v not in ("", [], 0) or k in ("x", "y")}
            print(f"[overlay] GhostAction {d}", flush=True)
            ev.ack.set()
        elif type(ev).__name__ != "MicLevel":
            print(f"[overlay] {ev}", flush=True)


def main() -> int:
    task = " ".join(sys.argv[1:]).strip()
    if not task:
        print('usage: python -m ghost.brain.cli "task"')
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    threading.Thread(target=_drain, daemon=True).start()
    from ghost.brain.agent import run_task
    run_task(task)
    import time
    time.sleep(0.5)          # let the drain thread print the tail
    return 0


if __name__ == "__main__":
    sys.exit(main())
