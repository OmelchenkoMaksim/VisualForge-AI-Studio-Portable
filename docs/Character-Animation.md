# Wan Animate 2 and SCAIL-2: your photo in a reference video

Both models take a **reference video** (the motion), **one photo of a person** and a **text**. They render the person doing what the video
shows. Added to the Video section on 2026-10-31; everything below was measured on an RTX 5070 Ti 16 GB, 62 GB RAM, the models on a SATA HDD.

| | Wan Animate 2 | SCAIL-2 |
|---|---|---|
| Modes | `animate`: the photo performs the motion; the background and the camera come from the text | `replace`: the person in the video becomes the person of the photo, the rest of the video stays; `animate`: the photo performs the motion in the photo's own scene |
| Weights | `wan_animate_2_distill_int8_convrot` (16.7 GB, 10 steps) and `wan_animate_2_int8_convrot` (16.7 GB, 6 steps with the speed LoRA) | `wan2.1_14B_SCAIL_2_int8_convrot` (16.7 GB) + DPO LoRA (1.2 GB), speed LoRA (0.7 GB, shared), relight LoRA (1.2 GB, optional) |
| Extra | CLIP vision (1.3 GB), Wan 2.1 VAE (0.25 GB), UMT5 text encoder (shared with Wan 2.2) | the same, plus SAM 3.1 (1.7 GB) that finds the people and objects by a word |
| Based on | Wan 2.2 (Apache-2.0), no pose extraction | Wan 2.1 14B (MIT), no pose extraction |
| Several objects | no | yes: a second picture (for example a car) can replace a second tracked object (a truck) |
| Sources | `Comfy-Org/Wan-Animate-2` @ `e7181fe` | `Comfy-Org/SCAIL-2` @ `fe3c728`, `Comfy-Org/sam3.1` @ `7bb8374` |

"Wan 2.2 Animate" (the first generation) needs a pose-extraction preprocessing chain of third-party ComfyUI nodes, which the studio does not
install; Wan Animate 2 replaced it, works natively in ComfyUI 0.34 and is what the studio offers. The files are in the component list
(`video.model.wan-animate2`, `...-animate2-full`, `...-scail2`, `...-sam3`, shared `...-wan-animate-shared`), with pinned SHA-256 and the usual
mirrors, and are downloaded like every other model.

## What the person does in the panel

1. **Engine** Wan Animate 2 or SCAIL-2, **mode** "Photo + video motion" or "Replace a person in a video".
2. **Photo of the person** and **reference video** (up to 60 s, 64 MiB). The clip is as long as the video, up to the chosen limit
   (3.4 / 6.7 / 10 / 15 / 20 / 30 / 60 s). The sound of the video is kept (a checkbox turns it off). 24 frames per second.
3. A text (Gemma writes it from the photo and a few frames of the video when the checkbox under the field is on).
4. **Fit the photo to the video** (both engines, on by default, see below). SCAIL-2 only: the **word** that finds the person in the photo and in
   the video (`human`, `man`, `woman`; `human:2` for two people), **relight**, and **a second object** (a picture, its word and the word of what it replaces).
5. Sizes: 512x896 (vertical, the default), 896x512, 704x1280, 1280x704. Quality: fast, or "quality" / "maximum" (see the table).

## How a long clip is made

The model renders 81 frames. Segment 2 and later start from the end of the previous one: Wan Animate 2 from its last frame (80 new frames per
segment), SCAIL-2 from its last 5 frames (76 new). The reference video is read on, frame for frame, so the clip never repeats itself and the
motion does not drift off the video. `segment_plan()` gives the lengths (`81, 81, 81, 61` for the 12 s test clip). SCAIL-2 moves the tone of a
continuation to the last frame it continues from (Reinhard transfer in Lab, the step of the official workflow) so the seam is invisible; SAM3
tracks the whole clip once when it has up to 401 frames, otherwise segment by segment.

## The reference photo must have the scale of the video (the main finding)

Both models draw the new person at **the scale the photo shows**. A close-up selfie put into a wide shot, where the person is small, gives a
head three times too large (SCAIL-2 `replace`) or makes Wan Animate 2 slide from the photo's room into the described field and the person walk
out of the frame. Three ways to a good result, from the cheapest:

* **SCAIL-2 `replace`, "fit the photo" on (the default).** SAM3 finds the head in the photo and in the first frame of the video; the photo is
  scaled and moved so that the heads match (`aligned_picture()`, `reference-fit.json` in the job folder). A selfie then works: the face
  carries over and the model draws the missing body from the text.
