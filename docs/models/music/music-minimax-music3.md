# MiniMax Music3 INT8 (`minimax-music3-int8`, Music)

[sources/code/GPU, follow-up 2026-11-06] A Japanese INT8/FP16 first-hand test motivated replacing the worker's negative vocal
list with `Fully instrumental.` alongside the existing `[Instrumental]` field. Four local 9.996 s / 32 kHz stereo comparisons,
seeds 71/173, are finite with no full-scale samples; both arms use the positive statement and one adds the former exclusions.
No listening-based instrumental-compliance advantage is established. [Audio Presets](../../VisualForge-Audio-Presets.md)
records exact conditions, Taiwanese/Chinese practical reports and rejected speculative AR tuning. Existing INT8 sampling/license remain unchanged.

Status: **short local integration checks recorded**. Source identity/prompting reviewed **2026-11-06**; fresh checks below are separate from dated historical observations. Upstream revision `9e364ccd290a401561091fb441ed19cd7de92550` (`App/server/music/music_registry.py`); exact Comfy conversion files/hashes remain in the component catalog. **[code]**

MiniMax Music3 (ComfyUI conversion, INT8 ConvRot, released 2026-08-13): lyrics-conditioned songs and instrumentals, autoregressive structure model + DiT; **MiniMax-Music3 Community License** (not MIT: notices, attribution for commercial products, acceptable-use policy, extra terms for offering a generation service). Three files, 11.9 GB, under `Models/Music/MiniMax-Music3/`. Marks: [the rules](../README.md).

## What it is for
The heaviest music engine, up to **60 seconds**. The Director's Ultra puts it first for a film it covers.

## Strong at / Weak at
* Strong: vocals from lyrics with section tags; its own structure model, so the ACE-Step planner is disabled for it; a short instrumental track was generated inside a Director film **[recorded 2026-11-01]**. "State of the art" is the publisher's claim **[sources]**.
* Weak: **experimental** here: the upper limit is 60 s and the model may finish earlier; tempo, language and instrumental-only adherence are generative instructions; long songs are disabled; other durations, languages and long-form consistency are not tested; the receipt covers a short test only **[recorded / unknown]**.

## Expect / Do not expect
Expect up to a minute of music, possibly shorter than asked. Do not expect a full song structure beyond a minute, guaranteed language or tempo, or comparison with ACE-Step to be settled: nobody compared them by ear.

## Modes and settings
| Item | Values |
|---|---|
| Mode | `instrumental`, `song` (lyrics required) |
| Duration | 10-60 s (an upper limit); output 32 kHz stereo WAV (native 44.1 kHz decoder geometry, resampled by FFmpeg without changing pitch or duration) |
| Sampling | 30 steps Euler/simple, CFG 1.7; AR CFG 1.7, top-k 50; tiled decode 1536 / overlap 64 |
| Prompt parts | caption: **Global Metadata**, **Vocal Details**, **Arrangement**; lyrics: `[Verse]`, `[Chorus]`, `[Bridge]`, `[Outro]` alone on their own lines |
| Planner | off (the setting is kept but disabled) |

## How to ask it
English caption with genre, mood, instruments and **precise voice description**; sections as above; lyrics in the singing language. For a film: one instrumental track whose length covers the film (60 s at most; longer films loop it or use ACE-Step). **[sources: the publisher's prompt guide in the registry; the format is [recorded]]**

## Measured here **[recorded]**
A 10 s instrumental in the Director's prompt-only film took part of a 9.2 min run (2026-11-01; the plan wrote it as `minimax-music3-int8`). The exact short-test receipt: `Support/Diagnostics/MiniMaxMusicVerification.json`.

## Not verified yet
Durations other than the short test, every language, long-form consistency, instrumental-only adherence, quality against ACE-Step. **[unknown]**

## Where in the code, deeper reports
`App/server/music/minimax_worker.py` (one owned child using `Runtime/Video/python` and native ComfyUI 0.34), `music_registry.py`. Report: [MiniMax-Music3.md](../../MiniMax-Music3.md).

## Sources and refresh
Hugging Face (MiniMaxAI/MiniMax-Music3 licence, Comfy-Org/MiniMax-Music-3), the ComfyUI blog (integration announcement), the ComfyUI workflow template. Refresh on a new release or licence terms.


## Automatic preparation and current evidence

[sources/code, 2026-11-06] Gemma builds Global Metadata, requested Vocal Details (songs) and Arrangement, <=1600 characters. Lyrics remain verbatim. Leading section tags must occupy their own lines: same-line words are refused before generation because the checkpoint input contract can discard them. Explicit tempo/language become generative direction, not guarantees. INT8 ConvRot, sampling, proper resampling and license metadata are unchanged. Refresh handles: MiniMaxAI/MiniMax-Music3 card, Comfy-Org/MiniMax-Music-3, Diffusers minimax_music3 input contract; first-hand 8-bit vocal-free attempts in MiniMax discussion #23 remain external failure evidence, not a local result.

[GPU, 2026-11-06] Real Gemma → instrumental 9.996 s and Russian song 19.992 s (requested 10/20), seed 71: 32 kHz stereo, native decoder 44.1 kHz, finite samples. A prior song exposed clipping in pre-resampling PCM quantization. Float resampling, measured constant headroom and 5 ms edge ramps now yield -1.00 dBTP, no full-scale samples and zero final samples in both real reruns; dynamics are not loudness-normalized. CPU over-range fixtures test this mechanism. Lyric intelligibility, vocal-free compliance and musical endings were not listened to. See [Audio Preparation](../../VisualForge-Audio-Preparation.md) for retained failures, measurements and limits.
