"""Serialise real-screen tests between parallel agents.

    python tools/screenlock.py -- python -m ghost.brain.cli "open notepad ..."

Waits (up to 10 min) for runs/.screen_lock, runs the command, releases."""
import os
import subprocess
import sys
import time

LOCK = os.path.join(os.path.dirname(__file__), "..", "runs", ".screen_lock")


def main() -> int:
    cmd = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    os.makedirs(os.path.dirname(LOCK), exist_ok=True)
    t0 = time.time()
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {' '.join(cmd)}".encode())
            os.close(fd)
            break
        except FileExistsError:
            if time.time() - os.path.getmtime(LOCK) > 300:     # stale lock
                os.remove(LOCK)
                continue
            if time.time() - t0 > 600:
                print("screenlock: timed out waiting", file=sys.stderr)
                return 2
            time.sleep(1)
    try:
        return subprocess.call(cmd)
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
