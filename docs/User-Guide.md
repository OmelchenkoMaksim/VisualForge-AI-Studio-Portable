# VisualForge AI Studio: user guide

This README is intentionally English. The owner normally communicates in **Russian**, and the application interface and user-facing assistance should remain Russian.

## Start the studio

Run `Start-VisualForge.cmd` (or `VisualForge AI Studio.exe` if you have the launcher) in this folder. Keep the portable directory structure together. Python, the model runtimes and the installed model files are bundled; you do not need to install a system Python for these installed features.

The application opens its own maximized window. Image, Video and Music share the GPU sequentially. Closing the application's own window normally stops its owned services and can stop an active generation. Save your work and wait for completion before closing it.

## Installed features

### Image

- Qwen Image Edit 2511, Qwen Image 2.1 Uncensored FP8, Z-Image Turbo INT8 and FLUX.3 Klein INT8.
- Single-image editing, reference-image editing and prompt modes.
- Quality presets, variations, cancellation, prompt archive/favorites and automatic saving.
- Separate AI upscaling at 2×/4× and image paste/preview.

For 2511, Prompt mode uses a neutral canvas; it is not native text-to-image. The 2.1 profile supports native text-to-image. Z-Image Turbo INT8 is text-to-image only: it has the Prompt mode and a single 8-step preset (CFG 1, no negative prompt), and the editing modes, reference image and "similar variant" are not available for it. FLUX.3 Klein INT8 (distilled, 16 steps, CFG 1) has all three modes: Prompt, Single (one image) and Reference (source plus one reference image; the result takes its size from the first image, about 1 MP). By default the official Qwen3.5 prompt upsampler first expands a short request into a detailed English caption (it sees the images in the edit modes); the "Расширять промпт" switch turns that off, and the expanded caption is shown under the result and kept in the history. Its weights come from a gated Hugging Face repository (accept the terms and set `HF_TOKEN` for the installer), and the FLUX Kommunity License limits commercial use of results. FLUX.3 Klein was run once (Prompt, 1024×1024, with the upsampler, 32 s, peak 15.5 GB of GPU memory in use, so very little headroom); Single and Reference have not been run on this machine yet, and the second image needs more video memory. Z-Image Turbo was run once (1024×1024, 8 steps, 28 s, peak 14.9 GB of GPU memory in use). Transparency behavior depends on the model and workflow.

### Video and character speech

- LTX 2.5 Distilled: joint local video and sound from text or an initial image. Start with 768x512, 49 frames (2.0417 seconds), 24 fps. Fast uses 8 steps; Two-pass uses 8 steps, learned latent enlargement and 3 refinement steps. Both modes were GPU-tested at this size. New 15, 30 and 60 second presets continue one scene through overlapping video/audio context; choose the duration marked «продолжение». Details, lighting and sounds may drift between fragments. Larger resolutions and long I2V remain experimental. Describe the scene, motion and sound together. See [LTX documentation](LTX25.md) and local evidence in `UserData/Settings/ltx25-verification.json`.

- Wan 2.2 S2V-14B: experimental audio-conditioned character video from an image plus a voice sample/text or finished speech. Initial profile: 512×512, 77 frames, 16 FPS, 20 steps; one 4.8125-second chunk. Long speech is rejected rather than silently trimmed. See [S2V documentation](Wan-S2V.md) and exact GPU/visual evidence in `Runtime/Video/s2v-verification.json`; lip accuracy is scene-dependent.

- Wan 2.2 TI2V-5B (released 2025-07-28): local text-to-video and initial-image animation, silent MP4. Start with 832×480, 81 frames, 20 steps; 512×288 showed severe artifacts. A 121-frame text profile was also GPU-tested with visible arm movement; other combinations remain experimental. See [Wan documentation](Wan.md) and exact GPU evidence in `Runtime/Video/wan-verification.json`. FreeVideo features below remain available separately.

