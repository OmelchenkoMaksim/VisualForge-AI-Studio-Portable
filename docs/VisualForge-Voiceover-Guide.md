# Voiceover (text to speech)

The **Озвучка / Voiceover** tab turns a long text into one audio file with a cloned voice: a pasted text or an EPUB, FB2, DOCX or
TXT file becomes WAV, MP3 or an M4B audiobook with chapters. It is not limited to books: any text works, a text without headings is
one part. Everything runs locally. Status of this phase: **TESTED ON OUR GPU with one short probe** (see "Measured"); the tab itself was
driven only against a fake worker, and no long job has been run.

## Flow

1. **Text.** `POST /api/voiceover/prepare` parses the text or the uploaded file (`text_prep.py`), keeps it under a draft id in
   `state/books/` and returns chapters, character and fragment counts and a duration *estimate* (14 characters per second).
2. **Voice.** The voice list has three kinds of entry: the **built-in voices** (see "Built-in voices"), the user's saved voices and the last
   entry «Свой голос…», which opens the form for one's own recordings. A saved voice is made of one recording or of up to five (`audios`, 8 MB
   each, 24 MB together) that `voice-add` converts with ffmpeg to 24 kHz mono WAV and joins with 0.4 s of silence into one sample of at most 45 s (a
   single recording is cut at 30 s); it is stored with a JSON card (name, length, parts, SHA-256) in `UserData/Voiceover/Voices`. The user's name and a
   confirmation that they may use the voice are required. A transcript can only be given with a single recording. The sample never leaves the machine.
   The card records the confirmation, the studio does not verify it.
3. **Job.** `POST /api/voiceover/generate` validates the request, claims the GPU (`GPU.claim('voiceover', 4000)`), and starts one
   owned worker process (`qwen_tts_worker.py`, in `Runtime/Video/python` with `voice-packages`, as the Video voice cloning does).
4. **Assembly.** The manager joins the fragments with pauses (0.25 s between sentences, 0.6 s between paragraphs, 1.5 s between
   chapters), normalizes loudness with `loudnorm` (I=-18, TP=-1.5) and encodes WAV (PCM 16), MP3 (96 kbit/s) or M4B (AAC 64 kbit/s with
   chapter marks from the real fragment lengths). The result is published to the output folder with a JSON sidecar.

## Engines

The page offers every engine whose files are installed (`registry.ENGINES`, one worker script each; a request carries `engine`). Fragments of different
engines never share cache files (the engine id and revision are part of the fragment key).

| | Qwen3-TTS 0.6B Base (default) | VoxCPM2 |
|---|---|---|
| Output | 24 kHz | **48 kHz** |
| License | Apache-2.0 | Apache-2.0 |
| Weights | 1.8 GB, part of the Video section (`video.model.qwen3-tts`) | 4.96 GB, `voiceover.model.voxcpm2`, `Models/Voiceover/VoxCPM2` |
| Packages | `Runtime/Video/voice-packages` | `Runtime/Video/voxcpm-packages` (voxcpm 2.0.3, pydantic) beside the Qwen ones; librosa, soundfile and transformers are borrowed |
| Cloning | speaker embedding of a 3-30 s sample | reference sample alone (default), or sample plus its transcript («continuation») |
| Video memory | 2.3 GB model, 7 GB with batches of 8 | 5.1 GB model, about 6.1 GB at work |
| Speed | 0.28-0.31 s of work per second of audio (batches of 8) | 0.35 with `torch.compile` (default), 0.79 without; one fragment at a time, no batch interface |

VoxCPM2 is called as in its own package (`VoxCPM(...).generate(text, reference_wav_path=...)`) without the denoiser (it would need ModelScope and a download)
and without text normalization (ours runs before). `torch.compile` costs about 110 s on the first call of a process and is cached in the job state
(`TORCHINDUCTOR_CACHE_DIR`); it is lazy, so a missing compiler would fail at the first fragment, and the worker then reloads the model without it
(`compile_failed` event). `VISUALFORGE_VOICEOVER_VOXCPM_OPTIMIZE=0` switches it off. The voice card has an optional **transcript**; with it VoxCPM2 continues the
sample, without it uses the sample as a reference (see "Measured: VoxCPM2"). The GPU claim is 9,000 MB for VoxCPM2 and 4,000 MB for Qwen3-TTS.

## Built-in voices

