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

Component `video.model.ltx25` requires `video.runtime`; it is optional and listed in Components.
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
