# LTX 2.5: direct local video and audio

Status: GPU acceptance passed for T2V Fast and I2V Two-pass at 768x512, 49 frames, 24 fps on RTX 5070 Ti 16 GB / 64 GB RAM. Other listed profiles remain experimental.

## What is integrated

`ltx25-distilled` in Video runs the official LTX 2.5 Distilled INT8 ConvRot model through native ComfyUI nodes in an owned Video worker.
It supports text-to-video and one initial image, 24 fps, 49/97/121 frames, landscape and portrait profiles. The model generates audio with video.
Its output is a normal Video history item and works with the existing mixer and delivery exporter.

Two explicit modes:

- **Быстрое**: one 8-step pass at the requested size, using the smaller convolutional video VAE.
- **Два прохода**: the published 8-step schedule at half width/height, learned latent x2 upsampling, a 3-step refinement pass and the diffusion video VAE.

The second mode performs neural generation/refinement; the later social-media exporter remains a separate resize/encode operation.
Do not call the exporter an LTX enhancement stage. Speech accuracy, identity consistency and lip synchronization are not guaranteed.
Start at 768x512, 49 frames (2.0417 seconds). Untested combinations remain experimental.

## Runtime and ownership

No WanGP UI, service or source is required. `video/ltx_worker.py` uses the existing private Video Python and native ComfyUI modules, without changing their packages.
Imports of the required modules were executed in CPU mode. Their execution signatures match the worker.
The worker starts only after shared GPU admission; it is attached to the existing Windows Job object and exits after the job.
Global Stop and module cancellation therefore retain exact process ownership.
Other models are released by the existing coordinator before admission.

An owned child process encodes the prompt and writes CPU conditioning in the job scratch directory. It exits before the main worker imports ComfyUI and loads the video model. The private artifact is read with torch.load(weights_only=True). This process boundary avoids the native Windows/PyTorch crash observed when unloading Gemma and loading LTX in the same process. Disabling mmap alone did not fix that crash; it is not enabled as a workaround.
ComfyUI offloads model weights to RAM with 2 GiB VRAM reserved. PyTorch attention is used initially; additional acceleration requires separate measurement.
Compiler/HF caches and job intermediates use the existing C Temp scratch path. Weights remain under `Models/Video/LTX25`.
The process is offline: the existing offline guard and offline library environment apply. No network fallback or automatic model download occurs during generation.

## Model provenance and setup

`App/config/ltx25-assets.json` is the pinned source of file names, sizes, SHA-256 and official download URLs.
Repository: `Lightricks/LTX-2.5`, revision `49b093a39c19169a8a81721dcc541e07e945232f`.
Six files total 41,162,142,158 bytes: distilled INT8 transformer, the LTX-tuned Gemma 4 encoder, two video VAEs, audio VAE and latent spatial upsampler.
The studio's Ollama chat model cannot replace the LTX-specific encoder.

Component `video.model.ltx25` requires `video.video_runtime`; it is optional and listed in Components.
`tools/build_catalog.py` rebuilds its entry from the asset manifest without refreshing model pins.
The official Hugging Face repository requires the owner to accept its model terms and supply a read token as `HF_TOKEN` while downloading.
The existing stdlib downloader resumes transfers, checks SHA-256 and strips credentials when following redirects to other hosts.
Generation needs neither a token nor a Hugging Face account after files are installed.
Keep tokens out of Git, command-line arguments, logs, receipts and diagnostics.

