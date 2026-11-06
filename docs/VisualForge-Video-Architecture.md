# Video section: architecture, interactions and the 2026-11-01 audit

Start here for anything in the Video tab. Per-engine behaviour, measured numbers and limits are in the engine documents linked below; this page is the map between them,
the invariants that are easy to break, and the result of the full audit of 2026-11-01.

## Verdict

* **The 2026-11-01 audit found working generation paths for the listed engines.** Its scoped runs included 30 s flagship clips, Gemma planning/rewrite/review and Whisper
  speech checks. This is evidence for those profiles, not a completeness guarantee or qualification of later features; limits and untested cases remain per engine.
* **No architectural rewrite is needed.** The boundaries are right: the bridge admits and routes, the Video service owns jobs, one worker process per engine does the GPU
  work, the registry is the single description of what a request may contain. The audit extracted the three places where the same scaffolding had grown three times (see "Fixed").
* Open, deliberately not done: split `video_service.py` / `video-panel.js` by feature, a storyboard job for per-scene keyframes, a hooks table that gathers the per-engine
  knowledge. Each is recorded under "Left as it is" with the reason and the trigger that would make it worth doing.

## Processes and who talks to whom

```
browser (video-panel.js, studio-images.js, video-panel.css)
   |  JSON over HTTP, :8189
studio_server.py  (the bridge: admission, GPU claim, static files, Gemma routes)
   |-- video_routes.py ---- video_prompt_enhancer.py | scene_planner.py | director_review.py   (all three are GemmaTask, gemma_task.py)
   |                              |  claim 'text' on the GPU coordinator, stream a chat answer from the private Ollama (TextManager)
   |                              `- speech_check.py Listener -> speech_worker.py (Whisper, in the Music runtime)  [review and LTX scenes]
   |-- range_response.py -- ranged MP4 for the player (shared with Music and Voiceover)
   `-- VideoManager (video_manager.py) -- starts the private service, token + Windows job object
          |  JSON over HTTP on a fixed local port (settings, default differs from 8188/8189)
       video_service.py  (class Jobs: queue of one, history, export, mix, delete)
          |  video_adapters.build_command(engine) -> one subprocess per job, events as JSON lines on stdout
       ltx_worker / ltx_scenes / ltx_long   wan_worker   s2v_worker (+ voice_worker)   character_worker   video_pipeline (FreeVideo CLI)
```

The **Director** ([VisualForge-Director.md](VisualForge-Director.md)) sits beside the Video service, not inside it: `App/server/director/` plans with Gemma (`brain.py`) and then drives the studio's own routes
(`studio.py` over the loopback bridge: voice-over through the Voiceover routes, Klein keyframes, LTX scenes, music) from a production thread (`runner.py`), assembles the scene clips with FFmpeg (`assemble.py`), builds the soundtrack (`soundtrack.py`: it does not use the Video mixer, whose ducking is too weak for speech) and hands the finished film to
`Jobs.register_film`. It appears in the engine list as a virtual entry (`virtual: 'director'`, added to the availability answer by the bridge) and is **not** in `video_registry.ENGINES`, so the engine matrix
test does not apply to it.

ComfyUI (:8188) is the Image runtime; the Video workers import the ComfyUI code vendored in the isolated Video runtime (`Runtime/Video/vendor/ComfyUI`) in-process and never call that
server. Pictures reach a job as data URLs and are converted to PNG by `image_convert.py` before any GPU job is committed. The services die together with the launcher window
(Windows job object).

### GPU rules

* One 16 GB card, one holder at a time: `GPU.claim(module, minimum_free_vram_mb)` under the bridge's submission lock frees what idle modules hold (Gemma included) and refuses
  with 409 while another module works. The Video service additionally accepts only one active job.
* Gemma tasks do not hold the global lock for their tens of seconds; each claims `'text'` itself (`GemmaTask.session`). A running video job makes them answer 409; the next video
  job unloads Gemma through `GPU.claim('video')`.
* Whisper (speech check, review) loads between scene processes or when no other module works; it never shares the card with a running LTX process. A missing speech model or any
  failure of the listening is silent: the check can never cost the clip.
* Heavy modules run sequentially. A model unload that is not chunked can exceed the Windows commit limit: `comfy_memory.release_models` moves models to the CPU in 1 GB steps.

## One generation, from the click to the file