- Music and sound (CPU only, no GPU): the "Музыка и звук" block under a finished clip lays up to three music results from the Music section under it and saves a new MP4 next to the original. For each track set start, volume, fade in/out and repeat; keep or mute the clip's own sound; optionally lower the music automatically while the clip's own sound (speech) plays. The picture is copied without re-encoding, the sound is rebuilt as AAC 48 kHz stereo and kept below full scale. The new clip appears in the Video history and can be exported to Instagram formats. See [Mixer documentation](Mixer.md). Levels were measured, not listened to; only WAV music from the Music section is supported.

- Export for social networks (CPU only, no GPU): the "Экспорт для соцсетей" block under a finished clip saves a copy as Instagram Reels/Stories 1080×1920, vertical 720×1280, feed 4:5 1080×1350 or landscape 1920×1080, with black fields instead of stretching, 30 FPS H.264 and AAC audio (keep the clip's sound, require it, or add a silent track). A small clip is only enlarged: no detail is added and no AI enhancement is applied. The original file is not changed. See [Delivery documentation](Delivery.md). Checked with synthetic clips and two existing outputs; no platform upload and no long clip were tested.

- Local FreeVideo v0.4.19 / H3 INT8 engine.
- Short text-to-video, image-to-video and first/last-frame modes.
- 512×288 resolution, 24 FPS. Short output is 1.625 seconds; the longer text/character preview is 10.125 seconds.
- A 60-second option for text or character-reference generation, assembled from six independently generated scenes. Cuts and appearance/motion changes can occur; this is not a guaranteed continuous one-minute shot.
- Up to three character cards. Choose an image or paste one into a card; use the character names in your scene description. Reference-guided appearance is experimental.
- Optional local Qwen3-TTS speech from a 2–30-second voice sample and typed dialogue. Lines play sequentially in card order. Lip synchronization is not implemented. The speech input is not a general-purpose uploaded music-track mixer.
- Automatic MP4/H.264 export with actual audio metadata, history, cancellation and readable logs.

For dialogue, use the character-reference mode, add an image and voice sample, and type a line. Choose a video duration long enough for all lines. If synthesized speech is longer than the video, the application asks you to shorten it or choose a longer duration.

### Music

- Stable Audio 3 Medium: music and sound effects from an English description, no lyrics or vocals. It runs in the Image ComfyUI but is a Music job: it holds the Music GPU slot until the WAV is saved, and Stop cancels only its own job. Duration 10–120 seconds; the saved WAV is converted from ComfyUI's FLAC and its sample rate, channels and length are read from the file. Reprompt and categories (Qwen) are not included in this phase. Its license limits commercial use.
- MiniMax Music3 INT8: additional local engine, 32 kHz stereo WAV, initial 10–60 second upper duration limit. One short Russian-lyrics song was GPU-tested; longer profiles and instrumental-only adherence were not separately verified. Community license, separate from ACE-Step MIT. See [model details](MiniMax-Music3.md).

- Local ACE-Step 1.5 Turbo (2B), isolated Windows runtime, MIT-licensed code and checkpoints.
- Instrumental creation or songs from supplied lyrics, optional tempo, vocal language, fixed seed and a 0.6B musical planner.
- Single stereo 48 kHz WAV, 10–180 seconds accepted; begin with 30 seconds.
- Automatic unique output names, JSON settings sidecars, player, job history, cancellation and bounded error logs.
- Description and lyrics drafts survive switching sections. Music output settings are independent of Image/Video.
- Final short real GPU checks are recorded in `Support/Diagnostics/MusicVerification.json`; long songs and all languages have not been exhaustively tested.
- Voice cloning, audio/video mixing and lip synchronization are not implemented in Music.

See [Music implementation and limitations](Music.md).

### Text

- Local chat with history (new): the **Text** tab runs the model "150ts-Gemma4A4B-9.41-…-C32K" (Gemma 4, 25B with about 4B active, Q4_0) through a private Ollama that lives inside this folder (`Runtime/Text`, model in `Models/Text/ollama`, port 8292, started on first use, stopped with the application). Chats are saved as files in `UserData/Text/Chats`. The model can think before answering (shown in a collapsed block), code blocks have a copy button, an answer can be stopped (the part already written is kept), regenerated, and the chat renamed or deleted. The studio does not use or change the Ollama installed on the computer. See [Text documentation](Text.md).
- The model takes almost the whole video card (about 15.9 GB with a 32K context). Text, Image, Video and Music work one at a time: a chat answer is refused while another module works, and starting Image, Video or Music unloads the chat model automatically. The first load from the disk can take a few minutes; the model leaves the memory by itself after the idle time set in the chat settings.
- Pictures in messages (new): the **Прикрепить изображение** button, Ctrl+V or dragging files onto the input attaches up to 4 PNG/JPEG pictures (shrunk in the page to 1536 px) to a message; the model sees them, and later questions in the same chat can refer to them. Pictures are stored with the chat in `UserData/Text/Chats` and deleted with it. Each picture costs about 250 tokens of context.

### One video card for four modules

- While any generation runs (Image, Video, Music or a Text answer) the buttons that start or disturb a job are inactive and dimmed (starting any generation, sending a chat message, changing the chat or the save folders, installing components) while tabs, the choice of neural networks, settings and typing in prompt fields stay available; a banner at the top shows what is working and has a red **Остановить генерацию** button that stops it from any tab, even after the page was reloaded. Exports and the sound mixer (processor only) do not lock anything.
- Starting Image, Video, Music or a Text answer is decided in one place: it is refused while another module works, and the models that idle modules still hold in video memory are released first (ComfyUI models for Image, the chat model for Text). Video and Music need no unloading: their worker processes exit after every job. See [GPU documentation](Gpu.md).
- Stop now also works while the chat model is still loading into memory.

### Explanations in the interface

Seed, steps, quality, upscaling, frame size, FPS and reference images have explanatory text or tooltips. A shared expandable glossary explains the remaining technical terms. The 3.1 release changed interface text only; no tests or generation runs were performed for that text patch, as requested by the owner.

## Where files are saved

Music default: `UserData/Results/Music/`. Change it in the Music output-folder section. Temporary Music inference files use the Windows user temporary folder on C; settings, results and bounded logs remain in the portable installation.

Video default:

```text
UserData/Results/Video/
```

Inside the installation folder this is `<installation folder>\UserData\Results\Video`.

The Video panel shows the active save directory and the complete path of a completed file. Change the directory in the open “Папка автосохранения Video” section and save that choice. “Скачать копию MP4” downloads an additional browser copy; the main output is already saved automatically.

Image uses its own selected output directory. The owner's existing external choice was preserved; it was the Windows Desktop when last observed. Moving this portable folder does not automatically redirect an explicitly selected absolute external path. Check the visible save directory on another computer.

Prompts, settings, job history and logs are under `UserData/`. Video and Image prompt archives are separate.

## Folder layout

```text
Start-VisualForge.cmd / Setup-VisualForge.cmd
App/                       application, frontend, adapters and workflows
Runtime/
  ComfyUI_windows_portable/ unchanged Image runtime
  Video/                   isolated video runtime and voice-only packages
  Music/                   isolated ACE-Step runtime and pinned vendor code
Models/
  ...                      installed Image weights
  Video/H3/                installed video weights
  Video/Qwen3TTS/           installed voice model
  Music/ACE-Step-1.5/       installed Turbo, VAE, text encoder and 0.6B planning LM
UserData/                  settings, prompts, outputs, history and logs
Support/                   diagnostics, documentation and recovery launcher
README.md                  this guide
```

Do not delete or individually rearrange model/runtime files. The executable's application icon is embedded; an external icon image is optional for startup.

## Hardware and portability

The verified development machine is a **desktop** with Ryzen 7 9800X3D, NVIDIA RTX 5070 Ti 16GB, approximately 64GB RAM and Windows 11 Pro. This is the tested configuration, not a proven minimum specification for every engine. GPU driver and runtime observations are recorded in the handoff documents; they are not universal driver recommendations.

The H3 workflow uses almost the entire 16GB GPU budget. Close other heavy GPU workloads. More VRAM can provide headroom, but other GPU generations have not been fully qualified with this package. Lower-memory machines, CPU-only inference, other operating systems and arbitrary GPUs are not promised to work.

The installed inference paths use local files and offline settings. Development included offline-guarded generation and a Unicode-path portable runtime exercise. Physical network-disconnection testing and complete transfer of the newest release to a different computer/GPU have not yet been performed. The goal is a self-contained offline portable studio on compatible machines, not an inference service that depends on cloud APIs.

A compatible NVIDIA driver is a host prerequisite; the package does not bundle or install drivers. Copy the whole application directory to a writable drive. A filesystem with support for large files is needed; FAT32 is unsuitable for individual weights exceeding 4GB. Allow room for outputs and working state.

## Disk policy

H is reserved for the installed application, its required models/runtimes, and intentional user data. Do not store development downloads, release ZIP duplicates, Git clones, duplicate Hugging Face/ModelScope caches or automatic full rollback snapshots on H. The owner explicitly declined an additional rollback copy for these deployments.

Development repositories, downloads and disposable build/test/compiler caches belong on C. CUDA/Triton/Numba compiler caches are directed to the current user's C temporary area for the current Unicode installation path. Small application working files can still exist under `UserData/Cache`; a policy that relocates every temporary artifact to C has not been fully implemented.

Do not automatically purge models, user files, older staging material or backups. Network access is available for development; a missing downloadable component can be retrieved again when needed instead of keeping redundant copies everywhere.

## Status and limitations

The actual minute example used two synthetic characters and one Russian line from a synthetic voice reference. Its final export is 1440 frames, 60.000 seconds, with H.264 video and audio. A one-frame timestamp issue was corrected by reassembling the already generated scenes, without repeating GPU generation.

Three simultaneous characters, several different cloned speakers in one job, numerical voice similarity, speech transcription and lip synchronization were not validated together. No forced out-of-memory recovery test was performed. Do not interpret a feature option as a promise of perfect identity preservation.

Music, Wan 2.2, extra audiovisual engines, experimental adapter packs, model release-date labels and 3–5-minute project generation are **planned**, not installed features of this release. See the project handoff and model research documents for the roadmap.

## Logs and support

Use the main “Логи” button or the Video log section. Logs are also stored in `UserData/Logs`; Video jobs have their own working logs and command records. Share only the relevant tail and error context; logs/history can contain local paths, prompts and dialogue. Do not publish tokens or personal media.

Useful local documents:

- `Support/Diagnostics/InstalledRelease.json`
- `Support/Diagnostics/VideoReleaseManifest.json`
- `Support/Diagnostics/Video3ReleaseManifest.json`
- `Support/Docs/Video3-Production-Notes.md`
- `Support/Recovery.cmd`

`Support/Recovery.cmd` is a recovery launcher, not a promise that a full old installation backup exists. Do not rerun historical one-time cleanup tools.

## Development/testing policy for future maintainers

Use the smallest necessary check for the changed behavior. Do not run the entire Image suite or repeat expensive GPU samples after every edit. Wording-only updates may be released without tests when the user explicitly requests that. Preserve honest statuses such as VERIFIED, TESTED ON OUR GPU and NOT TESTED.

Before a very long generation test, especially a 3–5-minute video, ask the user for approval and state an estimated wall-time range based on a measured short baseline. Defer exhaustive quality, portability and long-duration testing to dedicated testing tasks.

Do not change the working Image Python/ComfyUI installation, system CUDA/Python, drivers, firewall/DNS or Ollama to make a new model work. Use isolated on-demand backends, loopback-only services and the shared GPU coordinator.

### Output folders and completion notifications

Image, Video and Music have a shared disk/folder browser and independent output folders. Completed jobs show a notification with a short chime and an action to open the saved folder; errors use a different chime and link to the log. See [interface behavior and model date sources](Interface.md).


Program installation, models and the Components tab: see [Setup](Setup.md).
