# OPENAI_NOTES.md — confirmed API facts (brain agent, 2026-10-10)

Sources: developers.openai.com/api/docs/guides/tools-computer-use, .../tools-computer-use-integration,
.../speech-to-text, .../text-to-speech, and the installed `openai` SDK **3.28.0** type stubs
(`openai/types/responses/computer_action.py`, `response_computer_tool_call.py`, `response_input_item_param.py`).

## Computer use (GA, Responses API)
- Model: `gpt-6.1-sol`. Tool: `tools=[{"type": "computer"}]` — **no** `display_width`/`display_height`/`environment`
  (those were the old `computer_use_preview` tool). No `truncation: "auto"` needed.
- Output item: `{"type":"computer_call","id","call_id","actions":[...],"status","pending_safety_checks":[...]}`.
  `actions` is an **ordered batch** (GA). SDK also keeps a legacy single `action` field — handle both.
- Action shapes (SDK-confirmed):
  - `click`: `button` in `left|right|wheel|back|forward`, `x`, `y`, optional `keys` (held modifiers)
  - `double_click`: `x`, `y`, optional `keys`
  - `drag`: `path: [{x,y}, ...]` (>= 2 points), optional `keys`
  - `move`: `x`, `y`, optional `keys`
  - `scroll`: `x`, `y`, `scroll_x`, `scroll_y` (pixels; docs' xdotool recipe uses ~100 px per wheel click), optional `keys`
  - `keypress`: `keys: ["CTRL","C"]` — names like ENTER, RETURN, ESC, CTRL, SHIFT, ALT, OPTION, META, CMD, ARROWLEFT...
  - `type`: `text`
  - `wait`: no fields (recipe sleeps 2 s)
  - `screenshot`: no fields (no-op; we send a screenshot after every batch anyway)
- Reply:
  ```python
  client.responses.create(model="gpt-6.1-sol", tools=[{"type":"computer"}], previous_response_id=resp.id,
      input=[{"type":"computer_call_output","call_id":call.call_id,
              "output":{"type":"computer_screenshot","image_url":"data:image/png;base64,...","detail":"original"},
              # only if pending_safety_checks were present AND we chose to proceed:
              "acknowledged_safety_checks":[{"id":..., "code":..., "message":...}]}])
  ```
- Screenshots: PNG data URL, `detail: "original"` (docs: avoid high/low). Docs report best results ~1440x900 / 1600x900;
  API **rejects** images over the 30,000-patch limit (no auto-resize) -> we downscale to <= 1440 wide and map coords x scale.
- Initial screenshot goes in the first user message as `{"type":"input_image","image_url":"data:...","detail":"original"}`.
- Text: assistant `message` items carry `phase` = `"commentary"` (intermediate narration) or `"final_answer"`.
  We use commentary text next to a computer_call as the step caption, and the final message as the run summary.
- `pending_safety_checks` (`id`, `code`, `message`) exist in the SDK but the current guide doesn't describe them.
  **Our policy:** never auto-acknowledge -> emit `HandOver` and stop the run. (Guide's consent tiers: deleting data,
  sending data to third parties, financial confirmation = always confirm at action time; our guard enforces that anyway.)

## Speech (for voice.py)
- STT: `gpt-transcribe` (recommended), also `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`, `whisper-1`.
  `client.audio.transcriptions.create(model="gpt-transcribe", file=f).text`
- TTS: `gpt-4o-mini-tts` (recommended; supports `instructions=`), `tts-1`, `tts-1-hd`. Voices incl. `marin`, `cedar`
  (best), alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse.
  `client.audio.speech.with_streaming_response.create(model=..., voice=..., input=..., response_format="wav")` —
  wav/pcm fastest; pcm = raw 24 kHz 16-bit LE mono.

## Our env vars
`GH_MODEL=gpt-6.1-sol  GH_MAX_TURNS=25  GH_TEACH_PACE=1.0  GH_SCREEN_MAX_W=1440`

## Voice

Confirmed 2026-10-10 on developers.openai.com (guides: speech-to-text, text-to-speech). Code: `ghost/brain/voice.py`.

**Speech-to-text**
- `gpt-transcribe` is the recommended model for recorded speech (we use it; env `GH_STT_MODEL`). `gpt-4o-transcribe` / `gpt-4o-mini-transcribe` are still supported (we fall back to `gpt-4o-transcribe` if the call fails). `gpt-live-transcribe` is for realtime only; `whisper-1` is for timestamps or translation.
- Call: `client.audio.transcriptions.create(model="gpt-transcribe", file=f, prompt=...)` → `.text`.
- `gpt-transcribe` hints: `prompt` (free text), plus `keywords` (list of literal terms) and `languages` (e.g. `["en"]`, which replaces the singular `language`; don't send both) through `extra_body={...}`.
- Formats: mp3, mp4, mpeg, mpga, m4a, wav, webm; 25 MB max. We send 16 kHz mono 16-bit WAV from memory.

**Text-to-speech**
- `gpt-4o-mini-tts` is the newest and most reliable model (env `GH_TTS_MODEL`). It supports `instructions` for tone, pacing, and emotion. `tts-1` / `tts-1-hd` are older; we don't send `instructions` to them.
- There are 13 voices: alloy, ash, ballad, coral, echo, fable, nova, onyx, sage, shimmer, verse, marin, cedar. The docs recommend **marin** or **cedar** for best quality. We use **marin** (env `GH_TTS_VOICE`).
- Instructions: "calm, friendly teacher ... warm, reassuring, unhurried" (env `GH_TTS_INSTRUCTIONS`).
- `response_format`: mp3 (default), opus, aac, flac, wav, pcm. The docs recommend wav or pcm for the lowest latency. pcm = raw 24 kHz 16-bit signed LE mono.
- Streaming: `client.audio.speech.with_streaming_response.create(...)`. We `iter_bytes()` pcm straight into a sounddevice output stream, so playback starts after about 120 ms of audio. The full clip is cached via libsndfile to `stepNN.mp3`. speak() also accepts a `.wav` sibling.
- `GH_VOICE=0` mutes TTS. Offline fallback: pyttsx3 (SAPI5).
