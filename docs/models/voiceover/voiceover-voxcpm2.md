# VoxCPM2 (`voxcpm2`, Voiceover)

Status: **short local integration checks recorded**. Source identity/prompting reviewed **2026-11-06**; fresh checks below are separate from dated historical observations. OpenBMB/VoxCPM2, revision `32279effe8c19989596f05d353d1447f51d9e915` (`App/server/voiceover/voiceover_registry.py`). **[code]**

OpenBMB VoxCPM2, **48 kHz** text-to-speech with voice cloning, Apache-2.0, 4.96 GB (`Models/Voiceover/VoxCPM2`), package `voxcpm` 2.0.3 in `Runtime/Video/voxcpm-packages`. Called as in its own package (`VoxCPM(...).generate(text, reference_wav_path=...)`) without the denoiser and without its own text normalisation (ours runs before). Marks: [the rules](../README.md).

## What it is for
Reading long texts and the Director's lines in a cloned or built-in voice at 48 kHz. Also **Voice Design**: a new voice from an English description (how the built-in voices are made).

## Strong at / Weak at
* Strong: every one of 32 built-in-voice takes and the Director's Russian lines were transcribed word for word by Whisper; similarity to the sample 0.983-0.993; 48 kHz **[recorded 2026-10-28/29, 2026-11-01]**.
* Weak: one fragment at a time (no batch interface); the first `torch.compile` costs about 110 s per process; the transcript mode (sample + its text) was **not** more accurate on the historical synthetic test voice and speaks slower. Two public human references now have short integration checks below; perceptual timbre/pace continuity and stress marks remain unqualified **[recorded / GPU / unknown]**.

## Expect / Do not expect
Expect a clear, steady narration close to the sample's identity. Do not expect that a good measurement means a pleasing voice (only the owner can judge), perfect stress marks, or a sample longer than 30 s to help (the studio keeps 30 s after silence; a saved voice may join up to five recordings into 45 s).

## Modes and settings
| Item | Values |
|---|---|
| Cloning | reference sample alone (default), or sample + transcript ("continuation", optional) |
| Speed | `torch.compile` default: 0.35 s of work per second of audio; off (`VISUALFORGE_VOICEOVER_VOXCPM_OPTIMIZE=0`): 0.79 s |
| Memory | 5.1 GB model, 6.1 GB at work; GPU claim 9,000 MB |
| Output | 48 kHz; the manager joins fragments with pauses (0.25 s between sentences, 0.6 paragraphs, 1.5 chapters), `loudnorm`, WAV / MP3 / M4B with chapters |
| Languages the Director offers | Russian, English, German, French, Spanish, Italian, Portuguese, Chinese, Japanese, Korean (the names the module takes); only Russian is the target of the work |

## How to ask it
Plain text, already normalised (numbers and abbreviations as words); sentences of ordinary length; one voice per job. A sample of **3-30 s of clean speech**, no music or noise (noise is copied). In the Director a line is at most about 12 characters per second of the scene.

## Measured here **[recorded]**
| What | Result | Date |
|---|---|---|
| 8 fragments (83-96 s of audio), reference mode, compile | 0.35 s per second, 3.1 % words wrong (without compile 0.79 s) | 2026-10-28 |
| 28 min story through the manager | measured; see the guide | 2026-10-28 |
| Director: two Russian lines, built-in announcer voices | matched by ASR over scene sound; VoxCPM2 job 61 s warm; both lines about 4 min with the cold load | 2026-11-01 |
| Compile with a Cyrillic cache path | `InductorError: UnicodeDecodeError ... 0xd1`; fixed by ASCII cache folders | 2026-11-01 |

## Not verified yet
Beyond the historical approximately one-hour synthetic-reference run: long-form continuity with new pacing/styles, a clean installer execution and general human-reference timbre/emotion listening quality. A short pacing A/B and new public-reference checks are recorded below. **[unknown]**

## Where in the code, deeper reports
`App/server/voiceover/voxcpm_worker.py`, `voiceover_registry.py`, `defaults_worker.py`, `verify_worker.py`, `text_prep.py`. Report: [VisualForge-Voiceover-Guide.md](../../VisualForge-Voiceover-Guide.md).

## Sources and refresh
GitHub and Hugging Face (OpenBMB VoxCPM2 model card and package), the ModelScope page. Refresh on a new VoxCPM release.


## Automatic preparation and current evidence

[sources/code, 2026-11-06] Publisher card/demo and maintainer issue #210 confirm trained `(control)text` conditioning for reference-only cloning, unavailable in transcript continuation. The opt-in, default-off adapter maps a validated Gemma style enum at the worker boundary, preserving narration/reference. The installed 2.0.3 checkpoint encodes references at 16 kHz and outputs 48 kHz; retaining original uploads does not establish a higher-rate-reference advantage. CFG 2.0, ten steps, no denoiser and both compile fallbacks remain unchanged. Style/version enter the speech-cache key; exact transcript hash remains in continuation keys. Details/sources: [Audio Presets](../../VisualForge-Audio-Presets.md).

[GPU, 2026-11-06, separate 23-minute subset] Built-in voice plus two explicitly approved Kyutai CC0 human-donor references: neutral/story/calm, nine 19.73–25.17 s MP3s, 48 kHz mono. Whisper Turbo checked three fragments per output; donor 1410 needed one repair in each variant, with no final flags. Finished outputs finite, nonempty, zero full-scale samples. Style changes reach synthesis; this does not prove naturalness, identity retention or accent quality. No agent listening. [recorded, owner listening] For donor 0a67 the owner preferred neutral over story/calm («Нейтральный лучше / изменения нежелательны»); identity preservation was not separately assessed. Style control stays off by default. Other voices/styles remain unqualified. All comparisons/logs in the linked report.

[GPU, 2026-11-06] Three Russian fragments / two chapters, existing designed warm-woman reference, reference-only mode unchanged: MP3 48 kHz mono, 19.860→19.410 s with conversational gaps; all audio/Whisper verdicts reused. Finished prepared MP3: -17.82 LUFS / -2.09 dBTP, no nonfinite/full-scale samples. Whisper Turbo requested no repairs. [recorded, owner listening] Owner preferred new gaps in this short level-matched A/B; the agent did not listen. The first compiled baseline took 243.5 s including load/compile/verification; cached prepared assembly took 7.8 s, which is not a synthesis-speed comparison. See [Audio Preparation](../../VisualForge-Audio-Preparation.md).
