# MiniMax Music3 in VisualForge

Engine ID: `minimax-music3-int8`. Local lyrics-conditioned songs and instrumental prompts, with 32 kHz stereo PCM WAV output. It shares Music history, the selected output folder, bounded logs, cancellation and terminal notifications with ACE-Step. Model switching preserves the current description and lyrics. The ACE-Step planner setting is retained but disabled for MiniMax, which has its own autoregressive music structure model.

## Native inference path

`App/server/music/minimax_worker.py` runs one job in an owned child using the existing `Runtime/Video/python` and native ComfyUI 0.34.0 components. It does not run through the Image server, upgrade any runtime, or keep a model resident between jobs. Shared GPU admission remains in the bridge; only the owned process tree is cancelled.

The implementation follows the [official ComfyUI workflow](https://github.com/Comfy-Org/workflow_templates/blob/e7cd011d4ded3411c2f481200544f0be6fdc962e/templates/audio_minimax_music_3.json): MiniMax CLIP loader, MiniMaxMusic3TextEncode, zero negative conditioning, EmptyMiniMaxMusic3LatentAudio, 30-step Euler/simple at CFG 1.7, and tiled DAV decoding (1536 / overlap 64). AR uses CFG 1.7 and top-k 50. The native Comfy decoder uses 44.1 kHz sample geometry; the bundled FFmpeg resamples its WAV to 32 kHz without changing pitch or duration. WAV writing uses the Python standard library. Models unload between phases; native memory offloading reserves 1.5 GB VRAM.

Initial requests accept 10–60 seconds. Duration is an upper limit: an end-of-audio token may finish the composition earlier. Output is not artificially padded. Longer songs remain disabled pending appropriate testing. Tempo/language and instrumental-only behavior are generative instructions, not guarantees. Prompt and lyrics can use `[Verse]`, `[Chorus]`, `[Bridge]`, `[Outro]`; a caption can separate Global Metadata, Vocal Details and Arrangement.

## Models and provenance

One portable copy under `Models/Music/MiniMax-Music3/`:

| File | Bytes | SHA-256 |
|---|---:|---|
| `diffusion_models/minimax_music3_dit_int8_convrot.safetensors` | 2,502,161,682 | `d6b959633e69899f99f3a92d6741c0fe79f26958a30811e50e372ef978b24d5f` |
| `text_encoders/minimax_music3_text_encoder_pruned_int8_convrot.safetensors` | 9,196,611,886 | `010b7416d2336a08c711bc22ee65849c9623069ddb7d89bec011a75699e52014` |
| `vae/minimax_music3_dav.safetensors` | 216,696,128 | `2a32155b769be01445fcc2a8663b910fc9e1751e18dc1c3ec528064512d9ef0c` |

Source: [Comfy-Org/MiniMax-Music-3](https://huggingface.co/Comfy-Org/MiniMax-Music-3), pinned revision `9e364ccd290a401561091fb441ed19cd7de92550`. Model payload: 11,915,469,696 bytes; this is different from working-folder footprint or VRAM usage. Preparation and resumable downloads stay in Windows user Temp on C. Installed inference uses relative portable model/runtime paths and offline environment settings.

Model release date: 2026-08-13, [official ComfyUI integration announcement](https://blog.comfy.org/p/minimax-music-3-state-of-the-art). Conversion/repository revision dates are separate.

License: [MiniMax-Music3 Community License](https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE), bundled unchanged as `Models/Music/MiniMax-Music3/LICENSE`; it is not MIT. Its terms include notices, attribution for commercial products, an acceptable-use policy, and additional requirements when offering a generation service to third parties. ACE-Step retains its own MIT license. See the bundled license for the complete terms.

## Evidence

The exact short-test receipt and any current limitations are in `Support/Diagnostics/MiniMaxMusicVerification.json`. Availability alone does not establish successful inference. Other durations, languages, long-form consistency and instrumental-only adherence must not be presented as tested unless the receipt records those checks. No long-song GPU test is implied by this integration.
