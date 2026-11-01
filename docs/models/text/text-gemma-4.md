# Gemma 4 (Text and the planners; via Ollama, private store)

Status: **development draft** from the linked project reports; observations were not rerun for this collection. Source identity reviewed **2026-11-01**; upstream research not refreshed. Selectable Gemma sizes and private imports have different manifests; record the selected Ollama tag, digest, quantisation and context for each comparison. There is no single immutable revision for this family card. **[code]**

Google Gemma 4 served by the studio's private Ollama store under `Models/Text/ollama`. Setup offers E2B, E4B (fresh-install default) and 26B A4B; local imports can differ in quantisation, context, vision and tools. The studio picks the chosen Gemma if it is one, otherwise the first usable Gemma. "Ollama settings" is on by default: chat requests send no sampling overrides, the model's own parameters stay authoritative. Marks: [the rules](../README.md).

| Variant | Scope of this draft |
|---|---|
| E2B | Available component; a short dictation-edit test is recorded in the Voice Input guide. Planning/image quality is not compared here. |
| E4B | Default setup component; do not transfer the large model's timing or accuracy to it. |
| 26B A4B / custom A4B | Large-model historical measurements depend on the selected import. Initial chat qualification used a custom reported 25.2B Q4_0 model plus vision projector, not every 26B package. |

The initial large-model reports record about 13.7 GB model residency and whole-card peaks near 15.9 GB; these are not requirements for all Gemma sizes. Record the exact tag/digest before using those numbers for a new decision.

## What it is for
Chat; rewriting a request into the prompt style of each video or image model; scene scenarios; the **Director's plan** (JSON); the **review of a finished clip** (verdict, score, corrected prompt); reading screenshots; polishing dictation.

## Strong at / Weak at
* Strong: follows the long hidden guides of the video models; reads pictures and a few frames of a video well enough to copy a hero's description into every scene prompt; writes a usable Director plan in 9-25 s warm and 80-250 s when the model has to be loaded first **[recorded 2026-10-31/11-01]**; with vision tokens raised for screenshots the character error rate of reading fell from 0.31 to 0.04 **[recorded, see Gemma-Screenshot-OCR]**.
* Weak: **habits that code must repair** [recorded in real plans 2026-11-01]: writes a music track as genre/mood fields without the one `caption`; leaves keyframes null for a scene with a hero, a place and a car; wraps spoken lines in quotes; numbers pictures by the draft's numbers in text that should use the order; ignores "no music" and lays the person's song anyway; adds a generated track over the person's own song; answers differ between runs. A clip review of a good clip may be a false alarm (invented "motion blur") and it misread a planned cut as a defect. It cannot hear.

## Expect / Do not expect
Expect a good plan most of the time and a different plan each time. Do not expect it to obey a rule in the prompt reliably (use code), to count (gears, words per second), to judge sound, or to repair faces and hands in a review.

## Settings that matter
| Item | Note |
|---|---|
| Context | the studio's configured context (32 K default) without a model-specific value; the Director asks with `num_ctx` 12288 |
| Thinking | off (`think=False`) for `GemmaTask` helpers; Text chat has its own thinking switch when the selected model supports it |
| Temperature | the Director plan uses 0.6 and retries once with the validator's message when the JSON does not normalise |
| Vision | private Text runtime sets a 560–1120 image-token budget (`text_manager.VISION_TOKENS`); 280 was the earlier default. Director inspection uses bounded pictures/video frames; these budgets do not establish OCR correctness |
| GPU | claims `text` for the call; the video, image or music job frees it and Gemma loads again for the next plan |

## How to ask it
English system instructions; the person's idea in their own language (the plan writes `title` and `summary` in the page language and the scene prompts in English); give it numbered materials and ask for **ONE JSON object**; state the shape with an example object **and expect the example's values to be copied** (a `gain_db: -16` in the example became the value in every plan); put rules that must hold into code too.

## Measured here **[recorded]**
Director plans 9-25 s warm, 80-150 s cold, 250 s right after a video job; clip review 20 s to 2 min. Of the real plans of 2026-11-01 none failed validation after the one retry, but most needed a repair by code.

## Not verified yet
How the E2B/E4B sizes plan compared with 26B A4B; non-Russian and non-English ideas; very long ideas (5000 characters); plans for 5-minute films. **[unknown]**

## Where in the code, deeper reports
`App/server/video/gemma_task.py`, `director/brain.py`, `video/scene_planner.py`, `video/video_prompt_enhancer.py`, `video/director_review.py`, `text/text_manager.py`. Reports: [VisualForge-Text-Chat.md](../../VisualForge-Text-Chat.md), [Gemma-Clip-Review.md](../../Gemma-Clip-Review.md), [Gemma-Screenshot-OCR.md](../../Gemma-Screenshot-OCR.md), [VisualForge-Prompt-Managers.md](../../VisualForge-Prompt-Managers.md).

## Sources and refresh
Google AI (Gemma 4 model card and documentation), the Ollama library page, Hugging Face. Refresh when a new Gemma size or version is considered or the vision-token defaults change.
