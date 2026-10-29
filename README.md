# VisualForge AI Studio

A local, offline multimedia AI studio for Windows. One window, five tabs, one video card:

Settings → Interface language switches the studio between Russian and English immediately, preserving inputs and active tasks. The preference is saved in the browser profile. See [localization](docs/VisualForge-Interface-Localization.md).

Settings → «История событий» opens the [unified local event journal](docs/VisualForge-Event-History.md), including distinct technical operation types. Module logs stay separate; click analytics is not collected yet.

Image, Video and Music history cards include a confirmed «Удалить» action for the recorded result, metadata, per-job scratch and dedicated log. Active jobs must finish first; shared backend logs remain. «Данные генерации» shows the [four-field generation identity](docs/VisualForge-Generation-Identity.md), including a copyable ID, start time and measured duration. See [deletion details and limits](docs/VisualForge-Interface-Guide.md#deleting-a-generation).

| Tab | What it does | Engines |
|---|---|---|
| **Image** | edit a picture, edit with reference pictures, or draw from a prompt; 2x/4x upscaling | Qwen Image Edit 2511, Qwen Image 2.1 Uncensored (FP8), Z-Image Turbo INT8 (prompt, detail and optional Union/Tile guidance), FLUX.3 Klein INT8 on ComfyUI |
| **Video** | text/image to video with sound, character references, speech, talking characters; export for Instagram/Reels, sound mixer | FreeVideo H3 (MiniMax H3 INT8), Wan 2.2 TI2V-5B, Wan 2.2 S2V-14B, Qwen3-TTS |
| **Music** | songs from lyrics, instrumentals | ACE-Step 1.5 Turbo, MiniMax Music3 INT8, Stable Audio 3 Medium (music and sound effects) |
| **Text** | private chat with pictures, history, thinking blocks | any model of the Ollama registry (default Gemma 4) |
| **Components** | see what is installed, download missing models | - |
| **Voice input** | a microphone button (or F9) in every text field; Russian and other languages, recognized locally | Whisper large-v3-turbo / small / large-v3 |

Qwen Image Edit 2511 supports one to three inputs, Standard 40-step and optional Lightning 4-step editing. The community NSFW LoRA is a separate switch; negative prompts work in Standard. Inputs are resized to about 1 MP without cropping their edges. See [capabilities and measured checks](docs/Qwen-Image-Edit-2511.md).

Qwen Image 2.1 offers native generation/editing at about 1 MP or 2K, RGBA inputs/output, background-removal instructions and up to ten ordered inputs. Optional official T2I/I2I prompt enhancers are independent components. The 25/40-step controls state their actual budget; more steps do not guarantee better composition. See [capabilities and scoped checks](docs/Qwen-Image-2.1.md).

Z-Image Turbo has one 8-step text-generation preset. Optional **2K detail** combines Real-ESRGAN x2 and a 5-step refinement pass. Union/Tile add edge, grayscale or prepared-map guidance, masked editing and Tile enlargement. Refinement and Tile redraw texture and can change lettering and small features. Real-ESRGAN 2x/4x remains available separately in Prompt mode. See [capabilities, limitations and measured checks](docs/Z-Image-Turbo.md).

FLUX.3 Klein supports text generation, instruction edits, 2–10 image references and object layout boxes. Text modes offer native 2K/4K separately from Real-ESRGAN enlargement. Editing uses about 1 MP; 4K needs nearly the entire 16 GB GPU and does not reliably improve composition. See [capabilities and measured checks](docs/FLUX-3-Klein.md).

Everything runs on your computer and there is no cloud service behind it; the only network traffic is the download of the
programs and models from their original publishers (the model libraries run in offline mode).

This repository contains **only source code, scripts and documentation** (a few MB). The runtimes (about 10 GB) and the models
(up to about 234 GB) are downloaded by the setup program from the original sources, and every file is checked against a SHA-256
recorded in this repository.

## Requirements

- Windows 10 or 11 (64-bit), Git, an internet connection for the setup
- An NVIDIA video card; 16 GB of video memory is what the defaults are tuned for (smaller cards can use the smaller models)
- Disk space: about 17 GB for the programs, plus the models you choose (see below). A fast SSD helps a lot with model loading
- Install into a **short path** such as `D:\VisualForge` (Windows paths longer than 260 characters break some Python packages)

## Install

```bat
git clone <this repository> D:\VisualForge
cd D:\VisualForge
Start-VisualForge.cmd
```

On the first start `Start-VisualForge.cmd` runs `Setup-VisualForge.cmd`, which shows a menu:

- the **programs** around the models (ComfyUI, the Video, Music and Text runtimes, ffmpeg, libraries) are **always installed** -
  every tab needs its program part, so the tabs always stay in the studio;
- every **model** is a checkbox - choose only what you will use. A tab whose model is not installed says so and points to the
  **Components** tab, where the model can be downloaded at any time, without restarting the studio.

Unattended installs:

```bat
Setup-VisualForge.cmd --software-only            :: programs only, models later
Setup-VisualForge.cmd --preset light --yes        :: programs + smaller models of every tab
Setup-VisualForge.cmd --all --yes                 :: everything (about 244 GB)
Setup-VisualForge.cmd --models image.model.upscale,video.model.wan-ti2v-5b --yes
Setup-VisualForge.cmd --text-model gemma4:e4b     :: any model of the Ollama registry for the Text tab
Setup-VisualForge.cmd --import-ollama "mymodel:latest"   :: copy a model from your own Ollama installation
Setup-VisualForge.cmd --status                    :: what is installed
Setup-VisualForge.cmd --verify                    :: re-hash the installed models (slow)
Setup-VisualForge.cmd --help
```

The setup is **resumable**: an interrupted download continues where it stopped, finished files are never fetched again, and
running the setup again repairs a broken installation. It never touches Python, Ollama, drivers or the registry of your computer;
everything it installs lives inside the VisualForge folder.

### What gets downloaded

| Part | Download | On disk | Source |
|---|---|---|---|
| Programs: ComfyUI portable, Video runtime, Music runtime, Text runtime, tools | about 9.5 GB | about 17 GB | GitHub releases, PyPI, PyTorch, python.org, Microsoft (C++ runtime DLLs, unpacked from the signed installer without running it) |
| Image models | 0.07 to 31 GB each | same | Hugging Face |
| Video models | 2.5 to 51 GB each | same | Hugging Face |
| Music models | 7.7 and 11.9 GB | same | Hugging Face |
| Text models | 4.6 to 18.7 GB | same | Ollama registry |
| Voice input models | 1 to 3.1 GB | same | Hugging Face |

The authoritative list, with every URL, size and hash, is [App/config/component-catalog.json](App/config/component-catalog.json) (generated by
[tools/build_catalog.py](tools/build_catalog.py)). Hugging Face asks for a login on some repositories; if one of them does, set
the environment variable `HF_TOKEN` before running the setup.

## Starting and using

The Agent project-folder picker opens inside the studio window, above its content.

`Start-VisualForge.cmd` opens the studio in its own window. While any generation runs (Image, Video, Music or a Text answer) the buttons
that start or disturb a job are dimmed (tabs, engine choices and typing stay free), **Stop** works from every tab, and starting a generation releases the video memory held by the idle modules. Results are saved
under `UserData/Results`. Features and folders are described in the [user guide](docs/VisualForge-User-Guide.md); installation details in [Setup](docs/VisualForge-Component-Setup.md). More on each part in [docs](docs): [Delivery](docs/VisualForge-Video-Export.md) (export for social networks),
[Mixer](docs/VisualForge-Audio-Mixer.md), [Voice input](docs/VisualForge-Voice-Input.md), [Text](docs/VisualForge-Text-Chat.md), [GPU coordination](docs/VisualForge-GPU-Coordination.md), [Music](docs/VisualForge-Music-Generation.md), [Wan](docs/Wan-2.2-Video.md).

## Folder layout after the setup

```text
App/            the application (this repository)
Runtime/        programs downloaded by the setup (ComfyUI_windows_portable, Video, Music, Text, Tools)
Models/         model files downloaded by the setup
UserData/       your settings, chats, prompts, results, logs (never part of Git)
Support/        license texts
docs/ tests/ tools/
```

`Runtime/`, `Models/` and `UserData/` are ignored by Git. [.gitignore](.gitignore) is an allow-list, so a new top-level file or
folder is not committed by accident; `tools/check_repo.py` (run by the Git hooks in `.githooks`, enable once with
`git config core.hooksPath .githooks`) refuses model files, binaries, files over 2 MB, personal paths, e-mail addresses and tokens.

The commit hook reads staged blobs; the push hook checks every reachable tree in the exact outgoing history, including files deleted in later commits. Generated/private directories are blocked at any nesting level. Only reviewed branding images are source assets. The total publication ceiling is 10 GB; the source tree is only a few MB. GitHub CI repeats the history check. Local hooks can be deliberately bypassed, so use the checked publication command rather than pushing a private development branch.

From the portable folder, after setting the intended GitHub `origin`, run `python tools/publish_source.py --prepare` to review the source snapshot and `python tools/publish_source.py --push` to publish it. Only `public-main:main` is sent. A neutral source-only public history is maintained separately from private local branches; the tool never force-pushes or publishes tags.

Model switches and removal are in Settings → Components. Disabling keeps the files. Removal previews its scope, waits ten seconds on the server, then requires the exact word `REMOVE`. Shared model dependencies/Ollama blobs and user results/chats/prompts are preserved. Download alternatives retain the pinned versions and checksums; publisher aliases can share infrastructure and gated model terms still apply. See [Setup](docs/VisualForge-Component-Setup.md).

## Developing

```bat
python -m unittest discover -s tests          :: CPU tests; no model, no download, no GPU needed
python tools\build_catalog.py --help          :: regenerate the component catalog from a reference installation
```

Tests that exercise ffmpeg look for it in `Runtime/Video/ffmpeg` of the checkout or in the folder named by
`VISUALFORGE_VIDEO_RUNTIME`; without it they are skipped.

## Licenses and responsible use

The studio's own source code currently has **no license file**: all rights are reserved until the author adds one.
Third-party programs and models keep their own licenses, listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md); several
models are restricted to non-commercial use, and some community quantizations are uncensored. **Check the territory clause of the
MiniMax H3 Community License** (FreeVideo H3 model): its text excludes the European Union, the United Kingdom, South Korea and the
United States from the licensed territory. You are responsible for what you
generate and for following the licenses of the models you download.
