"""
Ghost Hands - voice: push-to-talk recording, OpenAI speech-to-text, OpenAI TTS.

Public interface (called by worker / agent / replay):
    start_recording() -> None
    stop_and_transcribe() -> str
    transcribe_file(path) -> str
    speak(text, cache_path=None) -> None      # non-blocking, newest wins
    stop_speaking() -> None

Models (confirmed on developers.openai.com, see OPENAI_NOTES.md "## Voice"):
    STT  gpt-transcribe   (prompt + keywords/languages hints)
    TTS  gpt-4o-mini-tts  voice "marin", steered with `instructions`
Env overrides: GH_STT_MODEL, GH_TTS_MODEL, GH_TTS_VOICE, GH_TTS_INSTRUCTIONS.
GH_VOICE=0 makes speak() a no-op (silent rehearsals).

Nothing in here raises into the caller; failures are logged.
"""
from __future__ import annotations

import io
import logging
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np

from ghost import contracts

log = logging.getLogger("ghost.voice")

try:  # the caller normally loads .env already; this is harmless if it did
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:  # pragma: no cover
    pass

try:
    import sounddevice as sd
except Exception as e:  # pragma: no cover
    sd = None
    log.error("sounddevice unavailable: %s", e)

try:
    import soundfile as sf
except Exception as e:  # pragma: no cover
    sf = None
    log.error("soundfile unavailable: %s", e)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------
DEFAULT_STT_MODEL = "gpt-transcribe"
FALLBACK_STT_MODEL = "gpt-4o-transcribe"
DEFAULT_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_TTS_VOICE = "marin"
DEFAULT_TTS_INSTRUCTIONS = (
    "Voice: a calm, friendly teacher sitting beside the learner. "
    "Tone: warm, reassuring, unhurried, gently encouraging. "
    "Pacing: relaxed and clear, with a small natural pause between phrases. "
    "Never sound rushed, robotic or overly excited."
)
STT_PROMPT = (
    "A task for a computer, e.g. freeze the top row, make a bar chart of sales "
    "by month, turn on dark mode, Google Sheets"
)
STT_KEYWORDS = ["freeze the top row", "Google Sheets", "bar chart", "dark mode"]

REC_RATE = 16000            # Hz, mono
LEVEL_HZ = 30               # MicLevel events per second
MIN_SECONDS = 0.3
SILENCE_DB = -58.0          # loudest block quieter than this => treat as silence
TTS_RATE = 24000            # OpenAI pcm: 24 kHz, 16-bit signed LE, mono
PREBUFFER_S = 0.12          # buffer this much streamed audio before starting playback
WRITE_CHUNK = 1200          # frames per write (50 ms) -> stop latency


def _stt_model() -> str:
    return os.getenv("GH_STT_MODEL") or DEFAULT_STT_MODEL


def _tts_model() -> str:
    return os.getenv("GH_TTS_MODEL") or DEFAULT_TTS_MODEL


def _tts_voice() -> str:
    return os.getenv("GH_TTS_VOICE") or DEFAULT_TTS_VOICE


def _voice_enabled() -> bool:
    return os.getenv("GH_VOICE", "1").strip() != "0"


_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    with _client_lock:
        if _client is None:
            from openai import OpenAI

            _client = OpenAI(timeout=20.0, max_retries=1)
        return _client


def _prewarm() -> None:
    # `import openai` takes seconds on a cold laptop; do it off the hot path.
    try:
        if os.getenv("OPENAI_API_KEY"):
            _get_client()
        else:
            import openai  # noqa: F401
    except Exception as e:
        log.warning("voice prewarm: %s", e)


threading.Thread(target=_prewarm, name="ghost-voice-prewarm", daemon=True).start()


# --------------------------------------------------------------------------
# recording
# --------------------------------------------------------------------------
_rec_lock = threading.Lock()
_rec_stream = None
_rec_chunks: list[np.ndarray] = []
_rec_peak_db = -120.0
_rec_level = 0.0


def _db_to_level(db: float) -> float:
    # -55 dBFS (room) -> 0.0 ; -10 dBFS (loud) -> 1.0 ; speech ~ -35..-15 -> 0.45..0.9
    return float(min(1.0, max(0.0, (db + 55.0) / 45.0)))