Eight voices (four female, four male; names «Женский · тёплый», «Женский · дикторский», «Женский · молодой», «Женский · низкий, спокойный», «Мужской · глубокий»,
«Мужской · дикторский», «Мужской · молодой», «Мужской · старший, неторопливый») are in the voice list under «Встроенные голоса». The repository keeps no audio
(`tools/check_repo.py` forbids it), so the studio **makes them on the user's machine**, once: the voice step shows «Создать встроенные голоса» while some are missing.
The button needs VoxCPM2 (`POST /api/voiceover/voices-build`, GPU claim 9,000 MB; Stop is `voices-build-cancel`); the state is part of the `voices` answer
(`defaults`: state, progress, ready, wanted, `can_build`).

How a voice is made (`defaults_worker.py`, descriptions in `App/config/default-voices.json`): VoxCPM2 **Voice Design** turns an English description («A woman in
her thirties, warm and soft voice…») into a new voice speaking a fixed 13-second Russian passage; four takes per voice (the seed is fixed, the result still differs
a little between machines). The model is freed, Whisper listens to every take and a take is **offered only if** it is heard back word for word, its median pitch fits the
gender (female ≥ 150 Hz, male ≤ 165 Hz, plus optional `pitch_min` / `pitch_max` of the voice, which keep two voices of one gender apart), it is 9–24 s long and not clipped; among those
the pace closest to calm narration (14 characters per second) wins (`defaults.choose`). The chosen take is trimmed, normalized to about -3 dBFS and stored as a 24 kHz mono sample
in `UserData/Voiceover/Voices/builtin/<id>.wav` with a JSON card (`builtin: true`, the description, the measurements). The ids are fixed (`defaults.voice_id`), so a rebuilt voice keeps its cached
fragments only if the sample is byte-identical. Only missing voices are made; a voice without an acceptable take is reported in `defaults.failed`. Built-in voices cannot be deleted.
Because the samples come from a description, **no real person's voice is copied**.

Measured (2026-10-29, this machine, one run of the maker, then each voice used as a clone sample for four sentences with each engine):

| | VoxCPM2 | Qwen3-TTS |
|---|---|---|
| Words heard wrongly by Whisper, 8 voices × 45 words | 0 | 4 (one sentence, «растаял» heard as «расстоял», in four voices) |
| Similarity to the voice sample (speaker encoder cosine) | 0.989–0.993 | 0.986–0.990 |

All 32 takes of the maker were heard back correctly; the takes of one description differ a lot in pitch (for «man-announcer» 126–190 Hz), which is why the
description alone does not decide the voice and a take is chosen. The speaker encoder compresses all voices into a narrow band (different voices still score 0.89–0.98 against each other,
male against female about 0.93–0.95), so these numbers show that cloning keeps the sample's identity, not how different the voices sound. **Not verified:** how natural the voices sound to a
listener; only the owner can judge that by ear (every voice has a player in the voice step). A voice one does not like can be deleted by removing its files from `builtin/` and
changing its `seed` or description in `default-voices.json`, then pressing the button again.

A real job (5 paragraphs) with «Женский · тёплый» on Qwen3-TTS and «Мужской · глубокий» on VoxCPM2 finished with every fragment verified and nothing flagged.

## Fragments, resume and cache

The unit of work is a **fragment**: a few sentences, at most 300 characters (`text_prep.fragments`). Its file name is a SHA-256 over
model revision, language, seed, voice hash and the text (`fragment_key`), and finished fragments live in
`%TEMP%/VisualForgeVoiceover/<install>/cache/<key>.wav` on the system drive. A worker skips fragments that already exist, so
**a stopped, failed or repeated job continues where it left off**, also in a new job and after a restart of the studio. Changing the
text of one sentence, the voice, the language or the seed re-synthesizes only what depends on it. Writes are atomic (`.tmp` then
rename). About 170 MB per hour of speech; "Clear the fragment cache" frees it when nothing runs.

The worker loads the model once and synthesizes the fragments in batches (see "Speed"). The seed of a batch is derived from the key
of its first fragment, so a re-run of the same job is reproducible. A cheap plausibility check inside the worker (finite, not silent,
length between 0.3x and 3x of the expected speaking time) retries a fragment up to three times with a shifted seed.

## Verification by speech recognition (phase 2)

