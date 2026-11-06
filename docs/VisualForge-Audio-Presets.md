# Audio presets and reference research — 2026-11-06

The installed H working tree is the implementation baseline. This follow-up extends
[Audio Preparation](VisualForge-Audio-Preparation.md); earlier GPU results and the owner's preference for shorter gaps
remain separate evidence. No model or package was downloaded. Two explicitly approved donor WAVs were downloaded to C Temp.

## Findings that changed the implementation

**[sources/code] VoxCPM2 reference-only cloning has trained style conditioning.** The publisher's current
[model card](https://huggingface.co/openbmb/VoxCPM2), [demo implementation](https://github.com/OpenBMB/VoxCPM/blob/main/app.py)
and the maintainer's answer in [issue 210](https://github.com/OpenBMB/VoxCPM/issues/210) agree:
`(control)target text` can steer reference-only cloning. Transcript-assisted continuation does not support it.
The installed `voxcpm` 2.0.3 API/checkpoint supports the corresponding reference path. Lack of a separate `instruct`
argument was an incomplete reason to exclude this trained serialization in the earlier audit.

**[code] Implementation:** an opt-in experimental checkbox, off by default, enables a fourth validated Gemma field,
`style`: `none`, `storytelling`, `calm`, `clear`, `lively`, `brisk`. Only the worker adapter serializes the fixed English
description from `voiceover_delivery.py`. Free-form Gemma prose never enters synthesis. Narration, normalization,
pronunciation text, selected reference and exact transcript remain separate. Continuation and Qwen cannot enable it.
The plan cache binds the opt-in and reference mode; the speech cache also binds the style version/id. A neutral or
pacing-only plan retains existing audio-cache behavior. CFG 2.0, ten steps, denoiser settings and compile fallbacks stay unchanged.

**[sources/code] A 48 kHz reference is not a prerequisite for VoxCPM2's 48 kHz output.** The installed checkpoint's
`audio_vae_config` has `sample_rate=16000`, `out_sample_rate=48000`; local `voxcpm2.py` loads references at the encoding
rate. This matches the [technical report](https://arxiv.org/abs/2606.06928). We preserve original uploads but do not
blindly migrate the existing 24 kHz library or promise that higher-rate references improve naturalness.

**[sources/code] MiniMax instrumental conditioning should remain concise.** A detailed
[Japanese INT8/FP16 test](https://mikkoge.com/2026/08/20/comfyui-minimax-music-3-int8-fp16/) reports less unwanted singing
with an instrumental tag and a short positive caption than with a list of forbidden vocal sounds; it also records a later
failure despite the tag. Our adapter already used `[Instrumental]`; its appended negative list is now `Fully instrumental.`
The guide also discourages lists of vocal exclusions. This is a testable prompt simplification, not a guarantee of silence from singers.

**[code] User-selected recipes:** Music buttons append intimate restrained singing or a verse-to-chorus build to the
visible request, without modifying lyrics or picking genre/instruments. Voiceover buttons fill the separate delivery request
with storytelling, calm intimacy at normal volume, or a clear explanation. These are editable requests, not hidden defaults.
They do not enable experimental style control automatically. Actual capability limits remain visible when switching engine/reference.

## Exact-model guidance and applicability

| Installed path | Practical guidance | What was deliberately excluded |
|---|---|---|
| ACE-Step 1.5 Turbo, instrumental/song | Short caption; requested voice character separately from lyrics. Structural lyric labels remain verbatim. Preserve explicit BPM/language/planner. | Base/SFT/XL CFG and step recipes; automatic extra harmonies, genres, or singer identity. |
| MiniMax Music3 INT8, instrumental/song | Global Metadata, requested Vocal Details, Arrangement; section tags on their own lyric lines. Duration is an upper bound. | Automatic 180–300 s jobs from community examples, unsupported AR temperature/top-p tuning, automatic mastering or denoising. |
| Stable Audio 3 Medium, audio | Audible source/texture/development; distinguish music, ambience and discrete events. | Singing/lyrics and Base-only controls; no new GPU claim for unchanged workflow. |
| Qwen3-TTS 12Hz 0.6B Base, embedding cloning | Clean naturally delivered reference; conservative fragments, measured pronunciation and gap profiles. | CustomVoice/VoiceDesign instructions, C++/GGUF sampling claims, automatic switch to transcript ICL. |
| VoxCPM2, reference-only | Optional bounded trained style prefix, selected voice preserved; audition for timbre drift. | Arbitrary stage directions, accent/identity changes, promised exact rate/emotion. |
| VoxCPM2, continuation | Exact transcript and corresponding sample; reference delivery matters. | Style prefix and silent switching to reference-only mode. |

ACE's publisher recommends eight Turbo steps and distinguishes the musical planner from caption rewriting:
[inference](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/INFERENCE.md),
[musicians' guide](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/ace_step_musicians_guide.md),
[planner choices](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/Tutorial.md).
No larger planner is installed by this change and no claim that planner-on is universally better is made.
MiniMax's [publisher card](https://huggingface.co/MiniMaxAI/MiniMax-Music3) and
[Comfy node](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_minimax_music.py) remain the control authority.
Stable's [Medium card](https://huggingface.co/stabilityai/stable-audio-3-medium) remains the workflow authority.

## Independent experience reviewed

Research date: **2026-11-06**. Languages/communities below describe the material, not verified nationalities of forum users.
English-language Reddit/GitHub are international; we cannot establish that every author is American or European.
Community observations are scoped hypotheses unless confirmed by publisher code or the local tests below.

| First-hand source / topic | Useful observation and boundary |
|---|---|
| [GitHub VoxCPM #210](https://github.com/OpenBMB/VoxCPM/issues/210), narrator presets | Assertive/clear/faster descriptions affected reference-only delivery; maintainer confirms continuation limitation. Its author's preferred forceful narrator is not a universal naturalness preset. |
| [Reddit TextToSpeech, same voice/eight emotions](https://www.reddit.com/r/TextToSpeech/comments/1x5n2pg/same_cloned_voice_8_very_different_emotions_with/) | Shows repeated reference with changed emotion prompts and samples, but uses audio.cpp. Supports auditioning style variants, not importing settings into our Python worker. |
| [Chinese Qwen 0.6B comparison](https://www.yuzhimin.com/articles/wechat-local-chinese-tts) | Author's July 30–31 test preferred ~10 s to ~30 s references on one metric; raw human/clone recordings not public. MLX/M5 and Chinese, not proof of optimal Russian BF16 reference length. |
| [Japanese VoxCPM Windows UI](https://github.com/ShibaPapaMikami/VoxCPM-Japanese-WebUI) | Voice-design history and auditioning candidate identities are useful UI ideas. Its broader training/model choices are not silently added to this studio. |
| [French practical guide](https://artpartage.org/guide-voix.html) | Audition a saved reference before a long job; distinguish designed identity from cloning. Claims of exact identical voice are stronger than we can establish. |
| [French/Quebec accent discussion](https://www.reddit.com/r/comfyui/comments/1uof5dy/looking_for_a_french_tts/) | Reference accent matters; model variant/settings insufficiently specified. Do not add accent-changing prompts to Qwen Base. |
| [German Turbo test](https://wissnox.de/ace-step/) | Eight-step Turbo with 1.7B planner; cold/warm and several seeds differ. Its 4090/1.7B results do not qualify our 0.6B planner or audible singing quality. |
| [Japanese ACE-Step trial](https://note.com/ryoma_bbc/n/na2fe74364feb) | Tests English style plus Japanese lyrics and investigates kana/kanji. Do not automatically respell protected lyrics from an anecdotal language tip. |
| [Taiwan MiniMax INT8 test](https://blog.aidec.tw/post/minimax-music3) | Includes generated samples and timings; captions did not force target duration. Do not import its 300 s suggestion into our 60 s exposed limit. |
| [Chinese MiniMax studio author](https://github.com/jinhanchen/minimax-music-studio) | Keeps user intent separate from style selection and exposes the actual caption. Its Chinese/English conditioning equivalence is explicitly untested; genre defaults must not overwrite our user's intent. |
| [Japanese MiniMax INT8/FP16 comparison](https://mikkoge.com/2026/08/20/comfyui-minimax-music-3-int8-fp16/) | Documents both apparent instrumental success and later unwanted singing, plus abrupt duration cutoffs. Its ACE comparator is XL, not our Turbo. |
| [MiniMax advanced encoder author](https://www.reddit.com/r/comfyui/comments/1vqew3j/minimax_music_3_custom_te_advanced_node/) | Exposes extra sampling parameters for exploration; explicitly offers no best settings. No speculative quality preset is imported. |

Two research checks reinforce conservative defaults. [Qwen conditioning study](https://arxiv.org/abs/2610.23678) uses
**1.7B Base**, so its ICL/X-vector outcomes do not qualify our 0.6B model. It also finds objective and listener rankings differ.
[VoxCPM2 descriptor audit](https://arxiv.org/abs/2608.00545) finds that intended style changes often accompany changes in other
attributes; it is a reason to compare timbre and text, not to treat a matching style description as success.

## Open reference candidates

**[sources] These are audio sources, not installed model components or transferable speaker embeddings.** Each collection
has its own terms. Discovery links are in Voiceover; imports still use the existing consent-based saved-voice path.

| Source | Why useful | Scope / download |
|---|---|---|
| [Kyutai voice-donations](https://huggingface.co/kyutai/tts-voices/tree/main/voice-donations), [provenance](https://huggingface.co/kyutai/tts-voices/blob/main/README.md) | Volunteer donations, CC0; original and enhanced audio available. | Use original WAVs first. Adjacent Expresso/EARS folders are non-commercial; the whole repository is not CC0. Kyutai `.safetensors` embeddings cannot replace our WAV references. |
| [Thorsten recordings](https://www.thorsten-voice.de/ueber-das-projekt/aufnahmen/), [neutral release](https://zenodo.org/records/5525342) | Speaker's own German TTS donation, CC0; neutral and acted emotional recordings. | Good candidate for reference-style comparisons; neutral archive 2.7 GB, not downloaded. Not a Russian narrator qualification. |
| [Denis 1.0](https://mozilladatacollective.com/datasets/cmiup9seu01flnv076fexaqp9) | Russian TTS donation stewarded by Open Home Foundation, CC0. | 104.52 MB archive; not downloaded. No attempt to identify speaker. Published audio/text were not post-validated: audition and check alignment before using continuation. |

Two Kyutai original files, `0a67.wav` and `1410.wav`, were explicitly approved and downloaded: **480,044 bytes each**.
Their SHA-256 values are respectively `4bd75d0ef0ad3f4e82ac075eab2a132651d2463f83bec210edeeccaf69294886` and
`8edd516de8c2171b67757cacb29e1effd3e6a8b78f5d6b035069273fadefac2b`. Source URLs:
`https://huggingface.co/kyutai/tts-voices/resolve/main/voice-donations/{0a67,1410}.wav`.
Files and isolated imports remain in `C:/Users/Apo/AppData/Local/Temp/vfr-tests/`; no production-library voice was added or replaced.
These are candidates for owner audition, not claimed "best voices". Speaker language/accent and subjective quality were not assessed by listening.

## Local evidence

**[GPU, 2026-11-06]** Explicitly approved sequential subset: **23.05 minutes**, no new models, normal Music/Voiceover route handlers,
isolated C settings/history/output. Exact configured Gemma selected `storytelling` and `calm` through the four-field contract.
The actual Text model was `150ts-Gemma4A4B-9.41-AndroidKotlin-Logic-Math-Algorithms-C32K:latest`, Q4_0,
manifest SHA-256 `61b31133798c840d6cf90e45c82fa5ea48593055085898570afc71aba88d2b62`.
VoxCPM2 reference-only, CFG 2.0 / ten steps / compile fallback unchanged; seed 71. Same three Russian fragments/two chapters
as the earlier preparation test. Style is part of the fragment key and therefore also changes the derived synthesis seed:
these are practical mode comparisons, not matched-latent proof of a causal style effect.

| Reference | Neutral / story / calm, seconds | Whisper Turbo |
|---|---|---|
| Existing designed warm woman | 19.86 / 20.53 / 22.45 | 3 fragments per output; no repairs or final flags |
| Human donor 0a67 | 22.74 / 19.73 / 22.77 | 3 fragments per output; no repairs or final flags |
| Human donor 1410 | 24.82 / 21.17 / 25.17 | 3 fragments per output; one fragment repaired in each variant; no final flags |

Finished speech MP3s: **48 kHz mono**, −17.82 to −20.39 LUFS, true peak −2.34 to −2.38 dBTP.
MiniMax: four **9.996 s / 32 kHz stereo WAVs**, seeds 71/173, 30 Euler/simple steps, CFG/AR CFG 1.7, AR top-k 50,
INT8 ConvRot, tiled decode. Both comparison arms used `[Instrumental]` and the worker's positive mode statement;
the `old-negative` label means **additional exclusions in the caption**, not an exact reconstruction of the previous worker.
No conclusion about unwanted singing is made without listening. Finished music: −14.30 to −18.39 LUFS, −1.00 to −3.21 dBTP.
First cold Vox job took 282.2 s including load/compile/verification; later jobs without repair took 72.7–80.7 s
(first story/Gemma handoff 143.5 s). Each donor-1410 job including repair took 141.3–145.4 s. MiniMax first job took
94.1 s, later jobs 41.2–43.6 s. These are job timings, not pure synthesis throughput or comparable latent runs.
**[CPU]** All thirteen decoded final outputs are finite, nonempty, with zero samples reaching full scale. This does not rule out
all clicks, pronunciation errors or perceptual artifacts. All outcomes, including repair counts, are retained in the summary.

Artifacts: `C:/Users/Apo/AppData/Local/Temp/vfr-tests/audio-presets-gpu-20261106/`:
`audio-summary.json`, raw `gemma-replies.jsonl`, `preset-integrity.json`, per-job logs/fragment verdicts,
original encoded results and thirteen constant-gain level-matched WAVs in `Comparisons/`.
`preset-comparisons.md` links every variant. The agent's available tools do **not** expose audio perception; none were listened to by the agent.

**[CPU]** 164 focused Python tests passed: audio contracts 19, Voiceover 111, Music/MiniMax/templates/localization 34.
Node audio-panel and voice-input checks, affected JavaScript syntax, Ruff (E9/F63/F7/F82/F401), catalog build (2,990 keys),
control-byte and unique-new-basename checks, and scoped `git diff --check` passed (ordinary LF/CRLF notices only).
**[UI, fake Gemma/TTS backend]** Actual browser checks cover presets preserving Russian lyrics/narration, reference mode
disabling style, Qwen hiding it, async preparation and final history style, RU→EN labels, external C output publication,
and a restored song draft in Stable Audio. The last exposed an existing async-capability ordering bug: song fields had been
evaluated before engine metadata arrived. `configureEngine()` now re-evaluates them, with a Node regression case.
An initial UI helper unexpectedly warmed real speech-input Whisper; only its proven-owned child was stopped, and the
helper now points speech input at an empty C model directory. Remaining UI checks loaded no real inference backend.
Screenshots in C Temp: `audio-presets-voice-ui.png`, `audio-presets-music-ui.png`.

**[recorded, owner listening, 2026-11-06]** For the level-matched donor-0a67 neutral/story/calm comparison, the owner selected
«Нейтральный лучше / изменения нежелательны». This favors the neutral variant for this reference and text; no separate
judgment of speaker-identity preservation or specific artifact was supplied. Experimental style control remains off by default.
This is distinct from the earlier approval of shorter assembly gaps on both TTS engines, which remains valid in its recorded scope.

**[unknown]** Listening qualification for other references/styles, long-form continuity, other languages, clear/lively/brisk
style quality, singer naturalness and instrumental-only success rates remains open. Do not generalize the 0a67 preference to all voices.
