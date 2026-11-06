# ACE-Step 1.5 Turbo (`ace-step-15-turbo`, Music)

[sources/code, follow-up 2026-11-06] [Audio Presets](../../VisualForge-Audio-Presets.md) reviews publisher and German/Japanese
experience. New song buttons append only user-selected vocal/arrangement wishes; lyrics and planner remain unchanged.
The external German benchmark uses a 1.7B planner, and MiniMax comparisons often use ACE XL; neither qualifies this installed Turbo/0.6B path.
No new ACE GPU or singing-quality claim is made by this follow-up.

Status: **short local integration checks recorded**. Source identity/prompting reviewed **2026-11-06**; fresh checks below are separate from dated historical observations. ACE-Step code revision `ca1e85fe9430179831e6bc6be790c332190a3866` (`App/server/music/music_registry.py`); individual checkpoint files/hashes are catalogued separately. **[code]**

ACE-Step 1.5 **Turbo**: a 2B distilled decoder with its VAE and a Qwen3 Embedding 0.6B conditioner, plus an optional 0.6B **musical planning LM** (`acestep-5Hz-lm-0.6B`). MIT licence for code and checkpoints. Runs in its own Python 3.12 runtime (`Runtime/Music/`) as an owned worker, 8 steps, CPU offload, batch 1. Marks: [the rules](../README.md).

## What it is for
Songs with vocals in many languages, or instrumentals, from 10 s to 3 minutes. The default engine of the Music tab and the one that covers films longer than a minute.

## Strong at / Weak at
* Strong: the longest track of the three (180 s), vocals in several languages, 48 kHz stereo WAV; a 60 s track fitted a Director film in 73 s **[recorded 2026-11-01, cold start]**.
* Weak: a particular voice, exact pronunciation, adherence to every lyric and full song structure are model-dependent **[sources]**; the planner is a music semantic LM, not a lyrics writer. Short instrumental/song integration is checked below; long tracks, other languages and musical listening quality remain **[unknown]**.

## Expect / Do not expect
Expect a plausible track in the style you describe and new vocals in the language you set. Do not expect voice cloning, a specific singer, exact lyrics, mixing, repaint/cover, LoRA packs or lip sync (none is offered).

## Modes and settings
| Item | Values |
|---|---|
| Mode | `instrumental`; `song` (lyrics required, otherwise the request is refused) |
| Duration | 10-180 s; recommended start 30 s |
| Extra | BPM 30-300 (optional); vocal language `unknown`, `ru`, `en`, `zh`, `ja`, `ko`, `es`, `fr`, `de`, `it`, `pt`; caption up to 2000 characters, lyrics up to 6000; seed; planner on/off |
| Memory | the registry admits it with at least 8 GB free |

## How to ask it
English caption: **genre, instruments, mood, tempo, and the voice** ("deep female voice, sparse vocals"), the production in a few words. Lyrics in the language of the song, a few short lines for sparse vocals. In a Director film Gemma writes the caption; give it the music in the description. **[recorded that the format works; quality of results is [unknown]]**

## Measured here **[recorded]**
A 60 s instrumental under a film, 73 s of the run (2026-11-01). Short offline GPU job of the integration (receipt in the release's `music.verification`).

## Not verified yet
Long tracks, each language, vocal quality, lyric adherence, repeatability by seed. **[unknown]**

## Where in the code, deeper reports
`App/server/music/music_registry.py`, `music_worker.py`, `music_manager.py`; runtime `Runtime/Music/`. Report: [VisualForge-Music-Generation.md](../../VisualForge-Music-Generation.md).

## Sources and refresh
GitHub (ace-step/ACE-Step-1.5, pinned revision in the registry), Hugging Face model cards. Refresh on a new ACE-Step release (a non-turbo or larger checkpoint would change this card).


## Automatic preparation and current evidence

[sources/code, 2026-11-06] Automatic Gemma preparation uses a concise English caption (<=512 characters), distinct song/instrumental guides and separate protected lyrics. Explicit BPM/language/duration/seed/planner remain selected by the user. Planner on creates semantic codes and may fill missing metadata, with extra loading/AR cost; off uses direct DiT. Caption/language rewriting stay off. No local on/off listening comparison establishes a universal winner. Refresh handles: ace-step/ACE-Step-1.5 Tutorial/INFERENCE, issue #677 (caption contamination); XL-SFT/4B/MLX discussion #1268 does not qualify this Turbo/0.6B worker.

[GPU, 2026-11-06] Real Gemma → instrumental 10 s and Russian song 20 s, seed 71, planner on: 48 kHz stereo, finite samples, no full-scale samples. Supplied lyrics stayed exact. The song needed the one correction to remove invented genre/instruments. Musical coherence, vocal/lyric adherence and planner on/off listening remain unqualified. [CPU/UI] Fake-backend checks and real-result player checks are separate. See [Audio Preparation](../../VisualForge-Audio-Preparation.md) for receipts, references and scope.
