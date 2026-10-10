<p align="center">
  <img src="docs/banner.png" alt="Ghost Hands: a glowing ghost cursor gliding toward a target with a numbered caption" width="100%">
</p>

<p align="center">
  <b>Ask how. Watch it happen on your screen. Then do it yourself.</b><br>
  Ask "how do I ___ in this app?" A ghost cursor does it on your real Windows screen at human speed,<br>
  narrating every step, then coaches you through doing it yourself.
</p>

<p align="center">
  <a href="https://ghost-hands-six.vercel.app"><b>Website</b></a> ·
  <a href="https://ghost-hands-six.vercel.app/#demo"><b>Demo video</b></a> ·
  <a href="#how-to-run-windows-1011-python-311-an-openai-api-key"><b>Run it</b></a> ·
  <a href="#how-it-works"><b>How it works</b></a>
</p>

<p align="center">
  <img alt="GPT-6.1 Sol computer use" src="https://img.shields.io/badge/GPT--6.1_Sol-computer_use-8B7CFF?logo=openai&logoColor=white">
  <img alt="OpenAI speech" src="https://img.shields.io/badge/OpenAI-STT_%2B_TTS-3EE7FF?logo=openai&logoColor=white">
  <img alt="Windows 10 and 11" src="https://img.shields.io/badge/Windows-10_%7C_11-0078D6?logo=windows&logoColor=white">
  <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="PyQt6" src="https://img.shields.io/badge/PyQt6-60_fps_overlay-41CD52?logo=qt&logoColor=white">
  <img alt="OpenAI DevDay 2026 hackathon" src="https://img.shields.io/badge/OpenAI_DevDay_2026-Bengaluru_hackathon-FFB547">
</p>

<p align="center">
  <img src="docs/hero.gif" alt="Asked to turn on dark mode, the ghost glides through Settings: Personalization, Colors, the mode dropdown, Dark. Each step is captioned, and the theme flips." width="900">
  <br>
  <sub>“Turn on dark mode in Windows.” Rendered frame by frame by the real overlay code (<code>ghost/overlay/</code>) over a mock of Settings. The captions are verbatim from a real GPT-6.1 Sol run. The live overlay is excluded from screen capture, so ordinary screen recorders can't see it.</sub>
</p>

## The problem

Learning software means pausing a tutorial six times to copy one click, from a screen that doesn't look like yours.
AI agents that "just do it" leave you no better at doing it yourself.

## What it does

1. **Ask.** Hold **F8** and say the task, or press **F7** and type it.
2. **The ghost does it on your screen.** A glowing cursor glides to each real target, then clicks or types there. Each step gets a caption and a spoken line. Verified end to end on Notepad ("open Notepad and type hello") and Windows Settings ("turn on dark mode in Windows", 6 steps).
3. **Your turn.** Press **F9** and the last run comes back as pulsing rings, one per step. Ghost Hands waits for *your* real click on each one: a hit goes mint, a miss shakes the ring, and after two misses the ghost demonstrates.
4. **Safety stop.** Before every click, it reads the UI Automation name under the cursor. Send, Pay, Delete, Submit, Buy, Transfer and similar actions are refused in code, not in the prompt. The ghost parks with an amber lock and says *"Stopped before Send. That one is yours."*
5. **Grab the mouse to take over.** A deliberate mouse move freezes the run instantly: "You've got it ✋". **Esc** also stops everything.

<table>
  <tr>
    <th width="50%">🔒 It never does the scary part</th>
    <th width="50%">✋ Grab the mouse, it lets go</th>
  </tr>
  <tr>
    <td><img src="docs/handover.gif" alt="The ghost types the manager's email into a Share dialog, then parks on Send with an amber lock: Stopped before Send. That one is yours." width="100%"></td>
    <td><img src="docs/takeover.gif" alt="Mid-run, the real mouse moves in; the ghost dissolves into particles and a toast says You've got it." width="100%"></td>
  </tr>
  <tr>
    <td>Send, Pay, Delete, Submit… are refused in code, checked against the UI Automation name under the click.</td>
    <td>A deliberate move of the real mouse freezes the run instantly. You're always in control.</td>
  </tr>
  <tr>
    <th colspan="2">🎯 Your turn (F9): it coaches you through the same path</th>
  </tr>
  <tr>
    <td colspan="2" align="center"><img src="docs/coach.gif" alt="Pulsing numbered rings mark each step; the user's clicks turn them mint, a miss shakes the ring, and it ends with Done. You did it yourself." width="720"></td>
  </tr>
  <tr>
    <td colspan="2">Rings replay the last run from <code>runs/LAST</code>. It waits for <i>your</i> real click within 45 px: a hit goes mint, a miss shakes the ring. No AI is involved in this part.</td>
  </tr>
</table>

## Built on OpenAI DevDay 2026

- **GPT-6.1 Sol + the computer-use tool** (Responses API, `tools=[{"type":"computer"}]`). The model sees screenshots and returns batched actions with short commentary, which become the captions.
- **OpenAI speech-to-text** (`gpt-transcribe`) for push-to-talk.
- **OpenAI text-to-speech** (`gpt-4o-mini-tts`, voice *marin*, streamed): a calm teacher voice narrates each step.
- **Planned and reviewed with Codex.** Codex built the brain (agent loop, executor, guard, voice) in parallel with the overlay.

## How it works

