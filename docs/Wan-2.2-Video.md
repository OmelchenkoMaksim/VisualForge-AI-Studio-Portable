# Wan 2.2 TI2V-5B

Native ComfyUI adapter `wan22-ti2v-5b`, selectable beside FreeVideo in Video.
Released 2025-07-28 according to the official Wan2.2 README.
License: Apache-2.0. Official source: https://github.com/Wan-Video/Wan2.2
Native workflow reference: https://docs.comfy.org/tutorials/video/wan/wan2_2

## Runtime and assets

Reuses the installed isolated Video Python and native ComfyUI 0.34.0 without upgrading its dependencies. It does not use the Image or Music Python environments. Native ComfyUI has no recorded Git checkout here; selected installed component hashes are recorded in `Support/Diagnostics/WanInstallManifest.json`, rather than attributing a fictitious upstream commit.

Weights are from `Comfy-Org/Wan-2.2`, revision `ee6f4a40737a995bf5818954cfce6d59443b0f04`:

- `diffusion_models/wan2.2_ti2v_5B_fp16.safetensors`
- `text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors`
- `vae/wan2.2_vae.safetensors`

These three assets total 18,144,966,705 bytes, installed under `Models/Video/Wan22TI2V5B/`. Download provenance and SHA-256 values are in `Support/Diagnostics/WanDownloadManifest.json`. No second Hugging Face cache is needed. Inference is local and uses the existing offline environment guard.

## Controls and limits

