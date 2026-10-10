# BUILD.md — Ghost Hands architecture

> **Ghost Hands:** ask how to do anything in any app. A glowing ghost cursor does it on *your* screen, narrating each step at human speed, then hands you the mouse and coaches you through doing it yourself.
>
> Built on **GPT-6.1 Sol + the computer-use tool (Responses API)**, OpenAI speech-to-text and TTS, and built with **Codex**.

## 1. What runs

One Python process on Windows, with two threads and two queues.

```
            ┌───────────────────── main.py (Claude) ─────────────────────┐
            │  1. SetProcessDpiAwareness(2)   2. start brain thread      │
            │  3. QApplication + Overlay (Qt main thread)                │
            └────────────────────────────────────────────────────────────┘
   OVERLAY  (ghost/overlay/*, Claude)              BRAIN  (ghost/brain/*, Codex)
   Qt main thread, 60 fps                          worker thread
   ┌─────────────────────────────┐   to_brain   ┌──────────────────────────────┐
   │ hotkeys F7 F8 F9 F10 Esc    │ ───────────▶ │ voice.py   record + STT + TTS │
   │ ghost cursor + comet trail  │              │ screen.py  mss capture+scale  │
   │ caption bubble, HUD pill    │  to_overlay  │ agent.py   computer-use loop  │
   │ handover / abort / done FX  │ ◀─────────── │ executor.py pyautogui inputs  │
   │ coach.py  "your turn" rings │              │ guard.py   UIA hard-stops +   │
   │ typed-task box (F7)         │              │            mouse-grab abort   │
   └─────────────────────────────┘              │ runlog.py  steps.json + crops │
                                                │ replay.py  re-run a log, no AI│
                                                └──────────────────────────────┘
                    shared, frozen:  ghost/contracts.py
```

## 2. One step, end to end

```
Brain: capture screen ─▶ Responses API (gpt-6.1-sol, tools=[{"type":"computer"}])
     ◀─ computer_call{actions:[...]} + short narration text
for each action:
   scale model coords → physical px
   guard.check(x,y): UIA name under point matches /send|pay|delete|submit|buy|.../ → HandOver, stop
   emit GhostAction(... caption, target_name)  ──▶ Overlay glides cursor (700 ms), shows caption, ripple
   wait ack (≤3 s)  ◀── ack.set()
   runlog.save_crop + record
   executor.do(action)          (guard ignores the cursor moves the brain makes itself)
   sleep TEACH_PACE (1.0 s)     ← "teaches at human speed"
   speak caption (TTS, non-blocking)
capture new screen ─▶ computer_call_output {type: computer_screenshot, image_url: data:image/png;base64,..., detail:"original"}
loop until no computer_call, max 25 turns → RunFinished
```

## 3. File ownership (hard rule: only touch your column)

| Path | Owner | Purpose |
|---|---|---|
| `ghost/contracts.py` | **Arham (frozen)** | events, queues, log schema |
| `ghost/brain/agent.py` | Codex | Responses computer-use loop, narration, turn cap |
| `ghost/brain/screen.py` | Codex | mss capture, downscale to ≤1440 w, scale factor, crops |
| `ghost/brain/executor.py` | Codex | pyautogui click/double/right/drag/move/scroll/type/keypress/wait |
| `ghost/brain/guard.py` | Codex | UIA hard-stop list, mouse-grab abort detector |
| `ghost/brain/voice.py` | Codex | push-to-talk record, STT, TTS (+ pyttsx3 offline fallback) |
| `ghost/brain/runlog.py` | Codex | `runs/<id>/steps.json`, crops, `runs/LAST` |
| `ghost/brain/replay.py` | Codex | re-execute a logged run with no model calls |
| `ghost/brain/worker.py` | Codex | thread: reads `to_brain`, dispatches, emits events |
| `ghost/brain/cli.py` | Codex | headless test: `python -m ghost.brain.cli "freeze the top row"` (auto-acks) |
| `main.py` | Claude | DPI awareness, start brain worker, start Qt |
| `ghost/overlay/app.py` | Claude | full-screen transparent click-through window, event pump |
| `ghost/overlay/cursor.py` | Claude | ghost cursor, comet trail, click ripple, typing chip |
| `ghost/overlay/caption.py` | Claude | caption bubble (typewriter text) |
| `ghost/overlay/hud.py` | Claude | top-centre pill: state, step x / y, task text |
| `ghost/overlay/fx.py` | Claude | TaskHeard splash, HandOver lock, Abort "You've got it", done burst |
| `ghost/overlay/coach.py` | Claude | "Your turn": rings from steps.json, waits for real clicks |
| `ghost/overlay/hotkeys.py` | Claude | pynput global hotkeys |
| `ghost/overlay/taskbox.py` | Claude | F7 typed-task box (restores previous foreground window) |
| `ghost/overlay/fake_brain.py` | Claude | scripted events so the overlay can be built with no API |
| `demo/` | Arham | seeded Google Sheet link, task card, fallback runs |