```mermaid
flowchart LR
    ask(["F8 voice / F7 text"]) --> shot["Screenshot<br/>(mss, ≤1440 px)"]
    shot --> model["GPT-6.1 Sol<br/>computer use"]
    model -->|"actions + commentary"| guard{"Guard: UI Automation<br/>name under (x, y)"}
    guard -->|"Send / Pay / Delete…"| stop["🔒 HandOver: stop"]
    guard -->|"safe"| ghost["Overlay: ghost glides,<br/>caption + cue"]
    ghost -->|"ack"| exec["Executor: the real<br/>click / type"]
    exec --> tts["TTS narrates"] --> shot
    exec --> log[("runs/&lt;id&gt;/steps.json")]
    log --> coach["F9: coach rings"]
    log --> replay["F10: replay, no AI"]
```

One Python process, two threads. The brain thread runs the computer-use loop. The Qt thread draws the overlay at 60 fps. They only talk through two queues defined in [`ghost/contracts.py`](ghost/contracts.py). The ghost always arrives and acks *before* the real input fires, which is why it looks like the ghost is doing the clicking.

### Engineering worth a look

- **Invisible to the model.** `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` keeps the ghost out of every screenshot. It fails on `UpdateLayeredWindow` windows, so the overlay composites through a GL child widget.
- **Click-through, never steals focus.** Frameless, transparent-for-input, `WS_EX_NOACTIVATE` tool window.
- **60 fps on a laptop iGPU.** PyQt6, prebaked glow pixmaps, eased arc glides, a 22-point comet trail.
- **Clicks land exactly.** Per-monitor DPI aware (`SetProcessDpiAwareness(2)`), correct at 125% and 150% scaling.
- **Crash-proof UI Automation.** The accessibility probe runs in an isolated helper process (`ghost/brain/uia_host.py`).
- **Replayable.** Every run is saved to `runs/<id>/` with `steps.json`, target crops and cached voice, so **F10** replays with zero AI calls.

## Safety

- Before every click, `guard.py` reads the UI Automation name under the point. Send, Pay, Delete, Submit, Post, Buy, Transfer, Checkout, Place order, Sign out and similar are refused in code. The ghost parks with an amber lock: *"Stopped before Send. That one is yours."*
- `pending_safety_checks` from the model are never auto-acknowledged; they hand control back to you.
- Move the mouse deliberately and the run freezes ("You've got it ✋"). **Esc** stops everything. pyautogui's corner failsafe stays on.

## How to run (Windows 10/11, Python 3.11+, an OpenAI API key)

```powershell
git clone https://github.com/Arham-Begani/ghost-hands
cd ghost-hands
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

Demo tasks we use: Google Sheets ("freeze the top row", "make a bar chart of sales by month", "highlight sales above 50,000 in green", "split the names into first and last name"; seed data in `demo/sales.tsv`), Windows Settings ("turn on dark mode"), and "send this sheet to my manager", which stops at Send.

## File map

| Path | What |
|---|---|
| `main.py` | DPI awareness, `.env`, picks the real or fake brain, starts Qt + hotkeys |
| `ghost/contracts.py` | Shared events and queues between brain and overlay |
| `ghost/brain/agent.py` | Responses API computer-use loop, captions from commentary |
| `ghost/brain/screen.py` | mss capture, downscale to <= 1440 px, coordinate mapping |
| `ghost/brain/executor.py` | pyautogui click / type / keypress / scroll / drag |
| `ghost/brain/guard.py`, `uia_rules.py`, `uia_host.py` | UIA hard stops, mouse-grab takeover, isolated UIA helper |
| `ghost/brain/voice.py` | Push-to-talk STT and streamed TTS (pyttsx3 offline fallback) |
| `ghost/brain/runlog.py`, `replay.py` | `runs/<id>/steps.json` + crops + voice; F10 replay |
| `ghost/brain/worker.py`, `cli.py` | Brain thread; headless test `python -m ghost.brain.cli "..."` |
| `ghost/overlay/app.py` | Full-screen click-through, capture-excluded window, event pump |
| `ghost/overlay/cursor.py`, `caption.py`, `hud.py`, `fx.py` | Ghost + trail + cues, caption bubble, HUD pill, splash / lock / burst FX |
| `ghost/overlay/coach.py` | F9 "Your turn" rings, listens for your real clicks |
| `ghost/overlay/hotkeys.py`, `taskbox.py` | Global hotkeys, F7 typed-task box |
| `ghost/overlay/fake_brain.py` | Scripted demo for `--fake` (no API key) |
| `web/` | Landing page (static, deployed on Vercel) |

## Troubleshooting

- **Clicks land in the wrong place.** Keep Windows display scaling fixed while running (100%, 125% or 150% all work) and don't change it between recording a run and replaying it with F10. Run on the primary monitor.
- **Projector / second screen.** Use Windows display mode **Duplicate** (Win+P) at one resolution. In Extend mode the overlay and the screenshots cover only the primary monitor.
- **The ghost shows up in screenshots.** Needs Windows 10 2004 or later for `WDA_EXCLUDEFROMCAPTURE`; the log warns if the call fails.
- **Clicks do nothing in one app.** That app is running as administrator; input from a normal process is blocked. Don't demo admin apps.
- **No voice.** Check the default mic and speakers; set `GH_VOICE=0` to run with captions only, or use **F7** to type instead of talking.
- **API stalls mid-demo.** Press **F10** to replay the last good run with no model calls.

## What's next

- Live click-level coaching that watches what *you* do and corrects you in real time, not just replays a recorded path.
- A shareable library of recorded runs ("how we do expenses here"), so a team's know-how becomes something a new hire can watch on their own screen.
