# Qwen Image 2.1 UC FP8 (`qwen-image-21-uc-fp8`, Image)

Status: **researched; all four modes exercised with real Gemma through the UI on 2026-11-05**. Publisher/community review: **2026-11-01**. Small local sample; scoped clarification retests completed. [Evidence rules](../README.md).

## Our version [code]

Unified **7B**, regular checkpoint, community **FP8**, Qwen3-VL 8B INT8 ConvRot encoder, RGBA VAE. Euler/simple, **25 or 40 steps, CFG 1**. Not Turbo or Edit 2511. Distributor: Hugging Face, abenzerps/Qwen-Image-2.1-Uncensored-Quantized, revision `7959e658915f52274897fb573ffeda49de8dc16b`; exact files/hashes and Qwen Research licence remain in `App/config/component-catalog.json`. UC is not evidence of greater accuracy.

Studio requests now use one automatic Gemma preparation; optional native Qwen3.5 9B PE T2I/I2I checkpoints are bypassed, their switch hidden. Missing/disabled native PE does not block this path. Stored workflows and direct tooling retain the native option and its alpha guard; no weights were changed.

## Useful for / limitations

- Genuine transparent PNGs, editing/extraction, native 2K and product/reference composition. Earlier tests produced alpha 0–255, recoloured objects and readable CLOCK STUDIO / ОТКРЫТО on a 2304×1536 poster. Three sources contributed scene, pattern and cactus. **[recorded, 2026-10-29]**
- Composition still failed: gears moved onto the clock, a requested card disappeared; 40 steps did not repair this. Native PE shifted style and painted a checkerboard in two transparency probes. Those failures concern the old PE, not the untested Gemma path. **[recorded]**
- No hard pixel locks, perfect mattes, guaranteed faces/counts/typography, native layers, Turbo schedule or working negative branch at CFG 1. Upstream annotated/masked editing exists, but this adapter exposes four image/text modes, not a dedicated Qwen mask editor. **[code/sources]**

## Modes and hidden prompt rules

The person writes normally, in any language. `qwen21_prompts.py`, guide **`qwen21-regular-2026-11-01-v1`**, supplies English model prose while protecting exact final lettering, including Russian. Removed/replaced old writing is not forced back.

| Mode | Actual inputs and preparation |
|---|---|
| Prompt | Zero inputs; uploaded pictures are ignored. A complete finished-picture description with spatial composition and purposeful detail; no compulsory lens/sensor tags or filler. Selected canvas, about 1 or 4 MP. |
| Single | One source; focused change plus compact preservation clause. A new scene from its subject gets a scene/composition instruction. No image tags. Background-removal button only prepares editable text. |
| Reference | Two inputs, source then donor; both `<image1>` and `<image2>` required. Specify transfer versus new composition, without copying an unwanted donor background. |
| Multi | 3–10 ordered inputs, 100 MiB total. Every real `<imageN>` tag required; absent/invented tags rejected. Assign scene/person/object/style roles, integrate scale and light. |
| Resolution / upscale | First input fixes edit proportions; selected 1/4 MP budget also applies to edits. AI Upscale is separate Real-ESRGAN x2/4x; alpha resized separately. |

Upstream PE may choose `wh_ratio`/`ratio_follow`; our manager cannot change controls or reorder the aspect anchor. Official T2I guidance favours a long description (around 400–500 words); our bounded Gemma guide scales detail to complexity instead of padding a simple scene. Exact optimal length for this FP8/Gemma pair is unknown. **[sources/design/unknown]**

For explicit transparent-background/alpha requests, code retains the publisher's RGBA opening/closing cue after Gemma. This preserves the instruction, not a guarantee of output alpha. Vision previews composite invisible RGB onto flat grey and report original alpha extrema separately; the original RGBA inputs still reach the image encoder. **[CPU]**

Clear requests generate immediately. A missing edit target or source role can trigger numbered request-specific choices; compatible selections continue, a group conflict permits one final question, and code forbids a third. No questions about technical tags or optional camera polish. API/Director calls are noninteractive. **[code/CPU]**

## Research decisions [sources, 2026-11-01]

