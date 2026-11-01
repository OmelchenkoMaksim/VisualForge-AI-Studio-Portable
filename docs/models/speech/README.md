# Speech to text (Whisper)

Evidence: [card rules](../README.md). Section comparisons are draft summaries of the linked cards/reports; **[recorded]** means historical evidence, not a new test. Source/config is authoritative; availability still depends on installation and policy.

| Card | What |
|---|---|
| [Whisper family](speech-whisper.md) | Turbo, large-v3 and small; generated-speech checks and dictation; optional RUPunct big for Russian punctuation |

## Rules that hold for the Speech section

* **Whisper is the studio's ears.** Whenever a claim says "heard word for word" it was Whisper. It proves intelligibility, **not** naturalness or lip sync.
* It runs as an owned worker (`speech/speech_worker.py`, in the Music runtime). GPU work obeys coordinator admission; dictation can fall back to CPU while generation uses the GPU. The route `/api/speech/transcribe?lang=ru` takes WAV bytes.
* **Give the language** (`ru`, `en`): auto detection is less reliable on short clips.
* Whisper **hallucinates on silence and music at the end of a clip** (a stock line "subtitle editor ..." appeared after two real lines): cut the tail or read only the part where speech was expected **[recorded 2026-11-01]**.
* A check compares the heard text with the requested one by the share of requested letters found in order (at least 0.8 in the LTX speech check).

## When you need to check or extend this section
Transcribe a known file through the route and compare; `tests/test_speech.py`, `test_speech_polish.py`.
