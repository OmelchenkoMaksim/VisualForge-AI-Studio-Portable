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

Text-to-video and image-to-video with one initial frame. Silent MP4/H.264 at 24 FPS. No first/last conditioning, character-card workflow, speech, lip synchronization or minute assembly in this adapter.

Start with 832×480, 81 frames (3.375 seconds), standard 20 steps. This text-to-video profile completed in 87 seconds on RTX 5070 Ti, with a visually coherent ball in the inspected sample. An additional 832×480, 121-frame text generation completed in 126 seconds; three inspected frames show the toy robot raising its arm. This confirms visible motion in the selected scene, not exact compliance with arbitrary prompts. The smaller 512×288/33-frame trial completed in 46 seconds but showed severe shape artifacts; it remains a diagnostic fast option, not the recommended profile. Preview uses 12 steps, high uses 30. Other combinations, including image-to-video at 121 frames, remain experimental. They are not implied to work merely by being selectable. The official workflow uses 1280×704 and 121 frames; that full profile is deliberately not exposed before hardware validation.

Windows x64 and NVIDIA CUDA are required. Start with 16 GB VRAM and 32 GB system RAM; native offload uses RAM between GPU stages, followed by tiled VAE decoding. ComfyUI advertises 8 GB support, which is not a local hardware verification.

## Jobs, caches and portability

The bridge keeps its shared GPU submission lock across Image, Video and Music. Each Wan generation is one owned worker process; cancellation terminates only that worker and its descendants, including its FFmpeg child. It does not enumerate or terminate unrelated GPU users. The model process exits after each job, releasing VRAM.

Wan job scratch, source frames and compiler caches are under the current Windows user's Temp directory, identified by a hash of this installation's UserData path. The installed application contains no developer staging-path dependency. A moved installation recomputes these paths. Model paths are relative to the installation; a deliberate `VISUALFORGE_WAN_MODELS` override is for staging only.

Completed output follows existing Video settings, meaningful unique filenames, metadata sidecars and history. External absolute output directories remain absolute. A bounded 48 KiB log tail per job is retained under `UserData/Logs/Video/Wan/`. The log viewer uses that saved tail after completion; detailed temporary files remain on C. No automatic bulk cache deletion is performed.

## Evidence

`Runtime/Video/wan-verification.json` records exact GPU-tested modes, settings, timing and decoded output properties. An absent verification file means NOT TESTED, even if assets exist. The interface displays this scoped evidence. A short successful clip does not verify larger profiles, visual consistency, long videos or S2V.

Targeted CPU checks are in `tests/test_wan.py`, alongside existing Video ownership/storage checks. No Image GPU regression suite is required for this adapter-only change.
