# One GPU, four modules: coordinator, global lock and the Stop banner

Image, Video, Music and Text take the video card **one at a time**. Status: **installed in release `2026.10.25-gpu.2`
(application files verified by SHA-256; the installed EXE was not launched for this release).**

## What stays in video memory when a module is idle

| Module | Engine | Memory after the job ends |
|---|---|---|
| Video (FreeVideo, Wan TI2V, Wan S2V) | one worker process per job | **nothing**: the process exits and frees everything |
| Music (ACE-Step, MiniMax) | one worker process per job | **nothing**, same reason |
| Image | ComfyUI, started by the installed launcher with `--cache-none` | **about 0.2 GB** (only the CUDA context): the models are dropped when the job ends. Measured: 15.4 GB peak during a Qwen Image 2.1 job, 0.17 GB above the empty card right after it |
| Text | private Ollama keeps the chat model for the keep-alive time (default 10 min) | **about 15.9 GB** (measured with a 32 K context) until unloaded |

So in practice only **Text** holds memory while idle; the answer to "do idle networks eat video memory" is no for Video, Music and
Image, and yes for Text until it is unloaded, which the studio does automatically when anything else starts (and `claim()` also asks
ComfyUI to free itself, which protects against a launch without `--cache-none`). A finished Video or Music job needs no unloading, because it
left nothing behind. (The small Video service process that the bridge starts on first use holds no GPU memory and also runs the CPU-only
export and mixer jobs, so it is deliberately **not** closed when another module starts.)

## `gpu.py`: one `claim(module)` for every "start" button

Every start route now calls `GPU.claim(module, need_vram_mb)` and nothing else decides about the GPU:

1. If another module works (Image: ComfyUI queue running or pending; Video: generation job; Music: job; Text: an answer being
   written) it raises `Busy` (HTTP 409, "GPU занят Video. Дождитесь завершения…"). A probe that fails (a service not running) counts as idle.
2. Otherwise it asks every **other** module to release what it holds: Image -> ComfyUI `/free` (models and memory), Text -> unload the
   chat model. Both are best effort; a service that does not answer holds nothing to free.
3. If the caller needs a minimum (Video: the engine's `minimum_free_vram_mb`, default 12 000; Music: 8 000) it waits up to 12 s until
   `nvidia-smi` shows that much free memory, then raises `Busy` with the numbers, or `GpuUnavailable` (HTTP 503) when the card cannot be read.

Wired in: Video `generate` (after validation, so a typo does not unload anything), Music `generate`, Image `generate` (after the request
and the save folder are validated, before anything is uploaded), and Text `send` (admission only; the stream itself never holds the lock).
This replaced four copies of the same checks and the separate Text hook; the behaviour of the messages is kept. ComfyUI that does not
answer is remembered for 10 s so that busy checks and starts do not each wait about 2 s for a refused connection.

The bridge's submission lock covers only the POST routes that check the card and start or free GPU work (`GPU_START_ROUTES` in
`server.py`: Image/Video/Music `generate`, components `install`, Text `unload`) plus the Text and voice-Gemma admissions. Stop, settings,
favorites and the native folder dialog (modal, open for minutes) run outside it: Stop once waited for as long as that dialog stayed open
(`tests/test_bridge_locks.py`). Module locks guard in-memory state only, never a network call or a file read: the Text manager does not hold
its lock across Ollama calls, the Video service copies its rows under `jobs.lock` and reads logs, decodes pictures and probes voice samples
outside it. A Video busy probe that times out keeps the last answer; it must never close the service's Job object (that kills the service
and the running generation).

## Global lock in the interface

