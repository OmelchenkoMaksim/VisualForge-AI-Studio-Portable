# MiniMax Music3 INT8 (`minimax-music3-int8`, Music)

Status: **development draft** from the linked project reports; observations were not rerun for this collection. Source identity reviewed **2026-11-01**; upstream research not refreshed. Upstream revision `9e364ccd290a401561091fb441ed19cd7de92550` (`App/server/music/music_registry.py`); exact Comfy conversion files/hashes remain in the component catalog. **[code]**

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
| Prompt parts | a caption may separate **Global Metadata**, **Vocal Details**, **Arrangement**; lyrics and prompt may use `[Verse]`, `[Chorus]`, `[Bridge]`, `[Outro]` |
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
