# Voice input

Every text field of the studio (prompts of Image, Video and Music, lyrics, the Text chat, search boxes) can be filled by voice.
Everything runs on this computer: the recording is made in the page, sent to the local bridge and recognized by a Whisper model;
no audio leaves the machine and no recording is kept (the temporary file is deleted right after recognition).

## Using it

1. Click into a field. A small bar with a microphone and a language selector appears above its top-right corner.
2. Press the microphone (or **F9**). The first time, Edge asks for microphone access; the bar stays visible meanwhile. The button turns into a
   red stop button and a level meter shows that the microphone hears you.
3. Speak. The words recognized so far appear in the bar while you talk (live preview, only while the model sits on the video card).
4. Stop talking: after about 1.6 s of silence the recording ends by itself and the text is inserted at the cursor, with a space where needed;
   the field's own handlers run as if you had typed it. You can also press the red button (or F9) to finish at once, **Esc** cancels.

If nobody speaks for 9 seconds the recording stops with "Не услышал речь". Recordings are limited to 110 seconds.

The language selector (Russian by default, also English, Ukrainian, German, French, Spanish, "auto") is remembered. If microphone access was
refused, allow it in the browser's site permissions and in Windows "Privacy -> Microphone". Fields that hold paths, tags or numbers do not
offer the microphone.

*Fixed in `voice.3`:* a recording that ended while a live preview was still being recognized was refused with "busy" and the whole dictation
vanished (the bar only spun); a final recording now waits for the preview (a running preview is also cancelled by the page).

*Fixed in `voice.2`:* in the first version the permission dialog took the focus from the field, which hid the bar (and its stop button) while
the recording had already started, and nothing ended the recording by itself, so nothing was ever recognized.

## Punctuation and capital letters

Whisper sometimes writes fast or stream-of-consciousness speech as one lowercase line without commas. The optional model **RUPunct big**
(MIT, 0.7 GB, `speech.model.rupunct-big`, Components -> "Voice input") runs right after recognition, in the same worker, and adds capital
letters, commas, full stops and question marks to the Russian words (10-50 ms). Rules:

- text that Whisper already punctuated well (a capital letter at the start and enough marks) is not touched;
- Latin words and numbers stay exactly as recognized ("anime", "30"); a Latin word is capitalized only when it starts a sentence;
- whatever follows a full stop, question or exclamation mark, and the first word of the text, gets a capital letter;
- if the model gives anything unexpected, the original text is inserted; switch it off with `"punctuation": false` in
  `UserData/Settings/speech.settings.json`.

Example (real model): "слушай а получится сделать локальный голосовой ввод картинки раз получилось научиться распознавать" becomes
"Слушай, а получится сделать локальный голосовой ввод картинки? Раз получилось научиться распознавать." It is a statistical model: it
guesses where sentences end, so a result can differ from what you meant; ChatGPT-level text needs a large language model, which would
compete for the video card (the local Gemma could do it, but it is a separate, optional idea).

## Enhanced mode: the local Gemma edits the text

The bar has a **Gemma AI** switch next to the language selector. It is off for a new browser profile; explicit on/off choices are saved in local storage and restored on the next visit. When it is on, a final
recording goes through two steps: Whisper recognizes, then the local Gemma (the model of the **Text** section, through its private Ollama) restores
punctuation and capital letters, fixes obvious mishearings and removes stutters, without answering, translating or rewriting. Live previews
are not edited. Switching the switch on starts loading Gemma in the background. The microphone icon is 36 px with a borderless
39 px hit area. Clicking or tabbing into the language selector or the Gemma AI checkbox keeps the bar visible and preserves the
original dictation field. Leaving both the field and the bar hides it after a short delay.

A separate animated hint above the bar shows that Gemma AI is loading or waiting for the GPU. It is absolutely positioned so
the message never changes the bar's dimensions. The page reads the existing local Text status endpoint until the selected model
is loaded and its voice warmup has finished, then fades the hint away. Disabling enhancement or leaving the field stops this
tracking; late responses cannot reopen the hint. Errors are shown separately, and a five-minute readiness deadline prevents an
endless loading message. An already loaded model is reused without another warmup. No inference is started for enhancement
when its switch is off; the ordinary Whisper warmup remains independent.

What to expect:

- **First use:** loading a big model from a hard disk takes up to a minute (one real test: 64 s for `gemma4:e2b`); afterwards an edit takes about
  0.1-0.2 s per phrase. The model stays loaded for the keep-alive time of the Text settings.
- **During a generation** the switch is greyed out but stays on; dictation then inserts the plain Whisper text ("Gemma пропущена"), and when the
  generation ends Gemma is loaded again in the background if the voice bar is visible (otherwise on the next field focus), so the next dictation is quick (the model files stay in the Windows file
  cache, so a reload takes seconds rather than the first minute). While Gemma loads or edits, the banner says "Gemma работает для голосового ввода"
  instead of "generation".
- **It takes the video card** like a chat answer: the coordinator refuses while another module generates (the plain Whisper text is inserted
  and the bar says why), unloads what idle modules hold, and stops the voice worker if it sits on the card. While Gemma sits there, Whisper
  runs on the processor (about 5 s per phrase). The Stop button of the banner stops an edit. A smaller Text model (for example
  `gemma4:e2b`, 4.6 GB, or `gemma4:e4b`) keeps this cheap; the largest one needs almost the whole card.
