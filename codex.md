# codex.md — Codex's brief (copy this file to `AGENTS.md` so Codex auto-loads it)

You are building the **BRAIN** of **Ghost Hands** for a live hackathon demo **today**: OpenAI DevDay Exchange community hackathon, Bengaluru, demos at ~14:00 IST. Read `BUILD.md` and `ghost/contracts.py` before writing anything.

**Product:** the user asks "how do I ___ in this app". A ghost cursor performs it on their real Windows screen at human speed, narrating each step. Then the user gets a "your turn" coaching mode built from the run log.

## Your lane

You own **only** `ghost/brain/*`. Never edit `ghost/overlay/*`, `main.py` or `ghost/contracts.py`. Claude is building the overlay in parallel. If you need a contract change, stop and tell Arham in one line.

You own everything that talks to OpenAI. That is deliberate: the judges score "how you used OpenAI's tools", and this is that story.

## Build order, with acceptance checks

### 1. `screen.py`
- `capture() -> (png_bytes, scale)` for the primary monitor via mss.
- Downscale to ≤ `GH_SCREEN_MAX_W` wide and return the factor that maps model coordinates to physical pixels.
- `crop(x, y, w=360, h=240) -> PNG`.
- ✅ Saves a screenshot. Mapping (0,0) and (max,max) back hits the screen corners.

### 2. `executor.py`
- `do(action)` for `click`, `double_click`, `right_click` (click with `button:"right"`), `drag`, `move`, `scroll`, `type`, `keypress`, `wait`.
- `pyautogui.FAILSAFE=True`.
- Keypress mapping:
  - The model's key names (e.g. `"CTRL"`, `"ENTER"`, `"ARROWDOWN"`) map to pyautogui names. Use a dict, lower-case, with aliases.
  - Combos use `hotkey(*keys)`.
- Typing: `pyautogui.write(text, interval=0.03)`. Use the clipboard plus Ctrl+V for non-ASCII.
- Every move the executor makes calls `guard.expect(x, y)` first, so the abort detector ignores it.
- ✅ Opens Notepad, types, selects all, copies.

### 3. `guard.py`
- **Hard stops:** `check(x, y) -> (ok, name)` reads the UI Automation element under the point (`uiautomation.ControlFromPoint`). It blocks when the name, its parent's name, or its value matches:
  ```
  send|pay|purchase|buy|checkout|delete|remove|submit|transfer|confirm order|place order|sign out|log ?out|unsubscribe|post
  ```
  A block emits `HandOver(x, y, 'Stopped before "<Name>". That one is yours.')` and ends the run. Also return `name` for captions.
- **Mouse-grab abort:**
  - A daemon thread polls `GetCursorPos` every 40 ms.
  - If the cursor is > 30 px from the last expected position while no executor move is in flight, set `ABORT` and emit `Status("aborted", "You've got it")`.
- ✅ Hover a Gmail "Send" button and `check` refuses. Wiggle the mouse mid-run and the run stops within 100 ms.

### 4. `agent.py`: the computer-use loop
- `client.responses.create(model=GH_MODEL, tools=[{"type":"computer"}], input=..., previous_response_id=...)`.
- First input: the system prompt below, then the user task, then the first screenshot as an input image with `detail:"original"`.
- For each `computer_call`, iterate its **`actions` array in order**. For each action:
  1. Scale the coordinates.
  2. Run `guard.check`.
  3. Build the caption.
  4. Emit `GhostAction`.
  5. Wait for `ack` (3 s timeout, then continue anyway).
  6. Save a crop and a log record.
  7. Run `executor.do`.
  8. Sleep `GH_TEACH_PACE`.
  9. Speak the caption on a non-blocking TTS thread.
- Then send `computer_call_output` with a fresh screenshot (`type:"computer_screenshot"`, `image_url` data URL, `detail:"original"`).
- Handle `pending_safety_checks` the way the docs say. If the docs require acknowledging them, **emit HandOver instead of auto-acknowledging**.
- Stop when a response has no `computer_call`, on `ABORT`, or at `GH_MAX_TURNS`. Then emit `RunFinished` with the model's final text as the summary.
- **Captions**, in priority order:
  1. The short narration text the model emits next to the call (see prompt).
  2. Otherwise, a template from the UIA name: `Click "<name>"`, `Type "<text>"`, `Press <keys>`, `Scroll down`.
- Emit `Status("thinking", ...)` before every API call, so the overlay pulses during latency.
- ✅ `python -m ghost.brain.cli "open notepad and type hello world"` works headless (the CLI auto-acks every `GhostAction`).

**System prompt (use verbatim, tune later):**
```
You are Ghost Hands, a patient teacher operating the user's real Windows computer to SHOW them how to do a task in the app that is open.
Rules:
- Before each action, write ONE short sentence (max 9 words) starting with a verb that says what you are doing and why, e.g. "Open the Data menu to find split tools." This text is shown and spoken to the user.
- Use the visible UI like a human would: menus, buttons, right-click menus. Prefer the discoverable path over keyboard shortcuts, because the user must be able to repeat it. Use shortcuts only if there is no visible path.
- Never click Send, Pay, Buy, Submit, Delete, Post or anything that sends, spends, publishes or deletes. Stop and say "Your turn: click <button> when you're ready."
- Do the smallest correct sequence. Do not explore. Do not close or switch apps unless the task requires it.
- If the screen is not what you expect, take a screenshot action and re-orient.
- When done, reply with one sentence summarising what changed on screen.
```

### 5. `runlog.py`
- Writes `runs/<YYYYmmdd-HHMMSS>-<slug>/steps.json` exactly as the schema in `contracts.py`.
- Saves `stepNN.png` crops.
- Writes the run dir path into `runs/LAST`.

### 6. `voice.py`
- `start()` / `stop() -> text`: sounddevice records 16 kHz mono into memory, then calls OpenAI speech-to-text.
- `speak(text)`: OpenAI TTS played through sounddevice, on a single-worker queue so voices don't overlap. Falls back to `pyttsx3` on any error or after 2.5 s.
- Look up the current STT and TTS model names in `developers.openai.com/api/docs/guides/speech-to-text` and `.../text-to-speech`, and read them from `.env`.
- ✅ Say "freeze the top row" and the transcript prints.

### 7. `replay.py`
- `replay(run_dir)` re-emits each logged step as a `GhostAction`, waits for the ack, runs `executor.do`, and keeps the same pacing and TTS. No API calls.
- This is the stage fallback, so it must be bulletproof.

### 8. `worker.py`
- `start_brain_thread()`: a daemon thread that loops on `to_brain` and dispatches:
  - `StartListening` / `StopListening` → `TaskHeard`, then run.
  - `RunTask` → run.
  - `ReplayRun` → replay.
  - `Abort` → set `ABORT`.
- It never crashes: wrap each run in try/except → `Error(text)` → `Status("idle")`.
- Only one run at a time.

## Rules
- Python 3.11+, Windows 10/11. No async frameworks; plain threads.
- Every OpenAI call has a timeout and one retry.
- Log to `runs/brain.log`, never spam stdout.
- Keep functions small. No feature beyond this list. If something is ambiguous, pick the option that makes the live demo more reliable.
- Commit after each green ✅ with a message like `brain: executor keypress map`.
