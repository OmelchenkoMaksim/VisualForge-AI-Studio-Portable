# Setup and the component catalog

The repository holds source only. Everything else is installed by `Setup-VisualForge.cmd` (menu or flags) or from the
**Components** tab, both driven by the same engine in `App/server/provision`.

## How a run works

1. `Setup-Bootstrap.ps1` finds a Python 3.9+ (or unpacks the official embeddable Python 3.12, SHA-256 pinned, into
   `Runtime/SetupPython`) and starts `App/Setup-VisualForge.py`.
2. The engine reads `App/config/components.json`, works out which components are missing (file presence and sizes; nothing is
   hashed, so an existing installation, for example a copied folder, is recognised at once), shows the plan and checks free disk space.
3. Per component: every `downloads` entry is fetched into `UserData/Cache/Downloads/<component>/` (resumable, SHA-256 verified), the
   `steps` run, every model `files` entry is fetched straight to its final path, the `after` steps run, and the `present` list is
   checked. Downloads are deleted after a successful component unless `--keep-downloads` is used.
4. `UserData/Settings/components.state.json` records what was installed and whether the first-run menu was answered.

Rules: software components are always part of an install; a model pulls in what it `requires`; if a component fails, the ones that depend
on it are skipped and the rest continue; one installer at a time (`UserData/Cache/provision.lock`); the installer never writes outside
the installation folder.

## Catalog format

`components.json` has `modules` (tabs), `presets`, and `components`. A component:

```json
{"id": "video.model.wan-ti2v-5b", "module": "video", "kind": "model", "title": "...", "license": "...", "note": "...",
 "requires": ["video.runtime"],
 "files": [{"path": "Models/Video/Wan22TI2V5B/vae/wan2.2_vae.safetensors", "bytes": 1409400960, "sha256": "...", "urls": ["https://..."]}],
 "after": [{"op": "copy", "from": "...", "to": "..."}],
 "present": ["..."]}
```

Software components use `downloads` (`id`, `urls`, `filename`, `bytes`, `sha256`) and `steps` that refer to a download by `id`.

