# VisualForge documentation

`docs/` is the published documentation: every file here is part of the repository and describes the current code. Local-only material (never published) is `AGENTS.md`, `Support/Docs/`
(the agent navigation map, a dated model-research shortlist and `Legacy/`, the retired handoffs and release notes of the first weeks) and `reports/` (agent-mode audit evidence).
Old reports are scoped evidence of one day, not a health check of the installed copy.

## Using the studio

| Document | What it covers |
|---|---|
| [VisualForge-User-Guide.md](VisualForge-User-Guide.md) | features, folders, logs, recovery |
| [VisualForge-Interface-Guide.md](VisualForge-Interface-Guide.md) | layout, themes, settings menu, deletion of generations, launcher and tray |
| [VisualForge-Interface-Localization.md](VisualForge-Interface-Localization.md) | Russian/English catalogs, resource keys, how server messages are translated |
| [VisualForge-Component-Setup.md](VisualForge-Component-Setup.md) | setup program, Components tab, Gemma sources, pinned downloads |
| [VisualForge-Hardware-Compatibility.md](VisualForge-Hardware-Compatibility.md) | the "Your computer" card and model requirements |
| [VisualForge-Portable-Layout.md](VisualForge-Portable-Layout.md) | folder contract of an installation |

## Image

[Qwen-Image-Edit-2511](Qwen-Image-Edit-2511.md) · [Qwen-Image-2.1](Qwen-Image-2.1.md) · [Z-Image-Turbo](Z-Image-Turbo.md) · [FLUX-3-Klein](FLUX-3-Klein.md) ·
[Image-Converter](Image-Converter.md) (pictures of any format for every network). `image-reliability-ru.md` is the release note of the Image reliability package built by
`tools/build_image_reliability.py`.

## Video

Start with **[VisualForge-Video-Architecture.md](VisualForge-Video-Architecture.md)**: processes, interactions, the Gemma and Whisper roles, invariants, the audit verdict.

| Document | What it covers |
|---|---|
| [LTX-2.5-Video.md](LTX-2.5-Video.md) | the flagship: sound with the picture, 30 s clips, scenario, speech check, measured numbers |
| [Wan-2.2-Video.md](Wan-2.2-Video.md) | TI2V-5B: sizes, chains and scenes, speed-ups |
| [Wan-S2V.md](Wan-S2V.md) | a talking character from a photo and a recording |
| [Character-Animation.md](Character-Animation.md) | Wan Animate 2 and SCAIL-2: a photo repeats or replaces a person in a reference video |
| [VisualForge-Director.md](VisualForge-Director.md) | the "Режиссёр" entry: a whole film from a description and optional materials (heroes, places, objects, reference videos, songs, voices): plan by Gemma, keyframes, scenes, voice-over, music, assembly, soundtrack |
| [Video-Model-Ratings.md](Video-Model-Ratings.md) | 0-10 scale (10 = a real camera) and the evidence |
| [Gemma-Clip-Review.md](Gemma-Clip-Review.md) | Gemma watches a finished clip and writes a corrected prompt |
| [VisualForge-Video-Export.md](VisualForge-Video-Export.md), [VisualForge-Audio-Mixer.md](VisualForge-Audio-Mixer.md) | export presets for social networks, music under a clip |

Historical evidence of the first FreeVideo H3 integration (24 Oct 2026, Russian): `video-integration-ru.md`, `video-validation-ru.md`, `video-cache-verification-ru.md`.
`freevideo-model-catalog.json` and `video-dependency-lock.json` are not prose: `tools/build_catalog.py` reads them to regenerate the component catalog.

## Music, voice and text

[VisualForge-Music-Generation](VisualForge-Music-Generation.md) · [MiniMax-Music3](MiniMax-Music3.md) · [VisualForge-Voiceover-Guide](VisualForge-Voiceover-Guide.md) ·
[VisualForge-Voice-Input](VisualForge-Voice-Input.md) · [VisualForge-Text-Chat](VisualForge-Text-Chat.md) · [Gemma-Screenshot-OCR](Gemma-Screenshot-OCR.md) ·
[OvisOCR3-Local-Evaluation](OvisOCR3-Local-Evaluation.md) · [VisualForge-Agent-Mode](VisualForge-Agent-Mode.md) (OpenCode, Aider, DeepSeek Harness in the Text tab).

## Platform

[VisualForge-GPU-Coordination](VisualForge-GPU-Coordination.md) (one card, one holder, the global lock and Stop) ·
[VisualForge-Event-History](VisualForge-Event-History.md) (operation types) · [VisualForge-Generation-Identity](VisualForge-Generation-Identity.md) (the four-field identity) ·
[VisualForge-Source-Layout](VisualForge-Source-Layout.md) (file naming, entry points, what a rename must update).

## Writing documents here

One topic per file; a number goes with its date, hardware and what was *not* measured; a document that describes a measured result says where the evidence lives. Do not copy a published
document into `Support/Docs` (the copies went stale within days), and do not put session diaries here: the git history and `Support/Docs/Legacy` hold those.
