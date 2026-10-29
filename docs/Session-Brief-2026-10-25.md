# VisualForge AI Studio Portable: session brief (2026-10-24 / 25)

Written for the owner and for whoever continues. Statuses follow the project convention: VERIFIED, TESTED ON OUR GPU, NOT TESTED.

## 1. In one paragraph

Starting from a working local studio (Image, Video, Music), the session added **six features** and released each of them to the installed
copy on H with a guarded procedure: a social-media **export** (Instagram presets), an **audio mixer** (music under a clip, with ducking),
a **Text** section (chat with history on the owner's Gemma 4 model, later with **picture attachments**), and a **GPU coordinator** that
makes the four modules share one 16 GB card safely, locks the whole interface while anything generates, and adds a global Stop button.
It also ran **real generations on H** with every installed video model. Everything is committed on branch `codex/delivery-presets`
(nothing pushed; there is no remote). 111 automated CPU tests pass. One regression of mine reached the installed copy for about 20 minutes
and was found and fixed by the first real job that touched it.

## 2. Ground rules that shaped the work

- H (an 8 TB hard disk) holds only the installed studio and user data; development, staging, caches and Git live on C. The owner declined
  automatic full rollback copies, so every release saved only the overwritten files to C.
- No push, no deletion of user data, no change to the owner's own Ollama, drivers or Python. Long GPU runs only on explicit request.
- Every release: preconditions (the installed files must still match the hashes in `InstalledRelease.json`, the app must not be running),
  backup of the files to be overwritten, copy, SHA-256 verification against the tested dev files, README/context-map/receipt update,
  a smoke test of the **installed** code in a throw-away root.

## 3. What was built

| Release | Feature | Where it lives |
|---|---|---|
| `delivery.1` | Export of a finished clip to 9:16 1080x1920, 9:16 720x1280, 4:5 1080x1350, 16:9 1920x1080 with pad (never stretch), 30 fps H.264 + AAC, preview with a real frame, progress, cancel, receipt | `video/video_delivery.py`, Video service, bridge routes, Video panel |
| `mixer.1` | Up to 3 music tracks under a clip: start, gain, fades, loop, own sound keep/mute, ducking, limiter; picture stream-copied; plan shared by preview and mix; result becomes a history entry | `video/video_mixer.py`, same plumbing |
| `text.1` | **Text** tab: chats with history, streaming, collapsed reasoning, code blocks, stop/regenerate/rename/delete, model settings | `text/` package, private Ollama |
| `text.2` | Pictures in messages (vision part of the model), paste/drop/attach, history keeps earlier pictures | `text/chat_store.py`, `text_routes.py`, `text-chat.js` |
| `gpu.1` / `gpu.2` | One `claim(module)` for every start route, `/api/busy`, `/api/stop-generation`, interface-wide lock + Stop banner, Stop during model load | `gpu_coordinator.py`, `studio-ui.js`, bridge |

## 4. Interesting design decisions

- **Why a private Ollama and not ComfyUI.** The owner's model (`...Gemma4A4B-9.41-...-C32K`, 25.2 B parameters, about 4 B active, Q4_0, vision + thinking)
  is built on Ollama's `gemma4` renderer/parser, which plain llama.cpp does not have, and ComfyUI has no chat engine. So the studio copies the
  owner's `ollama.exe` + libraries (1.8 GB, AMD libraries left out) and the model (14.6 GB, digests verified on C and again on H) into
  `Runtime/Text` and `Models/Text/ollama`, and runs it as its own process: port 8292, own home folder and keys, no cloud, no prune,
  killed by a Windows job object with the app. The owner's Ollama is never touched.
- **One plan for preview and execution.** Both the export and the mixer compute a plan (`plan_export`, `plan_mix`) on the server; the page only draws it.
  The preview cannot disagree with the result.
- **Mixed clips are history entries.** A mixed MP4 becomes a normal Video history row, so it can be exported or mixed again.
- **Pictures are never addressed by path.** Chat images are found by ids stored in the chat file, formats are recognised by their headers,
  and the mixer's music is named by Music job id; only the bridge turns it into a file.
- **The GPU is a shared, exclusive resource.** Measured idle memory: Video and Music workers exit after each job (nothing left), ComfyUI started
  with `--cache-none` leaves 0.17 GB, the chat model holds about 15.9 GB until unloaded. So only Text really needs unloading, but the coordinator
  asks every idle module to release anyway; it also replaced four hand-written copies of the same checks.
- **Streaming without freezing the bridge.** Every POST used to run under one global lock; a minutes-long chat stream would have blocked Stop. Only the admission now holds it.

## 5. Real runs on H (RTX 5070 Ti 16 GB, models on the hard disk)

- FreeVideo H3: 10.1 s clip with its own sound, 16 min. Wan 2.2 TI2V: 5.04 s silent, 14 min (126 s when warm earlier; the difference is the cold load from the HDD).
  Wan S2V: 4.8 s with a Russian line from a voice sample, 9 min. Music (ACE-Step): 10 s in about 3 min. All three clips were exported to Reels with sound;
  the silent one got generated music through a manual ffmpeg step, which later became the mixer.
- Text: 26 tokens/s on the first answer (warm-up + thinking), 90-170 afterwards; first word 72 s after a cold start from H (40 s on NVMe).
- Image with Qwen Image 2.1: 159 s, peak 15.4 GB, 0.17 GB left afterwards.
- Stop through the banner endpoint was exercised on real Video, Music and Text jobs; a Music job started with the chat model loaded unloaded it first and finished.

## 6. Difficulties and how they were handled

- **A moving target.** The installed app was updated by another session while this one was reading it; the first overview was stale. Fix: compare hashes against
  the receipt before every deployment, never assume.
- **Windows details.** Console code pages garble Cyrillic output (judged from files and JSON, not the console); `robocopy` returns 1 on success; PowerShell here-strings and
  quotes broke a commit message (a tool safety check blocked a bogus delete, nothing was lost; messages now go through files); LF/CRLF are preserved per file;
  a refused TCP connection takes about 2 s, which made every busy check slow when ComfyUI was off (now remembered for 10 s).
- **Stopping something that is silent.** Ollama sends nothing, not even HTTP headers, while loading a model. `http.client` blocks in `recv`, and `shutdown()` did not wake it
  on Windows. Fix: a plain socket polled every 0.2 s; Stop now takes about 2 s even during a load. A fake Ollama with a "slow load" mode made this testable.
- **Hidden-browser timers.** In the embedded browser pane, timers are throttled and a 45 s script limit applies; UI checks had to be split into short steps and triggered by hand.
- **My own regression.** In `gpu.1` I changed the signature of the Music route handler and updated the POST call but not the GET one, so Music status/history dropped connections.
  Unit tests could not see it because `studio_server.py` builds its managers on import. The first real Music job on H exposed it; `gpu.2` fixed it in one line and
  `tests/test_bridge_smoke.py` now starts the real bridge and walks every route family (verified to fail on the old code).
- Smaller catches before they shipped: a variable shadowing in the chat stream, an invalid 1x1 crop on a yuv420p frame in a test, a stale "not deployed" line in documents after releases.

## 7. Limits and obstacles that were not overcome

- **No truly blocking obstacle**, but real limits remain:
  - FreeVideo offers only 1.6 s or 10.1 s, so a 5 s test was impossible with it; its picture is 512x288, so Instagram files are padded, enlarged copies, not HD. The export records `scaled_up` and `enhancement: none` honestly.
  - The chat model needs almost the whole 16 GB card, so Text cannot run beside Image/Video/Music; the design makes them take turns instead.
  - The installed **EXE was never launched** for any release (the pane cannot drive the app's own window); everything was verified on the same code started from H and on browser copies.
  - Audio quality was **measured, not listened to** (levels, bands, ducking depth). Lip-sync accuracy of S2V was not judged. Instagram's exact current upload rules could not be read (the help page redirected to login); presets are a proposal.
  - Not tested: stopping a real Image job through the banner, Wan jobs through the lock, 64 K context, small text in screenshots, two browser windows at once.
- The first Git repository created on H (git directory on C) is a stale snapshot of the installation; the real source of truth is the dev checkout. It can be deleted.

## 8. State at the end

Installed: `2026.10.25-gpu.2` on H. Source: branch `codex/delivery-presets`, last commit `1fcc483`, clean tree, no remote. Test files left behind on purpose:
`Small Red Fox ... .png` on the Desktop and `Warm ambient pads - 4 - ...` (wav + json) in `UserData/Results/Music`; the generated clips and their Reels exports are on the Desktop.
Documents: `docs/VisualForge-Video-Export.md`, `VisualForge-Audio-Mixer.md`, `VisualForge-Text-Chat.md`, `VisualForge-GPU-Coordination.md` (copied to `Support/Docs`), the receipt `Support/Diagnostics/InstalledRelease.json`
with a section per release, and `Support/Diagnostics/TextRuntimeManifest.json`.

## 9. Sensible next steps

Launch the real EXE once and click through the new sections; listen to the mixer output; a scene/timeline layer for 1-3 minute videos with the exporter and mixer on top;
native higher-resolution Wan profiles and a temporally stable enhancement stage (the honest way to "HD"); own audio files as mixer tracks; chat export and search;
a cap on how many old pictures a chat resends; commit hygiene (squash or push to a real remote when one exists).

## 10. Publication preparation (release `2026.10.25-components.1`)

### Goal and outcome

Make the project publishable on GitHub so that a clone contains **only source**, while a setup program downloads everything else
from the original publishers: the programs around the models (always) and the models (optional, any time later). Done and installed on
H; **nothing was pushed anywhere**. H is now itself the Git working tree (git directory on C), branch `main`, clean status.

### What was built

| Part | What it does |
|---|---|
| Allow-list `.gitignore` | Ignores everything except `App`, `docs`, `tests`, `tools`, `Support/Licenses` and a few root files; model, binary and media types are ignored even inside those folders. A new unknown folder cannot be committed by accident. |
| `tools/check_repo.py` + `.githooks` | Pre-commit/pre-push guard: forbidden file types, files over 2 MB, personal paths, e-mail addresses, tokens. Covered by tests. |
| Catalog `App/config/component-catalog.json` | 18 components, each file with URL, size and SHA-256. Generated by `tools/build_catalog.py` from the manifests of the reference installation plus live lookups (GitHub digests, PyPI hashes, Hugging Face listings). |
| Installer `App/server/provision` | Standard library only: resumable verified downloads, safe zip/tar/7z/cab extraction, offline pip steps, pinned patches, C++ runtime DLLs taken out of the signed `vc_redist` without running it, private-Ollama pull/import, terminal menu and unattended flags. |
| Launchers | `Start-VisualForge.cmd` (installs the programs on the first start, then runs the studio) and `Setup-VisualForge.cmd`; Python is borrowed from the system or a hash-pinned embeddable Python is unpacked. |
| Components tab | Shows what is installed, downloads missing models with progress and cancel, repairs missing programs; Image/Video/Music/Text point to it when a model is absent. |
| Documents | README (English, Russian), `THIRD-PARTY-NOTICES.md`, `docs/VisualForge-Component-Setup.md`, `docs/VisualForge-User-Guide.md` (the former installed README). |

### Decisions worth remembering

- **Programs are mandatory, models are choices.** Every tab needs its program part, so the tabs always stay; a model is a checkbox and can be fetched later without restarting.
- **Pinned everything.** A download is accepted only if size and SHA-256 match the catalog; interrupted downloads resume; one installer at a time.
- **No system changes.** The studio's own Python, Ollama and Microsoft DLLs live inside its folder; the user's Python, Ollama, drivers and registry are untouched.
- **Clean public history.** The old history contains the owner's e-mail and local paths, so a separate one-commit branch `public-main` (neutral author) was prepared; the license and the author are left for the owner to choose.
- **H as the main repository.** `H/.git` points to a git directory on C that now holds the full history (`main`), `public-main` and the old `snapshot-initial`.

### Difficulties and how they were handled

- **Hand-assembled runtimes.** The Video/Music/Text runtimes had been built by many one-off scripts. Their provenance was recovered from recorded manifests and lock files, then proved by reinstalling into an empty folder and comparing with H.
- **Bugs found only by that comparison:** `diffusers` (patched) and `freevideo_engine` had been pip-installed from source in the reference Video runtime and were missing from my recipe; zip archives contain git symbolic links (now stored as plain files, never as links); the real Ollama pull stream sends `total` before `completed`; OEM-code-page output of `expand.exe` broke decoding; a test failed inside a non-ASCII checkout path.
- **Invented values.** I once typed a hash from memory into the bootstrap script; it was caught and replaced by the real value, and a test now ties the script to the catalog.
- **License surprise.** The MiniMax H3 Community License excludes the European Union, the United Kingdom, South Korea and the United States from its territory; this is shown next to the model in the menu, README and notices.

### Verification

- Every model file on H (about 160 GB, 500+ files) was re-hashed: all SHA-256 values in the catalog match.
- Clean install into an empty folder: runtimes equal to H except pip bookkeeping; `vendor`, ffmpeg and voice packages (10,986 files) byte-identical; `pip check` clean; torch sees the GPU in all Pythons.
- Real jobs on the clean install: a 10-second ACE-Step track (14 s), a chat answer from a model pulled through the private Ollama; ComfyUI starts; the Components tab was driven end to end against a fake catalog.
- 184 CPU tests pass in the dev checkout and again on H.

### Not verified

Image, FreeVideo H3, Wan and MiniMax generations on a clean install (models not downloaded); the first-run menu in a live console window; a computer without Git, or without an NVIDIA card; the installed studio was not started from H for this release.

### State and next steps

H: release `2026.10.25-components.1`, `main` clean, receipt hashes verified, backup of overwritten files on C. Open: choose a license and the public commit's author (then recreate `public-main`); optionally publish after a final review; later, re-run a generation of every engine on a fresh install when time allows.
