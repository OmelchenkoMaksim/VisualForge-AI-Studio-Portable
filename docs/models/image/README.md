# Image models: which one for what

Evidence: [card rules](../README.md). Section comparisons are draft summaries of the linked cards/reports; **[recorded]** means historical evidence, not a new test. Source/config is authoritative; availability still depends on installation and policy.

| Engine (card) | Id | Makes | Steps | Choose it when |
|---|---|---|---|---|
| [FIBO 2.0 Dev INT8](image-fibo2-dev.md) | `fibo2-dev-int8` | native RGB text generation and editing with 1–4 references, local structured VGL | 50, CFG 5 | photographic scene/material control and reference edits; six local outputs rated 9.0/10, scoped evidence in card |
| [FLUX.3 Klein INT8](image-flux3-klein.md) | `flux3-klein-int8` | text to image, edit with 1-10 reference pictures, layout by boxes, native 1/2/4K | 16 (one preset) | composing people, places and objects from photos (the Director's keyframes), layouts, fast edits |
| [Qwen Image 2.1 UC FP8](image-qwen-image-21.md) | `qwen-image-21-uc-fp8` | text to image, edit, up to 10 references, **real RGBA**, native 2K | 25 or 40 | transparent backgrounds, 2K output, readable lettering |
| [Qwen Image Edit 2511](image-qwen-edit-2511.md) | `qwen-edit-2511` (default engine) | instruction edit of 1-3 images; "prompt" mode is an edit of a blank canvas | 40 (CFG 4) or 4 (Lightning) | recolouring, material and text edits of an existing picture |
| [Z-Image Turbo INT8](image-z-image-turbo.md) | `z-image-turbo-int8` | text to image, ControlNet (Union), masked edit, 2K detail, Tile | 8 (Detail: 5) | quick text to image; control by edges or a mask |

## Rules that hold for the Image section

[Composition](../../VisualForge-Composition.md) orchestrates these models from one idea and optional materials. It is a workspace; current capabilities and each engine's guide remain authoritative.

* **The first picture sets the output proportions** in the edit modes of Klein and Qwen 2.1 (the requested canvas applies only to text modes). A portrait photo first gives a portrait result: put a picture of the wanted shape first, or pad the first one onto a canvas of that shape (`director/media.py frame_pictures` for keyframes; `composition/composition_media.py prepare` for Composition: stretch when the shape is within 6 %, else fit over a blurred copy of the picture). **[recorded 2026-11-01, Klein]**
* **Successful status is not visual correctness**: counts (three gears came out four), lettering, small parts and edit boundaries require checking in every model. Look at the picture.
* **More steps or pixels are not automatically better**; native 4K changed composition and counts in Klein.
* **AI Upscale** (Real-ESRGAN x2, once or twice) sharpens and invents texture; it is not restoration, and it is separate from native resolution.
* Qwen 2.1 and Klein use researched Gemma passes; their native upsamplers remain for direct tooling and are bypassed in the studio. Do not rewrite the final prompt twice.
* Negative prompts matter where the sampler uses CFG above 1 (Qwen Edit 2511 Standard; FIBO Dev uses its trained null caption).
* All five current Image models have active per-mode Gemma managers. FIBO additionally converts the checked intent into validated VGL. Their cards distinguish researched rules, CPU/UI checks and real-Gemma observations.
* The studio's journal records each job as Image with children (prompt expansion, upscale); their durations overlap.

## When you need to check or extend this section
Drive it through the UI (Image tab, a disposable output folder), then judge the picture; unit checks in `tests/test_image_engines.py`, `test_image_prompt_manager.py`, `test_image_reliability.py`. Record the result in the card with the date and the mark.
