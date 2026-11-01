# Wan Animate 2 (`wan-animate2`, Video)

Status: **development draft** from the linked project reports; observations were not rerun for this collection. Source identity reviewed **2026-11-01**; upstream research not refreshed. Primary package: Comfy-Org/Wan-Animate-2, revision `e7181fe1896b7f2ce34120d1a3663413548240d7` (`App/server/video/video_registry.py`). **[code]**

Your **photo of a person** performs the motion and expression of a **reference video**; the place and camera come from the text. Based on Wan 2.2, Apache-2.0, native ComfyUI 0.34, released 2026-07-14 (INT8 ConvRot, no pose-extraction nodes). Studio score **6.5/10**. Marks: [the rules](../README.md).

## What it is for
A person from a photo in a new place doing what a real clip shows (dance, gesture, walk). The first generation, "Wan 2.2 Animate", needs third-party pose-extraction nodes the studio does not install; this is its replacement.

## Strong at / Weak at
* Strong: the face and hair of the photo are recognisable in every segment; the motion and expression are the video's; the sound of the video is kept; with "fit the photo" a selfie works (head size matched to the video, person cut out on a grey ground, the place comes from the text) **[recorded 2026-10-31]**.
* Weak: **the photo must have the scale of the video** (a close-up selfie into a wide shot makes the person slide from the photo's room into the described place and leave the frame); the first fraction of a second shows the grey ground; faint rings in flat skies from the distilled model; objects and the scene of the video cannot be replaced; the "quality" mode looks like the fast one and is a third slower **[recorded]**.

## Expect / Do not expect
Expect the person of the photo to move like the reference. Do not expect scene replacement (use SCAIL-2), several people, objects swapped, or a good result from a reference with no clear single person.

## Modes and settings
| Item | Values |
|---|---|
| Mode | `animate`; photo, reference video (up to 60 s, 64 MiB), text, "fit the photo" (default on; no change when heads are already 0.85-1.18 of each other) |
| Weights | `wan_animate_2_distill_int8_convrot` (10 steps, standard) or the full model with speed LoRA (6 steps, "quality"); CLIP vision, Wan 2.1 VAE, UMT5 shared; SAM 3.1 finds the person for the fit |
| Sizes | 512x896 (default), 896x512, 704x1280, 1280x704; 24 fps; the clip is as long as the video up to 3.4 / 6.7 / 10 / 15 / 20 / 30 / 60 s |
| Segments | 81 frames; segment 2+ starts from the last frame of the previous (80 new each); the reference is read on frame by frame |

## How to ask it
Two objective texts, **no action words**: `Character appearance description: ...` (only what the photo shows) and `Background description: ...` (place, light, camera), plus a third text for the motion in the video. Gemma writes them from the photo and three video frames; never copy the clothes or hair of the person in the video. English and Chinese gave the same picture. The text matters little when the photo matches the video's scale: it decides only what the photo does not show (clothes below the frame, the background) **[recorded: bare, Gemma and hand-written texts gave the same scene, motion and face]**.

## Measured here **[recorded]** (RTX 5070 Ti, 2026-10-31)
| What | Result |
|---|---|
| 3.4 s at 512x896, fast | 3-6 min (cold), peak 12.1 GB |
| 704x1280, 3.4 s | 614 s, peak 13.4 GB, sharper, text on the picture correct |
| photo made at the video's scale with Klein | an almost exact copy of the scene |
| 30 s reference (9 segments), fit on | 1735 s, one person and one field, colour constant, no seam |
| One minute | estimated about 55 min at 512x896, 2-3 h at 704x1280 (not run) |

## Not verified yet
Clips longer than 30 s to the end, several people in the reference, strong head rotation in the photo. **[unknown]**

## Where in the code, deeper reports
`App/server/video/character_worker.py`, `character_support.py`, `character_input.py`, `comfy_memory.py`. Report: [Character-Animation.md](../../Character-Animation.md).

## Sources and refresh
Hugging Face (Comfy-Org/Wan-Animate-2 model card), Wan-Video GitHub, ComfyUI documentation. Refresh on a new Animate version.
