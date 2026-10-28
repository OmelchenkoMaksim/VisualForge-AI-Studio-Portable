# Local Music

Music supports local instrumental and lyrics-conditioned song creation with two
engines: ACE-Step 1.5 Turbo and MiniMax Music3 INT8. See [MiniMax details](MiniMax-Music3.md).

ACE-Step 1.5 Turbo uses a separately packaged Python 3.12 embedded runtime, the official
2B Turbo decoder, VAE, Qwen3 Embedding 0.6B and an optional 0.6B musical planning LM.
Source and checkpoints use MIT licenses; provenance is stored with the runtime.

## Stable Audio 3 Medium

A third engine, `stable-audio-3-medium`, runs in the Image ComfyUI instead of a worker subprocess (`backend: comfy` in
`music/registry.py`; the code is `music/comfy_backend.py`). One job is one ComfyUI prompt built from
`App/workflows/stable-audio-3-medium/text.api.json` (checkpoint, T5Gemma encoder, `lcm` sampler with 8 steps and CFG 1,
`PreviewAudio`). The bridge keeps the Music GPU slot from submission until the WAV is published, cancels only its own prompt through
`/api/jobs/<id>/cancel`, asks ComfyUI to drop its models afterwards, and keeps its prompt id out of Image's busy check and Stop.
ComfyUI writes a FLAC preview into its temp folder; the bridge converts it to WAV with PyAV (part of the ComfyUI Python) without
changing a sample and stores the rate, channels, bit depth and length read from that file. Phase one has a plain English prompt,
a 10–120 second duration and a seed: no lyrics, tempo, language, categories or reprompt. The Base checkpoint is not offered.
The checkpoint is found through the `checkpoints` entry of `App/config/extra_model_paths.yaml`.

## Portable layout

- `App/server/music/registry.py`: input limits and installed engine descriptor.
- `App/server/music/manager.py`: bridge jobs, exact-process ownership and cancellation.
- `App/server/music/worker.py`: real upstream inference, offline model initialization,
  CPU offload, batch 1, 8 steps, native PyTorch SDPA, no compilation/FlashAttention.
- `App/server/music/routes.py`: existing-origin HTTP routes and shared GPU admission.
- `App/server/music/storage.py`: atomic JSON and exclusive WAV publication.
- `App/frontend/music.js`, `music.css`, `index.html`: module controls and local draft.
- `Runtime/Music/python/`: standalone interpreter and isolated dependencies.
- `Runtime/Music/vendor/ACE-Step/`: pinned upstream Python package and MIT license.
- `Runtime/Music/upstream.json`, `requirements-lock.txt`: code/configuration provenance.
- `Models/Music/ACE-Step-1.5/`: one copy of the selected checkpoints.
- `UserData/Settings/music.settings.json`, `music-history.json`: independent preferences/history.
- `UserData/Results/Music/`: default output folder; custom absolute paths are preserved.
- `UserData/Logs/Music/<job-id>.log`: bounded job stages, warnings and traceback on failure.

Temporary inference files and runtime caches go into the current Windows user's
temporary folder under `VisualForgeMusic/<installation-key>/`, normally on C.
No fixed developer username or staging path is required at runtime. Copying the
whole portable installation includes the interpreter, code and weights. Offline
environment variables prevent Hugging Face access; a missing asset fails explicitly.
No system Python, CUDA toolkit or environment upgrade is needed. A compatible
NVIDIA driver and Windows x64 are prerequisites, as for other GPU modules.
The signed Microsoft Visual C++ x64 minimum-runtime DLLs (14.44.35211.0) are
bundled app-locally next to Music's Python executable, with their license and
`crt-provenance.json`. They were extracted from Microsoft's official signed
Redistributable archive without running its installer or changing Windows.
The runtime loader is checked to use these local C++ DLLs rather than System32.

## API and job behavior

The existing loopback bridge serves `/api/music/availability`, `settings`, `active`,
`generate`, `status?id=`, `cancel`, `history`, `logs?id=` and `result?id=`. `POST delete {id}` removes a terminal job and its recorded artifacts; see [generation deletion](Interface.md#deleting-a-generation).
POST requests inherit safe-origin checks, JSON bounds and the shared submission lock.
Generation refuses an active Image, Video or Music job, unloads idle Image models,
closes the owned idle Video backend, then checks for at least 8 GB free VRAM.
Image and Video submission also refuse an active Music job.

Each Music job has one dedicated owned process. Cancellation requires that job's
id/token and closes only its Windows job handle. On success the process exits before
publication, freeing GPU allocations. WAV files are decoded and checked for finite,
non-silent audio and requested duration before publication. Output names include
English prompt words (or an English fallback), the actual seed and a unique job id;
exclusive creation prevents overwriting existing audio/metadata. Each WAV has a JSON
sidecar recording prompt, lyrics, seed, settings and measured audio properties.

History keeps up to 200 records; the UI shows the most recent 30. Logs retain a bounded
tail (under approximately 192 KiB per task), expose at most 16 KiB per request and do
not log each successful Music polling request. No automatic repeated GPU tests run.

## Scope

The initial UI accepts 10–180 seconds, a single WAV output, instrumental or supplied
lyrics, optional BPM, vocal language and musical planning. The initial recommended
duration is 30 seconds. The planner is a music-specific semantic LM, not a general
lyrics writer. Songs use newly generated vocals, not voice cloning. A particular
voice, exact pronunciation, adherence to every lyric and full song structure are
model-dependent. Mixing, cover/repaint UI, LoRA packs, lip synchronization and
automatic video integration are not part of this release.

Long durations and every language/mode require their own scoped evidence; a short
instrumental smoke test must not be presented as verification of all capabilities.
See the installed release's `music.verification` record for executed checks.

## Focused validation

`python -m unittest discover -s tests -p test_music.py` checks request validation,
portable/external output paths, no-overwrite publication, cancellation authorization
and refusal while Image/Video is busy. Syntax checks cover the changed Python and JS.
A final short real offline GPU job checks the assembled runtime and integration.
No full Image suite or long music generation is required for this module iteration.

