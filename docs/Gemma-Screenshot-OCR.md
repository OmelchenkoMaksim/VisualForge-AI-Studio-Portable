# Reading screenshots with the studio's Gemma 4 model — benchmark and settings (2026-10-30)

Decision: **keep Gemma 4 for pasted screenshots and raise its vision-token budget to 1120.** No second model is needed for the Ctrl+V
workflow: with that one setting the model already reads Russian/English text, code and street signs, and also answers questions
about the picture. The tested dedicated OCR models (LightOnOCR-3 4B/0.8B, PaddleOCR-VL-1.6, earlier OvisOCR3) were not better, and
they cannot answer free-form questions.

## What was wrong

`ollama.exe` runs Gemma 4 through llama-server, which by default turns **any** picture into at most 280 vision tokens (about 0.65
megapixel; the log shows `image_max_pixels: 645120`). A 1920×1080 screenshot is therefore shrunk about 3× before the model looks at
it, whatever the page sent. Measured token counts: 960×540 → 236 image tokens, 1920×1080 and 3840×2160 → both 280. The page-side limit
`MAX_SIDE = 1536` was irrelevant above that size, and small text became fluent but wrong output.

The budget can be changed with two environment variables of llama-server, which Ollama passes on:
`LLAMA_ARG_IMAGE_MIN_TOKENS` / `LLAMA_ARG_IMAGE_MAX_TOKENS` (found in `libllama-common.dll`; Gemma's documentation lists 70, 140, 280,
560 and 1120). The studio's private Ollama now sets 560/1120 (`VISION_TOKENS` in `App/server/text/text_manager.py`); the page keeps a
long side of up to 2560 px (`MAX_SIDE` in `App/frontend/text-chat.js`).

## Results

Corpus: 36 images (`tools/ocr_bench`), 13 with a full reference text (CER) and 14 with expected strings (key recall); the others are
describe-only. Model: `150ts-Gemma4A4B-…-C32K` (Q4_0), RTX 5070 Ti, context 8192 for the sweeps. One run per configuration.

