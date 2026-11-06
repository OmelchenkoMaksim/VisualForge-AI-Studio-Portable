# Music and Voiceover preparation

Research and implementation scope: 2026-11-06. This document separates software behavior from audio qualification.

## Contract [code]

The normal Music and Voiceover generate routes create a cancellable background job immediately. The section's own
`GemmaTask` subclass prepares it before any synthesis worker starts. For Music and nonempty Voiceover delivery preferences, the actual usable Gemma selected by Text is required;
a non-Gemma chat fallback is refused. Component policy still applies through Text and the audio availability checks.
`think=False` remains unchanged. The global submission lock covers admission, never a complete answer.
The job remains busy during preparation; other modules cannot enter the gap. Director authority is explicitly inherited
by the job thread. A separate synthesis admission unloads idle Text before audio inference. Stop closes the Text owner or
the owned worker as appropriate. A bad answer gets one correction attempt, then a visible failure, without raw-prompt fallback.
A single whole-response JSON fence is accepted as transport formatting; prose, multiple objects, duplicate/extra fields and
unsupported controls still fail. Music and Voiceover use separate schemas and system templates.

The managers accept only the registered engine/mode matrix. Explicit mode, duration, seed, BPM, language, planner and voice
selections win over conflicting prose. Ordinary ambiguity is resolved conservatively; this version asks no creative questions
and has no waiting clarification state. It stores an empty clarification-decision list, rather than implying a question was asked.
No generation-time web lookup is performed.

Music keeps the original caption, final English caption, unchanged lyrics, controls, guide version, actual Gemma name and
attempt count in history and the published JSON sidecar. Gemma receives lyric section names and word count, not the protected
lyrics. It cannot return lyrics or controls from a caption pass. A numeric BPM contradicting the explicit BPM is refused.
`music_intent.py` checks common instrumentation, genre, vocal and production additions against bounded multilingual source
aliases, retaining accepted source anchors in `checked_intent`. Known unrequested/excluded additions trigger correction.
This is a conservative lexical guard, not an exhaustive semantic proof or universal translation dictionary. Schema errors
and all detected additions are reported together so the single correction can address them. Faithful negatives such as
`non-operatic` remain valid. New preparation receipts also identify the exact system guide by SHA-256.
The generator consumes the prepared caption. History reuse restores the original request, avoiding successive paraphrases.
An optional, explicit **Write lyrics if empty** selection authorizes a separate content-creation call; its input, result and
model provenance are stored as `lyric_creation`. Existing lyrics always win and are never rewritten. Missing lyrics without
that consent still fail. Dense lyrics produce a warning; they are never truncated and duration is never silently extended.
MiniMax refuses words on the same line as a leading section tag because its input parser can discard them.

Voiceover stores the delivery request separately from narration. Gemma sees only bounded delivery preferences and selected
engine/language/voice ID; it never receives a book to rewrite. Its validated pacing enum maps to actual fragment limits and
inserted boundary pauses. English free-form direction is explanatory metadata and never enters TTS text. The executable part is
`controls`, used by segmentation and FFmpeg assembly/timestamps. A later opt-in VoxCPM2 reference-only adapter adds a validated
`style` enum, serialized through the publisher's trained prefix only at the worker boundary; no SSML or invented tags.
See [Audio Presets](VisualForge-Audio-Presets.md) for its separate research, contract, GPU subset and listening limits.

| Pacing | Fragment limit | Sentence / paragraph / chapter gap, s |
|---|---:|---|
| neutral (empty request) | 300 | .25 / .6 / 1.5 |
| conversational | 300 | .16 / .45 / 1.2 |
| unhurried (explicit slow pacing) | 300 | .3 / .7 / 1.5 |
| brisk (explicit quick pacing) | 260 | .12 / .35 / 1.0 |