- **Safety nets:** the text is sent as it is (up to 4000 characters), the answer is cleaned of quotes and "Исправленный текст:" prefixes and is accepted
  only if it stays the same sentence (similar length, at least 60 % of the words in the same order). A question in the dictation is not
  answered: if Gemma answers instead of editing, the plain recognition is inserted.
- **Fallbacks:** no Text model, Text unavailable, card busy, model error, stop: the plain (Whisper + RUPunct) text is inserted and the bar says why.

Real test (2026-10-25, `gemma4:e2b`): "слушай а получится сделать локальный голосовой ввод картинки раз получилось научиться распознавать может и
русскую речь можно научить" became "Слушай, а получится сделать локальный голосовой ввод картинки? Раз получилось, научиться распознавать, может, и
русскую речь можно научить?"; "доктор пеппер хеликоптер вертолет летает на небе кайф какой дикий перец бешеный madness madness i'm crazy" became "Доктор Пеппер,
хеликоптер, вертолет летает на небе. Кайф какой дикий, перец бешеный, madness, madness, I'm crazy."

## Models

Installed from the **Components** tab (or `Setup-VisualForge.cmd --models speech.model.whisper-turbo`), section "Voice input":

| Model | Download | Notes |
|---|---|---|
| `whisper-turbo` (large-v3-turbo, recommended) | 1.6 GB | MIT. Best choice on a video card and good enough on the processor |
| `whisper-small` | 1 GB | Apache-2.0. Light and quick on the processor, noticeably less accurate |
| `whisper-large-v3` | 3.1 GB | Apache-2.0. Most accurate, heavier |

They run in the **Music runtime** (PyTorch and transformers are already there), so nothing else is installed. The best installed model is
used unless `UserData/Settings/speech.settings.json` names another one (`model`, `device` = auto|cpu|cuda, `language`, `keep_alive` minutes
1/5/15/60).

## How it shares the video card

- A worker process loads the model when the microphone appears, keeps it for `keep_alive` minutes and then exits.
- It uses the GPU only if no module is generating and enough video memory is free (about 3.5 GB for turbo); otherwise the processor. The
  choice is made when the worker starts.
- Before Image, Video, Music or Text takes the card, the GPU coordinator stops a GPU-resident voice worker (it is reloaded on the next use).
  Voice input never locks the interface and never blocks a generation; it can be used while one runs (on the processor then).

## Measured (2026-10-25, RTX 5070 Ti, Russian phrases synthesized with the Windows "Irina" voice)

| Model, device | Per phrase of 5-9 s | Word error rate |
|---|---|---|
| turbo, GPU | 0.2 s (first request after loading includes about 20 s of warm-up, hidden while you speak) | 3.6 % (the only difference: "тридцать" written as "30") |
| turbo, processor | about 5 s | 3.6 % |
| small, processor | 2-3 s | 9.5 % |

Silence is detected before the model is called (no output, no invented subtitles). **Not measured:** real microphones, accents and noisy
rooms: the test used synthetic speech, and the in-app browser used for development blocks microphone capture, so the whole recording chain
(audio worklet -> speech gate -> live preview -> automatic stop -> insertion) was exercised with a simulated microphone stream that played a
recorded phrase followed by silence, not with a live microphone.

## Other engines considered (from a web search, **not tested here**)

| Engine | What is known | Verdict |
|---|---|---|
| Vosk (Kaldi) streaming Russian models | Offline, tiny (about 45 MB small model), words appear as you speak, runs on any processor | Much less accurate than Whisper; a fallback for weak computers |
| sherpa-onnx with GigaAM v2/v3 (Sber) or T-one (streaming) Russian models | Offline ONNX runtime, fast on the processor; GigaAM is built for Russian, T-one streams | The most promising Russian-specific option; needs an extra runtime (onnxruntime + sherpa-onnx) |
| NVIDIA Parakeet-TDT 0.6B v3 | 25 European languages including Russian, very fast; Russian FLEURS word error about 5.5 % against about 4.2 % for Whisper large-v3 | Not clearly better than Whisper turbo for Russian; heavier toolchain (NeMo) |
| Gemma 4 E2B/E4B | Google says these models take audio and were trained for multilingual speech recognition; early reports say Ollama could not pass audio, a later blog says it can | Worth a test through the private Ollama; the author's large Gemma 26B fine-tune has no audio input |
| The music generators (ACE-Step, MiniMax Music3) | They turn text into music; they do not recognize speech | Not applicable |
| Edge / Windows built-in dictation | Cloud services | Excluded: the studio is offline |

Whisper large-v3-turbo stays the default: 0.2 s per phrase on the card, 3.6 % word error on the Russian test phrases, and no new runtime.

## Files

`App/server/speech/` (`manager.py` worker control and GPU rules, `worker.py` the recognizer, `polish.py` the Gemma edit, `routes.py`), `App/frontend/voice.js` (bar, capture,
live preview), `voice-gate.js` (when speech starts and stops; also run by Node in the tests), `voice-worklet.js`, `voice.css`, endpoints
`/api/speech/availability|settings|prepare|unload|transcribe[?partial=1][&enhance=1]`, tests `tests/test_speech.py`, `tests/test_speech_polish.py`,
`tests/test_voice_gate.py`.
