# PLAN.md — today's clock

Demos are at about 14:00. **Code freeze is 13:45.** After 13:45 the only work is rehearsing and recording fallbacks.

## Timeline

| Time | Codex (brain) | Claude (overlay) | Arham |
|---|---|---|---|
| 11:30–11:45 | first prompt, scaffold `ghost/brain` | first prompt, scaffold `main.py` + overlay | venv, `.env`, git init, seed the demo Sheet, Do Not Disturb, display → Duplicate |
| 11:45–12:30 | screen → executor → agent loop, **CLI green on Notepad** | click-through window, excluded from capture, **ghost glide + trail + caption + HUD** driven by `fake_brain` | test each ✅, feed the Sheet URL and coordinates to both |
| **12:30 CHECKPOINT 1** | | | `python main.py` with the real brain: ghost glides to each real target **before** each real click |
| 12:30–13:15 | guard (hard stops + mouse-grab abort), runlog, voice STT/TTS, replay | coach mode (F9), FX (TaskHeard, HandOver, Abort, Done), F7 taskbox, hotkeys | run task 1 end to end, write pitch |
| **13:15 CHECKPOINT 2** | | | Voice → ghost does task 1 in Sheets → F9 coach works → F10 replay works |
| 13:15–13:45 | tune system prompt on tasks 2–4, fix misses | polish only: timing, glow, caption placement on the projector resolution | **record one good run per task** (these are the F10 fallbacks), copy `runs/` to `demo/fallback/` |
| 13:45–14:00 | freeze | freeze | rehearse the pitch 3×, out loud, with a timer |

**Cut order if late** (cut from the top): fancy drag/scroll cues → coach keypress detection → TTS (keep captions) → voice (use F7 typed box). **Never cut:** ghost glide, captions, hard stop, F10 replay.

## Demo setup
- Chrome, zoom 100%, one tab: the seeded Google Sheet. Columns: `Name | Month | Region | Sales`, 20 rows, names as "First Last".
- Windows Settings minimised (for the second-app moment).
- Display: Duplicate, 1920×1080, the same scaling you recorded fallbacks at.
- Do Not Disturb on, Wi-Fi checked, phone hotspot ready as backup.

## Task card (print or show on a slide; the judge picks)
1. "Freeze the top row"
2. "Make a bar chart of sales by month"
3. "Highlight sales above 50,000 in green"
4. "Split the names into first and last name"
5. *Second app:* "Turn on dark mode" (Windows Settings)

## The 90-second demo script

| Time | What happens |
|---|---|
| **0:00** | *(Hook, no slides)* "Everyone here has paused a YouTube tutorial six times to copy one click. Watching someone else's screen is a terrible way to learn yours." |
| **0:12** | "Ghost Hands: you ask how, it does it on *your* screen, then makes you do it." Hand the task card to a judge: "Pick one." |
| **0:18** | Hold F8, say the judge's task. The task text splashes across the screen. The ghost wakes up, glides, narrates ("Open the View menu to find freeze options"), clicks. Step counter ticks. **Let it run, don't talk over it.** |
| **0:50** | Done burst. "Now it's your turn." Press F9 and **give the judge the mouse**. Rings guide them through; each correct click goes mint. |
| **1:10** | "And it never does the scary part." Ask it "send this sheet to my manager". The ghost parks on Send, amber lock: *"Stopped before Send. That one is yours."* |
| **1:20** | Grab the mouse mid-run and it freezes instantly: "You've got it ✋." "Built on GPT-6.1 Sol with the new computer-use tool, and built with Codex. It works in any app, because it reads pixels, not APIs." |

If the API stalls at any point, say "let me show you the one from earlier" and press **F10**. It looks identical.

## Judge Q&A prep
- **"Isn't this just Copilot in Excel?"** That's one app, and it does the task *for* you. Ghost Hands works on any Windows app, teaches the path, and then coaches you through repeating it.
- **"What stops it doing damage?"** It reads the UI Automation name under every click and refuses Send / Pay / Delete / Submit in code, not in the prompt. You can also grab the mouse at any time. Show it.
- **"Why is it slow?"** It moves at human speed on purpose; you can't learn from a blur. With Ultrafast it's a one-line config change.
- **"Who uses this every day?"** Anyone in a tool they half-know: a new hire on day one, a student in Sheets, a parent in a government portal, a founder in Figma.
- **"What did you build today?"** All of it, in about 2.5 hours, with Codex on the OpenAI API layer.