## 4. Hotkeys

| Key | Action | Event |
|---|---|---|
| **F8 hold** | talk (release to run) | `StartListening` / `StopListening` |
| **F7** | type a task instead (noisy room) | `RunTask(text)` |
| **F9** | Your turn: coach the last run | overlay-internal (reads `runs/LAST`) |
| **F10** | Replay the last run, no AI (fallback) | `ReplayRun(run_dir)` |
| **Esc** | stop everything | `Abort("user")` |
| *grab the mouse* | instant freeze, "You've got it ✋" | brain guard emits `Status("aborted")` |

## 5. Windows gotchas (read before coding)

1. **DPI:** first lines of `main.py`: `ctypes.windll.shcore.SetProcessDpiAwareness(2)`. Otherwise mss, pyautogui and Qt disagree on coordinates and every click misses on a 125% scaled laptop.
2. **Overlay invisible to screenshots:** `ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)` (WDA_EXCLUDEFROMCAPTURE, Windows 10 2004+). The model must never see the ghost cursor or captions. If it fails, brain hides the overlay during capture (fallback, slower).
3. **Click-through:** Qt flags `FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowTransparentForInput`, plus `WA_TranslucentBackground`, `WA_ShowWithoutActivating`, plus ctypes ex-style `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE`. The overlay must never take focus.
4. **pyautogui:** `FAILSAFE=True` (corner = emergency stop), `PAUSE=0.05`.
5. **Admin apps:** if a target app runs as admin, inputs from a normal process are blocked. Don't demo those.
6. **Projector:** Windows display set to **Duplicate**, one resolution (1920×1080), scaling 100% or 125%, and never change it after recording fallbacks. Turn on **Do Not Disturb** (no toasts in screenshots).

## 6. OpenAI config (`.env`)

```
OPENAI_API_KEY=sk-...
GH_MODEL=gpt-6.1-sol            # computer-use model (per the docs' computer tool examples)
GH_STT_MODEL=<look up in docs: speech-to-text guide>
GH_TTS_MODEL=<look up in docs: text-to-speech guide>
GH_TTS_VOICE=<pick one>
GH_MAX_TURNS=25
GH_TEACH_PACE=1.0               # seconds between actions
GH_SCREEN_MAX_W=1440
```

What we confirmed from `developers.openai.com/api/docs/guides/tools-computer-use`:
- Tool: `{"type": "computer"}`; examples use `gpt-6.1-sol`.
- Output item `type: "computer_call"` with `call_id`, an **`actions` array** (ordered), and `status`.
- Action types: `click, double_click, drag, move, scroll, keypress, type, wait, screenshot`. Click has `button, x, y`; type has `text`.
- Reply with `{"type":"computer_call_output","call_id":...,"output":{"type":"computer_screenshot","image_url":"data:image/png;base64,...","detail":"original"}}` and `previous_response_id`.
- Loop until there are no `computer_call` items. If you downscale screenshots, map coordinates back.

**Not confirmed (Codex must read the "integration recipes" pages before coding):** field names for scroll / keypress / drag / move / double_click / wait, `pending_safety_checks` handling, any display-size fields. Code defensively and log unknown actions instead of crashing.

## 7. Latency budget per step

The model call is about 2–5 s, the glide 0.7 s and the pace 1.0 s. Narrate *during* the model call (HUD "Thinking", pulsing ghost) so the room never sees dead air. If **Ultrafast** is on your plan, set it via the API option from the Ultrafast guide; the code makes it one config flag.

## 8. requirements.txt

```
openai>=2
python-dotenv
mss
pillow
numpy
pyautogui
pynput
PyQt6
sounddevice
soundfile
uiautomation
pyttsx3
```
