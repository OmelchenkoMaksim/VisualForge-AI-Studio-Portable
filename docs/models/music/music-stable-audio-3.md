# Stable Audio 3 Medium (`stable-audio-3-medium`, Music)

[sources/code, follow-up 2026-11-06] [Audio Presets](../../VisualForge-Audio-Presets.md) separates song-oriented findings
from this caption-only audio path. Song/vocal recipe buttons are hidden here. No singing, lyric controls, new sampler settings
or new GPU quality qualification are introduced by this follow-up; existing short-output evidence remains scoped below.

Status: **short local integration checks recorded**. Source identity/prompting reviewed **2026-11-06**; fresh checks below are separate from dated historical observations. Upstream revision `75aad8836271cb5a5123fb58304fae5d3bb4b0f9` (`App/server/music/music_registry.py`); this card is for Medium, not Base. **[code]**

Stability AI Stable Audio 3 **Medium** (released 2026-09-23): text to music or sound ambience, **no lyric-controlled singing in the exposed workflow**. Runs inside the Image ComfyUI (not a worker): checkpoint + T5Gemma encoder, `lcm` sampler, **8 steps, CFG 1**. Licence: Stability AI Community terms plus Gemma terms (see Components). The Base checkpoint is not offered. Marks: [the rules](../README.md).

## What it is for
Instrumental music and sound ambience (rain, a harbour, drones) of 10-120 seconds. Gemma may choose it for a film that needs no vocals.

## Strong at / Weak at
* Strong: a plain English prompt is enough; 44.1 kHz stereo; a 10 s track was made inside a Director film in about 73 s, cold **[recorded 2026-11-01]**.
* Weak: **no lyrics, tempo, language, categories or reprompt**; no voice control; memory grows with length (the VAE decode: about 5 GB for 10-20 s clips, about 12.5 GB at 120 s); quality never compared or listened to **[unknown]**.

## Expect / Do not expect
Expect ambient or instrumental texture from a description. Do not expect vocals, lyric control or exact tempo.

## Modes and settings
| Item | Values |
|---|---|
| Mode | `audio` only |
| Duration | 10-120 s; seed; caption 1-2000 characters (English) |
| Engine path | one ComfyUI prompt from `App/workflows/stable-audio-3-medium/stableaudio3-text.api.json`; the bridge keeps the Music GPU slot until the WAV is published, converts ComfyUI's FLAC preview to WAV without touching a sample and reads the real rate, channels and length |
| GPU claim | 8,000 MB up to 30 s, 10,000 up to 60 s, 14,000 up to 120 s (an estimate; the peak is measured at acceptance) |

## How to ask it
English: **genre, instruments, mood, energy**. Describe what is heard, not a story. **[sources: the prompt guide in the registry; the format works [recorded]]**

## Measured here **[recorded]**
Short acceptance job in the release's record; Director film of 10 s (2026-11-01): the track "ambient acoustic folk, gentle fingerstyle guitar, soft piano" in a run of 18.5 min; the track sat at about -32 dB RMS in the mix (the plan's -16 dB bed on a track normalised to -16 dB RMS).

## Not verified yet
Long durations, repeatability, quality against the song engines, whether the ambience suits a film's scenes. **[unknown]**

## Where in the code, deeper reports
`App/server/music/comfy_backend.py`, `music_registry.py`, the workflow above. Report: [VisualForge-Music-Generation.md](../../VisualForge-Music-Generation.md) (section "Stable Audio 3 Medium").

## Sources and refresh
Hugging Face (stabilityai/stable-audio-3-medium, Comfy-Org/stable-audio-3), the ComfyUI workflow templates. Refresh on a new Stable Audio release or licence change.


## Automatic preparation and current evidence

[sources/code, 2026-11-06] Gemma translates the casual request into an English audible description (<=1000 characters), distinguishing music, ambience and sound events. The installed Medium workflow still exposes only caption/duration/seed: no lyrics, structured BPM, categories or native reprompt. Reprompt exists in a publisher demo, not this graph. Eight LCM steps/CFG 1 remain unchanged; upstream example samplers and longer durations are not imported. Refresh handles: stabilityai/stable-audio-3-medium, Comfy-Org/stable-audio-3, Medium discussion #7 (reprompt provenance).

[GPU, 2026-11-06] Real Gemma → installed Medium graph: 10.031 s (requested 10), seed 71, 44.1 kHz stereo; finite samples, no full-scale samples. Rain ambience was prepared by real Gemma only, without an additional audio generation. Music/ambience listening, sound-event adherence and maximum-duration memory remain unqualified. [UI, real results] Published files were opened in the current player. See [Audio Preparation](../../VisualForge-Audio-Preparation.md) for evidence and limits.
