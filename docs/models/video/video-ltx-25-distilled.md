# LTX 2.5 Distilled (`ltx25-distilled`, Video)

Status: **development draft** from the linked project reports; observations were not rerun for this collection. Source identity reviewed **2026-11-01**; upstream research not refreshed. Primary model: Lightricks/LTX-2.5, revision `49b093a39c19169a8a81721dcc541e07e945232f` (`video_registry.py`, `App/config/ltx25-assets.json`). **[code]**

Lightricks LTX 2.5, 22B audio-video model, **Distilled INT8 ConvRot** variant, released 2026-08-11; run through native ComfyUI nodes in an owned worker. Licence: LTX 2.x Community License (read the publisher's terms before distribution). Six files, 41.2 GB, pinned in `App/config/ltx25-assets.json`. Studio score **7/10**: the best of the local engines. Marks: [the rules](../README.md).

## What it is for
Picture **and sound in one pass** at 24 fps: text to video, image to video, first and last frame, audio to video. The engine of the Director's scenes. Sound is made with the picture (ambience, effects, short speech).

## Strong at / Weak at
* Strong: realistic scenes, light, smooth camera, speed (8 steps, no CFG), image conditioning, sound made together with the picture, short Russian and English lines heard word for word by Whisper **[recorded 2026-10-30/31]**; native multishot and IC-LoRA exist in the original but are **not** implemented here **[sources]**.
* Weak: generated sound is quiet (mean -31 to -57 dB, -38 dB in the Director film) and its level is random between seeds; faces and hands at a distance, small text, several characters, long continuations drift (lighting, camera, unwanted text) **[recorded]**; lip sync, speech accuracy in other languages and two speakers are not guaranteed **[unknown]**.

## Expect / Do not expect
* Expect: a photographic look when the prompt gives one style phrase; a person who speaks one short sentence per shot; ambience with visible sources.
* Do not expect: lip-sync-accurate speech, readable lettering, stable identity over many seconds without a start picture, loud mixed sound (the Director levels and mixes it itself).

## Modes and settings in the studio
| Item | Values |
|---|---|
| Modes | `t2v`, `i2v`, `first_last` (both pictures; last one is a guide at frame -1: PSNR about 31 and 28 dB **[recorded]**), `a2v` (a recording up to 5.04 s; the mouth follows it **[recorded]**, one test) |
| One shot | 49 / 97 / 121 frames (2.04 / 4.04 / 5.04 s); frames are 8n+1 |
| Quality | `preview` = one pass of 8 steps; `standard` = 8 steps at half size, latent x2 upsample, 3 refine steps. Director: draft = preview 768x512, standard 1024x576, maximum 1280x704 (both two passes) |
| Sizes | 768x512, 1024x576, 1280x704 and the vertical ones (multiples of 32) |
| Long | continuation 15 / 30 / 60 s = 4 / 8 / 15 windows of 121 frames advancing 96, or **edited scenes** 2-30 (10 s to 2 min) from a Gemma scenario |
| Start picture | resized to cover and **centre-cropped**; strength 0.7 on the first latent frame: give it the film's shape **[recorded 2026-11-01]** |

## How to ask it
* English, **one paragraph of 60-110 words** (i2v: 110-170 keeping the person's specifics): shot type and camera, who or what with a fixed description (age, build, hair, clothes, colours), **one action**, place and light, then the **sound with a visible source** ("paws crunch in the snow", not "tense atmosphere"). Calm camera: static, slow push-in, pan, tracking. No on-screen text, subtitles, logos, no "4K/photorealistic" filler. **[recorded by comparison 2026-10-31; the filler advice is [sources]]**
* A film from several shots: begin **every** prompt with the same ONE style phrase, otherwise a scene came out as a cartoon; avoid cute, magical, glowing, storybook **[recorded]**.
* Speech: the exact words in straight double quotes, the language named ("says in Russian, ..."), a short beat of silence first, **at most 12 words per 5 s shot**; words without quotes become a murmur; 30 words in 5 s were only partly said **[recorded]**. For a speaker without given words write one short sentence per shot.
* Very short requests give a static or wrong subject; very long ones drift to close-ups; the studio's rewrite `concise` (t2v) and `hybrid` (i2v) beat the raw text **[recorded, one subject per level]**.

## Measured here **[recorded]** (RTX 5070 Ti)
| What | Result | Date |
|---|---|---|
| 768x512, 49 frames, fast | cold 385 s, warm 131 s; the same prompt again 23 s (prompt cache) | 2026-10-30 |
| 1024x576, 97 frames, two passes | 92 s | 2026-10-30 |
| 60 s T2V continuation, two passes 768x512 | 825 s, 12.4 GB peak | 2026-10-30 |
| 8 scenes of 30 s with speech | 275 s warm, 594 s cold; both lines word for word | 2026-10-31 |
| Director: 10 s at **maximum** 1280x704, 2 scenes + keyframes + music | 18.5 min in all; about 67 s per second of film in continuations, about 51 in a single shot | 2026-11-01 |
| Director: 60 s draft, 4 scenes of 15 s | scenes 10, 6, 3.5, 3.3 min (first with the cold load) | 2026-11-01 |
| A scene a little above 5 s (a continuation of 4 windows) | about 3.5 times the cost per second of a single 5 s shot | 2026-11-01 |

## Not verified yet
Vertical sizes in the long modes, two-pass quality of long clips, speech in other languages and with two speakers, lip accuracy, whether the sound is pleasant to a human ear (never listened to), films longer than a minute at maximum quality. **[unknown]**

## Where in the code, deeper reports
`App/server/video/ltx_worker.py`, `ltx_long.py`, `ltx_scenes.py`, `scene_planner.py` (LTX rules), `video_prompt_enhancer.py`; evidence file `UserData/Settings/ltx25-verification.json`. Reports: [LTX-2.5-Video.md](../../LTX-2.5-Video.md), [Director](../../VisualForge-Director.md).

## Sources and refresh
Hugging Face (Lightricks LTX-2.5 model card), ComfyUI documentation and workflow templates, Lightricks GitHub (LTX-2 licence), the fal.ai prompting guide, community reports. Refresh when a new LTX version, licence or prompting guide appears: search the model name with those sites and update this card.