License: [LTX 2.x Community License](https://github.com/Lightricks/LTX-2/blob/main/LICENSE-2_x); consult the exact terms at the pinned publisher repository before distribution or commercial decisions.
ComfyUI retains its own license. The studio does not relabel third-party code or weights as its own.

## Output checks

The worker streams RGB to the bundled FFmpeg and muxes the actual generated waveform into MP4/H.264 + AAC, 48 kHz stereo.
It rejects malformed/non-finite video or audio, wrong geometry/frame count, and audio that does not cover the generated clip.
The service independently probes the final MP4 and requires an audio stream of matching duration before publication.
Intermediates never replace existing user media. Inference settings and source revision are recorded in the job scratch directory.

## Evidence

- CPU: profile validation, independent size-aware readiness, adapter isolation, pinned catalog assets and required-audio checks.
- CPU: native LTX-node import/signature probe in the actual Video runtime.
- CPU: synthetic 49-frame media exported with the actual worker exporter and bundled FFmpeg; independently probed H.264, 49 frames, 24 fps, AAC 48 kHz stereo.
- GPU: real T2V Fast and I2V Two-pass both produced H.264, 49 frames, 768x512 at 24 fps, with AAC 48 kHz stereo covering 2.041667 seconds.
- T2V observed inference: 349.67 s, peak PyTorch allocation 12489 MiB; I2V: 109.11 s, 12359 MiB. These include different disk-cache states and possible concurrent StarCraft II; they are not controlled speed benchmarks or total GPU-memory measurements.
- Four-frame contact sheets inspected for both clips; visible bell motion and coherent scene. T2V audio is non-silent (mean -31.3 dB, peak -14.3 dB); semantic audio quality was not listened to.
- Cancellation during encoder loading passed with the owned parent/child tree. Sampling cancellation and full installed EXE launch were not separately exercised.
- Exact local evidence is stored in UserData/Settings/ltx25-verification.json. Speech/lip accuracy, other dimensions and longer durations remain unverified.

Sources: [official weights](https://huggingface.co/Lightricks/LTX-2.5),
[official ComfyUI T2V workflow](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_ltx2_5_t2v.json),
[official I2V workflow](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_ltx2_5_i2v.json).


## 15 / 30 / 60 second continuation

These durations are one scene extended through bounded overlapping AV windows, not a single 60-second diffusion pass.
The owned `ltx_long.py` runner encodes the prompt once. Each window runs in a fresh owned video process and reads the same CPU conditioning with `weights_only=True`.
Windows contain at most 121 native frames, advance by 96 frames and share 25 frames of context.
The next window freezes the previous generated video/audio latent prefix; the two-pass profile retains both low-resolution and refined video context.
The uploaded initial image is used only for the first I2V window. The sampler seeds advance for each continuation.

Video and audio are crossfaded only in the shared context. The final causal-grid extra frame is removed, giving exactly
360 / 720 / 1440 output frames at 24 fps and exactly 15 / 30 / 60 seconds of AAC 48 kHz stereo.
There are 4 / 8 / 15 windows respectively. Quality settings apply separately to each window; runtime grows with duration.
Models remain on the portable drive; private conditioning, latent tails and intermediate clips stay in owned C scratch.
Existing module/global cancellation owns the entire parent/child process tree.

Continuation may change lighting, details, objects or sounds over time. Speech spanning multiple windows is not guaranteed.
The runtime always checks final frame count, dimensions and audio duration before saving a user result.

Validation: real CPU assembly for all three durations; AV-prefix mask and stage-context checks; I2V orchestration and single-encoder checks.
GPU acceptance evidence is recorded separately in `UserData/Settings/ltx25-verification.json`.

GPU continuation acceptance: full 60-second T2V Two-pass at 768x512, 1440 frames, 24 fps, generated audio; 824.97 seconds observed, 12407 MiB peak PyTorch allocation. Possible concurrent game activity makes timing observational.
15/30 second acceptance reused the identical generated prefix and sampled fresh 73/49-frame terminal windows, then independently checked exact final video/audio durations.
Long I2V and the long Fast profile were not separately GPU-tested. Sampled scene remains coherent but lighting/camera drift and unwanted generated text were observed. Semantic sound quality was not listened to.

## Audit and changes (2026-10-30)

Research (model card, ComfyUI documentation, the fal.ai prompting guide, user reports): LTX 2.5 is a 22B audio-video model; the distilled variant runs 8 steps without
CFG; frames must be 8n+1 and sizes multiples of 32. Strong: realistic scenes and a smooth camera, sound made in the same pass, speed, native multishot,
image conditioning, audio-to-video, video-to-video, IC-LoRA control and keyframes in the original. Weak: audio is quieter and less mature than the picture, speech
articulation and language are not guaranteed, small text, hands and faces at a distance, several characters, prompt-dependence (one paragraph, up to about 200 words,
sound named with a source, dialogue in quotes with the language and a beat of silence first).

What the studio had: text, image and continuation to 15/30/60 s; no assessment block; modes of the original that are not implemented here (video-to-video, IC-LoRA, keyframes,
retake) are not shown. Measured before the changes (RTX 5070 Ti 16 GB, 768x512, 49 frames, fast): 385 s from a cold disk, 131 s warm, of which about 87 s is reading the 15 GB
text encoder; sampling itself is 8 steps in about 30 s; the picture is excellent, the generated sound is quiet (about -57 dB mean).

Changes:

- **Prompt conditioning cache** (`ltx_worker.conditioning_cache`): the text conditioning is stored beside the job folders under the hash of the prompt, the negative text and the
  encoder file; a repeat of the same prompt (another seed, size or length) skips the encoder child process: 23 s for the whole 49-frame clip instead of 98-131 s. The 40 newest
  entries are kept. Continuation windows already encoded once per job.
- **Audio -> video** (`mode: a2v`, panel: "Audio -> video · LTX"): the recording (up to 121 frames = 5.04 s, shorter ones are padded with silence, longer ones are
  rejected; prepared by `ltx_audio.py` as 48 kHz stereo PCM) is encoded by the audio VAE and frozen in the audio latent with a zero noise mask in both passes, so the sampler
  fits picture and lips to it; the output carries the original recording, not a vocoder copy. A picture is optional and becomes the first frame. No continuation windows.
- **Strengths/limits block and prompt guide** for LTX (`video.assessment_ltx_*`, the prompt guide above).
- SageAttention was measured and left out: 8 sampling steps took 7.3 s with and without it (1024x576, 97 frames, two passes).
- `step`/phase events carry `elapsed`.

Measured after: 1024x576, 97 frames, two passes 92 s; Audio -> video with a 3.9 s Russian recording and a portrait, 97 frames, 153 s (cold prompt), the mouth opens on the
words and the sound is the recording itself. Not tested: exact phoneme accuracy, vertical sizes and 15/30/60 s after these edits. Keyframes (first/last frame through
`LTXVAddGuide`) and a scenes edit are the next candidates.

### First and last frame (2026-10-30)

`mode: first_last` (both pictures required, 49/97/121 frames, not the 15/30/60 s continuations). The first picture is written into the first latent frame as in
image-to-video; the last one is a keyframe guide (`LTXVAddGuide`, frame -1, strength 1.0) added to the video latent before the audio is attached: its tokens ride behind the real frames
and `LTXVCropGuides` cuts them off after sampling. The guide is removed before the learned upscaling and added again at full size for the refinement pass. The last picture is
not written into the last latent frame: one encoded picture stands for a single pixel frame, the last latent frame for the eight before the end.

Measured (two frames of the fox clip as the pictures, the same prompt, RTX 5070 Ti): fast, 49 frames, 113 s, start 30.9 dB and end 31.1 dB PSNR against the pictures;
two passes, 97 frames, 97 s, start 31.9 dB, end 28.2 dB. The motion between them is a plausible walk in the same forest. Not tested: pictures of different scenes, portrait sizes.

### Edited scenes with a Gemma scenario, and the chain compared (2026-10-30)

`scenes: N` (2-30) with `frames` as the minimum total (240 / 360 / 720 / 1440 / 2880, i.e. 10 s to two minutes) and one prompt per scene (`segment_prompts`).
Each scene is the shortest 8n+1 frame count (49 to 121) that reaches the total, generated by `ltx_worker.py` in its own owned process from its own prompt, with the job seed shared
and only the first scene taking the uploaded picture. `ltx_scenes.py` first encodes all prompts in ONE encoder process (the encoder is the slow read; each prompt also goes to the
prompt cache), then joins the pictures by stream copy and lays the scene sounds end to end, each padded or cut to its exact length, with 12 ms fades at the cuts, and encodes AAC once.
The continuation to 15/30/60 s (`ltx_long.py`) accepts an optional scenario too: one prompt per window, one conditioning file per window.

The planner (`ScenePlanner(..., sound=True)`) writes LTX prompts as one paragraph of 60-110 words (shot, fixed character description, action, place and light, and the SOUND with a
visible source), starts every prompt with ONE style phrase chosen for the whole film and avoids words that pull towards animation. Without that phrase the first of three scenes of the test
film came out as a cartoon while the others were photographs.

Same story (a fox kit leaves its den, sniffs tracks, an owl lands, it trots home), 768x512, fast mode, Gemma scenario with the style phrase, RTX 5070 Ti:

| | Edited scenes (3 x 121 frames) | Continuation (4 windows) |
|---|---|---|
| Time | 377 s (363 frames) | 394 s (360 frames) |
| Story | follows the scenario shot by shot | one scene; the owl pushes the fox out of the last windows |
| Look | photographic in every scene | photographic, one continuous take, the same fox for three windows |
| Sound | each scene has its own ambience (quiet in this test, -38 dB mean) | one soundscape (-55 dB mean here; -30 dB in the first run: the level is random) |

Verdict: for a story the edited scenes are better (a tenth faster, the scenario is carried out, nothing accumulates, up to two minutes); the continuation stays for one uninterrupted take
(a long camera move, one action). Not tested: speech in a scene, portrait sizes, two-pass quality, clips longer than 15 s.

### Prompt enhancement by Gemma (2026-10-31)

The Video panel can rewrite the request with Gemma before generation (`App/server/video/prompt_enhancer.py`, route `/api/video/enhance`). Each engine and mode has its own hidden instruction;
the person's own words stay in the history (`user_prompt`) and the rewritten text is shown and editable. The block hides when a multi-scene plan is active: the planner already writes those prompts.
The instructions restate Lightricks' training-caption style in our own words (action first, observable detail only, one shot type + one camera move + viewpoint woven into prose, the whole soundscape,
chronological order, one paragraph, judged against the ORIGINAL request) and the community advice (short, concrete, no "4K/photorealistic" filler, one camera move).

Comparison (preview, 768x512, 97 frames, seed 11, one subject, three request levels: 5 words / 22 words / 93 words with camera, sensor and light), RTX 5070 Ti:

| Variant | Words | Short request | Medium request | Detailed request |
|---|---|---|---|---|
| raw (no rewrite) | - | static, wrong animal | good | good |
| caption (Lightricks style, 150-220 words) | ~180 | weak motion | good | drifts to blurred close-ups |
| concise (70-110 words) | ~95 | rich scene, motion 7.5, -44 dB | motion 8.7, -40 dB | framing kept, motion 9.2, -35 dB |
| hybrid (110-170 words, keeps the user's specifics) | ~145 | good, motion 4.4 | good, -56 dB | framing kept, motion 8.8, -38 dB |
| Lightricks' own enhancer text (gemma4 variant) | ~200 | good, loudest peak | jumpy camera, almost silent (-63 dB) | drifts to close-ups |

Verdict: t2v uses `concise`. For i2v (first frame from a picture) `hybrid` moved more and kept audible sound (-46 dB against -63 dB) and both followed the action (stop, look at the camera, bound away),
so i2v, first/last frame and audio-to-video use `hybrid`; the last two were not compared. Caveats: one seed and one subject per level, judged by contact sheets plus motion (mean frame difference)
and mean loudness; the level of the generated sound is random from seed to seed (see the scenes table above), so loudness differences of a few dB are weak evidence.
Not tested: two-pass quality, portrait sizes, rewrites of non-English requests.

### Windows memory limit (2026-10-31)

On a 62 GB machine with a normal desktop session open, LTX jobs died with exit code 3221225477 (0xC0000005, access violation in c10.dll). Cause (measured by sampling the process): loading the 22B
transformer charges the worker ~39 GB of Windows commit (RAM + page file; the card's memory counts against it too), the system had ~11 GB left, and `--disable-smart-memory` makes every unload
copy the ~12 GB held on the card into new host tensors. The failed host allocation surfaced as an access violation. `release()` in `ltx_worker.py` now moves the transformer out in 1 GB steps
and returns the card's memory after each step, which fixed single-pass jobs. Two-pass ("standard") jobs can still stop with a CUDA out-of-memory error while the second load runs (one of four
test runs passed) when other programs hold much commit; closing them or enlarging the Windows page file (system-managed was 8-16 GB here) helps. The service now explains exit code 0xC0000005 to the person.