1. The panel builds the request (`video-panel.js`; page formulas that mirror the server are pinned by `tests/test_video_engines.py`).
2. The bridge checks the model policy (disabled components), sends `/validate` to the service, refuses with 409 if the service is busy, claims the card, sends `/generate`.
3. `Jobs.submit` validates again with `video_registry.validate` (the only authority on fields, sizes, frame grids, scene layouts), converts the pictures, writes the job folder
   (every engine except H3 scratches in Windows Temp, `%TEMP%/VisualForgeWan/Jobs/<id>`; H3 in `UserData/Cache/Video/Jobs/<id>`), builds the command and starts the worker under `OwnedJob`.
4. The worker prints JSON events; `Jobs.event` turns them into the phase line and rows (`scene`, `speech_scores`, metrics). The panel polls `/status`.
5. On success the MP4 is probed, copied to the save folder with a name that carries the model, recorded in `video-job-history.json` (100 entries) and in the event history.
6. The player streams `/api/video/result` (ranged). Export (`video_delivery.py`) and mix (`video_mixer.py`) are CPU jobs of the same service and share one slot.

## The engines

| Engine | Worker | Modes | Long clips | Rating | Gemma guide | Document |
|---|---|---|---|---|---|---|
| FreeVideo H3 int8 (legacy) | `video_pipeline.py` + CLI | t2v, i2v, first_last, characters | scenes of 17n+5 frames | 4 | none | [FreeVideo-H3.md](FreeVideo-H3.md) |
| Wan 2.2 TI2V-5B | `wan_worker.py` | t2v, i2v | chain of 121-frame segments, or scenes (4n+1) | 5 | yes | `Wan-2.2-Video.md` |
| Wan 2.2 S2V-14B | `s2v_worker.py` | s2v (speech to video) | scenes cut at speech pauses | 5 | yes | `Wan-S2V.md` |
| LTX 2.5 Distilled | `ltx_worker.py`, `ltx_scenes.py`, `ltx_long.py` | t2v, i2v, first_last, a2v | continuation windows, scenes (8n+1), one shot with a scenario | 7 | yes | `LTX-2.5-Video.md` |
| Wan Animate 2 | `character_worker.py` | animate | segments (overlap 1), 30 s verified | 6.5 | yes | `Character-Animation.md` |
| SCAIL-2 | `character_worker.py` | replace, animate | segments (overlap 5), 30 s verified | 7 | yes | `Character-Animation.md` |

Ratings (0-10, 10 = a real camera) and their meaning: `Video-Model-Ratings.md`. Frame grids differ per engine and are the usual cause of a refused or trimmed request: LTX 8n+1
(at least 49), Wan 4n+1 (at least 33), H3 17n+5 (at least 39).

## Gemma in the Video section

| Task | Class / owner id | Operation type | Sampling | Output |
|---|---|---|---|---|
| Scenario: one idea to a prompt per scene or window | `ScenePlanner` / `video-plan` | `VideoPromptPlanning` | T 0.7, 8k context | JSON list of prompts; speech trimmed to 14 words a scene |
| Rewrite of one prompt in the style the model reads best | `PromptEnhancer` / `video-prompt` | `VideoPromptEnhancement` | T 0.5, 6-10k context, 700 tokens | one English paragraph (hidden guide per engine and mode) |
| Review of a finished clip | `DirectorReview` / `video-review` | `VideoReview` | T 0.3, 14k context, 1400 tokens | verdict, problems, fixes, corrected prompt |

All three derive from `GemmaTask` (`gemma_task.py`): `session()` checks that Gemma is available, claims `'text'` on the coordinator, marks the owner, always gives it back;
`ask()` streams the answer with `think=False` (thinking blocks only cost time here). Language policy: Gemma thinks and writes **English** for the networks, whatever the person typed;
**direct speech, voice-over and lettering stay verbatim in the person's language, in quotes**, with the language named. Only Russian and English are supported.
Routes live in `video_routes.py`; guides in `video_prompt_enhancer.GUIDES` (every guide ends with the language rule; `tests/test_prompt_enhancer.py` counts exactly one).

## Whisper in the Video section

