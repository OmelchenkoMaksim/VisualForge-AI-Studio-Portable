# Music models: which one for what

Evidence: [card rules](../README.md). Section comparisons are draft summaries of the linked cards/reports; **[recorded]** means historical evidence, not a new test. Source/config is authoritative; availability still depends on installation and policy.

| Engine (card) | Id | Length | Rate | Vocals | Prompt language | Choose it when |
|---|---|---|---|---|---|---|
| [ACE-Step 1.5 Turbo](music-ace-step-15-turbo.md) | `ace-step-15-turbo` | 10-180 s | 48 kHz | **yes, many languages** (new voices), or instrumental | English caption, lyrics in the sung language | songs with vocals, tracks longer than a minute |
| [MiniMax Music3 INT8](music-minimax-music3.md) | `minimax-music3-int8` | 10-60 s (an upper limit) | 32 kHz | yes, from lyrics, or instrumental | English caption | the heaviest engine for a track up to a minute; the Director's Ultra prefers it when it covers the film |
| [Stable Audio 3 Medium](music-stable-audio-3.md) | `stable-audio-3-medium` | 10-120 s | 44.1 kHz | **no voice** | plain English prompt | music or ambience without singing |

## Rules that hold for the Music section

* **Prompts**: English. Describe genre, instruments, mood, tempo and the voice; lyrics only where the engine sings. Tempo, language and "instrumental only" are instructions, not guarantees.
* **Songs use newly generated vocals, never voice cloning**; a particular voice, exact pronunciation and every lyric word are model-dependent.
* **Duration is a request**: MiniMax may finish earlier (an end-of-audio token). Check the length of the result.
* **One job at a time**, one owned process per job (ACE-Step, MiniMax) or one ComfyUI prompt (Stable Audio). Output checks follow each backend's contract; MiniMax can finish before the requested upper limit, so do not assume exact duration from a successful status.
* **In a film**: the Director normalises tracks, applies cue gains and ducking, then uses a bounded final gain towards -18 LUFS before limiting. The old -26.8 LUFS film predates that final stage; it is not the current loudness contract. See [the Director](../../VisualForge-Director.md).
* The separate Music prompt manager remains an inactive scaffold. These notes do not imply automatic Gemma preparation of every Music-tab request.
* Long durations and every language or mode need their own evidence; a short instrumental smoke test proves nothing about the rest.

## When you need to check or extend this section
Drive the Music tab through the UI, then judge the file with `ffprobe`, measured loudness and (for vocals) Whisper; `tests/test_music.py` covers validation and ownership. Evidence receipts: `music.verification` of the release and `Support/Diagnostics/MiniMaxMusicVerification.json`.
