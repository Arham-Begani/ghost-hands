# INSTRUCTIONS.md — start here, Arham

## 0. Setup (5 min, PowerShell)

```powershell
mkdir ghost-hands; cd ghost-hands
# unzip the files I sent into this folder: BUILD.md, PLAN.md, CLAUDE.md, codex.md, INSTRUCTIONS.md, ghost/contracts.py
copy codex.md AGENTS.md          # Codex auto-loads AGENTS.md; Claude Code auto-loads CLAUDE.md
mkdir ghost\brain, ghost\overlay, runs, demo
ni ghost\__init__.py, ghost\brain\__init__.py, ghost\overlay\__init__.py
py -3.11 -m venv .venv; .\.venv\Scripts\Activate.ps1
pip install openai python-dotenv mss pillow numpy pyautogui pynput PyQt6 sounddevice soundfile uiautomation pyttsx3
"OPENAI_API_KEY=sk-...`nGH_MODEL=gpt-6.1-sol`nGH_MAX_TURNS=25`nGH_TEACH_PACE=1.0`nGH_SCREEN_MAX_W=1440" | Out-File .env -Encoding utf8
git init; git add .; git commit -m "contract + briefs"
```

Then:
- **Windows:** Settings → System → Notifications → Do Not Disturb **on**. Display → **Duplicate** when you plug into the projector, and decide the resolution now.
- **The demo Sheet:** make a Google Sheet with `Name | Month | Region | Sales`, 20 rows, names as "First Last", some sales over 50,000.

## 1. How the work is split, and why

| | Codex | Claude Code |
|---|---|---|
| Owns | `ghost/brain/*` | `main.py`, `ghost/overlay/*` |
| Builds | everything that calls OpenAI: the computer-use loop, STT/TTS, executor, guard, run log, replay | everything the room *sees*: ghost cursor, trail, captions, HUD, FX, coach mode, hotkeys |
| Why | it's the OpenAI-native agent, so your "how we used OpenAI's tools" story is honest and strong | the overlay is pure craft and visual polish, and runs fully on fake events, so it never waits on the brain |

**The only shared file is `ghost/contracts.py`, and it is frozen.** If either agent asks for a contract change, you edit it by hand, commit, and tell the other agent in one line. That single rule prevents the merge disaster.

Run them **in two terminals in the same folder**. They touch disjoint folders, so no branches are needed. Commit at each checkpoint.

## 2. FIRST PROMPT: Codex (terminal 1, `codex` in the repo folder)

> Read AGENTS.md, BUILD.md and ghost/contracts.py fully before writing code. You own ONLY ghost/brain/*. Never edit ghost/overlay/*, main.py or ghost/contracts.py; Claude is building the overlay in parallel right now.
>
> Before coding the agent loop, open developers.openai.com/api/docs/guides/tools-computer-use and its integration recipes ("Implement action handlers", "Capture screenshots", "Repeat the computer-use loop"). Get the exact field names for every action type (scroll, keypress, drag, move, double_click, wait) and how pending_safety_checks work. Also look up the current speech-to-text and text-to-speech model names. Write what you found as a comment block at the top of ghost/brain/agent.py.
>
> Then build in this order, stopping to show me each ✅ from AGENTS.md: screen.py → executor.py → guard.py (hard-stop check only for now) → runlog.py → agent.py → cli.py → worker.py.
>
> First milestone, by 12:25: `python -m ghost.brain.cli "open notepad and type hello from ghost hands"` works on this Windows laptop, with auto-acked GhostActions printed. Process is DPI aware (SetProcessDpiAwareness(2) at the top of cli.py). Coordinates are mapped back from the downscaled screenshot. Be fast, keep it boring and reliable, and commit after each ✅.

## 3. FIRST PROMPT: Claude Code (terminal 2, `claude` in the repo folder)

> Read CLAUDE.md, BUILD.md and ghost/contracts.py fully. You own ONLY main.py and ghost/overlay/*. Never touch ghost/brain/* or ghost/contracts.py; Codex is building the brain in parallel right now.
>
> First milestone, by 12:25:
> 1. main.py: DPI aware, then try the real brain worker, falling back to fake_brain.
> 2. The full-screen overlay window: click-through, never steals focus, excluded from capture via SetWindowDisplayAffinity 0x11. Prove the exclusion by saving an mss screenshot while the ghost is visible and checking the ghost is NOT in it.
> 3. The ghost cursor: hand-drawn arrow, prebaked glow, comet trail, cubic ease-in-out glide along a slight arc, click ripple.
> 4. The caption bubble with typewriter text and step badge.
> 5. The HUD pill with state dot and step counter.
> 6. fake_brain.py running the scripted 6-step Sheets sequence on a loop.
>
> Follow the visual spec in CLAUDE.md exactly; this runs on a projector in front of judges, so it must look designed, not default. Keep 60 fps. Then show me a screenshot of it running and commit.

## 4. CHECKPOINT 1 (12:30), prompt both

**To Claude:**
> Brain is ready. Switch main.py to the real worker and test with F7: type "freeze the top row" with the Google Sheet focused. Fix anything on the overlay side; report brain-side issues to me in one line, don't fix them.

**To Codex:**
> Overlay is live. Next: guard mouse-grab abort, voice.py (STT + TTS with pyttsx3 fallback), replay.py, and worker.py dispatch for every to_brain event. Then run "freeze the top row" on the Google Sheet via the CLI and tune the system prompt until it takes the visible menu path (View → Freeze → 1 row).

## 5. CHECKPOINT 2 (13:15), prompt both

**To Claude:**
> Build coach mode (F9) from runs/LAST, and the HandOver / Abort / Done FX. Then polish only: check caption placement and font sizes at 1920×1080 on the projector. No new features.

**To Codex:**
> Run tasks 2–4 from PLAN.md, plus "send this sheet to my manager" (it must HandOver on Send). Fix misses by tuning the prompt or the key map only. Make sure replay.py reproduces each run exactly.

## 6. What you do yourself
- Test every ✅ the agents claim, on the real screen. Don't trust "it should work".
- At 13:15, **record one clean run per task.** Those `runs/` folders are your F10 fallbacks. Copy them into `demo/fallback/`.
- Write the pitch from PLAN.md on a card. Rehearse it 3 times with a timer.
- In the demo, say the line: *"built on GPT-6.1 Sol's computer-use tool, built with Codex."*

## 7. If things break

| Symptom | Fix |
|---|---|
| Clicks land offset | DPI awareness isn't the first thing that runs, or the screenshot scale isn't applied |
| Ghost appears in the model's screenshots | `SetWindowDisplayAffinity` failed; use the fallback of hiding the overlay during capture |
| Overlay eats clicks | `WindowTransparentForInput` plus `WS_EX_TRANSPARENT` are both needed |
| Model takes keyboard shortcuts instead of menus | strengthen the "prefer the visible path" line in the system prompt |
| API slow or down | F10 replay; say "here's the one from earlier" |
| Voice fails in a loud room | F7, type the task |
