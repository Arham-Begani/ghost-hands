# Ghost Hands 👻🖱️

**Ask "how do I ___ in this app?" A ghost cursor does it on your real Windows screen at human speed, narrating every step, then coaches you through doing it yourself.**

![The ghost mid-step: glowing cursor, live typing chip, numbered caption and the HUD pill](docs/ghost-mid-step.png)

<sub>The overlay mid-step: ghost cursor with glow, live typing chip, a step-numbered caption, and the HUD pill (Step 3 / 6). The overlay is excluded from screen capture, so the model never sees it. This still comes from the overlay's debug "room view" renderer.</sub>

## The problem

Learning software means pausing a tutorial six times to copy one click, from a screen that doesn't look like yours.
AI agents that "just do it" leave you no better at doing it yourself.

## What it does

1. **Ask.** Hold **F8** and say the task, or press **F7** and type it.
2. **The ghost does it on your screen.** A glowing cursor glides to each real target, then clicks or types there. Each step gets a caption and a spoken line. Verified end to end on Notepad ("open Notepad and type hello") and Windows Settings ("turn on dark mode in Windows", 6 steps).
3. **Your turn.** Press **F9** and the last run comes back as pulsing rings, one per step. Ghost Hands waits for *your* real click on each one: a hit goes mint, a miss shakes the ring, and after two misses the ghost demonstrates.
4. **Safety stop.** Before every click, it reads the UI Automation name under the cursor. Send, Pay, Delete, Submit, Buy, Transfer and similar actions are refused in code, not in the prompt. The ghost parks with an amber lock and says *"Stopped before Send. That one is yours."*
5. **Grab the mouse to take over.** A deliberate mouse move freezes the run instantly: "You've got it ✋". **Esc** also stops everything.

## Built on OpenAI DevDay 2026

- **GPT-6.1 Sol + the computer-use tool** (Responses API, `tools=[{"type":"computer"}]`). The model sees screenshots and returns batched actions with short commentary, which become the captions.
- **OpenAI speech-to-text** (`gpt-transcribe`) for push-to-talk.
- **OpenAI text-to-speech** (`gpt-4o-mini-tts`, voice *marin*, streamed): a calm teacher voice narrates each step.
- **Planned and reviewed with Codex.** Codex built the brain (agent loop, executor, guard, voice) in parallel with the overlay.

## How it works

```
 F8 voice / F7 text ──▶ brain thread: screenshot (mss) ──▶ GPT-6.1 Sol computer-use ──▶ actions + captions
   guard: UI Automation name under (x,y) matches Send/Pay/Delete…? ──▶ HandOver, stop
   GhostAction ──▶ Qt overlay (60 fps, click-through, capture-excluded) glides + captions ──▶ ack
   executor (pyautogui) performs the real click/type ──▶ TTS speaks ──▶ new screenshot ──▶ loop
   every step is logged to runs/<id>/steps.json ──▶ F9 coach rings · F10 replay without AI
```

## How to run (Windows 10/11, Python 3.12)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Create `.env` in the repo root:

```
OPENAI_API_KEY=sk-...
GH_MODEL=gpt-6.1-sol
```

Optional settings: `GH_TEACH_PACE=1.0` (seconds between steps), `GH_MAX_TURNS=25`, `GH_SCREEN_MAX_W=1440`, `GH_TTS_VOICE=marin`, `GH_VOICE=0` (mute narration).

```powershell
python main.py              # real brain
python main.py --fake       # no API key needed: scripted demo of the overlay
```

| Key | Does |
|---|---|
| **F8** (hold, release) | Talk: say the task, release to run |
| **F7** | Type the task into a small glass box |
| **F9** | Your turn: coach me through the last run |
| **F10** | Replay the last run with no model calls |
| **Esc** | Stop everything |
| *move the mouse* | Take over mid-run |

## What's next

- Live click-level coaching that watches what *you* do and corrects you in real time, not just replays a recorded path.
- A shareable library of recorded runs ("how we do expenses here"), so a team's know-how becomes something a new hire can watch on their own screen.
