# Wan 2.2 S2V-14B (`wan22-s2v-14b`, Video)

Status: **development draft** from the linked project reports; observations were not rerun for this collection. Source identity reviewed **2026-11-01**; upstream research not refreshed. Primary package: Comfy-Org/Wan-2.2, revision `ee6f4a40737a995bf5818954cfce6d59443b0f04` (`App/server/video/video_registry.py`); shared repository does not make TI2V/S2V interchangeable. **[code]**

Wan 2.2 speech-to-video, 14B, released 2025-08-26, Apache-2.0, native ComfyUI (the three new weights are 17.3 GB; the UMT5 encoder is shared with TI2V). A **photo + speech** (+ a visual prompt) becomes a person who talks or sings. Studio score **5/10**. Marks: [the rules](../README.md).

## What it is for
One person talking or singing to the camera from one photo and a voice: the speech drives lips, expression, head and body, gestures follow loudness and rhythm; the prompt sets camera, place and the manner of delivery.

## Strong at / Weak at
* Strong: a believable face and mouth following the speech; head and hand gestures; the face, room and colour stay the same across scenes **[recorded 2026-10-31]**; the sound is the recording itself (Whisper returned the text word for word).
* Weak: fixed 16 fps; **the scenes structure makes hard jump cuts** (each scene restarts from the reference pose; the requested camera angle is hardly carried out: seven cuts in a 30 s test, all the same medium close-up) **[recorded]**; the first frame is smeared in every run; long chains reportedly lose lip sync near a minute **[sources]**; the audio encoder is English-trained, lip accuracy in other languages is not established **[unknown]**; the speed LoRA is not installed (it removes the motion).

## Expect / Do not expect
Expect a talking head, 5-30 s, mouth in time with the recording. Do not expect camera work, a walk, several speakers, music mixing, exact voice similarity of a cloned voice, or long clips without cuts.

## Modes and settings
| Item | Values |
|---|---|
| Inputs | one reference PNG, a visual prompt, **either** a voice sample (2-30 s) + a short text that Qwen3-TTS speaks before the video model loads, **or** finished speech (0.1-4.8125 s one shot; up to 75 s in the scenes structure; at most 8 MiB) |
| Sizes | 512x512, 832x480, 480x832 |
| Fixed | 77 frames (4.8125 s) per segment, 16 fps, 20 steps, CFG 6, shift 8, UniPC; shorter speech is padded with silence, longer is refused in the one-shot mode |
| Long clips | the **scenes** structure: each scene at most 4.8125 s, cut at the quietest 0.2 s of the speech; the **chain** was tried and removed (a tenth slower, identity drifted: hair lightens, face thins) **[recorded]** |

## How to ask it
The text is optional. Describe the person **as the photo shows**, how they deliver the speech (look into the camera, small head movements, hand gestures), the place, the shot size, a steady camera; **never write the words, the sound or the lips**. The studio's default rewrite `scene` (30-60 words) gave more head movement and gestures than the raw text or a 20-word one, same face, room and sync **[recorded, one photo, one voice, one seed]**. The scenes planner repeats the description of person and place in every prompt and changes only distance, angle and gesture.

## Measured here **[recorded]** (RTX 5070 Ti)
| What | Result |
|---|---|
| 512x512, 77 frames, standard | 349 s, then **242 s** with SageAttention (same picture); 4-6 min per clip from a cold start |
| 832x480 | about 6.5 minutes, peak 10.6 GB |
| 21.4 s Russian speech, scenes (5 scenes) | 1115 s; chain 1222 s |
| 31.4 s English recording, 7 scenes | 1952 s (32.5 min), 32.12 s, 514 frames, text word for word, seven jump cuts |
Dated 2026-10-30/31.

## Not verified yet
Speech longer than 22 s in the scenes structure other than the 31 s test, a cloned voice in a long clip, singing, 832x480 and vertical clips in the scenes structure, several speakers, lip accuracy by a human eye. **[unknown]**

## Where in the code, deeper reports
`App/server/video/s2v_worker.py`, `s2v_audio.py`, `scene_planner.py` (talking mode), `video_prompt_enhancer.py` (mode `s2v`); evidence `Runtime/Video/s2v-verification.json`. Report: [Wan-S2V.md](../../Wan-S2V.md).

## Sources and refresh
Wan-Video GitHub (Wan2.2 README), Hugging Face (Wan-AI/Wan2.2-S2V-14B, Comfy-Org/Wan-2.2), ComfyUI documentation (Wan 2.2 S2V tutorial), user tests of long clips (MultiTalk and InfiniteTalk are reported to be steadier over minutes). Refresh on a new Wan or a talking-head model.
