# SCAIL-2 (`scail2`, Video)

Status: **development draft** from the linked project reports; observations were not rerun for this collection. Source identity reviewed **2026-11-01**; upstream research not refreshed. Primary package: Comfy-Org/SCAIL-2, revision `fe3c728bc793ba21ca674688f822afb709ad44fb` (`App/server/video/video_registry.py`). **[code]**

**Replaces a person in a real video with the person of your photo** and keeps the rest (scene, objects, dust, sound); or animates the photo in its own scene. Based on Wan 2.1 14B, MIT, native ComfyUI 0.34, released 2026-06-09 (INT8 ConvRot). SAM 3.1 finds the people and objects by a word. Studio score **7/10**. Marks: [the rules](../README.md).

## What it is for
Putting a new person into an existing real scene. The scene, the objects, the camera and the sound stay real, so the result is as good as the video you give it.

## Strong at / Weak at
* Strong: scene kept, identity stable over a 12 s clip of four segments and over a 30 s clip of ten **[recorded 2026-10-31]**; "fit the photo" (default on) matches the head of the photo to the video's, so a selfie works and the model draws the body from the text; **relight** matches light and shadows; a second object can be replaced (a truck became a race car, shape and emblem right).
* Weak: the new person's body below the photo and the clothes come from the text; background lettering is slightly distorted at 512x896 and readable at 704x1280; the second object's colours may not match when the picture and the video show it from different sides; the "maximum" mode (40 steps, cfg 5) gave **no visible gain** and is twelve times slower **[recorded]**.

## Expect / Do not expect
Expect the same scene with a new person. Do not expect a good result without a clear person the word can find (`human`, `man`, `woman`; `human:2` for two): if SAM3 finds nobody, the job stops with a message. Two people at once and non-human subjects other than an object swap were not tested.

## Modes and settings
| Item | Values |
|---|---|
| Modes | `replace` (the person in the video becomes the photo's person), `animate` (the photo performs the motion in the photo's scene) |
| Weights | `wan2.1_14B_SCAIL_2_int8_convrot` + DPO LoRA, speed LoRA (shared), relight LoRA (optional), SAM 3.1 |
| Quality | standard = 6 steps with the speed LoRA; high = 40 steps, cfg 5 (not worth it) |
| Sizes, segments | as Wan Animate 2; segments of 81 frames, continuation from the last 5 frames (76 new), tone moved to the last frame (Reinhard in Lab) so the seam is invisible; SAM3 tracks a whole clip once up to 401 frames, otherwise segment by segment |
| Extra | a second object: a picture, its word and the word of what it replaces; the word that finds the person (default `human`) |

## How to ask it
**One text that describes the finished video, not an instruction** ("replace", "make" are wrong); long and detailed: name the new person's clothing, the objects around, what moves. Variants `detailed` (70-130 words, default) and `concise` (40-70). The appearance comes from the photo; never copy clothes, glasses or hair of the person in the video. A bare "a man sits in a field" gave the same scene, motion and face; the text decides only the clothes and what the photo does not show **[recorded]**. Best photo: made at the video's scale (Klein "Reference" mode with the video's first frame as source) or the fit.

## Measured here **[recorded]** (RTX 5070 Ti, 2026-10-31)
| What | Result |
|---|---|
| replace, fit on, 3.4 s, 512x896 | 198 s, peak 6.9 GB; with relight 213 s |
| the whole 12 s clip with sound | 650 s, same face in all segments |
| person + car, 5 s | 273 s |
| 704x1280 | 404 s, peak 9.8 GB |
| 30 s reference, 10 segments | 1812 s, 7.4 GB, same person, no seam |
| One minute | estimated about 50 min at 512x896 (not run) |

## Not verified yet
A 60 s clip to the end (identity and colour over 19 segments), two people at once, photos with strong head rotation. **[unknown]**

## Where in the code, deeper reports
`App/server/video/character_worker.py` (`aligned_picture`, `cutout_picture`), `character_support.py`. Report: [Character-Animation.md](../../Character-Animation.md). Known pitfall fixed: SAM3 and the reference track must stay alive until the last segment (a `NameError` from a closure over a deleted model).

## Sources and refresh
Hugging Face (Comfy-Org/SCAIL-2, Comfy-Org/sam3.1 model cards), ComfyUI documentation. Refresh on a new SCAIL release.
