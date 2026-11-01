# Whisper family (`whisper-turbo`, `whisper-large-v3`, `whisper-small`, Speech)

Status: **development draft** from the linked project reports; observations were not rerun for this collection. Source identity reviewed **2026-11-01**; upstream research not refreshed. Whisper variants and punctuation are pinned per component in `App/config/component-catalog.json`; record the actual variant for each result, not just the family name. **[code]**

OpenAI Whisper under `Models/Speech`, served by `speech/speech_worker.py` in the Music runtime. The manager prefers Turbo, then large-v3, then small among usable installed models unless a setting chooses one. Device `auto` uses GPU when admitted, otherwise CPU; the coordinator releases a GPU-resident idle worker before generation. Marks: [the rules](../README.md).

| Variant / component suffix | Pinned Hugging Face revision | Our starting expectation |
|---|---|---|
| `whisper-turbo` (OpenAI large-v3-turbo) | `41f01f3fe87f28c78e2fbf8b568835947dd65ed9` | Preferred profile; historical observations below concern Turbo, not a controlled variant comparison. |
| `whisper-large-v3` | `06f233fe06e710322aca913c1bc4249a0d71fce1` | Heavier alternative; comparative accuracy and timing here are **[unknown]**. |
| `whisper-small` | `973afd24965f72e36ca33b3055d56a652f456b4d` | Smaller alternative; comparative accuracy on our dictation corpus is **[unknown]**. |

Component IDs are `speech.model.<suffix>`. Revisions are **[code, catalog review 2026-11-01]**, not proof that all three are installed. Optional **RUPunct big**, revision `d05f73afd84b57a45b83238c35b866bc625fe247` (`speech.model.rupunct-big`), restores Russian punctuation/case after recognition; it is not another speech recogniser or a general prompt improver. The [Voice Input guide](../../VisualForge-Voice-Input.md) records its behaviour and fallback rules.

## What it is for
Hearing back what was synthesised or generated: Voiceover verification and repair rounds, the LTX speech check (a scene that does not say its line is generated again, at most twice), the Director's lines in a finished film, dictation and `/api/speech/transcribe`.

## Strong at / Weak at
* Strong: Russian and English lines heard word for word in LTX clips, in the Voiceover (32 of 32 built-in-voice takes) and in finished Director mixes over a song and music **[recorded 2026-10-28 to 11-01]**; fast once loaded (0.14-0.5 s for a short clip).
* Weak: **invents text on silence or music** (a stock "subtitle editor / proofreader" line after the real speech); compares letters, so a wrong but similar word can pass; the English-trained audio encoder of S2V is a different model and is not covered; a cold load (about 35-55 s from the disk) is paid when it is started between GPU jobs **[recorded]**.

## Expect / Do not expect
Expect intelligibility checks and transcripts. Do not expect pronunciation quality, speaker identity, lip sync or any judgement of sound.

## Settings that matter
For generated-speech checks pass `lang=ru` or `en` and mono 16 kHz WAV (the Director extracts audio with FFmpeg); read only the span where speech is expected. Dictation additionally supports the manager's language/device/keep-alive settings and optional RUPunct/Gemma editing. This is an audio-input API, not a text-to-image prompt interface.

## Measured here **[recorded]**
Transcript of a 60 s Director film with two lines: both heard word for word, plus the stock hallucination at the end (2026-11-01); a 10 s film, 1.6 s lines: heard (2026-11-01). Accuracy over many speakers, noisy input, other languages: **[unknown]**.

## Where in the code, deeper reports
`App/server/speech/speech_manager.py`, `speech_worker.py`, `video/speech_check.py`, `voiceover/verify_worker.py`. Reports: [LTX-2.5-Video.md](../../LTX-2.5-Video.md) (speech check), [VisualForge-Voiceover-Guide.md](../../VisualForge-Voiceover-Guide.md) (verification), [VisualForge-Voice-Input.md](../../VisualForge-Voice-Input.md).

## Sources and refresh
OpenAI (Whisper repository and model card), Hugging Face (the turbo size). Refresh on a new Whisper version or when another language matters.
