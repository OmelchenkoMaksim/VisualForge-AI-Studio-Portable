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
2. Finished speech (0.1–4.8125 seconds in the one-shot mode, up to 75 seconds in the scenes mode, up to 8 MiB).

Sizes (since 2026-10-30): 512×512, 832×480 and vertical 480×832; always 77 frames, 16 FPS, 20 steps, CFG 6, shift 8, UniPC/simple. The native S2V chunk requires at least 73 frames, so it cannot be reduced to a 33-frame TI2V smoke test. Speech exceeding 4.8125 seconds is rejected rather than silently cut. Shorter speech is padded with silence. Text length 160 is an input bound, not a guarantee the synthesized utterance will fit.

No Lightning LoRA is installed: the official tutorial explicitly describes severe motion/quality loss and unmatched keys with the T2V acceleration LoRA. Multiple speakers, music mixing, a pose video and other quality profiles are not implemented; long speech is handled by the scenes structure (below).

## Execution, output and logs

`s2v_audio.py` validates media duration before submitting GPU work. `s2v_worker.py` runs speech synthesis, text/audio/image conditioning, native sampling, tiled decode, silent-video encoding, then AAC speech muxing. Unlike simple audio attachment, audio embeddings and image latents are passed into the diffusion model before sampling.

The common Video service retains its Image/Video/Music submission lock and owns the entire worker tree, including TTS and FFmpeg. Cancellation stops only that tree. No unrelated GPU processes are terminated. Worker exit releases GPU memory. Job scratch and compiler caches are under the Windows user's Temp directory; model paths resolve against the installed root. Staging-only environment overrides must not be written into production configuration.

Results follow existing Video output settings and unique English filenames, history and sidecars. Preserve external absolute output directories. Final validation checks dimensions, frame count, 16 FPS and presence of speech audio. A bounded 48 KiB backend-log tail is kept under `UserData/Logs/Video/Wan/`; TTS failure excerpts are included there. Full temporary logs stay on C. No automatic bulk cache deletion is performed.

## Evidence

`Runtime/Video/s2v-verification.json` is authoritative for executed GPU checks. Missing evidence means NOT TESTED even when weights are installed. Read its exact profile and visual/audio review; do not infer long-video support or ideal synchronization. CPU checks are in `tests/test_s2v.py`, `test_wan.py` and the existing Video safety checks. Image and Music GPU tests are not required for this isolated adapter change.

## Audit and changes (2026-10-30)

What the model is good at (official page, ComfyUI tutorial, user tests): one person who speaks or sings; speech drives lips, expression, head and
body, gestures follow the loudness and rhythm; portrait, half-body and full-body shots; the prompt sets camera, setting and actions not tied to the voice;
480P/720P in the original (we use 512-class sizes for 16 GB); an optional pose video (not implemented here). Weak points: fixed 16 FPS, speed LoRAs
(not trained for S2V) remove the dynamics, English-trained audio encoder, long clips (a user's 55 s test lost lip sync and image quality; MultiTalk /
InfiniteTalk are steadier over minutes), noisy references or audio are amplified across chunks. The first frame of our clips is smeared in every run.

Changes: sizes 832×480 and 480×832 (peak 10.6 GB, about 6.5 minutes); optional negative prompt (the built-in list is the default); SageAttention in
the worker (512×512: 349 s -> 242 s at the same seed, same picture; the audio encoder still falls back to PyTorch attention with a harmless log line);
`step`/`sampling_start` events carry `elapsed`; the strengths/limitations block (`video.assessment_s2v_*`). Regression fixed on the way: the Wan 2.2
rewrite had removed `export_video` from `wan_worker.py`, which broke S2V and the LTX picture step at start; `test_wan.py` now checks that sibling
workers only import names `wan_worker` defines.

## Long clips: scenes (chosen) and the chain (tried and removed), 2026-10-30

One segment is 77 frames (4.8125 s). Speech up to 75 seconds (a ready recording, or a typed line of up to 1000 characters for the voice sample)
is assembled by the **scenes** structure (`structure: scenes`; the Video panel shows it as "Edited scenes"):

1. the worker prepares the speech (cloning first when a voice sample is given), plans the scenes: each is at most 4.8125 s and ends at the quietest
   0.2 s of the speech between 2.5 s and 4.8125 s after its start (`pause_near`), or at 4.8125 s when there is no pause (`plan_segments`);
2. every scene is generated again from the same reference picture with its own slice of the speech, the same seed, and its own prompt; the model renders 77
   frames and the scene keeps only as many pictures as its slice lasts (the last one half a second more), so the cuts add no silence;
3. all scenes go through one encoder; the speech is laid under them as one continuous track (sound and picture have exactly the same length).

The prompts come from Gemma (`ScenePlanner` with `talking=True`; it gets the first picture and, for a typed line, the text): every prompt repeats the
description of the person and place and changes only the camera distance and angle, the camera move and the gesture, and never describes words or lips,
because the voice drives them. The count shown in the panel is an estimate (speech seconds / 4.4); the worker plans the real scenes and maps the
prompts onto them proportionally, so a voice that comes out longer or shorter than the estimate still works.

**The same 21.4 s Russian speech (Windows voice), the same reference, seed 7, 512×512, Gemma scenario, RTX 5070 Ti 16 GB:**

| | Scenes | Chain (`WanSoundImageToVideoExtend`) |
|---|---|---|
| Total time | 1115 s (5 scenes, 212 s each) | 1222 s (5 segments, 232 s each) |
| Result length | 21.56 s, sound = picture | 21.94 s |
| Identity at the end | the face of the reference in every scene | the hair lightens and the face thins gradually |
| Look | cuts between scenes with different camera and head angle | one continuous shot |
| Long-clip risk | none that builds up | reports of lip sync collapsing near a minute |

Decision: scenes stay, the chain was removed from the S2V code (the worker, the validation, the planner prompt and the panel option; Wan 2.2 TI2V keeps its
own chain). Reasons: it is a tenth faster, the quality is the same in the first and in the last scene, and a 75 s clip needs no trust in a chain that users
report collapsing. The price is hard cuts: every scene starts from the reference pose.

Not tested: speech longer than 22 s, voice cloning in a long clip, singing, 832×480 and vertical clips in the scenes structure.