def _rec_callback(indata, frames, time_info, status):  # PortAudio thread
    global _rec_peak_db, _rec_level
    try:
        mono = indata[:, 0].copy()
        _rec_chunks.append(mono)
        rms = float(np.sqrt(np.mean(mono * mono))) if mono.size else 0.0
        db = 20.0 * np.log10(max(rms, 1e-6))
        if db > _rec_peak_db:
            _rec_peak_db = db
        target = _db_to_level(db)
        k = 0.55 if target > _rec_level else 0.18   # fast attack, soft release
        _rec_level += k * (target - _rec_level)
        contracts.to_overlay.put_nowait(contracts.MicLevel(level=round(_rec_level, 3)))
    except Exception:
        pass


def start_recording() -> None:
    """Start capturing the default microphone into memory (16 kHz mono)."""
    global _rec_stream, _rec_chunks, _rec_peak_db, _rec_level
    try:
        with _rec_lock:
            if _rec_stream is not None:
                _close_rec_stream()
            _rec_chunks = []
            _rec_peak_db = -120.0
            _rec_level = 0.0
            if sd is None:
                log.error("start_recording: sounddevice missing")
                return
            stream = sd.InputStream(
                samplerate=REC_RATE,
                channels=1,
                dtype="float32",
                blocksize=REC_RATE // LEVEL_HZ,
                callback=_rec_callback,
            )
            stream.start()
            _rec_stream = stream
            log.info("recording started")
    except Exception as e:
        log.error("start_recording failed: %s", e)
        _rec_stream = None


def _close_rec_stream() -> None:
    global _rec_stream
    s = _rec_stream
    _rec_stream = None
    if s is not None:
        try:
            s.stop()
        except Exception:
            pass
        try:
            s.close()
        except Exception:
            pass


def stop_and_transcribe() -> str:
    """Stop recording and return the transcribed task ("" on silence/failure)."""
    try:
        with _rec_lock:
            was_recording = _rec_stream is not None
            _close_rec_stream()
            chunks = list(_rec_chunks)
            peak_db = _rec_peak_db
        try:
            contracts.to_overlay.put_nowait(contracts.MicLevel(level=0.0))
        except Exception:
            pass
        if not was_recording or not chunks:
            return ""
        audio = np.concatenate(chunks)
        seconds = audio.size / REC_RATE
        if seconds < MIN_SECONDS:
            log.info("recording too short (%.2f s)", seconds)
            return ""
        if peak_db < SILENCE_DB:
            log.info("recording is silence (peak %.1f dBFS)", peak_db)
            return ""
        buf = io.BytesIO()
        sf.write(buf, audio, REC_RATE, format="WAV", subtype="PCM_16")
        log.info("transcribing %.2f s (peak %.1f dBFS)", seconds, peak_db)
        return _transcribe_bytes(buf.getvalue(), "speech.wav")
    except Exception as e:
        log.error("stop_and_transcribe failed: %s", e)
        return ""


def transcribe_file(path) -> str:
    """Transcribe an audio file (wav/mp3/...) via the same path as push-to-talk."""
    try:
        p = Path(path)
        return _transcribe_bytes(p.read_bytes(), p.name)
    except Exception as e:
        log.error("transcribe_file failed: %s", e)
        return ""


def _err(e: Exception) -> str:
    """Exception text safe to log: never includes (even masked) API keys."""
    import re

    msg = re.sub(r"sk-[A-Za-z0-9_\-*]+", "sk-***", str(e))
    return f"{type(e).__name__}: {msg[:200]}"


def _clean(text: str) -> str:
    t = (text or "").strip().strip('"').strip()
    while t.endswith((".", "。")):
        t = t[:-1].rstrip()
    return t


def _transcribe_bytes(data: bytes, name: str) -> str:
    model = _stt_model()
    attempts = [model] if model == FALLBACK_STT_MODEL else [model, FALLBACK_STT_MODEL]
    for m in attempts:
        try:
            kwargs = dict(model=m, file=(name, data), prompt=STT_PROMPT)
            if m.startswith("gpt-transcribe"):
                kwargs["extra_body"] = {"keywords": STT_KEYWORDS, "languages": ["en"]}
            elif "diarize" in m:
                kwargs.pop("prompt")
            t0 = time.perf_counter()
            r = _get_client().audio.transcriptions.create(**kwargs)
            text = _clean(getattr(r, "text", "") if not isinstance(r, str) else r)
            log.info("STT %s %.2fs -> %r", m, time.perf_counter() - t0, text)
            return text
        except Exception as e:
            log.error("STT with %s failed: %s", m, _err(e))
    return ""