These are conservative timing alternatives, **not a qualified improvement in naturalness**. They do not change speaking rate
inside a fragment or give a synthetic reference human prosody. An empty delivery field uses the deterministic neutral baseline without loading Gemma (attempts=0, gemma_model=null), especially for short Director lines. Unrequested pacing stays neutral. The UI always explains this
capability limit, irrespective of Gemma's `unsupported` classification. A natural-storytelling request selects conversational
flow; warmth alone does not authorize longer pauses. Approved delivery plans are cached by request, engine, language, voice ID,
Gemma tag/manifest SHA-256 and guide version, bounded to 64 entries. Narration and synthesis fragments retain their existing independent cache.
Changing only assembly gaps can reuse all audio. Changing segment text changes its key. VoxCPM continuation keys now also include
the exact reference transcript hash. Chapter timestamps use actual audio lengths and the chosen gaps.

Source chapters remain separate from normalization and engine pronunciation (`fragments.json` has `text` and `speak`).
Sidecars identify the source chapter hash, reference hash, exact transcript, normalization flags and pronunciation replacements.
New saved references retain original uploaded bytes alongside the existing derived 24 kHz mono sample. Old references are not
reprocessed. A transcribed recording longer than 30 seconds is refused before the existing sample cap can misalign its exact transcript. No automatic denoising, voice substitution or change to reference/continuation mode is performed.
Named-month Russian dates have a bounded ordinal rule (e.g. «вернулась 5 мая» → «вернулась пятого мая»); numeric dates and
unhandled grammatical constructions remain limitations. Names and other written content are never creatively changed.

## Model guides and research [sources, code]

Research below was read on 2026-11-06. Publisher documentation describes upstream capabilities; the local worker/workflow
defines which are exposed. Community reports are first-hand hypotheses, not local measurements.

| Installed pair | Guide encoded in the manager | Limits / rejected transfers |
|---|---|---|
| ACE-Step 1.5 Turbo / instrumental | Concise English genre, requested instruments, mood, rhythm feel, modest development; <=512 characters | No singing or invented lyrics; eight steps remain fixed |
| ACE-Step 1.5 Turbo / song | Same concise caption plus requested vocal character; separate original lyrics/sections | New voice, no singer cloning; preserve explicit BPM/language |
| MiniMax Music3 INT8 / instrumental | Global Metadata and Arrangement, only supported requested detail | Vocal-free output is not guaranteed; no ACE planner |
| MiniMax Music3 INT8 / song | Global Metadata, Vocal Details, Arrangement, aligned to supplied sections; <=1600 characters | Section labels alone on lines; duration is an upper bound; no promise of exact tempo/language |
| Stable Audio 3 Medium / audio | Audible source, texture, mood, density and development; distinguish music/ambience/events; <=1000 characters | Only caption, duration, seed; no lyrics, structured BPM, categories or native reprompt in this workflow |
| Qwen3-TTS 12Hz 0.6B Base / speech | Delivery intent → conservative pacing only | Installed `generate_voice_clone`, `x_vector_only_mode=True`; no arbitrary instruct; no transcript conditioning |
| VoxCPM2 / speech | Pacing; opt-in bounded trained style control for reference-only cloning | Continuation cannot use style; Voice Design remains separate; language field is not conditioning |

ACE's optional 5Hz 0.6B LM creates musical semantic codes and can fill missing musical metadata. On adds another load and AR stage;
off uses direct DiT. Caption and language rewriting remain disabled, preserving one caption owner. Explicit BPM/duration are retained
by the installed metadata-update logic. The evidence does not establish that on is universally better. Keep the user's choice;
do not transfer XL-SFT/4B-MLX comparisons to this Turbo/0.6B/Windows setup.

MiniMax retains the installed INT8 ConvRot conversion, AR CFG 1.7/top-k 50, 30 Euler/simple steps and tiled decode. Nothing here
changes quantization, license metadata or the proper native-44.1-to-published-32-kHz resampling. Stable Audio retains its Medium
workflow, eight LCM steps and CFG 1; upstream's different example sampler and longer duration are not imported.