While `GET /api/busy` says a module generates, the buttons that **start or disturb a job** are inert: starting Image, Video or Music, sending or
regenerating a chat message (and Enter in the chat field), the Text chat list / new / rename / delete, saving Text settings, unloading the
model, mixing and exporting a clip, changing a save folder, installing components (the `BLOCKED` list at the top of the lock section of
`studio-ui.js`, the same list dims them in `studio-ui.css`). **Tabs, the choice of engines and modes, settings, scrolling, expanding sections
and typing in prompt fields stay free**, and the Stop buttons (`data-allow-busy`) always work. Capture-phase handlers swallow mouse
events on the blocked buttons, the `submit` of the four start forms and Enter in the chat field, so the module scripts never run. It holds
across tabs and after a page reload because it follows the server's state, polled every 1.2 s (and shortly after any click).
`studio:busy` / `studio:idle` events carry `detail.kind`; kind `voice` means the only job is Gemma editing a dictation (see Voice.md), and the
banner words that differently.

A banner at the top shows who works and a red **Остановить генерацию** button that calls `POST /api/stop-generation`, which stops
whatever runs, whichever module started it and even when the job was started before a reload: Text -> `stop_chat`; Music/Video -> the
job's own cancel with the token held by the bridge; Image -> ComfyUI cancel of the jobs this session started, otherwise interrupt and
clear the queue. CPU-only export and mixer jobs are not "generation": they neither lock the interface nor are stopped by the banner
(they have their own cancel buttons).

## Stop during the model load

Ollama sends nothing (not even HTTP headers) while it loads the model, which takes up to minutes from a hard disk, so Stop used to be
noticed only after the first word. The chat now talks to Ollama over a plain socket polled every 0.2 s: Stop closes it at once and
Ollama drops the request. Measured: the GPU is free again about 2 s after Stop during a model load (previously until the load ended).

## Evidence

`tests/test_gpu.py` (9 tests: who is released for each starting module, refusal in all 12 busy/start combinations without freeing
anything, a module never blocks itself, the message names every working module, waiting for memory and giving up with numbers, an unreadable
card is a separate error, failing probes/releases never block a start, a release that finds Text answering stops the start); the Music
admission tests now run on the coordinator; `tests/test_text.py` has 29 tests (Stop during a simulated load, chunked decoding at every
split). In the in-app browser with the real model: the banner appears and everything except Stop is blocked (clicks of tabs, new chat,
unload, settings, the main Logs button; typing, Enter, select mouse-down) while Tab stays usable and the Stop button is highlighted;
a Video start answers 409 "GPU занят Text"; Stop during the load frees the server in about 2 s and unlocks the interface.

`tests/test_bridge_smoke.py` (3 tests) starts the real `server.py` from a copy of `App` on throw-away state and walks every route family;
it exists because of the bug below.

## Real jobs on the installed copy (RTX 5070 Ti 16 GB, models on the H hard disk)

- **Image idle:** a Qwen Image 2.1 FP8 job (low quality, 159 s, peak 15.4 GB) left 0.17 GB above the baseline; `/api/busy` showed Image
  while it ran and idle afterwards.
- **Video stop:** a FreeVideo job was started right after the Image job, `/api/busy` showed Video, starting Image or Music answered 409
  "GPU занят Video", and `POST /api/stop-generation` ended the job (state `cancelled`) within 2 s; VRAM was back at the baseline.
- **Music stop:** a 10 s ACE-Step job showed `busy: music`, was stopped through the same endpoint (`cancelled`, VRAM back at the baseline).
- **Text stop:** a long chat answer (VRAM 15.85 GB with the model loaded) was stopped through the same endpoint after 60 words: the stream
  ended with `stopped: true` and the partial text was saved.
- **Unload on start:** with the chat model still in memory (15.85 GB), a Music job was started: `claim()` unloaded the model, the job ran
  to the end (125 s, peak 5.5 GB, would not have fitted otherwise) and `/api/text/status` showed no loaded model afterwards.

**A bug found by this check and fixed in `gpu.2`:** in `gpu.1` the *GET* branch of `/api/music` still called `music.routes.handle` with its
old signature, so music availability, history, status and result dropped the connection. The first real Music job exposed it; the fix is
one line and `tests/test_bridge_smoke.py` fails on the old call and passes on the fix. `gpu.1` was installed for about 20 minutes.

Not tested: the installed EXE, a real Image job stopped through the banner endpoint (the Image branch is exercised only by the busy
probe and by the path "started in this session -> ComfyUI cancel"), Wan jobs through the lock, two browser windows at once.