After synthesis `verify_worker.py` (Music runtime Python, the best installed Whisper model of the voice-input set: turbo, large-v3 or small)
recognizes every fragment on its own, in batches of 8, and `voice_comparison.py` counts the wrong words against the source text. A fragment is
**suspicious** when nothing was heard, when it has 4 or more wrong words, or when more than 15 % of its words are wrong (more than 34 % for
fragments of up to 4 words). Case, punctuation and «ё» are ignored; **digits are not compared** (recognition writes a spoken number as digits, as a
different number or as words), so a mispronounced number goes unnoticed. The first version of this rule forgave up to four words per number and
hid a real misreading in a real run («стр. 17» heard as «стрер, 7 си цить»); the rule was changed after that observation.

The manager runs: synthesis, recognition, then up to **two repair rounds**: the cache files of the suspicious fragments are deleted and only
those are synthesized again with shifted seeds (`seed_shift`), then recognized again. What is still wrong afterwards is **reported, not hidden**:
the job finishes as `done` with `flagged` (chapter, text, what was heard, wrong words) and `verification` (model, fragments checked, voiced again).
The page lists the flagged fragments with a player for each (`GET /api/voiceover/fragment`, only keys of that job's report), and the advice is to fix the word
in the pronunciation dictionary or the text and run again (only changed fragments are synthesized). The better of two takes is not kept: the last take wins.
Verification is a request option (`verify`, a checkbox, on by default). Without an installed Whisper model, or if the recognition process fails, the job
still completes and says why checking was skipped; Stop works during every stage.

Each stage is its own process, so the video card is free between them; every repair round pays a model load (Qwen and Whisper), which is why the
rounds work on all suspicious fragments together.

## Text preparation

- Readers use the standard library only: EPUB (container, OPF spine, XHTML), FB2 (XML, notes body skipped), DOCX (heading styles
  start chapters), TXT (UTF-8, UTF-16 with BOM, cp1251 fallback). Archives are limited to 120 MB unpacked and XML with entity declarations is
  refused instead of defended against.
- Plain text chapters start at lines such as «Глава 3», «Часть II», «Пролог», «Эпилог» or a lone Roman numeral.
- Sentences are split conservatively: not after common Russian abbreviations or single letters (г., в., А. С.), and not when the
  next word starts in lower case. A long sentence is cut at `;` `:` `,` ` — ` or a space. Fragment text is never lost or reordered (tested).
- The **pronunciation dictionary** (`word=how to read it`, one per line, case-sensitive, applied before splitting and before the
  normalization below) is the only stress control: the model has no stress markup.

## Built-in pronunciation dictionary

`App/config/voiceover-pronunciation.json` holds respellings for words one engine garbles, chosen by measurement: each variant was voiced 8 times in several
sentences and the result was judged by Whisper (not by guessing). A rule has `match` (lower case), `kind` (`word` = the whole word, `stem` = a
beginning that keeps its ending), `replace`, `engines` (a respelling that rescues one engine can hurt another) and optional `keep_case: false`.
The rules are applied to each fragment after the user's dictionary and after normalization (Russian only, checkbox «Встроенный словарь
произношения», on by default); the file is read per job, a damaged one is reported in the log and ignored. The fragment cache key contains the
respelled text, so changing a rule voices the affected fragments again. Verification compares against the **written** text: the heard text goes
through the same user pairs and rules, so «подаррок» is not counted as a mistake. For a flagged fragment the report lists the words
recognition lost (`suspects`); a click adds `word=` to the user's dictionary.