Qwen's family-level description of instruction control does not prove support in this Base checkpoint. Its installed API and
the publisher's Base clone examples are the authority. Clean single-speaker samples with natural prosody matter; speaker embeddings
do not guarantee preserved delivery. Reports about 1.7B ICL reference echo do not justify changing the 0.6B embedding path.
VoxCPM2's reference-only path isolates voice identity, whereas transcript continuation conditions on the sample/text pair.
The later publisher-card/demo review confirms a trained style prefix for reference-only cloning, with a maintainer-confirmed
continuation exclusion. The opt-in adapter keeps this conditioning outside protected narration; see the separate presets report.
CFG 2.0, ten steps, no denoiser, both compilation fallbacks and ASCII compile-cache paths remain unchanged.

Precise refresh references:

- ACE: [publisher tutorial](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/Tutorial.md),
  [inference contract](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/INFERENCE.md),
  [caption contamination report #677](https://github.com/ace-step/ACE-Step-1.5/issues/677),
  [different XL-SFT/MLX experiment #1268](https://github.com/ace-step/ACE-Step-1.5/discussions/1268).
- MiniMax: [publisher card and prompt representation](https://huggingface.co/MiniMaxAI/MiniMax-Music3),
  [Comfy conversion](https://huggingface.co/Comfy-Org/MiniMax-Music-3),
  [checkpoint input contract in Diffusers](https://huggingface.co/docs/diffusers/main/en/api/pipelines/minimax_music3),
  [8-bit vocal-free attempts #23](https://huggingface.co/MiniMaxAI/MiniMax-Music3/discussions/23).
- Stable Audio: [exact Medium card](https://huggingface.co/stabilityai/stable-audio-3-medium),
  [Comfy package](https://huggingface.co/Comfy-Org/stable-audio-3),
  [reprompt discussion #7](https://huggingface.co/stabilityai/stable-audio-3-medium/discussions/7).
- Qwen: [exact 0.6B Base card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base),
  [publisher API](https://github.com/QwenLM/Qwen3-TTS/blob/main/qwen_tts/inference/qwen3_tts_model.py),
  [12Hz echo report #386](https://github.com/QwenLM/Qwen3-TTS/issues/386),
  [different 1.7B ICL case #341](https://github.com/QwenLM/Qwen3-TTS/issues/341).
- VoxCPM2: [publisher card](https://huggingface.co/openbmb/VoxCPM2),
  [publisher clone API](https://github.com/OpenBMB/VoxCPM/blob/main/src/voxcpm/core.py),
  [mode-dependent style report #210](https://github.com/OpenBMB/VoxCPM/issues/210).

## Qualification

[CPU] Focused contract tests use fake Text and test all registered pairs, exact Russian protected data, bounded invalid-output
correction, cancellation, missing content, lyrics-creation consent, cache invalidation and inherited Director admission.
Integration tests confirm the prepared caption reaches the Comfy backend and sidecar/external output path; Voiceover uses the
chosen pauses in real FFmpeg assembly and reuses cached fake-worker fragments. These are software checks, not generation quality.

[UI, fake backend] A separate real bridge and the current frontend use temporary state on C. Checks cover model switching,
separate delivery/narration fields, background preparation, Music cancellation, history and published paths. Audio is synthetic
test-fixture output, not model output.

[GPU, 2026-11-06] The owner explicitly approved the sequential 35–60 minute set. Isolated C state and output were used, with
no downloads or changes to installed output preferences. The configured model was
`150ts-Gemma4A4B-9.41-AndroidKotlin-Logic-Math-Algorithms-C32K:latest`, Q4_0, manifest SHA-256
`61b31133798c840d6cf90e45c82fa5ea48593055085898570afc71aba88d2b62`. All five Music pairs and both TTS A/B pairs completed
through normal route handlers. These were in-process route calls, not browser-triggered generation. Seed 71; ACE planner on;
unchanged inference profiles; sparse original Russian lyrics in the two songs. Music preparation guide: `audio-music-v2-2026-11-06`;
speech: `audio-voice-v2-2026-11-06`. Final files measured with FFmpeg/ffprobe:

| Pair / output | Actual seconds | Rate / channels | Integrated LUFS | True peak dBTP |
|---|---:|---|---:|---:|
| ACE instrumental | 10.000 | 48 kHz stereo | -18.59 | -1.00 |
| ACE song | 20.000 | 48 kHz stereo | -15.35 | -0.98 |
| MiniMax instrumental | 9.996 | 32 kHz stereo | -16.26 | -1.00 |
| MiniMax song | 19.992 | 32 kHz stereo | -18.50 | -1.00 |
| Stable Medium / music | 10.031 | 44.1 kHz stereo | -13.55 | -0.54 |
| Qwen / neutral MP3 | 22.340 | 24 kHz mono | -19.36 | -2.33 |
| Qwen / prepared MP3 | 21.890 | 24 kHz mono | -19.33 | -2.36 |
| VoxCPM2 / neutral MP3 | 19.860 | 48 kHz mono | -17.82 | -2.34 |
| VoxCPM2 / prepared MP3 | 19.410 | 48 kHz mono | -17.82 | -2.09 |

First observed wall times (load, preparation and generation included) were Gemma 187.2 s during its rejected cold preparation,
ACE instrumental/song 143.9/53.6 s, MiniMax instrumental/song 147.5/62.8 s, Stable 94.7 s. Later Stable took 40.1 s and final
MiniMax 49.3/61.5 s with warm file caches. These are job timings across different prompts/cache states, not controlled inference
benchmarks. TTS baseline totals were Qwen 99.3 s and compiled Vox 243.5 s; prepared cached assembly was 8.3/7.8 s. Cold HDD loads
and compilation must not be confused with synthesis speed. Free whole-card memory returned from about 15,378 MiB before work
to 15,382 MiB afterwards. No continuous whole-card peak was recorded; worker allocated/reserved figures are not that peak.

[GPU/CPU] All final decoded files had finite, nonempty samples and zero full-scale samples. Music loudness was measured,
not normalized to the audiobook target. A previous MiniMax song had 11 decoded full-scale samples and +0.36 dBTP; its old
save path clamped the decoder float to PCM before resampling. `save_minimax_audio` now resamples float first, measures true peak,
uses only necessary constant attenuation to -1 dBTP and adds 5 ms edge ramps before PCM16. A controlled over-range CPU fixture
checks amplitude/stereo preservation, unchanged duration and clean edges. The two real reruns reached -1 dBTP and zero final
edge samples. Their captions differed from the earlier file, so they are not an auditory A/B of that fix. This prevents delivery
clipping/clicks, not an unfinished musical phrase: the final 50 ms of MiniMax still contain substantial energy.

[GPU/recorded] Speech used the existing **VoxCPM2-designed built-in warm woman**, ID `d0350461667a778f42216c10c0a6d964`,
not a newly consented human reference. Both engines synthesized three fragments in two chapters. Whisper Turbo checked each
fragment with zero repairs/flags; the prepared jobs reused all three audio fragments and ASR verdicts. Identical fragment keys
make the A/B isolate inserted gaps: paragraph .60→.45 and chapter 1.50→1.20, total .45 s shorter per engine. Internal phrase
prosody and selected voice were unchanged. Names Анна, Ёлкин, Мария, Алексей, date 5 мая 2025 and numbers 23/2 appeared in the
per-fragment transcripts; this manual transcript inspection supplements the comparison that can ignore numeric distinctions.
It does not prove pronunciation or naturalness. Original MP3s retain the final limiter; comparison WAVs are level-matched near -20 LUFS.

[recorded, owner listening, 2026-11-06] The owner listened to the paired comparisons and replied **«новые паузы лучше у обоих»**.
This supports the conversational gaps for this short passage and built-in reference. It is not a rating of timbre, emotion,
human cloning or long-form continuity. The testing agent measured the files but did not hear them; its tools expose playback,
not audio perception.

[GPU, Gemma only] Additional bounded probes prepared an explicitly authorized Russian lyric-writing request, a Spanish piano
request, Stable rain ambience, and calm/intimate delivery without whispering or drawn-out sentences. The last chose conversational,
not unhurried, and flagged unsupported performance control. Generated lyrics were 48 words for 20 s: the density warning fired
and nothing was truncated. These extra probes did not synthesize music/speech and do not qualify their audio quality.

[UI, real results] A read-only current bridge/frontend opened the real C histories and files. Music and Voiceover playback,
seeking, source/prepared detail and output paths worked; Voiceover seek was read back at 9.687/19.410 s with no decoder error.
The history now names the actual TTS engine. Whisper's summary says “flagged for review”, not “still wrong”. Screenshots:
`vfr-tests/audio-real-music-ui.png`, `audio-real-voice-ui.png`. The earlier fake-backend UI checks remain separately labelled.

[unknown] Several seeds, maximum durations, dense-lyric singing, instrumental-only/music-style compliance, sound-event audio,
long-form continuity under new gaps and clean-human-reference cloning remain unqualified. Music was not listened to in this run.
There is no fresh hour-long run, real-GPU Stop test at every phase, or new full Director film qualification. CPU/fake-backend
cancellation/resume and inherited Director admission passed; historical long runs retain their original provenance.


## Reproducible opt-in qualification

`tools/audio_preparation_smoke.py` refuses execution without `--yes-gpu` (owner approval required, estimated 35–60 minutes).
It uses normal route handlers, the actual configured Gemma and installed models, sequential jobs, separate C state/output/settings,
per-fragment Whisper and finished-file FFmpeg/ffprobe measurements. Five Music pairs and neutral/prepared speech from each TTS
are retained, including failures; it writes level-matched speech WAV comparisons for a human listener. It uses existing built-in
references, never assumes consent for an arbitrary saved human voice, never downloads models and never changes installed output
folder preferences. The command was executed with approval; `--retry-failed` reuses its C caches, skips qualified completed
labels and retains prior failures. Raw Gemma fixture replies, including rejected attempts, are kept in `gemma-replies.jsonl`.
Artifact root for this run: `C:/Users/Apo/AppData/Local/Temp/vfr-tests/audio-gpu-20261106-r2/` (`audio-summary.json`,
`audio-integrity.json`, `Comparisons/`, `Results/`). These temporary artifacts are not portable application assets.
`audio-qualified-comparisons.md` indexes the final nine outputs and four level-matched listening files; the summary also
records the owner's exact feedback separately from the agent's measurements.

Failures retained there explain three production fixes: JSON fences; copied protected-field names from a generic rewrite
template; and unrequested guitar/piano/folk/pop in song captions. Reporting only the first invented instrument required too
many corrections, so all errors now share the one retry. The final MiniMax song used the separate caption-only template;
earlier successful instrumental captions remain valid under the final guards. Stable ambience's faithful “ambient soundscape”
was initially rejected as a genre. The guard now distinguishes ambient sound/soundscape from an ambient music genre, with
a CPU replay covering the actual phrasing; the real corrected caption had already passed without those two words.

Software evidence recorded on 2026-11-06: 208 focused Python tests passed after the GPU-driven fixes, 9 real-bridge/fake-backend socket tests passed;
subsequent targeted tests cover reference/transcript preservation, manifest-aware cache invalidation and the shared GPU/Text
lifecycle. Ruff checks (E9/F63/F7/F82/F401) and changed JavaScript syntax passed. `audio_panel_ui_test.js` loads the production
panels and i18n runtime and verifies no duplicate submission, separate delivery input and cards surviving an empty-to-filled
history followed by a language change. Live browser checks found and confirmed the date-preview and empty-history binding fixes.
The UI proof screenshot is temporary on C (`vfr-tests/audio-ui-proof.png`); it shows fake-backend evidence only.
After the full regression, 18 targeted checks passed, including a controlled owned-child cancellation during assembly:
nothing was published, finished fragments remained cached, and the next normal job reused them. Director actor inheritance
also refused unrelated busy Video work. These cancellation/admission checks are [CPU], not new real-GPU stop qualification.
