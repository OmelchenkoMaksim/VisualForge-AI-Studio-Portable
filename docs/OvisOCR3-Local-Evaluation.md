# OvisOCR3 local evaluation — 2026-10-30

Decision: a promising optional OCR/extraction backend for the shared prompt attachment feature. Preserve source resolution, provide a crop/region workflow, show a reviewable result, and reject incomplete/repetitive output. It is not a reliable replacement for general image understanding. This evaluation did not integrate a new Studio feature or compare against Gemma inference.

## Provenance and execution

- Model: [ATH-MaaS/OvisOCR3](https://huggingface.co/ATH-MaaS/OvisOCR3), Apache-2.0, commit `4833d5d2afa6e769ee933f024504d67ace80a24c`.
- Downloaded inference artifacts: 1,728,993,040 bytes, one copy on C. Weight file: 1,706,030,528 bytes; SHA-256 `aa2311c6b22b07cf3afe9aac6e8bcb02a2b19dd8671b2a91d0b144071577e600`. Size/hash validation completed before inference. No remote model code was executed.
- RTX 5070 Ti, 16 GB; existing embedded Python 3.13.14, PyTorch 2.14.0+cu130, Transformers 5.17.0. Native `AutoModelForImageTextToText`, BF16, SDPA, greedy generation, local files only. No dependency installation or Studio runtime modification.
- Optimized causal-conv/linear-attention extensions were absent; Transformers used its PyTorch reference implementations. The timings below describe this environment, not the authors' vLLM configuration.
- Processor budget: minimum `448²`, maximum `2880²` pixels. This is an **area budget**, not a maximum side length. The actual grid was recorded for every call. For example, a 3840×2160 input became 3840×2144; the 10109×4542 photograph became 4288×1920; the 7530×9155 scan became 2592×3168.
- 39 sequential inferences on 13 distinct source images: 37 EOS completions, two token-limit failures, no worker exceptions/OOM. Completion is not a quality pass. Median inference 3.25 s; maximum 51.15 s; total measured inference 366.06 s. Preprocessing and loading are separate. Model loading took approximately 1.4–2.2 s from the C copy; initial calls were slower than later calls. Peak PyTorch allocated memory: 2.903 GiB, excluding driver/display/other-process memory.
- Original task prompts were taken from the pinned model repository; targeted and language probes are recorded separately. Token budgets varied by task (64–4096); each call had a 180-second generation limit. Neither time limit nor memory exhaustion occurred.

## Corpus and scoring

Eight downloaded images plus five deterministic fixtures were used. Downloaded corpus files totalled 66,574,837 bytes; the three reference rows added 1,653,395 bytes, below the approved 100 MB. One downloaded geometry file describes distortions and was **not** used as text ground truth. The viewer rows provided the text references.

Public sources:

| Source | Original dimensions | Attribution / scope |
|---|---|---|
| [Russian OCR pages 0001–0003](https://huggingface.co/datasets/metrika-labs/ru-ocr-benchmark-hard) | 1170×827; 1034×1462; 1241×1754 | Apache-2.0; commit `e1885252389448a1d86e1c8e5f8f3a5ad186b113`. The requested `vladlinv` repository redirected to `metrika-labs`. Only three original pages were evaluated, not the whole benchmark. |
| [LibreOffice Writer, Windows 10](https://commons.wikimedia.org/wiki/File:LibreOffice_Writer_7.5.0_Windows10.png) | 1920×1080 | Hérisson grognon, CC BY-SA 4.0. French UI, English template document. |
| [LibreOffice Writer, Cyrillic table](https://commons.wikimedia.org/wiki/File:LibreOffice_Writer_screenshot_showing_a_table_(Cyrillic).png) | 1014×700 | Yanpas, CC BY-SA 4.0. Russian UI and empty 3×3 table. |
| [Declaration of Independence scan](https://commons.wikimedia.org/wiki/File:USA_declaration_independence.jpg) | 7530×9155 | Public domain; Archives.gov provenance on Commons. Faded handwritten historical document, qualitative stress test. |
| [Cat03](https://commons.wikimedia.org/wiki/File:Cat03.jpg) | 1600×1598 | Fir0002, GFDL 1.2. Private local evaluation; image not added to the repository. |
| [Fronalpstock panorama](https://commons.wikimedia.org/wiki/File:Fronalpstock_big.jpg) | 10109×4542 | Hannes Röst, CC BY-SA 3.0. Mountains, forest, fields and buildings; no visible lake. |

Synthetic fixtures: six fixed Russian/English/code lines at 28 px (smoke), the same lines on 4K canvases at 14 and 24 px, a bilingual invoice with a 4×4 table and three simple formulas, and a flowchart with YES/NO branches. These are engineering fixtures, not downloaded photographs or real application captures. The 4K fixtures are sparse; they do not establish dense 4K page performance.

CER is Levenshtein edit distance divided by reference characters after collapsing whitespace. Case, punctuation and letter substitutions remain significant. CER also penalizes changes in reading order. For multicolumn pages, a separate order-independent token precision/recall helps distinguish reading-order changes from missing words; it is not a substitute for sequence accuracy.

| OCR case | Encoder size | CER | Inference |
|---|---|---:|---:|
| 14 px text, 4K source | 3840×2144 | 1.20% | 20.04 s |
| Same source reduced to long side 1536 | 1536×864 | 41.43% | 2.81 s |
| Same source, crop around all text | 1216×352 | 0.80% | 2.74 s |
| 24 px text, 4K source | 3840×2144 | 1.20% | 4.58 s |
| Same source reduced to long side 1536 | 1536×864 | 4.38% | 3.07 s |
| Same source, crop around all text | 1216×352 | 0.40% | 2.75 s |
| Russian page 0001, form with digit boxes | 1184×832 | 7.30% | 12.33 s |
| Russian page 0002, prose contract | 1024×1472 | 0.17% | 29.04 s |
| Russian page 0003, contract with two-column details | 1248×1760 | 16.50% | 33.62 s |
| Page 0003 reduced to long side 1536 | 1088×1536 | 16.05% | 32.87 s |

On page 0003, the model read the left column before the right, while the reference interleaved lines. Its order-independent token precision/recall was 99.39%/99.63%; the 16.50% CER must not be presented as 16.50% unread characters. Conversely, page 0001 still contained genuine OCR/spacing errors. None of these few samples is a representative benchmark average.

## Observed strengths and failures

| Task | Observed result |
|---|---|
| Printed Russian/English OCR | Strong on clear prose; confused similar glyphs (`O0`, `8B`, `5S`) and occasionally `ъ`/`ь`. Reducing tiny letters caused omissions and invented English text in place of Russian. |
| Full application screenshot | Plain OCR read the French screenshot's document but omitted application chrome. A physical toolbar crop recovered the menu with one visible transcription error. The official region prompt returned only the window title. A targeted VQA request recovered the menu labels, `Liberation Serif`, and `11 pt`; its explanation also falsely described a dropdown as expanded. |
| Russian UI screenshot | Plain OCR recovered the document and menu, but turned some icons into spurious letters. Markdown on the whole screenshot repeated `О` until the 2048-token cap. Cropping to the document recovered the empty 3×3 table in 1.65 s. |
| Filled table / fields | Synthetic invoice table: all 16 cells, quantities and amounts were recovered in HTML. Custom and official field extraction returned the correct invoice number, total and email as valid JSON. The official missing-bank-account probe returned an empty string. |
| Formula recognition | Three simple expressions (`E=mc²`, `x²+y²=r²`, summation to 42) recovered as LaTeX. Advanced mathematics, matrices and handwritten formulas were not evaluated. |
| Historical scan | Full document hallucinated/repeated prose and hit the 2048-token cap; even the heading year was wrong. A heading crop read the large heading but did not recover the faint body. High source resolution alone did not solve degradation. |
| Textless photographs | Plain OCR stated that there was no text, then added a description. It did not return an empty machine-readable result. |
| Photo recognition and English description | Correctly recognized a ginger cat, selected `cat` from five supplied classes, and described several visible features. These limited successes establish basic visual understanding, not general vision reliability. |
| Visual hallucinations | A question mentioning water caused a lake to be invented in the mountain panorama. Asked for an unknowable cat name/age, the final answer abstained but the reasoning introduced an unrelated “Ostrich” claim. Figure-caption JSON added a nonexistent document title `#`. |
| Russian photo prompts | A free Russian request described the cat as a cross. An English instruction requesting Russian recognized the cat but invented feathers and mixed in Chinese. A short Russian question inside the official VQA template correctly identified an orange cat. Neutral Russian landscape description mixed languages and guessed a location despite the instruction. Prompt format materially affected reliability. |
| Flowchart | Caption and VQA correctly described YES→OCR, NO→description, and the common prompt insertion endpoint. Caption JSON parsed, but embedded Mermaid contained literal escaped newlines/quotes and was not validated as directly renderable Mermaid. |

The authors explicitly document OCR, classification with candidate labels, VQA and document figure captioning in their [technical report](https://arxiv.org/html/2610.33066v1#S2). General photo understanding is therefore a supported task family, but the failures above make it unsuitable as an unqualified universal assistant.

## Integration implications

1. Keep original attachments for OCR; expose a pixel budget and source crop rather than unconditionally resizing to 1536. The current `MAX_SIDE=1536` in `App/frontend/text-chat.js` is a Studio preprocessing policy, not a measured Gemma architectural limit. Gemma was not benchmarked here.
2. Separate text extraction from photo description. Use tested English task instructions for OCR/structured extraction; preserve recognized Russian text. Do not route arbitrary Russian free-form visual conversations through this model by default.
3. Make extracted content reviewable before inserting it into the prompt. Detect repeated/truncated output, validate field JSON/table structure, and retain a crop/retry path. Do not interpret an EOS or valid JSON as evidence that its content is true.
4. A future OCR worker must join the shared GPU coordinator, cancellation lifecycle and unified event history. These integrations, sustained UI operation, PDF/multipage tables, handwriting breadth, grounded coordinates and general visual benchmarks remain untested.

## Local evidence

Source images, transcripts, manifests and experimental scripts remain under Windows Temp on C, outside Git (the OvisOCR3 weights were deleted on 2026-10-30 after the Gemma comparison, see [Gemma-Screenshot-OCR.md](Gemma-Screenshot-OCR.md)):

`%LOCALAPPDATA%/Temp/vfr-tests/ovisocr3-audit-20261030/`

Important files: `model-manifest.json`, `input-manifest.json`, `ru-reference-rows.json`, `fixture-truth.json`, the four `*-cases.json` manifests, `smoke-results.jsonl`, `results.jsonl`, `followup-results.jsonl`, `language-results.jsonl`, and `scored-results.json`. Raw public document transcripts are kept locally and not copied into this report. The corpus images are not redistributed in the source tree. This report is scoped to the runs above and does not assert installed Studio OCR support.