| `op` | What it does |
|---|---|
| `place` | copy a downloaded plain file to a path |
| `extract` | unpack zip / tar.gz / 7z / cab (`to`, `strip`, `keep`/`skip` glob lists); links and paths leaving the target are refused |
| `unpack_wheels` | unpack wheels (and a pure-Python sdist) into a plain folder (voice packages) |
| `ensurepip`, `pip_bootstrap`, `pip_install`, `pip_check` | install pinned wheels offline with `--no-index --no-deps` (`--no-build-isolation` for the one sdist) |
| `git_apply` | apply the two pinned `diffusers` patches with the system Git outside any repository; `verify_files` then checks the result |
| `verify_files` | fail the install unless the listed files have the expected SHA-256 |
| `crt_from_vcredist` | take the Microsoft C++ runtime DLLs out of `vc_redist.x64.exe` (cabinets opened with Windows' `expand.exe`, every DLL hash-pinned) |
| `move_tree`, `copy`, `copy_glob`, `write_text`, `write_json`, `mkdir`, `remove` | file plumbing |
| `ollama_pull`, `ollama_import` | put a model into the private Ollama store (`Models/Text/ollama`) by pulling it or copying it from your own Ollama |

## Regenerating the catalog

`tools/build_catalog.py` rebuilds the catalog from a complete reference installation plus live lookups (GitHub release digests, PyPI
hashes, Hugging Face listings). Run it when a pin changes, review `git diff App/config/components.json`, run the tests, commit.

## Troubleshooting

- *Windows path too long / pip errors:* install to a short path (`D:\VisualForge`).
- *`Git is required to apply the pinned source patches`:* install Git for Windows; you needed it to clone the repository anyway.
- *Hugging Face answers 401/403:* set `HF_TOKEN` to a read token and run the setup again.
- *Behind a proxy:* set `HTTPS_PROXY` (the standard library honors it).
- *Interrupted or damaged install:* run `Setup-VisualForge.cmd` again; `--verify` re-hashes models, `--force` reinstalls programs.
- *Not enough space:* the setup refuses before downloading; free space or choose fewer models.

## What was verified (2026-10-25) and what was not

Verified on a clean install into an empty folder (`Setup --software-only`, then ACE-Step and a small Ollama model), compared with the
reference installation the catalog was derived from:

- every model file of the reference installation (about 160 GB, 500+ files) re-hashed: all SHA-256 values in the catalog match;
- Image (ComfyUI), Video, Music and Text runtimes reinstalled from the catalog: file lists equal except pip bookkeeping files; vendor
  sources, ffmpeg and voice packages (10,986 files) byte-identical, patched `diffusers` included; `pip check` clean in both Pythons;
- Video Python imports torch (CUDA visible), diffusers, freevideo_engine, sageattention, qwen_tts; Music Python imports torch and ACE-Step;
- a real 10-second ACE-Step job ran through the bridge of the clean install (14 s on an RTX 5070 Ti); a chat answer came from a model pulled
  through the private Ollama; ComfyUI starts from the clean install; the Components tab installed demo components end to end.

Not verified on a clean install: Image, FreeVideo H3, Wan and MiniMax generations (their models were not downloaded), the first-run menu in an
interactive console window, a computer without Git or Python, and non-NVIDIA hardware. Their programs are identical to the reference
installation; the models are hash-identical to the ones that were tested there.

## Source endpoints and model lifecycle

The catalog retains multiple publisher endpoints for pinned file bytes. `provision/sources.py` and `App/config/download-sources.json` preserve alternatives when the catalog is rebuilt. HF/PyPI/Python aliases can share infrastructure; they are not independent backups. GitHub release API fallbacks request binary content. The pinned Microsoft C++ redistributable retains its exact publisher URL rather than a moving latest-version alias. Gated LTX access still requires accepted terms and authentication.

Components exposes local enable/disable and an explicit model removal plan. Removal requires a server-side ten-second delay, a single-use expiring token and the exact word `REMOVE`; it rechecks file identities and GPU/installer activity. File scopes come from the catalog, exclusive model directories and private Ollama manifests. Shared dependencies/blobs remain; general user history/results and unidentified shared caches are outside the deletion scope. Policy is stored in `UserData/Settings/models.policy.json`, never in Git. These operations do not alter system Python, drivers or a separate Ollama store.


## Recovering an unavailable download

After all sources for a pinned artifact fail, the studio opens a 30-second recovery dialog. Supply a direct HTTP(S) URL or choose an already downloaded file of the same version. Both paths retain the catalog size and SHA-256 checks. The original local file is copied, never moved. Closing the dialog or letting the server deadline expire skips that artifact; other artifacts/components continue. An incomplete component is not executed or marked installed, and models depending on it stay unavailable. Selecting an unavailable Image/Video/Music model or using **Install / repair** in Components retries only that model and its dependency closure. Enabling an incomplete model also triggers repair. A skipped artifact prompts again on the next attempt.

Windows terminal setup shows an equivalent native dialog unless `--yes` requests unattended operation. The bootstrap Python is required to run setup: if it cannot be obtained, setup exits with a retry message rather than pretending it can continue. Ollama registry model pulls use Ollama's own protocol and error reporting; the artifact recovery dialog applies to catalog-pinned downloads.

Small software artifacts (at most 300,000,000 bytes each) can be mirrored in GitHub Releases while the Git source history stays small. `tools/prepare_software_release.py --output <staging-directory> --download` stages software only, deduplicates by SHA-256 and writes a manifest/checksums. Keep original vendor archives and their embedded notices, review redistribution licenses before publishing, and enable `App/config/software-release.json` only after the assets exist and pass verification. Until then, the mirror is inactive and official sources remain in use. Models are never included in this software mirror. GitHub's ordinary Git file limit is 100 MiB: https://docs.github.com/en/repositories/creating-and-managing-repositories/repository-limits
