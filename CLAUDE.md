# CLAUDE.md — Claude's brief

You are building the **OVERLAY** of **Ghost Hands** for a live hackathon demo **today** (OpenAI DevDay Exchange community hackathon, Bengaluru, demos ~14:00 IST). Read `BUILD.md` and `ghost/contracts.py` first.

**Product:** the user asks "how do I ___ in this app". A ghost cursor performs it on their real Windows screen at human speed, narrating each step. Then "Your turn" coaches them through it with pulsing rings. **The overlay is the entire visual identity of the project. On a projector it has to look like magic.**

## Your lane

You own **only** `main.py` and `ghost/overlay/*`. Never edit `ghost/brain/*` or `ghost/contracts.py`. Codex builds the brain in parallel. Until it lands, drive everything from `ghost/overlay/fake_brain.py`, which pushes scripted events onto `to_overlay` and auto-acks.

## The window (get this right first)

- One full-screen `QWidget` over the primary monitor:
  - `FramelessWindowHint | WindowStaysOnTopHint | Tool | WindowTransparentForInput`
  - `WA_TranslucentBackground`, `WA_ShowWithoutActivating`
  - via ctypes, ex-style `WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW`
- **Invisible to screenshots:** after show, `SetWindowDisplayAffinity(hwnd, 0x11)`. Verify by taking an mss screenshot while the ghost is visible; the ghost must not appear in it.
- **Never steals focus or clicks.** Test by clicking through it into Notepad while the overlay animates.
- 60 fps `QTimer` repaint. All drawing in `paintEvent` with `QPainter` antialiasing.
- An event pump `QTimer` (16 ms) drains `to_overlay` non-blockingly.
- `main.py` order:
  1. `SetProcessDpiAwareness(2)`
  2. `load_dotenv`
  3. try importing `ghost.brain.worker.start_brain_thread`; if that fails, use `fake_brain` and log it
  4. start `QApplication` and the overlay
  5. start hotkeys

## Visual spec (make it unmistakably designed)

**Palette**

| Token | Value | Use |
|---|---|---|
| ghost | `#F5F7FF` | cursor fill |
| glow | `#8B7CFF` → `#3EE7FF` gradient | cursor glow, trail |
| ink | `rgba(10,12,20,0.82)` | panels |
| amber | `#FFB547` | handover |
| mint | `#4BE3A1` | success, "You've got it" |
| coral | `#FF5C7A` | errors |

**Type:** "Segoe UI Variable Display" (ships with Windows 11; fall back to Segoe UI). Captions 22px semibold. HUD 15px. Task splash 44px.

**Ghost cursor**
- A hand-drawn arrow path (not the OS cursor), 1.6× OS size, `ghost` fill, 1px white stroke, outer glow (blur via stacked translucent strokes).
- **Comet trail:** keep the last ~22 positions. Draw shrinking circles fading from violet to cyan to transparent.
- **Glide:** cubic ease-in-out over `GhostAction.duration_ms`, along a slight arc (bezier control point offset 12% perpendicular). That's what makes it feel alive and not robotic.
- **Idle:** gentle 2 px bob and a breathing glow.
- **Thinking state:** a slow orbiting dot around the cursor.

**Action cues** (play on arrival, then `ack.set()` ~120 ms later)

| Action | Cue |
|---|---|
| click | press-squash, plus a ripple ring expanding 0→56 px and fading (double click = two ripples) |
| right_click | ripple plus a tiny ☰ glyph |
| type | a glass "typing chip" under the cursor that types `text` out char by char at about the executor's speed |
| keypress | key caps (`Ctrl` `+` `V`) pop above the cursor |
| scroll | animated chevrons |
| drag | dotted path to `end_x, end_y` |

**Caption bubble**
- Glass pill in `ink`, 14px radius, anchored +28/+28 from the cursor, flipping to stay on screen.
- Typewriter text at 35 ms/char.
- A small step badge `3` in a gradient circle at the left.
- Stays until the next action, then cross-fades.

**HUD**
- Top-centre pill: state dot (pulsing), state text, `Step 3 / 7` (or `Step 3` when total is 0), and the task text truncated.
- States map to dot colours: listening = cyan, plus a live mic level bar (fake amplitude is fine); thinking = violet, pulsing; acting = cyan; done = mint; error = coral.

**FX**
- **TaskHeard:** the task text fades in centre screen at 44px with letter spacing, then holds 1.5 s and flies into the HUD pill.
- **HandOver:** the ghost parks, an amber ring pulses around the target with a 🔒, and the caption reads `reason`.
- **Aborted** (user grabbed the mouse): the ghost dissolves into particles, and a mint toast says "You've got it ✋".
- **RunFinished ok:** a subtle mint particle burst from the last target, then the HUD shows the `summary` for 3 s, then "F9: your turn".

## Coach mode, "Your turn" (F9, overlay only, no AI)

- Read `runs/LAST` → `steps.json`.
- Show a banner "Your turn: follow the rings".
- For each step, show a pulsing cyan ring with a step-number badge at (x, y), plus the caption.
- A `pynput` mouse listener waits for a **real** click within 45 px:
  - success → mint check + ripple, next step
  - miss → the ring shakes gently, and after 2 misses the ghost briefly demonstrates the motion and fades
- `type` steps show "Type: <text>" and advance on the next click or Enter. `keypress` steps show key caps and advance on that combo (pynput keyboard).
- At the end: "Done. You did it yourself." with the mint burst.

## Hotkeys (`hotkeys.py`, pynput, global)

| Key | Behaviour |
|---|---|
| F8 hold / release | `StartListening` / `StopListening` |
| F7 | `taskbox.py`: a small glass input window. Save `GetForegroundWindow()` before showing it; on Enter, send `RunTask(text)`, hide, and `SetForegroundWindow(saved)`. |
| F9 | coach |
| F10 | `ReplayRun(open("runs/LAST").read().strip())` |
| Esc | `Abort()` |

## `fake_brain.py`

A scripted 6-step "freeze the top row in Google Sheets" sequence with plausible screen coordinates. It emits `Status`, `TaskHeard`, `GhostAction`s (waits for each ack), one `HandOver` demo, and `RunFinished`. It also writes a fake `runs/demo-fake/steps.json` and `runs/LAST` so coach mode can be built today without the brain.

## Rules
- PyQt6 only, no web views.
- Everything must stay at 60 fps on a laptop iGPU: no per-frame image blurs (prebake glow as a pixmap).
- No feature beyond this spec until checkpoint 2 passes.
- Commit after each working piece, e.g. `overlay: comet trail`.
- If you need a contract change, say so in one line and stop.