# --------------------------------------------------------------------------
# speaking
# --------------------------------------------------------------------------
_speak_lock = threading.Lock()      # guards _gen / _cancel
_play_lock = threading.Lock()       # only one audio device stream at a time
_gen = 0
_cancel = threading.Event()         # set => current utterance must stop


def speak(text: str, cache_path=None) -> None:
    """Non-blocking. Newest call wins: stops whatever is currently speaking."""
    global _gen, _cancel
    try:
        if not _voice_enabled():
            return
        text = (text or "").strip()
        with _speak_lock:
            _gen += 1
            gen = _gen
            _cancel.set()
            _cancel = threading.Event()
            cancel = _cancel
        if not text:
            return
        threading.Thread(
            target=_speak_worker, args=(gen, text, cache_path, cancel),
            name=f"ghost-speak-{gen}", daemon=True,
        ).start()
    except Exception as e:
        log.error("speak failed: %s", e)


def stop_speaking() -> None:
    global _gen
    try:
        with _speak_lock:
            _gen += 1
            _cancel.set()
        _Fallback.interrupt()
    except Exception as e:
        log.error("stop_speaking failed: %s", e)


def _is_current(gen: int) -> bool:
    return gen == _gen


def _cached_file(cache_path) -> Path | None:
    if not cache_path:
        return None
    p = Path(cache_path)
    for cand in (p, p.with_suffix(".wav"), p.with_suffix(".mp3")):
        try:
            if cand.is_file() and cand.stat().st_size > 0:
                return cand
        except Exception:
            pass
    return None


def _speak_worker(gen: int, text: str, cache_path, cancel: threading.Event) -> None:
    try:
        cached = _cached_file(cache_path)
        if cached is not None:
            data, sr = sf.read(str(cached), dtype="int16", always_2d=True)
            _play_array(data[:, 0], sr, gen, cancel)
            return
        if _tts_stream_and_play(gen, text, cache_path, cancel):
            return
    except Exception as e:
        log.error("TTS playback failed: %s", _err(e))
    if _is_current(gen) and not cancel.is_set():
        _Fallback.say(gen, text)


def _open_out(sr: int):
    return sd.RawOutputStream(samplerate=sr, channels=1, dtype="int16", latency="low")


def _play_array(samples: np.ndarray, sr: int, gen: int, cancel: threading.Event) -> None:
    with _play_lock:
        if cancel.is_set() or not _is_current(gen):
            return
        stream = _open_out(sr)
        stream.start()
        try:
            for i in range(0, samples.size, WRITE_CHUNK):
                if cancel.is_set():
                    stream.abort()
                    return
                stream.write(np.ascontiguousarray(samples[i:i + WRITE_CHUNK]).tobytes())
            stream.stop()   # drains the buffer
        finally:
            stream.close()


def _tts_kwargs(text: str) -> dict:
    model = _tts_model()
    kw = dict(model=model, voice=_tts_voice(), input=text, response_format="pcm")
    if not model.startswith("tts-1"):
        kw["instructions"] = os.getenv("GH_TTS_INSTRUCTIONS") or DEFAULT_TTS_INSTRUCTIONS
    return kw


# last measured latency (seconds from request to first audio byte), for diagnostics
last_tts_first_byte_s: float | None = None