* **Wan Animate 2, "fit the photo" on (the default).** The same head match, but Wan Animate 2 keeps the whole picture as its starting scene, so a
  smaller photo pasted into a white or blurred frame stayed visible as a rectangle (a room inside the field) for the first seconds. So SAM3 also
  cuts the person out (`human`) and `cutout_picture()` puts only the person, at the video's head size and place, on a plain light-grey ground
  (`reference-used.png`); the place then comes from the text (test #17). Nothing is changed when the photo already has the scale of the video
  (heads within 0.85-1.18 of each other: the photo's room stays, as in #4) or when SAM3 finds no person; the checkbox turns it off.
* **A photo made for the video** with the studio's own image model: FLUX.3 Klein in the Image section, mode "Reference", the first frame of
  the video as the source and the photo as the reference, prompt "Replace the man in the first image with the man shown in the second image ...
  keep everything else". It put the person into the scene of the video at about the right scale in 90 s; Wan Animate 2 and SCAIL-2 both gave
  an almost exact copy of the scene with it.
* **A full-length photo** (or a Klein edit of the portrait to full length) for Wan Animate 2 with "fit" off.

## Prompts (what the hidden Gemma instruction asks for)

* **Wan Animate 2** (model card): two objective texts, no action words: `Character appearance description: ...` (only what the photo shows) and
  `Background description: ...` (place, light, camera), plus the ComfyUI template's third text for the pose branch that tells what the motion in
  the video is. Gemma writes all three from the photo and three frames; the studio shows the character-and-background text and the motion text
  and lets the person edit them. English and Chinese gave the same picture (tested), so the English text is used.
* **SCAIL-2** (model card): one text that describes the **finished** video, not an instruction ("replace", "make" are wrong), long and
  detailed; a replacement text names the clothing of the new person and the objects around (chair, car) and what moves around. Variants:
  `detailed` (70-130 words, the default) and `concise` (40-70 words).
* The appearance always comes from the photo; Gemma is told never to copy the clothing, glasses or hair of the person in the video frames, and
  to take only the pose, the motion, the objects, the place and the camera from them.

## Measured

Reference video: 12 s, 720x1280, 24 fps (a man sits calmly on a camp chair in a field, a ZIL truck jumps over the bumps behind him), the photo
is a close-up selfie unless said otherwise. Seed 7, "fast" quality, 512x896, 81 frames (3.4 s) unless said otherwise. The time is the whole
job with a cold start from the HDD (the model load is about a third of the first segment; a warm segment is shorter).

| # | Engine and setting | Time | Peak VRAM allocated | What came out |
|---|---|---|---|---|
| 1 | Wan Animate 2, selfie | 309 s | 12.1 GB | the face carries over; the first half keeps the photo's room, the field of the text comes late; the person leans out of the frame |
| 2 | Wan Animate 2, selfie, text in Chinese | 203 s | 12.1 GB | the same picture as with the English text |
| 3 | Wan Animate 2, full-length photo (Klein) | 355 s | 12.1 GB | a coherent field scene, but the person drifts out of the frame (the photo's figure is much larger than the video's) |
| 4 | Wan Animate 2, photo made at the scale of the video (Klein) | 329 s | 12.1 GB | an almost exact copy of the scene, the pose and the camera; text on the picture readable |
| 5 | Wan Animate 2, "quality" (full model, 6 steps, speed LoRA) | 539 s | 12.6 GB | close to #4; a third slower, no clear gain |
| 6 | Wan Animate 2, 704x1280 | 614 s | 13.4 GB | sharper; the text on the picture is correct |
| 7 | SCAIL-2 replace, selfie, "fit" off | 513 s | 6.9 GB | scene kept, the head about three times too large |
| 8 | SCAIL-2 replace, photo made at the scale of the video | 311 s | 6.9 GB | almost an exact copy of the scene with the new person |
| 9 | SCAIL-2 replace, selfie, "fit" on | 198 s | 6.9 GB | scene and pose kept, the face carries over, the body below the photo is drawn from the text |
| 10 | #9 with relight | 213 s | 6.9 GB | the light on skin and chain is warm like the evening sun, the person sits in the scene better |
| 11 | SCAIL-2 animate, scene photo | 190 s | 6.8 GB | the person of the photo performs the motion in the photo's scene |
| 12 | SCAIL-2 replace, 704x1280 | 404 s | 9.8 GB | sharper; the text on the picture is almost correct |
| 13 | SCAIL-2 "maximum" (40 steps, cfg 5) | 2013 s | 6.9 GB | no visible gain over the fast mode with relight, twelve times slower |
| 14 | SCAIL-2 replace, the whole 12 s clip (289 frames, 4 segments) | 650 s | 6.8 GB | the whole video with the sound kept: the same face in all segments, the truck, the dust and the plates as in the original |
| 15 | SCAIL-2, a person and a car (5 s, 120 frames, 2 segments) | 273 s | 6.8 GB | the man and the truck are replaced at once: a race Honda Civic jumps in the dust (shape and emblem right, livery only partly) |
| 16 | The same through the panel (2 s, tiny files, Gemma text) | 159 s | - | the whole chain works; the file is saved as `... - SCAIL-2.mp4` |
| 17 | Wan Animate 2, selfie, "fit" on (head match + person cut out on a grey ground) | 212 s | 12.1 GB | the person has the video's scale, sits in the field of the text and moves as in the video; the first frame still has the grey ground, the field takes over within the first second. With the photo pasted into a blurred room instead (first try) the room stayed as a rectangle for 2-3 s |
| 18 | SCAIL-2 replace, selfie, fit and relight on, a 30 s reference (721 frames, 10 segments) | 1812 s | 7.4 GB | the same person in all ten segments, the scene, the truck and the dust of the reference, mean colour (141, 118, 89) constant from the first to the last 5 s, no cut or flash at a seam |
| 19 | Wan Animate 2, selfie, fit on, the same 30 s reference (9 segments) | 1735 s | 12.1 GB | one person and one field for 30 s, the colour constant (mean 196-204), no seam; faint rings in the flat sky, which the distilled model draws in large gradients |

Estimated for a one-minute clip (not run in full): SCAIL-2 at 512x896 about 50 minutes (19 segments of about 155 s), Wan Animate 2 about
55 minutes, at 704x1280 about 2 and 3 hours. A 12 s clip took 11 minutes.

Does the text matter? Compared with the same seed and settings (SCAIL-2 replace, fit and relight on; Wan Animate 2 with a photo made at the
scale of the video): a bare "a man sits in a field", the Gemma text (detailed and concise) and a hand-written text gave **the same scene, the
same motion and the same face**. The text decides only what the photo does not show: the clothing below the photo's frame (the bare text drew
white trousers, "shirtless ... only a silver chain" gave bare legs and shorts, the concise text black trousers) and, for Wan Animate 2 with a
photo of another scale, the background. With a photo that matches the video, the picture of the photo decides almost everything in Wan Animate 2.
So Gemma's value here is controlling clothes and the setting, not the quality of the motion; `detailed` stays the default for SCAIL-2.

## Limits and what to know

* Only one GPU job runs at a time; Gemma (13.7 GB) must leave the card before a video job (the studio does it).
* A job needs about 40 GB of the Windows commit limit (RAM + page file) while the weights are loaded: the page file should be large (see
  `docs/LTX-2.5-Video.md`, "Windows memory limit"). `release_models()` in `video/comfy_memory.py` unloads in 1 GB steps for that reason.
* SAM3 needs `transformers` 5.15 with `tokenizers` 0.23.0rc0 that the Video runtime pins; `accept_cls_keyword()` bridges a renamed argument.
* If SAM3 finds nothing for the word, the job stops with a message instead of silently replacing nothing.
* Text in the video (a watermark, a number plate) comes out slightly distorted; faces stay consistent across segments in the 12 s test.
* Second object: the driving video had a truck in front view, the picture of the car showed it from the side. The result was a race car with the
  right shape, the Honda emblem and the dust, but not the livery of the picture. Pictures taken from the same side work better.
* The 30 s references (#18, #19) are the 12 s clip played forward, backward and forward (the truck drives in reverse in the middle): a stress test of the seams, not a story.
* Not tested: two people replaced at once, a 60 s clip run to the end (about 50 minutes estimated from 4 segments in 11 minutes: the colour
  match and the identity over 19 segments are unmeasured), photos with a strong head rotation, non-human subjects other than the car.

## Files

`App/server/video/character_support.py` (ids, files, plan, probe, validation), `character_input.py` (job inputs), `character_worker.py`
(the job), `comfy_memory.py` (unloading), `prompt_enhancer.py` (guides), `video_registry.py` (engines), `component-catalog.json`,
`tests/test_character.py`, `tests/test_prompt_enhancer.py`.

## 30-second clips (2026-10-31)

Both engines ran a 30 s reference through 9-10 segments. The first SCAIL-2 run failed at the second segment: a clip above 401 frames is tracked by SAM3 segment by segment, but the worker had
deleted the SAM3 model and the reference track right after the first one (`NameError: cannot access free variable 'sam_model'`); the 12 s tests (289 frames) never took that path. They now stay alive
until the last segment when the whole clip is not tracked at once. Timing: about 2.9 minutes per 81-frame segment for both engines at 512x896, 30 min for 30 s; a one-minute clip is about an hour.