Shipped rule and its measurement (2026-10-28, Qwen3-TTS, the owner's voice sample, 8 takes per context):

| Written | Sentence start (3 contexts, 24 takes) | In the middle of a sentence (8 takes) |
|---|---|---|
| «Подарок» (as is) | 3 correct (the case «Подарок, сказала она, нельзя прятать» alone: 0 of 8) | 8 |
| «подаррок» | 17 | 8 |
| «Падарок», «подарак», stress mark «пода́рок», «пода+рок», hyphen, «ПОДАРОК», «…», «—» | 0–11 | 0–8 |

«Подарки», «подарком» and every other form were read correctly as is, so only the nominative singular is respelled (`kind: word`). VoxCPM2 reads
the word correctly (47 of 48), so the rule is for Qwen3-TTS only; stress marks and «+» broke the word in both engines. A real job of five
paragraphs with eight «подарок» forms then had every one recognized correctly (4 of them at a sentence start). **Not a guarantee:** about two
thirds of the takes are right, not all; the repair rounds give a second and third try. A single wrong word does not reach the verification
threshold (15 % of the fragment's words), so such a word is not reported either: add other words to the file only after measuring them like this one.
Other words are not in the file because no measurement showed them wrong (about one consistently garbled word in 7,670 in the long tests).

## Normalization of numbers and abbreviations

The model reads digits and abbreviations unreliably (a real run turned «стр. 17» into «стрер, 7 си цить»), so for Russian text `speech_normalization.py`
rewrites them into the words a person says, after the user's dictionary and before the text is split (the option `normalize`, a checkbox, on by default;
other languages keep their digits). What is rewritten, with the context it uses:

| Written | Spoken |
|---|---|
| `в 1998 году`, `с 1998 года`, `к 2000 году`, `в 1990 г.` | year as an ordinal, declined by the noun or the preposition («в тысяча девятьсот девяносто восьмом году», «с … восьмого года») |
| `XIX веке`, `XX в.` | «девятнадцатом веке», «двадцатом веке» |
| `5-го`, `3-м`, `2-й`, `1-я` | ordinal in the case and gender of the suffix |
| `50%`, `3,5%`, `3,5 кг`, `0,05` | «пятьдесят процентов», «три целых пять десятых процента», «… килограмма», «ноль целых пять сотых» |
| `21 руб.`, `3 км`, `5 кг`, `12 тыс.`, `3 млн`, `-5 °C` | the number with the unit in the right plural form, «минус пять градусов Цельсия» |
| `14:30`, `9:05` | «четырнадцать тридцать», «девять ноль пять» |
| `1 минуту`, `2 минуты`, `2 дома`, `22 окна`, `21 неделя` | «одну», «две», «два», «два», «одна» (gender and case from the ending of the next word) |
| other whole numbers up to 15 digits, `007` | cardinals, «ноль ноль семь» |
| `т.е.`, `т.к.`, `т.н.`, `в т.ч.`, `и т.д.`, `и т.п.`, `и др.`, `и пр.`, `напр.`, `проф.`, `акад.`, `им.`, `ок.` | «то есть», «так как», «так называемый», «в том числе», «и так далее», «и тому подобное», «и другие», «и прочее», «например», … |
| `стр. 17`, `рис. 5`, `гл. 3`, `табл. 2`, `ул. Ленина`, `см. рис. 5`, `№ 5` | «на странице семнадцать», «рисунок пять», «смотри главу три», «номер пять» (case from the preposition before it) |

The full stop of an abbreviation that ends a sentence is kept («и т.д. Потом» becomes «и так далее. Потом»). **Not** rewritten: dates (`12.05.2024`),
year ranges (`1941-1945 гг.`), mixed tokens (`A4`, `mp3`), and a plain «г.», «м», «ч.» whose meaning is ambiguous. **Known inexactness:** a plain
number is spoken in the nominative or accusative («о 5 минутах» becomes «о пять минутах»), the gender of «один/два» is guessed from the next word (a
masculine noun in «а» such as «мужчина» gets «одна»), the head of a year («тысяча девятьсот») is not declined.
When the text is prepared, the page shows up to six sentences «before → after» and the number of places that will change.

The comparison with what recognition heard applies the same normalization to the heard text and skips number words, because Whisper writes
«и т.д.» and «17» where the speaker said «и так далее» and «семнадцать».

## Files and API

`App/server/voiceover/`: `pronunciation_rules.py` (built-in rules, data in `App/config/voiceover-pronunciation.json`), `voice_comparison.py`, `text_prep.py`, `voiceover_registry.py` (engine contract, request validation), `qwen_tts_worker.py`, `audio_assembly.py`,
`voiceover_manager.py` (voices, drafts, one job at a time, history `UserData/Settings/voiceover-history.json`, bounded log in
`UserData/Logs/Voiceover/`), `verify_worker.py`, `voice_comparison.py`, `speech_normalization.py`, `voiceover_routes.py`, `voiceover_storage.py`. Frontend: `voiceover-panel.js`, `voiceover-panel.css`, markup in `studio-index.html`.
Strings: `App/strings/voiceover-strings.json`. Routes under `/api/voiceover/`: `availability`, `voices`, `voice-audio`, `voice-add`,
`voice-delete`, `voices-build`, `voices-build-cancel`, `prepare`, `generate`, `active`, `status`, `history`, `result` and `fragment` (Range supported), `logs`,
`cancel`, `delete`, `settings`, `clear-cache`. The built-in voices add `default_voices.py`, `defaults_worker.py` and `App/config/default-voices.json`.

GPU: Voiceover is the fifth module of the coordinator (`gpu_coordinator.py`). The worker exits with the job, so nothing stays in video memory.
The banner's Stop button cancels it; Stop keeps the finished fragments. Event history: a `Voiceover` main event per job with a
`SpeechSynthesis` child written by the worker.

## Limits

- Two engines (see "Engines"). Qwen3-TTS 0.6B Base is used in speaker-embedding mode (no transcript of the sample). A Qwen3-TTS 1.7B model and other engines
  (MOSS-TTS v1.5, Chatterbox V3) are candidates, not integrated.
- Languages offered are those of the Qwen3-TTS API; only Russian is the target of this work.
- Timbre and pace stability over an hour, stress marks and numbers are **not measured**.

## Tests

`tests/test_voiceover.py` (76 tests, CPU; the stages are also covered with `tests/fake_verify_worker.py`): text readers and fragmenting, validation, the timeline, real ffmpeg assembly of all three
formats with chapter titles read back through ffprobe, and the manager against `tests/fake_voiceover_worker.py` (job, publish,
reuse of the cache, Stop and resume, worker failure, one job at a time, voice rules, deletion, missing files, cache clearing).
`tests/test_bridge_smoke.py` walks the new routes through the real bridge and imports `studio_server.py` in isolated mode, which
reproduces the embedded Python's `sys.path`.

## Measured (2026-10-28, RTX 5070 Ti 16 GB, models on the H hard disk, one probe)

`VoiceoverManager` with the installed runtime and the real Qwen3-TTS 0.6B model, a throw-away output folder, M4B, seed 7. The voice
sample was synthesized with the Windows «Irina» voice, so **cloning a real human voice was not tested**. Text: two chapters, 816 characters,
5 fragments, with a number, an abbreviation and dialogue.

| What | Result |
|---|---|
| Audio produced | 62.7 s (estimate from the text: 59 s) |
| Whole job | 151 s: model load and first fragment about 94 s (cold start, includes warm-up), the other four fragments 53 s for 39 s of audio |
| Speed after warm-up, one fragment at a time | about 1.35 s of work per second of audio, i.e. slower than real time (this was the first version; batching below made it about 4.5 times faster) |
| Peak video memory of the worker | 2.74 GB |
| Words read correctly | 97 % (whisper-turbo on each fragment separately, 135 words, case and «ё» ignored); no fragment was flagged |
| Chapters in the M4B | both present with the right titles and times (ffprobe) |
| Output | AAC 24 kHz mono, 62.8 s, true peak -1.3 dBFS, loudness range 3.4 LU |

Findings: «в т.ч.» was read as «в течеич», so abbreviations need a rule or the dictionary; Whisper's own 30-second chunking
dropped a sentence when the whole file was transcribed at once (19 % apparent errors), so verification must work per fragment, as planned.
The probe file is `UserData/Results/Voiceover/Probe - 0893d2bb….m4b` (a Windows voice, kept for listening). Listening quality was judged only
by measurement, not by ear.

## Speed: batched synthesis (2026-10-28)

Decoding is latency-bound (a small model, and a code-predictor call for every audio frame), so the card is mostly idle when one fragment is
synthesized at a time. `generate_voice_clone` accepts lists, so the worker synthesizes several fragments together
(`worker.make_batches`, length-sorted so a batch does not wait for one long member) and builds the voice prompt once instead of
re-encoding the sample for every call. Benchmark: the same 8 fragments (84-89 s of audio), model already warm, StarCraft II idling on the card
(9-11 % load), per-fragment whisper-turbo check of every output:

| Batch | Work per second of audio | Peak video memory | Words with errors |
|---:|---:|---:|---:|
| 1 (before) | 1.35 s | 2.7 GB | 2.1 % |
| 2 | 0.70 s | 3.4 GB | 2.1 % |
| 4 | 0.47 s | 4.7 GB | 2.6 % |
| 8 | 0.31 s | 6.7 GB | 2.1 % |

Pace (seconds per character) was the same in every mode, so batching does not change how fast the voice speaks. The default is 8
(`VISUALFORGE_VOICEOVER_BATCH`, 1-32). The worker reads the free video memory after loading the model and uses
`fit_batch`: about 0.6 GB per fragment plus a 1 GB margin, never below 1; on a CUDA out-of-memory error a batch is halved instead of failing the job.
So a game that holds several GB makes the batch smaller, not the job fail.

End to end with the batched worker (the installed runtime, 4 chapters, 2,289 characters, 12 fragments, a Windows-voice sample):
**170 s of audio in 71 s** including the model load (46.9 s of synthesis, batch 8, peak 7.0 GB, 12.6 GB were free), against
62.7 s of audio in 151 s with the first version; 1.6 % of words wrong over 375 words, no fragment flagged. A 28-minute book was
then measured end to end (next section).

Consequences: a fragment's audio now depends on the group it was synthesized with (the seed comes from the first fragment of the group), so
the same text is not bit-for-bit reproducible across runs with a different batch size; the cache still maps one key to one finished file,
so a cached fragment stays valid. Not tried: `torch.compile`, CUDA graphs, batch sizes above 8 and the 1.7B model.

### Measured: a 28-minute book (2026-10-28, same machine)

An original Russian story (13 chapters, 23,222 characters, 130 fragments, numbers and dialogue included) with a Windows-voice sample, M4B, verification on,
normalization on, batch 8 (12.6 GB free), the model files already in the Windows cache. The probe was run through `VoiceoverManager`, not through the tab.

| What | Result |
|---|---|
| Audio produced | **1,703.6 s (28.4 min)**, 13 chapters with the right titles (ffprobe), 14 MB M4B |
| Whole job | **493 s (8.2 min)**: synthesis 306 s (peak video memory 7.4 GB), recognition of all 130 fragments about 50 s, two repair rounds of one fragment about 100 s (mostly model loads), assembly 15 s |
| Speed | about 0.29 s of work per second of audio end to end, i.e. 3.5 times faster than real time; an hour of speech should take about 17 minutes (linear extrapolation) |
| Words correct | **97.9 %** (independent per-fragment whisper-turbo check, 3,713 words); 92 of 130 fragments perfect, 7 above 10 % wrong, 2 above 15 % |
| Timbre over the book | cosine similarity of every fragment's speaker embedding to the sample: mean 0.986, minimum 0.979, std 0.002; sixths of the book 0.985-0.987; first and last tenth both 0.986: **no drift** |
| Pace | 0.058-0.084 s per character (mean 0.0705), no outliers |
| Report | 1 fragment of 130 stayed wrong after two repairs («Подарок, сказала она…», heard as «По дорог…»), listed with its audio |
| Output | AAC 24 kHz mono 65 kbit/s, loudness -18.0 LUFS, loudness range 3.5 LU, peak -0.7 dBFS |

Caveats: the voice is a **synthetic** Windows voice, so a drift of a cloned human voice is not excluded (the embedding similarity measures the model's own
consistency, and the model is given the same speaker embedding for every fragment); the similarity score is not listening; the peak of -0.7 dBFS is
above the -1.5 dBFS the loudness filter aims for (single-pass dynamic mode); one text, one voice, one run. The first pass flagged only the single fragment above, so the
repair rounds were used little here.

### Measured: one hour of speech for each engine (2026-10-28, same machine)

An original Russian story of 26 chapters and an epilogue (48,605 characters after normalization, 269 fragments, about 58-59 minutes), the same synthetic
Windows-voice sample, M4B, verification and normalization on, run through `VoiceoverManager` one engine after the other (nothing else used the card; a
monitor wrote video memory, load, temperature and the memory of the studio's Python processes every 10 s).

| | Qwen3-TTS (batches of 8) | VoxCPM2 (`torch.compile`) |
|---|---:|---:|
| Audio | 3,565 s (59.4 min) | 3,507 s (58.4 min) |
| Whole job | **909 s (15.2 min)**, 0.255 per audio second | **1,421 s (23.7 min)**, 0.405 per audio second |
| Synthesis alone | 674 s | 1,152 s |
| Peak video memory the model needed | 7.8 GB | 6.1 GB |
| Video memory **in use on the card** (monitor) | 15.7 GB (median 15.7), see below | 10.1 GB (median 9.4) |
| Card load / temperature / power, maximum | 29 % mean / 63 °C / 212 W | 85 % mean / 61 °C / 203 W |
| Memory of the studio's Python processes | max 4.2 GB, **falls** over the job (2.5 to 1.6 GB) | max 5.5 GB, falls (2.6 to 2.2 GB) |
| Words correct (independent per-fragment check, 7,670 words) | 98.4 % | **98.7 %** |
| Fragments perfect / above 10 % wrong / above 15 % wrong | 196 / 10 / 1 | **213 / 9 / 0** |
| Fragments the check sent to repair (fixed by it / stayed wrong) | 2 (1 / 1) | 1 (**1 / 0**) |
| Similarity to the sample, mean (min, std) | 0.986 (0.977, 0.002) | 0.982 (0.977, 0.002) |
| Similarity across the six parts of the book | 0.985-0.986 | 0.982-0.983 |
| Pace | 0.0705 s/char, no outliers | 0.0693 s/char, no outliers |
| File | 27 chapters, AAC 24 kHz, 29.4 MB | 27 chapters, AAC 48 kHz, 42.3 MB |
| Loudness / peak | -18.0 LUFS / -0.9 dBFS | -18.0 LUFS / -1.3 dBFS |

- **No drift and no leak over an hour.** The timbre score is flat from the first sixth to the last for both engines, the pace has no outliers, the memory of the
  Python processes does not grow, the card stays at 61-64 °C. The M4B of both has 27 chapters with the right titles and the planned length (ffprobe).
- **Resume at scale** (Qwen): half of the finished fragment files were deleted, as after a crash, and the same job was started again. It reused 135 of 269
  fragments and took **549 s instead of 909 s** (synthesis 323 s instead of 674 s); the whole text was recognized again, which cost about 100 s. The new file is not
  identical to the first: the re-synthesized fragments were in other batches, so it is 17 s (0.5 %) longer (3,582 s against 3,565 s).
- **Two findings of this run were fixed afterwards** (see below): Qwen kept 15.7 GB of the card although it needed 7.8 GB (PyTorch's allocator cache), and peaks reached
  -0.3 dBFS in one file, above the -1.5 dBFS target of the loudness filter.
- «Подарок, сказала она, нельзя прятать» was read as «По дорог…» by Qwen in every take of both runs (two repair rounds each time, reported): a systematic error of this
  engine on this word, the case for the pronunciation dictionary. VoxCPM2 read it correctly.
- **Repairs worked in both runs**: in each, one fragment judged wrong by the check came out right on the repair take (the first time this was observed on real audio, earlier runs
  had none to fix).
- Caveats: one text, one synthetic voice, one run per engine; nothing was listened to.

#### Changes made after the hour

- `qwen_tts_worker.py` gives the allocator's cache back after every batch (`torch.cuda.empty_cache()`); `synthesis_done` reports `peak_reserved_mb` as well.
  Check on a fresh 28-minute synthesis (another seed, nothing cached): synthesis took 318 s instead of 306 s (the cache release costs about 4 %), the model needed at most
  7.3 GB and **the card showed at most 10.3 GB in use** (reserved peak 11.4 GB; median 3.4 GB over the whole job) where the hour had held 15.7 GB. A new one-hour run to
  compare like with like was not made.
- `assemble.LOUDNESS` ends with `alimiter=limit=0.8` (-1.9 dBFS) after `loudnorm`, which on its own only aims at the peak. A limit of 0.84 still left -1.0 dBFS in the finished
  file (the AAC encoder adds about 0.5 dB); with 0.8 the same text measured **-1.2 dBFS** (-18.1 LUFS).
- **Verdict cache**: every checked fragment gets `<key>.verdict.json` next to its audio (valid only for that exact file: size and modification time). A resumed or repeated job
  (another format, a stopped job) listens only to new takes, and a fragment that repairs could not fix is reported again without new repair rounds (`verification.cached`).

### Measured: VoxCPM2 against Qwen3-TTS (2026-10-28, same machine, same voice sample and texts)

The voice sample is the same **synthetic** Windows voice as before, so no human voice was cloned; nothing here is a listening result.

Benchmark on 8 fragments (83-96 s of audio), model files cached, per-fragment whisper-turbo check and speaker-embedding similarity to the sample:

| VoxCPM2 mode | Work per second of audio | Peak video memory | Words wrong | Similarity | Pace |
|---|---:|---:|---:|---:|---:|
| reference only, no `torch.compile` | 0.79 s | 6.1 GB | 3.1 % | 0.983 | 0.0706 s/char |
| **reference only, `torch.compile`** | **0.35 s** | 6.1 GB | not checked separately | | |
| sample plus transcript («continuation») | 0.79 s | 6.1 GB | 4.1 % | 0.986 | 0.0823 s/char (slower speech) |
| both | 0.79 s | 6.1 GB | 5.1 % | 0.986 | 0.0825 s/char |

The transcript modes were *not* more accurate on this voice, which is why the transcript is optional and the plain reference mode is the default. Loading takes 10 s from
the file cache, 110 s with the first compilation. Without the polyphase filter in `verify_worker.read_wav` (it used plain interpolation) 48 kHz audio would be
folded into the speech band before recognition.

The 28-minute story of the Qwen measurement, run through `VoiceoverManager` with VoxCPM2 (reference mode, `torch.compile`, verification and normalization on):

| | Qwen3-TTS (batches of 8) | VoxCPM2 |
|---|---:|---:|
| Audio | 1,703.6 s | 1,681.0 s |
| Whole job | 493 s (0.29 per audio second) | 746 s (0.44) |
| Synthesis alone | 306 s | 579 s (0.34) |
| Peak video memory | 7.4 GB | 6.1 GB |
| Words correct (independent check, 3,713 words) | 97.9 % | **98.3 %** |
| Fragments perfect / above 10 % wrong / above 15 % | 92 / 7 / 2 | **99 / 6 / 0** |
| Fragments the studio's own check sent to repair | 1 (stayed wrong after two repairs) | **0** |
| Similarity to the sample, mean (min, std) | 0.986 (0.979, 0.002) | 0.982 (0.977, 0.002) |
| Similarity across the six parts of the book | 0.985-0.987 | 0.982-0.983 |
| Output | AAC 24 kHz 65 kbit/s, 14 MB | **AAC 48 kHz 97 kbit/s, 20 MB** |
| Loudness / peak | -18.0 LUFS / -0.7 dBFS | -18.0 LUFS / -1.3 dBFS |

Reading: VoxCPM2 reads slightly more words correctly and without a repair round, uses less video memory and delivers 48 kHz, at 1.5 times the time of Qwen
with batches. Timbre does not drift in either. The similarity is computed with Qwen3-TTS's own speaker encoder, which favors Qwen's output a little. Whether 48 kHz
sounds better is for listening: both files of this text and voice are in `UserData/Results/Voiceover/` (`Probe - 658b931b…` Qwen, `Probe - 8a4d630f…` VoxCPM2).
Qwen3-TTS stays the default engine (proven longer, faster); the page remembers the engine the person chooses.

### Measured: normalization (2026-10-28, same machine)

The same text that could not be fixed before («по ГОСТу 8486-86, … и т.д. и т.п., см. рис. 5 на стр. 17, а также др. необходимые мелочи») was synthesized
again with normalization: Whisper heard «смотри рисунок 5 на странице 17, а также другие необходимые мелочи» and the abbreviations as «т.д.», «т.п.» (they were spoken
as words), that is, the audio was right where it had been wrong. The first check still flagged the fragment (4 wrong words of 25) because recognition writes
«и т.д.» for the spoken «и так далее»; that false alarm cost one repair round (127 s instead of about 60 s) and was removed by normalizing the heard text too.
After that fix the cached fragments were checked in 20 s: 3 checked, 0 repaired, 0 reported. Only this one text was run; other kinds of text, and
how often the guessed endings are wrong in a real book, are not measured.

### Measured: verification (2026-10-28, same machine)

- Three fragments checked by whisper-turbo in about 20 s including the model load (the first run found nothing wrong).
- A text with abbreviations and a standard number («по ГОСТу 8486-86, т.д. и т.п., см. рис. 5 на стр. 17, др.») was read badly: the fragment was flagged
  with 5-6 wrong words of 25, synthesized twice more, still wrong, and reported with the recognized text. The job took 152 s, mostly model loads.
  Repairs by re-synthesis do not fix a *systematic* mispronunciation (abbreviations, numbers); the dictionary or the text must change.
- Ordinary prose had 0-9 % wrong words per fragment and was not flagged. Recognition itself errs («помарок» heard as «поморок»), which is why the page asks to listen first.

## Not verified

A text of an hour or more (28 minutes were measured for each engine), listening quality (only measurements so far), VoxCPM2 with a cloned human voice or with a transcript of a real
sample, a cold first compilation of VoxCPM2 on another machine, a clean install of the VoxCPM2 components through the installer (the catalog entry and its hashes are checked by tests, the
download was done by hand), repair rounds that actually fixed a random glitch (none was observed in the real runs),
a human voice, the tab driven by a real worker, a voice cloned from a human recording, any text longer than one minute (timbre and pace stability over
an hour), numbers beyond one example, stress marks, M4B playback in an external player, and that the installed EXE shows the tab
(the throw-away bridge and the in-app browser were used).