def _tts_stream_and_play(gen: int, text: str, cache_path, cancel: threading.Event) -> bool:
    """Stream pcm from OpenAI TTS straight to the speakers; cache the whole clip.
    Returns True if audio was obtained (played or cancelled), False to fall back."""
    global last_tts_first_byte_s
    client = _get_client()
    t0 = time.perf_counter()
    pcm = bytearray()
    played = 0                      # bytes already written to the device
    stream = None
    prebuffer = int(PREBUFFER_S * TTS_RATE) * 2
    acquired = False
    underflows = 0
    try:
        with client.audio.speech.with_streaming_response.create(**_tts_kwargs(text)) as resp:
            for chunk in resp.iter_bytes(4800):
                if not chunk:
                    continue
                if not pcm:
                    last_tts_first_byte_s = time.perf_counter() - t0
                    log.info("TTS first byte %.2fs", last_tts_first_byte_s)
                pcm.extend(chunk)
                if cancel.is_set() or not _is_current(gen):
                    cancel.set()
                    if stream is not None:
                        try:
                            stream.abort()
                            stream.close()
                        except Exception:
                            pass
                        stream = None
                    if acquired:            # free the device for the newer caption now
                        _play_lock.release()
                        acquired = False
                    if not cache_path:
                        return True
                    continue            # keep downloading so the cache is complete
                if stream is None:
                    if len(pcm) < prebuffer:
                        continue
                    if not acquired:
                        _play_lock.acquire()
                        acquired = True
                    if cancel.is_set() or not _is_current(gen):
                        continue
                    stream = _open_out(TTS_RATE)
                    stream.start()
                end = len(pcm) - (len(pcm) % 2)
                if end > played:
                    if stream.write(bytes(pcm[played:end])):
                        underflows += 1
                    played = end
        if not pcm:
            return False
        _save_cache(cache_path, pcm)
        if cancel.is_set():
            return True
        if stream is None:          # short clip, never passed the prebuffer
            if not acquired:
                _play_lock.acquire()
                acquired = True
            if cancel.is_set() or not _is_current(gen):
                return True
            stream = _open_out(TTS_RATE)
            stream.start()
        end = len(pcm) - (len(pcm) % 2)
        rest = bytes(pcm[played:end])
        for i in range(0, len(rest), WRITE_CHUNK * 2):
            if cancel.is_set():
                stream.abort()
                return True
            if stream.write(rest[i:i + WRITE_CHUNK * 2]):
                underflows += 1
        stream.stop()
        if underflows:
            log.info("TTS playback underflows: %d", underflows)
        return True
    except Exception as e:
        log.error("OpenAI TTS failed: %s", _err(e))
        return bool(pcm) and cancel.is_set()
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                pass
        if acquired:
            _play_lock.release()


def _save_cache(cache_path, pcm: bytearray) -> None:
    if not cache_path:
        return
    try:
        p = Path(cache_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        samples = np.frombuffer(bytes(pcm[: len(pcm) - (len(pcm) % 2)]), dtype=np.int16)
        tmp = p.with_name(p.stem + ".part" + p.suffix)
        try:
            fmt = (p.suffix[1:] or "wav").upper()
            sf.write(str(tmp), samples, TTS_RATE, format=fmt)
            os.replace(tmp, p)
        except Exception:
            w = p.with_suffix(".wav")
            tmp = w.with_name(w.stem + ".part.wav")
            sf.write(str(tmp), samples, TTS_RATE, format="WAV", subtype="PCM_16")
            os.replace(tmp, w)
    except Exception as e:
        log.error("TTS cache save failed: %s", e)


class _Fallback:
    """pyttsx3 (SAPI5) on one dedicated thread. Only the newest request is spoken."""
    _q: "queue.Queue[tuple[int, str]]" = queue.Queue()
    _thread: threading.Thread | None = None
    _lock = threading.Lock()

    @classmethod
    def say(cls, gen: int, text: str) -> None:
        with cls._lock:
            if cls._thread is None or not cls._thread.is_alive():
                cls._thread = threading.Thread(target=cls._run, name="ghost-pyttsx3", daemon=True)
                cls._thread.start()
        cls._q.put((gen, text))

    @classmethod
    def interrupt(cls) -> None:
        pass  # the speaking thread polls _gen on every word and stops itself

    @classmethod
    def _run(cls) -> None:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            engine.setProperty("rate", 165)
        except Exception as e:
            log.error("pyttsx3 unavailable: %s", e)
            return
        state = {"gen": 0}

        def on_word(name, location, length):
            if not _is_current(state["gen"]):
                try:
                    engine.stop()
                except Exception:
                    pass

        try:
            engine.connect("started-word", on_word)
        except Exception:
            pass
        while True:
            gen, text = cls._q.get()
            while not cls._q.empty():          # skip stale captions
                gen, text = cls._q.get_nowait()
            if not _is_current(gen):
                continue
            state["gen"] = gen
            try:
                log.info("pyttsx3 fallback: %r", text)
                engine.say(text)
                engine.runAndWait()
            except Exception as e:
                log.error("pyttsx3 failed: %s", e)