Text-to-video and image-to-video with one initial frame. Silent MP4/H.264 at 24 FPS. One shot is 33/81/121 frames (up to about 5 seconds, the length the model was trained on); 10 seconds to two minutes are either a continuous chain of segments or an edit of independent scenes (see Long clips). Sizes: 832×480 and 480×832 (light), 1280×704 and 704×1280 (the model's native "720P" size, multiples of 32). 512×288 was removed on 2026-10-30: it gave severe shape artifacts and is far below the training size. An optional negative prompt replaces the built-in list (CFG 5 keeps the negative branch active). Hidden modes: this engine implements neither first/last conditioning (only the separate A14B family has it), character cards, speech nor lip sync, so the mode list shows only Text and Image.

Start with 832×480, 81 frames, standard 20 steps. For image-to-video the source is center-cropped to the selected size by `Wan22ImageToVideoLatent`; choose the same orientation. Preview uses 12 steps, high uses 30.

Measured before the speed-ups below, on an RTX 5070 Ti 16 GB (20 steps, warm load from an HDD adds about 1.5 minutes): 832×480×81 in 87 s, ×121 in 126 s, image-to-video ×81 in 85–97 s, 480×832×81 in 90 s, 704×1280×33 (preview) in 62 s, and 1280×704×121 in 615 s with 12.2 GB peak allocated VRAM. Cancelling at step 4 of 30 ended the worker within seconds with no leftover process.

The model selector's **Strengths and limitations** block (Video tab) summarizes the audit. It is written from the official model card, the ComfyUI template, third-party reviews (the 5B model is described as lower fidelity than A14B) and our own samples. In our samples the native-size fox scene was sharp and natural, while a person at 480×832 had blurred hands and face. No statement covers lettering, collision physics or several characters: they were not tested. Windows x64 and NVIDIA CUDA are required. 16 GB VRAM and 32 GB RAM are the tested start; native offload uses RAM between GPU stages, followed by tiled VAE decoding. ComfyUI advertises 8 GB support, which is not a local verification.

## Speed-ups (2026-10-30)

Profiling a 7-scene job (`segment_timing` events in the job log) showed that weight loading was already cheap and almost all time was sampling (about 67 of 86 s per 105-frame scene) plus about 14 s of VAE decode and FFmpeg writing. Three changes followed, each measured on an RTX 5070 Ti with the same seed and checked by eye on the frames:

- **SageAttention** (installed with the Video runtime, used when importable; `VISUALFORGE_WAN_ATTENTION=pytorch` forces the old path): sampling steps 81.8 -> 66.6 s on 121 frames at 832x480. Outputs are not bit-identical (the sampler trajectory diverges, PSNR about 17 dB) but composition and sharpness are the same.
- **fp8 matrix multiplication** (`fp8_e4m3fn_fast`, only on compute capability 8.9+, that is Ada/Blackwell; `VISUALFORGE_WAN_WEIGHTS=default` keeps fp16): steps 66.6 -> 42.0 s, peak VRAM 11.7 -> 7.3 GB at 832x480. At 1280x704 the whole clip fell from 615 s to 176 s (peak 12.2 -> 10.3 GB) because the weights no longer need RAM offload. Same composition and sharpness class in side-by-side frames; this is the one change that could cost fine detail, so compare if a result looks softer than expected.
- **Model built once per job**: the checkpoint was re-read and converted for every segment (about 4 s). The patcher now stays in RAM between segments; a repeat run was bit-identical to the one that rebuilt it.

Tried and dropped: larger VAE decode tiles (1024 px / 32 frames) were slower (18.5 s vs 16.5 s). Remaining per-scene cost at 832x480: about 35 s of steps and 14 s of decode and encode.

## Long clips (10 seconds to two minutes)

The model makes about 5 seconds at a time, so a long clip is a chain of 121-frame segments (2, 3, 6 or 12) generated one after another in the same worker process. Nothing needs a separate joining program: every segment starts from the exact decoded last frame of the previous one (floating point, before 8-bit rounding), drops its own first frame because it repeats that conditioning frame, and streams its frames into one FFmpeg encoder. The result is encoded once, so there are no concat seams and no extra re-encode generation. `n` segments give `121 + 120 * (n - 1)` frames; the last one is trimmed to the requested total, and each segment gets its own seed (`seed + index`).

Re-using one prompt for every segment would repeat the same action, so the Text section's Gemma (`App/server/video/scene_planner.py`, route `/api/video/plan`, event type `VideoPromptPlanning`) first splits the idea into one English prompt per segment. Each prompt repeats the same description of the character, place and style, because the video model remembers nothing but the conditioning frame, and starts with a visible change of pose, because the model tends to freeze in the pose it is given. With a source image the picture is attached (scaled to 1024 px) so the scenario matches it. The scenario is shown as editable fields; if it is missing or was made for a different idea, segment count or source frame, it is written when generation starts. Gemma runs before Wan: the GPU coordinator unloads it when the Video job claims the card. The request validator requires exactly one prompt per segment and rejects them for short clips.

Measured on an RTX 5070 Ti 16 GB at 832×480, 20 steps: 15 s (3 segments) in 323 s, 30 s (6 segments) in 632 s with 11.8 GB peak allocated VRAM, 10 s from a picture in 219 s, i.e. about 20 s of compute per second of video; 1280×704 costs about 120 s per second (a minute takes about two hours). Joins were seamless in all three runs. Quality is the limit: colors saturate and the subject's shape drifts from segment to segment, the model reluctantly leaves the pose of its first frame (a stronger "change of pose" rule in the scenario prompt made the second 15 s test follow the story clearly better than the first), and in the 30 s boat story the storm segments partly lost the boat and the harbor never appeared. Treat late story beats as less reliable than early ones; shorter chains and calm scenes work best.

### Edited scenes

The second way to get a long clip is the "Edited scenes" structure: the person chooses the number of scenes (2-30) and a minimum duration (10, 15, 30, 60 or 120 seconds). Each scene is its own shot of the shortest length that reaches the minimum, between 33 and 121 frames, always a 4n+1 count because of the video model's temporal grid. Seven scenes for 30 seconds are 7 x 105 frames = 30.6 s, so the duration is a minimum, not an exact value (`scene_frames()` in `video_registry.py`; five scenes would need 144 frames per scene and are refused). The scenes are generated one after another in the same process and streamed into the same single FFmpeg encode with hard cuts, so nothing is re-encoded or lost at the joins. Only the first scene may start from a source picture.

Gemma writes one self-contained prompt per scene (`SCENES_SYSTEM` in `scene_planner.py`): the same fixed description of the character and one style phrase at the start of every prompt, a different framing per shot, and no words that pull the picture toward a cartoon. All scenes share the job's seed. In a 4-scene comparison this kept the fox's fur and scarf slightly more alike than a seed per scene; that is a small sample, not a guarantee.

Measured at 832x480, 20 steps, with the speed-ups: 7 scenes (30.6 s) in 374 s (about 12 s per second of video, peak 7.0 GB), against 606 s before them. The film followed the story and kept the red scarf for five scenes; the two night scenes drifted toward a cartoon style. Compared with one continuous chain, scenes never accumulate distortion but do not keep an identical subject. Cancelling in scene 1 of 7 stopped the worker and its FFmpeg child.

## Jobs, caches and portability

The bridge keeps its shared GPU submission lock across Image, Video and Music. Each Wan generation is one owned worker process; cancellation terminates only that worker and its descendants, including its FFmpeg child. It does not enumerate or terminate unrelated GPU users. The model process exits after each job, releasing VRAM.

Wan job scratch, source frames and compiler caches are under the current Windows user's Temp directory, identified by a hash of this installation's UserData path. The installed application contains no developer staging-path dependency. A moved installation recomputes these paths. Model paths are relative to the installation; a deliberate `VISUALFORGE_WAN_MODELS` override is for staging only.

Completed output follows existing Video settings, meaningful unique filenames, metadata sidecars and history. External absolute output directories remain absolute. A bounded 48 KiB log tail per job is retained under `UserData/Logs/Video/Wan/`. The log viewer uses that saved tail after completion; detailed temporary files remain on C. No automatic bulk cache deletion is performed.

## Evidence

`Runtime/Video/wan-verification.json` records exact GPU-tested modes, settings, timing and decoded output properties. An absent verification file means NOT TESTED, even if assets exist. The interface displays this scoped evidence. A short successful clip does not verify larger profiles, visual consistency, long videos or S2V.

Targeted CPU checks are in `tests/test_wan.py` (modes, sizes, negative prompt, assets, bounded logs), alongside existing Video ownership/storage checks. No Image GPU regression suite is required for this adapter-only change.

## Prompt enhancement by Gemma (2026-10-31)

The panel can rewrite the request with Gemma before generation, for text-to-video and image-to-video (`App/server/video/prompt_enhancer.py`, route `/api/video/enhance`; the block hides when
a chain or a scene plan is active, because the planner already writes those prompts). The person's own words stay in the history; the rewritten text is shown and editable.
The TI2V-5B card has no prompt guide of its own, so the instructions restate the English prompt-extension rules the Wan 2.2 repository ships for its A14B models (`wan/utils/system_prompt.py`):
text-to-video: a "film director" who keeps the subject and the action, puts up to four cinematic tags first (time of day, light source and quality, colour tone, shot size, camera angle, composition),
adds visible detail, describes motion as a process and writes 60-200 words; image-to-video: only the dynamic content (the action and the camera), nothing the picture already shows, at most 100 words.

Test: 832x480, 81 frames, "standard", seed 11, RTX 5070 Ti, three request levels (5 / 16 / 62 words); motion is the mean frame difference.

| Variant | Words | Short request | Medium request | Detailed request |
|---|---|---|---|---|
| raw (no rewrite) | - | motion 6.3, fox walks but the picture is overexposed | motion 9.5, the fox is a speck in the forest | motion 6.1, the fox is small and far |
| director (the default, 80-140 words) | ~120 | motion 4.0, wide shot, the fox mostly sits and turns its head | motion 8.6, close tracking shot, fox trots through blue-and-gold snow | motion 5.8, close low-angle shot, the rim light and the film grain of the request are kept |
| concise (40-70 words) | ~55 | motion 4.6, the fox walks | motion 11.7, a wide scene, the fox walks away | motion 5.9, the fox runs out of the frame |

Verdict: `director` is the default for text-to-video: the subject stays large and in the frame at every level and the specifics of a detailed request survive. `concise` gives more motion for one-line
requests; it stays selectable through the `variant` field. An added rule that the subject must move did not change the short request much (4.0 against 3.4): "a fox in the snow" invites a pose.

Image-to-video (the selfie of the Character tests as the first frame, three requests of 2 / 15 / 43 words):

| Variant | Words | Result |
|---|---|---|
| raw | - | short: a smile; medium and detailed: the head and the hair change a lot, the hand fills the frame (motion 10-11) |
| motion (the default, at most 100 words, the camera "calm and steady") | ~48 | the same actions, the face and the background stay put, a clean wave in the frame (motion 4.9 / 9.0 / 8.4) |
| brief (at most 45 words) | ~23 | the strongest motion (6.9 / 20.3 / 9.2) but the hand covers the face on the medium request and the framing drifts |

Verdict: `motion` is the default for image-to-video. One seed and one picture per level, judged by contact sheets and motion; hair and background drift a little with every variant, so this is not a measure of identity.
Not tested: non-English requests, 1280x704, the chain and the edited scenes with a rewritten first prompt.