| Configuration | mean CER | median CER | key recall | loops / cut off | median s per image |
|---|---:|---:|---:|---:|---:|
| **studio today** (model's own system prompt, thinking on, temperature 1, picture shrunk to 1536 px, 280 tokens) | 0.312 | 0.050 | 0.661 | 7 | 16.1 |
| plain OCR instruction, no thinking, temperature 0, 1536 px, 280 tokens | 0.107 | 0.050 | 0.885 | 3 | 1.4 |
| same, 1024-token budget, original pixels | 0.049 | 0.013 | 0.936 | 3 | 2.5 |
| **same, 1120-token budget (chosen)** | **0.044** | **0.013** | **0.990** | 3 | 2.5 |
| same, 2048 tokens (beyond the documented range) | 0.041 | 0.008 | 0.902 | 3 | 10.8 |
| LightOnOCR-3 4B (BF16, long side ≤ 2048) | 0.151 | 0.019 | 0.871 | 3 | 6.1 |
| LightOnOCR-3 0.8B | 0.153 | 0.020 | 0.774 | 2 | 29.9 |
| PaddleOCR-VL-1.6 (20 of 36 images; run stopped, see below) | 0.26 (5 pages) | – | 0.62 (10) | 5 | ~240 |

CER is clipped at 1.0 per image before averaging, so a looping answer cannot hide how many images were read well. "Loops / cut off" counts
answers that repeat a chunk four times or hit the token limit; some of the 3 for Gemma are false positives of the repeat detector on code
that really repeats lines (their key recall is 1.0), and one is the dense two-column contract page cut at 1500 tokens.

Findings, in order of effect:

1. **Vision budget**: 280 → 1120 tokens took CER from 0.107 to 0.044 and fixed the 4K fixture with 14 px text (CER 0.61 → 0.04; at 280 it
   produced a fluent, wrong text; at 1536 px it looped). A Russian contract page went from CER 0.099 to 0.017.
2. **Studio defaults hurt transcription**: the model's built-in system prompt turns on thinking. On dense pages the model spent the whole 4096-token
   budget thinking and returned an empty answer (7 of 36). A plain OCR instruction with thinking off is faster (1.4–2.5 s) and better.
3. **Beyond 1120 is not better**: 2048 tokens is 4× slower, uses more memory and lowered key recall; stay inside the documented range.
4. **Tiling does not help**: cutting a picture into 2×2 tiles raised CER to about 0.8 (partial tiles invite invented text).
5. **Prompt language does not matter**: Russian and English transcription prompts gave CER 0.065 vs 0.066.
6. **No invented text on a text-free picture** from Gemma in any configuration ("I did not find any text in this image."). LightOnOCR 4B described the picture in French instead.
7. **Picture questions work**: with the same setting the model described the corpus photos correctly (cat, mountains, flowchart,
   RStudio, Times Square, a street sign with a poster) and quoted the text it saw. Text read correctly includes Cyrillic, Korean and Abkhaz-script signs.

Memory: with the model loaded the whole card used 15.56 GB of 16.3 GB at a 32K context and 15.35 GB at 16K (1120-token budget, nothing else
on the GPU). The owner's other GPU programs can hold 3–4 GB; then Ollama offloads part of the model to the CPU and is slower, which was not
measured here. Each picture now costs about 1100 tokens of context instead of 250 (4 pictures plus history can take a good part of a 32K
context).

## The other models

- **PaddleOCR-VL-1.6** (Apache-2.0, 1.93 GB, `transformers` 5.17 path without PaddlePaddle). Ran, but took about 240 s per 1024-token page in this
  environment (the PyTorch reference path, no flash-attention; not representative of the vendor's serving stack) and looped on screenshots and
  code (5 of 20). The card documents only fixed task prompts (`OCR:`, `Table Recognition:`, …) and does not list Russian. The run was stopped at
  20 of 36 images because of the time; the numbers above are from those.
- **LightOnOCR-3 4B / 0.8B** (Apache-2.0, 9.1 / 1.7 GB). 4B reads documents very well (Russian contract page CER 0.019) and returns Markdown, but it
  is French-centred (the textless picture was described in French), answers only the empty prompt or `grounding`, and the card does not list
  Russian. 0.8B is slow in the PyTorch reference path and weaker on screenshots.
- **LightOnOCR-3 1B**: not evaluated. With the installed Transformers 5.17 its checkpoint keys (`model.language_model.model.*`) do not match the
  expected layout, so every language layer is initialised randomly and the output is garbage. Fixing that needs a newer Transformers or key
  remapping, which this evaluation did not install.
- **OvisOCR3** (earlier evaluation, [OvisOCR3-Local-Evaluation.md](OvisOCR3-Local-Evaluation.md)): not re-run here; its corpus was reused.
  Its hard-case fixtures (the 14 px 4K text, the Russian pages) are in this corpus and Gemma at 1120 tokens reads them comparably.

Timings of the Hugging Face models describe this environment (PyTorch reference implementations, bf16, SDPA, greedy decoding); they say
nothing about vLLM or the vendors' optimised kernels.

## Limits of this evidence

- 36 images, one run each; 27 of them have a reference. The expected strings for photos and screenshots were written by the reviewer (Claude) from
  looking at each image, not by a human proofreader; unreadable text was left out. The full-text references are exact (rendered fixtures) or the
  dataset's own text (three Russian pages).
- The sweeps used a context of 8192; only a core set of 9 images was run at 16K and 32K (CER 0.060 / 0.061, same as 8192).
- The studio itself was exercised afterwards (see "UI test" below), but with pictures pasted by dispatching a `paste` event with the image
  bytes, not with the physical Ctrl+V of an operating-system clipboard, which the browser pane cannot drive.
- Sampling: the studio-like row uses the model's own temperature 1; the others use greedy decoding, so part of the gap is the sampling.
- Handwriting, tables of many columns, PDFs and long multi-page documents were not covered.

## Reproduce

`tools/ocr_bench` holds the harness. Images are not committed (third-party licences); `fetch_corpus.py` downloads the Commons files and pins
them in `corpus/manifest.json`, `make_synthetic.py` renders the exact-truth fixtures, `manual_truth.json` has the reviewer's expected strings.

```text
python tools/ocr_bench/fetch_corpus.py
python tools/ocr_bench/make_synthetic.py
python tools/ocr_bench/run_gemma.py --config tok1120 --set all       # private Ollama on port 8299; the studio must not be running
python tools/ocr_bench/summarize_runs.py                             # table over tools/ocr_bench/runs/*.json
```

The Hugging Face models were downloaded to `%LOCALAPPDATA%\Temp\vfr-tests\gemma-ocr\models` by `download_hf_model.py` (SHA-256 verified) and run with the Image
runtime's Python by `run_hf_ocr.py`; nothing was installed into the studio. After the decision their weights (LightOnOCR-3 0.8B/1B/4B, PaddleOCR-VL-1.6
and the OvisOCR3 weights of the earlier evaluation, about 15.6 GB) were deleted at the owner's request; the scripts download them again if a retest is wanted.
Pitfall found on the way: ending `ollama.exe` with `terminate()` leaves its `llama-server.exe` child holding about 13 GB of video memory, so the next model
load failed with CUDA out-of-memory; the harness now kills the process tree and waits for free memory.

## Suggested next steps

- Paste-to-transcribe: after Ctrl+V, offer one-click "Транскрибировать" that sends a plain OCR instruction with thinking off (the global thinking
  default makes dense pages slow and can end without an answer).
- Cap how many earlier pictures a chat resends now that each costs about 1100 tokens.
- A crop/region tool before sending, for very dense pages (a two-column contract page was cut off at 1500 tokens and read the columns in a different order).

## UI test in the running studio (2026-10-30)

The bridge and ComfyUI were started from the installed tree (as `Start-VisualForge.ps1` does) and driven in the built-in browser; pictures went in through the
Text tab's real `paste` handler, the question was typed and sent with Enter or the button.

| Check | Result |
|---|---|
| Real screenshot (VS Code + plot) through `/api/text/send`, cold model load | answer correct; 224 s in total, of which 111 s was loading the model from the hard disk |
| Vision budget active in the studio path | one 1920×1080 picture = 1115 prompt tokens (280 before) |
| Paste into the chat, question "values of `dt` and `h_init`?" (thinking on, the default) | correct (0.2 and 100), 115 tok/s, thinking block shown |
| Four pictures in one message (Russian tax form, street sign, terminal, Nevsky sign) | each described correctly; 3519 prompt tokens, 12.7 s |
| Follow-up text question about an earlier picture | correct in 4.4 s; the history resends the picture (context 1862) |
| "Transcribe all text" of the Russian tax form, thinking on | complete and accurate, 4688 tokens, about 41 s (11 k characters of thinking) |
| The same, thinking off (checkbox "Размышлять перед ответом") | the same text, 511 tokens, 8 s |
| 4K screenshot drawn in the page (16 px code, Russian comments, 12 px status line), thinking off | 8 s; 8 of 11 lines exact; wrong: `i[...]` dropped, `10_000` read as `10000`, `3.13.14` as `3.13.13`, `Ln` as `ln` |
| Agent mode | attach button disabled with the explanation (pictures are not passed to agents), as designed |
| Image tab: paste into Image 1; Video tab: paste into a character card | both work |
| Browser console | no script errors |

Bug found and fixed in this test: pasting a screenshot into the **Text chat** also silently loaded it into **Image 1** of the Image tab (and the same on
the Video tab). The Image tab listens for `paste` on the whole document and ignored that the chat input had already handled the event. `image-studio.js` now returns
when `event.defaultPrevented` is set or the Image tab is not the selected tab; both cases were checked after the change.

Observations that were not changed:
- Pictures pending in the input (pasted but not sent) stay attached when the user switches to another chat.
- Thinking is on by default for a new chat; for transcription the checkbox "Размышлять перед ответом" makes the same answer 3–5 times faster.
- The 4K case shows the limit of this setting: text that small after downscaling to a 2.6 MP budget is read with occasional character errors; crop the area for exact text.
