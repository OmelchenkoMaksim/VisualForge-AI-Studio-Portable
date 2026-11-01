# Wan 2.2 TI2V-5B (`wan22-ti2v-5b`, Video)

Status: **development draft** from the linked project reports; observations were not rerun for this collection. Source identity reviewed **2026-11-01**; upstream research not refreshed. Primary package: Comfy-Org/Wan-2.2, revision `ee6f4a40737a995bf5818954cfce6d59443b0f04` (`App/server/video/video_registry.py`). **[code]**

Wan 2.2 text/image-to-video, 5B dense model, released 2025-07-28, Apache-2.0, native ComfyUI (weights from Comfy-Org/Wan-2.2, 18.1 GB). **Silent** MP4 at 24 fps. Studio score **5/10**. Marks: [the rules](../README.md).

## What it is for
A clean, simple scene: one clear subject, one action, calm camera. Animating a picture keeps its composition and light. Usually **LTX 2.5 is the better choice** (picture, sound, speech); Wan is the silent alternative.

## Strong at / Weak at
* Strong: nature, animals, water, smoke, fabric; smooth camera; the native 1280x704 size is sharp; image-to-video keeps the source's composition **[recorded 2026-10-30]**. A fox in a winter forest at 1280x704 came out sharp and natural.
* Weak: faces, hands and small distant details, lettering, exact counts; fast or complex motion, several actors, collisions; strongly seed-dependent; 832x480 is softer; a person at 480x832 had blurred hands and face **[recorded]**. The 5B model is lower fidelity than the A14B family **[sources]**.

## Expect / Do not expect
Expect a plausible short silent shot; try several seeds. Do not expect speech, lip sync, first/last frame, character references (none here; use S2V or the character engines), identical subject across edited scenes, or a story that holds late in a long chain.

## Modes and settings
| Item | Values |
|---|---|
| Modes | `t2v`, `i2v` (one start frame, centre-cropped to the chosen size by `Wan22ImageToVideoLatent`: pick the same orientation) |
| One shot | 33 / 81 / 121 frames (up to about 5 s); 4n+1 grid |
| Sizes | 832x480, 480x832 (light), 1280x704, 704x1280 (native "720P"); **512x288 removed**: severe shape artifacts **[recorded]** |
| Steps | preview 12, standard 20, high 30; CFG 5 so the negative prompt is active (your own replaces the built-in list) |
| Long clips | a **chain** of 121-frame segments (10 s to 2 min, `121+120(n-1)` frames, each starts from the exact last frame of the previous), or **edited scenes** 2-30 (a duration is a minimum, 4n+1 frames per scene) |
| Speed-ups | SageAttention; fp8 matmul on Ada/Blackwell (`VISUALFORGE_WAN_WEIGHTS=default` keeps fp16: compare if a result looks soft) |

## How to ask it
* English. Text-to-video ("director" rewrite, 80-140 words): keep subject and action, **up to four cinematic tags first** (time of day, light, colour tone, shot size, angle, composition), visible detail, motion described as a process. Image-to-video: **only the dynamic part** (action and camera), nothing the picture already shows, at most 100 words, camera calm. **[recorded by comparison 2026-10-31, one seed per level]** The rules follow the prompt-extension guide of the Wan 2.2 repository **[sources]**.
* A chain: repeat the full description of the character, place and style in every segment's prompt (the model remembers only the last frame) and **start each with a visible change of pose** (the model freezes in the pose it is given). Scenes: one fixed character description and one style phrase in every prompt, a different framing per shot, no words that pull toward a cartoon.
* A one-line request like "a fox in the snow" invites a pose; asking for movement in the rewrite changed little **[recorded]**.

## Measured here **[recorded]** (RTX 5070 Ti)
| What | Result |
|---|---|
| 832x480, 81 frames, 20 steps | 87 s (+1.5 min cold from the HDD) |
| 1280x704, 121 frames | 615 s, then **176 s** with fp8 (peak 12.2 -> 10.3 GB) |
| Speed-ups on 832x480x121 | SageAttention steps 81.8 -> 66.6 s; fp8 66.6 -> 42.0 s, peak 11.7 -> 7.3 GB |
| 7 scenes, 30.6 s | 374 s (about 12 s per second of film), peak 7.0 GB |
| Chain at 832x480 | about 20 s of work per second; 15 s in 323 s, 30 s in 632 s; 1280x704 about 120 s per second before the speed-ups |
| Chain quality | colours saturate and the shape drifts; late story beats are less reliable; independent scenes avoid propagating the preceding last-frame error, but can vary in identity/style |
Dated 2026-10-30/31.

## Not verified yet
Lettering, collision physics, several characters, non-English requests, 1280x704 with the rewrite, chain and scenes with a rewritten first prompt. **[unknown]**

## Where in the code, deeper reports
`App/server/video/wan_worker.py`, `scene_planner.py`, `video_prompt_enhancer.py`, `video_registry.py`; evidence `Runtime/Video/wan-verification.json` (absent = not tested). Report: [Wan-2.2-Video.md](../../Wan-2.2-Video.md).

## Sources and refresh
Wan-Video GitHub (Wan2.2 README, prompt-extension rules), Hugging Face (Comfy-Org/Wan-2.2), ComfyUI documentation (Wan 2.2 tutorial), third-party reviews. Refresh on a new Wan release.