`speech_check.py` extracts the quoted spoken lines of a prompt (`spoken_lines`), listens (`Listener`, the Music runtime's speech worker) and scores the share of the requested words
heard (`heard_share`, case/punctuation/yo-insensitive). LTX scenes with spoken lines are checked scene by scene; a scene scoring below `ACCEPT` is generated again with another
seed (`reseed`, at most twice, the best try wins; a line too long for its scene is not retried). The job shows the phase "Checking the speech (Whisper)", a retry as
"speech did not match, try again N", and the per-scene match in the result line. The review uses the same listener for a single clip.

## Invariants and traps

* **Frame arithmetic is per engine** (above). The registry validates; the page repeats the formulas for its lists. Change both or neither (`PageFormulaTests`).
* **Scenes are trimmed to the exact total** with a re-encode; the sound of each scene is levelled (target -30 dB, +24/-6 dB, peak limit -2 dB) and continuations use `dynaudnorm`.
  Muxing without normalised timestamps shifts the start (a 1439-frame result at 0.04 s): keep `setpts=N/(fps*TB)`, constant frame rate, explicit `-frames:v` and `-t`.
* **Closure plus `del`.** A nested function or lambda that reads a model name which the enclosing function later `del`s fails with `NameError` on the first call after the `del`. SCAIL's SAM3 once did.
  The workers now pass models and conditioning to such helpers as arguments (`ltx_worker.sample`, the audio encoder of `s2v_worker.py`, the text encoder and CLIP vision helpers of `character_worker.py`);
  `ruff check --select F821 App/server/video` is clean and should stay clean.
* **The Video service never drops a connection.** An unexpected exception in a route answers 500 with the error type and leaves the traceback in `video-backend.log` (the bridge forwards the
  answer); `Jobs.submit` removes the job folder when `Jobs.start` fails, and `copy_unique` removes a half-copied MP4, so a failed start or a full disk leaves neither orphan folders nor a truncated "result".
* **Never put `:` (or `< > " | ? *`) in a file name.** On NTFS `name:suffix` creates an alternate data stream: the file looks empty and survives most tools. Model suffixes use `-`.
* **Server messages are Russian source literals.** The English interface translates them through the catalogs by exact text, then pattern (`%s`, `{0}`), then fragment, so a new
  server message needs a catalog entry with the identical Russian text. Fixed text of a pattern must contain letters (a bare number template used to match anything).
  Keys built at run time (`video.assessment_<group>_<field>_text`) are invisible to key scans; `test_video_engines.py` checks them.
* **Evidence files** (`Runtime/Video/*-verification.json`, `UserData/Settings/*-verification.json`) carry `summary_ru` and `summary_en`; the panel shows the one for the interface language.
* **One prompt per review.** A film of scenes or windows is refused: Gemma reads its planned hard cuts as defects.

## Guard tests and the checklist for a new engine

`tests/test_video_engines.py` fails with the name of the missing piece. A new engine needs, in this order:

1. `ENGINES[...]` in `video_registry.py` (adapter, modes, frames, `rating()`, `prompt_enhancer` modes) and the validation of any new field.
2. An adapter in `video_adapters.ADAPTERS`, a worker, its events in `Jobs.event`, `engine_missing` for the availability message.
3. The catalog component(s) (`tools/build_catalog.py`), `model_requirements.COMPONENTS`, `componentIds` in `component-manager.js`, the release row in `studio-ui.js`.
4. A guide per mode in `video_prompt_enhancer.GUIDES` (and the engine note in `director_review.NOTES`).
5. Strengths/limits text `video.assessment_<group>_{strong,variable,limits,evidence}_text` in both languages, and the group in the `assessed` table of `video-panel.js`.
6. A rating row in `Video-Model-Ratings.md` and a verification file with both summaries.

Other guards: `test_video_ratings.py` (no local model reaches 8), `test_prompt_enhancer.py` (language rule once per guide), `test_i18n.py` (catalogs, bundle, placeholders), `tools/check_repo.py`.

**After any edit of a GPU worker** the unit tests are not enough (they never load a model): run `tools/video_smoke.py` against a running studio. It submits one short job per engine through the
panel's own API, probes the result and deletes the test jobs (about 25 minutes for all four on an RTX 5070 Ti; the numbers to compare with are in its docstring). Project rule: give the owner the estimate
and wait for an explicit yes before the first job.

## Findings of the 2026-11-01 audit

### Fixed

* `video_registry.validate` was one very long function; it is now a sequence of small checks (`_pictures`, `_scene_layout`, `_segment_prompts`, ...) in the same order (compared with the old version on 180,000 random and mutated requests: no difference; the old version is not kept).
* The Gemma scaffolding (availability, GPU claim, owner marks, streaming, error mapping) existed three times; it is `GemmaTask`. The three Gemma routes left the bridge for `video_routes.py`.
* The ranged file server existed three times (video, music, voiceover); it is `range_response.py`.
* i18n engine: a pattern template without letters in its fixed text matched any string; English UI also had untranslated model names, requirements and limits (now through `remote()`), two server
  messages about pictures and the speech-check phases and scores.
* Dead code (unused locals and imports, five dead string keys) removed; the Gemma review got its own operation type; 23 duplicate or stale copies of published documents were removed from the local `Support/Docs`.
* New guard tests: engine matrix, page-versus-server formulas, `gemma_task`, `video_routes`, `range_response`, the speech events of the Video service.

Second pass (same day, after the review of the service, the workers and the panel):

* `Jobs.submit` left the job folder (converted pictures, a reference video of up to 64 MB) behind when anything failed after its creation; `copy_unique` left an empty or truncated MP4 under the final
  name when the source was missing or the disk filled up; an unexpected exception in the service dropped the connection instead of answering. All three are fixed and covered by `test_video_safety.py`.
* The six closure-plus-`del` spots of the workers (see "Invariants") are rewritten with explicit arguments. `ruff` is clean for `F` rules over `App/server/video`.
* Calls that could hold the card for ever got a limit: the voice child of S2V (one hour), the ffprobe/ffmpeg helpers of the voice and H3 pipelines.
* The panel's reading of reference-video frames for Gemma could wait for ever on a damaged video; it has time limits and releases the element. The review's error colour used a token that does not exist (`--bad`);
  it is `--danger-ink`. The `small_image` helper that two Gemma modules each carried is one function in `gemma_task.py`.
* The worker edits (`ltx_worker.sample`, the S2V encoder helper and voice timeout, the character-worker helpers) were first covered by static checks and tests, then **run on the GPU the same day**
  with `tools/video_smoke.py` through the studio's own API (RTX 5070 Ti 16 GB, 768x512 / 512x512 / 512x896, one short job each, outputs probed with ffprobe, the test jobs deleted afterwards):

  | Job | Result | Wall time | Peak VRAM |
  |---|---|---|---|
  | LTX 2.5 fast, 49 frames | done, 768x512, 49 frames, AAC 48 kHz stereo | 375 s (cold disk) | 12.5 GB |
  | LTX 2.5 two-pass, 49 frames (the refinement call of the sampler) | done, the same file properties | 264 s | 12.4 GB |
  | Wan 2.2 S2V, one segment, cloned voice (voice child plus audio encoder) | done, 512x512, 77 frames at 16 fps, speech track | 506 s | 11.5 GB |
  | Wan Animate 2, 81 frames, motion prompt (text encoder and CLIP vision helpers) | done, 512x896, 81 frames | 350 s | 12.1 GB |

  Frames from every clip were looked at: coherent subjects, no frozen or garbled output. Not run: SCAIL-2 (it shares the character-worker helpers with Animate 2), the 30 s modes, scenes and the speech
  check, portrait sizes of LTX; those paths were not touched by the edits.

### Left as it is

| Item | Why not now | Do it when |
|---|---|---|
| Split `video_service.py` (export and mix into their own module) | no defect; the service tests are heavy and the split touches locking | next change inside export/mix |
| Split `video-panel.js` (about 850 lines) by feature | no browser test suite that would catch a lost handler | next change inside export/mix/history blocks |
| Hooks table for the per-engine knowledge (eight places today) | the matrix test already turns a forgotten place into a failure | a seventh engine |
| Storyboard (per-scene keyframes drawn by an Image model, then LTX scenes start from them) | `scene_pictures` exists in the backend; the UI and the orchestration are a feature of their own | the owner wants stronger consistency across scenes |
| `tests/test_tray.py` fails with pwsh 7 (icon size) | outside Video; present before this audit | the tray is touched |

### Ideas that were weighed and rejected

* Automatic review-and-regenerate loop: the person decides whether a correction is worth another run; Gemma's verdict on a good clip is sometimes a false alarm (measured).
* Best-of-N seeds for single shots: costs N runs; the per-scene speech retry already covers the case where a check exists.
* A second Whisper pass for lip-sync: no local measure exists; mouth accuracy stays a documented limit.

## Findings of the 2026-11-06 audit (long films through the Director)

The goal of the audit: a film of one to ten minutes and more, from a bare request. What was read, measured and changed in the Video service (the Director's own part is in [VisualForge-Director.md](VisualForge-Director.md)).

### Fixed

* **LTX continued takes are as long as the clip needs.** `ltx_continuation.chain_frames` knows every length the windows can make (whole strides of 96 frames plus a last window of 8n+1 frames, 49 to 121; 144 to 1440 frames,
  137 lengths, 240 left out because the panel uses it as a total of its scenes mode). The registry accepts them as the engine's `chain_frames` (the panel still offers 15, 30 and 60 s and repeats `LTX_WINDOWS`, which
  `test_video_engines.py` pins), `ltx_command` routes every request above one window to `ltx_long.py`. The Director used to ask for the next of 15, 30 or 60 s and trim: a scene of 6.6 s cost four windows
  instead of two, one of 16.6 s eight instead of four. Measured on the GPU the same day, every exact length came out with exactly the frames asked for (144, 248, 360...): see the table below.
* **`video_delivery.filter_complex`**: a filter graph above 6000 characters goes to a file read with `-/filter_complex` (the bundled FFmpeg 9.0.2 has dropped `-filter_complex_script`). Windows refuses a command line over
  32,767 characters; the Director's picture graph is about 430 characters per scene and its sound graph about 140 per line of speech, so a film of 60 scenes or 150 lines could not even be started
  (measured by building the graphs: 75 scenes 39,000 characters with the clip paths, 150 lines 40,000).
* **The Video history is refused at load past 5 MB** (`Jobs.__init__`): `register_film` stored the whole 1500-character prompt of every scene (100 KB for a film of 75 scenes); it keeps the first 300 characters now.
* **`Studio.register` starts the Video service first** (it starts on demand with the first Video job): the Director's continuation of a film whose scenes were all shot before a restart of the studio had no service to register
  its film with.
* **The Director keeps the computer awake** while a film is shot (`SetThreadExecutionState` from the production thread): nothing in the studio did, and a computer that sleeps in the middle of a window stops the worker.

### Measured (CPU, no model)

| What | Result |
|---|---|
| Assembly of 40 clips of 15 s at 1280x704 into 576 s (inline graph, 26,000 characters) | 61 s, FFmpeg peaked at 4.2 GB |
| Assembly of 75 clips into 855 s through the graph file | 92 s, peak 5.5 GB; video 855.000 s and audio 855.000 s, 20,520 frames, 48 kHz stereo AAC |
| Mixdown (two measuring passes, ducking, the final loudness stage) of 150 lines of speech and a looped bed into that film | 25 s, peak 124 MB, -18.002 LUFS, graph file 21 KB |

No hierarchical assembly is needed up to the 75 scenes the Director allows; memory grows with the number of inputs (about 70 MB for each clip).

### Measured (GPU, 2026-11-06, RTX 5070 Ti, through the Video API of a test studio, one job after another, warm file cache except where noted)

Exact chains (the length of every file counted with ffprobe: the frames asked for, and a sound of the same length, AAC 48 kHz):

| Quality | 49 | 97 | 144 (2 windows) | 248 (3) | 360 (4) |
|---|---|---|---|---|---|
| draft 768x512, one pass | 31 s | 374 s cold (first job of the session) | 77 s | 132 s | 166 s |
| standard 1024x576, two passes | | 55 s | 148 s (first use: 98 s for its first window) | 157 s | 203 s |
| maximum 1280x704, two passes | | 57 s | 104 s | 169 s | 235 s |

A window of 121 frames: 35 s at draft, 50 s at standard, 57 s at maximum; least squares over 31 windows: draft 18.5 s + 0.133 s per frame, standard 26.3 + 0.196, maximum 22.2 + 0.291, plus 7 to 35 s of a job beside its
windows (the prompt encoder takes 8 s warm, 127 s from a cold disk). **The biggest picture costs about as much as the standard one** (the earlier 2.3 times came from cold loads and from scenes rounded up to long chains).
Per window the model's own start (the process, the weights from the file cache) is 3 to 5 s of 35 to 57: a worker that stays loaded between windows would save about a tenth, which is why `ltx_worker.py` was not rebuilt.
The model of the Director's plan screen (`plan_rules.scene_seconds`) reproduces these 13 jobs within 10 % (except the two cold ones).

### Left as it is

| Item | Why not now | Do it when |
|---|---|---|
| One LTX process for all the windows of a film (models stay loaded) | the measured start of a window is 3 to 5 s of 35 to 57 (see the table): a refactor of `ltx_worker.py` for about a tenth | the windows get faster or the file cache cannot hold the weights |
| Encoding all scene prompts in one encoder process | a warm encoder costs 8 s per prompt; 100 s was the first read from a cold disk | the encoder file is not kept in the file cache |
| The last frame of a scene as the first frame of the next (identity across scenes) | changes how scenes look; needs the owner's eye, not a measurement | the owner wants the same person in scenes without a picture |
