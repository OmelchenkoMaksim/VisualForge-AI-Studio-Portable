# Video models: which one for what

Evidence: [card rules](../README.md). Section comparisons are draft summaries of the linked cards/reports; **[recorded]** means historical evidence, not a new test. Source/config is authoritative; availability still depends on installation and policy.

| Engine (card) | Id | Makes | Sound | Size, fps | Score | Choose it when |
|---|---|---|---|---|---|---|
| [LTX 2.5 Distilled](video-ltx-25-distilled.md) | `ltx25-distilled` | text, picture, first+last frame or recording to video | **yes, made with the picture** | up to 1280x704, 24 | **7** | the default: the best picture and light, speech in short shots, long films from scenes |
| [Wan 2.2 TI2V-5B](video-wan-22-ti2v-5b.md) | `wan22-ti2v-5b` | text or picture to video | none | up to 1280x704, 24 | 5 | a silent, simple scene (one subject, one action) when LTX is not wanted |
| [Wan 2.2 S2V-14B](video-wan-22-s2v-14b.md) | `wan22-s2v-14b` | a photo + speech to a talking person | the speech given | 512x512 / 832x480, 16 | 5 | a talking head from one photo and a voice |
| [Wan Animate 2](video-wan-animate-2.md) | `wan-animate2` | your photo performs the motion of a reference video | kept from the video | up to 1280x704, 24 | 6.5 | a person of a photo in a new place with real motion |
| [SCAIL-2](video-scail-2.md) | `scail2` | replaces a person in a real video with your photo (or animates it) | kept from the video | up to 1280x704, 24 | 7 | the scene must stay real and only the person changes |
| [FreeVideo H3](video-freevideo-h3.md) | `freevideo-h3-int8` | text, picture, first+last, characters | yes | 512x288 only, 24 | 4 | compatibility with the legacy CLI path |
| the Director | `director` | a whole film from a description and materials | mixed | LTX scenes | n/a | not a model: a producer that uses LTX, Klein, the music engines and the voices ([doc](../../VisualForge-Director.md)) |

## Rules that hold for every video engine here

* **Prompts are English** (Gemma writes them); spoken or lettered words stay as written, in straight double quotes, with the language named. Every engine has a hidden Gemma guide (`video_prompt_enhancer.py`); H3 has none. **[recorded 2026-10-31]**
* **Frame grids differ**: LTX 8n+1 (49/97/121 per shot), Wan 4n+1 (33/81/121), Wan S2V 77, H3 17n+5. Sizes are multiples of 32. Never apply one engine's grid to another.
* **A start picture is resized to cover the frame and centre-cropped.** Give the engine a picture of the shape of the film; a portrait photo in a landscape clip shows only its middle band (found with LTX in the Director, 2026-11-01 **[recorded]**; Wan documents the same centre crop).
* **Long clips**: a continuation is one take of overlapping windows and drifts (colour, shape, lighting); edited scenes are independent shots joined with cuts and do not accumulate errors but do not keep an identical subject. For stories prefer scenes, for one long camera move the continuation. **[recorded]**
* **Speech**: LTX says short lines (at most about 12 words in a 5 s shot) and Whisper hears them word for word; a speaker without quoted words becomes a murmur. Wan TI2V has no sound; S2V's sound is the recording itself. Lip sync is never guaranteed. **[recorded]**
* **Time per second of film on this machine** (cold loads excluded; scenes are shot one after another): LTX preview about 14 s, standard about 27-30 s, maximum about 51-67 s per second of film **[recorded]**; Wan TI2V about 12-20 s (832x480, fp8, SageAttention); character engines about 2.9 minutes per 81-frame segment at 512x896. A single shot of up to 5 s costs far less per second than a scene in continuations.
* **Results are random from seed to seed** (picture, motion, the loudness of generated sound): judge with several seeds and with measured numbers (frame difference, `ffprobe`, loudness), not one clip.
* The GPU memory peaks: LTX 12.4 GB, Wan TI2V 7-12 GB, S2V 10.6 GB, Wan Animate 2 12-13 GB, SCAIL-2 7-10 GB. Gemma must leave the card first (the studio does it).

## When you need to check or extend this section

Run the engine through the UI (Video tab) and a short real job, judge a contact sheet of frames plus numbers, write the result into the card with the date and the mark. `tools/video_smoke.py` runs one short real job per engine through the studio's API (about 25 minutes; needs the owner's yes and `--yes-gpu`). Rules for adding an engine: [the Video architecture](../../VisualForge-Video-Architecture.md).
