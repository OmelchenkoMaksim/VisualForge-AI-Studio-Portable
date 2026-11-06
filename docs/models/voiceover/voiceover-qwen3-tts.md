# Qwen3-TTS 0.6B Base (`qwen3-tts-12hz-0.6b-base`, Voiceover and Video S2V)

[sources/code, follow-up 2026-11-06] Reference/preset research: [Audio Presets](../../VisualForge-Audio-Presets.md).
Chinese 0.6B short-reference observations are hypotheses for our BF16 Russian path; a recent ICL/X-vector study used 1.7B.
The new UI delivery recipes affect existing segmentation/pauses only on Qwen. VoxCPM2's trained style prefix is blocked here.
No new Qwen GPU or public-human-reference qualification is claimed by this follow-up.

Status: **short local integration checks recorded**. Source identity/prompting reviewed **2026-11-06**; fresh checks below are separate from dated historical observations. Qwen/Qwen3-TTS-12Hz-0.6B-Base, revision `5d83992436eae1d760afd27aff78a71d676296fc` (`App/server/voiceover/voiceover_registry.py`). **[code]**

Alibaba Qwen3-TTS 12 Hz **0.6B Base**, 24 kHz speech with voice cloning, Apache-2.0, 1.8 GB (`video.model.qwen3-tts`, shared with the Video section; packages in `Runtime/Video/voice-packages`). Used in **speaker-embedding mode** (no transcript of the sample). Marks: [the rules](../README.md).

## What it is for
The lighter engine: batches of fragments, far less memory than VoxCPM2, the fallback when VoxCPM2 fails. It also speaks the typed text for the Wan S2V voice sample.

## Strong at / Weak at
* Strong: fast in batches (0.28-0.31 s of work per second of audio with batches of 8; 2.3 GB model, 7 GB with batches); similarity to the sample 0.986-0.990; the Director fell back to it successfully when VoxCPM2 could not compile (115 s job) **[recorded 2026-10-28, 2026-11-01]**.
* Weak: 24 kHz; **4 ASR mismatches in 360 words** (one word in four voices) against 0 for VoxCPM2 on the built-in voices; the speaker embedding can reduce similarity to the voice; the Qwen3-TTS 1.7B model is a candidate, not integrated **[recorded / sources]**.

## Expect / Do not expect
Expect intelligible speech in the sample's identity at lower cost. Do not expect an exact voice (the embedding mode is not an exact guarantee), a 48 kHz result, or more than the sample's 3-30 s of material to help.

## Modes and settings
| Item | Values |
|---|---|
| Cloning | speaker embedding of a 3-30 s sample (no transcript) |
| Speed and memory | batches of 8; GPU claim 4,000 MB |
| Languages | those of the Qwen3-TTS API (Russian, English and automatic selection are used) |
| Output | 24 kHz fragments, then the same assembly as every engine (pauses, `loudnorm`) |

## How to ask it
Plain, normalised text; a clean sample of 3-30 s. Same text preparation as for VoxCPM2.

## Measured here **[recorded]**
28 min story: measured (guide, 2026-10-28). Eight built-in voices: 4 of 360 words wrong (one sentence). Director fallback job 115 s (2026-11-01).

## Not verified yet
Long-form continuity under new pacing, general timbre/emotion quality and a cloned human voice. A short pacing A/B is recorded below. Historical approximately one-hour objective runs use a synthetic Windows reference (2026-10-28), not fresh qualification. **[unknown]**

## Where in the code, deeper reports
`App/server/voiceover/qwen_tts_worker.py`, `voiceover_registry.py`; the S2V use is in `App/server/video/s2v_worker.py`. Report: [VisualForge-Voiceover-Guide.md](../../VisualForge-Voiceover-Guide.md).

## Sources and refresh
Hugging Face and GitHub (Qwen3-TTS model card and repository). Refresh on a new Qwen3-TTS release or when the 1.7B model is considered.


## Automatic preparation and current evidence

[sources/code, 2026-11-06] Checkpoint config confirms base / 0b6. The installed generate_voice_clone embedding path has no general instruct field. Gemma classifies the separate delivery request into validated segmentation and inserted boundary pauses; narration is never sent for rewriting or appended with direction. Empty delivery preserves baseline pacing. A clean naturally spoken reference matters; synthetic references and speaker similarity cannot establish human naturalness. Refresh handles: Qwen/Qwen3-TTS-12Hz-0.6B-Base, QwenLM/Qwen3-TTS API; issue #386 (12Hz echo). The 1.7B ICL echo case #341 does not qualify this embedding-only path.

[GPU, 2026-11-06] Three Russian fragments / two chapters, existing designed warm-woman reference: MP3 24 kHz mono, 22.340→21.890 s with conversational gaps, all three audio/Whisper verdicts reused. Finished prepared MP3: -19.33 LUFS / -2.36 dBTP, no nonfinite/full-scale samples. Whisper Turbo requested no repairs; numbers/names were inspected in its transcripts. [recorded, owner listening] Owner preferred the new gaps in this level-matched short A/B; the agent did not listen. This qualifies this timing change, not human cloning or new intra-phrase prosody. See [Audio Preparation](../../VisualForge-Audio-Preparation.md).
