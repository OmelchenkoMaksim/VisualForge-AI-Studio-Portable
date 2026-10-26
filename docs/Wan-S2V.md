# Wan 2.2 S2V-14B

`wan22-s2v-14b` is a separate native-Comfy video adapter, released 2025-08-26. It generates video conditioned on a reference image, encoded speech and a visual prompt. This is distinct from attaching audio after ordinary TI2V generation. Lip timing and visual quality remain scene-dependent; passing decode checks is not proof of accurate lip synchronization.

Primary sources:

- https://github.com/Wan-Video/Wan2.2
- https://huggingface.co/Wan-AI/Wan2.2-S2V-14B
- https://docs.comfy.org/tutorials/video/wan/wan2-2-s2v
- https://huggingface.co/Comfy-Org/Wan-2.2

## Portable installation

The installed isolated Video Python and native ComfyUI 0.34.0 are reused, without upgrading Image, FreeVideo, Music or voice dependencies. The existing UMT5 encoder in `Models/Video/Wan22TI2V5B/text_encoders/` is shared, not copied again.

New assets under `Models/Video/Wan22S2V14B/`:

- `diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors`
- `audio_encoders/wav2vec2_large_english_fp16.safetensors`
- `vae/wan_2.1_vae.safetensors` (different from the TI2V-5B VAE)
- `LICENSE.txt`

The three new weights total 17,279,645,114 bytes. Source repository revision: `ee6f4a40737a995bf5818954cfce6d59443b0f04`, license Apache-2.0. Exact sizes, source URLs and SHA-256 values belong in `Support/Diagnostics/S2VDownloadManifest.json`. The audio encoder's upstream is `jonatasgrosman/wav2vec2-large-xlsr-53-english` (Apache-2.0); its English ASR provenance does not establish equal lip accuracy across languages.

This adapter uses native RAM offload with a 2 GB GPU reserve and tiled VAE decode. Initial hardware target is RTX 5070 Ti 16 GB and 64 GB system RAM; this is a test configuration, not a verified minimum. Official CLI requirements differ from the native offload path. No new system CUDA, Python, driver or administrator installation is required.

## Inputs and bounded profile

One reference PNG, a visual prompt, and either:

1. Voice reference (2–30 seconds, up to 8 MiB) plus short text. Existing private Qwen3-TTS voice-reference dependencies synthesize the text in a separate child process before the video model loads. Russian, English and automatic language selection use that existing TTS interface. Speaker-embedding mode can reduce voice similarity; it is not an exact voice guarantee.
2. Finished speech (0.1–4.8125 seconds, up to 8 MiB).

Only one initial profile is exposed: 512×512, 77 frames, 16 FPS, 20 steps, CFG 6, shift 8, UniPC/simple. The native S2V chunk requires at least 73 frames, so it cannot be reduced to a 33-frame TI2V smoke test. Speech exceeding 4.8125 seconds is rejected rather than silently cut. Shorter speech is padded with silence. Text length 160 is an input bound, not a guarantee the synthesized utterance will fit.

No Lightning LoRA is installed: the official tutorial explicitly describes severe motion/quality loss and unmatched keys with the T2V acceleration LoRA. Longer chunk continuation, higher resolution, multiple speakers, music mixing and other quality profiles are not implemented in this initial adapter.

## Execution, output and logs

`s2v_audio.py` validates media duration before submitting GPU work. `s2v_worker.py` runs speech synthesis, text/audio/image conditioning, native sampling, tiled decode, silent-video encoding, then AAC speech muxing. Unlike simple audio attachment, audio embeddings and image latents are passed into the diffusion model before sampling.

The common Video service retains its Image/Video/Music submission lock and owns the entire worker tree, including TTS and FFmpeg. Cancellation stops only that tree. No unrelated GPU processes are terminated. Worker exit releases GPU memory. Job scratch and compiler caches are under the Windows user's Temp directory; model paths resolve against the installed root. Staging-only environment overrides must not be written into production configuration.

Results follow existing Video output settings and unique English filenames, history and sidecars. Preserve external absolute output directories. Final validation checks dimensions, frame count, 16 FPS and presence of speech audio. A bounded 48 KiB backend-log tail is kept under `UserData/Logs/Video/Wan/`; TTS failure excerpts are included there. Full temporary logs stay on C. No automatic bulk cache deletion is performed.

## Evidence

`Runtime/Video/s2v-verification.json` is authoritative for executed GPU checks. Missing evidence means NOT TESTED even when weights are installed. Read its exact profile and visual/audio review; do not infer long-video support or ideal synchronization. CPU checks are in `tests/test_s2v.py`, `test_wan.py` and the existing Video safety checks. Image and Music GPU tests are not required for this isolated adapter change.
