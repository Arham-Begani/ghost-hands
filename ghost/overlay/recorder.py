"""Demo recorder: `GH_RECORD=out.mp4 python main.py ...`

The overlay is normally excluded from capture (the AI must never see it), which
also hides it from screen recorders. In recording mode the overlay stays
visible to capture, and the brain's screenshots switch the exclusion on just for
the instant they grab (screen.before_grab / after_grab), so the AI still never
sees the ghost. ffmpeg records the desktop (gdigrab) and system audio (Stereo
Mix, i.e. the ghost's voice) in its own process, costing the overlay nothing.
If system audio can't be opened, the spoken clips are mixed in afterwards from
a timestamp log."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time

log = logging.getLogger("ghost.recorder")
FPS = 30
NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _stereo_mix() -> str | None:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
                       capture_output=True, text=True, creationflags=NOWIN)
    for line in r.stderr.splitlines():
        if "(audio)" in line and ("Stereo Mix" in line or "What U Hear" in line):
            return line.split('"')[1]
    return None


class Recorder:
    def __init__(self, ov, out_path: str) -> None:
        self.ov = ov
        self.out = os.path.abspath(out_path)
        self.tmp = tempfile.mkdtemp(prefix="ghost_rec_")
        self.audio: list[tuple[float, str]] = []
        self._hook_capture()
        dev = None if os.getenv("GH_RECORD_NO_SYSAUDIO") == "1" else _stereo_mix()
        self.has_audio = dev is not None
        self.video = self.out if self.has_audio else os.path.join(self.tmp, "video.mp4")
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
               "-f", "gdigrab", "-framerate", str(FPS), "-draw_mouse", "1", "-i", "desktop"]
        if dev:
            cmd += ["-f", "dshow", "-i", f"audio={dev}"]
        cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p"]
        if dev:
            cmd += ["-c:a", "aac", "-b:a", "160k"]
        cmd += [self.video]
        self.ff = subprocess.Popen(cmd, stdin=subprocess.PIPE, creationflags=NOWIN)
        self.t0 = time.perf_counter()
        # (voice is hooked by main's brain-boot thread once it has imported voice:
        #  importing the openai/pydantic stack from a second thread can crash natively)
        log.info("recording to %s (audio: %s)", self.out, dev or "mixed from voice log")

    def _hook_capture(self) -> None:
        ov = self.ov
        ov.set_capture_excluded(False)           # let the recorder see the ghost
        from ghost.brain import screen

        def before() -> None:
            ov.set_capture_excluded(True)
            time.sleep(0.07)                     # DWM applies affinity on its next frame

        def after() -> None:
            ov.set_capture_excluded(False)
        screen.before_grab, screen.after_grab = before, after

    def hook_voice(self) -> None:
        import sys
        voice = sys.modules.get("ghost.brain.voice")
        if voice is None or self.has_audio:     # Stereo Mix already records the voice
            return
        orig = voice.speak
        rec = self

        def speak(text, cache_path=None, *a, **kw):
            if cache_path is None:
                cache_path = os.path.join(rec.tmp, f"say{len(rec.audio):03d}.mp3")
            rec.audio.append((time.perf_counter() - rec.t0, cache_path))
            return orig(text, cache_path, *a, **kw)
        voice.speak = speak

    def stop(self) -> str:
        if self.ff.poll() is None:
            try:
                self.ff.stdin.write(b"q")
                self.ff.stdin.flush()
            except OSError:
                pass
            try:
                self.ff.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.ff.kill()
        self.ov.set_capture_excluded(True)
        if not self.has_audio:
            time.sleep(1.0)
            self._mux()
        log.info("recording saved: %s", self.out)
        return self.out

    def _mux(self) -> None:
        clips = []
        for i, (t, path) in enumerate(self.audio):
            base = os.path.splitext(path)[0]
            p = next((c for c in (path, base + ".wav", base + ".mp3") if os.path.exists(c)), None)
            if p:
                nxt = self.audio[i + 1][0] if i + 1 < len(self.audio) else None
                clips.append((t, p, (nxt - t) if nxt else None))
        if not clips:
            os.replace(self.video, self.out)
            return
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", self.video]
        parts = []
        for k, (t, p, dur) in enumerate(clips, 1):
            cmd += ["-i", p]
            trim = f"atrim=0:{dur:.3f}," if dur else ""
            parts.append(f"[{k}:a]{trim}adelay={int(t * 1000)}:all=1,aresample=48000[a{k}]")
        mix = "".join(f"[a{k}]" for k in range(1, len(clips) + 1))
        fc = ";".join(parts) + f";{mix}amix=inputs={len(clips)}:normalize=0:dropout_transition=0[aout]"
        cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[aout]", "-c:v", "copy", "-c:a", "aac",
                "-b:a", "160k", self.out]
        r = subprocess.run(cmd, capture_output=True, text=True, creationflags=NOWIN)
        if r.returncode != 0:
            log.warning("audio mux failed (%s); saving silent video", r.stderr[-400:])
            os.replace(self.video, self.out)


def enable_recording_mode(ov) -> None:
    """GH_RECORD_MODE=1: keep the overlay visible to an EXTERNAL screen recorder
    (ffmpeg / OBS) while still hiding it from the brain's screenshots."""
    ov.set_capture_excluded(False)
    from ghost.brain import screen

    def before() -> None:
        ov.set_capture_excluded(True)
        time.sleep(0.07)                     # DWM applies affinity on its next frame

    def after() -> None:
        ov.set_capture_excluded(False)
    screen.before_grab, screen.after_grab = before, after
    log.info("recording mode: overlay visible to screen recorders, hidden from the AI's screenshots")