- **Publisher:** QwenLM GitHub `Qwen-Image-2.1/prompt_rewrite` and separate T2I/edit system prompts; Hugging Face Qwen model card and official demo. Separate generation/edit intent, exact writing language, grounded changes and ordered tags inform this adapter. Official PE uses post-trained Qwen3.5 checkpoints; reusing its principles in Gemma is an adaptation, not equivalent training or proven quality. Gemma retains `think=False`.
- **Independent:** Reddit r/StableDiffusion, “I tested Qwen-Image-2.1: it's amazing!” (`1wlgv0l`, API tests) and “Qwen Image 2.1 is an EDITING Beast” (`1wokni7`, examples plus reports of small alignment/text changes). Useful for explicit transfers/reflections and realistic preservation limits; not a benchmark of our UC FP8.
- **Independent:** Reddit r/comfyui, “Qwen-Image-2.1 - Testing and Experience” (`1wpwyri`) explores CFG/samplers; its CFG-3 negative-prompt advice does not apply to our CFG-1 profile. “Qwen Image 2.1 default workflow's prompt enhancer is a joke” (`1x9irmw`) reports reasoning/token exhaustion. This supports bounded-output tests, not a claim that every PE fails or Gemma is superior.

Use these site/repository/title handles as a foundation; refresh changed claims rather than searching every basic fact again. Precise earlier citations and measurements: [Qwen-Image-2.1.md](../../Qwen-Image-2.1.md).

## Evidence and next checks

**[GPU/UI, 2026-11-05]** Real Gemma, installed UC FP8 and all four modes completed. A winter fox Prompt kept one mushroom. Reference at native 2048² replaced it with the donor mug while retaining the fox/forest. Three-source Multi made a summer terrace with the fox, mug and the third source's striped scarf. An isolated teapot Prompt with x2 produced actual 2048² RGBA, alpha 0–255; browser compositing confirmed transparency, with a visible edge halo/soft shadow. Almost-opaque photos/art still had small alpha deviations (248–255 or 252–255). A full-head portrait kept three apples and readable "УТРО" with plausible visible hands; watercolor retained paper/medium and "РАСТИ" after x2.

The ambiguous apple edit exposed manager failures: wrong UI language, incomplete labels and an omitted compatible choice. Russian labels and the two-question cap were verified by a repeat, which correctly drew the chosen left-yellow edit. A later two-property question correctly offered independent jacket/apple groups, but a verbatim-opening guard rejected a faithful paraphrase of short choices. Code now keeps agreed outcomes itself and uses bounded text-only reconciliation when needed. The real repeat accepted compatible choices 2,5 after one question: blue jacket, all three green apples, preserved face/hands/mug and readable "УТРО" (25 steps, 832×1248 RGBA, graph 80 s).

A four-source Multi diagnostic (place, receiving veranda reference, woman, transparent teapot; 40 steps, 1248×832) kept the standing woman, lake, mug and red teapot on the table. Her legs were cropped and the receiving veranda/window layout was lost despite faithful framing text. This does not qualify Qwen as a universal repair for Klein's complex reference failures. Ten-source and exact-identity quality remain unknown. See the [audit](../../VisualForge-Image-UI-Audit-2026-11-05.md) for settings and regressions. No raw-versus-enhanced comparison or universal identity/alpha guarantee.

**[CPU, 2026-11-01]** `tests/test_qwen21_prompt_manager.py`: real route/graph builder with fake Gemma/GPU/ComfyUI; four modes, ten ordered inputs, original RGBA bytes/matte metadata, 2K, literal text, tag retry, one final-prompt owner, two-question cap, stale source/budget rejection and history. Quoted RGBA lettering and explicit opaque requests do not force alpha. Related regression: **199 tests passed**, plus Ruff, JS syntax and clarification JS checks. These establish routing, not quality.

**[UI, 2026-11-01, fake backend]** Actual Image page plus actual route, fake Gemma/GPU/ComfyUI: Prompt ready/alpha cue, numbered conflict followed by submission without a third question, cancellation/unlocking, hidden native PE, guides for all four modes, RU/EN switching retaining Russian input. Two fake queues, three fake Gemma calls before the separate cancellation check; no inference or generated-file verification. Screenshot: C user Temp `vfr-tests/qwen21-clarification-ui.png`.

**[recorded, 2026-10-29]** Old path: RGBA Prompt 144 s; recolour 31 s; 2K Reference 221 s; three inputs + 4x upscale 56 s; extraction 20 s; PE poster 93 s / variant 294 s. One job peaked around 15.4 GB. Timings include mixed warm/cold conditions.

**[unknown]** General prompt/choice reliability beyond the tested cases, complex typography and edit boundaries, ten-image semantics, exact face identity, multi-person consistency and adult-content quality. Raw-versus-enhanced comparisons and repeated seeds remain useful follow-ups. The real samples above establish their particular outcomes, not a universal PASS.

Code: `image_prompt_enhancer.py`, `qwen21_prompts.py`, `image_registry.py`, `workflow_builder.py`, `qwen21_expansion.py`; workflows under `App/workflows/qwen-image-21-uc-fp8/`.
