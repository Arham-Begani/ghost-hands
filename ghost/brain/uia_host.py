"""UI Automation helper process (crash isolation for guard.py).

    python -m ghost.brain.uia_host

uiautomation/comtypes can segfault inside some apps (UWP Settings). Running it
here means a native crash only kills this helper; guard.py respawns it.

Protocol: JSON lines. stdin  {"id": n, "op": "check"|"focused_name"|"focused_point"|"ping", "x":.., "y":..}
                     stdout {"id": n, "ok": true, "r": ...} | {"id": n, "ok": false, "e": "..."}
On start-up it writes {"id": 0, "ready": true}. Exits when stdin closes.
Nothing but protocol lines ever reaches the real stdout (fd 1 is re-pointed at stderr).
"""
import ctypes

try:                                       # physical px, same as the parent (main.py)
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

import faulthandler
import json
import logging
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_LOG = os.path.join(_ROOT, "runs", "uia_host.log")


def _proto_out():
    """Keep a private handle on the real stdout; point fd 1 / sys.stdout at stderr
    so stray prints (ours or a library's, Python or C) can't corrupt the protocol."""
    out_fd = os.dup(1)
    try:
        os.dup2(2, 1)
    except OSError:
        pass
    sys.stdout = sys.stderr
    return os.fdopen(out_fd, "wb", buffering=0)


def main() -> int:
    out = _proto_out()
    inp = sys.stdin.buffer
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        logf = open(_LOG, "a", encoding="utf-8")
        logging.basicConfig(stream=logf, level=logging.INFO,
                            format=f"%(asctime)s uia_host[{os.getpid()}] %(levelname)s %(message)s")
        faulthandler.enable(file=logf)     # native crash -> stack in the log
    except Exception:
        logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    logging.getLogger("comtypes").setLevel(logging.WARNING)
    log = logging.getLogger("uia_host")

    def send(obj) -> None:
        out.write((json.dumps(obj, ensure_ascii=True) + "\n").encode("utf-8"))

    from ghost.brain import uia_rules as R
    import uiautomation as auto
    auto.InitializeUIAutomationInCurrentThread()       # never uninitialised
    try:
        auto.GetRootControl()                          # create the singleton here
    except Exception as e:                             # noqa: BLE001
        log.warning("uia init: %s", e)
    log.info("ready")
    send({"id": 0, "ready": True})

    ops = {
        "check": lambda m: list(R._inspect(auto, m["x"], m["y"])),
        "focused_name": lambda m: R._focused_name(auto),
        "focused_point": lambda m: (lambda p: list(p) if p else None)(R._focused_point(auto)),
        "ping": lambda m: True,
    }
    while True:
        raw = inp.readline()
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        rid = None
        try:
            m = json.loads(raw.decode("utf-8"))
            rid = m.get("id")
            r = ops[m["op"]](m)
            send({"id": rid, "ok": True, "r": r})
        except (BrokenPipeError, OSError):
            break
        except Exception as e:                         # noqa: BLE001
            log.warning("op failed: %r -> %s", raw[:200], e)
            try:
                send({"id": rid, "ok": False, "e": f"{type(e).__name__}: {e}"})
            except OSError:
                break
    log.info("stdin closed, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
